#!/usr/bin/env python3
"""Phase 3R: 高 stretch 边根因分析。

判断高 stretch 边是否主要是 3D 多层 GVD 的垂直边（Δz 大）：
- 列出 stretch > 2 的边，分析端点 z、Δz、witness 长度、欧氏(3D/2D)
- 评估 ground projection 后 stretch 是否会改变
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsg_reader.loader import load_places  # noqa: E402
from waypoint_graph.sparsifier import sparsify_graphaware  # noqa: E402

DSG = "/home/ulmmt/Masterthesis-wang/scene-graph-khronos/snapshots/office_launch_dense/2026_08_03-16_46_38/dsg.json"


def main():
    g = load_places(DSG)
    res = sparsify_graphaware(g, R_geo=3.0)

    high = [(k, v) for k, v in res.stretch.items()
            if math.isfinite(v) and v > 2.0]
    high.sort(key=lambda kv: -kv[1])

    print(f"R_geo=3: {len(res.graph.nodes)} reps, {len(res.graph.edges)} edges, "
          f"stretch>2: {len(high)}")

    print("\n=== 高 stretch 边 top20 分析 ===")
    print(f"{'edge':<14}{'stretch':>8}{'geo_len':>9}{'euc3d':>8}{'euc2d':>8}"
          f"{'dz':>6}{'zA':>6}{'zB':>6}")
    for (a, b), s in high[:20]:
        pa, pb = g.nodes[a].position, g.nodes[b].position
        geo = res.geodesic_len[(a, b)]
        euc3 = math.sqrt(sum((pa[k] - pb[k]) ** 2 for k in range(3)))
        euc2 = math.sqrt((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2)
        dz = abs(pa[2] - pb[2])
        print(f"({a},{b})".ljust(14) + f"{s:>8.2f}{geo:>9.1f}{euc3:>8.2f}"
              f"{euc2:>8.2f}{dz:>6.2f}{pa[2]:>6.1f}{pb[2]:>6.1f}")

    # Δz 统计（所有 stretch>2 的边）
    dzs = []
    for (a, b), s in high:
        pa, pb = g.nodes[a].position, g.nodes[b].position
        dzs.append(abs(pa[2] - pb[2]))
    if dzs:
        dzs.sort()
        print(f"\nstretch>2 边的 |Δz|: median={dzs[len(dzs)//2]:.2f} "
              f"mean={sum(dzs)/len(dzs):.2f} max={dzs[-1]:.2f} "
              f"|Δz|>1.0: {sum(1 for d in dzs if d>1.0)}/{len(dzs)}")

    # 全部边的 Δz 对比
    all_dz = []
    for (a, b) in res.stretch:
        pa, pb = g.nodes[a].position, g.nodes[b].position
        all_dz.append(abs(pa[2] - pb[2]))
    all_dz.sort()
    print(f"全部边 |Δz|: median={all_dz[len(all_dz)//2]:.2f} "
          f"max={all_dz[-1]:.2f} |Δz|>1.0: {sum(1 for d in all_dz if d>1.0)}/{len(all_dz)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
