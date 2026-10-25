"""Phase 1: target_ref / step-refs / compiler / validator 严格化测试（任务书第 22 节）。

覆盖：
- target_ref 必填、缺失/非法拒绝、角色绑定、编译正确性
- step-specific refs 编译与角色绑定
- Grounding status gate（ambiguous/not_found/unsupported 阻塞计划）
- avoid/prefer 约束保留
- executor 映射
- repair 不泄漏 GT
- ID 安全（hallucinated rate 0）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "experiment" / "evaluations" / "taskchain"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "RAG" / "khronos_3dsg_rag" / "src"))

import pytest  # noqa: E402

from schema import PlanDraftStep, TaskPlan, TaskStep  # noqa: E402
from grounding.orchestrator import GroundingResult  # noqa: E402
from api_schemas import ResolutionResult, ResolutionCandidate  # noqa: E402
from planner_schemas.intent import EntityReference  # noqa: E402
from main_rag import _resolve_target_refs, _validate_grounded_plan, _repair_plan  # noqa: E402


# ---------- helpers ----------

def _plan(target_ref: str | None = "target", action: str = "navigate_to",
          target_id: str | None = None, executor: str = "nav2",
          avoid: list | None = None, prefer: list | None = None) -> TaskPlan:
    from schema import Constraints
    return TaskPlan(
        status="valid",
        task_chain=[TaskStep(
            step=1, action=action, target_type="object", target_ref=target_ref,
            target_id=target_id, description="d", executor=executor,
            constraints=Constraints(avoid=avoid or [], prefer=prefer or [], conditions=[]),
        )],
    )


def _grounding(roles: dict | None = None, constraints: list | None = None) -> GroundingResult:
    g = GroundingResult()
    for role, node_id in (roles or {"target": "obj_1"}).items():
        g.add_result(role, ResolutionResult(
            status="resolved", reference=EntityReference(text=role),
            selected=ResolutionCandidate(node_id=node_id, node_type="object", label=role),
        ))
    for key, node_id in (constraints or []):
        g.add_constraint_result(key, ResolutionResult(
            status="resolved", reference=EntityReference(text=key),
            selected=ResolutionCandidate(node_id=node_id, node_type="object", label=key),
        ))
    return g


def _gt(**kw):
    base = {"references": {"target": {"node_id": "obj_1", "label": "chair"}}, "constraints": [], "steps": []}
    base.update(kw)
    return base


# ---------- 1/2. target_ref 必填，缺失 → 拒绝 ----------

def test_target_ref_required():
    with pytest.raises(ValueError):
        PlanDraftStep(step=1, action="navigate_to", target_type="object", target_ref="",
                      description="x", executor="nav2")


def test_missing_target_ref_rejected():
    with pytest.raises(ValueError):
        PlanDraftStep(step=1, action="locate_entity", target_type="object", target_ref="",
                      description="x", executor="scene_graph")


# ---------- 3. 非法 target_ref → 拒绝 ----------

def test_invalid_target_ref_rejected():
    with pytest.raises(ValueError):
        PlanDraftStep(step=1, action="navigate_to", target_type="object", target_ref="bogus_ref",
                      description="x", executor="nav2")


# ---------- 4. 角色错误 ref → validator 拒绝（7.3 available refs） ----------

def test_wrong_role_ref_rejected():
    gt = _gt(references={"target": {"node_id": "obj_1", "label": "chair"}})
    p = _plan(target_ref="source")  # source 不在 available refs
    errors = _validate_grounded_plan(p, _grounding(), grounded_task=gt)
    assert any("not in available refs" in e for e in errors)


# ---------- 5. target_ref 编译成正确 ID ----------

def test_target_ref_compiles_to_correct_id():
    gt = _gt(references={"target": {"node_id": "obj_1", "label": "chair"},
                         "destination": {"node_id": "room_1", "label": "room"}})
    p = _resolve_target_refs(_plan("target"), gt)
    assert p.task_chain[0].target_id == "obj_1"
    p2 = _resolve_target_refs(_plan("destination"), gt)
    assert p2.task_chain[0].target_id == "room_1"


# ---------- 6. step-specific ref 编译正确 ----------

def test_step_specific_ref_compiles_correctly():
    gt = _gt(steps=[{"references": {"destination": {"node_id": "room_1", "label": "room"}}}])
    p = _resolve_target_refs(_plan("step_0_destination"), gt)
    assert p.task_chain[0].target_id == "room_1"


# ---------- 7. step-ref swap → 拒绝（8.1 角色绑定） ----------

def test_step_ref_swap_rejected():
    # step 0 只有 destination role；LLM 却引用 step_0_target
    gt = _gt(steps=[{"references": {"destination": {"node_id": "room_1", "label": "room"}}}])
    p = _plan(target_ref="step_0_target")
    p2 = _resolve_target_refs(p, gt)
    errors = _validate_grounded_plan(p2, _grounding(), grounded_task=gt)
    assert any("step-specific ref" in e and "missing" in e for e in errors)


# ---------- 8/9/10. Grounding status gate（oracle pipeline_status） ----------

def _oracle_status(status: str):
    from run_pipeline_eval import _run_planner_with_oracle_grounding
    return _run_planner_with_oracle_grounding(
        "find chair", {"pipeline_status": status}, "mistral:latest", {}, "/tmp")


def test_ambiguous_grounding_blocks_plan():
    r = _oracle_status("ambiguous")
    assert r.status == "ambiguous"
    assert r.task_plan is None


def test_not_found_grounding_blocks_plan():
    r = _oracle_status("not_found")
    assert r.status == "target_not_found"
    assert r.task_plan is None


def test_unsupported_action_blocks_plan():
    r = _oracle_status("unsupported")
    assert r.status == "unsupported"
    assert r.task_plan is None


# ---------- 11/12. avoid / prefer 约束保留 ----------

def test_avoid_constraint_preserved():
    g = _grounding(constraints=[("constraint_avoid_0", "kitchen_1")])
    gt = _gt(constraints=[{"type": "avoid", "node_id": "kitchen_1", "label": "kitchen"}])
    # 计划不含 avoid → error
    p_missing = _resolve_target_refs(_plan(target_ref="target", avoid=[]), gt)
    errors = _validate_grounded_plan(p_missing, g, grounded_task=gt)
    assert any("Avoid constraints not applied" in e for e in errors)
    # 计划含 avoid → pass
    p_ok = _resolve_target_refs(_plan(target_ref="target", avoid=["constraint_avoid_0"]), gt)
    errors2 = _validate_grounded_plan(p_ok, g, grounded_task=gt)
    assert not any("Avoid constraints not applied" in e for e in errors2)


def test_prefer_constraint_preserved():
    g = _grounding(constraints=[("constraint_prefer_0", "corridor_a")])
    gt = _gt(constraints=[{"type": "prefer", "node_id": "corridor_a", "label": "corridor_a"}])
    p_missing = _resolve_target_refs(_plan(target_ref="target", prefer=[]), gt)
    errors = _validate_grounded_plan(p_missing, g, grounded_task=gt)
    assert any("Prefer constraints not applied" in e for e in errors)
    p_ok = _resolve_target_refs(_plan(target_ref="target", prefer=["constraint_prefer_0"]), gt)
    errors2 = _validate_grounded_plan(p_ok, g, grounded_task=gt)
    assert not any("Prefer constraints not applied" in e for e in errors2)


# ---------- 13. executor 映射 ----------

def test_executor_mapping():
    from schema import ACTION_EXECUTOR_MAP
    # 用一个合法 ExecutorType 但映射错误的 executor（navigate_to 需要 nav2）
    for action, exp_executor in ACTION_EXECUTOR_MAP.items():
        if action in ("wait", "ask_clarification", "return_to_base"):
            continue
        wrong_executor = "scene_graph" if exp_executor != "scene_graph" else "nav2"
        p = _plan(target_ref="target", action=action, executor=wrong_executor)
        errors = _validate_grounded_plan(p, _grounding(), grounded_task=_gt())
        assert any(f"requires executor '{exp_executor}'" in e for e in errors), f"{action} 未捕获错误 executor"
        # 正确 executor 不应报错
        p_ok = _plan(target_ref="target", action=action, executor=exp_executor)
        errors_ok = _validate_grounded_plan(p_ok, _grounding(), grounded_task=_gt())
        assert not any("requires executor" in e for e in errors_ok), f"{action} 正确 executor 误报"


# ---------- 14. repair 不接收 GT plan ----------

def test_repair_does_not_receive_ground_truth_plan():
    import inspect
    src = inspect.getsource(_repair_plan)
    # repair prompt 不允许包含 expected/ground-truth plan 关键字
    forbidden = ["expected_plan", "expected_steps", "ground_truth", "expected_actions"]
    for kw in forbidden:
        assert kw not in src, f"repair prompt 泄漏了 '{kw}'"


# ---------- 15. 幻觉 ID rate = 0（validator 拒绝未接地 ID） ----------

def test_hallucinated_id_rate_zero():
    gt = _gt(references={"target": {"node_id": "obj_1", "label": "chair"}})
    p = _plan(target_ref="target", target_id="FAKE_ID_12345")
    errors = _validate_grounded_plan(p, _grounding(), grounded_task=gt)
    assert any("not in grounded references" in e for e in errors)


# ---------- 16. role 正确但 id 错 → wrong-role 拒绝 ----------

def test_wrong_role_id_rejected():
    gt = _gt(references={"target": {"node_id": "obj_1", "label": "chair"},
                         "destination": {"node_id": "room_1", "label": "room"}})
    # target_ref=target 但 target_id 绑到 room_1（错角色）
    p = _plan(target_ref="target", target_id="room_1")
    errors = _validate_grounded_plan(p, _grounding(roles={"target": "obj_1", "destination": "room_1"}),
                                     grounded_task=gt)
    assert any("requires" in e and "got" in e for e in errors)
