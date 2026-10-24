from pathlib import Path

import yaml
import pytest


def test_point_profile_has_zero_geometry_and_retains_hospital_costmap():
    package = Path(__file__).resolve().parents[1]
    full = yaml.safe_load((package / "config/nav2_hospital.yaml").read_text())
    point = yaml.safe_load((package / "config/nav2_hospital_point.yaml").read_text())
    for name in ("local_costmap", "global_costmap"):
        full_costmap = full[name][name]["ros__parameters"]
        point_costmap = point[name][name]["ros__parameters"]
        assert point_costmap["footprint"] == "[]"
        assert point_costmap["robot_radius"] == 0.0
        assert point_costmap["footprint_padding"] == 0.0
        assert point_costmap["plugins"] == full_costmap["plugins"]
        assert point_costmap["inflation_layer"]["inflation_radius"] == 0.45
        assert point_costmap["static_layer"]["plugin"] == "nav2_costmap_2d::StaticLayer"
        assert point_costmap["inflation_layer"]["plugin"] == "nav2_costmap_2d::InflationLayer"


def test_point_profile_keeps_same_planner_and_controller_settings():
    package = Path(__file__).resolve().parents[1]
    full = yaml.safe_load((package / "config/nav2_hospital.yaml").read_text())
    point = yaml.safe_load((package / "config/nav2_hospital_point.yaml").read_text())
    assert point["planner_server"] == full["planner_server"]
    point_controller = point["controller_server"]
    full_controller = full["controller_server"]
    point_controller["ros__parameters"]["goal_checker"]["yaw_goal_tolerance"] = \
        full_controller["ros__parameters"]["goal_checker"]["yaw_goal_tolerance"]
    assert point_controller == full_controller


def test_point_profile_does_not_impose_semantically_unspecified_goal_heading():
    package = Path(__file__).resolve().parents[1]
    full = yaml.safe_load((package / "config/nav2_hospital.yaml").read_text())
    point = yaml.safe_load((package / "config/nav2_hospital_point.yaml").read_text())
    full_goal_checker = full["controller_server"]["ros__parameters"]["goal_checker"]
    point_goal_checker = point["controller_server"]["ros__parameters"]["goal_checker"]
    assert full_goal_checker["yaw_goal_tolerance"] == 0.35
    assert point_goal_checker["yaw_goal_tolerance"] == pytest.approx(3.141592653589793)
    assert point_goal_checker["xy_goal_tolerance"] == full_goal_checker["xy_goal_tolerance"] == 0.8


def test_point_local_costmap_uses_four_meter_rolling_window():
    package = Path(__file__).resolve().parents[1]
    point = yaml.safe_load((package / "config/nav2_hospital_point.yaml").read_text())
    local = point["local_costmap"]["local_costmap"]["ros__parameters"]

    assert local["rolling_window"] is True
    assert type(local["width"]) is int
    assert type(local["height"]) is int
    assert local["width"] == 4.0
    assert local["height"] == 4.0
    assert local["resolution"] == 0.1
    assert local["global_frame"] == "odom"
    assert local["robot_base_frame"] == "base_link"
    global_costmap = point["global_costmap"]["global_costmap"]["ros__parameters"]
    assert global_costmap["global_frame"] == "map"
    assert global_costmap["robot_base_frame"] == "base_link"
    assert local["footprint"] == "[]"
    assert local["robot_radius"] == 0.0
    assert local["inflation_layer"]["inflation_radius"] == 0.45
