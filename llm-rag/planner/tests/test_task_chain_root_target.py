"""Intent-plan contract tests for mixed top-level and ordered-step references."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main_rag
from planner_schemas.intent import TaskIntent
from llm import intent_contract


def test_unrepresented_top_level_target_is_required_before_nested_steps():
    grounded_task = {
        "action": "navigate",
        "requested_roles": ["target"],
        "references": {"target": {"node_id": "room-first", "node_type": "room"}},
        "constraints": [],
        "steps": [{
            "action": "navigate",
            "requested_roles": ["target"],
            "references": {"target": {"node_id": "room-second", "node_type": "room"}},
            "constraints": [],
        }],
    }

    contract = main_rag._intent_plan_contract(grounded_task)

    assert [(item["ref"], item["node_ids"]) for item in contract] == [
        ("target", ["room-first"]),
        ("step_0_target", ["room-second"]),
    ]


def test_top_level_target_already_in_steps_is_not_added_twice():
    grounded_task = {
        "action": "navigate",
        "requested_roles": ["target"],
        "references": {"target": {"node_id": "room-first", "node_type": "room"}},
        "constraints": [],
        "steps": [{
            "action": "navigate",
            "requested_roles": ["target"],
            "references": {"target": {"node_id": "room-first", "node_type": "room"}},
            "constraints": [],
        }, {
            "action": "navigate",
            "requested_roles": ["target"],
            "references": {"target": {"node_id": "room-second", "node_type": "room"}},
            "constraints": [],
        }],
    }

    contract = main_rag._intent_plan_contract(grounded_task)

    assert [(item["ref"], item["node_ids"]) for item in contract] == [
        ("step_0_target", ["room-first"]),
        ("step_1_target", ["room-second"]),
    ]


def test_unrepresented_primary_target_is_lifted_before_tail_steps():
    intent = TaskIntent.model_validate({
        "action": "navigate",
        "target": {"text": "room with elevator and sofa", "label": "room",
                   "node_type": "room"},
        "steps": [{"action": "navigate", "target": {
            "text": "room with medical screen", "label": "room", "node_type": "room"}}],
    })

    normalize = getattr(intent_contract, "lift_primary_target_into_steps", None)
    assert callable(normalize)
    normalization = normalize(intent)

    assert intent.target is None
    assert [step.target.text for step in intent.steps] == [
        "room with elevator and sofa", "room with medical screen",
    ]
    assert normalization == {
        "action": "lift_primary_target_into_step_zero",
        "text": "room with elevator and sofa",
        "label": "room",
        "node_type": "room",
    }


def test_primary_target_already_in_steps_is_not_lifted():
    intent = TaskIntent.model_validate({
        "action": "navigate",
        "target": {"text": "room with bed", "label": "room", "node_type": "room"},
        "steps": [{"action": "navigate", "target": {
            "text": "room with bed", "label": "room", "node_type": "room"}},
            {"action": "navigate", "target": {
                "text": "room with chair", "label": "room", "node_type": "room"}}],
    })

    normalize = getattr(intent_contract, "lift_primary_target_into_steps", None)
    assert callable(normalize)
    assert normalize(intent) is None
    assert [step.target.text for step in intent.steps] == [
        "room with bed", "room with chair",
    ]
