"""Phase 3R: graph-aware sparsification（替换旧的 3D 欧氏 FPS）。

依据 khronos_anymald_phase0_8_audit 返工要求：
- 禁止 global Euclidean FPS + nearest representative（会产生 topology shortcut）
- 所有 clustering / assignment 限制在 source connected component 内
- 用 geodesic（图上最短路）距离，而非纯欧氏
- 每条 sparse edge 保留 dense graph witness path
- 不允许 source disconnected components 被静默合并
"""
import heapq
import math
from dataclasses import dataclass, field

from dsg_reader.loader import PlaceEdge, PlacesGraph


def build_adj(graph: PlacesGraph) -> dict[int, list[tuple[int, float]]]:
    adj: dict[int, list[tuple[int, float]]] = {i: [] for i in graph.nodes}
    for e in graph.edges:
        adj.setdefault(e.source, []).append((e.target, e.weight))
        adj.setdefault(e.target, []).append((e.source, e.weight))
    return adj


def connected_components(graph: PlacesGraph) -> list[list[int]]:
    adj = build_adj(graph)
    visited: set = set()
    comps: list[list[int]] = []
    for i in graph.nodes:
        if i in visited:
            continue
        stack, comp = [i], []
        visited.add(i)
        while stack:
            c = stack.pop()
            comp.append(c)
            for nb, _w in adj.get(c, []):
                if nb not in visited:
                    visited.add(nb)
                    stack.append(nb)
        comps.append(comp)
    return comps


def _dijkstra(adj, source: int):
    """从 source 的 (dist, prev)。"""
    INF = float("inf")
    dist = {source: 0.0}
    prev: dict[int, int] = {}
    pq = [(0.0, source)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, INF):
            continue
        for v, w in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, INF):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    return dist, prev


def _reconstruct_path(prev: dict, source: int, target: int) -> list[int] | None:
    if target not in prev and source != target:
        return None
    path = [target]
    cur = target
    while cur != source:
        cur = prev[cur]
        path.append(cur)
    path.reverse()
    return path


def _path_length(adj, path: list[int]) -> float:
    """沿 dense 图路径累加权重。"""
    wmap = {}
    for u in path:
        for v, w in adj.get(u, []):
            wmap[(u, v)] = w
    return sum(wmap.get((path[i], path[i + 1]), float("inf"))
               for i in range(len(path) - 1))


def _fps_geodesic(adj, nodes: list[int], R_geo: float):
    """在 nodes（单个连通分量）内，用 geodesic 距离做 FPS 选代表。

    返回 (reps, assigned, cover_dist)：
      assigned[i]   = 节点 i 归属的代表
      cover_dist[i] = 节点 i 到其代表的 geodesic 距离（<= R_geo）
    """
    nodes = list(nodes)
    start = nodes[0]
    reps = [start]
    dist, _ = _dijkstra(adj, start)
    cur_dist = {i: dist.get(i, float("inf")) for i in nodes}
    assigned = {i: start for i in nodes}
    while True:
        far = max(nodes, key=lambda i: cur_dist[i])
        if cur_dist[far] <= R_geo:
            break
        reps.append(far)
        d_far, _ = _dijkstra(adj, far)
        for i in nodes:
            d = d_far.get(i, float("inf"))
            if d < cur_dist[i]:
                cur_dist[i] = d
                assigned[i] = far
    return reps, assigned, cur_dist


@dataclass
class SparseResult:
    graph: PlacesGraph                        # sparse graph（节点 = 代表）
    assigned: dict[int, int]                  # place index -> rep index
    witnesses: dict[tuple[int, int], list[int]]  # (a,b) -> dense path（index 序列）
    components: list[list[int]]               # source 连通分量
    cover_dist: dict[int, float]              # place -> 到代表的 geodesic 距离
    stretch: dict[tuple[int, int], float]     # (a,b) -> dense_geo / euclid
    geodesic_len: dict[tuple[int, int], float]  # (a,b) -> witness geodesic 长度


def sparsify_graphaware(graph: PlacesGraph, R_geo: float) -> SparseResult:
    """graph-aware 稀疏化主函数。

    - 每个 source 连通分量独立 FPS（geodesic），不允许跨分量 assignment
    - sparse edge 只来自"原图边两端分属不同代表"，并保存 dense witness path
    - edge weight = witness 路径的 geodesic 长度
    """
    adj = build_adj(graph)
    comps = connected_components(graph)

    reps_all: list[int] = []
    assigned_all: dict[int, int] = {}
    cover_all: dict[int, float] = {}
    for comp in comps:
        reps, assigned, cover = _fps_geodesic(adj, comp, R_geo)
        reps_all.extend(reps)
        assigned_all.update(assigned)
        cover_all.update(cover)

    rep_set = set(reps_all)
    sparse_edges: list[PlaceEdge] = []
    witnesses: dict[tuple[int, int], list[int]] = {}
    stretch: dict[tuple[int, int], float] = {}
    geodesic_len: dict[tuple[int, int], float] = {}
    seen: set = set()

    pos = graph.nodes
    for e in graph.edges:
        a, b = assigned_all[e.source], assigned_all[e.target]
        if a == b:
            continue
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        # witness path = dense graph 中代表间最短路（用排序后端点保证一致）
        dist, prev = _dijkstra(adj, key[0])
        path = _reconstruct_path(prev, key[0], key[1])
        if path is None:
            continue
        geo = dist[key[1]]
        # 欧氏距离（3D）
        pa, pb = pos[key[0]].position, pos[key[1]].position
        euc = math.sqrt(sum((pa[k] - pb[k]) ** 2 for k in range(3)))
        witnesses[key] = path
        geodesic_len[key] = geo
        stretch[key] = (geo / euc) if euc > 1e-9 else float("inf")
        sparse_edges.append(PlaceEdge(key[0], key[1], geo))

    new_nodes = {r: graph.nodes[r] for r in reps_all}
    sparse_graph = PlacesGraph(
        frame=graph.frame,
        nodes=new_nodes,
        edges=sparse_edges,
        n_total_nodes=len(graph.nodes),
        n_total_edges=len(graph.edges),
    )
    return SparseResult(
        graph=sparse_graph,
        assigned=assigned_all,
        witnesses=witnesses,
        components=comps,
        cover_dist=cover_all,
        stretch=stretch,
        geodesic_len=geodesic_len,
    )


def filter_high_stretch(res: SparseResult, max_stretch: float):
    """过滤 geodesic stretch > max_stretch 的 sparse edge。

    依据 audit §18：stretch 太大意味着潜在跨墙/跨走廊 shortcut，
    ANYmal edge 语义要求两 waypoint 之间可被 local planner 直接走。
    返回 (新图, 保留边数, 孤立代表列表)。
    """
    keep = []
    for e in res.graph.edges:
        key = tuple(sorted((e.source, e.target)))
        s = res.stretch.get(key, float("inf"))
        if math.isfinite(s) and s <= max_stretch:
            keep.append(e)
    new_graph = PlacesGraph(
        frame=res.graph.frame,
        nodes=res.graph.nodes,
        edges=keep,
        n_total_nodes=res.graph.n_total_nodes,
        n_total_edges=res.graph.n_total_edges,
    )
    deg = {i: 0 for i in new_graph.nodes}
    for e in keep:
        deg[e.source] += 1
        deg[e.target] += 1
    isolated = [i for i in new_graph.nodes if deg[i] == 0]
    return new_graph, len(keep), isolated
