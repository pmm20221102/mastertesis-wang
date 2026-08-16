#!/usr/bin/env python3
"""发布 Khronos 3D mesh（MESH_RESOURCE 实心 或 LINE_LIST 线框）。

集成进 mock_navigation.launch.py（enable_mesh / mesh_path 参数）。

实心模式（默认）：
  MESH_RESOURCE marker，assimp 加载 PLY/STL。注意：OGRE 对大 mesh
  （约 >50k 顶点）渲染失败，且面被背面剔除，俯视只显顶面。

线框模式（--wireframe，推荐）：
  读 PLY 顶点+面提取边，发布 LINE_LIST marker。无背面剔除，建筑结构
  （墙/房间分隔）清晰可见，与 waypoint 点线图同屏。

发布策略：延迟 delay 秒 + 检测订阅者后 one-shot publish（TRANSIENT_LOCAL），
不做高频重发。
"""
import argparse
import math
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from visualization_msgs.msg import Marker

from .data_loader import color, point


class KhronosMeshPublisher(Node):
    def __init__(self, mesh_path: str, frame: str = "map",
                 delay: float = 2.0, max_wait: float = 30.0,
                 rot_x: float = 0.0, wireframe: bool = False,
                 color_a: tuple = (0.92, 0.92, 0.92, 1.0)):
        super().__init__("khronos_mesh_publisher")
        latch = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                           durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub = self.create_publisher(Marker, "/anymald/khronos_mesh", latch)
        self.mesh_path = mesh_path
        self.frame = frame
        self.rot_x = rot_x
        self.wireframe = wireframe
        self.marker_color = color_a
        self._delay = delay
        self._max_wait = max_wait
        self._start = time.monotonic()
        self._published = False
        self._check_timer = self.create_timer(0.5, self._maybe_publish)

    def _maybe_publish(self):
        if self._published:
            self._check_timer.cancel()
            return
        elapsed = time.monotonic() - self._start
        subscribed = self.pub.get_subscription_count() > 0
        if elapsed < self._delay:
            return
        if subscribed or elapsed >= self._max_wait:
            self.publish()
            self._published = True
            self._check_timer.cancel()
            self.get_logger().info(
                f"mesh published once: {self.mesh_path} frame={self.frame} "
                f"(mode={'wireframe' if self.wireframe else 'solid'}, "
                f"delay={elapsed:.1f}s, subscribers={self.pub.get_subscription_count()})")

    def _quat_rot_x(self):
        return (math.sin(self.rot_x / 2), 0.0, 0.0, math.cos(self.rot_x / 2))

    def publish(self):
        if self.wireframe:
            m = self._build_wireframe_marker()
        else:
            m = self._build_solid_marker()
        self.pub.publish(m)

    def _build_solid_marker(self):
        m = Marker()
        m.header.frame_id = self.frame
        m.header.stamp = self.get_clock().now().to_msg()
        m.ns = "khronos_mesh"
        m.id = 0
        m.type = Marker.MESH_RESOURCE
        m.action = Marker.ADD
        qx, qy, qz, qw = self._quat_rot_x()
        m.pose.orientation.x, m.pose.orientation.y = qx, qy
        m.pose.orientation.z, m.pose.orientation.w = qz, qw
        m.scale.x = m.scale.y = m.scale.z = 1.0
        m.mesh_resource = f"file://{self.mesh_path}"
        m.mesh_use_embedded_materials = False
        m.color = color(*self.marker_color)
        return m

    def _build_wireframe_marker(self):
        """读 PLY 顶点+面，提取去重边，发布 LINE_LIST。"""
        verts, faces = self._read_ply(self.mesh_path)
        edge_set = set()
        for a, b, c in faces:
            for u, v in ((a, b), (b, c), (c, a)):
                edge_set.add((min(u, v), max(u, v)))
        m = Marker()
        m.header.frame_id = self.frame
        m.header.stamp = self.get_clock().now().to_msg()
        m.ns = "khronos_mesh"
        m.id = 0
        m.type = Marker.LINE_LIST
        m.action = Marker.ADD
        qx, qy, qz, qw = self._quat_rot_x()
        m.pose.orientation.x, m.pose.orientation.y = qx, qy
        m.pose.orientation.z, m.pose.orientation.w = qz, qw
        m.scale.x = 0.1
        m.color = color(*self.marker_color)
        for u, v in edge_set:
            m.points.append(point(*verts[u]))
            m.points.append(point(*verts[v]))
        self.get_logger().info(f"wireframe: {len(edge_set)} edges, {len(verts)} verts")
        return m

    @staticmethod
    def _read_ply(path):
        lines = open(path, "rb").read().decode("ascii", "ignore").splitlines()
        nverts = nfaces = 0
        i = 0
        while i < len(lines):
            l = lines[i]
            if l.startswith("element vertex"):
                nverts = int(l.split()[-1])
            elif l.startswith("element face"):
                nfaces = int(l.split()[-1])
            elif l.strip() == "end_header":
                i += 1
                break
            i += 1
        verts = []
        for j in range(nverts):
            v = lines[i + j].split()
            verts.append((float(v[0]), float(v[1]), float(v[2])))
        faces = []
        for j in range(nfaces):
            f = lines[i + nverts + j].split()
            if f[0] == "3":
                faces.append((int(f[1]), int(f[2]), int(f[3])))
        return verts, faces


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mesh", required=True, help="mesh.ply 绝对路径")
    ap.add_argument("--frame", default="map")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="启动后延迟秒数再发布（等 RViz 订阅，one-shot）")
    ap.add_argument("--max-wait", type=float, default=30.0,
                    help="最大等待订阅者秒数，超时仍发布一次")
    ap.add_argument("--mesh-rotation", type=float, default=0.0,
                    help="绕 X 轴旋转弧度（补偿 assimp 坐标变换）")
    ap.add_argument("--wireframe", default="false",
                    help="true=LINE_LIST 线框 mesh（推荐）；false=MESH_RESOURCE 实心")
    args, _ = ap.parse_known_args()
    wireframe = args.wireframe.lower() in ("true", "1", "yes")

    rclpy.init()
    try:
        node = KhronosMeshPublisher(args.mesh, frame=args.frame,
                                    delay=args.delay, max_wait=args.max_wait,
                                    rot_x=args.mesh_rotation, wireframe=wireframe)
        rclpy.spin(node)
    finally:
        rclpy.shutdown()


if __name__ == "__main__":
    main()
