import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_task_chain_nav2.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("run_task_chain_nav2", SCRIPT)
RUNNER = importlib.util.module_from_spec(SPEC) if SPEC else None
if RUNNER is not None and SCRIPT.exists():
    SPEC.loader.exec_module(RUNNER)
else:
    RUNNER = None


@pytest.fixture
def runner():
    return RUNNER


class FakeAdapter:
    def __init__(self, nav_results=None, map_results=None, pose_results=None,
                 plan_results=None):
        self.events = []
        self.nav_results = list(nav_results or [])
        self.map_results = list(map_results or [])
        self.pose_results = list(pose_results or [])
        self.plan_results = list(plan_results or [])

    def load_map(self, floor_id, map_yaml_path):
        self.events.append(("load_map", floor_id, map_yaml_path))
        return self.map_results.pop(0) if self.map_results else True

    def reset_pose(self, floor_id, pose):
        self.events.append(("reset_pose", floor_id, pose["x"], pose["y"]))
        return self.pose_results.pop(0) if self.pose_results else True

    def plan_stop(self, floor_id, candidates):
        self.events.append(("plan_stop", floor_id, [item["target_id"] for item in candidates]))
        if self.plan_results:
            return self.plan_results.pop(0)
        return {"status": "path_found", "target_id": candidates[0]["target_id"],
                "goal": {"x": 1.0, "y": 2.0, "yaw": 0.0}}

    def navigate_to_pose(self, pose):
        self.events.append(("navigate", pose["x"], pose["y"]))
        return self.nav_results.pop(0) if self.nav_results else True


def compiled_same_floor():
    return {
        "task_id": "same-floor",
        "initial_state": {"floor_id": 1, "map_yaml_path": "floor1.yaml",
                          "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0}},
        "actions": [
            {"kind": "navigate_to_stop", "stop_index": 0, "floor_id": 1,
             "candidate_targets": [{"target_id": "a", "navigation_target": {}}]},
            {"kind": "navigate_to_stop", "stop_index": 1, "floor_id": 1,
             "candidate_targets": [{"target_id": "b", "navigation_target": {}}]},
        ],
    }


def compiled_cross_floor():
    return {
        "task_id": "cross-floor",
        "initial_state": {"floor_id": 1, "map_yaml_path": "floor1.yaml",
                          "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0}},
        "actions": [
            {"kind": "navigate_to_entry", "transition_id": "F1_TO_F2",
             "floor_id": 1, "pose": {"x": 1.0, "y": 1.0, "yaw": 0.0}},
            {"kind": "load_map", "transition_id": "F1_TO_F2",
             "floor_id": 2, "map_yaml_path": "floor2.yaml"},
            {"kind": "set_pose", "transition_id": "F1_TO_F2",
             "floor_id": 2, "pose": {"x": 2.0, "y": 2.0, "yaw": 0.5}},
            {"kind": "navigate_to_stop", "stop_index": 0, "floor_id": 2,
             "candidate_targets": [{"target_id": "target", "navigation_target": {}}]},
        ],
    }


def test_same_floor_chain_uses_one_runner_without_floor_switch(runner):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    adapter = FakeAdapter()
    result = runner.execute_compiled_mission(compiled_same_floor(), adapter)
    assert result["status"] == "succeeded"
    assert [event[0] for event in adapter.events] == [
        "load_map", "reset_pose", "plan_stop", "navigate", "plan_stop", "navigate"
    ]
    assert sum(event[0] == "load_map" for event in adapter.events) == 1


def test_single_step_pipeline_preserves_candidate_floor_provenance(runner):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    stop = {
        "stop_index": 2,
        "floor_id": 1,
        "floor_assignment_source": "crosschecked_map_floor",
        "candidate_targets": [
            {
                "target_id": "room-a",
                "floor_id": 1,
                "floor_assignment_source": "crosschecked_map_floor",
                "navigation_target": {
                    "position": [1.0, 2.0, 3.855],
                    "frame_id": "map",
                    "floor_id": 1,
                    "floor_assignment_source": "crosschecked_map_floor",
                },
            }
        ],
    }
    pipeline = runner.build_single_step_pipeline(stop)
    step = pipeline["task_plan"]["task_chain"][0]
    reference = pipeline["grounded_task"]["references"]["target"]
    assert step["candidate_node_ids"] == ["room-a"]
    assert step["step"] == 3
    assert reference["candidate_navigation_targets"][0]["navigation_target"]["floor_id"] == 1


def test_normalize_nav2_pose_accepts_portal_yaw_and_planner_quaternion(runner):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    portal = runner.normalize_nav2_pose(
        {"x": -1.0, "y": 2.0, "yaw": -0.5, "frame_id": "map"}
    )
    planned = runner.normalize_nav2_pose({
        "x": 3.0, "y": 4.0,
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.7071067812, "w": 0.7071067812},
    })
    assert portal["x"] == pytest.approx(-1.0)
    assert portal["y"] == pytest.approx(2.0)
    assert portal["yaw"] == pytest.approx(-0.5)
    assert portal["frame_id"] == "map"
    assert planned["yaw"] == pytest.approx(1.5707963268)


def test_point_robot_uses_point_cost_queries_and_footprint_mode_stays_default(runner):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    assert runner.costmap_uses_footprint("point") is False
    assert runner.costmap_uses_footprint("footprint") is True
    with pytest.raises(ValueError, match="robot_model"):
        runner.costmap_uses_footprint("sphere")


@pytest.mark.parametrize("cost,expected", [(0, True), (252, True), (253, False), (255, False)])
def test_live_point_start_cost_uses_nav2_lethal_threshold(runner, cost, expected):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    assert runner.costmap_cost_is_safe(cost) is expected


@pytest.mark.parametrize("cost", [True, -1, 256, 12.5])
def test_live_point_start_cost_rejects_invalid_cost_values(runner, cost):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    with pytest.raises(ValueError, match="cost"):
        runner.costmap_cost_is_safe(cost)


def test_costmap_yaw_accepts_projected_goal_quaternion_mapping(runner):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    assert runner.costmap_yaw({
        "x": 1.0, "y": 2.0,
        "orientation": {"x": 0.0, "y": 0.0,
                        "z": 0.7071067812, "w": 0.7071067812},
    }) == pytest.approx(1.5707963268)


def test_compute_path_action_success_is_normalized_for_existing_planner(runner):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    path = [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}]
    assert runner.normalize_compute_path_result(True, path) == {
        "status": "succeeded", "path": path}
    assert runner.normalize_compute_path_result(False, path) == {
        "status": "failed", "path": []}
    assert runner.normalize_compute_path_result(True, []) == {
        "status": "failed", "path": []}


def test_loaded_map_geometry_is_checked_against_bundle(runner):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    from types import SimpleNamespace as NS

    grid = NS(header=NS(frame_id="map"), info=NS(
        width=364, height=335, resolution=0.1,
        origin=NS(position=NS(x=-23.1, y=-20.6)),
    ))
    expected = {"width": 364, "height": 335, "resolution": 0.1,
                "origin": [-23.1, -20.6]}
    assert runner.grid_geometry_matches(grid, expected, "map")
    grid.info.origin.position.x = -20.3
    assert not runner.grid_geometry_matches(grid, expected, "map")


def test_global_costmap_service_metadata_must_match_active_floor(runner):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    from types import SimpleNamespace as NS

    costmap = NS(header=NS(frame_id="map"), metadata=NS(
        size_x=20, size_y=10, resolution=0.1,
        origin=NS(position=NS(x=-1.0, y=-2.0)),
    ))
    expected = {"width": 20, "height": 10, "resolution": 0.1,
                "origin": [-1.0, -2.0]}
    assert runner.costmap_metadata_matches(costmap, expected, "map")
    assert not runner.costmap_metadata_matches(costmap, {**expected, "width": 21}, "map")
    costmap.header.frame_id = "odom"
    assert not runner.costmap_metadata_matches(costmap, expected, "map")


def test_floor_target_planning_loads_hash_checked_occupancy_for_projection(runner):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    import yaml
    package = Path(__file__).resolve().parents[1]
    bundle_path = package / "config" / "hospital_p1000_floor_transitions.yaml"
    bundle = yaml.safe_load(bundle_path.read_text(encoding="utf-8"))
    floor = bundle["floors"].get("1", bundle["floors"].get(1))
    data = runner.load_floor_navigation_map(floor)
    values, width, height, resolution, origin = data
    assert width > 0 and height > 0
    assert len(values) == width * height
    assert resolution == pytest.approx(0.1)
    assert origin == pytest.approx([-23.1, -20.6])


def test_cross_floor_chain_switches_only_after_portal_navigation_succeeds(runner):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    adapter = FakeAdapter()
    result = runner.execute_compiled_mission(compiled_cross_floor(), adapter)
    assert result["status"] == "succeeded"
    assert [event[0] for event in adapter.events] == [
        "load_map", "reset_pose", "navigate", "load_map", "reset_pose",
        "plan_stop", "navigate",
    ]


def test_execution_emits_incremental_events_in_same_order_as_final_record(runner):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    adapter = FakeAdapter()
    progress = []
    result = runner.execute_compiled_mission(
        compiled_cross_floor(), adapter, event_callback=progress.append)
    assert result["status"] == "succeeded"
    assert progress == result["events"]
    assert [item["kind"] for item in progress] == [
        "navigate_to_entry", "load_map", "set_pose", "plan_stop",
        "navigate_to_stop",
    ]


def test_failed_portal_navigation_does_not_load_destination_map(runner):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    adapter = FakeAdapter(nav_results=[False])
    result = runner.execute_compiled_mission(compiled_cross_floor(), adapter)
    assert result["status"] == "failed"
    assert result["failure_stage"] == "navigate_to_entry"
    assert not any(event[0] == "load_map" and event[1] == 2 for event in adapter.events)


def test_failed_target_preflight_is_preserved_in_result_events(runner):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    diagnostic = {"status": "no_path", "planner_result": {
        "steps": [{"status": "no_costmap_safe_approach"}]}}
    result = runner.execute_compiled_mission(
        compiled_same_floor(), FakeAdapter(plan_results=[diagnostic]))
    assert result["status"] == "failed"
    assert result["failure_stage"] == "plan_stop"
    plan_events = [event for event in result["events"] if event["kind"] == "plan_stop"]
    assert len(plan_events) == 1
    assert plan_events[0]["result"] == diagnostic


@pytest.mark.parametrize("map_results,pose_results,stage", [
    ([True, False], [], "load_map"),
    ([True, True], [True, False], "set_pose"),
])
def test_transition_failures_stop_before_target_navigation(runner, map_results, pose_results, stage):
    assert runner is not None, "run_task_chain_nav2.py has not been implemented"
    adapter = FakeAdapter(map_results=map_results, pose_results=pose_results)
    result = runner.execute_compiled_mission(compiled_cross_floor(), adapter)
    assert result["status"] == "failed"
    assert result["failure_stage"] == stage
    assert not any(event[0] == "plan_stop" for event in adapter.events)
