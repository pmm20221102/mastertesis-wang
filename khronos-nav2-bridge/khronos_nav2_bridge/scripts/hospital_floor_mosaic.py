#!/usr/bin/env python3
"""Publish four verified Hospital maps as an RViz-only 2x2 mosaic."""

import json
from pathlib import Path
from floor_mosaic_core import (
    compose_floor_mosaic,
    extract_room_labels,
    floor_occupancy_matches,
    identify_floor_by_geometry,
    load_floor_grids,
    map_pose_to_mosaic,
    map_to_mosaic_translation,
    map_grid_origin_in_mosaic,
    mission_floor_sequence,
    room_label_to_mosaic,
    as_rgb_float,
)
from multifloor_status import LIVE_STATUS_SCHEMA, build_live_status


def main(args=None):
    import rclpy
    from geometry_msgs.msg import TransformStamped
    from nav_msgs.msg import OccupancyGrid
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    from std_msgs.msg import String
    from tf2_ros import TransformBroadcaster
    from visualization_msgs.msg import Marker, MarkerArray

    class FloorMosaic(Node):
        def __init__(self):
            super().__init__("hospital_floor_mosaic")
            self.declare_parameter("bundle_path", "")
            self.declare_parameter("mission_path", "")
            self.declare_parameter("dsg_path", "")
            bundle_path = self.get_parameter("bundle_path").value
            if not bundle_path:
                raise ValueError("bundle_path parameter is required")
            import yaml
            with open(bundle_path, "r", encoding="utf-8") as stream:
                self.bundle = yaml.safe_load(stream)
            self.floors = load_floor_grids(self.bundle)
            self.mosaic = compose_floor_mosaic(self.floors)
            heights = {int(key): float(value["floor_z_m"])
                       for key, value in self.bundle["floors"].items()}
            dsg_path = self.get_parameter("dsg_path").value
            if not dsg_path:
                dsg_path = str(Path(self.bundle["source_root"])
                               / "dsg_topology_repaired.json")
            with open(dsg_path, "r", encoding="utf-8") as stream:
                self.room_labels = extract_room_labels(json.load(stream), heights)
            mission_path = self.get_parameter("mission_path").value
            self.mission = None
            self.route_floors = []
            if mission_path:
                with open(mission_path, "r", encoding="utf-8") as stream:
                    self.mission = json.load(stream)
                self.route_floors = mission_floor_sequence(self.mission, self.bundle)
            self.get_logger().info(
                f"loaded {len(self.room_labels)} DSG room IDs; route={self.route_floors}")
            self.active_floor = None
            self.status = None
            self.status_error = None
            self.map_error = None
            durable_qos = QoSProfile(depth=1)
            durable_qos.reliability = ReliabilityPolicy.RELIABLE
            durable_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
            self.floor_map_pubs = {
                floor_id: self.create_publisher(
                    OccupancyGrid, f"/hospital/floor_mosaic/f{floor_id}/map", durable_qos)
                for floor_id in self.floors
            }
            self.marker_pub = self.create_publisher(
                MarkerArray, "/hospital/floor_mosaic/markers", durable_qos)
            self.map_sub = self.create_subscription(
                OccupancyGrid, "/map", self._on_map, durable_qos)
            self.status_sub = self.create_subscription(
                String, "/hospital/multifloor/status", self._on_status, durable_qos)
            self.tf_broadcaster = TransformBroadcaster(self)
            self._publish_mosaic()
            self._publish_markers()
            self.timer = self.create_timer(0.1, self._tick)

        def _publish_mosaic(self):
            """Publish four small floor grids; RViz places them in the shared mosaic frame."""
            now = self.get_clock().now().to_msg()
            for floor_id, floor in self.floors.items():
                message = OccupancyGrid()
                message.header.frame_id = "hospital_floor_mosaic"
                message.header.stamp = now
                message.info.map_load_time = now
                message.info.resolution = floor.resolution
                message.info.width = floor.width
                message.info.height = floor.height
                origin_x, origin_y = map_grid_origin_in_mosaic(
                    floor, self.mosaic.placements[floor_id], self.mosaic)
                message.info.origin.position.x = origin_x
                message.info.origin.position.y = origin_y
                message.info.origin.orientation.w = 1.0
                message.data = list(floor.occupancy)
                self.floor_map_pubs[floor_id].publish(message)

        def _on_map(self, message):
            origin = message.info.origin
            geometry = {
                "width": message.info.width,
                "height": message.info.height,
                "resolution": message.info.resolution,
                "origin_x": origin.position.x,
                "origin_y": origin.position.y,
            }
            if message.header.frame_id != self.bundle["frame_id"]:
                self.active_floor = None
                self.map_error = f"active map frame mismatch: {message.header.frame_id}"
                self._publish_markers()
                return
            floor = identify_floor_by_geometry(geometry, self.floors)
            if floor is None:
                self.active_floor = None
                self.map_error = "active /map geometry matches zero or multiple floors"
            elif not floor_occupancy_matches(self.floors[floor], message.data):
                self.active_floor = None
                self.map_error = f"active /map cells differ from verified F{floor} raster"
            else:
                self.active_floor = floor
                self.map_error = None
                if getattr(self, "last_verified_floor", None) != floor:
                    self.get_logger().info(
                        f"active /map geometry and all cells match verified F{floor} raster")
                    self.last_verified_floor = floor
                if (self.status is not None
                        and self.status["state"] in ("succeeded", "failed")
                        and self.status["active_floor"] is not None
                        and self.status["active_floor"] != floor):
                    self.status = None
                    self.status_error = None
            self._publish_markers()

        def _on_status(self, message):
            try:
                data = json.loads(message.data)
                if data.get("schema") != LIVE_STATUS_SCHEMA:
                    raise ValueError("unsupported status schema")
                self.status = build_live_status(
                    data.get("task_id"), data.get("state"),
                    data.get("active_floor"), data.get("from_floor"),
                    data.get("to_floor"), data.get("transition_id"),
                    data.get("error"))
                self.status_error = None
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                self.status = None
                self.status_error = str(error)
                self.get_logger().error(f"invalid multifloor status: {error}")
            self._publish_markers()

        def _status_text(self):
            if self.map_error:
                return f"MAP ERROR | {self.map_error}"
            if self.status_error:
                return f"STATUS ERROR | {self.status_error}"
            if self.active_floor is None:
                return "ACTIVE FLOOR UNKNOWN | waiting for a verified /map"
            if self.status is None:
                return f"ACTIVE F{self.active_floor} | transition status unknown"
            state = self.status["state"]
            task = self.status["task_id"]
            if state in ("switching_map", "resetting_pose"):
                return (f"{task} | F{self.status['from_floor']} -> "
                        f"F{self.status['to_floor']} | {state.upper()}")
            if state in ("failed", "succeeded"):
                suffix = f" | {self.status['error']}" if self.status["error"] else ""
                return f"{task} | F{self.active_floor} | {state.upper()}{suffix}"
            return f"{task} | ACTIVE F{self.active_floor} | {state.upper()}"

        def _portal_markers(self):
            result = MarkerArray()
            now = self.get_clock().now().to_msg()
            for floor_id, floor in self.floors.items():
                tile = self.mosaic.placements[floor_id]
                label = Marker()
                label.header.frame_id = "hospital_floor_mosaic"
                label.header.stamp = now
                label.ns = "hospital_floor_labels"
                label.id = floor_id
                label.type = Marker.TEXT_VIEW_FACING
                label.action = Marker.ADD
                label.pose.position.x = (self.mosaic.origin_x
                                         + (tile.cell_x + 0.5) * self.mosaic.resolution)
                label.pose.position.y = (self.mosaic.origin_y
                                         + (tile.cell_y + self.mosaic.tile_height - 0.7)
                                         * self.mosaic.resolution)
                label.pose.position.z = 0.1
                label.pose.orientation.w = 1.0
                label.scale.z = 1.2
                label.color.r = label.color.g = label.color.b = 0.0
                label.color.a = 1.0
                label.text = f"F{floor_id}"
                label.lifetime.sec = 0
                label.lifetime.nanosec = 0
                result.markers.append(label)

            room_ids = {room.node_id: room.label for room in self.room_labels}
            for index, room in enumerate(self.room_labels):
                point = room_label_to_mosaic(room, self.floors, self.mosaic)
                if point is None:
                    self.get_logger().warning(
                        f"room label {room.label} ({room.node_id}) has no nearby free cell")
                    continue
                marker = Marker()
                marker.header.frame_id = "hospital_floor_mosaic"
                marker.header.stamp = now
                marker.ns = "hospital_floor_room_labels"
                marker.id = index
                marker.type = Marker.TEXT_VIEW_FACING
                marker.action = Marker.ADD
                marker.pose.position.x, marker.pose.position.y = point
                marker.pose.position.z = 0.22
                marker.pose.orientation.w = 1.0
                marker.scale.z = 1.0
                marker.color.r = marker.color.g = marker.color.b = 0.0
                marker.color.a = 1.0
                marker.text = (f"F3.5 {room.label}" if room.half_level
                               else room.label)
                marker.lifetime.sec = 0
                marker.lifetime.nanosec = 0
                result.markers.append(marker)

            # Portal points stay on their maps; readable stair names live in the sidebar.
            for index, edge in enumerate(self.bundle["transitions"]):
                for endpoint, floor_key, color, suffix in (
                    (edge["entry_pose"], "from_floor", (1.0, 0.45, 0.05), "ENTRY"),
                    (edge["exit_pose"], "to_floor", (0.05, 0.75, 1.0), "EXIT"),
                ):
                    floor_id = int(edge[floor_key])
                    floor = self.floors[floor_id]
                    tile = self.mosaic.placements[floor_id]
                    x, y = map_pose_to_mosaic(
                        (endpoint["x"], endpoint["y"]), floor, tile, self.mosaic)
                    marker = Marker()
                    marker.header.frame_id = "hospital_floor_mosaic"
                    marker.header.stamp = now
                    marker.ns = "hospital_floor_portals"
                    marker.id = index * 2 + (suffix == "EXIT")
                    marker.type = Marker.SPHERE
                    marker.action = Marker.ADD
                    marker.pose.position.x = x
                    marker.pose.position.y = y
                    marker.pose.position.z = 0.16
                    marker.pose.orientation.w = 1.0
                    marker.scale.x = marker.scale.y = marker.scale.z = 0.42
                    marker.color.r, marker.color.g, marker.color.b = color
                    marker.color.a = 1.0
                    marker.lifetime.sec = 0
                    marker.lifetime.nanosec = 0
                    result.markers.append(marker)

            separators = Marker()
            separators.header.frame_id = "hospital_floor_mosaic"
            separators.header.stamp = now
            separators.ns = "hospital_floor_separators"
            separators.id = 0
            separators.type = Marker.LINE_LIST
            separators.action = Marker.ADD
            separators.scale.x = 0.2
            separators.color.r = separators.color.g = separators.color.b = 0.0
            separators.color.a = 1.0
            gap_x = self.mosaic.placements[1].cell_x - self.mosaic.tile_width
            gap_y = (self.mosaic.placements[0].cell_y - self.mosaic.tile_height)
            center_x = (self.mosaic.tile_width + gap_x / 2) * self.mosaic.resolution
            center_y = (self.mosaic.tile_height + gap_y / 2) * self.mosaic.resolution
            map_width = (2 * self.mosaic.tile_width + gap_x) * self.mosaic.resolution
            total_height = self.mosaic.height * self.mosaic.resolution
            for x1, y1, x2, y2 in (
                (center_x, 0.0, center_x, total_height),
                (0.0, center_y, map_width, center_y),
                ((self.mosaic.panel_x - 0.5) * self.mosaic.resolution, 0.0,
                 (self.mosaic.panel_x - 0.5) * self.mosaic.resolution, total_height),
            ):
                separators.points.extend((
                    self._point(x1, y1, 0.24), self._point(x2, y2, 0.24)))
            result.markers.append(separators)

            panel = Marker()
            panel.header.frame_id = "hospital_floor_mosaic"
            panel.header.stamp = now
            panel.ns = "hospital_floor_sidebar_background"
            panel.id = 0
            panel.type = Marker.CUBE
            panel.action = Marker.ADD
            panel.pose.position.x = (self.mosaic.panel_x
                                     + self.mosaic.panel_width / 2) * self.mosaic.resolution
            panel.pose.position.y = self.mosaic.height * self.mosaic.resolution / 2
            panel.pose.position.z = 0.02
            panel.pose.orientation.w = 1.0
            panel.scale.x = self.mosaic.panel_width * self.mosaic.resolution
            panel.scale.y = self.mosaic.height * self.mosaic.resolution
            panel.scale.z = 0.02
            panel.color.r = panel.color.g = panel.color.b = 1.0
            panel.color.a = 1.0
            panel.lifetime.sec = panel.lifetime.nanosec = 0
            result.markers.append(panel)

            result.markers.extend(self._sidebar_markers(now, room_ids).markers)
            return result

        @staticmethod
        def _point(x, y, z):
            from geometry_msgs.msg import Point
            point = Point()
            point.x, point.y, point.z = x, y, z
            return point

        def _sidebar_markers(self, now, room_ids):
            result = MarkerArray()
            x = (self.mosaic.panel_x + 70) * self.mosaic.resolution
            top = self.mosaic.height * self.mosaic.resolution - 12.0

            def text_marker(namespace, marker_id, y, content, size,
                            color=(0.0, 0.0, 0.0)):
                marker = Marker()
                marker.header.frame_id = "hospital_floor_mosaic"
                marker.header.stamp = now
                marker.ns = namespace
                marker.id = marker_id
                marker.type = Marker.TEXT_VIEW_FACING
                marker.action = Marker.ADD
                marker.pose.position.x = x
                marker.pose.position.y = y
                marker.pose.position.z = 0.28
                marker.pose.orientation.w = 1.0
                marker.scale.z = size
                marker.color.r, marker.color.g, marker.color.b = as_rgb_float(color)
                marker.color.a = 1.0
                marker.text = content
                marker.lifetime.sec = marker.lifetime.nanosec = 0
                return marker

            route = (self.mission or {}).get("visualization_route", {}).get("entries", [])
            lines = []
            for entry in route:
                floor_id = entry["floor_id"]
                room_id = entry.get("room_id")
                lines.append(f"F{floor_id}" + (f"  {room_id}" if room_id else ""))
            if not lines:
                lines = [f"F{floor}" for floor in self.route_floors]
            result.markers.append(text_marker(
                "hospital_floor_route_panel", 0, top,
                "\n".join(lines), 3.0))
            return result

        def _publish_markers(self):
            markers = self._portal_markers()
            self.marker_pub.publish(markers)

        def _tick(self):
            transform = TransformStamped()
            transform.header.stamp = self.get_clock().now().to_msg()
            transform.header.frame_id = "hospital_floor_mosaic"
            transform.child_frame_id = self.bundle["frame_id"]
            transition_in_progress = (
                self.status is not None
                and self.status["state"] in ("switching_map", "resetting_pose"))
            floor_id = self.active_floor
            status_floor = self.status.get("active_floor") if self.status else None
            status_requires_match = self.status is not None and self.status["state"] in (
                "navigating_to_stop", "navigating_to_entry", "floor_ready",
                "succeeded", "failed")
            status_mismatch = (status_requires_match and status_floor is not None
                               and status_floor != floor_id)
            hide = (floor_id is None or transition_in_progress or status_mismatch
                    or (self.status is not None and self.status["state"] == "failed"
                        and status_floor is None))
            if hide:
                transform.transform.translation.x = (
                    self.mosaic.width * self.mosaic.resolution + 100.0)
                transform.transform.translation.y = (
                    self.mosaic.height * self.mosaic.resolution + 100.0)
            else:
                floor = self.floors[floor_id]
                tile = self.mosaic.placements[floor_id]
                x, y = map_to_mosaic_translation(floor, tile, self.mosaic)
                transform.transform.translation.x = x
                transform.transform.translation.y = y
            transform.transform.rotation.w = 1.0
            self.tf_broadcaster.sendTransform(transform)

    rclpy.init(args=args)
    node = FloorMosaic()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
