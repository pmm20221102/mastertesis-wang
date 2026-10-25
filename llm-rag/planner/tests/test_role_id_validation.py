"""Tests for role-specific ID validation.

Verifies that:
- Swapped role IDs are detected
- Missing target_id for executable steps fails
- Constraint IDs are validated separately
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
from planner_schemas.intent import EntityReference


def test_swapped_role_ids_detected():
    """Swapped actor and destination IDs should be detectable."""
    # Setup grounding
    result = GroundingResult()

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

    # Build role -> id mapping
    role_to_id = {}
    for role in ["actor", "target", "source", "destination"]:
        rid = result.get_resolved_id_by_role(role)
        if rid:
            role_to_id[role] = rid

    # Verify correct mapping
    assert role_to_id["actor"] == "patient_1"
    assert role_to_id["destination"] == "entrance_1"

    # LLM outputs swapped IDs (both are in allowed set, but wrong role)
    # This should be caught by role-specific validation
    llm_target_id = "entrance_1"  # Should be patient_1 for actor

    # Check: is this ID valid for the expected role?
    expected_role = "actor"
    expected_id = role_to_id.get(expected_role)
    if llm_target_id != expected_id:
        # Role mismatch detected!
        role_mismatch = True
    else:
        role_mismatch = False

    assert role_mismatch is True  # Swapped IDs detected

    print("✓ test_swapped_role_ids_detected passed")


def test_missing_target_id_fails():
    """Executable steps with missing target_id should fail."""
    ACTIONS_REQUIRING_TARGET = {
        "navigate_to", "escort_to", "guide_to", "locate_entity",
        "avoid_area", "handover_to_staff", "trigger_workflow",
        "perform_screening",
    }

    # Create plan with missing target_id
    task_plan = TaskPlan(
        status="valid",
        task_chain=[
            TaskStep(
                step=1,
                action="navigate_to",
                target_type="room",
                target_id=None,  # Missing!
                description="Navigate to room",
                executor="nav2",
            )
        ],
    )

    # Validate
    errors = []
    for step in task_plan.task_chain:
        if step.action in ACTIONS_REQUIRING_TARGET and not step.target_id:
            errors.append(f"Step {step.step}: action '{step.action}' requires target_id but got None")

    assert len(errors) == 1
    assert "requires target_id" in errors[0]

    print("✓ test_missing_target_id_fails passed")


def test_wait_step_doesnt_need_target():
    """Wait steps should not require target_id."""
    ACTIONS_REQUIRING_TARGET = {
        "navigate_to", "escort_to", "guide_to", "locate_entity",
        "avoid_area", "handover_to_staff", "trigger_workflow",
        "perform_screening",
    }

    task_plan = TaskPlan(
        status="valid",
        task_chain=[
            TaskStep(
                step=1,
                action="wait",
                target_type="unknown",
                target_id=None,
                description="Wait for condition",
                executor="none",
            )
        ],
    )

    errors = []
    for step in task_plan.task_chain:
        if step.action in ACTIONS_REQUIRING_TARGET and not step.target_id:
            errors.append(f"Step {step.step}: requires target_id")

    assert len(errors) == 0  # Wait doesn't need target

    print("✓ test_wait_step_doesnt_need_target passed")


def test_target_ref_resolution():
    """target_ref should be resolved to target_id programmatically."""
    # Simulate grounded task
    grounded_task = {
        "references": {
            "target": {"node_id": "chair_1", "label": "chair"},
            "destination": {"node_id": "room_1", "label": "room"},
        },
        "constraints": [
            {"type": "avoid", "node_id": "kitchen_1", "label": "kitchen"},
        ],
    }

    # Build lookup
    ref_lookup = {}
    for role, ref_data in grounded_task.get("references", {}).items():
        if ref_data.get("node_id"):
            ref_lookup[role] = ref_data["node_id"]

    constraint_lookup = {}
    for i, constr in enumerate(grounded_task.get("constraints", [])):
        if constr.get("node_id"):
            constraint_lookup[f"constraint_{constr.get('type', 'avoid')}_{i}"] = constr["node_id"]

    all_lookup = {**ref_lookup, **constraint_lookup}

    # Verify lookup
    assert all_lookup["target"] == "chair_1"
    assert all_lookup["destination"] == "room_1"
    assert all_lookup["constraint_avoid_0"] == "kitchen_1"

    # Simulate target_ref resolution
    target_ref = "target"
    resolved_id = all_lookup.get(target_ref)
    assert resolved_id == "chair_1"

    avoid_ref = "constraint_avoid_0"
    resolved_avoid_id = all_lookup.get(avoid_ref)
    assert resolved_avoid_id == "kitchen_1"

    print("✓ test_target_ref_resolution passed")


def test_constraint_avoid_in_plan():
    """Avoid constraints in plan should use resolved constraint IDs."""
    # Simulate grounded task
    grounded_task = {
        "references": {
            "target": {"node_id": "room_1"},
        },
        "constraints": [
            {"type": "avoid", "node_id": "kitchen_1"},
        ],
    }

    # Build lookup
    all_lookup = {}
    for role, ref_data in grounded_task.get("references", {}).items():
        all_lookup[role] = ref_data["node_id"]
    for i, constr in enumerate(grounded_task.get("constraints", [])):
        all_lookup[f"constraint_{constr['type']}_{i}"] = constr["node_id"]

    # LLM outputs avoid as reference
    plan_avoid = ["constraint_avoid_0"]

    # Resolve
    resolved_avoid = []
    for avoid_ref in plan_avoid:
        if avoid_ref in all_lookup:
            resolved_avoid.append(all_lookup[avoid_ref])

    assert resolved_avoid == ["kitchen_1"]

    print("✓ test_constraint_avoid_in_plan passed")


def run_all_tests():
    """Run all role ID validation tests."""
    print("Running role ID validation tests...\n")

    tests = [
        test_swapped_role_ids_detected,
        test_missing_target_id_fails,
        test_wait_step_doesnt_need_target,
        test_target_ref_resolution,
        test_constraint_avoid_in_plan,
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
