"""Phase 3R: geodesic stretch validator（审计 §18）。

stretch = dense_graph_shortest_path(A,B) / euclidean_distance(A,B)

含义：两个 waypoint 空间上多近 vs 真实自由空间拓扑上要多绕。
- stretch 大 → 潜在跨墙 / 跨走廊 / 隔墙 shortcut
- ANYmal edge 若语义为"两 waypoint 间 local planner 直接走"，stretch 应小

用途：报告 stretch 分布 + 标记高 stretch 边，不静默删除（删除会破坏区域连通）。
"""
import math
from dataclasses import dataclass


@dataclass
class StretchReport:
    n_edges: int
    median: float
    mean: float
    max: float
    p90: float
    over_thresholds: dict[float, int]
    top_edges: list[tuple[tuple[int, int], float]]


def compute_stretch_report(stretch: dict, thresholds=(1.2, 1.5, 2.0, 3.0, 5.0),
                           top_n: int = 10) -> StretchReport:
    items = [(k, v) for k, v in stretch.items() if math.isfinite(v)]
    if not items:
        return StretchReport(0, 0.0, 0.0, 0.0, 0.0, {}, [])
    vals = sorted(v for _, v in items)
    n = len(vals)
    def pct(p):
        idx = min(int(p * n), n - 1)
        return vals[idx]
    over = {t: sum(1 for v in vals if v > t) for t in thresholds}
    top = sorted(items, key=lambda kv: -kv[1])[:top_n]
    return StretchReport(
        n_edges=n,
        median=round(pct(0.5), 3),
        mean=round(sum(vals) / n, 3),
        max=round(vals[-1], 3),
        p90=round(pct(0.9), 3),
        over_thresholds=over,
        top_edges=[((int(a), int(b)), round(v, 3)) for (a, b), v in top],
    )


def high_stretch_edges(stretch: dict, threshold: float):
    """返回 stretch > threshold 的边（sorted 列表）。"""
    return sorted(((k, v) for k, v in stretch.items()
                   if math.isfinite(v) and v > threshold),
                  key=lambda kv: -kv[1])
