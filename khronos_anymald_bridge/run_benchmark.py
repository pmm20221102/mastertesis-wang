#!/usr/bin/env python3
"""Phase 8R: 无真机 Benchmark（audit 修订版）。

指标:
  1. Waypoint Export Validity            导出 waypoints.json 通过 validator
  2. Topology Preservation               主连通分量保持 + 节点覆盖 + 分量数对齐
  3. Semantic-to-Waypoint Resolution     语义目标 → waypoint 解析成功率
  4. Route Planning Success              waypoint 对间 route 规划成功率
  5. Pipeline Execution Consistency      route 逐 waypoint 执行一致（改名自 E2E，见审计 §12.1）
  6. Invalid Waypoint ID Rate            解析出的 waypoint 不在图中 / 解析失败比例（目标 0%）

用法:
  /opt/conda/envs/llm-task-planner/bin/python run_benchmark.py \
      <dsg.json> [--R 3.0] [--out generated/benchmark_report.json]
"""
import argparse
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dsg_reader.loader import load_places  # noqa: E402
from mock_anymald.executor import MockExecutor  # noqa: E402
from navigation_goal_compiler import InvalidGoal, NavigationGoalCompiler  # noqa: E402
from waypoint_graph.planner import WaypointGraph, shortest_path  # noqa: E402
from waypoint_graph.sparsifier import connected_components, sparsify_graphaware  # noqa: E402
from waypoint_graph.validator import validate_waypoints  # noqa: E402

BRIDGE = Path(__file__).resolve().parent
SPARS = BRIDGE / "generated" / f"waypoints_sparsified_R{{R}}.json"
SEM = BRIDGE / "generated" / "semantic_to_waypoint.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dsg_json")
    ap.add_argument("--R", type=float, default=3.0)
    ap.add_argument("--out", default="generated/benchmark_report.json")
    args = ap.parse_args()

    spars_path = str(SPARS).format(R=args.R)
    print(f"[0/7] 加载 DSG + sparsified graph (R={args.R}) ...", flush=True)
    g = load_places(args.dsg_json)
    res = sparsify_graphaware(g, args.R)
    assigned = res.assigned
    graph = WaypointGraph.load(spars_path, str(BRIDGE / "generated" / "witness_paths.json"))
    sem = json.load(open(SEM, encoding="utf-8"))

    rep = {"R": args.R, "sparsified_nodes": len(res.graph.nodes)}

    # 1. Waypoint Export Validity
    print("[1/7] Export validity ...")
    v = validate_waypoints(spars_path)
    rep["1_waypoint_export_validity"] = {
        "valid": v["valid"], "n_errors": len(v["errors"]),
        "n_nodes": v["stats"]["n_nodes"], "n_edges": v["stats"]["n_edges"],
    }

    # 2. Topology Preservation Rate
    print("[2/7] Topology preservation ...")
    # source PLACES 图连通分量
    src_adj: dict[int, list[int]] = {i: [] for i in g.nodes}
    for e in g.edges:
        src_adj.setdefault(e.source, []).append(e.target)
        src_adj.setdefault(e.target, []).append(e.source)
    visited: set = set()
    comps: list[list[int]] = []
    for i in g.nodes:
        if i in visited:
            continue
        stack, comp = [i], []
        visited.add(i)
        while stack:
            c = stack.pop()
            comp.append(c)
            for nb in src_adj.get(c, []):
                if nb not in visited:
                    visited.add(nb)
                    stack.append(nb)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    main_component = comps[0]
    main_reps = {assigned[i] for i in main_component}
    # exported 图中 main_reps 是否单分量连通
    start_rep = next(iter(main_reps))
    reachable = {start_rep}
    stack = [start_rep]
    while stack:
        c = stack.pop()
        for nb, _w in graph.adj.get(c, []):
            if nb not in reachable:
                reachable.add(nb)
                stack.append(nb)
    main_preserved = main_reps.issubset(reachable)
    exp_comps = connected_components(res.graph)
    rep["2_topology_preservation_rate"] = {
        "main_component_preserved": bool(main_preserved),
        "node_coverage": round(len(assigned) / max(len(g.nodes), 1), 4),
        "source_components": len(comps),
        "exported_components": len(exp_comps),
        "main_component_places": len(main_component),
        "main_component_waypoints": len(main_reps),
        "note": "graph-aware sparsify：每个 source 连通分量独立 FPS(geodesic)，"
                "不跨分量 assignment，不静默合并 disconnected components。",
    }

    # 3. Semantic-to-Waypoint Resolution Rate
    print("[3/7] Semantic-to-waypoint resolution ...")
    rooms = sem["room_to_waypoint"]
    room_ok = sum(1 for v in rooms.values() if v.get("status") == "ok")
    rep["3_semantic_to_waypoint_resolution_rate"] = {
        "rate": round(room_ok / max(len(rooms), 1), 4),
        "rooms_total": len(rooms), "rooms_ok": room_ok,
    }

    # 4. Route Planning Success Rate（room anchor 全对）
    print("[4/7] Route planning (all room pairs) ...")
    anchors = {v["room_index"]: v["waypoint_id"] for v in rooms.values()
               if v.get("status") == "ok"}
    pairs = list(itertools.combinations(sorted(anchors), 2))
    route_ok = 0
    for a, b in pairs:
        if shortest_path(graph, anchors[a], anchors[b])[0] is not None:
            route_ok += 1
    rep["4_route_planning_success_rate"] = {
        "rate": round(route_ok / max(len(pairs), 1), 4),
        "pairs": len(pairs), "ok": route_ok,
    }

    # 5. Pipeline Execution Consistency（改名自 End-to-End Navigation Success）
    print("[5/7] Pipeline execution consistency (MockExecutor) ...")
    ex = MockExecutor(graph)
    e2e_ok = 0
    for a, b in pairs:
        res = ex.run(anchors[a], anchors[b])
        if res["state"] == MockExecutor.REACHED:
            e2e_ok += 1
    rep["5_pipeline_execution_consistency"] = {
        "rate": round(e2e_ok / max(len(pairs), 1), 4),
        "pairs": len(pairs), "ok": e2e_ok,
        "note": "MockExecutor 先 shortest_path 后逐 waypoint 自动推进，无碰撞/"
                "避障/motion 约束，故该指标是 Pipeline Execution Consistency，"
                "不是 Navigation Success（审计 §12.1）。",
    }

    # 6. Invalid Waypoint ID Rate（目标 0%）
    print("[6/7] Invalid waypoint id rate ...")
    compiler = NavigationGoalCompiler(str(SEM))
    invalid, total = 0, 0
    for rid_str in rooms:
        total += 1
        try:
            wp = compiler.compile(int(rid_str))
            if wp not in graph.nodes:
                invalid += 1
        except InvalidGoal:
            invalid += 1
    for p, w in sem["place_to_waypoint"].items():
        total += 1
        if int(w) not in graph.nodes:
            invalid += 1
    rep["6_invalid_waypoint_id_rate"] = {
        "rate": round(invalid / max(total, 1), 6),
        "checked": total, "invalid": invalid,
    }

    # 汇总
    print("[7/7] 汇总 ...")
    summary = {k: v.get("rate") if isinstance(v, dict) and "rate" in v
               else v.get("valid") if isinstance(v, dict) and "valid" in v
               else v for k, v in rep.items() if k not in ("R", "sparsified_nodes")}
    rep["summary"] = summary

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 52)
    print("Phase 8 Benchmark 结果")
    print("=" * 52)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nreport: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
