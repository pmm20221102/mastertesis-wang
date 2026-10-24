from pathlib import Path
import sys


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE / "scripts"
sys.path.insert(0, str(SCRIPTS))
from multifloor_status import build_live_status
from run_task_chain_nav2 import execute_compiled_mission


def test_live_status_has_versioned_nullable_fields_and_rejects_unknown_state():
    message = build_live_status(
        task_id="HC-031", state="switching_map", active_floor=None,
        from_floor=3, to_floor=2, transition_id="F3_TO_F2_MAIN", error=None,
    )
    assert message == {
        "schema": "hospital_multifloor_live_status_v1",
        "task_id": "HC-031", "state": "switching_map", "active_floor": None,
        "from_floor": 3, "to_floor": 2,
        "transition_id": "F3_TO_F2_MAIN", "error": None,
    }
    try:
        build_live_status(task_id="HC-031", state="teleporting")
    except ValueError as error:
        assert "state" in str(error)
    else:
        raise AssertionError("unsupported live status state was accepted")


def test_runner_emits_floor_status_around_each_abstract_transition():
    class Adapter:
        def load_map(self, floor, _path):
            return {"status": "succeeded", "floor": floor}

        def reset_pose(self, floor, _pose):
            return {"status": "succeeded", "floor": floor}

        def navigate_to_pose(self, _pose):
            return {"status": "succeeded"}

        def plan_stop(self, floor, _candidates):
            return {"status": "path_found", "goal": {"x": float(floor), "y": 0.0}}

    compiled = {
        "task_id": "HC-031", "initial_state": {"floor_id": 3,
            "pose": {}, "map_yaml_path": "f3.yaml"},
        "actions": [
            {"kind": "navigate_to_entry", "transition_id": "T32", "floor_id": 3, "pose": {}},
            {"kind": "load_map", "transition_id": "T32", "floor_id": 2, "map_yaml_path": "f2.yaml"},
            {"kind": "set_pose", "transition_id": "T32", "floor_id": 2, "pose": {}},
            {"kind": "navigate_to_stop", "floor_id": 2, "stop_index": 0, "candidate_targets": [{}]},
        ],
    }
    statuses = []

    result = execute_compiled_mission(compiled, Adapter(), status_callback=statuses.append)

    assert result["status"] == "succeeded"
    assert [status["state"] for status in statuses] == [
        "switching_map", "resetting_pose", "floor_ready",
        "navigating_to_entry", "switching_map", "resetting_pose",
        "floor_ready", "navigating_to_stop", "floor_ready", "succeeded",
    ]
    transition = statuses[4]
    assert (transition["from_floor"], transition["to_floor"],
            transition["transition_id"]) == (3, 2, "T32")


def test_runner_emits_failed_status_when_map_load_fails():
    class Adapter:
        def load_map(self, _floor, _path):
            return False

        def reset_pose(self, _floor, _pose):
            raise AssertionError("pose reset must not run after map failure")

    statuses = []
    result = execute_compiled_mission(
        {"task_id": "HC-X", "initial_state": {"floor_id": 0, "pose": {},
          "map_yaml_path": "f0.yaml"}, "actions": []},
        Adapter(), status_callback=statuses.append,
    )
    assert result["status"] == "failed"
    assert statuses[-1]["state"] == "failed"
    assert "map" in statuses[-1]["error"]


def test_ros_status_publisher_uses_rclpy_duration_for_ack_wait():
    source = (SCRIPTS / "run_task_chain_nav2.py").read_text(encoding="utf-8")
    assert "wait_for_all_acked(Duration(seconds=1.0))" in source


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
