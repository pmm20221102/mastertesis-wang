"""ROS2 bag 静态审计（指南 §19, §23）。

统计各 topic 的 message 数量/类型，校验 RGB=Depth=Semantic=CameraInfo 数量一致，
并生成报告 dict（供 reports/smoke_bag_info.txt 与 semantic_stats.json）。
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def audit_bag(bag_dir, expected_frames=None):
    """返回 {topic: {count, type}}。"""
    from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
    storage = StorageOptions(uri=str(bag_dir), storage_id="sqlite3")
    converter = ConverterOptions(input_serialization_format="cdr",
                                 output_serialization_format="cdr")
    reader = SequentialReader()
    reader.open(storage, converter)
    types = {ti.name: ti.type for ti in reader.get_all_topics_and_types()}
    counts = Counter()
    while reader.has_next():
        topic, _data, _t = reader.read_next()
        counts[topic] += 1
    result = {t: {"count": counts[t], "type": types.get(t)} for t in types}
    if expected_frames is not None:
        result["_expected_frames"] = expected_frames
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("bag_dir")
    ap.add_argument("--expected-frames", type=int, default=None)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    res = audit_bag(args.bag_dir, args.expected_frames)
    print("=== bag audit ===")
    for t, info in res.items():
        if t.startswith("_"):
            continue
        print(f"  {t:55s} {info['type']:30s} count={info['count']}")
    if args.expected_frames:
        rgb = res.get("/tartanground/front/rgb/image_raw", {}).get("count")
        depth = res.get("/tartanground/front/depth/image_raw", {}).get("count")
        seg = res.get("/tartanground/front/semantic/image_raw", {}).get("count")
        info_cam = res.get("/tartanground/front/rgb/camera_info", {}).get("count")
        tf = res.get("/tf", {}).get("count")
        tf_static = res.get("/tf_static", {}).get("count")
        ok = (rgb == depth == seg == info_cam == args.expected_frames
              and tf is not None and tf >= args.expected_frames
              and tf_static and tf_static >= 1)
        print(f"  expected_frames={args.expected_frames} rgb={rgb} depth={depth} "
              f"seg={seg} camera_info={info_cam} tf={tf} tf_static={tf_static}")
        print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
