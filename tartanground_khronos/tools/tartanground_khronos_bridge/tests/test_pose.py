"""Pose 解析与 NED->ENU 转换测试（指南 §38 test_pose）。"""
import numpy as np
import pytest

from tartanground_khronos_bridge.pose_utils import (load_poses, ned_to_ros_pose,
                                                    quat_to_matrix)


def test_pose_seven_columns(dataset):
    poses = dataset.poses()
    assert poses.ndim == 2 and poses.shape[1] == 7


def test_pose_count_matches_frames(dataset):
    assert len(dataset.poses()) == len(dataset)


def test_quaternion_normalized(dataset):
    norms = np.linalg.norm(dataset.poses()[:, 3:], axis=1)
    assert abs(norms.min() - 1.0) < 1e-3
    assert abs(norms.max() - 1.0) < 1e-3


def test_rotation_determinant(dataset):
    for q in dataset.poses()[:20, 3:]:
        R = quat_to_matrix(q)
        assert abs(np.linalg.det(R) - 1.0) < 1e-4


def test_ned_to_enu_rotation_valid(dataset):
    pos_ned = dataset.poses()[0][:3]
    quat_ned = dataset.poses()[0][3:]
    p_enu, q_ros = ned_to_ros_pose(pos_ned, quat_ned)
    assert np.linalg.norm(q_ros) - 1.0 < 1e-3
    # z 应接近 -z_ned（NED down -> ENU up）
    assert abs(p_enu[2] - (-pos_ned[2])) < 1e-6
    # x/y 交换：x_enu = y_ned, y_enu = x_ned
    assert abs(p_enu[0] - pos_ned[1]) < 1e-6
    assert abs(p_enu[1] - pos_ned[0]) < 1e-6


def test_load_poses_format(tmp_path):
    f = tmp_path / "pose.txt"
    f.write_text("1 2 3 0 0 0 1\n4 5 6 0 0 0 1\n")
    arr = load_poses(f)
    assert arr.shape == (2, 7)
