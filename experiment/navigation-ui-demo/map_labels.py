"""Publish room and floor labels for the currently displayed Nav2 map."""

import argparse
import json
from pathlib import Path
import sys

from nav_msgs.msg import OccupancyGrid
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from visualization_msgs.msg import Marker, MarkerArray
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "khronos-nav2-bridge/khronos_nav2_bridge/scripts"))
from floor_mosaic_core import extract_room_labels, identify_floor_by_geometry, load_floor_grids


class MapLabels(Node):
    def __init__(self, bundle, graph):
        super().__init__("hospital_map_labels")
        self.frame = bundle["frame_id"]
        self.floors = load_floor_grids(bundle)
        heights = {int(key): entry["floor_z_m"] for key, entry in bundle["floors"].items()}
        rooms = [
            {"id": key, "layer": 1, "attributes": {"position": node["position"]}}
            for key, node in graph["nodes"].items()
            if node["node_type"] == "room" and node.get("position") is not None
        ]
        self.rooms = extract_room_labels(
            {"nodes": rooms, "layer_names": {"ROOMS": {"layer": 1}}}, heights
        )
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.publisher = self.create_publisher(MarkerArray, "/hospital/map_labels", qos)
        self.subscription = self.create_subscription(OccupancyGrid, "/map", self.on_map, qos)

    def label(self, marker_id, text, x, y, size):
        marker = Marker()
        marker.header.frame_id = self.frame
        marker.ns = "hospital_map_labels"
        marker.id = marker_id
        marker.type = Marker.TEXT_VIEW_FACING
        marker.pose.position.x = float(x)
        marker.pose.position.y = float(y)
        marker.pose.position.z = 0.25
        marker.pose.orientation.w = 1.0
        marker.scale.z = size
        marker.color.r = 0.12
        marker.color.g = 0.42
        marker.color.b = 0.90
        marker.color.a = 1.0
        marker.text = text
        return marker

    def on_map(self, message):
        info = message.info
        origin = info.origin.position
        geometry = {"width": info.width, "height": info.height,
                    "resolution": info.resolution, "origin_x": origin.x, "origin_y": origin.y}
        floor = (identify_floor_by_geometry(geometry, self.floors)
                 if message.header.frame_id == self.frame else None)
        clear = Marker()
        clear.header.frame_id = self.frame
        clear.action = Marker.DELETEALL
        markers = [clear]
        if floor is not None:
            markers.append(self.label(
                0, f"Floor F{floor}", origin.x + 2.0,
                origin.y + info.height * info.resolution - 0.8, 1.2,
            ))
            markers.extend(
                self.label(index, room.label, room.x, room.y, 0.8)
                for index, room in enumerate(self.rooms, start=1) if room.floor_id == floor
            )
        self.publisher.publish(MarkerArray(markers=markers))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--graph", type=Path, required=True)
    args = parser.parse_args()
    rclpy.init()
    node = MapLabels(yaml.safe_load(args.bundle.read_text()), json.loads(args.graph.read_text()))
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
