import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from kinematic_pose_source import PoseState


def test_command_tracking_follows_predicted_constant_twist_arc():
    state = PoseState(apply_acceleration_limits=False)
    state.command(0.5, 0.6, now=0)
    x, y, yaw, v, w = state.step(0.1, now=0.1)
    assert math.isclose(x, 0.5 / 0.6 * math.sin(0.06))
    assert math.isclose(y, 0.5 / 0.6 * (1 - math.cos(0.06)))
    assert math.isclose(yaw, 0.06)
    assert (v, w) == (0.5, 0.6)


def test_command_tracking_stops_without_unpredicted_coasting():
    state = PoseState(apply_acceleration_limits=False)
    state.command(0.5, 0, now=0)
    state.step(0.1, now=0.1)
    before = (state.x, state.y)
    state.command(0, 0, now=0.1)
    state.step(0.1, now=0.2)
    assert (state.x, state.y) == before
    assert (state.v, state.w) == (0, 0)


def test_command_tracking_timeout_still_stops():
    state = PoseState(apply_acceleration_limits=False)
    state.command(0.5, 0.5, now=0)
    state.step(0.1, now=0.1)
    before = (state.x, state.y)
    state.step(0.1, now=1)
    assert (state.x, state.y) == before


def test_legacy_mode_retains_acceleration_limited_integration():
    state = PoseState(max_accel=1.0)
    state.command(0.5, 0.6, now=0)
    x, y, yaw, v, w = state.step(0.1, now=0.1)
    assert math.isclose(v, 0.1) and math.isclose(w, 0.1)
    assert math.isclose(x, 0.01) and y == 0
    assert math.isclose(yaw, 0.01)
