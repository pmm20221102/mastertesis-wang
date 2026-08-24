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
import json
import math
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Int32
from visualization_msgs.msg import Marker, MarkerArray

from .data_loader import color, floor_offset, floor_z_bounds, point


def clip_segment_to_z(p0, p1, low: float, high: float):
    """Clip one 3D segment to a horizontal z slab."""
    z0, z1 = p0[2], p1[2]
    if abs(z1 - z0) < 1e-12:
        return (p0, p1) if low <= z0 <= high else None
    clipped_low = max(min(z0, z1), low)
    clipped_high = min(max(z0, z1), high)
    if clipped_high - clipped_low <= 1e-9:
        return None
    ta, tb = (clipped_low - z0) / (z1 - z0), (clipped_high - z0) / (z1 - z0)
    ta, tb = min(ta, tb), max(ta, tb)
    lerp = lambda t: tuple(p0[i] + (p1[i] - p0[i]) * t for i in range(3))
    return lerp(ta), lerp(tb)


class KhronosMeshPublisher(Node):
    def __init__(self, mesh_path: str, frame: str = "map",
                 delay: float = 2.0, max_wait: float = 30.0,
                 rot_x: float = 0.0, wireframe: bool = False,
                 max_edges: int = 120000,
                 color_a: tuple = (0.55, 0.68, 0.82, 0.32),
                 floor_model: str | None = None, floor_spacing: float = 0.0,
                 floor_columns: int = 1, focus_floors: bool = False,
                 initial_floor: int = 0):
        super().__init__("khronos_mesh_publisher")
        latch = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                           durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub = self.create_publisher(Marker, "/anymald/khronos_mesh", latch)
        self.pub_floors = self.create_publisher(
            MarkerArray, "/anymald/khronos_mesh_floors", latch)
        self.mesh_path = mesh_path
        self.frame = frame
        self.rot_x = rot_x
        self.wireframe = wireframe
        self.max_edges = max_edges
        self.floor_spacing = float(floor_spacing)
        self.floor_columns = max(1, int(floor_columns))
        self.focus_floors = focus_floors
        self.active_floor = initial_floor
        self.floor_markers = {}
        self.floor_medians = {}
        if floor_model:
            with open(floor_model, encoding="utf-8") as stream:
                model = json.load(stream)
            self.floor_medians = {
                int(info["floor_id"]): float(info["z_median"])
                for info in model.get("floors", [])}
        if (self.floor_spacing or self.focus_floors) and not self.floor_medians:
            raise ValueError("floor display requires floor_model")
        if (self.floor_spacing or self.focus_floors) and not wireframe:
            raise ValueError("floor display requires wireframe mesh mode")
        self.marker_color = ((0.92, 0.92, 0.94, 0.82)
                             if self.floor_spacing or self.focus_floors else color_a)
        if self.focus_floors:
            self.floor_sub = self.create_subscription(
                Int32, "/anymald/active_floor", self._active_floor_cb, latch)
        self._delay = delay
        self._max_wait = max_wait
        self._start = time.monotonic()
        self._published = False
        self._check_timer = self.create_timer(0.5, self._maybe_publish)

    def _active_floor_cb(self, msg):
        if self.active_floor == msg.data:
            return
        self.active_floor = msg.data
        if self._published:
            self._publish_active_floor()

    def _maybe_publish(self):
        if self._published:
            self._check_timer.cancel()
            return
        elapsed = time.monotonic() - self._start
        active_pub = self.pub_floors if self.focus_floors else self.pub
        subscribed = active_pub.get_subscription_count() > 0
        if elapsed < self._delay:
            return
        if subscribed or elapsed >= self._max_wait:
            self.publish()
            self._published = True
            self._check_timer.cancel()
            self.get_logger().info(
                f"mesh published once: {self.mesh_path} frame={self.frame} "
                f"(mode={'wireframe' if self.wireframe else 'solid'}, "
                f"delay={elapsed:.1f}s, subscribers={active_pub.get_subscription_count()})")

    def _quat_rot_x(self):
        return (math.sin(self.rot_x / 2), 0.0, 0.0, math.cos(self.rot_x / 2))

    def publish(self):
        if self.focus_floors:
            self.floor_markers = self._build_floor_wireframe_markers()
            self._publish_active_floor()
            return
        if self.wireframe:
            m = self._build_wireframe_marker()
        else:
            m = self._build_solid_marker()
        self.pub.publish(m)

    def _publish_active_floor(self):
        clear = Marker()
        clear.header.frame_id = self.frame
        clear.header.stamp = self.get_clock().now().to_msg()
        clear.action = Marker.DELETEALL
        markers = [clear]
        marker = self.floor_markers.get(self.active_floor)
        if marker is not None:
            marker.header.stamp = clear.header.stamp
            markers.append(marker)
        self.pub_floors.publish(MarkerArray(markers=markers))

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
        # ponytail: evenly stride faces to cap RViz Marker size; use a mesh
        # decimator if publication-quality surface fidelity is later required.
        stride = max(1, math.ceil(len(faces) * 3 / self.max_edges)) \
            if self.max_edges > 0 else 1
        for a, b, c in faces[::stride]:
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
        m.scale.x = 0.04 if self.floor_spacing else 0.035
        m.color = color(*self.marker_color)
        for u, v in edge_set:
            floor = self._floor_of_z((verts[u][2] + verts[v][2]) / 2.0)
            dx, dy = floor_offset(floor, self.floor_spacing, self.floor_columns)
            m.points.append(point(verts[u][0] + dx, verts[u][1] + dy, verts[u][2]))
            m.points.append(point(verts[v][0] + dx, verts[v][1] + dy, verts[v][2]))
        self.get_logger().info(
            f"wireframe: {len(edge_set)} edges, {len(verts)} verts, face_stride={stride}")
        return m

    def _build_floor_wireframe_markers(self):
        """Build genuinely clipped floor slices for Office-style floor focus."""
        verts, faces = self._read_ply(self.mesh_path)
        edge_set = set()
        stride = max(1, math.ceil(len(faces) * 3 / self.max_edges)) \
            if self.max_edges > 0 else 1
        for a, b, c in faces[::stride]:
            for u, v in ((a, b), (b, c), (c, a)):
                edge_set.add((min(u, v), max(u, v)))
        markers = {}
        bounds = floor_z_bounds(self.floor_medians)
        for floor in sorted(bounds):
            marker = Marker()
            marker.header.frame_id = self.frame
            marker.ns = "khronos_mesh_floor"
            marker.id = floor
            marker.type = Marker.LINE_LIST
            marker.action = Marker.ADD
            qx, qy, qz, qw = self._quat_rot_x()
            marker.pose.orientation.x, marker.pose.orientation.y = qx, qy
            marker.pose.orientation.z, marker.pose.orientation.w = qz, qw
            marker.scale.x = 0.035
            marker.color = color(*self.marker_color)
            dx, dy = floor_offset(floor, self.floor_spacing, self.floor_columns)
            low, high = bounds[floor]
            for u, v in edge_set:
                clipped = clip_segment_to_z(verts[u], verts[v], low, high)
                if clipped is None:
                    continue
                p0, p1 = clipped
                marker.points.append(point(p0[0] + dx, p0[1] + dy, p0[2]))
                marker.points.append(point(p1[0] + dx, p1[1] + dy, p1[2]))
            markers[floor] = marker
        self.get_logger().info(
            f"strict floor mesh: {len(edge_set)} source edges -> "
            + ", ".join(f"F{floor}={len(m.points) // 2}"
                        for floor, m in markers.items()))
        return markers

    def _floor_of_z(self, z: float) -> int:
        if not self.floor_medians:
            return 0
        return min(self.floor_medians,
                   key=lambda floor: abs(z - self.floor_medians[floor]))

    @staticmethod
    def _read_ply(path):
        nverts = nfaces = 0
        with open(path, encoding="ascii", errors="ignore") as stream:
            for line in stream:
                if line.startswith("format") and "ascii" not in line:
                    raise ValueError("wireframe mode requires an ASCII PLY")
                if line.startswith("element vertex"):
                    nverts = int(line.split()[-1])
                elif line.startswith("element face"):
                    nfaces = int(line.split()[-1])
                elif line.strip() == "end_header":
                    break
            verts = []
            for _ in range(nverts):
                v = stream.readline().split()
                verts.append((float(v[0]), float(v[1]), float(v[2])))
            faces = []
            for _ in range(nfaces):
                f = stream.readline().split()
                if f and f[0] == "3":
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
    ap.add_argument("--max-edges", type=int, default=120000,
                    help="线框最大近似边数；0=不抽稀")
    ap.add_argument("--floor-model", default=None,
                    help="floor_model_v2.json")
    ap.add_argument("--floor-spacing", type=float, default=0.0,
                    help="与 waypoint 相同的楼层 X 轴展开间距")
    ap.add_argument("--floor-columns", type=int, default=1,
                    help="与 waypoint 相同的楼层布局列数")
    ap.add_argument("--focus-floors", default="false",
                    help="按 /anymald/active_floor 发布严格楼层切片")
    ap.add_argument("--initial-floor", type=int, default=0)
    args, _ = ap.parse_known_args()
    wireframe = args.wireframe.lower() in ("true", "1", "yes")

    rclpy.init()
    try:
        node = KhronosMeshPublisher(args.mesh, frame=args.frame,
                                    delay=args.delay, max_wait=args.max_wait,
                                    rot_x=args.mesh_rotation, wireframe=wireframe,
                                    max_edges=args.max_edges,
                                    floor_model=args.floor_model,
                                    floor_spacing=args.floor_spacing,
                                    floor_columns=args.floor_columns,
                                    focus_floors=args.focus_floors.lower()
                                    in ("true", "1", "yes"),
                                    initial_floor=args.initial_floor)
        try:
            rclpy.spin(node)
        except (KeyboardInterrupt, ExternalShutdownException):
            pass
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
