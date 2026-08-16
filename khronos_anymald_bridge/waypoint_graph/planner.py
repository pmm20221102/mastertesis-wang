"""Phase 5R: Mock ANYmal Waypoint Planner（Dijkstra，witness geodesic cost）。

边权（phase85_reaudit §1 Fix 1）：
- 若提供 witness_paths.json（edge-cost sidecar），Dijkstra cost = witness `geodesic_m`
  （沿真实 dense free-space 拓扑的长度）
- 否则 fallback 欧氏距离（用于无 witness 的 fixture / 通用场景）

验证 waypoint graph 是否合法、连通、可规划、goal 可达。
"""
import heapq
import json
import math


class WaypointGraph:
    """从 waypoints.json（ANYmal schema）+ 可选 witness_paths.json 构建有向图。"""

    def __init__(self, waypoints: dict, witness: dict | None = None):
        self.nodes: dict[int, dict] = {n["id"]: n for n in waypoints["nodes"]}
        self.witness: dict = witness or {}
        self.adj: dict[int, list[tuple[int, float]]] = {i: [] for i in self.nodes}
        for e in waypoints["edges"]:
            a, b = int(e["start_node_id"]), int(e["end_node_id"])
            w = self._edge_cost(a, b)
            self.adj.setdefault(a, []).append((b, w))

    @classmethod
    def load(cls, path: str, witness_path: str | None = None) -> "WaypointGraph":
        with open(path, encoding="utf-8") as f:
            wp = json.load(f)
        wit = None
        if witness_path:
            with open(witness_path, encoding="utf-8") as f:
                wit = json.load(f)
        return cls(wp, wit)

    def _edge_cost(self, a: int, b: int) -> float:
        w = self.witness.get(f"({min(a, b)},{max(a, b)})")
        if isinstance(w, dict):
            # 优先 projected_xy_length_m（物理米制，与 RViz mock 动画长度一致，MD §5.3）
            for key in ("projected_xy_length_m", "metric_3d_length_m", "geodesic_m"):
                v = w.get(key)
                if isinstance(v, (int, float)):
                    return float(v)
        return self._dist(a, b)

    def _dist(self, a: int, b: int) -> float:
        pa = self.nodes[a]["pose_stamped"]["position"]
        pb = self.nodes[b]["pose_stamped"]["position"]
        return math.dist(pa, pb)

    def has_node(self, nid: int) -> bool:
        return nid in self.nodes


def shortest_path(graph: WaypointGraph, start: int, goal: int):
    """Dijkstra 最短路。返回 (path, cost)；不可达返回 (None, inf)。"""
    if start not in graph.nodes or goal not in graph.nodes:
        return None, float("inf")
    if start == goal:
        return [start], 0.0

    INF = float("inf")
    dist = {start: 0.0}
    prev: dict[int, int] = {}
    pq = [(0.0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == goal:
            break
        if d > dist.get(u, INF):
            continue
        for v, w in graph.adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, INF):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))

    if goal not in dist:
        return None, INF
    path = []
    cur = goal
    while cur != start:
        path.append(cur)
        cur = prev[cur]
    path.append(start)
    path.reverse()
    return path, dist[goal]
