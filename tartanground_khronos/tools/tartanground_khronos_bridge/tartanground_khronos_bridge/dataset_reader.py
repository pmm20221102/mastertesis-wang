"""TartanGround 数据集读取（扁平解压布局，指南 §1/§7）。

实际解压结构（医院 P1000 lcam_front 实测）：
  dataset_root/
    image_lcam_front/000000_lcam_front.png
    depth_lcam_front/000000_lcam_front_depth.png
    seg_lcam_front/000000_lcam_front_seg.png
    pose_lcam_front.txt
    seg_label_map.json
    P1000_metadata.json

不做文件名硬编码假设：按数字 frame index 排序，并对齐三路 modality。
"""
import json
import re
from pathlib import Path

import cv2
import numpy as np

from .depth_codec import read_decode_depth


def _frame_index(name):
    m = re.match(r"(\d+)", Path(name).stem)
    return int(m.group(1)) if m else -1


class TartanGroundDataset:
    """定位并读取单个 trajectory、单个相机的数据。"""

    def __init__(self, dataset_root, camera="lcam_front"):
        self.root = Path(dataset_root)
        self.camera = camera
        self.image_dir = self.root / f"image_{camera}"
        self.depth_dir = self.root / f"depth_{camera}"
        self.seg_dir = self.root / f"seg_{camera}"
        self.pose_file = self.root / f"pose_{camera}.txt"

        for d in (self.image_dir, self.depth_dir, self.seg_dir):
            if not d.is_dir():
                raise FileNotFoundError(f"missing modality dir: {d}")
        if not self.pose_file.is_file():
            raise FileNotFoundError(f"missing pose file: {self.pose_file}")

        # 帧索引以 image 目录为准（depth/seg 必须同名数）
        self.frames = sorted(
            (f for f in self.image_dir.glob("*.png") if _frame_index(f.name) >= 0),
            key=lambda f: _frame_index(f.name),
        )
        if not self.frames:
            raise RuntimeError(f"no images found in {self.image_dir}")

        self._poses = None

    def __len__(self):
        return len(self.frames)

    def frame_index(self, i):
        """第 i 帧在文件名里的数字 index（通常 == i，但以实测为准）。"""
        return _frame_index(self.frames[i].name)

    def rgb(self, i):
        """返回 HxWx3 uint8 RGB。"""
        bgr = cv2.imread(str(self.frames[i]), cv2.IMREAD_COLOR)
        if bgr is None:
            raise RuntimeError(f"Cannot read rgb: {self.frames[i]}")
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def depth(self, i):
        """返回 HxW float32 depth（meter）。"""
        idx = self.frame_index(i)
        p = self.depth_dir / f"{idx:06d}_{self.camera}_depth.png"
        if not p.is_file():
            raise RuntimeError(f"depth frame not found: {p}")
        return read_decode_depth(p)

    def seg(self, i):
        """返回 HxW uint8（raw semantic ID）。"""
        idx = self.frame_index(i)
        p = self.seg_dir / f"{idx:06d}_{self.camera}_seg.png"
        if not p.is_file():
            raise RuntimeError(f"seg frame not found: {p}")
        img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise RuntimeError(f"Cannot read seg: {p}")
        return img

    def poses(self):
        """返回 Nx7 pose 数组（与 frames 对齐）。"""
        if self._poses is None:
            from .pose_utils import load_poses
            self._poses = load_poses(self.pose_file)
            if len(self._poses) != len(self.frames):
                raise RuntimeError(
                    f"pose count {len(self._poses)} != image count {len(self.frames)}"
                )
        return self._poses

    def pose(self, i):
        return self.poses()[i]

    def metadata(self):
        """返回 P1000_metadata.json（若存在）。"""
        p = self.root / "P1000_metadata.json"
        if p.is_file():
            return json.load(open(p, encoding="utf-8"))
        return {}

    def check_sync(self):
        """校验 N_rgb == N_depth == N_seg == N_pose。"""
        n_img = len(self.frames)
        n_depth = sum(1 for _ in self.depth_dir.glob("*.png"))
        n_seg = sum(1 for _ in self.seg_dir.glob("*.png"))
        n_pose = len(self.poses())
        return n_img, n_depth, n_seg, n_pose
