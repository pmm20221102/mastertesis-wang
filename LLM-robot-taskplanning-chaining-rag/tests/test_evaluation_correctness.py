"""Synthetic CPU regression checks; no parser/model/DB imports."""
import ast
import copy
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from planner_schemas.intent import TaskIntent
from eval.run_intent_eval import evaluate_intent_case, compute_intent_summary


def score(monkeypatch, predicted, expected=None, wrapped=True):
    intent = TaskIntent.model_validate(predicted)
    expected = copy.deepcopy(predicted if expected is None else expected)
    parser = types.ModuleType("llm.intent_parser")
    parser.parse_intent_with_llm = lambda *a, **k: (
        {"raw_lmm_output": intent.model_dump(mode="json"),
         "canonicalized_intent": intent} if wrapped else intent)
    parser.intent_to_dict = lambda value: value.model_dump(mode="json")
    monkeypatch.setitem(sys.modules, "llm.intent_parser", parser)
    case = {"case_id": "synthetic", "instruction": "synthetic", "oracle_intent": expected}
    return evaluate_intent_case(case, "fake-no-model", {}), case


def e2e_score(pred, case):
    # Execute the actual scorer and its pure helpers without importing pipeline IO.
    path = ROOT / "eval/run_e2e_eval.py"
    tree = ast.parse(path.read_text())
    names = {"evaluate_e2e", "_norm_room", "extract_room_text"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {"re": re, "GATE_STATUSES": {"ambiguous", "not_found", "unsupported"}}
    # Metadata constants are also taken from the real file (once introduced).
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in {"SCORER_VERSION", "EVALUATION_SCOPE"}
                for t in node.targets):
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["evaluate_e2e"](pred, case)


@pytest.mark.parametrize("action", ["wait", "unsupported", "navigate"])
@pytest.mark.parametrize("wrapped", [True, False])
def test_explicit_null_is_valid(monkeypatch, action, wrapped):
    payload = {"action": action, "target": None}
    if action == "navigate":
        payload.update(source={"text": "room 111", "node_type": "room"},
                       destination={"text": "room 222", "node_type": "room"})
    result, _ = score(monkeypatch, payload, wrapped=wrapped)
    assert result["schema_valid"], result["error"]
    assert result["strict_intent_match"]
    assert result["raw_json"]["target"] is None


@pytest.mark.parametrize("role", ["actor", "target", "source", "destination"])
@pytest.mark.parametrize("field,value", [
    ("relations", [{"relation": "near", "target_text": "desk", "target_node_id": "object_1"}]),
    ("locations", [{"text": "room 111", "node_type": "room", "node_id": "room_111"}]),
    ("quantifier", "all"),
    ("aggregate", {"operation": "max_count", "target_labels": ["desks"], "value": 2}),
    ("name", "Alice"), ("attributes", {"color": "red"}), ("position", [1, 2, 3]),
])
def test_extra_role_semantics_fail(monkeypatch, role, field, value):
    oracle = {"action": "locate", role: {"text": "chair", "label": "chairs", "node_type": "object"}}
    predicted = copy.deepcopy(oracle)
    predicted[role][field] = value
    result, _ = score(monkeypatch, predicted, oracle)
    assert result["schema_valid"]
    assert not result.get("strict_intent_match", result["strict_exact_match"])


@pytest.mark.parametrize("field,value", [
    ("relations", [{"relation": "near", "target_text": "desk"}]),
    ("locations", [{"text": "room 111", "node_type": "room"}]),
    ("quantifier", "all"),
    ("aggregate", {"operation": "count_at_least", "target_labels": ["desks"], "value": 2}),
])
def test_missing_role_semantics_fail(monkeypatch, field, value):
    predicted = {"action": "locate", "target": {"text": "chair", "label": "chairs", "node_type": "object"}}
    oracle = copy.deepcopy(predicted)
    oracle["target"][field] = value
    result, _ = score(monkeypatch, predicted, oracle)
    assert not result.get("strict_intent_match", result["strict_exact_match"])


@pytest.mark.parametrize("in_step", [False, True])
@pytest.mark.parametrize("mutation", ["extra", "missing", "target_text", "target_label"])
def test_constraints_whole_fields(monkeypatch, in_step, mutation):
    constraint = {"type": "avoid", "description": "avoid room", "target_text": "room 111", "target_label": "rooms"}
    oracle = {"action": "wait", "target": {"text": "chair", "label": "chairs", "node_type": "object"}}
    if in_step:
        oracle["steps"] = [{"action": "wait", "constraints": [constraint]}]
        scope = oracle["steps"][0]
    else:
        scope = oracle
    scope["constraints"] = [constraint]
    predicted = copy.deepcopy(oracle)
    pscope = predicted["steps"][0] if in_step else predicted
    if mutation == "extra":
        pscope["constraints"].append({"type": "condition", "description": "screen first"})
    elif mutation == "missing":
        pscope["constraints"] = []
    else:
        pscope["constraints"][0][mutation] = "different"
    result, _ = score(monkeypatch, predicted, oracle)
    assert not result.get("strict_intent_match", result["strict_exact_match"])


@pytest.mark.parametrize("field,value", [
    ("value", 3), ("location", {"text": "room 999", "node_type": "room"}),
    ("target_labels", ["chairs"]), ("location", None),
])
def test_aggregate_full_fields(monkeypatch, field, value):
    oracle = {"action": "locate", "target": {"text": "room", "node_type": "room",
              "aggregate": {"operation": "count_at_least", "target_labels": ["desks"], "value": 2,
                            "location": {"text": "room 111", "node_type": "room"}}}}
    predicted = copy.deepcopy(oracle)
    predicted["target"]["aggregate"][field] = value
    result, _ = score(monkeypatch, predicted, oracle)
    assert not result.get("strict_intent_match", result["strict_exact_match"])


def test_equivalent_unordered_fields_and_surface_identity(monkeypatch):
    oracle = {"action": "locate", "target": {"text": "chair", "label": "chairs", "node_type": "object",
        "locations": [{"text": " Room 111 ", "node_type": "room"}, {"text": "hall", "node_type": "place"}],
        "relations": [{"relation": "near", "target_text": "desk"}, {"relation": "inside", "target_text": "hall"}],
        "aggregate": {"operation": "max_count", "target_labels": ["chairs", "desks"]}},
        "constraints": [{"type": "avoid", "description": "stairs"}, {"type": "prefer", "description": "lift"}],
        "steps": [{"action": "wait"}, {"action": "locate", "target": {"text": "desk"}}]}
    predicted = copy.deepcopy(oracle)
    predicted["target"]["text"] = "a seat"
    for key in ("locations", "relations"):
        predicted["target"][key].reverse()
    predicted["target"]["aggregate"]["target_labels"].reverse()
    predicted["constraints"].reverse()
    result, _ = score(monkeypatch, predicted, oracle)
    assert result["strict_intent_match"]
    predicted["steps"].reverse()
    result, _ = score(monkeypatch, predicted, oracle)
    assert not result["strict_intent_match"]


@pytest.mark.parametrize("role", ["source", "destination"])
@pytest.mark.parametrize("text,ok", [("room 999", False), ("Room 111", True), ("111", True)])
def test_e2e_dict_gt_room_value(role, text, ok):
    oracle = {"action": "navigate", role: {"text": "room 111", "node_type": "room"}}
    payload = copy.deepcopy(oracle)
    payload[role]["text"] = text
    pred = {"intent": TaskIntent.model_validate(payload), "grounding": {"status": "not_found", "node_ids": []},
            "planner": {"called": False}, "validation_errors": []}
    case = {"case_id": "synthetic", "instruction": "synthetic", "oracle_intent": oracle,
            "expected_grounding": {"status": "not_found", "node_ids": []},
            "expected_final_status": "target_not_found"}
    result = e2e_score(pred, case)
    assert result["e2e_success"] is ok
    assert result["stage_scores"]["role_value_ok"] is ok
    assert result["evaluation_scope"] == "restricted_office"
    assert result["scorer_version"] == "office_e2e_role_values_v2"
    if not ok:
        assert result["failure_stage"] == "intent_role_value"


def test_new_summary_version_and_legacy_metrics(monkeypatch):
    payload = {"action": "locate", "target": {"text": "chair", "label": "chairs", "node_type": "object"}}
    predicted = copy.deepcopy(payload)
    predicted["target"]["relations"] = [{"relation": "near", "target_text": "desk"}]
    result, case = score(monkeypatch, predicted, payload)
    assert result["strict_exact_match"]  # compatibility: old weak metric retained
    assert not result["strict_intent_match"]
    assert result["scorer_version"] == "intent_whole_fields_v2"
    summary = compute_intent_summary([result], [case])
    assert summary["strict_exact_match_rate"] == 1.0
    assert summary["strict_intent_match_rate"] == 0.0
    assert summary["scorer_version"] == result["scorer_version"]
    legacy = copy.deepcopy(result)
    legacy.pop("scorer_version")
    legacy.pop("strict_intent_match")
    mixed = compute_intent_summary([result, legacy], [case])
    assert mixed["scorer_version"] == "mixed"
    assert mixed["strict_intent_match_rate"] is None


def test_e2e_summary_identifies_corrected_scorer_scope():
    path = ROOT / "eval/run_e2e_eval.py"
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_eval")
    assignment = next(node for node in ast.walk(function) if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "summary" for target in node.targets))
    namespace = {"model": "fake", "repeats": 1, "n": 1, "e2e": 1, "gate_ok": None,
                 "fail_stage": {}, "results": [{}], "stage_rates": {name: 1 for name in
                    ("validator_success", "intent_success", "semantic_success", "grounding_success",
                     "planner_success", "compiler_success")}, "_cond": lambda *args: None,
                 "SCORER_VERSION": "office_e2e_role_values_v2", "EVALUATION_SCOPE": "restricted_office"}
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), str(path), "exec"), namespace)
    assert namespace["summary"]["scorer_version"] == "office_e2e_role_values_v2"
    assert namespace["summary"]["evaluation_scope"] == "restricted_office"
