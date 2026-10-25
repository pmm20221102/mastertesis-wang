from pathlib import Path
import yaml


PACKAGE = Path(__file__).resolve().parents[1]
BUNDLE = PACKAGE / "config/hospital_p1000_floor_transitions.yaml"


def test_multifloor_rviz_launch_uses_visualization_profile_and_frozen_bundle():
    launch_path = PACKAGE / "launch/hospital_nav2_multifloor_rviz.launch.py"
    assert launch_path.is_file()
    launch = launch_path.read_text(encoding="utf-8")
    assert "hospital_nav2.launch.py" in launch
    assert "nav2_hospital_rviz.yaml" in launch
    assert "hospital_floor_mosaic.py" in launch
    assert "hospital_nav2_multifloor.rviz" in launch
    assert "hospital_p1000_floor_transitions.yaml" in launch
    assert "run_task_chain_nav2.py" not in launch


def test_multifloor_rviz_config_shows_mosaic_and_live_navigation():
    config_path = PACKAGE / "rviz/hospital_nav2_multifloor.rviz"
    assert config_path.is_file()
    config = config_path.read_text(encoding="utf-8")
    for required in (
        "Fixed Frame: hospital_floor_mosaic",
        "Value: /hospital/floor_mosaic/f0/map",
        "Value: /hospital/floor_mosaic/f1/map",
        "Value: /hospital/floor_mosaic/f2/map",
        "Value: /hospital/floor_mosaic/f3/map",
        "Value: /hospital/floor_mosaic/markers",
        "Value: /plan",
        "Value: /local_plan",
        "nav2_rviz_plugins/Navigation 2",
        "nav2_rviz_plugins/GoalTool",
        "rviz_default_plugins/RobotModel",
        "rviz_default_plugins/TF",
        "Value: /global_costmap/costmap",
        "Value: /local_costmap/costmap",
        "Window Geometry:",
    ):
        assert required in config, f"missing RViz view element: {required}"


def test_mosaic_node_has_core_bundle_and_live_topics():
    node_path = PACKAGE / "scripts/hospital_floor_mosaic.py"
    assert node_path.is_file()
    source = node_path.read_text(encoding="utf-8")
    for required in (
        "/hospital/floor_mosaic/markers",
        "/hospital/multifloor/status",
        "hospital_floor_mosaic",
        "map_to_mosaic_translation",
        "identify_floor_by_geometry",
        "extract_room_labels",
        "mission_floor_sequence",
        "hospital_floor_room_labels",
        "visualization_route",
        "LINE_LIST",
        "mission_path",
        "map_grid_origin_in_mosaic",
    ):
        assert required in source
    assert 'f"/hospital/floor_mosaic/f{floor_id}/map"' in source
    assert "hospital_floor_stair_labels" not in source
    assert "LIVE STATUS:" not in source
    assert BUNDLE.is_file()
    assert "if rclpy.ok():" in source, "shutdown must tolerate SIGINT-triggered context shutdown"


def test_rviz_plan_declares_16_9_tiles_room_ids_route_sidebar_and_fixed_f0_demo():
    source = (PACKAGE / "scripts/floor_mosaic_core.py").read_text(encoding="utf-8")
    launch = (PACKAGE / "launch/hospital_nav2_multifloor_rviz.launch.py").read_text(
        encoding="utf-8")
    assert "tile_aspect: tuple[int, int] = (16, 9)" in source
    assert "HC-031" in launch and "mission_file" in launch
    assert "dsg_file" in launch and "dsg_path" in launch
    assert "14.85" in launch and "3.65" in launch


def test_multifloor_rviz_default_inputs_are_workspace_relative():
    launch = (PACKAGE / "launch/hospital_nav2_multifloor_rviz.launch.py").read_text(
        encoding="utf-8")
    assert "hospital-p1000-khronos-only" in launch
    assert 'KHRONOS_DSG = "experiment/map-generation/output/' in launch
    assert 'HC031_F0_DEMO = "experiment/four-floor-demo/' in launch


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
