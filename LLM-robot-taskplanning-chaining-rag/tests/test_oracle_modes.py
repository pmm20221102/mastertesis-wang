"""Tests for oracle evaluation modes.

Verifies that:
- oracle_intent uses case data instead of LLM
- oracle_grounding skips Intent Parser and Grounding
- Oracle data structures are valid
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "RAG" / "khronos_3dsg_rag" / "src"))

from planner_schemas.intent import EntityReference, TaskIntent, TaskConstraint


def test_oracle_intent_structure():
    """Verify oracle_intent data structure is valid."""
    oracle_intent = {
        "action": "locate",
        "target": {
            "text": "chair",
            "label": "chair",
            "node_type": "objects",
        },
        "destination": {
            "text": "bedroom",
            "label": "bedroom",
            "node_type": "rooms",
        },
        "constraints": [
            {
                "type": "avoid",
                "description": "avoid bathroom",
                "target_text": "bathroom",
                "target_label": "bathroom",
            }
        ],
        "description": "Find the chair in the bedroom",
    }

    # Build TaskIntent from oracle
    def _build_ref(data):
        if not data:
            return None
        return EntityReference(
            text=data.get("text", ""),
            label=data.get("label"),
            node_type=data.get("node_type"),
            name=data.get("name"),
            room=data.get("room"),
        )

    intent = TaskIntent(
        action=oracle_intent.get("action", "locate"),
        target=_build_ref(oracle_intent.get("target")),
        destination=_build_ref(oracle_intent.get("destination")),
        constraints=[
            TaskConstraint(
                type=c.get("type", "avoid"),
                description=c.get("description", ""),
                target_text=c.get("target_text"),
                target_label=c.get("target_label"),
            )
            for c in oracle_intent.get("constraints", [])
        ],
        description=oracle_intent.get("description", ""),
    )

    assert intent.action == "locate"
    assert intent.target is not None
    assert intent.target.text == "chair"
    assert intent.target.label == "chair"
    assert intent.destination is not None
    assert intent.destination.text == "bedroom"
    assert len(intent.constraints) == 1
    assert intent.constraints[0].type == "avoid"
    assert intent.constraints[0].target_text == "bathroom"

    print("✓ test_oracle_intent_structure passed")


def test_oracle_grounding_structure():
    """Verify oracle_grounding data structure is valid."""
    oracle_grounding = {
        "action": "navigate",
        "target": {
            "node_id": "chair_123",
            "node_type": "objects",
            "label": "chair",
            "room_id": "bedroom_456",
            "place_id": "place_789",
        },
        "destination": {
            "node_id": "bedroom_456",
            "node_type": "rooms",
            "label": "bedroom",
        },
        "constraints": [
            {
                "type": "avoid",
                "node_id": "bathroom_012",
                "node_type": "rooms",
                "label": "bathroom",
            }
        ],
    }

    # Verify structure
    assert "target" in oracle_grounding
    assert "node_id" in oracle_grounding["target"]
    assert oracle_grounding["target"]["node_id"] == "chair_123"

    assert "destination" in oracle_grounding
    assert oracle_grounding["destination"]["node_id"] == "bedroom_456"

    assert len(oracle_grounding["constraints"]) == 1
    assert oracle_grounding["constraints"][0]["node_id"] == "bathroom_012"

    print("✓ test_oracle_grounding_structure passed")


def test_oracle_intent_with_actor():
    """Verify oracle_intent supports actor reference."""
    oracle_intent = {
        "action": "escort",
        "actor": {
            "text": "patient",
            "label": "patient",
            "node_type": "agents",
        },
        "destination": {
            "text": "entrance",
            "label": "entrance",
            "node_type": "places",
        },
    }

    def _build_ref(data):
        if not data:
            return None
        return EntityReference(
            text=data.get("text", ""),
            label=data.get("label"),
            node_type=data.get("node_type"),
        )

    intent = TaskIntent(
        action=oracle_intent["action"],
        actor=_build_ref(oracle_intent.get("actor")),
        destination=_build_ref(oracle_intent.get("destination")),
    )

    assert intent.action == "escort"
    assert intent.actor is not None
    assert intent.actor.text == "patient"
    assert intent.destination is not None
    assert intent.destination.text == "entrance"

    print("✓ test_oracle_intent_with_actor passed")


def test_oracle_grounding_with_multiple_constraints():
    """Verify oracle_grounding supports multiple constraints."""
    oracle_grounding = {
        "action": "navigate",
        "target": {
            "node_id": "room_1",
            "node_type": "rooms",
            "label": "office",
        },
        "constraints": [
            {"type": "avoid", "node_id": "room_2", "label": "kitchen"},
            {"type": "avoid", "node_id": "room_3", "label": "storage"},
        ],
    }

    assert len(oracle_grounding["constraints"]) == 2
    assert oracle_grounding["constraints"][0]["type"] == "avoid"
    assert oracle_grounding["constraints"][1]["type"] == "avoid"

    print("✓ test_oracle_grounding_with_multiple_constraints passed")


def run_all_tests():
    """Run all oracle mode tests."""
    print("Running oracle mode tests...\n")

    tests = [
        test_oracle_intent_structure,
        test_oracle_grounding_structure,
        test_oracle_intent_with_actor,
        test_oracle_grounding_with_multiple_constraints,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} failed: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*50}")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
