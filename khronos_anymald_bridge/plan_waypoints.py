"""CLI: Mock ANYmal waypoint planner 演示。

用法:
  python plan_waypoints.py --graph generated/waypoints_sparsified_R3.0.json \
      --start 20 --goal 3896
  或按 room anchor 规划:
  python plan_waypoints.py --graph ... --from-room 0 --to-room 12 \
      --semantic generated/semantic_to_waypoint.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from waypoint_graph.planner import WaypointGraph, shortest_path  # noqa: E402


def _resolve_from_semantic(semantic_path, room_index):
    with open(semantic_path, encoding="utf-8") as f:
        idx = json.load(f)["room_to_waypoint"]
    for k, v in idx.items():
        if v.get("room_index") == room_index and v.get("status") == "ok":
            return v["waypoint_id"]
    raise ValueError(f"room_index {room_index} 无映射")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="generated/waypoints_sparsified_R3.0.json")
    ap.add_argument("--semantic", default="generated/semantic_to_waypoint.json")
    ap.add_argument("--start", type=int, default=None)
    ap.add_argument("--goal", type=int, default=None)
    ap.add_argument("--from-room", type=int, default=None)
    ap.add_argument("--to-room", type=int, default=None)
    args = ap.parse_args()

    graph = WaypointGraph.load(args.graph)

    if args.start is None and args.from_room is None:
        ap.error("需 --start 或 --from-room")
    if args.goal is None and args.to_room is None:
        ap.error("需 --goal 或 --to-room")

    start = args.start
    if start is None:
        start = _resolve_from_semantic(args.semantic, args.from_room)
    goal = args.goal
    if goal is None:
        goal = _resolve_from_semantic(args.semantic, args.to_room)

    if not graph.has_node(start):
        print(f"ERROR: start waypoint {start} 不在图中", file=sys.stderr)
        return 1
    if not graph.has_node(goal):
        print(f"ERROR: goal waypoint {goal} 不在图中", file=sys.stderr)
        return 1

    path, cost = shortest_path(graph, start, goal)
    if path is None:
        print(f"NO_PATH: {start} → {goal} 不可达（不同连通分量）")
        return 2

    print(f"start={start} goal={goal}")
    print(f"route: {' → '.join(str(w) for w in path)}")
    print(f"n_waypoints={len(path)}  total_cost={round(cost, 3)}m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
