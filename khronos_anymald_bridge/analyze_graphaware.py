#!/usr/bin/env python3
"""Phase 3R: graph-aware sparsification 真实数据验证（含 stretch 过滤分析）。"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsg_reader.loader import load_places  # noqa: E402
from waypoint_graph.sparsifier import (  # noqa: E402
    connected_components, filter_high_stretch, sparsify_graphaware,
)

DSG = "/home/ulmmt/Masterthesis-wang/scene-graph-khronos/snapshots/office_launch_dense/2026_08_03-16_46_38/dsg.json"


def stretch_stats(stretch: dict) -> dict:
    vals = [v for v in stretch.values() if math.isfinite(v)]
    if not vals:
        return {}
    s = sorted(vals)
    return {
        "median": round(s[len(s) // 2], 2),
        "mean": round(sum(s) / len(s), 2),
        "max": round(s[-1], 2),
        ">1.5": sum(1 for v in vals if v > 1.5),
        ">2": sum(1 for v in vals if v > 2.0),
        ">5": sum(1 for v in vals if v > 5.0),
    }


def main():
    print("加载 DSG ...", flush=True)
    g = load_places(DSG)
    print(f"PLACES: {len(g.nodes)} nodes, {len(g.edges)} edges\n")

    for R_geo in [3.0, 5.0]:
        res = sparsify_graphaware(g, R_geo)
        n_rep = len(res.graph.nodes)
        st = stretch_stats(res.stretch)
        comps0 = connected_components(res.graph)
        print(f"\n### R_geo={R_geo}: reps={n_rep}, sparse_edges={len(res.graph.edges)}, "
              f"components={len(comps0)}")
        print(f"    stretch 不过滤: {st}")

        for thresh in [1.5, 2.0]:
            fg, nkeep, isolated = filter_high_stretch(res, thresh)
            comps = connected_components(fg)
            sizes = sorted((len(c) for c in comps), reverse=True)
            print(f"    过滤 >{thresh}: keep={nkeep}/{len(res.graph.edges)} "
                  f"components={len(comps)} sizes={sizes[:5]} "
                  f"isolated_reps={len(isolated)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
