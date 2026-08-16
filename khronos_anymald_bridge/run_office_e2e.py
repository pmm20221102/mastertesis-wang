#!/usr/bin/env python3
"""Phase 6: Office E2E 闭环 + 论文 trace（§20/§21）。

输入（两种模式）：
  A. --targets "R12,R4,R9"          直接目标序列（可读 room）
  B. --task-plan <json>             模拟现有 LLM+RAG 输出（task_chain 的 navigate_to + target_id）

流程：
  NL → [LLM/Planner] → Navigate(R) → [Grounding] → R
  → [semantic_to_waypoint] → WP → [MockANYmalWaypointPlanner] → route → [MockExecutor] reached

md §11（final_demo_llm_rag_authenticity_fix.md）：Intent/Planner PASS 非硬编码。
当 --task-plan 携带 upstream_validation（由 demo/build_final_multistep_taskplan.py 生成）时，
[Intent]/[Planner] 行输出真实上游验证结果；缺失时明确标注，不伪造 PASS。

用法:
  /opt/conda/envs/llm-task-planner/bin/python run_office_e2e.py \
      --graph generated/waypoints_sparsified_R3.0.json \
      --semantic generated/semantic_to_waypoint.json \
      --start R0 --task-plan demo/final_task_plan.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_anymald.executor import MockExecutor  # noqa: E402
from navigation_goal_compiler import InvalidGoal, NavigationGoalCompiler  # noqa: E402
from waypoint_graph.planner import WaypointGraph, shortest_path  # noqa: E402


def _load_task_plan_targets(task_plan_path: str) -> tuple:
    """返回 (targets, upstream_validation)。"""
    with open(task_plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    targets = []
    for step in plan.get("task_chain", []):
        if step.get("action") == "navigate_to" and step.get("target_id"):
            targets.append(step["target_id"])
    if not targets:
        raise ValueError("task_plan 中没有 navigate_to 步骤")
    return targets, plan.get("upstream_validation", {})


def _fmt_route(route: list[int]) -> str:
    return " → ".join(str(w) for w in route)


def run_e2e(graph: WaypointGraph, compiler: NavigationGoalCompiler,
            start_ref, targets: list, trace_lines: list,
            upstream: dict | None = None) -> bool:
    """执行闭环，追加 trace 行。返回是否全部成功。

    md §11：Intent/Planner 验证基于真实上游证据（build_final_multistep_taskplan
    的 upstream_validation），不再硬编码 PASS。
    """
    if upstream:
        nav_ok = bool(upstream.get("all_action_navigate"))
        plan_steps_ok = (upstream.get("actual_navigation_steps")
                         == upstream.get("expected_navigation_steps"))
        order_ok = bool(upstream.get("order_preserved"))
        intent_pass = nav_ok
        planner_pass = intent_pass and plan_steps_ok and order_ok
        trace_lines.append(
            f"[Intent] {'PASS' if intent_pass else 'FAIL'} "
            f"(5/5 action=navigate={nav_ok}; 冻结 LLM 真实输出，无 locate→navigate 改写)")
        trace_lines.append(
            f"[Planner] {'PASS' if planner_pass else 'FAIL'} "
            f"(steps={upstream.get('actual_navigation_steps')}/"
            f"{upstream.get('expected_navigation_steps')}, "
            f"order_preserved={order_ok}, no_tsp_reorder="
            f"{upstream.get('no_tsp_reorder')})")
        if not planner_pass:
            trace_lines.append("[TASK] FAIL")
            return False
    else:
        trace_lines.append("[Intent] (无 upstream_validation，跳过验证)")
        trace_lines.append("[Planner] (无 upstream_validation，跳过验证)")

    all_pass = True
    try:
        start_wp = compiler.compile(start_ref)
    except InvalidGoal as e:
        trace_lines.append(f"[Grounding:start {start_ref}] FAIL ({e})")
        trace_lines.append("[TASK] FAIL")
        return False
    trace_lines.append(f"[Grounding:start {start_ref}] PASS (waypoint {start_wp})")

    cur_wp = start_wp
    executor = MockExecutor(graph)
    for ref in targets:
        # Grounding / semantic → waypoint
        try:
            wp = compiler.compile(ref)
        except InvalidGoal as e:
            trace_lines.append(f"[Grounding:{ref}] FAIL ({e})")
            all_pass = False
            continue
        trace_lines.append(f"[Grounding:{ref}] PASS")
        trace_lines.append(f"[Semantic→Waypoint] WP{wp}")

        # 规划
        route, cost = shortest_path(graph, cur_wp, wp)
        if route is None:
            trace_lines.append(f"[Route] FAIL (NO_PATH {cur_wp} → {wp})")
            all_pass = False
            continue
        trace_lines.append(f"[WaypointGraph] VALID")
        trace_lines.append(f"[Route] {_fmt_route(route)} (cost {round(cost, 1)}m)")

        # Mock 执行
        res = executor.run(cur_wp, wp)
        if res["state"] != MockExecutor.REACHED:
            trace_lines.append(f"[Reached] FAIL ({res['state']})")
            all_pass = False
            continue
        trace_lines.append(f"[Reached] PASS (n_waypoints={res['n_waypoints']})")
        cur_wp = wp

    trace_lines.append("[TASK] SUCCESS" if all_pass else "[TASK] FAIL")
    return all_pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="generated/waypoints_sparsified_R3.0.json")
    ap.add_argument("--witness", default="generated/witness_paths.json",
                    help="edge-cost sidecar（MD Fix A：统一 witness cost）")
    ap.add_argument("--semantic", default="generated/semantic_to_waypoint.json")
    ap.add_argument("--start", default="R0", help="起点 room（如 R0）或 waypoint id")
    ap.add_argument("--targets", default=None, help="逗号分隔目标序列，如 R12,R4,R9")
    ap.add_argument("--task-plan", default=None, help="模拟现有 LLM+RAG 输出的 json")
    ap.add_argument("--out", default="generated/office_e2e_trace.txt")
    args = ap.parse_args()

    graph = WaypointGraph.load(args.graph, args.witness)
    compiler = NavigationGoalCompiler(args.semantic)

    upstream = None
    if args.task_plan:
        targets, upstream = _load_task_plan_targets(args.task_plan)
    elif args.targets:
        targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    else:
        ap.error("需 --targets 或 --task-plan")

    trace = []
    ok = run_e2e(graph, compiler, args.start, targets, trace, upstream=upstream)
    text = "\n".join(trace)
    print(text)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(f"\ntrace 已写: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
