"""Khronos -> ANYmal D RViz2 数据加载与 marker 辅助（jazzy）。

Office demo 默认把导航层投影到 DISPLAY_Z；Hospital 通过 ``project_z=None``
保留 waypoint/canonical graph 的原生三维坐标。两者共用同一套发布节点。
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


def floor_offset(floor: int, spacing: float, columns: int = 1) -> tuple[float, float]:
    columns = max(1, int(columns))
    return ((floor % columns) * spacing, -(floor // columns) * spacing)


def floor_z_bounds(medians: dict[int, float]) -> dict[int, tuple[float, float]]:
    ordered = sorted(medians.items(), key=lambda item: item[1])
    bounds = {}
    for i, (floor, _) in enumerate(ordered):
        low = -math.inf if i == 0 else (ordered[i - 1][1] + ordered[i][1]) / 2
        high = math.inf if i + 1 == len(ordered) else (
            ordered[i][1] + ordered[i + 1][1]) / 2
        bounds[floor] = (low, high)
    return bounds


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
    """读取 Office generated/ 或 Hospital waypoint_v2/ 数据。"""

    def __init__(self, base_dir: str, r: float = 3.0, ground_z: float = 0.0,
                 frame: str = "map", data_dir: str | None = None,
                 canonical_graph: str | None = None,
                 vertical_transitions: str | None = None,
                 project_z: float | None = DISPLAY_Z,
                 floor_model: str | None = None,
                 floor_spacing: float = 0.0, floor_columns: int = 1,
                 z_offset: float = 0.0):
        self.base = Path(base_dir)
        self.gen = Path(data_dir) if data_dir else self.base / "generated"
        self.r = r
        self.ground_z = ground_z
        self.frame = frame
        self.project_z = project_z
        self.floor_spacing = float(floor_spacing)
        self.floor_columns = max(1, int(floor_columns))
        self.z_offset = float(z_offset)
        with open(self.gen / f"waypoints_sparsified_R{r}.json", encoding="utf-8") as f:
            self.sparsified = json.load(f)
        with open(self.gen / "witness_paths.json", encoding="utf-8") as f:
            self.witness = json.load(f)
        with open(self.gen / "semantic_to_waypoint.json", encoding="utf-8") as f:
            self.semantic = json.load(f)
        self._spars_pos = {n["id"]: n["pose_stamped"]["position"]
                           for n in self.sparsified["nodes"]}
        self.floor_model_path = self._existing_path(
            floor_model, self.gen.parent / "floor_model_v2.json")
        self._waypoint_floor = {}
        self._place_floor = {}
        self.floor_medians = {}
        if self.floor_model_path:
            with open(self.floor_model_path, encoding="utf-8") as f:
                floors = json.load(f)
            mask = (1 << 56) - 1
            self._waypoint_floor = {
                int(wid): int(floor) for wid, floor
                in floors.get("waypoint_to_floor", {}).items()}
            self._place_floor = {
                int(pid) & mask: int(floor) for pid, floor
                in floors.get("place_to_floor", {}).items()}
            self.floor_medians = {
                int(info["floor_id"]): float(info["z_median"])
                for info in floors.get("floors", [])}
        self._full_pos = self._load_dense_positions(canonical_graph)
        self._full_pos.update(self._spars_pos)
        self.vertical_transitions_path = self._existing_path(
            vertical_transitions, self.gen.parent / "vertical_transitions_v2.json")
        if self.vertical_transitions_path:
            with open(self.vertical_transitions_path, encoding="utf-8") as f:
                self._add_vertical_witnesses(json.load(f))

    @staticmethod
    def _existing_path(explicit: str | None, inferred: Path) -> Path | None:
        path = Path(explicit) if explicit else inferred
        return path if path.is_file() else None

    @staticmethod
    def _xyz(position) -> tuple[float, float, float]:
        if isinstance(position, dict):
            return (float(position["x"]), float(position["y"]), float(position["z"]))
        return tuple(float(v) for v in position[:3])

    def _load_dense_positions(self, canonical_graph: str | None) -> dict[int, object]:
        full_path = self.gen / "waypoints.json"
        if full_path.is_file():
            with open(full_path, encoding="utf-8") as f:
                full = json.load(f)
            return {int(n["id"]): n["pose_stamped"]["position"]
                    for n in full["nodes"]}

        canonical_path = self._existing_path(
            canonical_graph, self.gen.parent / "canonical_graph_v2.json")
        if not canonical_path:
            return {}
        with open(canonical_path, encoding="utf-8") as f:
            canonical = json.load(f)
        # Spark-DSG symbol IDs store the layer prefix in the high byte. Witness
        # paths use the lower 56-bit place index.
        mask = (1 << 56) - 1
        return {
            int(node_id) & mask: node["position"]
            for node_id, node in canonical.get("nodes", {}).items()
            if node.get("node_type") == "place" and node.get("position")
        }

    def _add_vertical_witnesses(self, data: dict) -> None:
        """Expose validated stair/elevator place trajectories to route expansion."""
        mask = (1 << 56) - 1
        for transition in data.get("transitions", []):
            a = int(transition["entry_waypoint"])
            b = int(transition["exit_waypoint"])
            path = [a]
            path.extend(int(pid) & mask for pid in transition.get("place_path", []))
            path.append(b)
            path = [pid for i, pid in enumerate(path) if i == 0 or pid != path[i - 1]]
            if a > b:
                path.reverse()
            self.witness.setdefault(
                f"({min(a, b)},{max(a, b)})",
                {"path": path,
                 "metric_3d_length_m": float(transition.get("cost_m", 0.0)),
                 "vertical_transition_id": transition.get("id")},
            )

    def floor_of_z(self, z: float) -> int:
        if not self.floor_medians:
            return 0
        return min(self.floor_medians, key=lambda floor: abs(z - self.floor_medians[floor]))

    def floor_of_waypoint(self, wid: int) -> int:
        p = self._spars_pos[int(wid)]
        return self._waypoint_floor.get(int(wid), self.floor_of_z(self._xyz(p)[2]))

    def _display_pos(self, position, floor: int | None = None) -> tuple[float, float, float]:
        x, y, z = self._xyz(position)
        floor = self.floor_of_z(z) if floor is None else floor
        display_z = z if self.project_z is None else float(self.project_z)
        dx, dy = floor_offset(floor, self.floor_spacing, self.floor_columns)
        return (x + dx, y + dy, display_z + self.z_offset)

    # ---- 位置 ----
    def spars_pos(self, wid) -> tuple | None:
        p = self._spars_pos.get(int(wid))
        if p is None:
            return None
        return self._display_pos(p, self.floor_of_waypoint(int(wid)))

    def dense_pos(self, pid) -> tuple | None:
        p = self._full_pos.get(int(pid))
        if p is None:
            return None
        floor = self._waypoint_floor.get(int(pid), self._place_floor.get(int(pid)))
        return self._display_pos(p, floor)

    def waypoint_ids(self) -> list:
        return sorted(self._spars_pos)

    def edges(self) -> list[tuple[int, int]]:
        return [(int(e["start_node_id"]), int(e["end_node_id"]))
                for e in self.sparsified["edges"]]

    def navigation_edges(self) -> list[tuple[int, int]]:
        return self.edges() + self.vertical_edges()

    def vertical_edges(self) -> list[tuple[int, int]]:
        return [
            tuple(int(v) for v in key.strip("()").split(","))
            for key, witness in self.witness.items()
            if witness.get("vertical_transition_id")]

    def display_edges(self) -> list[tuple[int, int]]:
        if not self.floor_spacing:
            return self.edges()
        intra_floor = [
            (a, b) for a, b in self.edges()
            if self.floor_of_waypoint(a) == self.floor_of_waypoint(b)]
        return intra_floor + self.vertical_edges()

    def reachable_waypoint_ids(self, start: int) -> set[int]:
        adjacency = {wid: set() for wid in self._spars_pos}
        for a, b in self.navigation_edges():
            adjacency.setdefault(a, set()).add(b)
            adjacency.setdefault(b, set()).add(a)
        seen = {int(start)}
        stack = [int(start)]
        while stack:
            current = stack.pop()
            for neighbor in adjacency.get(current, ()):
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        return seen

    # ---- witness / stretch ----
    def witness_of(self, a, b) -> dict | None:
        return self.witness.get(f"({min(a, b)},{max(a, b)})")

    def stretch_of(self, a, b) -> float | None:
        w = self.witness_of(a, b)
        if not w:
            return None
        return w.get("topology_stretch", w.get("stretch"))

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
