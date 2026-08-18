"""从实测数据生成 Khronos label_space / remap（指南 §11, §21）。

流程：
  1. 统计全部 seg 帧的 raw id 出现次数
  2. 结合 seg_label_map.json 生成 semantic inventory TSV
  3. 按 remap policy 生成 compact label_space + remap yaml
  4. 校验每个出现 raw id 都有 remap

用法：
  python -m tartanground_khronos_bridge.gen_label_config \
    --dataset-root <dir> --out-config <config_dir> --out-reports <reports_dir>
"""
import argparse
import csv
import json
import sys
from pathlib import Path

from .dataset_reader import TartanGroundDataset
from .semantic_utils import (build_label_space_and_remap, load_seg_label_map,
                             semantic_inventory, write_yaml_label_space,
                             write_yaml_remap)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset-root", required=True)
    ap.add_argument("--out-config", default="config", help="label_space/remap 输出目录")
    ap.add_argument("--out-reports", default="reports", help="inventory 输出目录")
    ap.add_argument("--max-frames", type=int, default=None, help="仅统计前 N 帧（可选）")
    args = ap.parse_args(argv)

    ds = TartanGroundDataset(args.dataset_root)
    label_map_path = ds.root / "seg_label_map.json"
    if not label_map_path.is_file():
        raise FileNotFoundError(f"missing seg_label_map.json: {label_map_path}")
    id_name = load_seg_label_map(label_map_path)

    print(f"seg_label_map raw classes: {len(id_name)}")
    inventory, total_pixels = semantic_inventory(ds, max_frames=args.max_frames)
    print(f"inventoried pixels: {total_pixels}, present raw ids: {len(inventory)}")

    # TSV inventory
    reports = Path(args.out_reports)
    reports.mkdir(parents=True, exist_ok=True)
    tsv_path = reports / "hospital_semantic_inventory.tsv"
    with open(tsv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["raw_id", "original_name", "pixel_count",
                    "proposed_super_id", "proposed_name"])
        from .semantic_utils import classify_name, COMPACT_NAMES
        name_of = {sid: nm for sid, nm in COMPACT_NAMES}
        for rid in sorted(inventory):
            name = id_name.get(rid, "")
            sid = classify_name(name.lower()) if name else None
            if sid is None:
                sid = 0
            w.writerow([rid, name, inventory[rid], sid, name_of.get(sid, "")])
    print(f"inventory -> {tsv_path}")

    # label space + remap
    label_space, remap = build_label_space_and_remap(id_name, inventory)
    config = Path(args.out_config)
    config.mkdir(parents=True, exist_ok=True)
    ls_path = config / "tartanground_hospital_label_space.yaml"
    rm_path = config / "tartanground_hospital_remap.yaml"
    write_yaml_label_space(label_space, ls_path)
    write_yaml_remap(remap, rm_path)

    # 校验覆盖
    present = set(inventory.keys())
    remapped = {r["sub_id"] for r in remap}
    missing = present - remapped
    if missing:
        raise RuntimeError(f"remap 未覆盖 raw ids: {sorted(missing)}")
    print(f"label_space({ls_path}): {len(label_space['label_names'])} classes")
    print(f"remap({rm_path}): {len(remap)} entries, 覆盖全部 {len(present)} raw ids")
    print(f"object_labels: {label_space['object_labels']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
