"""Regressions for language intent -> grounded A-plan contracts (no model IO)."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main_rag as planner
from schema import TaskPlan, TaskStep
from grounding.orchestrator import GroundingResult
from api_schemas import ResolutionCandidate, ResolutionResult
from planner_schemas.intent import EntityReference, TaskIntent


def ref(identifier):
    return {"node_id": identifier, "node_type": "object", "label": "bed"}


def ground(task):
    result = GroundingResult()
    def add(key, data):
        node_ids = data.get("node_ids") or ([data["node_id"]] if data.get("node_id") else [])
        if not node_ids:
            return
        candidates = [ResolutionCandidate(node_id=node_id,
            node_type=data.get("node_type", "object"), label=data.get("label", "bed"))
            for node_id in node_ids]
        result.add_result(key, ResolutionResult(reference=EntityReference(text=key),
            status="resolved_set" if len(candidates) > 1 else "resolved",
            selected=candidates[0], selected_many=candidates if len(candidates) > 1 else []))
    for role, data in task.get("references", {}).items():
        add(role, data)
    for index, item in enumerate(task.get("steps", [])):
        for role, data in item.get("references", {}).items():
            add(f"step_{index}_{role}", data)
    return result


def step(action="locate_entity", identifier="object-1", reference="target", number=1):
    return TaskStep(step=number, action=action, target_type="object", target_ref=reference,
        target_id=identifier, executor="scene_graph" if action == "locate_entity" else "nav2",
        description="fixture")


def errors(chain, task):
    return planner._validate_grounded_plan(TaskPlan(status="valid", task_chain=chain), ground(task), task)


def test_locate_cannot_silently_become_navigation():
    task = {"action": "locate", "references": {"target": ref("object-1")}}
    assert errors([step("navigate_to")], task)
    assert not errors([step()], task)


@pytest.mark.parametrize("mode", ["missing", "extra", "reversed", "wrong_scope"])
def test_ordered_intent_coverage(mode):
    task = {"action": "locate", "references": {"target": ref("object-1")}, "steps": [
        {"action": "locate", "references": {"target": ref("object-1")}},
        {"action": "navigate", "references": {"target": ref("object-2")}}]}
    chain = [step(reference="step_0_target"),
             step("navigate_to", "object-2", "step_1_target", 2)]
    if mode == "missing":
        chain.pop()
    elif mode == "extra":
        chain.append(step("navigate_to", "object-1", "target", 3))
    elif mode == "reversed":
        chain.reverse()
        for i, item in enumerate(chain, 1):
            item.step = i
    else:
        # Canonical identity is the same, but the wrong requested step is referenced.
        task["steps"][1]["references"]["target"] = ref("object-1")
        chain[1].target_ref = "step_0_target"
        chain[1].target_id = "object-1"
    assert errors(chain, task)


def test_explicitly_repeated_task_is_not_an_accidental_duplicate():
    task = {"action": "locate", "references": {"target": ref("object-1")}, "steps": [
        {"action": "locate", "references": {"target": ref("object-1")}},
        {"action": "locate", "references": {"target": ref("object-1")}}]}
    chain = [step(reference="step_0_target"), step(reference="step_1_target", number=2)]
    assert not errors(chain, task)


def test_ready_task_cannot_be_changed_to_unsupported():
    task = {"action": "locate", "references": {"target": ref("object-1")}}
    assert planner._validate_grounded_plan(TaskPlan(status="unsupported", task_chain=[]), ground(task), task)


def test_semantic_target_type_must_match_selected_object():
    task = {"action": "locate", "references": {"target": ref("object-1")}}
    item = step()
    item.target_type = "room"
    assert errors([item], task)


def test_set_contract_covers_all_members_without_enforcing_set_order():
    task = {"action": "locate", "references": {"target": {
        "node_ids": ["object-1", "object-2"], "node_type": "object"}}}
    chain = [step(identifier="object-2"), step(identifier="object-1", number=2)]
    result = ground(task)
    assert not planner._validate_grounded_plan(TaskPlan(status="valid", task_chain=chain), result, task)
    assert planner._validate_grounded_plan(TaskPlan(status="valid", task_chain=chain[:1]), result, task)


def test_avoid_set_contract_preserves_expanded_constraint_members():
    task = {"action": "avoid", "references": {}, "constraints": [
        {"key": "constraint_avoid_0", "type": "avoid", "node_id": "room-1"},
        {"key": "constraint_avoid_0", "type": "avoid", "node_id": "room-2"}]}
    assert planner._intent_plan_contract(task)[0]["node_ids"] == ["room-1", "room-2"]


@pytest.mark.parametrize("action", ["escort", "guide"])
def test_macro_actor_cannot_be_swapped_between_requested_steps(action):
    task = {"action": action, "references": {}, "steps": [
        {"action": action, "references": {"actor": ref("actor-1"), "destination": ref("room-1")}},
        {"action": action, "references": {"actor": ref("actor-2"), "destination": ref("room-1")}}]}
    chain = [TaskStep(step=i + 1, action=action + "_to", target_type="person", target_id=None,
        role_refs={"actor": f"step_{1-i}_actor", "destination": f"step_{i}_destination"},
        description="macro", executor="nav2") for i in range(2)]
    plan = planner._resolve_target_refs(TaskPlan(status="valid", task_chain=chain), task)
    assert planner._validate_grounded_plan(plan, GroundingResult(), task)


@pytest.mark.parametrize("action,executor", [("wait", "none"), ("ask_clarification", "llm")])
def test_ordered_no_ref_step_accepts_empty_draft_ref(action, executor):
    task = {"action": "locate", "references": {}, "steps": [
        {"action": "locate", "references": {"target": ref("object-1")}},
        {"action": action, "references": {}}]}
    chain = [step(reference="step_0_target"), TaskStep(step=2, action=action,
        target_type="unknown", target_ref="", description="wait", executor=executor)]
    assert not errors(chain, task)


@pytest.mark.parametrize("action", ["escort", "guide"])
def test_existing_macro_locate_preparation_is_preserved(action):
    task = {"action": action, "requested_roles": ["actor", "destination"],
        "references": {"actor": ref("actor-1"), "destination": ref("room-1")}}
    chain = [step(identifier="actor-1", reference="actor"), TaskStep(step=2,
        action=action + "_to", target_type="person", description="macro", executor="nav2",
        role_refs={"actor": "actor", "destination": "destination"})]
    plan = planner._resolve_target_refs(TaskPlan(status="valid", task_chain=chain), task)
    assert not planner._validate_grounded_plan(plan, ground(task), task)


def test_existing_source_to_destination_decomposition_is_preserved():
    task = {"action": "navigate", "requested_roles": ["source", "destination"],
        "references": {"source": ref("room-1"), "destination": ref("room-2")}}
    chain = [step("navigate_to", "room-1", "source"), step("navigate_to", "room-2", "destination", 2)]
    assert not errors(chain, task)


def test_avoid_set_reference_is_valid_after_expansion():
    result = GroundingResult()
    members = [ResolutionCandidate(node_id=id, node_type="room", label="room") for id in ["room-1", "room-2"]]
    result.add_constraint_result("constraint_avoid_0", ResolutionResult(status="resolved_set",
        reference=EntityReference(text="rooms", quantifier="all"), selected=members[0], selected_many=members))
    task = {"action": "avoid", "references": {}, "constraints": [
        {"key": "constraint_avoid_0", "type": "avoid", "node_id": id} for id in ["room-1", "room-2"]]}
    items = [step("avoid_area", node_id, "constraint_avoid_0", index)
             for index, node_id in enumerate(("room-1", "room-2"), 1)]
    for item in items:
        item.constraints.avoid = ["constraint_avoid_0"]
    plan = planner._resolve_target_refs(TaskPlan(status="valid", task_chain=items), task)
    assert not planner._validate_grounded_plan(plan, result, task)


def test_navigation_constraints_cannot_be_applied_to_wrong_segment():
    task = {"action": "navigate", "references": {}, "steps": [
        {"action": "navigate", "references": {"target": ref("object-1")}},
        {"action": "navigate", "references": {"target": ref("object-2")}}], "constraints": [
        {"key": "constraint_avoid_0", "type": "avoid", "node_id": "room-1"},
        {"key": "step_1_constraint_condition_0", "type": "condition", "description": "ready"}]}
    chain = [step("navigate_to", reference="step_0_target"),
             step("navigate_to", "object-2", "step_1_target", 2)]
    chain[0].constraints.conditions = ["ready"]
    chain[1].constraints.avoid = ["room-1"]
    result = ground(task)
    result.add_constraint_result("constraint_avoid_0", ResolutionResult(status="resolved",
        reference=EntityReference(text="room"), selected=ResolutionCandidate(node_id="room-1", node_type="room", label="room")))
    assert planner._validate_grounded_plan(TaskPlan(status="valid", task_chain=chain), result, task)
    chain[0].constraints.avoid = ["room-1"]
    chain[0].constraints.conditions = []
    chain[1].constraints.conditions = ["ready"]
    assert not planner._validate_grounded_plan(TaskPlan(status="valid", task_chain=chain), result, task)


def test_pipeline_passes_live_ontology_without_mutating_loaded_config(monkeypatch):
    config = {"options": {"seed": 42}}
    monkeypatch.setattr(planner, "load_config", lambda *args: config)
    monkeypatch.setattr(planner, "SceneGraphRAG", lambda *args: SimpleNamespace(
        get_label_ontology=lambda: ["bed", "medicalscreen"]))
    seen = []
    def parse(*args, **kwargs):
        seen.append(kwargs["config"])
        return TaskIntent(action="unsupported")
    monkeypatch.setattr(planner, "parse_intent_with_llm", parse)
    result = planner.run_structured_task_planner("Pick up bed", "fake", "unused")
    assert result.status == "unsupported"
    assert seen[0]["scene_ontology"] == ["bed", "medicalscreen"]
    assert "scene_ontology" not in config


@pytest.mark.parametrize("failure", [False, True])
def test_pipeline_keeps_intent_attempt_trace(monkeypatch, failure):
    monkeypatch.setattr(planner, "load_config", lambda *args: {})
    monkeypatch.setattr(planner, "SceneGraphRAG", lambda *args: SimpleNamespace())
    trace = [{"raw_content": "original", "completeness_audit": {"complete": False}}]
    def parse(*args, **kwargs):
        kwargs["attempt_callback"](trace)
        if failure:
            raise ValueError("incomplete")
        return TaskIntent(action="unsupported")
    monkeypatch.setattr(planner, "parse_intent_with_llm", parse)
    result = planner.run_structured_task_planner("Pick up bed", "fake", "unused")
    assert result.model_dump()["intent_parse_attempts"] == trace


def test_planner_prompt_has_semantic_contract_and_grounded_ids_without_coordinates(monkeypatch):
    import ollama
    task = {"action": "locate", "references": {"target": {
        **ref("object-1"), "place_id": "place-999", "position": [0, 0, 0],
        "navigation_target": {"navigation_node_id": "place-999"}}}}
    captured = []
    def chat(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps({"status": "valid", "task_chain": [{
            "step": 1, "action": "locate_entity", "target_type": "object", "target_ref": "target",
            "target_id": "object-1", "description": "locate", "executor": "scene_graph"}]})))
    monkeypatch.setattr(ollama, "Client", lambda **kwargs: SimpleNamespace(chat=chat))
    planner._generate_grounded_plan("Find bed", task, "fake", {})
    prompt = captured[0]["messages"][1]["content"]
    assert "place-999" in prompt
    assert "0, 0, 0" not in prompt
    assert "INTENT CONTRACT" in prompt
    assert "locate_entity" in prompt
    assert task["references"]["target"]["navigation_target"]["navigation_node_id"] == "place-999"


@pytest.mark.parametrize("initial", ["not json", {"status": "valid", "task_chain": [{
    "step": 1, "action": "locate_entity", "target_type": "place", "target_ref": "target",
    "target_id": "object-1", "description": "find bed", "executor": "scene_graph"}]}])
@pytest.mark.parametrize("repair_bad", [False, True])
def test_schema_errors_enter_exactly_one_repair(monkeypatch, initial, repair_bad):
    import ollama
    task = {"action": "locate", "references": {"target": ref("object-1")}}
    monkeypatch.setattr(planner, "load_config", lambda *args: {})
    monkeypatch.setattr(planner, "parse_intent_with_llm", lambda *args, **kwargs:
        TaskIntent(action="locate", target=EntityReference(text="bed")))
    monkeypatch.setattr(planner, "SceneGraphRAG", lambda *args: SimpleNamespace(
        get_navigation_target=lambda node: None))
    monkeypatch.setattr(planner, "GroundingOrchestrator", lambda *args: SimpleNamespace(
        ground_intent=lambda intent: ground(task)))
    calls = []
    good = {"status": "valid", "task_chain": [{"step": 1, "action": "locate_entity",
        "target_type": "object", "target_ref": "target", "target_id": "object-1",
        "description": "find bed", "executor": "scene_graph"}]}
    def chat(**kwargs):
        calls.append(kwargs)
        payload = initial if len(calls) == 1 or repair_bad else good
        return SimpleNamespace(message=SimpleNamespace(content=payload if isinstance(payload, str) else json.dumps(payload)))
    monkeypatch.setattr(ollama, "Client", lambda **kwargs: SimpleNamespace(chat=chat))
    result = planner.run_structured_task_planner("Find bed", "fake", "unused")
    assert result.status == ("system_error" if repair_bad else "valid")
    assert len(calls) == 2
    assert len(result.planning_attempts) == 2
    assert result.planning_attempts[0]["error"]
    assert result.planning_attempts[1]["phase"] == "repair"
    if repair_bad:
        assert result.planning_attempts[1]["error"]
    assert result.raw_plan_draft == (initial if isinstance(initial, dict) else {"_raw_text": initial})


def test_transport_failure_is_not_a_semantic_repair(monkeypatch):
    import ollama
    task = {"action": "locate", "references": {"target": ref("object-1")}}
    monkeypatch.setattr(planner, "load_config", lambda *args: {})
    monkeypatch.setattr(planner, "parse_intent_with_llm", lambda *args, **kwargs:
        TaskIntent(action="locate", target=EntityReference(text="bed")))
    monkeypatch.setattr(planner, "SceneGraphRAG", lambda *args: SimpleNamespace(
        get_navigation_target=lambda node: None))
    monkeypatch.setattr(planner, "GroundingOrchestrator", lambda *args: SimpleNamespace(
        ground_intent=lambda intent: ground(task)))
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        raise ConnectionError("transport fixture")
    monkeypatch.setattr(ollama, "Client", lambda **kwargs: SimpleNamespace(chat=chat))
    result = planner.run_structured_task_planner("Find bed", "fake", "unused")
    assert result.status == "system_error"
    assert len(calls) == 1
    assert len(result.planning_attempts) == 1
