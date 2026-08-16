#!/usr/bin/env python3
"""Phase 8.5R 验证：planner witness cost route 展开无端点跳跃。"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "khronos_anymald_rviz"))
from waypoint_graph.planner import WaypointGraph, shortest_path  # noqa: E402
from khronos_anymald_rviz.witness_expand import expand_route  # noqa: E402

BASE = Path(__file__).resolve().parent / "generated"


def main():
    spars_path = BASE / "waypoints_sparsified_R3.0.json"
    wit = json.load(open(BASE / "witness_paths.json", encoding="utf-8"))
    full = json.load(open(BASE / "waypoints.json", encoding="utf-8"))
    sem = json.load(open(BASE / "semantic_to_waypoint.json", encoding="utf-8"))

    spars_pos = {n["id"]: (n["pose_stamped"]["position"][0], n["pose_stamped"]["position"][1], 0.0)
                 for n in json.load(open(spars_path))["nodes"]}
    full_pos = {n["id"]: (p[0], p[1], 0.0) for n in full["nodes"]}

    g = WaypointGraph.load(str(spars_path), str(BASE / "witness_paths.json"))
    anchors = {v["room_index"]: v["waypoint_id"] for v in sem["room_to_waypoint"].values()
               if v.get("status") == "ok"}

    print("=== 单目标 R0→R12 ===")
    route, cost = shortest_path(g, anchors[0], anchors[12])
    pts = expand_route(route, wit, spars_pos.get, full_pos.get)
    steps = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    print(f"route_wps={len(route)} dense_pts={len(pts)}")
    print(f"witness_cost={cost:.2f}m  expanded_len={sum(steps):.2f}m")
    print(f"max_step={max(steps):.2f}m  median_step={sorted(steps)[len(steps)//2]:.2f}m")
    print(f"jump>3m: {sum(1 for s in steps if s > 3.0)}")
    print(f"route: {route}")

    print("\n=== 多目标 R0→R12→R4→R9 ===")
    seq = [anchors[0]]
    cur = anchors[0]
    total = 0.0
    all_pts = []
    for r in (12, 4, 9):
        p, c = shortest_path(g, cur, anchors[r])
        if p is None:
            print("NO_PATH"); return 1
        seq.extend(p[1:])
        cur = anchors[r]
        total += c
    pts = expand_route(seq, wit, spars_pos.get, full_pos.get)
    steps = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    print(f"total_wps={len(seq)} dense_pts={len(pts)}")
    print(f"total_witness_cost={total:.2f}m  expanded_len={sum(steps):.2f}m")
    print(f"max_step={max(steps):.2f}m  jump>3m: {sum(1 for s in steps if s > 3.0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
