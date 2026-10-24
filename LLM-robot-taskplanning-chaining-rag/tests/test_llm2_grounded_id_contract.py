"""LLM2 must plan with IDs grounded by RAG, without changing upstream stages."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm.plan_contract import (  # noqa: E402
    PlanDraftContractError,
    grounded_plan_schema,
    parse_grounded_plan_draft,
)


def grounded_task():
    return {
        "action": "navigate",
        "requested_roles": ["target"],
        "references": {
            "target": {
                "node_id": "hospital-object-17",
                "node_type": "object",
                "label": "linen trolley",
                "room_id": "hospital-room-4",
                "place_id": "hospital-place-22",
                "navigation_target": {
                    "navigation_node_id": "nav-place-22",
                    "position": [4.0, 2.0, 0.0],
                    "frame_id": "map",
                },
            },
            "destination": {
                "node_id": "hospital-room-4",
                "node_type": "room",
                "label": "storage room",
            },
        },
        "steps": [],
        "constraints": [],
    }


def draft(**step_changes):
    step = {
        "step": 1,
        "action": "navigate_to",
        "target_type": "object",
        "target_ref": "target",
        "target_id": "hospital-object-17",
        "description": "Navigate to the linen trolley",
        "executor": "nav2",
        "constraints": {"avoid": [], "prefer": [], "conditions": []},
    }
    step.update(step_changes)
    return {"status": "valid", "task_chain": [step]}


def test_grounded_schema_exposes_only_role_valid_ids():
    schema = grounded_plan_schema(grounded_task())
    step = schema["$defs"]["PlanDraftStep"]

    assert step["properties"]["target_id"]["anyOf"][0]["enum"] == [
        "hospital-object-17", "hospital-room-4"
    ]
    assert {"target_id", "candidate_node_ids", "selection_policy"}.issubset(
        step["required"]
    )
    assert step["properties"]["target_ref"]["enum"] == ["", "destination", "target"]


def test_grounded_schema_binds_target_id_to_selected_target_ref():
    schema = grounded_plan_schema(grounded_task())
    validator = Draft202012Validator(schema)
    wrong_reference = draft(target_ref="destination", target_id="hospital-object-17",
                             candidate_node_ids=[], selection_policy=None)

    assert not validator.is_valid(wrong_reference)
    conflicting_roles = draft(
        role_refs={"destination": "destination"},
        candidate_node_ids=[], selection_policy=None,
    )
    assert not validator.is_valid(conflicting_roles)
    assert validator.is_valid(draft(candidate_node_ids=[], selection_policy=None))


def test_grounded_schema_binds_candidate_set_to_selected_target_ref():
    task = grounded_task()
    task["references"]["target"] = {
        "node_type": "object", "candidate_node_ids": ["trolley-a", "trolley-b"]
    }
    schema = grounded_plan_schema(task)
    validator = Draft202012Validator(schema)
    wrong_reference = draft(
        target_ref="destination", target_id=None,
        candidate_node_ids=["trolley-a", "trolley-b"],
        selection_policy="deferred_to_nav2",
    )


    assert not validator.is_valid(wrong_reference)
    correct_reference = draft(target_ref="target", target_id=None,
                              candidate_node_ids=["trolley-b", "trolley-a"],
                              selection_policy="deferred_to_nav2")
    assert validator.is_valid(correct_reference)



def test_grounded_schema_binds_multirole_target_id_to_primary_role():
    schema = grounded_plan_schema(grounded_task())
    validator = Draft202012Validator(schema)
    wrong_primary_role = draft(
        action="escort_to", target_type="person", target_ref="",
        role_refs={"actor": "target", "destination": "destination"},
        target_id="hospital-object-17", candidate_node_ids=[], selection_policy=None,
    )
    correct_primary_role = draft(
        action="escort_to", target_type="person", target_ref="",
        role_refs={"actor": "target", "destination": "destination"},
        target_id="hospital-room-4", candidate_node_ids=[], selection_policy=None,
    )

    assert not validator.is_valid(wrong_primary_role)
    assert validator.is_valid(correct_primary_role)


def test_grounded_schema_uses_empty_array_constraint_when_no_candidate_ids():
    schema = grounded_plan_schema(grounded_task())
    candidate_ids = schema["$defs"]["PlanDraftStep"]["properties"]["candidate_node_ids"]

    assert candidate_ids["maxItems"] == 0
    assert "enum" not in candidate_ids["items"]


def test_llm2_accepts_grounded_node_id_in_plan_draft():
    plan = parse_grounded_plan_draft(json.dumps(draft()), grounded_task())

    assert plan.task_chain[0].target_id == "hospital-object-17"


def test_llm2_rejects_node_id_not_returned_by_rag():
    with pytest.raises(PlanDraftContractError, match="target_id"):
        parse_grounded_plan_draft(
            json.dumps(draft(target_id="invented-room-999")), grounded_task()
        )


def test_stale_deferred_policy_is_cleared_only_for_exact_grounded_target():
    raw = draft(candidate_node_ids=[], selection_policy="deferred_to_nav2")

    plan = parse_grounded_plan_draft(json.dumps(raw), grounded_task())

    assert plan.task_chain[0].target_id == "hospital-object-17"
    assert plan.task_chain[0].candidate_node_ids == []
    assert plan.task_chain[0].selection_policy is None


def test_ambiguous_target_must_preserve_exact_grounded_candidate_set():
    task = grounded_task()
    task["references"]["target"] = {
        "node_type": "object",
        "label": "linen trolley",
        "candidate_node_ids": ["trolley-a", "trolley-b"],
        "selection_policy": "deferred_to_nav2",
    }
    candidate_draft = draft(
        target_id=None,
        candidate_node_ids=["trolley-a", "trolley-b"],
        selection_policy="deferred_to_nav2",
    )

    plan = parse_grounded_plan_draft(json.dumps(candidate_draft), task)

    assert plan.task_chain[0].target_id is None
    assert plan.task_chain[0].candidate_node_ids == ["trolley-a", "trolley-b"]
    assert plan.task_chain[0].selection_policy == "deferred_to_nav2"


def test_candidate_draft_cannot_drop_or_invent_candidates():
    task = grounded_task()
    task["references"]["target"] = {
        "node_type": "object",
        "label": "linen trolley",
        "candidate_node_ids": ["trolley-a", "trolley-b"],
        "selection_policy": "deferred_to_nav2",
    }
    bad = draft(target_id=None, candidate_node_ids=["trolley-a", "invented"])

    with pytest.raises(PlanDraftContractError, match="candidate"):
        parse_grounded_plan_draft(json.dumps(bad), task)


def test_fixed_prompt_example_is_explicitly_synthetic_and_id_bounded():
    from llm.plan_contract import FORMAT_ONLY_SYNTHETIC_EXAMPLE

    assert "FORMAT-ONLY SYNTHETIC EXAMPLE" in FORMAT_ONLY_SYNTHETIC_EXAMPLE
    assert "never copy example ids" in FORMAT_ONLY_SYNTHETIC_EXAMPLE.lower()
    assert "demo-area-LS-03" in FORMAT_ONLY_SYNTHETIC_EXAMPLE
    assert "demo-area-SL-08" in FORMAT_ONLY_SYNTHETIC_EXAMPLE
    assert "target_id" in FORMAT_ONLY_SYNTHETIC_EXAMPLE


def test_synthetic_examples_have_all_required_step_fields():
    from llm.plan_contract import FORMAT_ONLY_SYNTHETIC_EXAMPLE

    decoder = json.JSONDecoder()
    unique_text = FORMAT_ONLY_SYNTHETIC_EXAMPLE.split("Output pattern:", 1)[1].lstrip()
    unique, _ = decoder.raw_decode(unique_text)
    ambiguous_text = FORMAT_ONLY_SYNTHETIC_EXAMPLE.split("Neither candidate is selected here.", 1)[1].lstrip()
    ambiguous, _ = decoder.raw_decode(ambiguous_text)
    required = {"step", "action", "target_type", "target_ref", "target_id",
                "candidate_node_ids", "selection_policy", "description", "executor", "constraints"}
    assert all(required.issubset(item) for item in unique["task_chain"] + ambiguous["task_chain"])
