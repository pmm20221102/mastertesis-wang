"""Simple Planner schema + Deterministic Compiler 单元测试（指导第 21 节）。

Simple Planner：schema 只允许 allowed actions、ref 必填/无 raw id。
Compiler：executor/type/id/constraints/numbering/escort-actor 全部确定性，目标 100%。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from schema import SimplePlanDraft, SimplePlanStep  # noqa: E402
from compile_simple_plan import compile_simple_plan  # noqa: E402


def _gt(references=None, constraints=None):
    base = {
        "references": {
            "target": {"node_id": "obj_1", "node_type": "object", "label": "chair"},
            "actor": {"node_id": "pat_1", "node_type": "agent", "label": "person"},
            "source": {"node_id": "room_a", "node_type": "room", "label": "room a"},
            "destination": {"node_id": "room_b", "node_type": "room", "label": "room b"},
        },
        "constraints": [],
        "steps": [],
    }
    if references is not None:
        base["references"] = references
    if constraints is not None:
        base["constraints"] = constraints
    return base


def _draft(*steps):
    return SimplePlanDraft(status="valid", steps=[SimplePlanStep(**s) for s in steps])


# ---------- Simple Planner schema ----------

def test_only_allowed_actions():
    with pytest.raises(ValidationError):
        SimplePlanStep(action="locate_entity", ref="target")
    with pytest.raises(ValidationError):
        SimplePlanStep(action="avoid_area", ref="target")


def test_ref_must_exist_when_required():
    with pytest.raises(ValidationError):
        SimplePlanStep(action="navigate_to", ref=None)
    with pytest.raises(ValidationError):
        SimplePlanStep(action="navigate_to", ref="bogus")
    with pytest.raises(ValidationError):
        SimplePlanStep(action="escort_to", ref=None)


def test_ref_forbidden_on_no_ref_actions():
    SimplePlanStep(action="return_to_base", ref=None)  # OK
    SimplePlanStep(action="wait", ref=None)  # OK
    with pytest.raises(ValidationError):
        SimplePlanStep(action="return_to_base", ref="target")


def test_no_raw_node_ids_in_llm_output():
    # raw node id 不在有效 role 集合 → 拒绝（LLM 无法输出 ID）
    with pytest.raises(ValidationError):
        SimplePlanStep(action="navigate_to", ref="5692549928996307171")


def test_simple_plan_draft_roundtrip():
    d = _draft({"action": "navigate_to", "ref": "target"})
    assert d.steps[0].action == "navigate_to"
    assert d.steps[0].ref == "target"


# ---------- Deterministic Compiler ----------

def test_executor_is_deterministic():
    plan = compile_simple_plan(_draft({"action": "navigate_to", "ref": "target"}), _gt())
    assert plan.task_chain[0].executor == "nav2"
    plan2 = compile_simple_plan(_draft({"action": "return_to_base", "ref": None}), _gt())
    assert plan2.task_chain[0].executor == "nav2"
    plan3 = compile_simple_plan(_draft({"action": "wait", "ref": None}), _gt())
    assert plan3.task_chain[0].executor == "none"


def test_target_type_from_grounded_ref():
    plan = compile_simple_plan(_draft({"action": "navigate_to", "ref": "target"}), _gt())
    assert plan.task_chain[0].target_type == "object"
    plan2 = compile_simple_plan(_draft({"action": "navigate_to", "ref": "destination"}), _gt())
    assert plan2.task_chain[0].target_type == "room"


def test_ref_compiles_to_correct_id():
    plan = compile_simple_plan(_draft({"action": "navigate_to", "ref": "destination"}), _gt())
    assert plan.task_chain[0].target_id == "room_b"


def test_avoid_auto_attached():
    gt = _gt(constraints=[{"type": "avoid", "node_id": "kitchen_1", "node_type": "room", "label": "kitchen"}])
    plan = compile_simple_plan(_draft({"action": "navigate_to", "ref": "target"}), gt)
    assert plan.task_chain[0].constraints.avoid == ["kitchen_1"]


def test_prefer_policy_auto_attached():
    gt = _gt(constraints=[{"type": "prefer", "value": "shortest_path", "label": "shortest_path"}])
    plan = compile_simple_plan(_draft({"action": "navigate_to", "ref": "target"}), gt)
    # prefer 是 route policy（非 node id）
    assert plan.task_chain[0].constraints.prefer == ["shortest_path"]


def test_escort_actor_auto_bound():
    refs = {"actor": {"node_id": "pat_1", "node_type": "agent"},
            "destination": {"node_id": "room_b", "node_type": "room"}}
    plan = compile_simple_plan(_draft({"action": "escort_to", "ref": "destination"}), _gt(references=refs))
    step = plan.task_chain[0]
    assert step.role_ids == {"actor": "pat_1", "destination": "room_b"}
    assert step.executor == "nav2"
    assert step.target_id == "room_b"


def test_step_numbers_generated():
    plan = compile_simple_plan(_draft(
        {"action": "navigate_to", "ref": "source"},
        {"action": "navigate_to", "ref": "destination"},
    ), _gt())
    assert [s.step for s in plan.task_chain] == [1, 2]


def test_wrong_role_impossible_after_compile():
    # Compiler 只从 grounded refs 读 ID；LLM 无法注入错误 id
    plan = compile_simple_plan(_draft({"action": "navigate_to", "ref": "target"}), _gt())
    assert plan.task_chain[0].target_id == "obj_1"
    # 不存在 ref → target_id None（validator 会捕获，但 LLM 无法编造 ID）
    plan2 = compile_simple_plan(_draft({"action": "navigate_to", "ref": "target"}),
                                _gt(references={"other": {"node_id": "x", "node_type": "object"}}))
    assert plan2.task_chain[0].target_id is None
