#!/usr/bin/env python3
"""Phase 5 验证：所有 room anchor 两两可达性。"""
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from waypoint_graph.planner import WaypointGraph, shortest_path

BASE = Path(__file__).resolve().parent
GRAPH = BASE / "generated" / "waypoints_sparsified_R3.0.json"
WITNESS = BASE / "generated" / "witness_paths.json"
SEMANTIC = BASE / "generated" / "semantic_to_waypoint.json"


def main():
    graph = WaypointGraph.load(str(GRAPH), str(WITNESS))
    with open(SEMANTIC, encoding="utf-8") as f:
        sem = json.load(f)["room_to_waypoint"]

    anchors = {}
    for k, v in sem.items():
        if v.get("status") == "ok":
            anchors[v["room_index"]] = v["waypoint_id"]

    pairs = list(itertools.combinations(sorted(anchors), 2))
    ok = 0
    routes = []
    for a, b in pairs:
        path, cost = shortest_path(graph, anchors[a], anchors[b])
        if path is not None:
            ok += 1
            routes.append((a, b, len(path), round(cost, 1)))
        else:
            routes.append((a, b, None, None))

    reachable = ok / len(pairs) * 100
    print(f"room 数: {len(anchors)}  配对: {len(pairs)}")
    print(f"可达: {ok}/{len(pairs)} ({reachable:.1f}%)")
    unreachable = [r for r in routes if r[2] is None]
    if unreachable:
        print("不可达对:", unreachable[:10])
    print("示例 route:")
    for r in routes[:8]:
        if r[2]:
            print(f"  R{r[0]}→R{r[1]}: {r[2]} waypoints, {r[3]}m")
    return 0 if reachable == 100 else 1


if __name__ == "__main__":
    sys.exit(main())
