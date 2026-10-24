import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = (Path(__file__).resolve().parents[1] / "scripts" /
          "candidate_route_planner.py")
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("candidate_route_planner", SCRIPT)
ROUTES = importlib.util.module_from_spec(SPEC) if SPEC else None
if ROUTES is not None and SCRIPT.exists():
    SPEC.loader.exec_module(ROUTES)
else:
    ROUTES = None


def candidate(node_id, floor):
    return {"target_id": node_id, "floor_id": floor,
            "floor_assignment_source": "test-floor-assignment",
            "navigation_target": {"frame_id": "map", "position": [0.0, 0.0, 0.0]}}


class FakeRouteAdapter:
    def __init__(self, fail_plan_floor=None, fail_restore=False,
                 candidate_lengths=None):
        self.calls = []
        self.fail_plan_floor = fail_plan_floor
        self.fail_restore = fail_restore
        self.candidate_lengths = candidate_lengths or {}

    def plan_to_pose(self, floor_id, start_pose, goal_pose):
        self.calls.append(("plan", floor_id, goal_pose["x"]))
        if floor_id == self.fail_plan_floor:
            return {"status": "no_path", "reason": "blocked portal"}
        return {"status": "path_found", "path_length_m": 1.0}

    def switch_floor(self, edge, map_yaml_path):
        self.calls.append(("switch", edge["to_floor"], map_yaml_path))
        return {"status": "succeeded"}

    def plan_candidate(self, floor_id, target):
        self.calls.append(("candidate", floor_id, target["target_id"]))
        return {"status": "path_found",
                "path_length_m": self.candidate_lengths.get(target["target_id"], 2.0),
                "goal": {"x": float(floor_id), "y": 0.0,
                         "yaw": 0.0, "frame_id": "map"}}

    def set_virtual_pose(self, floor_id, pose):
        self.calls.append(("virtual_pose", floor_id, pose["x"]))
        return {"status": "succeeded"}

    def restore_start(self, initial_state):
        self.calls.append(("restore", initial_state["floor_id"]))
        return {"status": "failed" if self.fail_restore else "succeeded"}


def route_bundle():
    return {
        "floors": {str(i): {"map_yaml_path": f"floor_{i}.yaml"} for i in range(3)},
        "transitions": [
            {"transition_id": "F0_TO_F1", "from_floor": 0, "to_floor": 1,
             "entry_pose": {"x": 1.0, "y": 0.0, "yaw": 0.0},
             "exit_pose": {"x": 1.0, "y": 0.0, "yaw": 0.0}},
            {"transition_id": "F1_TO_F2", "from_floor": 1, "to_floor": 2,
             "entry_pose": {"x": 2.0, "y": 0.0, "yaw": 0.0},
             "exit_pose": {"x": 2.0, "y": 0.0, "yaw": 0.0}},
        ],
    }


def test_selects_lowest_cost_complete_cross_floor_sequence():
    assert ROUTES is not None, "candidate route planner is not implemented"
    stops = [[candidate("room-a", 1), candidate("room-b", 3)],
             [candidate("room-c", 2), candidate("room-d", 3)]]
    costs = {
        ("room-a", "room-c"): (18.0, 2),
        ("room-a", "room-d"): (50.0, 1),
        ("room-b", "room-c"): (28.0, 2),
        ("room-b", "room-d"): (15.0, 0),
    }

    def evaluate(sequence):
        length, transitions = costs[tuple(item["target_id"] for item in sequence)]
        return {"status": "feasible", "total_path_length_m": length,
                "transition_count": transitions, "route_actions": []}

    result = ROUTES.select_candidate_chain(stops, evaluate)
    assert result["status"] == "selected"
    assert [x["target_id"] for x in result["selected_candidates"]] == ["room-b", "room-d"]
    assert result["total_path_length_m"] == pytest.approx(15.0)


def test_deterministic_tie_breaks_by_transition_count_then_node_ids():
    assert ROUTES is not None, "candidate route planner is not implemented"
    stops = [[candidate("z-room", 1), candidate("m-room", 2),
              candidate("a-room", 3)]]

    def evaluate(sequence):
        transitions = 2 if sequence[0]["target_id"] == "z-room" else 1
        return {"status": "feasible", "total_path_length_m": 8.0,
                "transition_count": transitions, "route_actions": []}

    result = ROUTES.select_candidate_chain(stops, evaluate)
    assert result["selected_candidates"][0]["target_id"] == "a-room"


def test_returns_fail_closed_diagnostics_when_no_sequence_is_feasible():
    assert ROUTES is not None, "candidate route planner is not implemented"
    stops = [[candidate("room-a", 1), candidate("room-b", 3)]]
    result = ROUTES.select_candidate_chain(
        stops, lambda sequence: {"status": "no_path",
                                 "reason": f"blocked:{sequence[0]['target_id']}"})
    assert result["status"] == "no_feasible_candidate_route"
    assert {x["reason"] for x in result["rejected_sequences"]} == {
        "blocked:room-a", "blocked:room-b"}


def test_selector_aborts_immediately_if_preflight_state_restore_fails():
    assert ROUTES is not None, "candidate route planner is not implemented"
    stops = [[candidate("room-a", 1), candidate("room-b", 2)]]
    calls = []

    def evaluate(sequence):
        target_id = sequence[0]["target_id"]
        calls.append(target_id)
        if target_id == "room-a":
            return {"status": "preflight_state_restore_failed",
                    "reason": "cannot restore F0"}
        return {"status": "feasible", "total_path_length_m": 1.0,
                "transition_count": 0}

    result = ROUTES.select_candidate_chain(stops, evaluate)
    assert result["status"] == "preflight_state_restore_failed"
    assert calls == ["room-a"]


def test_evaluates_every_candidate_sequence_exactly_once():
    assert ROUTES is not None, "candidate route planner is not implemented"
    stops = [[candidate("a", 0), candidate("b", 1)],
             [candidate("c", 2), candidate("d", 3)]]
    calls = []

    def evaluate(sequence):
        key = tuple(x["target_id"] for x in sequence)
        calls.append(key)
        return {"status": "feasible", "total_path_length_m": 1.0,
                "transition_count": 0, "route_actions": []}

    result = ROUTES.select_candidate_chain(stops, evaluate)
    assert len(calls) == 4
    assert len(set(calls)) == 4
    assert result["evaluated_sequence_count"] == 4


@pytest.mark.parametrize("bad_length", [-1.0, float("nan"), float("inf")])
def test_rejects_invalid_route_cost(bad_length):
    assert ROUTES is not None, "candidate route planner is not implemented"
    stops = [[candidate("room-a", 1)]]
    result = ROUTES.select_candidate_chain(
        stops, lambda _: {"status": "feasible", "total_path_length_m": bad_length,
                          "transition_count": 0, "route_actions": []})
    assert result["status"] == "no_feasible_candidate_route"


def test_route_preflight_plans_portals_and_restores_start_without_motion_goals():
    assert ROUTES is not None, "candidate route planner is not implemented"
    adapter = FakeRouteAdapter()
    initial = {"floor_id": 0, "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0,
                                       "frame_id": "map"}}
    result = ROUTES.evaluate_candidate_sequence(
        [candidate("room-f2", 2)], initial, route_bundle(), adapter)
    assert result["status"] == "feasible"
    assert result["total_path_length_m"] == pytest.approx(4.0)
    assert result["transition_count"] == 2
    assert [call[0] for call in adapter.calls].count("plan") == 2
    assert [call[0] for call in adapter.calls].count("candidate") == 1
    assert adapter.calls[-1] == ("restore", 0)
    assert not any("navigate" in call[0] for call in adapter.calls)


def test_route_preflight_restores_start_when_a_portal_segment_is_infeasible():
    assert ROUTES is not None, "candidate route planner is not implemented"
    adapter = FakeRouteAdapter(fail_plan_floor=1)
    initial = {"floor_id": 0, "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0,
                                       "frame_id": "map"}}
    result = ROUTES.evaluate_candidate_sequence(
        [candidate("room-f2", 2)], initial, route_bundle(), adapter)
    assert result["status"] == "no_path"
    assert "blocked portal" in result["reason"]
    assert adapter.calls[-1] == ("restore", 0)


def test_route_preflight_fails_closed_if_start_state_cannot_be_restored():
    assert ROUTES is not None, "candidate route planner is not implemented"
    adapter = FakeRouteAdapter(fail_restore=True)
    initial = {"floor_id": 0, "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0,
                                       "frame_id": "map"}}
    result = ROUTES.evaluate_candidate_sequence(
        [candidate("room-f0", 0)], initial, route_bundle(), adapter)
    assert result["status"] == "preflight_state_restore_failed"


def test_resolved_candidate_sequence_compiles_to_single_floor_stops():
    assert ROUTES is not None, "candidate route planner is not implemented"
    candidate_mission = {
        "schema_version": "hospital-multifloor-candidate-mission-v1",
        "task_id": "HC-027", "map_bundle_id": "fixed-hospital-map",
        "initial_floor": 0,
        "initial_pose": {"x": 14.85, "y": 3.65, "yaw": 0.0, "frame_id": "map"},
        "stops": [
            {"stop_index": 0, "candidate_targets": [candidate("room-a", 1),
                                                       candidate("room-b", 3)]},
            {"stop_index": 1, "candidate_targets": [candidate("room-c", 2)]},
        ],
    }
    resolved = ROUTES.build_resolved_mission(
        candidate_mission, [candidate("room-b", 3), candidate("room-c", 2)])
    assert resolved["schema_version"] == "hospital-multifloor-mission-v1"
    assert resolved["initial_floor"] == 0
    assert [stop["floor_id"] for stop in resolved["stops"]] == [3, 2]
    assert [[c["target_id"] for c in stop["candidate_targets"]]
            for stop in resolved["stops"]] == [["room-b"], ["room-c"]]


def test_resolved_candidate_sequence_rejects_candidate_not_in_frozen_set():
    assert ROUTES is not None, "candidate route planner is not implemented"
    candidate_mission = {
        "schema_version": "hospital-multifloor-candidate-mission-v1",
        "task_id": "HC-027", "map_bundle_id": "fixed-hospital-map",
        "initial_floor": 0,
        "initial_pose": {"x": 14.85, "y": 3.65, "yaw": 0.0, "frame_id": "map"},
        "stops": [{"stop_index": 0, "candidate_targets": [candidate("room-a", 1)]}],
    }
    with pytest.raises(ValueError, match="not in frozen candidate set"):
        ROUTES.build_resolved_mission(candidate_mission, [candidate("room-x", 1)])


def test_candidate_route_planner_is_installed_beside_ros_runner():
    package_root = Path(__file__).resolve().parents[1]
    cmake = (package_root / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "scripts/candidate_route_planner.py" in cmake


def test_candidate_mission_resolution_selects_route_and_builds_concrete_mission():
    assert ROUTES is not None, "candidate route planner is not implemented"
    mission = {
        "schema_version": "hospital-multifloor-candidate-mission-v1",
        "task_id": "HC-027", "map_bundle_id": "test-hospital-map",
        "initial_floor": 0,
        "initial_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame_id": "map"},
        "stops": [{"stop_index": 0,
                   "candidate_targets": [candidate("room-f1", 1),
                                         candidate("room-f2", 2)]}],
    }
    adapter = FakeRouteAdapter(candidate_lengths={"room-f1": 1.0, "room-f2": 8.0})
    result = ROUTES.resolve_candidate_mission(mission, route_bundle(), adapter)
    assert result["status"] == "selected"
    assert result["route_selection"]["selected_target_ids"] == ["room-f1"]
    assert result["resolved_mission"]["initial_floor"] == 0
    assert result["resolved_mission"]["stops"][0]["floor_id"] == 1
    assert [call for call in adapter.calls if call[0] == "restore"]


def test_candidate_mission_resolution_fails_closed_without_a_feasible_floor_route():
    assert ROUTES is not None, "candidate route planner is not implemented"
    mission = {
        "schema_version": "hospital-multifloor-candidate-mission-v1",
        "task_id": "HC-027", "map_bundle_id": "test-hospital-map",
        "initial_floor": 0,
        "initial_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame_id": "map"},
        "stops": [{"stop_index": 0,
                   "candidate_targets": [candidate("room-f1", 1),
                                         candidate("room-f2", 2)]}],
    }
    result = ROUTES.resolve_candidate_mission(
        mission, route_bundle(), FakeRouteAdapter(fail_plan_floor=0))
    assert result["status"] == "no_feasible_candidate_route"
    assert "resolved_mission" not in result
