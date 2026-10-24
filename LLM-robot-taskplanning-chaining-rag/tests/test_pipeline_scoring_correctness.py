"""Strict scoring and denominators with synthetic planner/grounding boundaries."""
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval import run_pipeline_eval as evaluation
from grounding.orchestrator import GroundingResult
from api_schemas import ResolutionCandidate, ResolutionResult
from planner_schemas.intent import EntityReference
from schema import PipelineResult
import grounding.orchestrator
import scene_graph_api


def summary(results, cases=None):
    return evaluation.compute_summary({"mode": "fake", "model": "fake", "repeats": 1, **results}, cases)


def ground_score(monkeypatch, selected="wrong", extra=None, raises=False, expected_id="right", tag="relation"):
    result = GroundingResult()
    for role, identifier in [("target", selected), *(([("actor", extra)]) if extra else [])]:
        result.add_result(role, ResolutionResult(status="resolved", reference=EntityReference(text=role),
            selected=ResolutionCandidate(node_id=identifier, node_type="object", label="chair")))
    def outcome(intent):
        if raises:
            raise RuntimeError("synthetic grounding exception")
        return result
    monkeypatch.setattr(scene_graph_api, "SceneGraphRAG", lambda *args: None)
    monkeypatch.setattr(grounding.orchestrator, "GroundingOrchestrator",
                        lambda *args: SimpleNamespace(ground_intent=outcome))
    expected = {"pipeline_status": "ready", "target": {"status": "resolved"}}
    if expected_id is not None:
        expected["target"]["node_id"] = expected_id
    case = {"case_id": "synthetic", "instruction": "synthetic", "tags": [tag],
        "oracle_intent": {"action": "locate", "target": {"text": "chair", "node_type": "object"}},
        "expected_grounding": expected}
    return evaluation.evaluate_grounding_case(case, "fake", "fake", {}, oracle_intent=True), case


def test_relation_exact_rejects_wrong_entity(monkeypatch):
    result, _ = ground_score(monkeypatch)
    assert not result["grounding_case_success"]
    assert result["scorer_version"] == "pipeline_contracts_v2"


def test_spurious_role_prevents_grounding_exact(monkeypatch):
    result, _ = ground_score(monkeypatch, selected="right", extra="person")
    assert result["case_failure"] and not result["grounding_case_success"]


def test_missing_id_gt_does_not_vacuously_prove_exact(monkeypatch):
    result, _ = ground_score(monkeypatch, selected="right", expected_id=None, tag="exact_id")
    assert not result["grounding_case_success"]


def test_grounding_exception_retains_expected_role_denominator(monkeypatch):
    result, case = ground_score(monkeypatch, raises=True, tag="exact_id")
    report = summary({"grounding_results": [result], "planning_results": []}, [case])
    assert report["grounding"]["role_status_evaluated_count"] == 1
    assert report["grounding"]["role_id_evaluated_count"] == 1
    assert report["grounding"]["role_entity_id_accuracy"] == 0


def planning_score(monkeypatch, graph_ids=None, chain=None, raw_draft=None):
    import main_rag
    payload = {"status": "valid", "task_chain": chain or [{
        "step": 1, "action": "navigate_to", "target_ref": "target", "target_id": "right",
        "constraints": {"avoid": [], "prefer": ["quiet"]}}]}
    monkeypatch.setattr(evaluation, "run_structured_task_planner", lambda *args, **kwargs: PipelineResult(
        status="valid", task_plan=payload, raw_plan_draft=raw_draft, grounding={"references": {"target": {
            "selected": {"node_id": "right"}}}}, grounded_task={
                "constraints": [{"type": "prefer", "node_id": "quiet", "label": "quiet"}]}))
    case = {"case_id": "synthetic", "instruction": "synthetic", "expected_plan": {
        "status": "valid", "required_actions": ["navigate_to"], "expected_prefer_ids": ["quiet"],
        "expected_steps": [{"target_ref": "target"}, {"target_ref": "destination"}]}}
    return evaluation.evaluate_planning_case(case, "fake", "fake", {}, all_graph_ids=graph_ids)


def test_missing_graph_is_not_zero_hallucination_evidence(monkeypatch):
    result = planning_score(monkeypatch)
    report = summary({"grounding_results": [], "planning_results": [result]})
    assert report["planning"]["hallucinated_id_rate"] is None


def test_empty_known_graph_checks_every_emitted_id(monkeypatch):
    result = planning_score(monkeypatch, graph_ids=set())
    assert "right" in result["hallucinated_ids"]
    assert result["graph_id_check_available"]


def test_missing_expected_step_counts_in_ref_denominator(monkeypatch):
    result = planning_score(monkeypatch, graph_ids={"right"})
    assert result["step_target_ref_total"] == 2 and result["step_target_ref_accuracy"] == 0.5


def test_prefer_summary_uses_prefer_not_avoid(monkeypatch):
    result = planning_score(monkeypatch, graph_ids={"right"})
    assert result["constraint_prefer_correct"]
    report = summary({"grounding_results": [], "planning_results": [result]})
    assert report["planning"]["constraint_prefer_accuracy"] == 1.0


@pytest.mark.parametrize("raw", [{"_raw_text": "invalid-json"}, {"status": "valid"}])
def test_recorded_raw_draft_is_schema_checked_not_just_present(monkeypatch, raw):
    result = planning_score(monkeypatch, graph_ids={"right"}, raw_draft=raw)
    assert not result["raw_plan_valid"]
