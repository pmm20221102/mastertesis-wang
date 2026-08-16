#!/usr/bin/env python3
"""CLI: Khronos DSG → ANYmal D waypoints.json + export_report.json.

用法:
  /opt/conda/envs/llm-task-planner/bin/python export_waypoints.py <dsg.json> \
      [--out generated/waypoints.json] [--report generated/export_report.json] \
      [--policy config/default_anymal_edge_policy.yaml] [--frame map]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dsg_reader.loader import load_places  # noqa: E402
from waypoint_exporter.exporter import export, load_policy, write_waypoints  # noqa: E402
from waypoint_graph.validator import compare_topology, validate_waypoints  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dsg_json")
    ap.add_argument("--out", default="generated/waypoints.json")
    ap.add_argument("--report", default="generated/export_report.json")
    ap.add_argument("--policy", default="config/default_anymal_edge_policy.yaml")
    ap.add_argument("--frame", default="map")
    args = ap.parse_args()

    graph = load_places(args.dsg_json, frame=args.frame)
    policy = load_policy(args.policy)
    wp = export(graph, policy, frame=args.frame)

    out = Path(args.out)
    write_waypoints(wp, str(out))

    validation = validate_waypoints(out)
    topology = compare_topology(graph, out)

    # z 高度标注（Phase 1 硬阻塞项）：不做静默转换，如实记录
    zs = [n["pose_stamped"]["position"][2] for n in wp["nodes"]]
    report = {
        "source_dsg": args.dsg_json,
        "frame": args.frame,
        "exported_nodes": len(wp["nodes"]),
        "exported_edges": len(wp["edges"]),
        "source_place_nodes": len(graph.nodes),
        "source_place_edges": len(graph.edges),
        "policy_file": args.policy,
        "z_range_exported": {"min": round(min(zs), 3), "max": round(max(zs), 3)},
        "z_note": "Khronos PLACES z 为自由空间高度(1.1~3.9)，ANYmal 样本 z≈0.03；"
                  "MVP 原样导出，未做静默转换，真实一致性待 Phase 9/10 验证",
        "orientation_note": "Khronos Place orientation 全为 identity，MVP 原样导出",
        "validation": validation,
        "topology_compare": topology,
    }
    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({
        "valid": validation["valid"],
        "topology_preserved": topology["topology_preserved"],
        "nodes": len(wp["nodes"]),
        "edges": len(wp["edges"]),
        "report": str(rp),
    }, ensure_ascii=False))
    if not validation["valid"]:
        print("VALIDATION ERRORS:", file=sys.stderr)
        for e in validation["errors"][:20]:
            print("  -", e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
