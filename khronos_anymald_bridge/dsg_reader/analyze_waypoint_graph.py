#!/usr/bin/env python3
"""Phase 3: waypoint graph 可导航性分析。

对比 ANYmal 样本（inputs/waypoints_sample.json）与导出的 waypoints.json：
- 节点密度 / edge 长度分布 / 短边比例
- 冗余节点（最近邻过近）
- 靠墙/穿墙粗略检查（利用 Khronos Place GVD distance 属性）

只分析，不做 sparsification。只有当结果证明"PLACES 不能直接用"时才进入过滤。
用法: python analyze_waypoint_graph.py <dsg.json>
"""
import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dsg_reader.loader import load_places  # noqa: E402


def _edge_len(positions: dict[int, list], s: int, t: int) -> float:
    a, b = positions[s], positions[t]
    return math.dist(a, b)


def _analyze_graph(wp: dict) -> dict:
    positions = {n["id"]: n["pose_stamped"]["position"] for n in wp["nodes"]}
    edges = wp["edges"]
    lens = [_edge_len(positions, e["start_node_id"], e["end_node_id"]) for e in edges]
    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    zs = [p[2] for p in positions.values()]

    # 密度: 用 x/y 范围面积（近似）
    area = max((max(xs) - min(xs)) * (max(ys) - min(ys)), 1e-6)
    n_nodes = len(positions)
    density_per_m2 = n_nodes / area
    mean_edge = sum(lens) / len(lens) if lens else 0.0
    short = {
        "<0.2m": sum(1 for l in lens if l < 0.2),
        "<0.5m": sum(1 for l in lens if l < 0.5),
        "<1.0m": sum(1 for l in lens if l < 1.0),
        "<2.0m": sum(1 for l in lens if l < 2.0),
    }
    # 最近邻距离（每个 node 到最近的其他 node）
    ids = list(positions)
    nn_dist = []
    for i in ids:
        best = min(_edge_len(positions, i, j) for j in ids if j != i)
        nn_dist.append(best)
    return {
        "n_nodes": n_nodes,
        "n_edges": len(edges),
        "xy_area_m2": round(area, 1),
        "density_nodes_per_m2": round(density_per_m2, 4),
        "z_range": [round(min(zs), 2), round(max(zs), 2)],
        "edge_len_min": round(min(lens), 3) if lens else None,
        "edge_len_max": round(max(lens), 3) if lens else None,
        "edge_len_mean": round(mean_edge, 3),
        "short_edge_frac": {k: round(v / len(lens), 4) if lens else 0 for k, v in short.items()},
        "nn_dist_min": round(min(nn_dist), 3) if nn_dist else None,
        "nn_dist_mean": round(sum(nn_dist) / len(nn_dist), 3) if nn_dist else None,
        "nodes_with_nn_lt_0.5m": sum(1 for d in nn_dist if d < 0.5),
        "nodes_with_nn_lt_1.0m": sum(1 for d in nn_dist if d < 1.0),
    }


def _wall_check(places, edges) -> dict:
    """靠墙/穿墙粗略检查：利用 GVD distance（到最近障碍距离）。
    distance 小的 place 贴近障碍；若 edge 两端 distance 都很小且 edge 很长，
    该边可能穿越狭窄/障碍区域（GVD 中轴边，需人工确认）。"""
    small = [p for p in places.values() if p.distance < 0.3]
    tiny = [p for p in places.values() if p.distance < 0.1]
    suspicious = []
    for e in edges:
        d_a, d_b = places[e.source].distance, places[e.target].distance
        edge_len = math.dist(places[e.source].position, places[e.target].position)
        if d_a < 0.3 and d_b < 0.3 and edge_len > 1.5:
            suspicious.append({"edge": [e.source, e.target], "len": round(edge_len, 2),
                               "dist": [round(d_a, 2), round(d_b, 2)]})
    return {
        "places_close_to_wall_d<0.3": len(small),
        "places_very_close_d<0.1": len(tiny),
        "suspicious_edges_long_near_walls": len(suspicious),
        "suspicious_samples": suspicious[:10],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dsg_json")
    ap.add_argument("--bridge", default=".")
    args = ap.parse_args()

    bridge = Path(args.bridge)
    sample = json.load(open(bridge / "inputs" / "waypoints_sample.json", encoding="utf-8"))
    exported = json.load(open(bridge / "generated" / "waypoints.json", encoding="utf-8"))
    places_graph = load_places(args.dsg_json)

    sample_stats = _analyze_graph(sample)
    exported_stats = _analyze_graph(exported)
    wall = _wall_check(places_graph.nodes, places_graph.edges)

    print("=" * 60)
    print("ANYmal 样本 graph 基准")
    print("=" * 60)
    print(json.dumps(sample_stats, indent=2, ensure_ascii=False))
    print("\n" + "=" * 60)
    print("导出的 Khronos waypoint graph")
    print("=" * 60)
    print(json.dumps(exported_stats, indent=2, ensure_ascii=False))
    print("\n" + "=" * 60)
    print("靠墙/穿墙粗略检查（Khronos GVD distance）")
    print("=" * 60)
    print(json.dumps(wall, indent=2, ensure_ascii=False))

    print("\n" + "=" * 60)
    print("对比判断")
    print("=" * 60)
    print(f"样本密度: {sample_stats['density_nodes_per_m2']} / 导出: {exported_stats['density_nodes_per_m2']}")
    print(f"样本 mean edge: {sample_stats['edge_len_mean']} / 导出: {exported_stats['edge_len_mean']}")
    ratio = exported_stats["density_nodes_per_m2"] / max(sample_stats["density_nodes_per_m2"], 1e-9)
    print(f"密度比值: {round(ratio, 2)}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
