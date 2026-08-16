#!/usr/bin/env python3
"""全链路数据一致性独立复查（不依赖单元测试，直接核对生成产物 vs DSG）。

检查:
  1. waypoints.json 每个 node 的 position 与 DSG 原始 PLACES 逐点一致
  2. waypoints.json 边双向完整（每条有向边都有反向）
  3. sparsified waypoints 是全量 waypoints.json 节点的子集（id+position）
  4. R0→R12 route 的每一段边都真实存在于 sparsified waypoints.json
  5. semantic place_to_waypoint 的所有 waypoint id 都在 sparsified 图内
  6. sparsified 覆盖保证: 每个 DSG place 到其代表 ≤ R
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsg_reader.loader import load_places  # noqa: E402
from waypoint_graph.planner import WaypointGraph, shortest_path  # noqa: E402
from waypoint_graph.sparsifier import sparsify_graphaware  # noqa: E402

BRIDGE = Path(__file__).resolve().parent
DSG = "/home/ulmmt/Masterthesis-wang/scene-graph-khronos/snapshots/office_launch_dense/2026_08_03-16_46_38/dsg.json"
R = 3.0
FAILS = []


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def main():
    print("=== 1. waypoints.json node position vs DSG PLACES ===")
    g = load_places(DSG)
    full = json.load(open(BRIDGE / "generated" / "waypoints.json", encoding="utf-8"))
    mism = 0
    for n in full["nodes"]:
        nid = n["id"]
        if nid not in g.nodes:
            mism += 1
            continue
        a = g.nodes[nid].position
        b = n["pose_stamped"]["position"]
        if [round(float(x), 4) for x in a] != [round(float(x), 4) for x in b]:
            mism += 1
    check(f"2472 节点全部匹配 DSG place 位置 (mismatch={mism})", mism == 0)

    print("=== 2. waypoints.json 边双向完整性 ===")
    pairs = {(e["start_node_id"], e["end_node_id"]) for e in full["edges"]}
    missing_rev = sum(1 for a, b in pairs if (b, a) not in pairs)
    check(f"每条有向边都有反向 (缺反向={missing_rev})", missing_rev == 0)

    print("=== 3. sparsified waypoints 是全量子集 (xy 一致, z 已 ground 投影) ===")
    spars = json.load(open(BRIDGE / "generated" / "waypoints_sparsified_R3.0.json",
                           encoding="utf-8"))
    full_pos = {n["id"]: n["pose_stamped"]["position"] for n in full["nodes"]}
    bad = []
    for n in spars["nodes"]:
        nid = n["id"]
        if nid not in full_pos:
            bad.append(("missing", nid))
            continue
        p, fp = n["pose_stamped"]["position"], full_pos[nid]
        if abs(p[0] - fp[0]) > 1e-3 or abs(p[1] - fp[1]) > 1e-3:
            bad.append(("xy_mismatch", nid))
    check(f"sparsified {len(spars['nodes'])} nodes 是全量子集且 xy 一致"
          f" (bad={bad[:3]}, z 已 ground 投影)", len(bad) == 0)

    print("=== 4. R0→R12 route 每段边真实存在 ===")
    sem = json.load(open(BRIDGE / "generated" / "semantic_to_waypoint.json",
                         encoding="utf-8"))
    anchors = {v["room_index"]: v["waypoint_id"] for v in sem["room_to_waypoint"].values()
               if v.get("status") == "ok"}
    graph = WaypointGraph.load(str(BRIDGE / "generated" / "waypoints_sparsified_R3.0.json"),
                                str(BRIDGE / "generated" / "witness_paths.json"))
    route, _ = shortest_path(graph, anchors[0], anchors[12])
    edge_set = {(e["start_node_id"], e["end_node_id"]) for e in spars["edges"]}
    bad = [i for i in range(len(route) - 1) if (route[i], route[i + 1]) not in edge_set]
    check(f"route {len(route)} waypoints, 每段边存在 (缺={bad})", len(bad) == 0)

    print("=== 5. semantic place_to_waypoint 全在 sparsified 图内 ===")
    sp_wp = {n["id"] for n in spars["nodes"]}
    bad_p = [w for w in sem["place_to_waypoint"].values() if w not in sp_wp]
    check(f"place 映射 {len(sem['place_to_waypoint'])} 条全在图中 (缺={bad_p[:5]})",
          len(bad_p) == 0)

    print("=== 6. sparsified 覆盖保证 (geodesic, 每个 place 到代表 <= R) ===")
    res = sparsify_graphaware(g, R)
    over = sum(1 for d in res.cover_dist.values() if d > R + 1e-6)
    check(f"覆盖保证: 超过 R={R}m 的 geodesic 代表归属 {over} 个", over == 0)

    print("\n" + "=" * 50)
    if FAILS:
        print(f"复查发现 {len(FAILS)} 个 FAIL:")
        for f in FAILS:
            print("  -", f)
        return 1
    print("全部独立复查 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
