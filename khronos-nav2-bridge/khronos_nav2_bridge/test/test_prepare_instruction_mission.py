import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = (Path(__file__).resolve().parents[1] / "scripts" /
          "prepare_instruction_mission.py")
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("prepare_instruction_mission", SCRIPT)
PREP = importlib.util.module_from_spec(SPEC) if SPEC else None
if PREP is not None and SCRIPT.exists():
    SPEC.loader.exec_module(PREP)
else:
    PREP = None


def floor_bundle():
    return {
        "map_bundle_id": "hospital-p1000-khronos-only-test",
        "frame_id": "map",
        "floors": {
            "0": {"floor_z_m": -1.2, "map_yaml_path": "f0.yaml"},
            "1": {"floor_z_m": 2.3, "map_yaml_path": "f1.yaml"},
            "2": {"floor_z_m": 5.8, "map_yaml_path": "f2.yaml"},
            "3": {"floor_z_m": 9.3, "map_yaml_path": "f3.yaml"},
        },
        "transitions": [],
    }


def nav_target(node_id, z, frame_id="map"):
    return {"navigation_node_id": f"place-{node_id}",
            "position": [1.0, 2.0, z], "frame_id": frame_id,
            "goal_type": "place"}


def pipeline_result():
    return {
        "status": "valid",
        "task_plan": {
            "status": "valid",
            "task_chain": [
                {"step": 1, "action": "locate_entity", "executor": "scene_graph",
                 "target_ref": "target", "target_id": "bed-1",
                 "candidate_node_ids": [], "constraints": {}},
                {"step": 2, "action": "navigate_to", "executor": "nav2",
                 "target_ref": "destination", "target_id": "room-2",
                 "candidate_node_ids": [], "constraints": {}},
            ],
        },
        "grounded_task": {
            "references": {
                "destination": {"node_id": "room-2", "navigation_target":
                                nav_target("room-2", 2.31)},
            },
            "steps": [],
        },
    }


def initial_state():
    return {"floor_id": 0, "pose": {"frame_id": "map", "x": 14.85,
                                    "y": 3.65, "yaw": 0.0}}


def test_builds_ordered_candidate_mission_from_live_pipeline_result():
    assert PREP is not None, "instruction-to-mission adapter is not implemented"
    result = PREP.build_candidate_mission(
        pipeline_result(), floor_bundle(), initial_state(),
        task_id="instruction-smoke-1",
    )

    assert result["status"] == "ready"
    mission = result["mission"]
    assert mission["schema_version"] == "hospital-multifloor-candidate-mission-v1"
    assert mission["map_bundle_id"] == "hospital-p1000-khronos-only-test"
    assert mission["initial_floor"] == 0
    assert mission["initial_pose"] == initial_state()["pose"]
    assert len(mission["stops"]) == 1
    stop = mission["stops"][0]
    assert stop["source_task_step"] == 2
    assert stop["candidate_targets"][0]["target_id"] == "room-2"
    assert stop["candidate_targets"][0]["floor_id"] == 1


def test_accepts_yaml_integer_floor_keys_from_the_real_map_bundle():
    assert PREP is not None
    bundle = floor_bundle()
    bundle["floors"] = {int(key): value for key, value in bundle["floors"].items()}

    result = PREP.build_candidate_mission(
        pipeline_result(), bundle, initial_state(), task_id="instruction-int-floor-keys"
    )

    assert result["status"] == "ready"
    assert result["mission"]["stops"][0]["candidate_targets"][0]["floor_id"] == 1


def test_preserves_candidates_spanning_floors_for_nav2_route_selection():
    assert PREP is not None
    pipeline = pipeline_result()
    pipeline["task_plan"]["task_chain"] = [{
        "step": 1, "action": "navigate_to", "executor": "nav2",
        "target_ref": "destination", "target_id": None,
        "candidate_node_ids": ["room-2", "room-3"],
        "selection_policy": "deferred_to_nav2", "constraints": {},
    }]
    pipeline["grounded_task"]["references"]["destination"] = {
        "candidate_node_ids": ["room-2", "room-3"],
        "candidate_navigation_targets": [
            {"node_id": "room-2", "navigation_target": nav_target("room-2", 2.31)},
            {"node_id": "room-3", "navigation_target": nav_target("room-3", 9.28)},
        ],
        "selection_policy": "deferred_to_nav2",
    }

    result = PREP.build_candidate_mission(
        pipeline, floor_bundle(), initial_state(), task_id="instruction-ambiguous"
    )

    assert result["status"] == "ready"
    candidates = result["mission"]["stops"][0]["candidate_targets"]
    assert [(item["target_id"], item["floor_id"]) for item in candidates] == [
        ("room-2", 1), ("room-3", 3)]


def test_rejects_navigation_targets_outside_registered_floor_tolerance():
    assert PREP is not None
    pipeline = pipeline_result()
    pipeline["grounded_task"]["references"]["destination"]["navigation_target"] = (
        nav_target("room-2", 2.75))

    result = PREP.build_candidate_mission(
        pipeline, floor_bundle(), initial_state(), task_id="instruction-out-of-band"
    )

    assert result["status"] == "floor_unresolved"
    assert result["mission"] is None
    assert result["unresolved_targets"][0]["target_id"] == "room-2"


def test_room_floor_uses_member_place_evidence_instead_of_room_centroid_z():
    assert PREP is not None
    pipeline = pipeline_result()
    pipeline["task_plan"]["task_chain"] = [{
        "step": 1, "action": "navigate_to", "executor": "nav2",
        "target_ref": "destination", "target_id": "room-3",
        "candidate_node_ids": [], "constraints": {},
    }]
    pipeline["grounded_task"]["references"]["destination"] = {
        "node_id": "room-3", "node_type": "room",
        "navigation_target": nav_target("room-3", 10.93),
    }
    graph = {"nodes": {
        "room-3": {"node_type": "room", "position": [5.6, -7.6, 10.93]},
        "place-a": {"node_type": "place", "room_id": "room-3",
                    "position": [5.0, -7.0, 8.9]},
        "place-b": {"node_type": "place", "room_id": "room-3",
                    "position": [6.0, -8.0, 9.3]},
        "place-c": {"node_type": "place", "room_id": "room-3",
                    "position": [6.5, -8.0, 9.9]},
    }}

    result = PREP.build_candidate_mission(
        pipeline, floor_bundle(), initial_state(), task_id="instruction-room-floor",
        canonical_graph=graph,
    )

    assert result["status"] == "ready"
    candidate = result["mission"]["stops"][0]["candidate_targets"][0]
    assert candidate["floor_id"] == 3
    assert candidate["navigation_target"]["position"][2] == 9.3
    assert candidate["floor_assignment_evidence"]["member_place_count"] == 3


def test_rejects_route_modifiers_the_nav2_planner_does_not_apply():
    assert PREP is not None
    pipeline = pipeline_result()
    pipeline["task_plan"]["task_chain"][1]["constraints"] = {
        "avoid": ["restricted-corridor"], "prefer": [], "conditions": []}

    result = PREP.build_candidate_mission(
        pipeline, floor_bundle(), initial_state(), task_id="instruction-avoid"
    )

    assert result["status"] == "unsupported_navigation_constraint"
    assert result["mission"] is None
