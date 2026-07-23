"""Tests for TaskIntent serialization completeness.

Phase 0.3: Verify intent_to_dict() preserves all schema fields.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from planner_schemas.intent import (
    TaskIntent, EntityReference, RelationConstraint,
    AggregateConstraint, LocationConstraint, IntentStep, TaskConstraint,
)
from llm.intent_parser import intent_to_dict


def test_intent_serialization_preserves_locations():
    """locations field must be serialized correctly."""
    intent = TaskIntent(
        action="navigate",
        target=EntityReference(
            text="chair",
            locations=[
                LocationConstraint(node_id="p1", text="Place A", node_type="place"),
                LocationConstraint(text="Room 201", node_type="room"),
            ],
        ),
    )
    d = intent_to_dict(intent)
    assert d["target"]["locations"] is not None
    assert len(d["target"]["locations"]) == 2
    assert d["target"]["locations"][0]["node_type"] == "place"
    assert d["target"]["locations"][1]["node_type"] == "room"


def test_intent_serialization_preserves_relations():
    """relations with target_node_id must be serialized correctly."""
    intent = TaskIntent(
        action="locate",
        target=EntityReference(
            text="chair",
            relations=[
                RelationConstraint(
                    relation="near",
                    target_text="desk",
                    target_node_id="1234567890123456789",
                    target_label="desks",
                    target_node_type="objects",
                ),
            ],
        ),
    )
    d = intent_to_dict(intent)
    rels = d["target"]["relations"]
    assert len(rels) == 1
    assert rels[0]["relation"] == "near"
    assert rels[0]["target_node_id"] == "1234567890123456789"
    assert rels[0]["target_label"] == "desks"


def test_intent_serialization_preserves_aggregate():
    """aggregate with location must be serialized correctly."""
    intent = TaskIntent(
        action="locate",
        target=EntityReference(
            text="chairs",
            aggregate=AggregateConstraint(
                operation="count_in_location",
                target_labels=["chairs"],
                value=5,
                location=LocationConstraint(text="Room 201", node_type="room"),
            ),
        ),
    )
    d = intent_to_dict(intent)
    agg = d["target"]["aggregate"]
    assert agg is not None
    assert agg["operation"] == "count_in_location"
    assert agg["value"] == 5
    assert agg["location"] is not None
    assert agg["location"]["node_type"] == "room"


def test_intent_serialization_preserves_steps():
    """steps field must be serialized correctly."""
    intent = TaskIntent(
        action="navigate",
        steps=[
            IntentStep(
                action="locate",
                target=EntityReference(text="chair", label="chairs"),
                description="Find the chair",
            ),
            IntentStep(
                action="navigate",
                destination=EntityReference(text="kitchen", node_type="rooms"),
                description="Go to kitchen",
            ),
        ],
    )
    d = intent_to_dict(intent)
    assert d["steps"] is not None
    assert len(d["steps"]) == 2
    assert d["steps"][0]["action"] == "locate"
    assert d["steps"][0]["target"]["label"] == "chairs"
    assert d["steps"][1]["action"] == "navigate"
    assert d["steps"][1]["destination"]["node_type"] == "rooms"


def test_intent_serialization_preserves_constraints():
    """constraints must be serialized correctly."""
    intent = TaskIntent(
        action="navigate",
        target=EntityReference(text="office"),
        constraints=[
            TaskConstraint(type="avoid", description="Avoid stairs", target_text="stairs"),
        ],
    )
    d = intent_to_dict(intent)
    assert len(d["constraints"]) == 1
    assert d["constraints"][0]["type"] == "avoid"
    assert d["constraints"][0]["target_text"] == "stairs"


def test_intent_serialization_preserves_quantifier():
    """quantifier must be serialized correctly."""
    intent = TaskIntent(
        action="locate",
        target=EntityReference(text="chairs", quantifier="all"),
    )
    d = intent_to_dict(intent)
    assert d["target"]["quantifier"] == "all"


def test_intent_serialization_roundtrip():
    """Serialized intent must be re-parseable by Pydantic."""
    intent = TaskIntent(
        action="navigate",
        target=EntityReference(
            text="chair",
            node_type="objects",
            label="chairs",
            locations=[LocationConstraint(text="Place A", node_type="place")],
            relations=[RelationConstraint(relation="near", target_text="desk")],
            quantifier="nearest",
        ),
        source=EntityReference(text="office"),
        destination=EntityReference(text="kitchen"),
        steps=[
            IntentStep(action="locate", target=EntityReference(text="chair")),
        ],
    )
    d = intent_to_dict(intent)
    # Re-parse with Pydantic
    roundtripped = TaskIntent.model_validate(d)
    assert roundtripped.action == "navigate"
    assert roundtripped.target.text == "chair"
    assert len(roundtripped.target.locations) == 1
    assert len(roundtripped.target.relations) == 1
    assert roundtripped.steps is not None
    assert len(roundtripped.steps) == 1
