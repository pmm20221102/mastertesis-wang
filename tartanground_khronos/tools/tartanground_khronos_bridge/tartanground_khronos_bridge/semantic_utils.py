"""Semantic 工具：seg_label_map 解析 + compact Khronos labelspace/remap 生成。

指南 §11：
- TartanGround raw seg ID 是环境自己的编码，必须先 inventory 再 remap 到
  Khronos compact label space。
- 每个实际出现的 raw ID 都必须有 remap（assert 覆盖）。
- 不确定的类别宁可归 Unknown，也不要错误当 object。
"""
import json

import cv2
import numpy as np

# 指南 §11.3 compact hospital labelspace（按语义名自动归类的目标）
COMPACT_NAMES = [
    (0, "Unknown"),
    (1, "Floor"),
    (2, "Wall"),
    (3, "Ceiling"),
    (4, "Door"),
    (5, "Chair"),
    (6, "Table"),
    (7, "Bed"),
    (8, "Cabinet/Shelf"),
    (9, "MedicalEquipment"),
    (10, "Sign"),
    (11, "OtherFurniture"),
    (12, "Human"),
    (13, "OtherObject"),
]

# lowercase 语义名 -> compact super id（指南 §11.4 remap policy）
_NAME_RULES = [
    (("floor", "ground", "groundmat"), 1),
    (("wall",), 2),
    (("ceiling", "acousticceiling"), 3),
    (("door", "doorway", "gate"), 4),
    (("chair", "stool", "bench", "wheelchair"), 5),
    (("table", "desk", "counter", "stand"), 6),
    (("bed", "hospitalbed", "stretcher"), 7),
    (("cabinet", "shelf", "shelving", "chest", "wardrobe"), 8),
    (("medical", "monitor", "equipment", "defibrillator", "infusion", "ventilator",
      "respirator", "oxygen", "iv", "diagnostic", "machine", "compressor",
      "respiration", "anesthesia", "scanner"), 9),
    (("sign", "notice", "poster", "calendar", "signboard",
      "blackboard", "whiteboard", "noticeboard", "billboard"), 10),
    (("human", "person", "patient", "doctor", "nurse", "visitor"), 12),
]


def load_seg_label_map(path):
    """返回 {raw_id: name}。兼容 {"name_map": {...}} 或直接 {id: name}。"""
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, dict) and "name_map" in data:
        nm = data["name_map"]
        return {int(v): str(k) for k, v in nm.items()}
    return {int(k): str(v) for k, v in data.items()}


def semantic_inventory(dataset, max_frames=None):
    """统计全部 seg 帧的 raw id 出现次数。返回 Counter[int] 与总像素。"""
    from collections import Counter
    counter = Counter()
    n = len(dataset) if max_frames is None else min(max_frames, len(dataset))
    total_pixels = 0
    for i in range(n):
        seg = dataset.seg(i)
        unique, counts = np.unique(seg, return_counts=True)
        for u, c in zip(unique.tolist(), counts.tolist()):
            counter[u] += int(c)
        total_pixels += int(seg.size)
    return counter, total_pixels


def classify_name(name):
    """按 remap policy 把 lowercase 语义名映射到 compact super id。"""
    for keys, sid in _NAME_RULES:
        for k in keys:
            if k in name:
                return sid
    return None


def build_label_space_and_remap(id_name, inventory):
    """从实测 raw id 生成 (label_space_yaml_dict, remap_list)。

    id_name: {raw_id: name}
    inventory: Counter[raw_id] 出现次数
    """
    present = sorted(inventory.keys())
    remap = []
    for rid in present:
        name = (id_name.get(rid) or "").lower()
        sid = classify_name(name) if name else None
        if sid is None:
            sid = 0  # 不确定 -> Unknown
        remap.append({"sub_id": rid, "super_id": sid})

    label_names = [{"label": sid, "name": nm} for sid, nm in COMPACT_NAMES]
    object_labels = sorted(
        {r["super_id"] for r in remap if r["super_id"] not in (0, 1, 2, 3, 4)}
    )
    return {
        "total_semantic_labels": len(COMPACT_NAMES),
        "dynamic_labels": [12],
        "invalid_labels": [0],
        "object_labels": object_labels,
        "surface_places_labels": [1],
        "label_names": label_names,
    }, remap


def write_yaml_label_space(label_space, path):
    import yaml
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\n")
        yaml.safe_dump(label_space, f, sort_keys=False, allow_unicode=True)


def write_yaml_remap(remap, path):
    import yaml
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\n")
        yaml.safe_dump(remap, f, sort_keys=False)
