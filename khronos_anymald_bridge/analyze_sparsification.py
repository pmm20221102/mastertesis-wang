#!/usr/bin/env python3
"""Phase 3: 生成并分析不同 R 的 sparsification 候选。

每个候选: waypoints_sparsified_R{R}.json + 密度/连通统计。
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsg_reader.loader import load_places
from waypoint_exporter.exporter import export, load_policy, write_waypoints
from waypoint_graph.sparsifier import sparsify_graphaware
from waypoint_graph.validator import compare_topology, validate_waypoints

DSG = "/home/ulmmt/Masterthesis-wang/scene-graph-khronos/snapshots/office_launch_dense/2026_08_03-16_46_38/dsg.json"


def density_stats(wp, n_nodes, area):
    pos = {n["id"]: n["pose_stamped"]["position"] for n in wp["nodes"]}
    lens = [math.dist(pos[e["start_node_id"]], pos[e["end_node_id"]]) for e in wp["edges"]]
    return {
        "n_nodes": len(wp["nodes"]),
        "n_edges": len(wp["edges"]),
        "density_per_m2": round(len(wp["nodes"]) / area, 4),
        "edge_mean": round(sum(lens) / len(lens), 3) if lens else None,
        "reduction_vs_2472": round(len(wp["nodes"]) / 2472, 3),
    }


def main():
    g = load_places(DSG)
    xs = [n.position[0] for n in g.nodes.values()]
    ys = [n.position[1] for n in g.nodes.values()]
    area = (max(xs) - min(xs)) * (max(ys) - min(ys))
    policy = load_policy("config/default_anymal_edge_policy.yaml")

    # 样本基准：动态从样本计算，不硬编码 groundtruth 数值
    sample = json.load(open("inputs/waypoints_sample.json", encoding="utf-8"))
    sp = [n["pose_stamped"]["position"] for n in sample["nodes"]]
    sxs = [p[0] for p in sp]
    sys2 = [p[1] for p in sp]
    sample_area = max((max(sxs) - min(sxs)) * (max(sys2) - min(sys2)), 1e-6)
    sample_density = len(sp) / sample_area
    spmap = {n["id"]: n["pose_stamped"]["position"] for n in sample["nodes"]}
    slens = [math.dist(spmap[e["start_node_id"]], spmap[e["end_node_id"]])
             for e in sample["edges"]]
    sample_edge_mean = sum(slens) / len(slens) if slens else None
    results = []
    for R in [3.0, 5.0]:
        s = sparsify_graphaware(g, R_geo=R).graph
        wp = export(s, policy)
        out = f"generated/waypoints_sparsified_R{R}.json"
        write_waypoints(wp, out)
        v = validate_waypoints(out)
        t = compare_topology(g, out)
        st = density_stats(wp, len(g.nodes), area)
        st["R"] = R
        st["n_components"] = t["exported_n_components"]
        st["component_sizes"] = t["exported_component_sizes"]
        st["valid"] = v["valid"]
        st["density_ratio_vs_sample"] = round(st["density_per_m2"] / sample_density, 1)
        st["file"] = out
        results.append(st)
        print(json.dumps(st, ensure_ascii=False))

    summary = {
        "sample_benchmark": {
            "density_per_m2": round(sample_density, 4),
            "n_nodes": len(sp),
            "edge_mean": round(sample_edge_mean, 3) if sample_edge_mean else None,
        },
        "full_export_density_per_m2": round(len(g.nodes) / area, 4),
        "candidates": results,
        "note": "R 为 FPS 覆盖半径(米)；实际 R 需 ANYmal 接口/真机实验确认，此处为候选分析。",
    }
    with open("generated/sparsification_candidates.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print("\n候选已写入 generated/sparsification_candidates.json")


if __name__ == "__main__":
    main()
