"""Tests for structured pipeline with mocked components.

Verifies that:
- Pipeline components work together
- target_ref resolution works end-to-end
- Validation catches errors
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "RAG" / "khronos_3dsg_rag" / "src"))

from schema import TaskPlan, TaskStep, Constraints
from grounding.orchestrator import GroundingResult
from api_schemas import ResolutionResult, ResolutionCandidate
from planner_schemas.intent import EntityReference, TaskIntent


def test_grounding_result_to_dict():
    """GroundingResult.to_dict() should produce valid dict."""
    result = GroundingResult()
    result.status = "ready"

    ref = EntityReference(text="chair")
    res = ResolutionResult(
        status="resolved",
        reference=ref,
        selected=ResolutionCandidate(
            node_id="chair_1",
            node_type="objects",
            label="chair",
            room_id="room_1",
            position=[1.0, 2.0, 0.0],
        ),
        candidates=[
            ResolutionCandidate(node_id="chair_1", node_type="objects", label="chair", score=0.9),
        ],
    )
    result.add_result("target", res)

    d = result.to_dict()

    assert d["status"] == "ready"
    assert "target" in d["references"]
    assert d["references"]["target"]["status"] == "resolved"
    assert d["references"]["target"]["selected"]["node_id"] == "chair_1"
    assert d["references"]["target"]["candidates_count"] == 1

    print("✓ test_grounding_result_to_dict passed")


def test_build_grounded_task_structure():
    """_build_grounded_task should produce correct structure."""
    from main_rag import _build_grounded_task

    intent = TaskIntent(
        action="navigate",
        target=EntityReference(text="chair"),
        destination=EntityReference(text="room"),
    )

    # Create mock grounding
    grounding = GroundingResult()

    target_ref = EntityReference(text="chair")
    target_res = ResolutionResult(
        status="resolved",
        reference=target_ref,
        selected=ResolutionCandidate(
            node_id="chair_1",
            node_type="objects",
            label="chair",
            room_id="room_1",
            place_id="place_1",
            position=[1.0, 2.0, 0.0],
        ),
    )
    grounding.add_result("target", target_res)

    dest_ref = EntityReference(text="room")
    dest_res = ResolutionResult(
        status="resolved",
        reference=dest_ref,
        selected=ResolutionCandidate(
            node_id="room_1",
            node_type="rooms",
            label="office",
        ),
    )
    grounding.add_result("destination", dest_res)

    # Create mock RAG
    class MockRAG:
        def get_navigation_target(self, node_id):
            from api_schemas import NavigationTarget
            return NavigationTarget(
                target_entity_id=node_id,
                navigation_node_id=node_id,
                position=[1.0, 2.0, 0.0],
                goal_type="place",
            )

    grounded_task = _build_grounded_task(intent, grounding, MockRAG())

    assert grounded_task["action"] == "navigate"
    assert "target" in grounded_task["references"]
    assert grounded_task["references"]["target"]["node_id"] == "chair_1"
    assert "navigation_target" in grounded_task["references"]["target"]

    print("✓ test_build_grounded_task_structure passed")


def test_resolve_target_refs():
    """_resolve_target_refs should fill target_id from target_ref."""
    from main_rag import _resolve_target_refs

    task_plan = TaskPlan(
        status="valid",
        task_chain=[
            TaskStep(
                step=1,
                action="locate_entity",
                target_type="object",
                target_ref="target",
                target_id=None,
                description="Locate chair",
                executor="scene_graph",
            ),
            TaskStep(
                step=2,
                action="navigate_to",
                target_type="room",
                target_ref="destination",
                target_id=None,
                description="Navigate to room",
                executor="nav2",
                constraints=Constraints(avoid=["constraint_avoid_0"]),
            ),
        ],
    )

    grounded_task = {
        "references": {
            "target": {"node_id": "chair_1"},
            "destination": {"node_id": "room_1"},
        },
        "constraints": [
            {"type": "avoid", "node_id": "kitchen_1"},
        ],
    }

    resolved_plan = _resolve_target_refs(task_plan, grounded_task)

    assert resolved_plan.task_chain[0].target_id == "chair_1"
    assert resolved_plan.task_chain[1].target_id == "room_1"
    assert resolved_plan.task_chain[1].constraints.avoid == ["kitchen_1"]

    print("✓ test_resolve_target_refs passed")


def test_validate_grounded_plan_catches_errors():
    """_validate_grounded_plan should catch various errors."""
    from main_rag import _validate_grounded_plan

    # Create grounding
    grounding = GroundingResult()

    target_ref = EntityReference(text="chair")
    target_res = ResolutionResult(
        status="resolved",
        reference=target_ref,
        selected=ResolutionCandidate(
            node_id="chair_1",
            node_type="objects",
            label="chair",
        ),
    )
    grounding.add_result("target", target_res)

    # Plan with hallucinated ID
    task_plan = TaskPlan(
        status="valid",
        task_chain=[
            TaskStep(
                step=1,
                action="navigate_to",
                target_type="object",
                target_id="hallucinated_id",  # Not in grounding
                description="Navigate",
                executor="nav2",
            )
        ],
    )

    errors = _validate_grounded_plan(task_plan, grounding)
    assert len(errors) > 0
    assert any("not in grounded references" in e for e in errors)

    print("✓ test_validate_grounded_plan_catches_errors passed")


def test_validate_empty_plan_fails():
    """Empty plan with valid status should fail validation."""
    from main_rag import _validate_grounded_plan

    grounding = GroundingResult()
    grounding.status = "ready"

    task_plan = TaskPlan(
        status="valid",
        task_chain=[],
    )

    errors = _validate_grounded_plan(task_plan, grounding)
    assert len(errors) > 0
    assert any("empty" in e.lower() for e in errors)

    print("✓ test_validate_empty_plan_fails passed")


def run_all_tests():
    """Run all structured pipeline tests."""
    print("Running structured pipeline tests...\n")

    tests = [
        test_grounding_result_to_dict,
        test_build_grounded_task_structure,
        test_resolve_target_refs,
        test_validate_grounded_plan_catches_errors,
        test_validate_empty_plan_fails,
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
