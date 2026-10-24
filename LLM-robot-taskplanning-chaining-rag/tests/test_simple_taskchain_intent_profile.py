from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llm.intent_parser import parse_intent_with_llm
from planner_schemas.taskchain_intent import SimpleTaskChainIntent


def test_simple_profile_converts_ordered_destination_object_terms(monkeypatch):
    import ollama

    payload = {
        "action": "navigate",
        "stops": [
            {"order": 1, "destination": "room", "objects": ["bookcase", "aquarium"]},
            {"order": 2, "destination": "atrium", "objects": []},
        ],
    }
    requests = []

    def chat(self, **kwargs):
        requests.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload), thinking=None))

    monkeypatch.setattr(ollama.Client, "chat", chat)
    attempts = []
    intent = parse_intent_with_llm(
        "Take me to a room with a bookcase and an aquarium, then to the atrium.",
        model="synthetic",
        config={"ollama_host": "http://127.0.0.1:11434", "intent_prompt_profile": "hospital_taskchain_simple_v1"},
        scene_ontology=["bookcase", "aquarium"],
        attempt_callback=lambda rows: attempts.__setitem__(slice(None), rows),
    )

    assert intent.action == "navigate"
    assert intent.steps is not None and len(intent.steps) == 2
    first = intent.steps[0].target
    assert first is not None and first.text == "room" and first.node_type == "room"
    assert [(r.relation, r.target_text, r.target_node_type) for r in first.relations] == [
        ("contains", "bookcase", "object"),
        ("contains", "aquarium", "object"),
    ]
    assert intent.steps[1].target is not None
    assert intent.steps[1].target.text == "atrium"
    assert intent.steps[1].target.relations == []

    sent_format = requests[0]["format"]
    assert "stops" in sent_format["properties"]
    assert "target" not in sent_format["properties"]
    assert "ordered stops" in requests[0]["messages"][0]["content"]
    assert attempts[0]["raw_lmm_output"] == payload
    assert any(row.get("action") == "adapt_simple_taskchain_payload" for row in attempts[0]["intent_normalizations"])


def test_simple_profile_rejects_noncontiguous_stop_order_and_extra_fields():
    with pytest.raises(ValueError):
        SimpleTaskChainIntent.model_validate({
            "action": "navigate",
            "stops": [
                {"order": 1, "destination": "room", "objects": ["bed"]},
                {"order": 3, "destination": "lobby", "objects": []},
            ],
        })
    with pytest.raises(ValueError):
        SimpleTaskChainIntent.model_validate({
            "action": "navigate", "node_ids": ["123"],
            "stops": [{"order": 1, "destination": "room", "objects": ["bed"]}],
        })


def test_simple_profile_does_not_run_conflicting_locate_action_heuristic(monkeypatch):
    import ollama

    payload = {
        "action": "navigate",
        "stops": [{"order": 1, "destination": "room", "objects": ["blue widget"]}],
    }
    calls = []

    def chat(self, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload), thinking=None))

    monkeypatch.setattr(ollama.Client, "chat", chat)
    attempts = []
    intent = parse_intent_with_llm(
        "Find a room with a blue widget.",
        model="synthetic",
        config={"ollama_host": "http://127.0.0.1:11434", "intent_prompt_profile": "hospital_taskchain_simple_v1"},
        attempt_callback=lambda rows: attempts.__setitem__(slice(None), rows),
    )

    assert intent.action == "navigate"
    assert len(calls) == 1
    assert attempts[0]["instruction_sequence_check"]["status"] == "unverified"


def test_simple_profile_preserves_unresolved_context_as_clarification():
    payload = SimpleTaskChainIntent.model_validate({
        "action": "ask_clarification",
        "stops": [],
        "clarification": "Which room do you mean by the one you visited earlier?",
    })
    from llm.intent_parser import simple_taskchain_to_task_intent

    intent = simple_taskchain_to_task_intent(payload)

    assert intent.action == "ask_clarification"
    assert intent.steps is None
    assert intent.target is None
    assert intent.description == payload.clarification


def test_simple_profile_examples_do_not_reuse_a_benchmark_instruction():
    from llm.intent_parser import HOSPITAL_TASKCHAIN_SIMPLE_SYSTEM_PROMPT

    assert "Take me back to the room I visited earlier." not in HOSPITAL_TASKCHAIN_SIMPLE_SYSTEM_PROMPT
    assert "blue widget" in HOSPITAL_TASKCHAIN_SIMPLE_SYSTEM_PROMPT
    assert "garden" in HOSPITAL_TASKCHAIN_SIMPLE_SYSTEM_PROMPT


def test_structured_planner_short_circuits_explicit_clarification(monkeypatch):
    import main_rag
    from planner_schemas.intent import TaskIntent

    seen_config = {}

    class FakeRag:
        def get_label_ontology(self):
            return ["bed"]

    def fake_parse(command, model, config, **kwargs):
        seen_config.update(config)
        return TaskIntent(
            action="ask_clarification",
            description="Which room do you mean?",
        )

    monkeypatch.setattr(main_rag, "load_config", lambda path: {"bounded_repair_enabled": True})
    monkeypatch.setattr(main_rag, "get_default_model", lambda config: "synthetic")
    monkeypatch.setattr(main_rag, "SceneGraphRAG", lambda db_dir: FakeRag())
    monkeypatch.setattr(main_rag, "parse_intent_with_llm", fake_parse)

    result = main_rag.run_structured_task_planner(
        "Take me back to the room I visited earlier.",
        model_name="synthetic",
        db_dir="/unused",
        intent_prompt_profile="hospital_taskchain_simple_v1",
    )

    assert seen_config["intent_prompt_profile"] == "hospital_taskchain_simple_v1"
    assert result.status == "ambiguous"
    assert result.task_plan is None
    assert result.grounding is None


def test_structured_pipeline_stage_latencies_use_monotonic_clock(monkeypatch):
    import time
    import main_rag
    from grounding.orchestrator import GroundingResult
    from planner_schemas.intent import EntityReference, TaskIntent

    class FakeRag:
        def get_label_ontology(self):
            return ["bed"]

    class FakeGrounder:
        def ground_intent(self, _intent):
            result = GroundingResult()
            result.status = "not_found"
            result.errors = ["synthetic missing object"]
            return result

    wall_clock = iter([100.0, 101.0, 102.0, 103.0, 104.0, 90.0])
    monotonic_clock = iter([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    monkeypatch.setattr(time, "time", lambda: next(wall_clock, 90.0))
    monkeypatch.setattr(time, "perf_counter", lambda: next(monotonic_clock))
    monkeypatch.setattr(main_rag, "load_config", lambda _path: {"bounded_repair_enabled": True})
    monkeypatch.setattr(main_rag, "get_default_model", lambda _config: "synthetic")
    monkeypatch.setattr(main_rag, "SceneGraphRAG", lambda _path: FakeRag())
    monkeypatch.setattr(main_rag, "GroundingOrchestrator", lambda _rag: FakeGrounder())
    monkeypatch.setattr(
        main_rag,
        "parse_intent_with_llm",
        lambda *_args, **_kwargs: TaskIntent(
            action="navigate",
            target=EntityReference(text="bed"),
            destination=EntityReference(text="room"),
        ),
    )

    result = main_rag.run_structured_task_planner("Go to a room with a bed.", db_dir="unused")

    assert result.status == "target_not_found"
    assert result.latency_ms == {
        "ontology_ms": 1000.0,
        "intent_ms": 1000.0,
        "grounding_ms": 1000.0,
    }
