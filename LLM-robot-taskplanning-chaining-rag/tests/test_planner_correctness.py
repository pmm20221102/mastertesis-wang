"""Correctness regression tests; no model, database or ROS calls."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main_rag as planner
from schema import Constraints, PipelineResult, SimplePlanDraft, TaskPlan, TaskStep
from grounding.orchestrator import GroundingResult
from api_schemas import ResolutionCandidate, ResolutionResult
from planner_schemas.intent import EntityReference, TaskIntent
from compile_simple_plan import compile_simple_plan


def grounding():
    result = GroundingResult()
    for role, identifier in {"target": "object-1", "actor": "person-1",
                             "destination": "room-1", "source": "room-2"}.items():
        result.add_result(role, ResolutionResult(
            reference=EntityReference(text=role), status="resolved",
            selected=ResolutionCandidate(node_id=identifier, node_type="object", label=role)))
    return result


def step(**updates):
    values = dict(step=1, action="navigate_to", target_type="object",
                  target_ref="target", target_id="object-1", description="go", executor="nav2")
    values.update(updates)
    return TaskStep(**values)


def validate(item, result=None, task=None, status="valid"):
    return planner._validate_grounded_plan(
        TaskPlan(status=status, task_chain=[item]), result or grounding(), task)


@pytest.mark.parametrize("compare", [False, True])
def test_structured_cli_writes_object(monkeypatch, tmp_path, compare):
    result = PipelineResult(status="system_error", error="fixture",
                            grounding={"status": "not_found"}, latency_ms={"intent_ms": 1})
    monkeypatch.setattr(planner, "run_structured_task_planner", lambda *args: result)
    monkeypatch.setattr(planner, "run_legacy_task_planner",
                        lambda *args: TaskPlan(status="unsupported", task_chain=[]))
    output = tmp_path / "result.json"
    monkeypatch.setattr(planner, "DEFAULT_OUTPUT_PATH", output)
    monkeypatch.setattr(planner, "BASE_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["main_rag", "-c", "fixture", *(["--compare"] if compare else [])])
    planner.main()
    assert json.loads(output.read_text()) == result.model_dump(mode="json")

def test_cli_accepts_attempt_local_output_path(monkeypatch, tmp_path):
    output = tmp_path / "attempt" / "result.json"
    result = PipelineResult(status="unsupported", error="synthetic")
    monkeypatch.setattr(planner, "run_structured_task_planner", lambda *args: result)
    monkeypatch.setattr(sys, "argv", ["main_rag", "-c", "synthetic", "--output-json", str(output)])
    planner.main()
    assert json.loads(output.read_text())["status"] == "unsupported"


@pytest.mark.parametrize("action,executor", [("wait", "none"), ("ask_clarification", "llm"),
                                             ("return_to_base", "nav2")])
def test_no_ref_correct_executor_is_valid(action, executor):
    assert not validate(step(action=action, executor=executor, target_ref=None, target_id=None))


@pytest.mark.parametrize("action", ["wait", "ask_clarification", "return_to_base"])
def test_no_ref_invalid_executor_is_rejected(action):
    assert validate(step(action=action, executor="external_workflow",
                         target_ref=None, target_id=None))


@pytest.mark.parametrize("action,executor", [("wait", "none"), ("ask_clarification", "llm"),
                                             ("return_to_base", "nav2")])
def test_no_ref_arbitrary_id_is_rejected(action, executor):
    assert validate(step(action=action, executor=executor, target_ref=None, target_id="FAKE"))


@pytest.mark.parametrize("status", ["unsupported", "ambiguous", "system_error", "target_not_found"])
def test_nonvalid_plan_cannot_have_chain(status):
    assert validate(step(), status=status)


def test_maximum_six_steps():
    chain = [step(step=i, action="wait", target_ref=None, target_id=None, executor="none")
             for i in range(1, 8)]
    assert planner._validate_grounded_plan(TaskPlan(status="valid", task_chain=chain), grounding())


def test_role_ids_swapped_rejected():
    assert validate(step(action="escort_to", target_ref=None, target_id="room-1",
                         role_refs={"actor": "actor", "destination": "destination"},
                         role_ids={"actor": "room-1", "destination": "person-1"}))


def test_escort_requires_destination_and_actor():
    assert validate(step(action="escort_to", target_ref="actor", target_id="person-1"))


@pytest.mark.parametrize("order", [("actor", "destination"), ("destination", "actor")])
def test_escort_navigation_destination_not_dict_first(order):
    refs = {role: role for role in order}
    plan = TaskPlan(status="valid", task_chain=[step(action="escort_to", target_ref=None,
                                                   target_id=None, role_refs=refs)])
    task = {"references": {"actor": {"node_id": "person-1"}, "destination": {"node_id": "room-1"}}}
    planner._resolve_target_refs(plan, task)
    assert plan.task_chain[0].target_id == "room-1"
    assert not planner._validate_grounded_plan(plan, grounding(), task)

@pytest.mark.parametrize("mode", ["success", "bad_json", "repair", "raw_id"])
def test_pipeline_keeps_actual_draft_and_attempt_provenance(monkeypatch, mode):
    monkeypatch.setattr(planner, "load_config", lambda *args: {})
    monkeypatch.setattr(planner, "parse_intent_with_llm", lambda *args, **kwargs:
                        TaskIntent(action="navigate", target=EntityReference(text="object")))
    monkeypatch.setattr(planner, "SceneGraphRAG", lambda *args:
                        SimpleNamespace(get_navigation_target=lambda node: None))
    monkeypatch.setattr(planner, "GroundingOrchestrator",
                        lambda *args: SimpleNamespace(ground_intent=lambda intent: grounding()))
    calls = []
    def chat(**kwargs):
        calls.append(True)
        if mode == "bad_json":
            content = "invalid-json-fixture"
        else:
            executor = "none" if mode == "repair" and len(calls) == 1 else "nav2"
            raw_step = {
                "step": 1, "action": "navigate_to", "target_type": "object", "target_ref": "target",
                "target_id": "object-1", "description": "go", "executor": executor}
            if mode == "raw_id" and len(calls) == 1:
                raw_step["target_id"] = "UNTRUSTED_RAW_ID"
            content = json.dumps({"status": "valid", "task_chain": [raw_step]})
        return SimpleNamespace(message=SimpleNamespace(content=content))
    import ollama
    monkeypatch.setattr(ollama, "Client", lambda **kwargs: SimpleNamespace(chat=chat))
    result = planner.run_structured_task_planner("synthetic", "fake", "not-used")
    assert len(result.planning_attempts) == len(calls)
    if mode == "bad_json":
        assert result.status == "system_error"
        assert result.raw_plan_draft["_raw_text"] == "invalid-json-fixture"
        assert result.planning_attempts[0]["error"]
    else:
        assert result.status == "valid"
        if mode == "raw_id":
            assert result.raw_plan_draft["task_chain"][0]["target_id"] == "UNTRUSTED_RAW_ID"
            assert result.planning_attempts[0]["error"]
            assert result.planning_attempts[1]["raw"]["task_chain"][0]["target_id"] == "object-1"
        else:
            assert result.raw_plan_draft["task_chain"][0]["target_id"] == "object-1"
        assert result.task_plan["task_chain"][0]["target_id"] == "object-1"
        if mode in ("repair", "raw_id"):
            assert len(result.planning_attempts) == 2
            assert result.planning_attempts[1]["phase"] == "repair"


def test_unresolved_role_is_not_copied_as_node_id():
    plan = TaskPlan(status="valid", task_chain=[
        step(action="escort_to", target_ref=None, target_id=None,
             role_refs={"actor": "missing", "destination": "destination"})])
    planner._resolve_target_refs(plan, {"references": {"destination": {"node_id": "room-1"}}})
    assert "actor" not in plan.task_chain[0].role_ids
    assert planner._validate_grounded_plan(plan, grounding())


def test_step_actor_cannot_bypass_navigation_role_restriction():
    task = {"steps": [{"references": {"actor": {"node_id": "person-1"}}}]}
    assert validate(step(target_ref="step_0_actor", target_id="person-1"), task=task)


def test_constraint_ref_key_survives_filtering():
    plan = TaskPlan(status="valid", task_chain=[step(constraints=Constraints(prefer=["constraint_prefer_1"]))])
    task = {"references": {"target": {"node_id": "object-1"}},
            "constraints": [{"key": "constraint_prefer_1", "type": "prefer", "node_id": "room-1"}]}
    planner._resolve_target_refs(plan, task)
    assert plan.task_chain[0].constraints.prefer == ["room-1"]


def test_grounded_task_preserves_constraint_key_and_condition():
    result = grounding()
    result.add_constraint_result("constraint_prefer_1", ResolutionResult(
        reference=EntityReference(text="room"), status="resolved",
        selected=ResolutionCandidate(node_id="room-1", node_type="room", label="room")))
    result.condition_constraints = [{"key": "constraint_condition_2", "type": "condition",
                                     "description": "patient_ready"}]
    task = planner._build_grounded_task(TaskIntent(action="navigate"), result,
                                       SimpleNamespace(get_navigation_target=lambda node: None))
    assert task["constraints"][0]["key"] == "constraint_prefer_1"
    assert any(c.get("description") == "patient_ready" for c in task["constraints"])


def test_missing_required_condition_rejected():
    task = {"constraints": [{"type": "condition", "description": "patient_ready"}]}
    assert validate(step(), task=task)
    assert not validate(step(constraints=Constraints(conditions=["patient_ready"])), task=task)


@pytest.mark.parametrize("node_type,expected", [("place", "area"), ("places", "area"),
    ("agent", "person"), ("agents", "person"), ("rooms", "room"), ("objects", "object")])
def test_simple_compiler_maps_canonical_types(node_type, expected):
    draft = SimplePlanDraft(status="valid", steps=[{"action": "navigate_to", "ref": "target"}])
    plan = compile_simple_plan(draft, {"references": {"target": {
        "node_id": "object-1", "node_type": node_type}}})
    assert plan.task_chain[0].target_type == expected


def test_simple_compiler_ref_must_exist():
    plan = compile_simple_plan(
        SimplePlanDraft(status="valid", steps=[{"action": "navigate_to", "ref": "target"}]),
        {"references": {}})
    assert plan.task_chain[0].target_id is None
    assert planner._validate_grounded_plan(plan, GroundingResult())


def test_repair_preserves_role_refs(monkeypatch):
    repaired_draft = {"status": "valid", "task_chain": [{
        "step": 1, "action": "escort_to", "target_type": "person", "target_ref": "",
        "role_refs": {"actor": "actor", "destination": "destination"},
        "target_id": "room-1", "description": "escort", "executor": "nav2"}]}
    import ollama
    monkeypatch.setattr(ollama, "Client", lambda **kwargs: SimpleNamespace(
        chat=lambda **kwargs: SimpleNamespace(message=SimpleNamespace(content=json.dumps(repaired_draft)))))
    plan = planner._repair_plan(TaskPlan(status="valid", task_chain=[]), ["fixture"],
                                {"references": {"actor": {"node_id": "person-1"},
                                                "destination": {"node_id": "room-1"}}}, "fake", {})
    assert plan.task_chain[0].role_refs == {"actor": "actor", "destination": "destination"}

def test_set_reference_survives_builder_and_expands_without_dropping_members():
    result = GroundingResult()
    candidates = [ResolutionCandidate(node_id=node, node_type="object", label="chair")
                  for node in ("chair-1", "chair-2")]
    result.add_result("target", ResolutionResult(status="resolved_set",
        reference=EntityReference(text="chairs", quantifier="all"), selected=candidates[0],
        selected_many=candidates))
    task = planner._build_grounded_task(
        TaskIntent(action="locate", target=EntityReference(text="chairs", quantifier="all")),
        result, SimpleNamespace(get_navigation_target=lambda node: None))
    assert task["references"]["target"]["node_ids"] == ["chair-1", "chair-2"]
    plan = TaskPlan(status="valid", task_chain=[
        step(action="locate_entity", executor="scene_graph", target_id=None)])
    planner._resolve_target_refs(plan, task)
    assert [s.target_id for s in plan.task_chain] == ["chair-1", "chair-2"]
    assert [s.step for s in plan.task_chain] == [1, 2]
    assert not planner._validate_grounded_plan(plan, result, task)


def test_set_hard_avoid_preserves_every_entity():
    result = grounding()
    candidates = [ResolutionCandidate(node_id=node, node_type="room", label="room")
                  for node in ("avoid-1", "avoid-2")]
    result.add_constraint_result("constraint_avoid_0", ResolutionResult(
        status="resolved_set", reference=EntityReference(text="rooms", quantifier="all"),
        selected=candidates[0], selected_many=candidates))
    task = planner._build_grounded_task(TaskIntent(action="navigate"), result,
                                       SimpleNamespace(get_navigation_target=lambda node: None))
    plan = TaskPlan(status="valid", task_chain=[
        step(constraints=Constraints(avoid=["constraint_avoid_0"]))])
    planner._resolve_target_refs(plan, task)
    assert plan.task_chain[0].constraints.avoid == ["avoid-1", "avoid-2"]
    assert not planner._validate_grounded_plan(plan, result, task)


@pytest.mark.parametrize("failure", ["grounding", "navigation_target"])
def test_stage_errors_return_pipeline_result(monkeypatch, failure):
    monkeypatch.setattr(planner, "load_config", lambda *args: {})
    intent = TaskIntent(action="navigate", target=EntityReference(text="object"))
    monkeypatch.setattr(planner, "parse_intent_with_llm", lambda *args, **kwargs: intent)
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic boundary failure")
    rag = SimpleNamespace(get_navigation_target=fail)
    monkeypatch.setattr(planner, "SceneGraphRAG", fail if failure == "grounding" else lambda *args: rag)
    monkeypatch.setattr(planner, "GroundingOrchestrator",
                        lambda *args: SimpleNamespace(ground_intent=lambda intent: grounding()))
    result = planner.run_structured_task_planner("synthetic", "fake", "not-used")
    assert isinstance(result, PipelineResult)
    assert result.status == "system_error"
    assert "synthetic boundary failure" in result.error


def test_grounding_system_error_never_calls_planner(monkeypatch):
    monkeypatch.setattr(planner, "load_config", lambda *args: {})
    monkeypatch.setattr(planner, "parse_intent_with_llm",
                        lambda *args, **kwargs: TaskIntent(action="navigate"))
    result = GroundingResult()
    result.status = "system_error"
    result.errors = ["synthetic grounding error"]
    monkeypatch.setattr(planner, "SceneGraphRAG", lambda *args: None)
    monkeypatch.setattr(planner, "GroundingOrchestrator",
                        lambda *args: SimpleNamespace(ground_intent=lambda intent: result))
    monkeypatch.setattr(planner, "_generate_grounded_plan",
                        lambda *args: pytest.fail("Must not generate after a grounding system_error"))
    assert planner.run_structured_task_planner("synthetic", "fake", "not-used").status == "system_error"

@pytest.mark.parametrize("action,executor", [("navigate_to", "none"), ("navigate_to", "nav2")])
def test_legacy_missing_target_never_validates(action, executor):
    errors = planner.validate_task_plan_semantics(
        TaskPlan(status="valid", task_chain=[step(action=action, executor=executor, target_id=None)]),
        {"entities": []}, "go to chair")
    assert errors


def test_legacy_executor_mapping_is_enforced():
    errors = planner.validate_task_plan_semantics(
        TaskPlan(status="valid", task_chain=[step(executor="none")]),
        {"entities": [{"id": "object-1", "type": "object"}]}, "go to chair")
    assert errors


def test_orphan_role_ids_are_rejected():
    assert validate(step(role_ids={"actor": "person-1"}))


@pytest.mark.parametrize("action", ["escort_to", "guide_to"])
def test_multi_role_precedence_ignores_stale_target_ref(action):
    plan = TaskPlan(status="valid", task_chain=[step(action=action, target_ref="actor",
        target_id=None, role_refs={"actor": "actor", "destination": "destination"})])
    task = {"references": {"actor": {"node_id": "person-1"}, "destination": {"node_id": "room-1"}}}
    planner._resolve_target_refs(plan, task)
    assert not planner._validate_grounded_plan(plan, grounding(), task)
