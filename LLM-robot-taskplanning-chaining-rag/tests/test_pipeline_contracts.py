"""Pipeline contract tests for LLM Task Planning + Khronos 3DSG RAG.

These tests verify deterministic behavior without requiring Ollama or ChromaDB.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Add RAG src to path for canonical_index and api_schemas
RAG_SRC = Path(__file__).parent.parent.parent / "RAG" / "khronos_3dsg_rag" / "src"
sys.path.insert(0, str(RAG_SRC))

from planner_schemas.intent import EntityReference, TaskIntent, TaskConstraint


def test_parent_child_direction():
    """Test that parent/child relationships are correctly oriented."""
    # This test verifies the canonical index direction
    # room_1 -> place_1 -> chair_1
    # parents[chair_1] should be [place_1]
    # children[place_1] should be [chair_1]

    from canonical_index import CanonicalGraphIndex

    index = CanonicalGraphIndex()

    # Manually set up a test hierarchy
    from api_schemas import SceneEntity

    index.nodes["room_1"] = SceneEntity(
        node_id="room_1",
        node_type="rooms",
        label="room",
    )
    index.nodes["place_1"] = SceneEntity(
        node_id="place_1",
        node_type="places",
        label="place",
    )
    index.nodes["chair_1"] = SceneEntity(
        node_id="chair_1",
        node_type="objects",
        label="chair",
    )

    # Set up relationships
    index.parents["place_1"] = ["room_1"]
    index.parents["chair_1"] = ["place_1"]
    index.children["room_1"] = ["place_1"]
    index.children["place_1"] = ["chair_1"]

    # Test
    assert index.get_parents("chair_1") == [index.nodes["place_1"]]
    assert index.get_children("place_1") == [index.nodes["chair_1"]]

    print("✓ test_parent_child_direction passed")


def test_no_arbitrary_id_fallback():
    """Test that arbitrary ID fallback is not allowed."""
    from schema import TaskPlan, TaskStep, Constraints

    # Create a task plan with missing target_id
    task_plan = TaskPlan(
        status="valid",
        task_chain=[
            TaskStep(
                step=1,
                action="navigate_to",
                target_type="room",
                target_id=None,  # Missing
                description="Navigate to room",
                executor="nav2",
            )
        ],
    )

    # The new _auto_fill_target_id should NOT fill in an arbitrary ID
    # It should leave target_id as None
    assert task_plan.task_chain[0].target_id is None

    print("✓ test_no_arbitrary_id_fallback passed")


def test_ambiguous_status():
    """Test that ambiguous status is properly handled."""
    from grounding.orchestrator import GroundingResult
    from api_schemas import ResolutionResult, ResolutionCandidate
    from planner_schemas.intent import EntityReference

    # Create a grounding result with ambiguous status
    result = GroundingResult()
    ref = EntityReference(text="chair")
    resolution = ResolutionResult(
        status="ambiguous",
        reference=ref,
        candidates=[
            ResolutionCandidate(node_id="chair_1", node_type="objects", label="chair", score=0.9),
            ResolutionCandidate(node_id="chair_2", node_type="objects", label="chair", score=0.89),
        ],
    )
    result.add_result("target", resolution)

    assert result.status == "ambiguous"

    print("✓ test_ambiguous_status passed")


def test_not_found_status():
    """Test that not_found status is properly handled."""
    from grounding.orchestrator import GroundingResult
    from api_schemas import ResolutionResult
    from planner_schemas.intent import EntityReference

    # Create a grounding result with not_found status
    result = GroundingResult()
    ref = EntityReference(text="refrigerator")
    resolution = ResolutionResult(
        status="not_found",
        reference=ref,
        reason="No nodes found matching 'refrigerator'",
    )
    result.add_result("target", resolution)

    assert result.status == "not_found"

    print("✓ test_not_found_status passed")


def test_intent_schema():
    """Test that TaskIntent schema works correctly."""
    intent = TaskIntent(
        action="navigate",
        target=EntityReference(text="bedroom"),
        destination=EntityReference(text="entrance"),
    )

    assert intent.action == "navigate"
    assert intent.target.text == "bedroom"
    assert intent.destination.text == "entrance"
    assert len(intent.constraints) == 0

    print("✓ test_intent_schema passed")


def test_resolution_result():
    """Test ResolutionResult structure."""
    from api_schemas import ResolutionResult, ResolutionCandidate
    from planner_schemas.intent import EntityReference

    ref = EntityReference(text="chair")
    selected = ResolutionCandidate(
        node_id="chair_1",
        node_type="objects",
        label="chair",
        score=0.95,
        room_id="room_1",
    )

    result = ResolutionResult(
        status="resolved",
        reference=ref,
        selected=selected,
    )

    assert result.status == "resolved"
    assert result.selected.node_id == "chair_1"

    print("✓ test_resolution_result passed")


def test_grounding_result_resolved_ids():
    """Test that get_resolved_ids returns correct IDs."""
    from grounding.orchestrator import GroundingResult
    from api_schemas import ResolutionResult, ResolutionCandidate
    from planner_schemas.intent import EntityReference

    result = GroundingResult()

    # Add resolved reference
    ref1 = EntityReference(text="chair")
    res1 = ResolutionResult(
        status="resolved",
        reference=ref1,
        selected=ResolutionCandidate(node_id="chair_1", node_type="objects", label="chair"),
    )
    result.add_result("target", res1)

    # Add ambiguous reference
    ref2 = EntityReference(text="desk")
    res2 = ResolutionResult(
        status="ambiguous",
        reference=ref2,
        candidates=[
            ResolutionCandidate(node_id="desk_1", node_type="objects", label="desk"),
            ResolutionCandidate(node_id="desk_2", node_type="objects", label="desk"),
        ],
    )
    result.add_result("source", res2)

    resolved_ids = result.get_resolved_ids()
    assert "chair_1" in resolved_ids
    assert "desk_1" not in resolved_ids
    assert "desk_2" not in resolved_ids

    print("✓ test_grounding_result_resolved_ids passed")


def run_all_tests():
    """Run all contract tests."""
    print("Running pipeline contract tests...\n")

    tests = [
        test_parent_child_direction,
        test_no_arbitrary_id_fallback,
        test_ambiguous_status,
        test_not_found_status,
        test_intent_schema,
        test_resolution_result,
        test_grounding_result_resolved_ids,
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
