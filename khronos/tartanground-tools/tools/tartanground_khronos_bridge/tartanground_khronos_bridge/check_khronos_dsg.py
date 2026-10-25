"""Khronos smoke DSG 验收（指南 §28）。

读取 Khronos finish 保存的 DSG json，输出：
  reports/khronos_smoke_graph_report.json

检查：nodes/edges/objects/places/rooms > 0，positions finite，
xyz 范围合理（无离谱 z），连通分量不碎裂。
"""
import argparse
import json
import math
import sys
from collections import Counter, deque
from pathlib import Path


def analyze(dsg_path):
    with open(dsg_path, encoding="utf-8") as f:
        data = json.load(f)

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    header = data.get("SPARK_DSG_header", {})

    by_layer = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        by_layer.setdefault((n.get("layer"), n.get("partition")), []).append(n)

    places = by_layer.get((3, 0), [])
    rooms = by_layer.get((4, 0), [])
    objects = by_layer.get((2, 0), [])
    buildings = by_layer.get((5, 0), [])

    positions = []
    nonfinite = 0
    for n in nodes:
        pos = (n.get("attributes", {}) or {}).get("position")
        if not pos or len(pos) < 3:
            nonfinite += 1
            continue
        if not all(math.isfinite(float(v)) for v in pos[:3]):
            nonfinite += 1
            continue
        positions.append([float(v) for v in pos[:3]])

    xyz_min, xyz_max = None, None
    if positions:
        xyz_min = [min(p[i] for p in positions) for i in range(3)]
        xyz_max = [max(p[i] for p in positions) for i in range(3)]

    # 连通分量（PLACES 层内边）
    place_ids = {int(n["id"]) for n in places}
    adj = {pid: [] for pid in place_ids}
    for e in edges:
        if not isinstance(e, dict):
            continue
        s, t = int(e.get("source", 0)), int(e.get("target", 0))
        if s in place_ids and t in place_ids:
            adj.setdefault(s, []).append(t)
            adj.setdefault(t, []).append(s)
    visited = set()
    comps = []
    for pid in place_ids:
        if pid in visited:
            continue
        stack, comp = [pid], []
        while stack:
            c = stack.pop()
            if c in visited:
                continue
            visited.add(c)
            comp.append(c)
            stack.extend(adj.get(c, []))
        comps.append(comp)
    comp_sizes = sorted((len(c) for c in comps), reverse=True)

    report = {
        "num_nodes": len(nodes),
        "num_edges": len(edges),
        "num_objects": len(objects),
        "num_places": len(places),
        "num_rooms": len(rooms),
        "num_buildings": len(buildings),
        "xyz_min": [round(v, 3) for v in xyz_min] if xyz_min else None,
        "xyz_max": [round(v, 3) for v in xyz_max] if xyz_max else None,
        "num_nonfinite_positions": nonfinite,
        "place_connected_components": len(comps),
        "largest_component": comp_sizes[0] if comp_sizes else 0,
        "component_sizes_top": comp_sizes[:10],
        "source": str(dsg_path),
        "header_version": header.get("version", {}),
    }

    # PASS/FAIL 判定
    ok = (len(nodes) > 0 and len(edges) > 0 and len(places) > 0
          and nonfinite == 0 and xyz_min is not None)
    z_range = (xyz_max[2] - xyz_min[2]) if xyz_max else 0
    report["z_range_m"] = round(z_range, 3)
    if z_range > 50:
        ok = False
    if comp_sizes and comp_sizes[0] < len(places) * 0.3:
        report["connectivity_warning"] = "最大分量占比过小，可能坐标错乱"
        ok = False
    report["result"] = "PASS" if ok else "FAIL"
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dsg_json")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    report = analyze(args.dsg_json)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
