#!/usr/bin/env python3
"""Freeze floor annotations and the approved F0 start for cross-floor E2E."""

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import yaml


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def candidate_floor_rows(records, floor_z):
    output = []
    for record in records:
        result = record.get("result") or {}
        chain = ((result.get("task_plan") or {}).get("task_chain") or [])
        grounded = ((result.get("grounded_task") or {}).get("steps") or [])
        navigation_targets = {}
        for grounded_step in grounded:
            target = (grounded_step.get("references") or {}).get("target") or {}
            if (isinstance(target.get("node_id"), str)
                    and isinstance(target.get("navigation_target"), dict)):
                navigation_targets[target["node_id"]] = target["navigation_target"]
            for item in target.get("candidate_navigation_targets", []):
                if (isinstance(item, dict) and isinstance(item.get("node_id"), str)
                        and isinstance(item.get("navigation_target"), dict)):
                    navigation_targets[item["node_id"]] = item["navigation_target"]

        stops = []
        for step in chain:
            ids = step.get("candidate_node_ids") or (
                [step["target_id"]] if isinstance(step.get("target_id"), str) else [])
            details = []
            for node_id in ids:
                position = (navigation_targets.get(node_id) or {}).get("position")
                if not isinstance(position, (list, tuple)) or len(position) < 3:
                    continue
                z = float(position[2])
                floor = min(floor_z, key=lambda item: abs(z - floor_z[item]))
                details.append({"node_id": node_id, "nearest_floor": floor,
                                "floor_z_delta_m": round(abs(z - floor_z[floor]), 4)})
            stops.append(details)
        output.append({"task_id": record["task_id"],
                       "candidate_floor_details_by_stop": json.dumps(
                           stops, separators=(",", ":"))})
    return output


def build_start_pose(bundle, f0_cell, clearance_m):
    floor_key = next(key for key in bundle["floors"] if int(key) == 0)
    floor = bundle["floors"][floor_key]
    metadata = yaml.safe_load(Path(floor["map_yaml_path"]).read_text(encoding="utf-8"))
    resolution = float(metadata["resolution"])
    origin = metadata["origin"]
    x, y = f0_cell
    pose = {"x": float(origin[0]) + (x + 0.5) * resolution,
            "y": float(origin[1]) + (y + 0.5) * resolution,
            "yaw": 0.0, "frame_id": bundle["frame_id"]}
    return {
        "schema_version": "hospital-fixed-start-pose-v1",
        "status": "user-approved-for-crossfloor-e2e-2026-10-22",
        "map_bundle_id": bundle["map_bundle_id"],
        "floor_id": 0,
        "pose": pose,
        "source": {
            "method": "max-clearance-cell-in-largest-8-connected-known-free-component",
            "cell_xy": list(f0_cell),
            "pgm_value": 254,
            "clearance_from_occupied_or_unknown_m": float(clearance_m),
            "resolution_m": resolution,
            "map_yaml_sha256": floor["map_yaml_sha256"],
            "map_pgm_sha256": floor["map_pgm_sha256"],
        },
        "map_yaml_sha256": floor["map_yaml_sha256"],
        "map_pgm_sha256": floor["map_pgm_sha256"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    bundle = yaml.safe_load(args.bundle.read_text(encoding="utf-8"))
    records = read_jsonl(args.predictions)
    if len(records) != 60 or len({row.get("task_id") for row in records}) != 60:
        parser.error("expected exactly 60 unique frozen prediction rows")
    floor_z = {int(key): float(value["floor_z_m"])
               for key, value in bundle["floors"].items()}
    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root / "khronos-nav2-bridge" /
                           "khronos_nav2_bridge" / "scripts"))
    from plan_candidate_task_chain import read_pgm
    floor_key = next(key for key in bundle["floors"] if int(key) == 0)
    floor0 = bundle["floors"][floor_key]
    values, width, height = read_pgm(floor0["map_pgm_path"])
    cell_x, cell_y = 206, 140
    if not (0 <= cell_x < width and 0 <= cell_y < height):
        parser.error("fixed F0 start cell is outside the frozen map")
    pgm_value = values[(height - 1 - cell_y) * width + cell_x]
    if pgm_value != 254:
        parser.error(f"fixed F0 start cell is not known-free (PGM={pgm_value})")
    start = build_start_pose(bundle, (206, 140), 4.675467891024384)
    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    class_path = out_dir / "classification_e2b.csv"
    start_path = out_dir / "fixed_start_pose_f0.json"
    if class_path.exists() or start_path.exists():
        parser.error("refusing to overwrite frozen run inputs")
    with class_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["task_id", "candidate_floor_details_by_stop"])
        writer.writeheader()
        writer.writerows(candidate_floor_rows(records, floor_z))
    start_path.write_text(json.dumps(start, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    print(json.dumps({
        "prediction_rows": len(records),
        "prediction_sha256": sha256(args.predictions),
        "map_bundle_id": bundle["map_bundle_id"],
        "fixed_start_pose": start["pose"],
        "f0_cell": start["source"]["cell_xy"],
        "f0_clearance_m": start["source"]["clearance_from_occupied_or_unknown_m"],
        "classification_csv": str(class_path),
        "classification_sha256": sha256(class_path),
        "start_pose_json": str(start_path),
        "start_pose_sha256": sha256(start_path),
    }, indent=2))


if __name__ == "__main__":
    main()
