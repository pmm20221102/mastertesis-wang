import importlib.util
from pathlib import Path
import sys


SCRIPT = (Path(__file__).resolve().parents[1] / "scripts" /
          "run_task_chain_nav2.py")
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("run_task_chain_nav2", SCRIPT)
RUNNER = importlib.util.module_from_spec(SPEC) if SPEC else None
if RUNNER is not None and SCRIPT.exists():
    SPEC.loader.exec_module(RUNNER)
else:
    RUNNER = None


class PlanOnlyAdapter:
    def __init__(self):
        self.motion_goals = []
        self.restored = []

    def restore_start(self, initial_state):
        self.restored.append(initial_state["floor_id"])
        return {"status": "succeeded"}

    def plan_to_pose(self, floor_id, start_pose, goal_pose):
        return {"status": "path_found", "path_length_m": 1.0,
                "path": [{"x": start_pose["x"], "y": start_pose["y"]},
                         {"x": goal_pose["x"], "y": goal_pose["y"]}]}

    def switch_floor(self, edge, map_yaml_path):
        return {"status": "succeeded"}

    def plan_candidate(self, floor_id, candidate):
        return {"status": "path_found", "path_length_m": 2.0,
                "path": [{"x": 1.0, "y": 2.0}, {"x": 2.0, "y": 3.0}],
                "goal": {"x": 2.0, "y": 3.0, "yaw": 0.0, "frame_id": "map"},
                "projection": {"method": "source_target"}}

    def set_virtual_pose(self, floor_id, pose):
        return {"status": "succeeded"}

    def navigate_to_pose(self, pose):
        self.motion_goals.append(pose)
        return {"status": "succeeded"}


def candidate_mission():
    return {
        "schema_version": "hospital-multifloor-candidate-mission-v1",
        "task_id": "instruction-smoke-1",
        "map_bundle_id": "hospital-map-v1",
        "initial_floor": 0,
        "initial_pose": {"frame_id": "map", "x": 14.85, "y": 3.65, "yaw": 0.0},
        "stops": [{"stop_index": 0, "candidate_targets": [{
            "target_id": "room-1", "floor_id": 0,
            "floor_assignment_source": "hospital-map-v1:navigation_target_z",
            "navigation_target": {"frame_id": "map", "position": [2.0, 3.0, -1.2],
                                  "floor_id": 0,
                                  "floor_assignment_source": "hospital-map-v1:navigation_target_z"},
        }]}],
    }


def test_plan_only_returns_a_route_and_never_sends_navigation_goal():
    assert RUNNER is not None, "Nav2 route-only runner is not implemented"
    mission = candidate_mission()
    bundle = {"map_bundle_id": "hospital-map-v1", "floors": {
        "0": {"map_yaml_path": "floor_0.yaml"}}, "transitions": []}
    adapter = PlanOnlyAdapter()

    result = RUNNER.plan_candidate_mission(mission, bundle, adapter)

    assert result["status"] == "planned"
    assert result["execution_performed"] is False
    assert result["route_selection"]["selected_target_ids"] == ["room-1"]
    assert result["route_selection"]["segment_results"][0]["path"] == [
        {"x": 1.0, "y": 2.0}, {"x": 2.0, "y": 3.0}]
    assert adapter.motion_goals == []
    assert adapter.restored == [0, 0]


def test_plan_only_reports_no_route_without_claiming_execution():
    assert RUNNER is not None
    mission = candidate_mission()
    bundle = {"map_bundle_id": "hospital-map-v1", "floors": {
        "0": {"map_yaml_path": "floor_0.yaml"}}, "transitions": []}
    adapter = PlanOnlyAdapter()
    adapter.plan_candidate = lambda floor_id, candidate: {
        "status": "no_path", "reason": "blocked on active map"}

    result = RUNNER.plan_candidate_mission(mission, bundle, adapter)

    assert result["status"] == "no_feasible_candidate_route"
    assert result["execution_performed"] is False
    assert adapter.motion_goals == []
