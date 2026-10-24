import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "floor_transition_core.py"
SPEC = importlib.util.spec_from_file_location("floor_transition_core", SCRIPT)
CORE = importlib.util.module_from_spec(SPEC) if SPEC else None
if CORE is not None and SCRIPT.exists():
    SPEC.loader.exec_module(CORE)
else:
    CORE = None


@pytest.fixture
def core():
    return CORE


def bundle():
    floors = {
        floor: {
            "report_path": f"/maps/floor_{floor}/report.json",
            "map_yaml_path": f"/maps/floor_{floor}/map.yaml",
            "map_yaml_sha256": "a" * 64,
            "map_pgm_path": f"/maps/floor_{floor}/map.pgm",
            "map_pgm_sha256": "b" * 64,
        }
        for floor in range(4)
    }
    def edge(edge_id, src, dst):
        return {
            "transition_id": edge_id,
            "from_floor": src,
            "to_floor": dst,
            "entry_pose": {"x": float(src), "y": 0.0, "yaw": 0.0},
            "exit_pose": {"x": float(dst), "y": 0.0, "yaw": 0.0},
        }
    return {
        "schema_version": "hospital-floor-transitions-v1",
        "map_bundle_id": "test-hospital-map",
        "frame_id": "map",
        "floors": floors,
        "transitions": [
            edge("F0_TO_F1", 0, 1), edge("F1_TO_F0", 1, 0),
            edge("F1_TO_F2", 1, 2), edge("F2_TO_F1", 2, 1),
            edge("F2_TO_F3", 2, 3), edge("F3_TO_F2", 3, 2),
        ],
    }


def stop(index, floor):
    target_id = f"target-{index}"
    source = "test-floor-fixture"
    return {
        "stop_index": index,
        "floor_id": floor,
        "floor_assignment_source": source,
        "candidate_targets": [{
            "target_id": target_id,
            "floor_id": floor,
            "floor_assignment_source": source,
            "navigation_target": {
                "position": [10.0 + index, 2.0, 0.0],
                "frame_id": "map",
                "floor_id": floor,
                "floor_assignment_source": source,
            },
        }],
    }


def mission(initial_floor, stops):
    return {
        "schema_version": "hospital-multifloor-mission-v1",
        "task_id": "test-task",
        "initial_floor": initial_floor,
        "initial_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "stops": stops,
    }


def test_same_floor_stops_compile_without_map_switches(core):
    assert core is not None, "floor_transition_core.py has not been implemented"
    result = core.compile_mission(
        mission(1, [stop(0, 1), stop(1, 1)]), bundle(), verify_files=False
    )
    assert [action["kind"] for action in result["actions"]] == [
        "navigate_to_stop", "navigate_to_stop"
    ]
    assert all("transition_id" not in action for action in result["actions"])
    assert result["actions"][0]["candidate_targets"][0]["target_id"] == "target-0"


def test_cross_floor_stop_inserts_all_adjacent_switches_in_order(core):
    assert core is not None, "floor_transition_core.py has not been implemented"
    result = core.compile_mission(
        mission(1, [stop(0, 1), stop(1, 3)]), bundle(), verify_files=False
    )
    actions = result["actions"]
    assert [action["kind"] for action in actions] == [
        "navigate_to_stop",
        "navigate_to_entry", "load_map", "set_pose",
        "navigate_to_entry", "load_map", "set_pose",
        "navigate_to_stop",
    ]
    assert [action["transition_id"] for action in actions if "transition_id" in action] == [
        "F1_TO_F2", "F1_TO_F2", "F1_TO_F2",
        "F2_TO_F3", "F2_TO_F3", "F2_TO_F3",
    ]


def test_unresolved_floor_fails_closed(core):
    assert core is not None, "floor_transition_core.py has not been implemented"
    bad_stop = stop(0, 1)
    bad_stop["floor_id"] = None
    with pytest.raises(ValueError, match="floor_id"):
        core.compile_mission(mission(1, [bad_stop]), bundle(), verify_files=False)


def test_candidate_floor_mismatch_fails_closed(core):
    bad_stop = stop(0, 1)
    bad_stop["candidate_targets"][0]["floor_id"] = 2
    bad_stop["candidate_targets"][0]["navigation_target"]["floor_id"] = 2
    with pytest.raises(ValueError, match="candidate floor_id"):
        core.compile_mission(mission(1, [bad_stop]), bundle(), verify_files=False)


def test_missing_directed_route_fails_closed(core):
    assert core is not None, "floor_transition_core.py has not been implemented"
    data = bundle()
    data["transitions"] = [edge for edge in data["transitions"]
                           if edge["transition_id"] != "F2_TO_F3"]
    with pytest.raises(ValueError, match="no floor route"):
        core.compile_mission(
            mission(1, [stop(0, 3)]), data, verify_files=False
        )
