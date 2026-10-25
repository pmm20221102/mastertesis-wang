"""Synthetic contract regressions; no benchmark reads, DB writes or model IO."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main_rag as planner
from llm.intent_parser import parse_intent_with_llm
from schema import TaskPlan

CONFIG = {"ollama_host": "http://127.0.0.1:11434"}


def task():
    return {"action": "navigate", "references": {
        "target": {"node_id": "synthetic-bed", "node_type": "object", "label": "bed"}},
        "steps": [], "constraints": [
            {"key": "constraint_avoid_0", "type": "avoid", "node_id": "synthetic-cabinet"}]}


def draft(**changes):
    step = {"step": 1, "action": "navigate_to", "target_type": "object",
            "target_ref": "target", "target_id": "synthetic-bed",
            "description": "synthetic", "executor": "nav2"}
    step.update(changes)
    return {"status": "valid", "task_chain": [step]}


def serve(monkeypatch, *responses):
    import ollama
    calls = []
    queue = iter(responses)
    def chat(self, **kwargs):
        calls.append(kwargs)
        data = next(queue)
        if isinstance(data, Exception):
            raise data
        return SimpleNamespace(message=SimpleNamespace(
            content=data if isinstance(data, str) else json.dumps(data), thinking=None))
    monkeypatch.setattr(ollama.Client, "chat", chat)
    return calls


def invoke(stage, grounded=None):
    grounded = grounded or task()
    if stage == "initial":
        return planner._generate_grounded_plan("Go to the bed.", grounded, "synthetic", CONFIG)
    return planner._repair_plan(TaskPlan(status="valid", task_chain=[]), ["synthetic error"],
                                grounded, "synthetic", CONFIG)


@pytest.mark.parametrize("stage", ["initial", "repair"])
def test_request_schema_requires_actual_role_ref(monkeypatch, stage):
    calls = serve(monkeypatch, draft())
    invoke(stage)
    schema = calls[0]["format"]
    step = schema["$defs"]["PlanDraftStep"]
    assert "target_ref" in step["required"]
    assert set(step["properties"]["target_ref"]["enum"]) == {"", "target", "constraint_avoid_0"}
    assert step["additionalProperties"] is False
    assert schema["$defs"]["Constraints"]["properties"]["avoid"]["items"]["enum"] == ["constraint_avoid_0"]


@pytest.mark.parametrize("stage", ["initial", "repair"])
@pytest.mark.parametrize("changes", [
    {"target_ref": "destination"},
    {"target_id": "invented-node"},
    {"constraints": {"avoid": ["synthetic-cabinet"]}},
    {"constraints": {"avoid": ["constraint_avoid_1"]}},
    {"role_refs": {"target": "destination"}},
    {"role_refs": {"bogus": "target"}},
])
def test_output_cannot_bypass_available_reference_contract(monkeypatch, stage, changes):
    serve(monkeypatch, draft(**changes))
    with pytest.raises(ValueError):
        invoke(stage)


@pytest.mark.parametrize("action,executor", [("wait", "none"), ("ask_clarification", "llm"),
                                           ("return_to_base", "nav2")])
def test_no_ref_actions_keep_explicit_empty_ref(monkeypatch, action, executor):
    serve(monkeypatch, draft(action=action, executor=executor, target_ref="", target_id=None,
                             target_type="unknown"))
    plan = invoke("initial")
    assert plan.task_chain[0].target_ref == ""


@pytest.mark.parametrize("action", ["escort_to", "guide_to"])
def test_multi_role_actions_keep_existing_support(monkeypatch, action):
    grounded = {"action": action[:-3], "references": {
        "actor": {"node_id": "synthetic-person", "node_type": "agent"},
        "destination": {"node_id": "synthetic-room", "node_type": "room"}}, "constraints": []}
    serve(monkeypatch, draft(action=action, target_type="person", target_ref="", target_id="synthetic-room",
                            role_refs={"actor": "actor", "destination": "destination"}))
    plan = planner._resolve_target_refs(invoke("initial", grounded), grounded)
    assert plan.task_chain[0].role_ids == {"actor": "synthetic-person", "destination": "synthetic-room"}


def test_constraint_role_expands_set_without_model_selecting_ids(monkeypatch):
    grounded = task()
    grounded["constraints"].append({"key": "constraint_avoid_0", "type": "avoid",
                                   "node_id": "synthetic-cabinet-2"})
    serve(monkeypatch, draft(constraints={"avoid": ["constraint_avoid_0"]}))
    plan = planner._resolve_target_refs(invoke("initial", grounded), grounded)
    assert plan.task_chain[0].target_id == "synthetic-bed"
    assert plan.task_chain[0].constraints.avoid == ["synthetic-cabinet", "synthetic-cabinet-2"]


@pytest.mark.parametrize("bad", ['{"action":', {"action": "impossible"},
    {"action": "wait", "constraints": [{"relation": "near"}]}])
def test_parse_schema_error_consumes_one_retry_and_keeps_evidence(monkeypatch, bad):
    calls = serve(monkeypatch, bad, {"action": "wait"})
    records = []
    result = parse_intent_with_llm("Wait.", model="synthetic", config=CONFIG,
        attempt_callback=lambda rows: records.__setitem__(slice(None), rows))
    assert result.action == "wait"
    assert len(calls) == 2
    assert len(records) == 2
    assert records[0]["error_phase"] == "parse_schema"
    assert records[0]["parse_raw_content"] is not None
    assert records[1]["parse_status"] == "completed"
    assert "parse_schema" in str(calls[1]["messages"])


def test_persistent_schema_failure_stops_after_two(monkeypatch):
    calls = serve(monkeypatch, '{"action":', '{"action":', {"action": "wait"})
    with pytest.raises(ValueError) as caught:
        parse_intent_with_llm("Wait.", model="synthetic", config=CONFIG)
    assert len(calls) == 2
    assert len(caught.value.intent_parse_attempts) == 2


def test_schema_and_sequence_share_same_two_parse_budget(monkeypatch):
    calls = serve(monkeypatch, '{"action":', {"action": "wait"},
                  {"action": "wait", "steps": [{"action": "wait"}, {"action": "wait"}]})
    with pytest.raises(ValueError):
        parse_intent_with_llm("Wait, then wait.", model="synthetic", config=CONFIG)
    assert len(calls) == 2


def test_transport_failure_is_not_reparsed(monkeypatch):
    calls = serve(monkeypatch, RuntimeError("synthetic transport failure"), {"action": "wait"})
    with pytest.raises(RuntimeError):
        parse_intent_with_llm("Wait.", model="synthetic", config=CONFIG)
    assert len(calls) == 1


def test_callback_failure_is_not_reparsed(monkeypatch):
    calls = serve(monkeypatch, {"action": "wait"}, {"action": "wait"})
    def broken(rows):
        raise RuntimeError("synthetic callback failure")
    with pytest.raises(RuntimeError):
        parse_intent_with_llm("Wait.", model="synthetic", config=CONFIG, attempt_callback=broken)
    assert len(calls) == 1


def test_legacy_eval_keeps_single_schema_failure(monkeypatch):
    calls = serve(monkeypatch, '{"action":', {"action": "wait"})
    with pytest.raises(ValueError):
        parse_intent_with_llm("Wait.", model="synthetic", config=CONFIG, eval_mode=True)
    assert len(calls) == 1


@pytest.mark.parametrize("action,executor", [("wait", "none"), ("ask_clarification", "llm"),
                                           ("return_to_base", "nav2")])
@pytest.mark.parametrize("changes", [{"target_ref": "target"},
                                   {"target_ref": "", "role_refs": {"target": "target"}}])
def test_no_ref_actions_cannot_silently_discard_target_semantics(monkeypatch, action, executor, changes):
    serve(monkeypatch, draft(action=action, executor=executor, target_type="unknown", **changes))
    with pytest.raises(ValueError):
        invoke("initial")


@pytest.mark.parametrize("stage", ["initial", "repair"])
def test_empty_model_output_is_a_contract_error_not_silent_fallback(monkeypatch, stage):
    from llm.plan_contract import PlanDraftContractError
    serve(monkeypatch, "")
    with pytest.raises(PlanDraftContractError):
        invoke(stage)
