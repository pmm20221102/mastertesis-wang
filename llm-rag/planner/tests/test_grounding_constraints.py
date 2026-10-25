"""Tests for grounding constraint handling.

Verifies that:
- Avoid constraints use constraint IDs (not reference IDs)
- Constraint IDs are properly resolved
- Role-specific validation works
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "RAG" / "khronos_3dsg_rag" / "src"))

from grounding.orchestrator import GroundingResult
from api_schemas import ResolutionResult, ResolutionCandidate
from planner_schemas.intent import EntityReference


def test_constraint_ids_separate_from_ref_ids():
    """Constraint IDs should be tracked separately from reference IDs."""
    result = GroundingResult()

    # Add resolved reference
    ref = EntityReference(text="chair")
    res = ResolutionResult(
        status="resolved",
        reference=ref,
        selected=ResolutionCandidate(
            node_id="chair_1",
            node_type="objects",
            label="chair",
        ),
    )
    result.add_result("target", res)

    # Add constraint
    constr_ref = EntityReference(text="bathroom")
    constr_res = ResolutionResult(
        status="resolved",
        reference=constr_ref,
        selected=ResolutionCandidate(
            node_id="bathroom_1",
            node_type="rooms",
            label="bathroom",
        ),
    )
    result.add_constraint_result("constraint_avoid_0", constr_res)

    # Verify separation
    ref_ids = result.get_resolved_ids()
    constraint_ids = result.get_constraint_ids()
    avoid_ids = result.get_constraint_avoid_ids()

    assert "chair_1" in ref_ids
    assert "bathroom_1" in constraint_ids
    assert "bathroom_1" in avoid_ids
    assert "chair_1" not in constraint_ids
    assert "bathroom_1" not in ref_ids

    print("✓ test_constraint_ids_separate_from_ref_ids passed")


def test_avoid_id_in_plan_is_valid():
    """Avoid IDs from constraints should be valid in plan validation."""
    result = GroundingResult()

    # Add resolved reference
    ref = EntityReference(text="room")
    res = ResolutionResult(
        status="resolved",
        reference=ref,
        selected=ResolutionCandidate(
            node_id="room_1",
            node_type="rooms",
            label="office",
        ),
    )
    result.add_result("target", res)

    # Add avoid constraint
    constr_ref = EntityReference(text="kitchen")
    constr_res = ResolutionResult(
        status="resolved",
        reference=constr_ref,
        selected=ResolutionCandidate(
            node_id="kitchen_1",
            node_type="rooms",
            label="kitchen",
        ),
    )
    result.add_constraint_result("constraint_avoid_0", constr_res)

    # Build allowed set (as validator does)
    allowed_ref_ids = result.get_resolved_ids()
    allowed_constraint_ids = result.get_constraint_ids()
    allowed_all_ids = allowed_ref_ids | allowed_constraint_ids

    # Avoid ID should be in allowed set
    assert "kitchen_1" in allowed_all_ids
    assert "room_1" in allowed_all_ids

    print("✓ test_avoid_id_in_plan_is_valid passed")


def test_role_specific_resolution():
    """Each role should have its own resolved ID."""
    result = GroundingResult()

    # Add actor
    actor_ref = EntityReference(text="patient")
    actor_res = ResolutionResult(
        status="resolved",
        reference=actor_ref,
        selected=ResolutionCandidate(
            node_id="patient_1",
            node_type="agents",
            label="patient",
        ),
    )
    result.add_result("actor", actor_res)

    # Add target
    target_ref = EntityReference(text="room")
    target_res = ResolutionResult(
        status="resolved",
        reference=target_ref,
        selected=ResolutionCandidate(
            node_id="room_1",
            node_type="rooms",
            label="office",
        ),
    )
    result.add_result("target", target_res)

    # Add destination
    dest_ref = EntityReference(text="entrance")
    dest_res = ResolutionResult(
        status="resolved",
        reference=dest_ref,
        selected=ResolutionCandidate(
            node_id="entrance_1",
            node_type="places",
            label="entrance",
        ),
    )
    result.add_result("destination", dest_res)

    # Verify role-specific access
    assert result.get_resolved_id_by_role("actor") == "patient_1"
    assert result.get_resolved_id_by_role("target") == "room_1"
    assert result.get_resolved_id_by_role("destination") == "entrance_1"
    assert result.get_resolved_id_by_role("source") is None

    print("✓ test_role_specific_resolution passed")


def test_ambiguous_result_has_candidates():
    """Ambiguous results should have multiple candidates."""
    result = GroundingResult()

    ref = EntityReference(text="chair")
    res = ResolutionResult(
        status="ambiguous",
        reference=ref,
        candidates=[
            ResolutionCandidate(node_id="chair_1", node_type="objects", label="chair", score=0.9),
            ResolutionCandidate(node_id="chair_2", node_type="objects", label="chair", score=0.89),
        ],
    )
    result.add_result("target", res)

    assert result.status == "ambiguous"
    assert len(result.references["target"].candidates) == 2

    print("✓ test_ambiguous_result_has_candidates passed")


def test_not_found_result_has_reason():
    """Not-found results should have a reason."""
    result = GroundingResult()

    ref = EntityReference(text="refrigerator")
    res = ResolutionResult(
        status="not_found",
        reference=ref,
        reason="No nodes found matching 'refrigerator'",
    )
    result.add_result("target", res)

    assert result.status == "not_found"
    assert result.references["target"].reason is not None

    print("✓ test_not_found_result_has_reason passed")


def test_optional_room_doesnt_block():
    """Optional room references should not change blocking status."""
    result = GroundingResult()

    # Add required reference (resolved)
    ref = EntityReference(text="chair")
    res = ResolutionResult(
        status="resolved",
        reference=ref,
        selected=ResolutionCandidate(
            node_id="chair_1",
            node_type="objects",
            label="chair",
        ),
    )
    result.add_result("target", res, is_required=True)

    # Add optional room reference (not_found)
    room_ref = EntityReference(text="bedroom")
    room_res = ResolutionResult(
        status="not_found",
        reference=room_ref,
        reason="No bedroom found",
    )
    result.add_result("room", room_res, is_required=False)

    # Status should still be ready (not not_found)
    assert result.status == "ready"

    print("✓ test_optional_room_doesnt_block passed")


def run_all_tests():
    """Run all grounding constraint tests."""
    print("Running grounding constraint tests...\n")

    tests = [
        test_constraint_ids_separate_from_ref_ids,
        test_avoid_id_in_plan_is_valid,
        test_role_specific_resolution,
        test_ambiguous_result_has_candidates,
        test_not_found_result_has_reason,
        test_optional_room_doesnt_block,
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
