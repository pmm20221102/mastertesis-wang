"""Khronos -> ANYmal D RViz2 数据加载与 marker 辅助（jazzy）。

注意：mesh z 范围 1.0..4.25（DSG 坐标系），RViz 半透明 mesh 写 depth，
mesh 内部的 marker 必然被遮挡。因此导航层统一悬浮到 DISPLAY_Z=4.6
（mesh 最高点之上），x,y 与 mesh 完全重合，实现 waypoint-mesh 对齐验证。

读取 khronos_anymald_bridge/generated/ 下的产物，供 graph_publisher 和
mock_robot_node 使用。所有 waypoint/dense 坐标的 z 统一投影到 ground_z
（projected_for_mock）。
"""
import json
import math
from pathlib import Path

# 导航层悬浮高度：mesh 最高 z=4.25（PLY 实测），取 4.6 保证全部元素在 mesh 之上
DISPLAY_Z = 4.6

from geometry_msgs.msg import Point, Quaternion
from std_msgs.msg import ColorRGBA, Header
from visualization_msgs.msg import Marker

from .witness_expand import expand_route


def quaternion_from_yaw(yaw: float) -> Quaternion:
    return Quaternion(x=0.0, y=0.0, z=math.sin(yaw / 2.0), w=math.cos(yaw / 2.0))


def color(r, g, b, a=1.0) -> ColorRGBA:
    return ColorRGBA(r=r, g=g, b=b, a=a)


def header(frame: str, stamp=None) -> Header:
    h = Header(frame_id=frame)
    if stamp is not None:
        h.stamp = stamp
    return h


class BridgeData:
    """读取 khronos_anymald_bridge/generated 数据。"""

    def __init__(self, base_dir: str, r: float = 3.0, ground_z: float = 0.0,
                 frame: str = "map"):
        self.base = Path(base_dir)
        self.gen = self.base / "generated"
        self.r = r
        self.ground_z = ground_z
        self.frame = frame
        self.sparsified = json.load(
            open(self.gen / f"waypoints_sparsified_R{r}.json", encoding="utf-8"))
        self.full = json.load(open(self.gen / "waypoints.json", encoding="utf-8"))
        self.witness = json.load(open(self.gen / "witness_paths.json", encoding="utf-8"))
        self.semantic = json.load(
            open(self.gen / "semantic_to_waypoint.json", encoding="utf-8"))
        self._spars_pos = {n["id"]: n["pose_stamped"]["position"]
                           for n in self.sparsified["nodes"]}
        self._full_pos = {n["id"]: n["pose_stamped"]["position"]
                          for n in self.full["nodes"]}
        # 原始 z（DSG 同坐标系：waypoints.json 保留原始 z，mesh z=1..4.25，
        # 与 waypoint 原始 z 一致；sparsified 已 ground 投影到 ground_z）。
        # RViz 显示用原始 z 实现 waypoint 与 mesh 完全重合。
        self._orig_z = {}
        for _n in self.full["nodes"]:
            _pos = _n["pose_stamped"]["position"]
            _z = (_pos[2] if isinstance(_pos, list) and len(_pos) >= 3 else
                  (_pos.get("z") if isinstance(_pos, dict) else None))
            if _z is not None:
                self._orig_z[int(_n["id"])] = float(_z)

    # ---- 位置 ----
    def spars_pos(self, wid) -> tuple | None:
        p = self._spars_pos.get(int(wid))
        if p is None:
            return None
        return (p[0], p[1], DISPLAY_Z)

    def dense_pos(self, pid) -> tuple | None:
        p = self._full_pos.get(int(pid))
        if p is None:
            return None
        return (p[0], p[1], DISPLAY_Z)

    def waypoint_ids(self) -> list:
        return sorted(self._spars_pos)

    def edges(self) -> list[tuple[int, int]]:
        return [(int(e["start_node_id"]), int(e["end_node_id"]))
                for e in self.sparsified["edges"]]

    # ---- witness / stretch ----
    def witness_of(self, a, b) -> dict | None:
        return self.witness.get(f"({min(a, b)},{max(a, b)})")

    def stretch_of(self, a, b) -> float | None:
        w = self.witness_of(a, b)
        return w["stretch"] if w else None

    # ---- semantic ----
    def room_anchors(self) -> dict[int, int]:
        out = {}
        for info in self.semantic["room_to_waypoint"].values():
            if info.get("status") == "ok":
                out[info["room_index"]] = info["waypoint_id"]
        return out

    # ---- route 展开 ----
    def route_positions(self, route: list[int]) -> list[tuple]:
        """route（waypoint id 序列）→ 沿 dense witness path 展开的坐标序列。

        若 route 方向与 stored witness（min→max）相反，则 reverse witness path
        （phase85_reaudit §2 Fix 2）。展开逻辑见 witness_expand.expand_route。
        """
        return expand_route(route, self.witness, self.spars_pos, self.dense_pos)


def dist(a, b) -> float:
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in range(3)))


def make_sphere_marker(ns, mid, pos, scale, rgba, frame="map", stamp=None) -> Marker:
    m = Marker()
    m.header = header(frame, stamp)
    m.ns = ns
    m.id = mid
    m.type = Marker.SPHERE
    m.action = Marker.ADD
    m.pose.position.x, m.pose.position.y, m.pose.position.z = pos
    m.pose.orientation = quaternion_from_yaw(0.0)
    m.scale.x = m.scale.y = m.scale.z = scale
    m.color = color(*rgba)
    return m


def point(x, y, z) -> Point:
    return Point(x=float(x), y=float(y), z=float(z))
