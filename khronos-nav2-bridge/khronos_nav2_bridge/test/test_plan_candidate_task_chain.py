"""Pure tests for the planning-only PipelineResult adapter."""

import importlib.util
import json
import hashlib
import time
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/plan_candidate_task_chain.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("plan_candidate_task_chain", SCRIPT)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def target(x, y):
    return {"position": [x, y, 0.0], "frame_id": "map", "goal_type": "place"}


def pipeline(steps, references, grounded_steps=None):
    return {
        "status": "valid",
        "task_plan": {"status": "valid", "task_chain": steps},
        "grounded_task": {"references": references, "steps": grounded_steps or []},
    }


def path(*xy):
    return {"status": "succeeded", "path": [{"x": x, "y": y} for x, y in xy]}


def map_with_blocked_goal():
    width = height = 60
    values = [254] * (width * height)
    goal_cell = (30, 30)
    values[(height - 1 - goal_cell[1]) * width + goal_cell[0]] = 0
    return values, width, height, 0.1, [0.0, 0.0]


def test_static_footprint_rejects_occupied_cell_intersecting_footprint_edge():
    from navigation_grid_geometry import safe

    width = height = 60
    values = [254] * (width * height)
    # This cell starts at x=3.4 m. The robot's right edge is x=3.43 m,
    # so the occupied cell intersects the footprint although its center
    # (x=3.45 m) lies outside the polygon.
    cell_x, cell_y = 34, 30
    values[(height - 1 - cell_y) * width + cell_x] = 0
    map_data = (values, width, height, 0.1, [0.0, 0.0])

    is_safe, reason = safe({"x": 3.05, "y": 3.05, "yaw": 0.0}, map_data)

    assert is_safe is False
    assert reason == "occupied"


def test_read_binary_pgm_raster(tmp_path):
    pgm = tmp_path / "map.pgm"
    pgm.write_bytes(b"P5\n3 2\n255\n" + bytes([0, 205, 254, 1, 2, 3]))
    values, width, height = adapter.read_pgm(pgm)
    assert (width, height) == (3, 2)
    assert values == [0, 205, 254, 1, 2, 3]


def test_projected_approaches_are_same_component_safe_and_within_two_meters():
    from navigation_grid_geometry import safe

    map_data = map_with_blocked_goal()
    original = {"x": 3.0, "y": 3.0, "orientation": {
        "x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0,
    }}
    start = {"x": 1.0, "y": 1.0, "yaw": 0.0}
    approaches = adapter.project_approach_goals(original, start, map_data)
    assert adapter.MAX_APPROACH_PROJECTION_M == 2.0
    assert approaches
    assert len(approaches) == 64
    assert all(item["projection_distance_m"] <= 2.0 for item in approaches)
    assert all(item["start_connected"] is True for item in approaches)
    assert all(safe({
        "x": item["goal"]["x"], "y": item["goal"]["y"],
        "yaw": adapter._yaw_from_quaternion(item["goal"]["orientation"]),
    }, map_data)[0] for item in approaches)


def test_costmap_rejected_static_safe_source_tries_bounded_fallbacks():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2",
         "target_ref": "target", "candidate_node_ids": ["room"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "room", "navigation_target": {
            "position": [3.0, 3.0, 0.04], "frame_id": "map",
            "floor_id": 0, "floor_assignment_source": "test",
        }},
    ]}})
    map_data = ([254] * (60 * 60), 60, 60, 0.1, [0.0, 0.0])

    def compute(start, goal):
        if abs(goal["x"] - 3.0) < 1e-8 and abs(goal["y"] - 3.0) < 1e-8:
            return {"status": "unsafe_goal_costmap", "path": [],
                    "costmap_footprint_cost": 253}
        return path((start["x"], start["y"]), (goal["x"], goal["y"]))

    result = adapter.plan_task_chain(
        data, {"x": 1.0, "y": 1.0}, compute, map_floor=0,
        map_floor_z=0.04, map_data=map_data,
        allow_costmap_fallbacks=True)
    step = result["steps"][0]
    assert result["status"] == "planning_only_complete"
    assert step["status"] == "path_found"
    assert step["projection"]["method"] == "bounded_approach_projection"
    assert 0.0 < step["projection"]["distance_m"] <= 2.0


def test_point_robot_projection_uses_connected_free_cell_not_polygon_footprint():
    from navigation_grid_geometry import safe

    width = height = 60
    values = [0] * (width * height)
    free_y = 20
    for free_x in range(10, 41):
        values[(height - 1 - free_y) * width + free_x] = 254
    map_data = (values, width, height, 0.1, [0.0, 0.0])
    target_pose = {"x": 2.55, "y": 2.05, "orientation": {
        "x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0,
    }}
    start = {"x": 1.05, "y": 2.05, "yaw": 0.0}

    assert safe({"x": target_pose["x"], "y": target_pose["y"], "yaw": 0.0},
                map_data)[0] is False
    goals = adapter.project_point_approach_goals(target_pose, start, map_data)

    assert goals
    assert any(item["goal"]["x"] == pytest.approx(2.55)
               and item["goal"]["y"] == pytest.approx(2.05) for item in goals)
    for item in goals:
        x, y = item["goal"]["x"], item["goal"]["y"]
        cell_x, cell_y = round(x / 0.1), round(y / 0.1)
        assert values[(height - 1 - cell_y) * width + cell_x] >= 250
        assert item["start_connected"] is True
        assert item["projection_distance_m"] <= 2.0


def test_point_robot_plan_skips_polygon_start_check_and_uses_point_projection():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2",
         "target_ref": "target", "candidate_node_ids": ["room"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "room", "navigation_target": {
            "position": [2.55, 2.05, 0.04], "frame_id": "map",
            "floor_id": 0, "floor_assignment_source": "test_point_robot",
        }},
    ]}})
    width = height = 60
    values = [0] * (width * height)
    for free_x in range(10, 41):
        values[(height - 1 - 20) * width + free_x] = 254
    map_data = (values, width, height, 0.1, [0.0, 0.0])
    compute = lambda start, goal: path((start["x"], start["y"]),
                                       (goal["x"], goal["y"]))

    result = adapter.plan_task_chain(
        data, {"x": 1.05, "y": 2.05}, compute,
        map_floor=0, map_floor_z=0.04, map_data=map_data,
        point_robot=True)

    assert result["status"] == "planning_only_complete"
    assert result["steps"][0]["status"] == "path_found"


def test_blocked_goal_plans_to_projected_approach_and_records_original():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["blocked"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "blocked", "navigation_target": {
            "position": [3.0, 3.0, 0.04], "frame_id": "map",
        }},
    ]}})
    calls = []

    def compute(start, goal):
        calls.append(goal)
        return path((start["x"], start["y"]), (goal["x"], goal["y"]))

    result = _plan_task_chain(data, {"x": 1.0, "y": 1.0}, compute,
                              map_floor_z=0.04, map_data=map_with_blocked_goal())
    candidate = result["steps"][0]["candidates"][0]
    assert candidate["status"] == "path_found"
    assert candidate["goal"]["x"] == 3.0
    assert candidate["projected_goal"] != candidate["goal"]
    assert candidate["projection"]["method"] == "bounded_approach_projection"
    assert 0.0 < candidate["projection"]["distance_m"] <= 2.0
    assert all(goal != candidate["goal"] for goal in calls)


def test_blocked_goal_without_start_connected_safe_approach_fails_before_nav2():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["blocked"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "blocked", "navigation_target": {
            "position": [4.0, 4.0, 0.04], "frame_id": "map",
        }},
    ]}})
    values = [0] * (60 * 60)
    for cell_y in range(5, 21):
        for cell_x in range(5, 21):
            values[(60 - 1 - cell_y) * 60 + cell_x] = 254
    map_data = (values, 60, 60, 0.1, [0.0, 0.0])
    result = _plan_task_chain(data, {"x": 1.0, "y": 1.0},
                              lambda *_: pytest.fail("no valid approach means no Nav2 request"),
                              map_floor_z=0.04, map_data=map_data)
    assert result["steps"][0]["candidates"][0]["status"] == "no_safe_approach_within_2m"
    assert result["steps"][0]["status"] == "no_safe_approach_within_2m"


def test_live_start_must_be_footprint_safe_on_active_map():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["blocked"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "blocked", "navigation_target": {
            "position": [2.0, 2.0, 0.04], "frame_id": "map",
        }},
    ]}})
    with pytest.raises(ValueError, match="live TF start is not footprint-safe"):
        _plan_task_chain(data, {"x": 3.0, "y": 3.0},
                         lambda *_: pytest.fail("unsafe start must not request a path"),
                         map_floor_z=0.04, map_data=map_with_blocked_goal())


@pytest.mark.parametrize("cost,expected", [
    (0, True), (252, True), (252.0, True), (253, False), (253.0, False),
    (254, False), (255, False),
])
def test_nav2_footprint_cost_safety_boundary(cost, expected):
    assert adapter.nav2_footprint_cost_is_safe(cost) is expected


def test_nav2_footprint_cost_rejects_invalid_values():
    with pytest.raises(ValueError, match="cost must be an integer"):
        adapter.nav2_footprint_cost_is_safe(True)
    with pytest.raises(ValueError, match="cost must be an integer"):
        adapter.nav2_footprint_cost_is_safe(252.5)
    with pytest.raises(ValueError, match="cost must be an integer"):
        adapter.nav2_footprint_cost_is_safe(float("nan"))
    with pytest.raises(ValueError, match="cost must be in"):
        adapter.nav2_footprint_cost_is_safe(256)


def test_costmap_unsafe_target_is_not_counted_as_path_failure():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["blocked"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "blocked", "navigation_target": target(1, 0)},
    ]}})
    result = _plan_task_chain(
        data, {"x": 0, "y": 0},
        lambda *_: {"status": "unsafe_goal_costmap", "path": [],
                    "costmap_footprint_cost": 254},
    )
    assert result["steps"][0]["candidates"][0]["status"] == "no_costmap_safe_approach"
    assert result["steps"][0]["candidates"][0]["approaches"][0]["costmap_footprint_cost"] == 254
    assert result["steps"][0]["status"] == "no_costmap_safe_approach"


_raw_plan_task_chain = adapter.plan_task_chain


def _plan_task_chain(*args, **kwargs):
    kwargs.setdefault("map_floor", 0)
    kwargs.setdefault("map_floor_z", 0.04)
    return _raw_plan_task_chain(*args, **kwargs)


adapter.plan_task_chain = _plan_task_chain


def test_load_floor_report_records_floor_identity_and_hash(tmp_path):
    pgm = tmp_path / "map.pgm"
    yaml = tmp_path / "map.yaml"
    pgm.write_bytes(b"pgm-content")
    yaml.write_text("image: map.pgm\nresolution: 0.1\n", encoding="utf-8")
    report = {
        "status": "GENERATED", "floor": 1, "floor_z": 3.88,
        "hashes": {
            "pgm": hashlib.sha256(pgm.read_bytes()).hexdigest(),
            "yaml": hashlib.sha256(yaml.read_bytes()).hexdigest(),
        },
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    loaded = adapter.load_floor_report(path)
    assert loaded["floor"] == 1
    assert loaded["floor_z"] == pytest.approx(3.88)
    assert loaded["report_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert loaded["map_yaml_path"] == str(yaml.resolve())
    assert loaded["map_pgm_sha256"] == hashlib.sha256(pgm.read_bytes()).hexdigest()
    assert loaded["map_yaml_sha256"] == hashlib.sha256(yaml.read_bytes()).hexdigest()


@pytest.mark.parametrize("report", [
    {"status": "STALE", "floor": 0, "floor_z": 0.04,
     "hashes": {"pgm": "a", "yaml": "b"}},
    {"status": "GENERATED", "floor": True, "floor_z": 0.04,
     "hashes": {"pgm": "a", "yaml": "b"}},
    {"status": "GENERATED", "floor": 0, "floor_z": float("nan"),
     "hashes": {"pgm": "a", "yaml": "b"}},
    {"status": "GENERATED", "floor": 0, "floor_z": True,
     "hashes": {"pgm": "a", "yaml": "b"}},
])
def test_load_floor_report_rejects_invalid_contract(tmp_path, report):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid generated map floor report"):
        adapter.load_floor_report(path)


def test_load_floor_report_rejects_map_asset_hash_mismatch(tmp_path):
    (tmp_path / "map.pgm").write_bytes(b"actual-pgm")
    (tmp_path / "map.yaml").write_bytes(b"actual-yaml")
    report = {
        "status": "GENERATED", "floor": 0, "floor_z": 0.04,
        "hashes": {"pgm": "wrong", "yaml": "wrong"},
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="floor report hashes do not match map files"):
        adapter.load_floor_report(path)


def test_load_floor_report_requires_yaml_to_reference_verified_pgm(tmp_path):
    pgm = tmp_path / "map.pgm"
    yaml = tmp_path / "map.yaml"
    other = tmp_path / "other.pgm"
    pgm.write_bytes(b"verified-pgm")
    other.write_bytes(b"unverified-pgm")
    yaml.write_text("image: other.pgm\nresolution: 0.1\n", encoding="utf-8")
    report = {
        "status": "GENERATED", "floor": 0, "floor_z": 0.04,
        "hashes": {
            "pgm": hashlib.sha256(pgm.read_bytes()).hexdigest(),
            "yaml": hashlib.sha256(yaml.read_bytes()).hexdigest(),
        },
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="YAML image does not match verified map.pgm"):
        adapter.load_floor_report(path)


def test_active_map_yaml_must_match_floor_report_map(tmp_path):
    expected = tmp_path / "floor_0" / "map.yaml"
    wrong = tmp_path / "floor_1" / "map.yaml"
    expected.parent.mkdir()
    wrong.parent.mkdir()
    expected.write_text("map zero", encoding="utf-8")
    wrong.write_text("map one", encoding="utf-8")
    adapter.verify_active_map_yaml(expected, expected)
    with pytest.raises(RuntimeError, match="active Nav2 map does not match floor report"):
        adapter.verify_active_map_yaml(expected, wrong)


def test_map_server_yaml_parameter_response_uses_ros_values_field():
    response = SimpleNamespace(values=[SimpleNamespace(type=4, string_value="/maps/floor_0/map.yaml")])
    assert adapter.map_server_yaml_filename(response, string_type=4) == "/maps/floor_0/map.yaml"


def test_invalid_map_server_yaml_parameter_response_fails_closed():
    with pytest.raises(RuntimeError, match="yaml_filename parameter response"):
        adapter.map_server_yaml_filename(SimpleNamespace(values=[]), string_type=4)


def test_candidate_choice_uses_geometric_length_and_stable_node_id_tie_break():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["z", "b", "a"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "z", "navigation_target": target(4, 0)},
        {"node_id": "b", "navigation_target": target(2, 0)},
        {"node_id": "a", "navigation_target": target(1, 0)},
    ]}})
    calls = []

    def compute(start, goal):
        calls.append((start, goal))
        if goal["x"] == 4:
            return {"status": "rejected", "path": []}
        if goal["x"] == 2:
            return path((0, 0), (0, 1), (1, 1))
        return path((0, 0), (1, 1), (1, 0))

    result = adapter.plan_task_chain(data, {"x": 0, "y": 0}, compute)
    step = result["steps"][0]
    assert step["status"] == "path_found"
    assert step["selected_node_id"] == "b"
    assert step["path_length_m"] == pytest.approx(2.0)
    assert step["path"] == [{"x": 0, "y": 0}, {"x": 0, "y": 1}, {"x": 1, "y": 1}]
    assert [c[1]["x"] for c in calls] == [1, 2, 4]
    assert all(c[0] == {"x": 0, "y": 0} for c in calls)
    assert result["status"] == "planning_only_complete"
    assert result["movement_executed"] is False
    assert result["planning_latency_ms"] >= 0
    assert step["planning_latency_ms"] >= 0
    assert all(candidate["planning_latency_ms"] >= 0 for candidate in step["candidates"])


def test_candidates_on_other_floors_are_not_sent_to_current_floor_planner():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["floor-0", "floor-1"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "floor-0", "navigation_target": {
            "position": [1.0, 1.0, 0.04], "frame_id": "map",
        }},
        {"node_id": "floor-1", "navigation_target": {
            "position": [1.0, 1.0, 3.88], "frame_id": "map",
        }},
    ]}})
    planned = []

    def compute(start, goal):
        planned.append(goal)
        return path((start["x"], start["y"]), (goal["x"], goal["y"]))

    result = adapter.plan_task_chain(data, {"x": 0, "y": 0}, compute,
                                     map_floor_z=0.04)
    candidates = result["steps"][0]["candidates"]
    assert [candidate["status"] for candidate in candidates] == ["path_found", "wrong_floor"]
    assert len(planned) == 1
    assert result["map_floor_z"] == 0.04


def test_duplicate_candidate_node_ids_are_rejected_not_overwritten():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["same"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "same", "navigation_target": {
            "position": [1.0, 1.0, 0.04], "frame_id": "map",
        }},
        {"node_id": "same", "navigation_target": {
            "position": [9.0, 9.0, 0.04], "frame_id": "map",
        }},
    ]}})
    with pytest.raises(ValueError, match="duplicate candidate node_id"):
        _plan_task_chain(data, {"x": 0, "y": 0},
                         lambda *_: pytest.fail("duplicate candidate must not be planned"))


def test_duplicate_ids_in_candidate_set_are_rejected():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["same", "same"]},
    ], {"target": {"candidate_navigation_targets": []}})
    with pytest.raises(ValueError, match="candidate_node_ids"):
        _plan_task_chain(data, {"x": 0, "y": 0},
                         lambda *_: pytest.fail("duplicate candidate IDs must not be planned"))


def test_boolean_map_floor_z_is_rejected():
    data = pipeline([], {})
    with pytest.raises(ValueError, match="map_floor_z"):
        _plan_task_chain(data, {"x": 0, "y": 0}, lambda *_: None,
                         map_floor_z=True)


def test_boolean_target_floor_z_is_rejected():
    assert adapter._target_floor_z({"position": [0.0, 0.0, True]}) is None


def test_duplicate_candidate_ids_are_rejected_even_for_non_nav_step():
    data = pipeline([
        {"step": 1, "action": "wait", "executor": "behavior", "target_ref": "",
         "candidate_node_ids": ["same", "same"]},
    ], {})
    with pytest.raises(ValueError, match="candidate_node_ids"):
        _plan_task_chain(data, {"x": 0, "y": 0},
                         lambda *_: pytest.fail("invalid IDs must fail before action dispatch"))


def test_all_wrong_floor_candidates_are_not_reported_as_nav2_unreachable():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["floor-1"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "floor-1", "navigation_target": {
            "position": [1.0, 1.0, 3.88], "frame_id": "map",
        }},
    ]}})
    result = _plan_task_chain(data, {"x": 0, "y": 0},
                              lambda *_: pytest.fail("wrong-floor target must not reach Nav2"))
    assert result["steps"][0]["status"] == "no_candidate_on_active_floor"
    assert result["steps"][0]["candidates"][0]["status"] == "wrong_floor"
    assert result["status"] == "planning_only_incomplete"


def test_planning_latency_includes_compute_path_wait():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["one"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "one", "navigation_target": target(1, 0)},
    ]}})

    def slow_compute(start, goal):
        time.sleep(0.02)
        return path((start["x"], start["y"]), (goal["x"], goal["y"]))

    result = _plan_task_chain(data, {"x": 0, "y": 0}, slow_compute)
    step = result["steps"][0]
    assert step["candidates"][0]["planning_latency_ms"] >= 15.0
    assert step["planning_latency_ms"] >= 15.0
    assert result["planning_latency_ms"] >= 15.0


@pytest.mark.parametrize("target_z,expected", [
    (0.25, "path_found"),
    (0.250001, "wrong_floor"),
])
def test_floor_z_tolerance_boundary(target_z, expected):
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["candidate"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "candidate", "navigation_target": {
            "position": [1.0, 1.0, target_z], "frame_id": "map",
        }},
    ]}})
    result = _plan_task_chain(
        data, {"x": 0, "y": 0},
        (lambda start, goal: path((start["x"], start["y"]), (goal["x"], goal["y"])))
        if expected == "path_found"
        else (lambda *_: pytest.fail("off-floor candidate must not be planned")),
        map_floor_z=0.0,
    )
    assert result["steps"][0]["candidates"][0]["status"] == expected


def test_rejected_and_empty_paths_are_infeasible():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["a", "b"]},
    ], {"target": {"candidate_navigation_targets": {
        "a": target(1, 0), "b": target(2, 0),
    }}})
    result = adapter.plan_task_chain(
        data, {"x": 0, "y": 0},
        lambda start, goal: {"status": "rejected", "path": []} if goal["x"] == 1
        else {"status": "succeeded", "path": []},
    )
    step = result["steps"][0]
    assert step["status"] == "no_feasible_path"
    assert [item["status"] for item in step["candidates"]] == ["rejected", "empty_path"]
    assert result["status"] == "planning_only_incomplete"


def test_overflowing_path_length_is_infeasible():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["extreme"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "extreme", "navigation_target": target(1.0, 0.0)},
    ]}})
    result = adapter.plan_task_chain(
        data, {"x": 0.0, "y": 0.0},
        lambda *_: path((-1e308, 0.0), (1e308, 0.0)),
    )
    candidate = result["steps"][0]["candidates"][0]
    assert candidate["status"] == "invalid_path_length"
    assert result["status"] == "planning_only_incomplete"


def test_finite_segments_with_overflowing_total_length_are_infeasible():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["extreme"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "extreme", "navigation_target": target(1.0, 0.0)},
    ]}})
    result = adapter.plan_task_chain(
        data, {"x": 0.0, "y": 0.0},
        lambda *_: path((0.0, 0.0), (1e308, 0.0), (0.0, 0.0)),
    )
    assert result["steps"][0]["candidates"][0]["status"] == "invalid_path_length"


def test_non_unit_goal_quaternion_is_rejected():
    navigation = target(1, 2)
    navigation["orientation"] = {"x": 0.0, "y": 0.0, "z": 1.0, "w": 1.0}
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "target_id": "one"},
    ], {"target": {"node_id": "one", "navigation_target": navigation}})
    result = adapter.plan_task_chain(data, {"x": 0.0, "y": 0.0},
                                     lambda *_: pytest.fail("invalid quaternion must not be planned"))
    assert result["steps"][0]["candidates"][0]["status"] == "invalid_navigation_target"


def test_unrepresentably_large_goal_coordinate_is_rejected_as_invalid_target():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["huge"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "huge", "navigation_target": target(10**1000, 0)},
    ]}})
    result = adapter.plan_task_chain(data, {"x": 0, "y": 0},
                                    lambda *_: pytest.fail("invalid goal must not be planned"))
    assert result["steps"][0]["candidates"][0]["status"] == "invalid_navigation_target"


def test_unrepresentably_large_live_tf_coordinate_is_rejected():
    with pytest.raises(ValueError, match="invalid live TF start"):
        adapter.plan_task_chain(pipeline([], {}), {"x": 10**1000, "y": 0},
                                lambda *_: pytest.fail("invalid start must not be planned"))


def test_unreasonable_finite_goal_coordinate_is_rejected():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["far"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "far", "navigation_target": target(1e100, 0)},
    ]}})
    result = adapter.plan_task_chain(data, {"x": 0, "y": 0},
                                    lambda *_: pytest.fail("unreasonable goal must not be planned"))
    assert result["steps"][0]["candidates"][0]["status"] == "invalid_navigation_target"


def test_unreasonable_finite_live_tf_coordinate_is_rejected():
    with pytest.raises(ValueError, match="invalid live TF start"):
        adapter.plan_task_chain(pipeline([], {}), {"x": 1e100, "y": 0},
                                lambda *_: pytest.fail("unreasonable start must not be planned"))


@pytest.mark.parametrize("orientation", [
    {"x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0},
    {"x": 0.0, "y": 0.0, "z": float("nan"), "w": 1.0},
    {"x": 0.0, "y": 0.0, "z": 1.0, "w": 1.0},
])
def test_invalid_live_tf_orientation_is_rejected(orientation):
    with pytest.raises(ValueError, match="invalid live TF orientation"):
        adapter.plan_task_chain(pipeline([], {}),
                                {"x": 0, "y": 0, "orientation": orientation},
                                lambda *_: pytest.fail("invalid TF orientation must not be planned"))


def test_malformed_position_dimension_is_rejected():
    navigation = target(1, 2)
    navigation["position"].append(3.0)
    assert adapter._goal_pose(navigation) is None


def test_two_dimensional_position_is_accepted():
    navigation = target(1, 2)
    navigation["position"] = [1.0, 2.0]
    assert adapter._goal_pose(navigation)["x"] == 1.0
    assert adapter._goal_pose(navigation)["y"] == 2.0


def test_coordinate_limit_boundary_is_inclusive():
    navigation = target(1_000_000.0, -1_000_000.0)
    assert adapter._goal_pose(navigation) is not None
    result = adapter.plan_task_chain(
        pipeline([], {}), {"x": 1_000_000.0, "y": -1_000_000.0}, lambda *_: None,
    )
    assert result["status"] == "planning_only_complete"


def test_malformed_candidate_target_entry_fails_closed():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["candidate"]},
    ], {"target": {"candidate_navigation_targets": [None]}})
    with pytest.raises(ValueError, match="candidate_navigation_targets"):
        adapter.plan_task_chain(data, {"x": 0, "y": 0},
                                lambda *_: pytest.fail("malformed candidate must not be planned"))


def test_candidate_target_entry_requires_node_id():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["candidate"]},
    ], {"target": {"candidate_navigation_targets": [
        {"navigation_target": target(1, 0)},
    ]}})
    with pytest.raises(ValueError, match="node_id"):
        adapter.plan_task_chain(data, {"x": 0, "y": 0},
                                lambda *_: pytest.fail("malformed candidate must not be planned"))


@pytest.mark.parametrize("entry", [
    {"node_id": "candidate"},
    {"node_id": "candidate", "navigation_target": "not-an-object"},
])
def test_candidate_target_requires_navigation_target_object(entry):
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["candidate"]},
    ], {"target": {"candidate_navigation_targets": [entry]}})
    with pytest.raises(ValueError, match="navigation_target"):
        adapter.plan_task_chain(data, {"x": 0, "y": 0},
                                lambda *_: pytest.fail("malformed target must not be planned"))


def test_out_of_bounds_planner_path_is_infeasible():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["candidate"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "candidate", "navigation_target": target(1, 0)},
    ]}})
    result = adapter.plan_task_chain(
        data, {"x": 0, "y": 0},
        lambda *_: path((0, 0), (1e100, 0)),
    )
    assert result["steps"][0]["candidates"][0]["status"] == "invalid_path_length"


def test_later_step_uses_previous_chosen_goal_as_hypothetical_start():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "target_id": "first"},
        {"step": 2, "action": "navigate_to", "executor": "nav2", "target_ref": "step_0_destination",
         "candidate_node_ids": ["second"]},
    ], {"target": {"node_id": "first", "navigation_target": target(1, 2)}}, [
        {"references": {"destination": {"candidate_navigation_targets": [
            {"node_id": "second", "navigation_target": target(5, 6)},
        ]}}},
    ])
    calls = []

    def compute(start, goal):
        calls.append((start, goal))
        return path((start["x"], start["y"]), (goal["x"], goal["y"]))

    result = adapter.plan_task_chain(data, {"x": 0, "y": 0}, compute)
    assert calls[0][0] == {"x": 0, "y": 0}
    assert {key: calls[1][0][key] for key in ("x", "y")} == {"x": 1, "y": 2}
    assert calls[1][0]["orientation"] == {"x": 0, "y": 0, "z": 0, "w": 1}
    assert result["steps"][0]["start_source"] == "live_tf_map_to_base_link"
    assert result["steps"][1]["start_source"] == "hypothetical_previous_goal"
    assert result["steps"][1]["actual_arrival_confirmed"] is False


def test_failed_leg_prevents_planning_later_leg_from_unreached_goal():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "target_id": "a"},
        {"step": 2, "action": "navigate_to", "executor": "nav2", "target_ref": "destination",
         "target_id": "b"},
    ], {"target": {"node_id": "a", "navigation_target": target(1, 0)},
        "destination": {"node_id": "b", "navigation_target": target(2, 0)}})
    calls = []

    def compute(start, goal):
        calls.append(goal)
        return {"status": "aborted", "path": []}

    result = adapter.plan_task_chain(data, {"x": 0, "y": 0}, compute)
    assert len(calls) == 1
    assert [step["status"] for step in result["steps"]] == [
        "no_feasible_path", "blocked_by_prior_step",
    ]


def test_non_navigation_steps_are_reported_without_path_calls():
    data = pipeline([
        {"step": 1, "action": "locate_entity", "executor": "scene_graph", "target_ref": "target"},
    ], {})
    result = adapter.plan_task_chain(data, {"x": 0, "y": 0}, lambda *_: pytest.fail("path call"))
    assert result["steps"][0]["status"] == "not_navigation"


def test_invalid_pipeline_status_fails_closed():
    data = pipeline([], {})
    data["status"] = "ambiguous"
    with pytest.raises(ValueError, match="PipelineResult status"):
        adapter.plan_task_chain(data, {"x": 0, "y": 0}, lambda *_: None)


@pytest.mark.parametrize("bad_step", [None, "not-an-object", []])
def test_malformed_task_step_fails_with_value_error(bad_step):
    with pytest.raises(ValueError, match="task_chain step"):
        adapter.plan_task_chain(pipeline([bad_step], {}), {"x": 0, "y": 0},
                                lambda *_: pytest.fail("malformed step must not be planned"))


def test_non_string_candidate_id_fails_closed():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": [{"not": "an id"}]},
    ], {"target": {"candidate_navigation_targets": []}})
    with pytest.raises(ValueError, match="candidate_node_ids"):
        adapter.plan_task_chain(data, {"x": 0, "y": 0},
                                lambda *_: pytest.fail("invalid candidate id must not be planned"))


@pytest.mark.parametrize("candidate_ids", ["candidate", [""]])
def test_malformed_candidate_id_collection_fails_closed(candidate_ids):
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": candidate_ids},
    ], {"target": {"candidate_navigation_targets": []}})
    with pytest.raises(ValueError, match="candidate_node_ids"):
        adapter.plan_task_chain(data, {"x": 0, "y": 0},
                                lambda *_: pytest.fail("invalid candidate IDs must not be planned"))


def test_planning_only_adapter_does_not_import_navigation_execution_actions():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "ComputePathToPose" in source
    assert "FollowWaypoints" not in source
    assert "NavigateToPose" not in source
    cmake = SCRIPT.parents[1] / "CMakeLists.txt"
    if cmake.is_file():
        installed = cmake.read_text(encoding="utf-8")
        assert "scripts/navigation_grid_geometry.py" in installed
        assert "scripts/prepare_region_waypoint_missions.py" in installed


def test_ros_package_installs_planning_only_cli():
    cmake = SCRIPT.parents[1] / "CMakeLists.txt"
    if not cmake.is_file():
        pytest.skip("shared package CMakeLists is absent from the isolated worktree")
    assert "scripts/plan_candidate_task_chain.py" in cmake.read_text(encoding="utf-8")


def test_ros_context_is_shutdown_if_planner_constructor_raises(monkeypatch):
    import sys
    import weakref
    from types import ModuleType

    calls = []
    rclpy = ModuleType("rclpy")
    rclpy.init = lambda: calls.append("init")
    rclpy.ok = lambda: True
    rclpy.shutdown = lambda: calls.append("shutdown")
    node_module = ModuleType("rclpy.node")

    class Node:
        def __init__(self, _name):
            self._executor_ref = None

        @property
        def executor(self):
            return self._executor_ref() if self._executor_ref is not None else None

        @executor.setter
        def executor(self, value):
            if value is None:
                self._executor_ref = None
            else:
                value.add_node(self)
                self._executor_ref = weakref.ref(value)

        def destroy_node(self):
            calls.append("destroy_node")

        def create_client(self, *_args):
            return SimpleNamespace(wait_for_service=lambda **_kwargs: True)

    node_module.Node = Node
    action_module = ModuleType("rclpy.action")

    class ActionClient:
        def __init__(self, *_args):
            raise RuntimeError("simulated planner construction failure")

    action_module.ActionClient = ActionClient
    executor_module = ModuleType("rclpy.executors")

    class SingleThreadedExecutor:
        def add_node(self, _node):
            pass

        def remove_node(self, _node):
            calls.append("remove_node")

        def shutdown(self):
            calls.append("executor_shutdown")

    executor_module.SingleThreadedExecutor = SingleThreadedExecutor
    parameter_module = ModuleType("rclpy.parameter_client")
    parameter_module.AsyncParameterClient = type("AsyncParameterClient", (), {
        "__init__": lambda self, *_args: None,
    })
    tf2_ros = ModuleType("tf2_ros")
    tf2_ros.Buffer = type("Buffer", (), {})
    tf2_ros.TransformListener = lambda *_args, **_kwargs: object()
    action_msgs_msg = ModuleType("action_msgs.msg")
    action_msgs_msg.GoalStatus = type("GoalStatus", (), {})
    geometry_msgs_msg = ModuleType("geometry_msgs.msg")
    geometry_msgs_msg.PoseStamped = type("PoseStamped", (), {})
    rcl_interfaces_msg = ModuleType("rcl_interfaces.msg")
    rcl_interfaces_msg.ParameterType = type("ParameterType", (), {"PARAMETER_STRING": 4})
    nav2_msgs_action = ModuleType("nav2_msgs.action")
    nav2_msgs_action.ComputePathToPose = type("ComputePathToPose", (), {})
    nav2_msgs_srv = ModuleType("nav2_msgs.srv")
    nav2_msgs_srv.GetCost = type("GetCost", (), {"Request": type("Request", (), {})})
    for name, module in {
        "rclpy": rclpy,
        "rclpy.node": node_module,
        "rclpy.action": action_module,
        "rclpy.executors": executor_module,
        "rclpy.parameter_client": parameter_module,
        "tf2_ros": tf2_ros,
        "action_msgs.msg": action_msgs_msg,
        "geometry_msgs.msg": geometry_msgs_msg,
        "rcl_interfaces.msg": rcl_interfaces_msg,
        "nav2_msgs.action": nav2_msgs_action,
        "nav2_msgs.srv": nav2_msgs_srv,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(adapter, "load_floor_map", lambda _contract: object())

    with pytest.raises(RuntimeError, match="construction failure"):
        adapter.ros_plan({}, timeout=1.0, planner_id="GridBased",
                         floor_contract={"floor": 0, "floor_z": 0.04,
                                         "report_path": "/tmp/report.json",
                                         "report_sha256": "0" * 64,
                                         "map_yaml_path": "/tmp/map.yaml",
                                         "map_pgm_sha256": "1" * 64,
                                         "map_yaml_sha256": "2" * 64})
    assert calls == ["init", "remove_node", "executor_shutdown", "destroy_node", "shutdown"]


def test_every_first_leg_candidate_keeps_live_tf_orientation():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["b", "a"]},
    ], {"target": {"candidate_navigation_targets": {
        "a": target(1, 0), "b": target(2, 0),
    }}})
    starts = []

    def compute(start, goal):
        starts.append(start)
        return path((0, 0), (goal["x"], goal["y"]))

    tf_orientation = SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0)
    result = adapter.plan_task_chain(
        data, {"x": 0, "y": 0, "orientation": tf_orientation}, compute,
    )
    assert [start["orientation"] for start in starts] == [
        {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    ]
    assert result["steps"][0]["start"] == {"x": 0, "y": 0}


def test_live_tf_orientation_accepts_ros_quaternion_message():
    geometry_msgs = pytest.importorskip("geometry_msgs.msg")
    quaternion = geometry_msgs.Quaternion()
    assert adapter._validated_quaternion(quaternion) == {
        "x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0,
    }


def test_equal_length_candidates_choose_lexicographically_first_node_id():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["z", "a"]},
    ], {"target": {"candidate_navigation_targets": {
        "z": target(1, 0), "a": target(-1, 0),
    }}})
    result = adapter.plan_task_chain(
        data, {"x": 0, "y": 0},
        lambda start, goal: path((0, 0), (goal["x"], goal["y"])),
    )
    assert result["steps"][0]["selected_node_id"] == "a"


@pytest.mark.parametrize("action", ["escort_to", "guide_to"])
def test_multi_role_destination_candidate_set_uses_destination_role_ref(action):
    data = pipeline([
        {"step": 1, "action": action, "executor": "nav2", "target_ref": "",
         "role_refs": {"actor": "actor", "destination": "step_0_destination"},
         "candidate_node_ids": ["far", "near"]},
    ], {"actor": {"node_id": "person", "navigation_target": target(50, 0)}}, [
        {"references": {"destination": {"candidate_navigation_targets": [
            {"node_id": "far", "navigation_target": target(3, 0)},
            {"node_id": "near", "navigation_target": target(1, 0)},
        ]}}},
    ])
    goals = []

    def compute(start, goal):
        goals.append(goal)
        return path((start["x"], start["y"]), (goal["x"], goal["y"]))

    result = adapter.plan_task_chain(data, {"x": 0, "y": 0}, compute)
    assert [goal["x"] for goal in goals] == [3, 1]
    assert result["steps"][0]["selected_node_id"] == "near"
    assert result["steps"][0]["status"] == "path_found"


def test_state_uncertain_error_stops_before_next_candidate():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["a", "b"]},
    ], {"target": {"candidate_navigation_targets": {
        "a": target(1, 0), "b": target(2, 0),
    }}})
    calls = []

    def compute(start, goal):
        calls.append(goal)
        raise adapter.PlannerStateUncertainError("acceptance timed out")

    with pytest.raises(adapter.PlannerStateUncertainError, match="acceptance"):
        adapter.plan_task_chain(data, {"x": 0, "y": 0}, compute)
    assert len(calls) == 1


def test_accepted_goal_timeout_cancels_and_confirms_terminal_canceled_state():
    events = []
    result_future = object()
    cancel_future = object()

    class Handle:
        accepted = True
        goal_id = "goal-1"

        def get_result_async(self):
            return result_future

        def cancel_goal_async(self):
            events.append("cancel")
            return cancel_future

    handle = Handle()

    class Client:
        def send_goal_async(self, request):
            events.append("send")
            return "acceptance-future"

    result_waits = 0

    def wait(future, label):
        nonlocal result_waits
        events.append(label)
        if future == "acceptance-future":
            return handle
        if future is cancel_future:
            return SimpleNamespace(goals_canceling=[SimpleNamespace(goal_id="goal-1")])
        if future is result_future:
            result_waits += 1
            if result_waits == 1:
                raise TimeoutError("result timeout")
            return SimpleNamespace(status=5)
        pytest.fail("unexpected future")

    response = adapter._request_path(Client(), object(), wait, 4, 5)
    assert response == {"status": "canceled_after_timeout", "path": []}
    assert events == ["send", "goal acceptance", "path result", "cancel",
                      "goal cancellation", "canceled goal result"]


def test_accepted_goal_result_exception_cancels_and_confirms_terminal_state():
    events = []
    result_future = object()
    cancel_future = object()

    class Handle:
        accepted = True
        goal_id = "goal-1"

        def get_result_async(self):
            return result_future

        def cancel_goal_async(self):
            events.append("cancel")
            return cancel_future

    class Client:
        def send_goal_async(self, request):
            return "acceptance-future"

    def wait(future, label):
        events.append(label)
        if future == "acceptance-future":
            return Handle()
        if future is result_future and label == "path result":
            raise RuntimeError("transient result future error")
        if future is cancel_future:
            return SimpleNamespace(goals_canceling=[SimpleNamespace(goal_id="goal-1")])
        if future is result_future:
            return SimpleNamespace(status=5)
        pytest.fail("unexpected future")

    response = adapter._request_path(Client(), object(), wait, 4, 5)
    assert response["status"] == "planner_error_after_cancel"
    assert events == ["goal acceptance", "path result", "cancel",
                      "goal cancellation", "canceled goal result"]


def test_goal_finishing_during_cancel_race_uses_confirmed_terminal_result():
    events = []
    result_future = object()
    cancel_future = object()
    wrapped = SimpleNamespace(status=4, result=SimpleNamespace(path=SimpleNamespace(poses=[
        SimpleNamespace(pose=SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0))),
        SimpleNamespace(pose=SimpleNamespace(position=SimpleNamespace(x=1.0, y=2.0))),
    ])))

    class Handle:
        accepted = True
        goal_id = "goal-1"

        def get_result_async(self):
            return result_future

        def cancel_goal_async(self):
            events.append("cancel")
            return cancel_future

    class Client:
        def send_goal_async(self, request):
            return "acceptance-future"

    def wait(future, label):
        events.append(label)
        if future == "acceptance-future":
            return Handle()
        if future is result_future and label == "path result":
            raise TimeoutError("result deadline raced with completion")
        if future is cancel_future:
            return SimpleNamespace(goals_canceling=[])
        if future is result_future:
            return wrapped
        pytest.fail("unexpected future")

    response = adapter._request_path(Client(), object(), wait, 4, 5)
    assert response == {"status": "succeeded", "path": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 2.0}]}
    assert events == ["goal acceptance", "path result", "cancel",
                      "goal cancellation", "goal result after cancel race"]


@pytest.mark.parametrize("failure", ["no_ack", "no_terminal", "wrong_goal"])
def test_unconfirmed_cancellation_is_fatal(failure):
    result_future = object()
    cancel_future = object()

    class Handle:
        accepted = True
        goal_id = "goal-1"

        def get_result_async(self):
            return result_future

        def cancel_goal_async(self):
            return cancel_future

    class Client:
        def send_goal_async(self, request):
            return "acceptance-future"

    def wait(future, label):
        if label == "goal acceptance":
            return Handle()
        if label == "path result":
            raise TimeoutError("result timeout")
        if label == "goal cancellation":
            if failure == "no_ack":
                raise TimeoutError("cancel timeout")
            goal_id = "other" if failure == "wrong_goal" else "goal-1"
            return SimpleNamespace(goals_canceling=[SimpleNamespace(goal_id=goal_id)])
        if label in ("canceled goal result", "goal result after cancel race"):
            raise TimeoutError("terminal result timeout")
        pytest.fail("unexpected wait")

    with pytest.raises(adapter.PlannerStateUncertainError):
        adapter._request_path(Client(), object(), wait, 4, 5)


def test_goal_acceptance_timeout_is_fatal_without_sending_next_goal():
    class Client:
        def send_goal_async(self, request):
            return "acceptance-future"

    def wait(future, label):
        raise TimeoutError("acceptance timeout")

    with pytest.raises(adapter.PlannerStateUncertainError, match="acceptance"):
        adapter._request_path(Client(), object(), wait, 4, 5)


def test_accepted_goal_without_result_future_is_fatal():
    class Handle:
        accepted = True

        def get_result_async(self):
            raise RuntimeError("result future unavailable")

    class Client:
        def send_goal_async(self, request):
            return "acceptance-future"

    with pytest.raises(adapter.PlannerStateUncertainError, match="result"):
        adapter._request_path(Client(), object(), lambda *_: Handle(), 4, 5)


@pytest.mark.parametrize("status", [0, None])
def test_unknown_result_status_is_fatal(status):
    class Handle:
        accepted = True

        def get_result_async(self):
            return "result-future"

    class Client:
        def send_goal_async(self, request):
            return "acceptance-future"

    def wait(future, label):
        if label == "goal acceptance":
            return Handle()
        return SimpleNamespace(status=status) if status is not None else SimpleNamespace()

    with pytest.raises(adapter.PlannerStateUncertainError, match="terminal"):
        adapter._request_path(Client(), object(), wait, 4, 5)


def test_goal_handle_without_acceptance_state_is_fatal():
    class Client:
        def send_goal_async(self, request):
            return "acceptance-future"

    with pytest.raises(adapter.PlannerStateUncertainError, match="acceptance"):
        adapter._request_path(Client(), object(), lambda *_: object(), 4, 5)


@pytest.mark.parametrize("field,value,source,expected", [
    ("orientation", {"x": 0, "y": 0, "z": 0.5, "w": 0.8660254038},
     "navigation_target_quaternion", {"x": 0, "y": 0, "z": 0.5, "w": 0.8660254038}),
    ("quaternion", {"x": 0, "y": 0, "z": 0.5, "w": 0.8660254038},
     "navigation_target_quaternion", {"x": 0, "y": 0, "z": 0.5, "w": 0.8660254038}),
    ("yaw", 1.5707963267948966, "navigation_target_yaw",
     {"x": 0, "y": 0, "z": 0.7071067811865475, "w": 0.7071067811865476}),
    (None, None, "default_identity", {"x": 0, "y": 0, "z": 0, "w": 1}),
])
def test_goal_orientation_is_preserved_or_defaulted_with_source(field, value, source, expected):
    navigation = target(1, 2)
    if field:
        navigation[field] = value
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "target_id": "one"},
    ], {"target": {"node_id": "one", "navigation_target": navigation}})
    goals = []

    def compute(start, goal):
        goals.append(goal)
        return path((0, 0), (goal["x"], goal["y"]))

    result = adapter.plan_task_chain(data, {"x": 0, "y": 0}, compute)
    assert goals[0]["orientation"] == pytest.approx(expected)
    assert result["steps"][0]["goal"]["orientation_source"] == source


@pytest.mark.parametrize("frame", [None, "odom"])
def test_missing_or_non_map_target_frame_is_rejected(frame):
    navigation = target(1, 2)
    if frame is None:
        del navigation["frame_id"]
    else:
        navigation["frame_id"] = frame
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "target_id": "one"},
    ], {"target": {"node_id": "one", "navigation_target": navigation}})
    result = adapter.plan_task_chain(data, {"x": 0, "y": 0},
                                     lambda *_: pytest.fail("path call"))
    assert result["steps"][0]["candidates"][0]["status"] == "invalid_navigation_target"


def test_explicit_floor_id_allows_room_center_height_offset():
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["room-f1"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "room-f1", "navigation_target": {
            "position": [1.0, 1.0, 3.855], "frame_id": "map",
            "floor_id": 1, "floor_assignment_source": "map_bundle_candidate_floor",
        }},
    ]}})
    result = _plan_task_chain(
        data, {"x": 0.0, "y": 0.0},
        lambda start, goal: path((start["x"], start["y"]), (goal["x"], goal["y"])),
        map_floor=1, map_floor_z=2.3,
    )

    candidate = result["steps"][0]["candidates"][0]
    assert candidate["status"] == "path_found"
    assert candidate["floor_assignment_source"] == "map_bundle_candidate_floor"
    assert candidate["floor_z_delta_m"] == pytest.approx(1.555)


@pytest.mark.parametrize("floor_id,source,expected", [
    (2, "map_bundle_candidate_floor", "wrong_floor"),
    (1, "", "invalid_floor_assignment"),
    (True, "map_bundle_candidate_floor", "invalid_floor_assignment"),
])
def test_explicit_floor_metadata_is_validated_before_path_planning(
    floor_id, source, expected
):
    data = pipeline([
        {"step": 1, "action": "navigate_to", "executor": "nav2", "target_ref": "target",
         "candidate_node_ids": ["candidate"]},
    ], {"target": {"candidate_navigation_targets": [
        {"node_id": "candidate", "navigation_target": {
            "position": [1.0, 1.0, 2.3], "frame_id": "map",
            "floor_id": floor_id, "floor_assignment_source": source,
        }},
    ]}})
    result = _plan_task_chain(
        data, {"x": 0.0, "y": 0.0},
        lambda *_: pytest.fail("invalid or mismatched explicit floor must not reach Nav2"),
        map_floor=1, map_floor_z=2.3,
    )
    assert result["steps"][0]["candidates"][0]["status"] == expected
