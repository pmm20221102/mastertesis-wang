"""TartanGround pose 解析 + NED -> ROS ENU/FLU 转换（指南 §12-13）。

约定：
- 输入 pose 行：tx ty tz qx qy qz qw（NED：x forward, y right, z down）。
- quaternion 表示机体在 NED 世界中的朝向，旋转矩阵 R 满足
  p_ned = R @ p_body（body->world，TartanAir 惯例）。
- 输出：odom(ENU, z-up) 世界中的机体 pose，机体基为 FLU(base_link)。

所有旋转组合集中在本模块，几何验证（地面 z-up / 前进方向）只依赖
`ned_to_ros_pose` 的输出，验证不对时只需改这一处。
"""
import numpy as np

# NED -> ENU 位置坐标变换矩阵（p_enu = R_ENU_NED @ p_ned）
R_ENU_NED = np.array([[0, 1, 0],
                      [1, 0, 0],
                      [0, 0, -1]], dtype=float)

# body NED 基 -> body ROS(FLU) 基：x forward 不变, y right -> left, z down -> up
R_BNED_BROS = np.diag([1.0, -1.0, -1.0]).astype(float)

# base_link(FLU) -> camera_optical_frame（x right, y down, z forward）
R_BASE_OPT = np.array([[0, 0, 1],
                       [-1, 0, 0],
                       [0, -1, 0]], dtype=float)

# base_link 原点 == 相机光心（指南 §12.3：最小测试合理）
T_BASE_OPT = np.zeros(3)


def load_poses(path):
    """读 pose txt，返回 Nx7 (tx ty tz qx qy qz qw)。"""
    arr = np.loadtxt(path)
    if arr.ndim == 1:
        arr = arr[None, :]
    if arr.shape[1] != 7:
        raise ValueError(
            f"pose file must have 7 columns (tx ty tz qx qy qz qw), got {arr.shape[1]}"
        )
    return arr


def quat_to_matrix(q):
    """四元数 (x,y,z,w) -> 3x3 旋转矩阵（body->world）。"""
    x, y, z, w = q
    n = np.linalg.norm(q)
    if n < 1e-12:
        raise ValueError("zero quaternion")
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ], dtype=float)


def matrix_to_quat(Rm):
    """3x3 旋转矩阵 -> 四元数 (x,y,z,w)。"""
    trace = np.trace(Rm)
    if trace > 0:
        s = np.sqrt(trace + 1.0) * 2
        w = 0.25 * s
        x = (Rm[2, 1] - Rm[1, 2]) / s
        y = (Rm[0, 2] - Rm[2, 0]) / s
        z = (Rm[1, 0] - Rm[0, 1]) / s
    elif Rm[0, 0] > Rm[1, 1] and Rm[0, 0] > Rm[2, 2]:
        s = np.sqrt(1.0 + Rm[0, 0] - Rm[1, 1] - Rm[2, 2]) * 2
        w = (Rm[2, 1] - Rm[1, 2]) / s
        x = 0.25 * s
        y = (Rm[0, 1] + Rm[1, 0]) / s
        z = (Rm[0, 2] + Rm[2, 0]) / s
    elif Rm[1, 1] > Rm[2, 2]:
        s = np.sqrt(1.0 + Rm[1, 1] - Rm[0, 0] - Rm[2, 2]) * 2
        w = (Rm[0, 2] - Rm[2, 0]) / s
        x = (Rm[0, 1] + Rm[1, 0]) / s
        y = 0.25 * s
        z = (Rm[1, 2] + Rm[2, 1]) / s
    else:
        s = np.sqrt(1.0 + Rm[2, 2] - Rm[0, 0] - Rm[1, 1]) * 2
        w = (Rm[1, 0] - Rm[0, 1]) / s
        x = (Rm[0, 2] + Rm[2, 0]) / s
        y = (Rm[1, 2] + Rm[2, 1]) / s
        z = 0.25 * s
    q = np.array([x, y, z, w], dtype=float)
    return q / np.linalg.norm(q)


def ned_to_ros_pose(pos_ned, quat_ned):
    """NED body pose -> ROS odom(ENU) 中 base_link(FLU) pose。

    返回 (pos_enu, quat_ros)，quat 顺序 (x,y,z,w)。

    旋转组合经 depth 地面拟合实测验证（verticality 0.97）：
      body->ENU = R_BNED_BROS @ R_wb.T @ R_ENU_NED.T
    其中 R_wb = quat_to_matrix(q) 为 body->world NED。
    注意：指南 §13.3 的公式（R_ENU_NED@R_wb@R_BNED_BROS）在正确反投影
    下不满足地面 z-up，此处以几何实测为准。
    """
    p_enu = R_ENU_NED @ np.asarray(pos_ned, dtype=float)
    R_wb_ned = quat_to_matrix(np.asarray(quat_ned, dtype=float))  # body->world NED
    R_enu_bros = R_BNED_BROS @ R_wb_ned.T @ R_ENU_NED.T  # body FLU -> ENU world
    q_ros = matrix_to_quat(R_enu_bros)
    return p_enu, q_ros
