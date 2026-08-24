#!/usr/bin/env python3
"""发布 Khronos -> ANYmal D waypoint graph 静态 Marker（jazzy）。

Topics:
  /anymald/waypoints/nodes      MarkerArray  waypoint nodes（SPHERE 绿）
  /anymald/waypoints/edges      Marker       LINE_LIST 稀疏边（蓝）
  /anymald/room_anchors         MarkerArray  room anchors（SPHERE 红 + TEXT）
  /anymald/waypoints/stretch_edges MarkerArray  stretch 分级边（可选开关）
所有 frame_id = map，latch（TRANSIENT_LOCAL）。
"""
import argparse
import math
import sys

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Int32
from visualization_msgs.msg import Marker, MarkerArray

from .data_loader import (DISPLAY_Z, BridgeData, color, header, make_sphere_marker,
                          point, quaternion_from_yaw)

# stretch 分级颜色（audit: >1.5 / >2 / >5）
_STRETCH_LEVELS = [
    (1.5, (0.0, 1.0, 0.0, 0.85)),   # <=1.5 可直走（绿）
    (2.0, (1.0, 1.0, 0.0, 0.85)),   # (1.5,2]（黄）
    (5.0, (1.0, 0.6, 0.0, 0.85)),   # (2,5]（橙）
    (math.inf, (1.0, 0.0, 0.0, 0.9)),  # >5 高风险（红）
]


def _stretch_color(s):
    for lim, c in _STRETCH_LEVELS:
        if s <= lim:
            return c
    return _STRETCH_LEVELS[-1][1]


class GraphPublisher(Node):
    def __init__(self, data: BridgeData, publish_stretch: bool, frame: str = "map",
                 republish_period: float = 0.0, reachable_from: int = -1,
                 focus_floors: bool = False):
        super().__init__("anymald_graph_publisher")
        self.data = data
        self.publish_stretch = publish_stretch
        self.frame = frame
        self.republish_period = republish_period
        self.focus_floors = focus_floors
        self.visible = (data.reachable_waypoint_ids(reachable_from)
                        if reachable_from >= 0 else set(data.waypoint_ids()))
        self.active_floor = (data.floor_of_waypoint(reachable_from)
                             if focus_floors and reachable_from >= 0 else None)
        self.visible_edges = [(a, b) for a, b in data.display_edges()
                              if a in self.visible and b in self.visible]
        latch = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                           durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub_nodes = self.create_publisher(MarkerArray, "/anymald/waypoints/nodes", latch)
        self.pub_edges = self.create_publisher(Marker, "/anymald/waypoints/edges", latch)
        self.pub_anchors = self.create_publisher(MarkerArray, "/anymald/room_anchors", latch)
        self.pub_stretch = self.create_publisher(
            MarkerArray, "/anymald/waypoints/stretch_edges", latch)
        self.pub_floors = self.create_publisher(
            MarkerArray, "/anymald/floors", latch)
        if self.focus_floors:
            self.floor_sub = self.create_subscription(
                Int32, "/anymald/active_floor", self._active_floor_cb, latch)
        # MD Fix E：静态图 TRANSIENT_LOCAL + publish once（late subscriber 由 QoS 保证）
        # 仅当 --republish-period>0 才周期重发（特殊 DDS 环境诊断用）
        self.publish_all()
        if self.republish_period > 0:
            self.timer = self.create_timer(self.republish_period, self.publish_all)

    def _active_floor_cb(self, msg):
        if self.active_floor == msg.data:
            return
        self.active_floor = msg.data
        self.publish_all()

    def _floor_visible(self, wid: int) -> bool:
        return (not self.focus_floors or self.active_floor is None
                or self.data.floor_of_waypoint(wid) == self.active_floor)

    def _delete_all(self, stamp) -> Marker:
        marker = Marker()
        marker.header = header(self.frame, stamp)
        marker.action = Marker.DELETEALL
        return marker

    def publish_all(self):
        stamp = self.get_clock().now().to_msg()
        self._publish_nodes(stamp)
        self._publish_edges(stamp)
        self._publish_anchors(stamp)
        self._publish_floors(stamp)
        if self.publish_stretch:
            self._publish_stretch(stamp)

    def _publish_nodes(self, stamp):
        ma = MarkerArray()
        if self.focus_floors:
            ma.markers.append(self._delete_all(stamp))
        node_scale = 0.75 if self.data.floor_spacing else (0.48 if self.focus_floors else 0.32)
        for i, wid in enumerate(sorted(self.visible)):
            if not self._floor_visible(wid):
                continue
            p = self.data.spars_pos(wid)
            if p is None:
                continue
            ma.markers.append(make_sphere_marker(
                "waypoint_nodes", i, (p[0], p[1], p[2]), node_scale,
                (0.0, 0.9, 0.2, 1.0),
                frame=self.frame, stamp=stamp))
        self.pub_nodes.publish(ma)
        self.get_logger().debug(f"nodes: {len(ma.markers)}")

    def _publish_edges(self, stamp):
        m = Marker()
        m.header = header(self.frame, stamp)
        m.ns = "edges"
        m.id = 0
        m.type = Marker.LINE_LIST
        m.action = Marker.ADD
        m.pose.orientation = quaternion_from_yaw(0.0)
        m.scale.x = 0.12 if self.data.floor_spacing else 0.08
        m.color = color(0.25, 0.5, 1.0, 1.0)
        for a, b in self.visible_edges:
            if not self._floor_visible(a) or not self._floor_visible(b):
                continue
            pa, pb = self.data.spars_pos(a), self.data.spars_pos(b)
            if pa is None or pb is None:
                continue
            m.points.append(point(pa[0], pa[1], pa[2]))
            m.points.append(point(pb[0], pb[1], pb[2]))
        self.pub_edges.publish(m)
        self.get_logger().debug(f"edges: {len(self.visible_edges)}")

    def _publish_anchors(self, stamp):
        ma = MarkerArray()
        if self.focus_floors:
            ma.markers.append(self._delete_all(stamp))
        anchors = self.data.room_anchors()
        anchor_scale = 0.95 if self.data.floor_spacing else (0.7 if self.focus_floors else 0.45)
        text_scale = 0.8 if self.data.floor_spacing else (0.5 if self.focus_floors else 0.35)
        for i, (room, wid) in enumerate(sorted(anchors.items())):
            if wid not in self.visible:
                continue
            if not self._floor_visible(wid):
                continue
            p = self.data.spars_pos(wid)
            if p is None:
                continue
            ma.markers.append(make_sphere_marker(
                "room_anchors", i, (p[0], p[1], p[2]), anchor_scale,
                (1.0, 0.2, 0.2, 1.0),
                frame=self.frame, stamp=stamp))
            tm = make_sphere_marker("room_anchor_text", i, (p[0], p[1], p[2]),
                                    0.0, (1.0, 1.0, 1.0, 1.0), frame=self.frame, stamp=stamp)
            tm.type = Marker.TEXT_VIEW_FACING
            tm.text = f"R{room}"
            tm.scale.z = text_scale
            ma.markers.append(tm)
        self.pub_anchors.publish(ma)
        self.get_logger().debug(f"anchors: {len(anchors)}")

    def _publish_floors(self, stamp):
        ma = MarkerArray()
        if self.focus_floors:
            ma.markers.append(self._delete_all(stamp))
        if not self.data.floor_spacing and not self.focus_floors:
            self.pub_floors.publish(ma)
            return
        groups = {}
        for wid in self.visible:
            if not self._floor_visible(wid):
                continue
            groups.setdefault(self.data.floor_of_waypoint(wid), []).append(
                self.data.spars_pos(wid))
        for floor, positions in sorted(groups.items()):
            xs = [p[0] for p in positions]
            ys = [p[1] for p in positions]
            z = max(p[2] for p in positions) + 0.2
            x0, x1 = min(xs) - 1.5, max(xs) + 1.5
            y0, y1 = min(ys) - 1.5, max(ys) + 1.5
            border = Marker()
            border.header = header(self.frame, stamp)
            border.ns = "floor_borders"
            border.id = floor
            border.type = Marker.LINE_STRIP
            border.action = Marker.ADD
            border.pose.orientation = quaternion_from_yaw(0.0)
            border.scale.x = 0.12
            border.color = color(0.2, 0.85, 1.0, 0.85)
            for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)):
                border.points.append(point(x, y, z))
            ma.markers.append(border)
            label = Marker()
            label.header = header(self.frame, stamp)
            label.ns = "floor_labels"
            label.id = 100 + floor
            label.type = Marker.TEXT_VIEW_FACING
            label.action = Marker.ADD
            label.pose.position = point((x0 + x1) / 2, y1 + 2.0, z + 0.8)
            label.pose.orientation = quaternion_from_yaw(0.0)
            label.text = f"FLOOR {floor}"
            label.scale.z = 1.8
            label.color = color(0.2, 0.85, 1.0, 1.0)
            ma.markers.append(label)
        self.pub_floors.publish(ma)

    def _publish_stretch(self, stamp):
        ma = MarkerArray()
        idx = 0
        for a, b in self.visible_edges:
            if not self._floor_visible(a) or not self._floor_visible(b):
                continue
            s = self.data.stretch_of(a, b)
            if s is None:
                continue
            pa, pb = self.data.spars_pos(a), self.data.spars_pos(b)
            if pa is None or pb is None:
                continue
            m = Marker()
            m.header = header(self.frame, stamp)
            m.ns = "stretch_edges"
            m.id = idx
            m.type = Marker.LINE_LIST
            m.action = Marker.ADD
            m.pose.orientation = quaternion_from_yaw(0.0)
            m.scale.x = 0.06
            m.color = color(*_stretch_color(s))
            m.points.append(point(pa[0], pa[1], pa[2]))
            m.points.append(point(pb[0], pb[1], pb[2]))
            ma.markers.append(m)
            idx += 1
        self.pub_stretch.publish(ma)
        self.get_logger().info(f"stretch edges published: {idx}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", required=True,
                    help="khronos_anymald_bridge 目录（含 generated/）")
    ap.add_argument("--data-dir", default=None,
                    help="waypoint JSON 目录；默认 <base-dir>/generated")
    ap.add_argument("--canonical-graph", default=None,
                    help="canonical_graph_v2.json（Hospital dense witness 坐标）")
    ap.add_argument("--vertical-transitions", default=None,
                    help="vertical_transitions_v2.json")
    ap.add_argument("--floor-model", default=None,
                    help="floor_model_v2.json")
    ap.add_argument("--floor-spacing", type=float, default=0.0,
                    help="楼层面板间距（0=原生坐标）")
    ap.add_argument("--floor-columns", type=int, default=1,
                    help="展开布局列数；Hospital 总览使用 2")
    ap.add_argument("--z-offset", type=float, default=0.0,
                    help="waypoint/route 相对 mesh 的显示高度")
    ap.add_argument("--reachable-from", type=int, default=-1,
                    help="只发布该起点可达的 waypoint；-1=全部")
    ap.add_argument("--focus-floors", default="false",
                    help="仅显示 /anymald/active_floor 当前楼层")
    ap.add_argument("--native-z", default="false",
                    help="true=保留原生 3D z；false=Office DISPLAY_Z 投影")
    ap.add_argument("--r", type=float, default=3.0)
    ap.add_argument("--ground-z", type=float, default=0.0)
    ap.add_argument("--frame", default="map", help="frame_id（默认 map；Khronos 用 world 时设 world）")
    ap.add_argument("--publish-stretch", default="false",
                    help="发布 stretch 分级边 true/false（绿<=1.5 黄<=2 橙<=5 红>5）")
    ap.add_argument("--republish-period", type=float, default=0.0,
                    help="周期重发秒数（0=不重发，TRANSIENT_LOCAL 已覆盖 late subscriber）")
    args, _ = ap.parse_known_args()
    publish = args.publish_stretch.lower() in ("true", "1", "yes")

    rclpy.init()
    try:
        native_z = args.native_z.lower() in ("true", "1", "yes")
        data = BridgeData(
            args.base_dir, r=args.r, ground_z=args.ground_z, frame=args.frame,
            data_dir=args.data_dir, canonical_graph=args.canonical_graph,
            vertical_transitions=args.vertical_transitions,
            project_z=None if native_z else DISPLAY_Z,
            floor_model=args.floor_model, floor_spacing=args.floor_spacing,
            floor_columns=args.floor_columns, z_offset=args.z_offset)
        node = GraphPublisher(data, publish_stretch=publish, frame=args.frame,
                              republish_period=args.republish_period,
                              reachable_from=args.reachable_from,
                              focus_floors=args.focus_floors.lower()
                              in ("true", "1", "yes"))
        try:
            rclpy.spin(node)
        except (KeyboardInterrupt, ExternalShutdownException):
            pass
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
