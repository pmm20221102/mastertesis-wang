"""Synthetic grounding contracts; runnable under the existing offline guard."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "LLM-robot-taskplanning-chaining-rag"))
sys.path.insert(0, str(ROOT / "RAG/khronos_3dsg_rag/src"))

import pytest
from api_schemas import ResolutionCandidate, ResolutionResult
from grounding.orchestrator import GroundingOrchestrator, GroundingResult
from planner_schemas.intent import EntityReference, IntentStep, TaskConstraint, TaskIntent


def candidate(node_id):
    return ResolutionCandidate(node_id=node_id, node_type="object", label=node_id,
                               position=[1.0 if node_id.endswith("_1") else 2.0, 0.0, 0.0])


class FakeRAG:
    """Only the in-memory resolution boundary is replaced."""
    def __init__(self, statuses=None):
        self.statuses = statuses or {}

    def resolve_entity(self, ref):
        status = self.statuses.get(ref.text, "resolved")
        choices = [candidate(ref.text + "_1"), candidate(ref.text + "_2")]
        return ResolutionResult(
            status=status, reference=ref,
            selected=choices[0] if status == "resolved" else None,
            selected_many=choices if status == "resolved_set" else [],
            candidates=choices if status != "not_found" else [],
        )


@pytest.mark.parametrize("status", ["ambiguous", "not_found", "unsupported", "system_error"])
def test_hard_avoid_blocks_with_exact_status(status):
    intent = TaskIntent(action="navigate", target=EntityReference(text="goal"),
                        constraints=[TaskConstraint(type="avoid", description="Keep clear", target_text="hazard")])
    result = GroundingOrchestrator(FakeRAG({"hazard": status})).ground_intent(intent)
    assert result.status == status
    result.recompute_status()
    assert result.status == status


def test_soft_preference_and_optional_room_stay_nonblocking_after_recompute():
    intent = TaskIntent(action="navigate", target=EntityReference(text="goal", room="missing"),
                        constraints=[TaskConstraint(type="prefer", description="Optional", target_text="quiet")])
    result = GroundingOrchestrator(FakeRAG({"missing": "not_found", "quiet": "ambiguous"})).ground_intent(intent)
    result.recompute_status()
    assert result.status == "ready"
    assert result.errors == []


def test_legacy_constraint_default_is_optional():
    result = GroundingResult()
    resolution = FakeRAG({"quiet": "not_found"}).resolve_entity(EntityReference(text="quiet"))
    result.add_constraint_result("constraint_prefer_0", resolution)
    result.recompute_status()
    assert result.status == "ready"


@pytest.mark.parametrize("target_text", [None, "door"])
def test_condition_metadata_survives_without_requiring_entity(target_text):
    condition = TaskConstraint(type="condition", description="Wait until the door opens", target_text=target_text)
    result = GroundingOrchestrator(FakeRAG({"door": "not_found"})).ground_intent(
        TaskIntent(action="wait", constraints=[condition]))
    expected = {"ref": "constraint_condition_0", "key": "constraint_condition_0",
                "type": "condition", "description": condition.description}
    assert result.condition_constraints == [expected]
    assert result.to_dict()["condition_constraints"] == [expected]
    assert result.status == "ready"
    if target_text:
        assert isinstance(result.constraints["constraint_condition_0"], ResolutionResult)


@pytest.mark.parametrize("action,role", [
    ("navigate", "target"), ("navigate", "destination"), ("navigate", "source"),
    ("locate", "target"), ("avoid", "target"),
    ("escort", "actor"), ("escort", "source"), ("escort", "destination"),
    ("guide", "actor"), ("guide", "target"), ("guide", "destination"),
])
@pytest.mark.parametrize("status", ["not_found", "ambiguous"])
def test_required_step_role_blocks(action, role, status):
    step = IntentStep(action=action, **{role: EntityReference(text="missing")})
    result = GroundingOrchestrator(FakeRAG({"missing": status})).ground_intent(
        TaskIntent(action=action, steps=[step]))
    assert result.references[f"step_0_{role}"].status == status
    assert result.status == status
    result.recompute_status()
    assert result.status == status


def test_wait_incidental_roles_and_multistep_summary_do_not_block():
    result = GroundingOrchestrator(FakeRAG({"summary": "not_found", "incidental": "ambiguous"})).ground_intent(
        TaskIntent(action="navigate", target=EntityReference(text="summary"), steps=[
            IntentStep(action="wait", actor=EntityReference(text="incidental")),
            IntentStep(action="navigate", target=EntityReference(text="goal"))]))
    result.recompute_status()
    assert result.status == "ready"


def test_step_constraints_keep_original_indices_and_condition_description():
    constraints = [TaskConstraint(type="prefer", description="soft", target_text="unknown"),
                   TaskConstraint(type="avoid", description="hard", target_text="hazard"),
                   TaskConstraint(type="condition", description="Wait until ready")]
    result = GroundingOrchestrator(FakeRAG({"unknown": "not_found"})).ground_intent(
        TaskIntent(action="navigate", steps=[IntentStep(action="navigate", source=EntityReference(text="start"),
            target=EntityReference(text="goal"), constraints=constraints)]))
    assert "step_0_source" in result.references
    assert set(result.constraints) == {"step_0_constraint_prefer_0", "step_0_constraint_avoid_1"}
    assert result.condition_constraints == [{"ref": "step_0_constraint_condition_2",
        "key": "step_0_constraint_condition_2", "type": "condition", "description": "Wait until ready"}]
    assert all(isinstance(value, ResolutionResult) for value in result.constraints.values())
    result.recompute_status()
    assert result.status == "ready"


def test_step_hard_avoid_failure_blocks():
    result = GroundingOrchestrator(FakeRAG({"hazard": "not_found"})).ground_intent(
        TaskIntent(action="navigate", steps=[IntentStep(action="navigate", target=EntityReference(text="goal"),
            constraints=[TaskConstraint(type="avoid", description="Keep clear", target_text="hazard")])]))
    assert result.status == "not_found"


def test_all_id_accessors_include_selected_many_but_not_unselected_candidates():
    result = GroundingResult()
    selected = [candidate("a"), candidate("b")]
    resolution = ResolutionResult(status="resolved_set", reference=EntityReference(text="all"),
                                  selected_many=selected, candidates=selected + [candidate("unselected")])
    result.add_result("target", resolution)
    result.add_constraint_result("constraint_avoid_0", resolution)
    result.add_constraint_result("step_0_constraint_prefer_1", resolution)
    assert result.get_resolved_ids() == {"a", "b"}
    assert result.get_all_resolved_ids() == {"a", "b"}
    assert result.get_constraint_ids() == {"a", "b"}
    assert result.get_constraint_avoid_ids() == {"a", "b"}
    assert result.get_constraint_prefer_ids() == {"a", "b"}
    assert result.to_dict()["constraints"]["constraint_avoid_0"]["selected_node_ids"] == ["a", "b"]


@pytest.mark.parametrize("quantifier,status", [("all", "resolved_set"), ("any", "resolved"), ("nearest", "resolved")])
def test_explicit_quantifier_ambiguous_fallback(quantifier, status):
    result = GroundingOrchestrator(FakeRAG({"goal": "ambiguous"})).ground_intent(
        TaskIntent(action="locate", target=EntityReference(text="goal", quantifier=quantifier, position=[0, 0, 0])))
    resolution = result.references["target"]
    assert resolution.status == status
    if quantifier == "all":
        assert result.get_resolved_ids() == {"goal_1", "goal_2"}
    else:
        assert resolution.selected.node_id == "goal_1"


def test_resolved_nearest_api_selection_is_preserved():
    ref = EntityReference(text="goal", quantifier="nearest", position=[0, 0, 0])
    resolution = FakeRAG().resolve_entity(ref)
    resolution.selected = resolution.candidates[1]
    assert GroundingOrchestrator(FakeRAG())._apply_quantifier(ref, resolution).selected.node_id == "goal_2"


def test_quantifier_one_remains_ambiguous():
    result = GroundingOrchestrator(FakeRAG({"goal": "ambiguous"})).ground_intent(
        TaskIntent(action="locate", target=EntityReference(text="goal")))
    assert result.status == "ambiguous"


def test_step_quantifier_all_is_preserved():
    result = GroundingOrchestrator(FakeRAG({"goal": "ambiguous"})).ground_intent(
        TaskIntent(action="locate", steps=[IntentStep(action="locate", target=EntityReference(text="goal", quantifier="all"))]))
    assert result.references["step_0_target"].status == "resolved_set"
    assert result.get_resolved_ids() == {"goal_1", "goal_2"}


def test_nearest_missing_position_does_not_use_semantic_score_as_distance():
    result = GroundingOrchestrator(FakeRAG({"goal": "ambiguous"})).ground_intent(
        TaskIntent(action="locate", target=EntityReference(text="goal", quantifier="nearest")))
    assert result.status == "not_found"


if __name__ == "__main__":
    # Reuse the shared runner's guard verbatim, without changing its allowlist or executing its suite.
    guard_source = (ROOT / "docs/optimization-review-20261003/verify_offline.py").read_text()
    namespace = {"__name__": "grounding_offline_guard"}
    exec(compile(guard_source.split("group = sys.argv[1]", 1)[0], "verify_offline.py", "exec"), namespace)
    files = [str(Path(__file__).resolve()), str(Path(__file__).with_name("test_grounding_constraints.py"))]
    code = pytest.main(["-q", "-p", "no:cacheprovider", "-p", "no:logging",
                        "--basetemp", str(namespace["temporary"] / "pytest"), *files])
    print("BLOCKED_SIDE_EFFECTS", namespace["denied"])
    raise SystemExit(code if code else bool(namespace["denied"]))
