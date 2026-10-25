"""TartanGround depth 解码（指南 §8）。

官方把 float32 meter depth 无损压成 HxWx4 uint8 PNG 存储。
解码方式：以 4-channel PNG 读入，整块 view("<f4") 还原 float32。
兼容旧版可能的 .npy 文件。
"""
import cv2
import numpy as np


def read_decode_depth(path):
    """读一个 depth 文件，返回 HxW float32（单位 meter）。"""
    depth_rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if depth_rgba is None:
        raise RuntimeError(f"Cannot read depth: {path}")

    if depth_rgba.ndim == 3 and depth_rgba.shape[2] == 4:
        depth = depth_rgba.view("<f4")
        depth = np.squeeze(depth, axis=-1)
        return depth.astype(np.float32, copy=False)

    if str(path).endswith(".npy"):
        return np.load(path).astype(np.float32)

    raise RuntimeError(
        f"Unexpected depth format: shape={depth_rgba.shape}, dtype={depth_rgba.dtype}"
    )
