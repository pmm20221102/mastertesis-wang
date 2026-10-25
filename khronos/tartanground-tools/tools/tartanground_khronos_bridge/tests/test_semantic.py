"""Semantic 测试：出现 raw ids 全部有 remap（指南 §38 test_semantic）。"""
import os

import numpy as np
import pytest

from tartanground_khronos_bridge.semantic_utils import (semantic_inventory,
                                                        build_label_space_and_remap)


def test_all_present_ids_have_remap(dataset, seg_label_map):
    inventory, _ = semantic_inventory(dataset, max_frames=60)
    _, remap = build_label_space_and_remap(seg_label_map, inventory)
    remapped = {r["sub_id"] for r in remap}
    present = set(inventory.keys())
    missing = present - remapped
    assert not missing, f"raw ids 无 remap: {sorted(missing)}"


def test_seg_dtype_mono8_compatible(dataset):
    seg = dataset.seg(0)
    assert seg.ndim == 2
    assert seg.max() <= 255, "raw seg id 超过 255，mono8 不够"


def test_remap_unknown_safe(dataset, seg_label_map):
    inventory, _ = semantic_inventory(dataset, max_frames=60)
    _, remap = build_label_space_and_remap(seg_label_map, inventory)
    # Unknown(0) 不应成为主要 object label
    unknown_pixels = sum(inventory[m["sub_id"]] for m in remap if m["super_id"] == 0)
    total = sum(inventory.values())
    ratio = unknown_pixels / total if total else 0.0
    assert ratio < 0.9, f"Unknown 占比过高: {ratio:.2f}"
