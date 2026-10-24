"""Planner v3 确定性 Compiler（Planner阶段全面审查与简化改进指导 第 7/19 节）。

LLM 只输出 {action, ref}；本模块确定性地生成：
- executor（ACTION_EXECUTOR_MAP）
- target_id / node_type（ref → grounded references）
- navigation constraints 自动附加（avoid 真实 node ID + prefer route policy）
- escort 宏动作的 actor 自动绑定（role_ids）
- step 编号（1..n）
- description

所有确定性字段应通过单元测试达到 100%。
"""
from __future__ import annotations

from typing import Any

from schema import (
    ACTION_EXECUTOR_MAP,
    Constraints,
    SimplePlanDraft,
    TaskPlan,
    TaskStep,
)

NAV_ACTIONS = {"navigate_to", "escort_to"}
NO_REF_ACTIONS = {"wait", "return_to_base"}
ESCORT_ACTIONS = {"escort_to"}


def _extract_constraints(grounded_task: dict) -> tuple[list[str], list[str]]:
    """返回 (avoid node_ids, prefer policies)。

    avoid 是真实 graph node ID；prefer 是 route policy string（shortest_path 等），
    不参加 node-ID safety 统计（指导第 4.6/20.6 节）。
    """
    avoid_ids: list[str] = []
    prefer_policies: list[str] = []
    for c in grounded_task.get("constraints", []) or []:
        if not isinstance(c, dict):
            continue
        ctype = c.get("type")
        if ctype == "avoid" and c.get("node_id"):
            avoid_ids.append(str(c["node_id"]))
        elif ctype == "prefer":
            policy = c.get("value") or c.get("label")
            if policy:
                prefer_policies.append(str(policy))
    return avoid_ids, prefer_policies


def _ref_lookup(grounded_task: dict) -> dict[str, dict]:
    refs = grounded_task.get("references", {}) or {}
    return {role: data for role, data in refs.items() if isinstance(data, dict)}


def compile_simple_plan(draft: SimplePlanDraft, grounded_task: dict) -> TaskPlan:
    """确定性编译 SimplePlanDraft → 完整 TaskPlan。"""
    refs = _ref_lookup(grounded_task)
    avoid_ids, prefer_policies = _extract_constraints(grounded_task)

    steps: list[TaskStep] = []
    for i, s in enumerate(draft.steps, start=1):
        action = s.action
        executor = ACTION_EXECUTOR_MAP.get(action, "nav2")
        ref_data = refs.get(s.ref) if s.ref else None

        target_id: str | None = None
        node_type = "unknown"
        if ref_data is not None:
            target_id = str(ref_data["node_id"]) if ref_data.get("node_id") else None
            canonical_type = str(ref_data.get("node_type") or "unknown").lower().rstrip("s")
            node_type = {"place": "area", "agent": "person"}.get(canonical_type, canonical_type)

        constraints = Constraints(avoid=[], prefer=[], conditions=[])
        role_refs: dict[str, str] | None = None
        role_ids: dict[str, str] | None = None

        if action in NAV_ACTIONS:
            # 自动附加全部 navigation constraints（指导第 7.2 节）
            constraints.avoid = list(avoid_ids)
            constraints.prefer = list(prefer_policies)

        if action in ESCORT_ACTIONS:
            # 宏动作：自动绑定 actor + destination（指导第 7.3 节）
            actor = refs.get("actor")
            dest = refs.get(s.ref) if s.ref else refs.get("destination")
            role_ids = {}
            if actor is not None and actor.get("node_id"):
                role_ids["actor"] = str(actor["node_id"])
            if dest is not None and dest.get("node_id"):
                role_ids["destination"] = str(dest["node_id"])
                target_id = str(dest["node_id"])
            role_refs = {"actor": "actor", "destination": s.ref or "destination"}

        if action in NO_REF_ACTIONS:
            target_id = None
            node_type = "unknown"

        steps.append(TaskStep(
            step=i,
            action=action,
            target_type=node_type,
            target_ref=s.ref,
            target_id=target_id,
            role_refs=role_refs,
            role_ids=role_ids,
            description=f"{action} {s.ref or ''}".strip(),
            executor=executor,
            constraints=constraints,
        ))

    return TaskPlan(
        status=draft.status,
        task_chain=steps,
        clarification_request=None,
        target_not_found=None,
        notes=[],
    )
