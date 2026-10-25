from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main_rag  # noqa: E402
from llm.plan_contract import PlanDraftContractError  # noqa: E402
from planner_schemas.intent import TaskIntent  # noqa: E402


def test_compiler_preserves_order_and_resolves_unique_rag_targets():
    intent = TaskIntent.model_validate({
        "action": "navigate",
        "steps": [
            {"action": "navigate", "target": {"text": "first room", "node_type": "room"}},
            {"action": "navigate", "target": {"text": "second room", "node_type": "room"}},
        ],
    })
    grounded = {
        "references": {}, "constraints": [],
        "steps": [
            {"action": "navigate", "description": "first", "references": {
                "target": {"node_id": "room-1", "node_type": "room",
                           "navigation_target": {"position": [1.0, 2.0, 0.0]}}}},
            {"action": "navigate", "description": "second", "references": {
                "target": {"node_id": "room-2", "node_type": "room",
                           "navigation_target": {"position": [3.0, 4.0, 0.0]}}}},
        ],
    }

    plan = main_rag.compile_grounded_navigation_plan(
        intent, grounded, "Visit both rooms in order.")

    assert [step.target_id for step in plan.task_chain] == ["room-1", "room-2"]
    assert [step.target_ref for step in plan.task_chain] == ["step_0_target", "step_1_target"]
    assert [step.action for step in plan.task_chain] == ["navigate_to", "navigate_to"]
    assert all(step.executor == "nav2" for step in plan.task_chain)


def test_compiler_preserves_ambiguous_candidates_for_nav2_selection():
    intent = TaskIntent.model_validate({
        "action": "navigate",
        "steps": [{"action": "navigate", "target": {"text": "a room", "node_type": "room"}}],
    })
    grounded = {"references": {}, "constraints": [], "steps": [{"action": "navigate",
        "references": {"target": {"node_type": "room", "candidate_node_ids": ["r2", "r1"],
                                   "candidate_navigation_targets": [
                                       {"node_id": "r1", "navigation_target": {
                                           "position": [1.0, 2.0, 0.0]}},
                                       {"node_id": "r2", "navigation_target": {
                                           "position": [3.0, 4.0, 0.0]}},
                                   ], "selection_policy": "deferred_to_nav2"}}}]}

    step = main_rag.compile_grounded_navigation_plan(intent, grounded, "Go to a room.").task_chain[0]

    assert step.target_id is None
    assert step.candidate_node_ids == ["r1", "r2"]
    assert step.selection_policy == "deferred_to_nav2"


@pytest.mark.parametrize("intent_data,grounded", [
    ({"action": "locate", "steps": [{"action": "locate", "target": {"text": "chair"}}]},
     {"references": {}, "constraints": [], "steps": [{"action": "locate", "references": {}}]}),
    ({"action": "navigate", "constraints": [{"type": "avoid", "description": "stairs"}],
      "steps": [{"action": "navigate", "target": {"text": "room"}}]},
     {"references": {}, "constraints": [{"type": "avoid", "node_id": "x"}],
      "steps": [{"action": "navigate", "references": {"target": {"node_id": "r"}}}]}),
])
def test_compiler_fails_closed_for_non_navigation_actions_or_route_constraints(
        intent_data, grounded):
    intent = TaskIntent.model_validate(intent_data)

    with pytest.raises(PlanDraftContractError):
        main_rag.compile_grounded_navigation_plan(intent, grounded, "Request")


def test_pipeline_deterministic_backend_never_calls_llm2(monkeypatch):
    from grounding.orchestrator import GroundingResult

    intent = TaskIntent.model_validate({"action": "navigate", "steps": [
        {"action": "navigate", "target": {"text": "room 1", "node_type": "room"}}]})
    grounded = {"references": {}, "constraints": [], "steps": [{"action": "navigate",
        "description": "room 1", "references": {"target": {"node_id": "room-1",
            "node_type": "room", "navigation_target": {"frame_id": "map",
                "position": [1.0, 2.0, 0.0]}}}}]}

    class FakeRag:
        def get_label_ontology(self):
            return ["room"]

    class FakeGrounder:
        def ground_intent(self, _intent):
            result = GroundingResult()
            result.status = "resolved"
            return result

    monkeypatch.setattr(main_rag, "load_config", lambda _path: {})
    monkeypatch.setattr(main_rag, "get_default_model", lambda _config: "synthetic")
    monkeypatch.setattr(main_rag, "SceneGraphRAG", lambda _path: FakeRag())
    monkeypatch.setattr(main_rag, "GroundingOrchestrator", lambda _rag: FakeGrounder())
    monkeypatch.setattr(main_rag, "parse_intent_with_llm", lambda *_a, **_k: intent)
    monkeypatch.setattr(main_rag, "_can_defer_instance_selection", lambda _grounding: False)
    monkeypatch.setattr(main_rag, "_build_grounded_task", lambda *_a: grounded)
    monkeypatch.setattr(main_rag, "_validate_grounded_plan", lambda *_a: [])
    monkeypatch.setattr(main_rag, "_generate_grounded_plan",
                        lambda *_a, **_k: pytest.fail("LLM2 must not run"))

    result = main_rag.run_structured_task_planner(
        "Go to room 1.", db_dir="unused", task_chain_backend="grounded_deterministic")

    assert result.status == "valid"
    assert result.task_plan["task_chain"][0]["target_id"] == "room-1"
    assert result.planning_attempts[0]["phase"] == "grounded_deterministic"
