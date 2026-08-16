#!/usr/bin/env python3
"""edge-weight audit（MD Fix C / Stage 2）。

统计 Office DSG 每条 PLACES edge：
  stored_weight = edge.info.weight
  metric_3d     = ||position(source) - position(target)||_3D
  ratio         = stored_weight / metric_3d

判定 weight 是否≈物理米制。输出 generated/edge_weight_audit.json。
"""
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsg_reader.loader import load_places  # noqa: E402

DSG = "/home/ulmmt/Masterthesis-wang/scene-graph-khronos/snapshots/office_launch_dense/2026_08_03-16_46_38/dsg.json"


def main():
    g = load_places(DSG)
    ratios = []
    errs = []
    close_1cm = 0
    for e in g.edges:
        a = g.nodes[e.source].position
        b = g.nodes[e.target].position
        m3 = math.sqrt(sum((a[k] - b[k]) ** 2 for k in range(3)))
        w = e.weight
        if m3 > 1e-9:
            ratios.append(w / m3)
        errs.append(abs(w - m3))
        if abs(w - m3) < 0.01:
            close_1cm += 1

    ratios.sort()
    errs.sort()
    n = len(ratios)
    stats = {
        "n_edges": n,
        "weight_vs_metric3d_ratio": {
            "mean": round(sum(ratios) / n, 4),
            "median": round(ratios[n // 2], 4),
            "p90": round(ratios[int(0.9 * n)], 4),
            "max": round(ratios[-1], 4),
            "min": round(ratios[0], 4),
        },
        "mae_m": round(sum(errs) / n, 4),
        "close_within_1cm_pct": round(close_1cm / n * 100, 2),
        "conclusion": "",
    }
    # 判定
    ratio_med = stats["weight_vs_metric3d_ratio"]["median"]
    if abs(ratio_med - 1.0) < 0.05 and stats["close_within_1cm_pct"] > 90:
        stats["conclusion"] = "weight ≈ 3D metric (m)，可保留 *_m 命名"
    else:
        stats["conclusion"] = (
            f"weight 非物理米制（median ratio={ratio_med}），"
            "geodesic_m 须改名 khronos_graph_cost，另加 metric/projected 长度"
        )

    out = Path(__file__).resolve().parent / "generated" / "edge_weight_audit.json"
    out.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    print(f"\n写 {out}")


if __name__ == "__main__":
    main()
