"""CPU-only simple planner scoring and attempt provenance regressions."""
import json
import sys
from pathlib import Path

import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval import run_planner_simple_eval as evaluator
from schema import SimplePlanDraft


@pytest.mark.parametrize("both_fail", [False, True])
def test_attempt_count_includes_failed_calls(monkeypatch, both_fail):
    calls = []
    def generate(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1 or both_fail:
            raise ValueError("synthetic parse error")
        return SimplePlanDraft(status="valid", steps=[{"action": "navigate_to", "ref": "target"}])
    monkeypatch.setattr(evaluator, "generate_simple_plan", generate)
    case = {"case_id": "synthetic", "instruction": "synthetic", "oracle_grounding": {
        "target": {"node_id": "object-1", "node_type": "object"}},
        "expected_symbolic_plan": {"status": "valid", "steps": [{"action": "navigate_to", "ref": "target"}]}}
    result = evaluator.evaluate_case(case, "fake", {})
    assert result["attempt_count"] == len(calls) == 2
    assert result["schema_valid"] is (not both_fail)
    assert result["scorer_version"] == "simple_planner_metrics_v2"


def test_summary_keeps_fractional_accuracy_and_all_first_pass_cases(monkeypatch, tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps({"case_id": "a"}) + "\n" + json.dumps({"case_id": "b"}) + "\n")
    monkeypatch.setattr(evaluator, "CASES", cases)
    def evaluate(case, *args):
        repaired = case["case_id"] == "b"
        return {"case_id": case["case_id"], "planner_invoked": True, "repair_rate": 1 if repaired else None,
                "strict_symbolic_success": True, "role_ref_accuracy": 1 if repaired else 0.5,
                "latency_ms": 0, "schema_valid": True, "first_pass_errors": ["schema"] if repaired else [],
                "compiled_validation_pass": True, "scorer_version": "simple_planner_metrics_v2"}
    monkeypatch.setattr(evaluator, "evaluate_case", evaluate)
    monkeypatch.setattr(evaluator, "_write_manifest", lambda *args: None)
    output = tmp_path / "results"
    evaluator.run_eval("fake", 1, output)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["symbolic_role_ref_accuracy"] == 0.75
    assert summary["first_pass_success"] == 0.5
    assert summary["first_pass_denominator"] == 2
    assert summary["scorer_version"] == "simple_planner_metrics_v2"
