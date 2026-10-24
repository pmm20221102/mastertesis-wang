import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_multifloor_missions.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("prepare_multifloor_missions", SCRIPT)
PREP = importlib.util.module_from_spec(SPEC) if SPEC else None
if PREP is not None and SCRIPT.exists():
    SPEC.loader.exec_module(PREP)
else:
    PREP = None


def records(candidate_floors=(2, 2)):
    ids = ["room-a", "room-b"]
    targets = [{"node_id": node_id, "navigation_target": {
        "frame_id": "map", "navigation_node_id": node_id,
        "position": [float(index), 1.0, 6.8],
    }} for index, node_id in enumerate(ids)]
    prediction = {
        "task_id": "HC-027",
        "result": {
            "status": "valid",
            "task_plan": {"status": "valid", "task_chain": [
                {"target_id": "start-room", "candidate_node_ids": [],
                 "target_ref": "step_0_target"},
                {"target_id": None, "candidate_node_ids": ids,
                 "target_ref": "step_1_target"},
            ]},
            "grounded_task": {"steps": [
                {"references": {"target": {
                    "node_id": "start-room", "navigation_target": {
                        "frame_id": "map", "position": [0.0, 0.0, 10.9]},
                }}},
                {"references": {"target": {"candidate_node_ids": ids,
                                                "candidate_navigation_targets": targets}}},
            ]},
        },
    }
    classification = {"task_id": "HC-027", "candidate_floor_details_by_stop": [
        [{"node_id": "start-room", "nearest_floor": 3}],
        [{"node_id": ids[0], "nearest_floor": candidate_floors[0]},
         {"node_id": ids[1], "nearest_floor": candidate_floors[1]}],
    ]}
    starts = {"poses": {str(floor): {"pose": {"x": floor, "y": 0.0,
                                               "yaw": 0.0, "frame_id": "map"}}
                         for floor in range(4)}}
    return prediction, classification, starts


def test_same_floor_ambiguous_target_remains_candidate_set_for_nav2():
    assert PREP is not None, "mission-preparation adapter is not implemented"
    prediction, classification, starts = records()
    result = PREP.prepare_task_missions([prediction], [classification], starts,
                                        ["HC-027"], "fixed-hospital-map")
    task = result["tasks"][0]
    assert task["status"] == "ready"
    assert task["mission"]["initial_floor"] == 3
    assert task["mission"]["stops"][1]["floor_id"] == 2
    assert [item["target_id"] for item in task["mission"]["stops"][1]["candidate_targets"]] == [
        "room-a", "room-b"]


def test_cross_floor_candidate_ambiguity_fails_closed_instead_of_picking_first():
    assert PREP is not None, "mission-preparation adapter is not implemented"
    prediction, classification, starts = records((1, 3))
    result = PREP.prepare_task_missions([prediction], [classification], starts,
                                        ["HC-027"], "fixed-hospital-map")
    task = result["tasks"][0]
    assert task["status"] == "unresolved_floor"
    assert task["mission"] is None
    assert task["unresolved_stops"] == [{"stop_index": 1,
                                         "candidate_floors": [1, 3]}]


def test_cross_floor_opt_in_preserves_every_candidate_for_route_search():
    assert PREP is not None, "mission-preparation adapter is not implemented"
    prediction, classification, starts = records((1, 3))
    fixed_start = {
        "map_bundle_id": "fixed-hospital-map",
        "floor_id": 0,
        "pose": {"x": 14.85, "y": 3.65, "yaw": 0.0, "frame_id": "map"},
    }
    result = PREP.prepare_task_missions(
        [prediction], [classification], starts, ["HC-027"], "fixed-hospital-map",
        allow_cross_floor_candidates=True, fixed_start_pose=fixed_start,
    )
    task = result["tasks"][0]
    assert task["status"] == "ready"
    candidate_mission = task["mission"]
    assert candidate_mission["schema_version"] == "hospital-multifloor-candidate-mission-v1"
    assert candidate_mission["initial_floor"] == 0
    assert candidate_mission["initial_pose"] == fixed_start["pose"]
    assert [item["target_id"] for item in candidate_mission["stops"][1]["candidate_targets"]] == [
        "room-a", "room-b"]
    assert [item["floor_id"] for item in candidate_mission["stops"][1]["candidate_targets"]] == [
        1, 3]
    assert "floor_id" not in candidate_mission["stops"][1]


def test_fixed_start_pose_is_independent_of_first_target_floor():
    assert PREP is not None, "mission-preparation adapter is not implemented"
    prediction, classification, starts = records((2, 2))
    fixed_start = {
        "map_bundle_id": "fixed-hospital-map",
        "floor_id": 0,
        "pose": {"x": 14.85, "y": 3.65, "yaw": 0.0, "frame_id": "map"},
    }
    result = PREP.prepare_task_missions(
        [prediction], [classification], starts, ["HC-027"], "fixed-hospital-map",
        allow_cross_floor_candidates=True, fixed_start_pose=fixed_start,
    )
    mission = result["tasks"][0]["mission"]
    assert mission["initial_floor"] == 0
    assert mission["initial_pose"] == fixed_start["pose"]


def test_missing_nav_target_candidate_fails_as_data_gap():
    assert PREP is not None, "mission-preparation adapter is not implemented"
    prediction, classification, starts = records()
    prediction["result"]["grounded_task"]["steps"][1]["references"]["target"][
        "candidate_navigation_targets"].pop()
    result = PREP.prepare_task_missions([prediction], [classification], starts,
                                        ["HC-027"], "fixed-hospital-map")
    assert result["tasks"][0]["status"] == "invalid_pipeline_output"
    assert "navigation target" in result["tasks"][0]["reason"]
