#!/usr/bin/env python3
"""CLI: 生成 semantic_to_waypoint.json（Phase 4）。

流程：DSG → PLACES → sparsify(R) → cover_assign → build_semantic_index
用法:
  /opt/conda/envs/llm-task-planner/bin/python build_semantic_to_waypoint.py <dsg.json> \
      [--R 3.0] [--out generated/semantic_to_waypoint.json] [--max-room-distance 8]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dsg_reader.loader import load_places  # noqa: E402
from waypoint_graph.sparsifier import sparsify_graphaware  # noqa: E402
from semantic_waypoint_mapper.mapper import build_semantic_index  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dsg_json")
    ap.add_argument("--R", type=float, default=3.0, help="sparsify 覆盖半径")
    ap.add_argument("--out", default="generated/semantic_to_waypoint.json")
    ap.add_argument("--max-room-distance", type=float, default=None,
                    help="room→anchor 最大距离硬阈值(fail closed)，默认不限制")
    args = ap.parse_args()

    g = load_places(args.dsg_json)
    res = sparsify_graphaware(g, R_geo=args.R)
    index = build_semantic_index(args.dsg_json, res.graph, res.assigned,
                                 max_room_distance=args.max_room_distance)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")

    rooms = index["room_to_waypoint"]
    ok = sum(1 for v in rooms.values() if v.get("status") == "ok")
    failed = {k: v["status"] for k, v in rooms.items() if v.get("status") != "ok"}
    print(json.dumps({
        "R": args.R,
        "sparsified_nodes": len(res.graph.nodes),
        "sparsified_edges": len(res.graph.edges),
        "place_to_waypoint_entries": len(index["place_to_waypoint"]),
        "rooms_total": len(rooms),
        "rooms_mapped_ok": ok,
        "rooms_failed": failed,
        "out": str(out),
    }, ensure_ascii=False, indent=2))
    if failed:
        print(f"WARNING: {len(failed)} 个 room 未映射成功: {failed}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
