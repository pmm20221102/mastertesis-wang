#!/usr/bin/env python3
"""Phase 3R: graph-aware sparsified waypoints 生成。

流程（依据 khronos_anymald_phase0_8_audit §15/16/17/18/31）：
  1. load DSG → PLACES
  2. sparsify_graphaware(R_geo)：geodesic FPS（per-component）+ witness path
  3. ground projection：z → mock ground（--ground-z），标记 z_source=projected_for_mock
  4. stretch 报告（写入 report，不静默删除；可选 --max-stretch 过滤）
  5. export waypoints_sparsified_R{R_geo}.json + sparsified_report.json

用法:
  /opt/conda/envs/llm-task-planner/bin/python build_sparsified.py <dsg.json> \
      --R 3.0 [--ground-z 0.0] [--max-stretch None] [--out generated/]
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dsg_reader.loader import load_places  # noqa: E402
from waypoint_exporter import schema  # noqa: E402
from waypoint_exporter.exporter import load_policy  # noqa: E402
from waypoint_graph.sparsifier import (  # noqa: E402
    SparseResult, connected_components, filter_high_stretch, sparsify_graphaware,
)
from waypoint_graph.stretch_validator import compute_stretch_report  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dsg_json")
    ap.add_argument("--R", type=float, default=3.0, help="geodesic FPS 覆盖半径")
    ap.add_argument("--ground-z", type=float, default=0.0,
                    help="mock ground 投影 z（审计 §15 方案 B，标记 projected_for_mock）")
    ap.add_argument("--max-stretch", type=float, default=None,
                    help="可选：过滤 stretch > 该值的边（默认保留+报告）")
    ap.add_argument("--policy", default="config/default_anymal_edge_policy.yaml")
    ap.add_argument("--out-dir", default="generated")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] 加载 DSG ...", flush=True)
    g = load_places(args.dsg_json)
    print(f"  PLACES: {len(g.nodes)} nodes, {len(g.edges)} edges, "
          f"{len(connected_components(g))} components")

    print(f"[2/5] graph-aware sparsify R_geo={args.R} ...", flush=True)
    res = sparsify_graphaware(g, args.R)
    graph = res.graph
    filtered_info = None
    if args.max_stretch is not None:
        graph, nkeep, isolated = filter_high_stretch(res, args.max_stretch)
        filtered_info = {"max_stretch": args.max_stretch,
                         "kept": nkeep, "total": len(res.graph.edges),
                         "isolated_reps": len(isolated)}
        print(f"  过滤 >{args.max_stretch}: keep {nkeep}/{len(res.graph.edges)}")
    print(f"  reps={len(graph.nodes)}, edges={len(graph.edges)}")

    print(f"[3/5] ground projection z={args.ground_z} ...", flush=True)
    policy = load_policy(args.policy)
    wp = {"header": {"frame": g.frame, "stamp": 0}, "nodes": [], "edges": []}
    for idx in sorted(graph.nodes):
        node = graph.nodes[idx]
        pos = list(node.position)
        pos[2] = args.ground_z
        wp["nodes"].append(
            schema.make_node(idx, pos, node.orientation, frame=g.frame, stamp=0)
        )
    edge_id = 1
    for e in graph.edges:
        for a, b in ((e.source, e.target), (e.target, e.source)):
            wp["edges"].append(schema.make_edge(edge_id, a, b, policy))
            edge_id += 1

    spars_out = out_dir / f"waypoints_sparsified_R{args.R}.json"
    spars_out.write_text(json.dumps(wp, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  写 {spars_out}")

    print(f"[4/5] stretch 报告 ...", flush=True)
    srep = compute_stretch_report(res.stretch)
    # 高 stretch 边 top（含 witness 长度与欧氏）
    top_details = []
    for (a, b), s in srep.top_edges:
        geo = res.geodesic_len.get((a, b))
        pa, pb = g.nodes[a].position, g.nodes[b].position
        euc = math.sqrt(sum((pa[k] - pb[k]) ** 2 for k in range(3)))
        top_details.append({
            "edge": [a, b], "stretch": s, "geodesic_m": round(geo, 2),
            "euclidean_m": round(euc, 3),
            "witness_n_places": len(res.witnesses.get((a, b), [])),
        })

    print(f"[5/5] 写 report ...", flush=True)
    comps = connected_components(graph)
    comp_sizes = sorted((len(c) for c in comps), reverse=True)
    report = {
        "method": "graph-aware (geodesic FPS per-component + witness path)",
        "R_geo": args.R,
        "source_places": len(g.nodes),
        "source_edges": len(g.edges),
        "source_components": len(res.components),
        "representatives": len(graph.nodes),
        "sparse_edges": len(graph.edges),
        "exported_components": len(comps),
        "exported_component_sizes": comp_sizes[:10],
        "max_geodesic_cover_m": round(max(res.cover_dist.values()), 3)
            if res.cover_dist else None,
        "filtered": filtered_info,
        "ground_projection": {
            "z": args.ground_z,
            "z_source": "projected_for_mock",
            "note": "Khronos PLACES z=1.1~3.9 为 volumetric free-space 高度，"
                    "非 ANYmal ground waypoint；此处投影到 mock ground，"
                    "真机 z 需接口确认",
        },
        "stretch": {
            "n_edges": srep.n_edges,
            "median": srep.median,
            "mean": srep.mean,
            "p90": srep.p90,
            "max": srep.max,
            "over_thresholds": srep.over_thresholds,
            "note": "stretch = dense geodesic / euclidean；>1.5 可能为跨墙/跨走廊 shortcut，"
                    "route cost 已用 witness geodesic 长度（沿真实拓扑，不穿墙）",
            "top_high_stretch": top_details,
        },
    }
    rep_out = out_dir / "sparsified_report.json"
    rep_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # witness paths 导出（供 RViz2 route 展开使用）
    # MD Fix C: 区分 khronos_graph_cost（原图 cost，非米制）与 metric/projected 长度（米制）
    edge_w = {}
    for e in g.edges:
        edge_w[(min(e.source, e.target), max(e.source, e.target))] = e.weight
    witness_out = {}
    for (a, b), path in res.witnesses.items():
        pa, pb = g.nodes[a].position, g.nodes[b].position
        euc3d = math.sqrt(sum((pa[k] - pb[k]) ** 2 for k in range(3)))
        khronos_cost = 0.0
        metric3d = 0.0
        proj_xy = 0.0
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            khronos_cost += edge_w.get((min(u, v), max(u, v)), 0.0)
            pu, pv = g.nodes[u].position, g.nodes[v].position
            metric3d += math.sqrt(sum((pu[k] - pv[k]) ** 2 for k in range(3)))
            proj_xy += math.sqrt((pu[0] - pv[0]) ** 2 + (pu[1] - pv[1]) ** 2)
        witness_out[f"({a},{b})"] = {
            "path": path,
            "khronos_graph_cost": round(khronos_cost, 4),
            "metric_3d_length_m": round(metric3d, 3),
            "projected_xy_length_m": round(proj_xy, 3),
            "endpoint_euclidean_3d_m": round(euc3d, 3),
            "topology_stretch": round(metric3d / euc3d, 3) if euc3d > 1e-9 else float("inf"),
        }
    (out_dir / "witness_paths.json").write_text(
        json.dumps(witness_out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"  witness_paths: {len(witness_out)} edges -> {out_dir / 'witness_paths.json'}")

    print(json.dumps({
        "R_geo": args.R, "reps": len(graph.nodes), "edges": len(graph.edges),
        "components": len(comps), "stretch_median": srep.median,
        "stretch_mean": srep.mean, "stretch_max": srep.max,
        "stretch_gt_1.5": srep.over_thresholds.get(1.5),
        "ground_z": args.ground_z,
        "out": str(spars_out), "report": str(rep_out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
