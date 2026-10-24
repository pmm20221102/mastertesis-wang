from pathlib import Path
import sys

import yaml


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE / "scripts"
sys.path.insert(0, str(SCRIPTS))
from kinematic_pose_source import PoseState


def test_rviz_demo_profile_raises_only_the_desired_speed():
    point = yaml.safe_load((PACKAGE / "config/nav2_hospital_point.yaml").read_text())
    demo_path = PACKAGE / "config/nav2_hospital_rviz.yaml"
    assert demo_path.is_file(), "dedicated RViz speed profile is missing"
    demo = yaml.safe_load(demo_path.read_text())
    point_follow = point["controller_server"]["ros__parameters"]["FollowPath"]
    demo_follow = demo["controller_server"]["ros__parameters"]["FollowPath"]
    assert point_follow["desired_linear_vel"] == 0.15
    assert demo_follow["desired_linear_vel"] == 0.5
    assert {k: v for k, v in demo_follow.items() if k != "desired_linear_vel"} == {
        k: v for k, v in point_follow.items() if k != "desired_linear_vel"
    }
    assert demo["planner_server"] == point["planner_server"]
    assert demo["local_costmap"] == point["local_costmap"]
    assert demo["global_costmap"] == point["global_costmap"]


def test_kinematic_pose_source_can_accept_half_meter_per_second_target():
    state = PoseState()
    state.reset(0.0, 0.0, 0.0, now=0.0)
    state.command(0.5, 0.0, now=0.0)
    _, _, _, linear, _ = state.step(0.5, now=0.5)
    assert linear == 0.5


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
