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
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from visualization_msgs.msg import Marker, MarkerArray

from .data_loader import (BridgeData, color, header, make_sphere_marker,
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
                 republish_period: float = 0.0):
        super().__init__("anymald_graph_publisher")
        self.data = data
        self.publish_stretch = publish_stretch
        self.frame = frame
        self.republish_period = republish_period
        latch = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                           durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub_nodes = self.create_publisher(MarkerArray, "/anymald/waypoints/nodes", latch)
        self.pub_edges = self.create_publisher(Marker, "/anymald/waypoints/edges", latch)
        self.pub_anchors = self.create_publisher(MarkerArray, "/anymald/room_anchors", latch)
        self.pub_stretch = self.create_publisher(
            MarkerArray, "/anymald/waypoints/stretch_edges", latch)
        # MD Fix E：静态图 TRANSIENT_LOCAL + publish once（late subscriber 由 QoS 保证）
        # 仅当 --republish-period>0 才周期重发（特殊 DDS 环境诊断用）
        self.publish_all()
        if self.republish_period > 0:
            self.timer = self.create_timer(self.republish_period, self.publish_all)

    def publish_all(self):
        stamp = self.get_clock().now().to_msg()
        self._publish_nodes(stamp)
        self._publish_edges(stamp)
        self._publish_anchors(stamp)
        if self.publish_stretch:
            self._publish_stretch(stamp)

    def _publish_nodes(self, stamp):
        ma = MarkerArray()
        for i, wid in enumerate(self.data.waypoint_ids()):
            p = self.data.spars_pos(wid)
            if p is None:
                continue
            ma.markers.append(make_sphere_marker(
                "waypoint_nodes", i, (p[0], p[1], p[2]), 0.75, (0.0, 0.85, 0.2, 1.0),
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
        m.scale.x = 0.2
        m.color = color(0.25, 0.5, 1.0, 1.0)
        for a, b in self.data.edges():
            pa, pb = self.data.spars_pos(a), self.data.spars_pos(b)
            if pa is None or pb is None:
                continue
            m.points.append(point(pa[0], pa[1], pa[2]))
            m.points.append(point(pb[0], pb[1], pb[2]))
        self.pub_edges.publish(m)
        self.get_logger().debug(f"edges: {len(self.data.edges())}")

    def _publish_anchors(self, stamp):
        ma = MarkerArray()
        anchors = self.data.room_anchors()
        for i, (room, wid) in enumerate(sorted(anchors.items())):
            p = self.data.spars_pos(wid)
            if p is None:
                continue
            ma.markers.append(make_sphere_marker(
                "room_anchors", i, (p[0], p[1], p[2]), 0.8, (1.0, 0.2, 0.2, 1.0),
                frame=self.frame, stamp=stamp))
            tm = make_sphere_marker("room_anchor_text", i, (p[0], p[1], p[2]),
                                    0.0, (1.0, 1.0, 1.0, 1.0), frame=self.frame, stamp=stamp)
            tm.type = Marker.TEXT_VIEW_FACING
            tm.text = f"R{room}"
            tm.scale.z = 0.55
            ma.markers.append(tm)
        self.pub_anchors.publish(ma)
        self.get_logger().debug(f"anchors: {len(anchors)}")

    def _publish_stretch(self, stamp):
        ma = MarkerArray()
        idx = 0
        for a, b in self.data.edges():
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
        data = BridgeData(args.base_dir, r=args.r, ground_z=args.ground_z, frame=args.frame)
        node = GraphPublisher(data, publish_stretch=publish, frame=args.frame,
                              republish_period=args.republish_period)
        rclpy.spin(node)
    finally:
        rclpy.shutdown()


if __name__ == "__main__":
    main()
