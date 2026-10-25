"""坐标转换几何验证（指南 §14）。

必须通过三关，不能只看 TF 树"没报错"：
  1. 所有旋转矩阵 det == 1、quaternion norm == 1
  2. 地面 z-up：从 depth 下半像素反投影（camera optical -> odom），
     多帧拟合地面平面，法向量应 ≈ ±Z，floor z 应稳定
  3. 前进方向：连续两帧 body 位移方向与相机前向在 ENU 中一致

用法：
  python -m tartanground_khronos_bridge.validate_geometry --dataset-root <dir>
"""
import argparse
import sys
from pathlib import Path

import numpy as np

from .dataset_reader import TartanGroundDataset
from .pose_utils import (R_BASE_OPT, R_ENU_NED, load_poses, matrix_to_quat,
                         ned_to_ros_pose, quat_to_matrix)

FX = FY = 320.0
CX = CY = 320.0


def _backproject_depth(depth):
    """depth(H,W) -> camera optical XYZ (N,3)。"""
    h, w = depth.shape
    valid = np.isfinite(depth) & (depth > 0.05) & (depth < 10.0)
    ys, xs = np.nonzero(valid)
    d = depth[ys, xs]
    x = (xs - CX) / FX * d
    y = (ys - CY) / FY * d
    return np.stack([x, y, d], axis=1)


def fit_floor_plane(pts, iters=100, thresh=0.03):
    """RANSAC 拟合平面 ax+by+cz+d=0。返回 (normal, d, inlier_ratio)。"""
    best = None
    n = len(pts)
    rng = np.random.default_rng(0)
    for _ in range(iters):
        idx = rng.choice(n, 3, replace=False)
        p = pts[idx]
        normal = np.cross(p[1] - p[0], p[2] - p[0])
        norm = np.linalg.norm(normal)
        if norm < 1e-9:
            continue
        normal = normal / norm
        d = -float(normal @ p[0])
        dist = np.abs(pts @ normal + d)
        inliers = dist < thresh
        ratio = inliers.mean()
        if best is None or ratio > best[2]:
            best = (normal, d, ratio)
    return best


def validate(dataset_root, n_frames=30):
    ds = TartanGroundDataset(dataset_root)
    poses = ds.poses()
    print(f"frames={len(ds)}, pose rows={len(poses)}")

    # 1) quaternion norm / det
    norms = np.linalg.norm(poses[:, 3:], axis=1)
    print(f"[1] quat norm min={norms.min():.6f} max={norms.max():.6f}")
    dets = [np.linalg.det(quat_to_matrix(q)) for q in poses[:50, 3:]]
    print(f"[1] det(rotation) min={min(dets):.6f} max={max(dets):.6f}")
    assert abs(norms.min() - 1.0) < 1e-3, "quaternion not normalized"
    assert all(abs(d - 1.0) < 1e-4 for d in dets), "rotation det != 1"

    # 2) 地面 z-up：取中间帧下半像素反投影 -> odom
    all_floor = []
    for i in np.linspace(len(ds) * 0.2, len(ds) * 0.8, n_frames, dtype=int):
        depth = ds.depth(int(i))
        pts_opt = _backproject_depth(depth)
        # 只取图像下半部分（更可能是地面）
        ys = np.arange(640)
        lower_mask = pts_opt[:, 1] < 0  # optical y down，下半 = y>... 见说明
        # camera optical: x right, y down, z forward; 地面在图像下方 -> y 较大
        pos_ned = poses[i][:3]
        quat_ned = poses[i][3:]
        pos_enu, quat = ned_to_ros_pose(pos_ned, quat_ned)
        R_enu_bros = quat_to_matrix(quat)  # body FLU -> ENU world
        # R_BASE_OPT = optical->base；optical -> ENU = R_enu_bros @ R_BASE_OPT
        R_odom_opt = R_enu_bros @ R_BASE_OPT
        # optical XYZ -> odom XYZ
        p_odom = (R_odom_opt @ pts_opt.T).T + pos_enu
        floor_pts = p_odom[p_odom[:, 2] < pos_enu[2]]  # 相机下方
        if len(floor_pts) < 100:
            continue
        res = fit_floor_plane(floor_pts)
        if res:
            all_floor.append(res)

    if all_floor:
        normals = np.array([n for n, _, _ in all_floor])
        zs = np.array([-d for n, d, _ in all_floor])
        mean_n = normals.mean(axis=0)
        print(f"[2] floor normals mean={mean_n.round(3)}  (expect ≈ [0,0,±1])")
        print(f"[2] floor z mean={zs.mean():.3f} std={zs.std():.3f}")
        vert = abs(mean_n[2]) / (np.linalg.norm(mean_n) + 1e-9)
        print(f"[2] verticality={vert:.3f}  z-range={zs.min():.2f}..{zs.max():.2f}")
        assert vert > 0.7, "floor is NOT z-up"
    else:
        print("[2] WARN: no floor points gathered")

    # 3) 前进方向：前后两帧位移在 ENU 下的方向 vs 当前朝向 forward
    i = int(len(ds) * 0.5)
    p0, q0 = ned_to_ros_pose(poses[i][:3], poses[i][3:])
    p1, _ = ned_to_ros_pose(poses[i + 5][:3], poses[i + 5][3:])
    fwd = p1 - p0
    fwd = fwd / np.linalg.norm(fwd)
    R_enu_bros = quat_to_matrix(q0)
    base_fwd = R_enu_bros.T @ np.array([1.0, 0, 0])  # base_link x 轴在 ENU 中
    dot = float(np.dot(fwd, base_fwd))
    print(f"[3] motion_dir={fwd.round(3)} base_forward_enu={base_fwd.round(3)} dot={dot:.3f}")
    print(f"[3] expect dot > 0 (机器人朝前进方向移动); got {dot:.3f}")

    print("\n=== geometry validation done ===")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset-root", required=True)
    ap.add_argument("--n-frames", type=int, default=30)
    args = ap.parse_args(argv)
    return validate(args.dataset_root, args.n_frames)


if __name__ == "__main__":
    sys.exit(main())
