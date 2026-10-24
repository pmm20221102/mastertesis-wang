#!/usr/bin/env python3
"""Convert actual Hospital task-chain predictions into fixed-map Nav2 missions."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import yaml

from floor_transition_core import validate_transition_bundle


MISSION_SCHEMA = "hospital-multifloor-mission-v1"
CANDIDATE_MISSION_SCHEMA = "hospital-multifloor-candidate-mission-v1"
DEFAULT_TASK_IDS = [
    "HC-025", "HC-026", "HC-027", "HC-028", "HC-029", "HC-030",
    "HC-031", "HC-032", "HC-033", "HC-034", "HC-039",
]


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _task_id(row):
    return row.get("task_id") or row.get("\ufefftask_id")


def _floor_details(row):
    raw = row.get("candidate_floor_details_by_stop")
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, list):
        raise ValueError("classification candidate floor details must be a list")
    result = []
    for stop_index, entries in enumerate(raw):
        if not isinstance(entries, list):
            raise ValueError(f"classification stop {stop_index} must be a list")
        stop = {}
        for entry in entries:
            node_id = entry.get("node_id")
            floor_id = entry.get("nearest_floor")
            if not isinstance(node_id, str) or isinstance(floor_id, bool) or not isinstance(floor_id, int):
                raise ValueError(f"invalid floor assignment at stop {stop_index}")
            if node_id in stop and stop[node_id] != floor_id:
                raise ValueError(f"conflicting floor assignment for {node_id}")
            stop[node_id] = floor_id
        result.append(stop)
    return result


def _candidate_targets(chain_step, grounded_step, floor_assignments, floor_source,
                       allow_cross_floor=False):
    target_ref = chain_step.get("target_ref")
    target = (grounded_step.get("references", {}).get("target")
              if target_ref else None)
    if not isinstance(target, dict):
        raise ValueError("task-chain target has no grounded navigation target")

    chain_ids = chain_step.get("candidate_node_ids")
    if not isinstance(chain_ids, list):
        raise ValueError("task-chain candidate_node_ids must be a list")
    if chain_ids:
        candidate_ids = chain_ids
    elif isinstance(chain_step.get("target_id"), str) and chain_step["target_id"]:
        candidate_ids = [chain_step["target_id"]]
    else:
        candidate_ids = target.get("candidate_node_ids", [])
    if not candidate_ids or any(not isinstance(node_id, str) or not node_id
                                for node_id in candidate_ids):
        raise ValueError("task-chain step has no concrete target candidates")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("task-chain step contains duplicate target candidates")

    target_by_id = {}
    selected_id = target.get("node_id")
    if isinstance(selected_id, str) and isinstance(target.get("navigation_target"), dict):
        target_by_id[selected_id] = target["navigation_target"]
    for item in target.get("candidate_navigation_targets", []):
        if not isinstance(item, dict) or not isinstance(item.get("node_id"), str):
            continue
        navigation_target = item.get("navigation_target")
        if isinstance(navigation_target, dict):
            target_by_id[item["node_id"]] = navigation_target

    missing = [node_id for node_id in candidate_ids if node_id not in target_by_id]
    if missing:
        raise ValueError(f"candidate navigation target missing for {missing}")
    missing_floors = [node_id for node_id in candidate_ids
                      if node_id not in floor_assignments]
    if missing_floors:
        raise ValueError(f"candidate floor assignment missing for {missing_floors}")
    floors = sorted({floor_assignments[node_id] for node_id in candidate_ids})
    if len(floors) != 1 and not allow_cross_floor:
        return floors, []

    candidates = []
    for node_id in candidate_ids:
        candidate_floor = floor_assignments[node_id]
        source_target = target_by_id[node_id]
        if source_target.get("frame_id", "map") != "map":
            raise ValueError(f"target {node_id} is not in map frame")
        position = source_target.get("position")
        if (not isinstance(position, (list, tuple)) or len(position) < 2
                or any(isinstance(v, bool) for v in position[:3])):
            raise ValueError(f"target {node_id} has invalid position")
        if len(position) < 3:
            raise ValueError(f"target {node_id} has no world-frame floor elevation")
        normalized = {**source_target, "frame_id": "map", "position": list(position[:3]),
                      "floor_id": candidate_floor, "floor_assignment_source": floor_source}
        candidates.append({
            "target_id": node_id,
            "floor_id": candidate_floor,
            "floor_assignment_source": floor_source,
            "navigation_target": normalized,
        })
    return floors, candidates


def _normalize_fixed_start_pose(value, map_bundle_id, start_poses):
    if not isinstance(value, dict):
        raise ValueError("fixed_start_pose must be an object")
    if value.get("map_bundle_id") != map_bundle_id:
        raise ValueError("fixed start pose map_bundle_id does not match map bundle")
    floor_id = value.get("floor_id")
    if isinstance(floor_id, bool) or not isinstance(floor_id, int) or floor_id < 0:
        raise ValueError("fixed start pose floor_id must be a non-negative integer")
    if str(floor_id) not in start_poses.get("poses", {}):
        raise ValueError(f"fixed start pose is not registered for floor {floor_id}")
    pose = value.get("pose")
    if not isinstance(pose, dict) or pose.get("frame_id", "map") != "map":
        raise ValueError("fixed start pose must use the map frame")
    normalized_pose = {"frame_id": "map"}
    for key in ("x", "y", "yaw"):
        raw = pose.get(key)
        if isinstance(raw, bool):
            raise ValueError(f"fixed start pose {key} must be finite")
        try:
            number = float(raw)
        except (OverflowError, TypeError, ValueError) as error:
            raise ValueError(f"fixed start pose {key} must be finite") from error
        if not math.isfinite(number):
            raise ValueError(f"fixed start pose {key} must be finite")
        normalized_pose[key] = number
    return {"map_bundle_id": map_bundle_id, "floor_id": floor_id,
            "pose": normalized_pose}


def prepare_task_missions(prediction_records, classification_records, start_poses,
                          task_ids, map_bundle_id, *,
                          allow_cross_floor_candidates=False,
                          fixed_start_pose=None):
    """Prepare runnable tasks without consulting gold labels or choosing a first candidate."""
    if not isinstance(allow_cross_floor_candidates, bool):
        raise ValueError("allow_cross_floor_candidates must be a boolean")
    if allow_cross_floor_candidates and fixed_start_pose is None:
        raise ValueError("cross-floor candidate mode requires a fixed start pose")
    normalized_start = (_normalize_fixed_start_pose(fixed_start_pose, map_bundle_id,
                                                     start_poses)
                        if fixed_start_pose is not None else None)
    predictions = {}
    for row in prediction_records:
        task_id = row.get("task_id")
        if not isinstance(task_id, str) or task_id in predictions:
            raise ValueError("prediction task IDs must be unique non-empty strings")
        predictions[task_id] = row
    classifications = {}
    for row in classification_records:
        task_id = _task_id(row)
        if not isinstance(task_id, str) or task_id in classifications:
            raise ValueError("classification task IDs must be unique non-empty strings")
        classifications[task_id] = row

    rows = []
    for task_id in task_ids:
        prediction = predictions.get(task_id)
        classification = classifications.get(task_id)
        if prediction is None or classification is None:
            rows.append({"task_id": task_id, "status": "missing_input",
                         "reason": "prediction or classification row is missing",
                         "mission": None})
            continue
        result = prediction.get("result")
        if (not isinstance(result, dict) or result.get("status") != "valid"
                or result.get("task_plan", {}).get("status") != "valid"):
            rows.append({"task_id": task_id, "status": "invalid_pipeline_output",
                         "reason": "Full task-chain output is not valid", "mission": None})
            continue
        chain = result["task_plan"].get("task_chain")
        grounded_steps = result.get("grounded_task", {}).get("steps")
        try:
            details = _floor_details(classification)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            rows.append({"task_id": task_id, "status": "invalid_classification",
                         "reason": str(error), "mission": None})
            continue
        if not isinstance(chain, list) or not isinstance(grounded_steps, list) or len(chain) != len(grounded_steps):
            rows.append({"task_id": task_id, "status": "invalid_pipeline_output",
                         "reason": "task-chain and grounded stop counts differ", "mission": None})
            continue
        if len(details) != len(chain):
            rows.append({"task_id": task_id, "status": "invalid_classification",
                         "reason": "classification and predicted stop counts differ", "mission": None})
            continue

        stops = []
        unresolved = []
        failure = None
        floor_source = "hospital-60-nav2-planning-20261015:nearest_floor_crosschecked_to_khronos_planes"
        for index, (chain_step, grounded_step, floor_map) in enumerate(
                zip(chain, grounded_steps, details)):
            try:
                floors, candidates = _candidate_targets(chain_step, grounded_step,
                                                        floor_map, floor_source,
                                                        allow_cross_floor_candidates)
            except (TypeError, ValueError, KeyError) as error:
                failure = str(error)
                break
            if len(floors) != 1 and not allow_cross_floor_candidates:
                unresolved.append({"stop_index": index, "candidate_floors": floors})
                continue
            stop = {"stop_index": index, "candidate_targets": candidates}
            if len(floors) == 1:
                stop["floor_id"] = floors[0]
                stop["floor_assignment_source"] = floor_source
            stops.append(stop)
        if failure:
            rows.append({"task_id": task_id, "status": "invalid_pipeline_output",
                         "reason": failure, "mission": None})
            continue
        if unresolved:
            rows.append({"task_id": task_id, "status": "unresolved_floor",
                         "reason": "one or more predicted candidate sets span floors",
                         "unresolved_stops": unresolved, "mission": None})
            continue
        if len(stops) != len(chain):
            rows.append({"task_id": task_id, "status": "invalid_pipeline_output",
                         "reason": "not all ordered task-chain stops were materialized",
                         "mission": None})
            continue
        if normalized_start is not None:
            initial_floor = normalized_start["floor_id"]
            start = normalized_start["pose"]
        else:
            initial_floor = stops[0].get("floor_id")
            start = start_poses.get("poses", {}).get(str(initial_floor), {}).get("pose")
        if not isinstance(start, dict) or start.get("frame_id", "map") != "map":
            rows.append({"task_id": task_id, "status": "missing_start_pose",
                         "reason": f"no map-frame start pose for floor {initial_floor}",
                         "mission": None})
            continue
        mission = {"schema_version": (CANDIDATE_MISSION_SCHEMA if allow_cross_floor_candidates
                                       else MISSION_SCHEMA), "task_id": task_id,
                   "map_bundle_id": map_bundle_id, "initial_floor": initial_floor,
                   "initial_pose": dict(start), "stops": stops}
        rows.append({"task_id": task_id, "status": "ready", "mission": mission})
    return {"schema_version": "hospital-multifloor-mission-preparation-v1",
            "map_bundle_id": map_bundle_id, "tasks": rows}


def _load_jsonl(path):
    rows = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ValueError(f"invalid JSONL at {path}:{line_number}") from error
    return rows


def _atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", required=True, type=Path,
                        help="actual Full predictions JSONL; gold is never read")
    parser.add_argument("--classification", required=True, type=Path)
    parser.add_argument("--start-poses", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--task-ids", nargs="+", default=DEFAULT_TASK_IDS)
    parser.add_argument("--allow-cross-floor-candidates", action="store_true",
                        help="preserve candidates spanning floors for route preflight")
    parser.add_argument("--fixed-start-pose", type=Path,
                        help="map-verified initial floor/pose JSON for cross-floor selection")
    parser.add_argument("--approve-proposed-starts", action="store_true",
                        help="record user's explicit authorization to use proposed safe starts")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    bundle = yaml.safe_load(args.bundle.read_text(encoding="utf-8"))
    normalized_bundle = validate_transition_bundle(bundle, verify_files=True)
    starts = json.loads(args.start_poses.read_text(encoding="utf-8"))
    if (starts.get("status") == "proposed_pending_user_review"
            and not args.approve_proposed_starts):
        parser.error("start poses are pending review; explicit approval flag is required")
    fixed_start = None
    if args.fixed_start_pose:
        try:
            fixed_start = json.loads(args.fixed_start_pose.read_text(encoding="utf-8"))
            normalized_start = _normalize_fixed_start_pose(
                fixed_start, normalized_bundle["map_bundle_id"], starts)
            floor_config = normalized_bundle["floors"][str(normalized_start["floor_id"])]
            if (fixed_start.get("map_yaml_sha256") != floor_config["map_yaml_sha256"]
                    or fixed_start.get("map_pgm_sha256") != floor_config["map_pgm_sha256"]):
                parser.error("fixed start pose map hashes do not match the map bundle")
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            parser.error(f"invalid --fixed-start-pose: {error}")
    for floor_key, config in normalized_bundle["floors"].items():
        record = starts.get("poses", {}).get(floor_key, {})
        if record.get("map_yaml_sha256") != config["map_yaml_sha256"]:
            parser.error(f"start pose/map hash mismatch on floor {floor_key}")
        if record.get("map_pgm_sha256") != config["map_pgm_sha256"]:
            parser.error(f"start pose/PGM hash mismatch on floor {floor_key}")
    with args.classification.open(newline="", encoding="utf-8-sig") as stream:
        classification = list(csv.DictReader(stream))
    prepared = prepare_task_missions(_load_jsonl(args.predictions), classification,
                                     starts, args.task_ids,
                                     normalized_bundle["map_bundle_id"],
                                     allow_cross_floor_candidates=args.allow_cross_floor_candidates,
                                     fixed_start_pose=fixed_start)
    provenance = {
        "prediction_path": str(args.predictions.resolve()),
        "prediction_sha256": _hash_file(args.predictions),
        "classification_path": str(args.classification.resolve()),
        "classification_sha256": _hash_file(args.classification),
        "start_poses_path": str(args.start_poses.resolve()),
        "start_poses_sha256": _hash_file(args.start_poses),
        "fixed_start_pose_path": str(args.fixed_start_pose.resolve())
        if args.fixed_start_pose else None,
        "fixed_start_pose_sha256": _hash_file(args.fixed_start_pose)
        if args.fixed_start_pose else None,
        "map_bundle_path": str(args.bundle.resolve()),
        "map_bundle_sha256": _hash_file(args.bundle),
        "start_pose_authorization": ("user_authorized_for_wsl_e2e_2026-10-22"
                                     if args.approve_proposed_starts else "previously_approved"),
        "gold_used_to_construct_missions": False,
    }
    report = {**prepared, "provenance": provenance}
    if args.validate_only:
        print(json.dumps({"status": "valid", "tasks": len(prepared["tasks"]),
                          "ready": sum(row["status"] == "ready" for row in prepared["tasks"]),
                          "unresolved_floor": sum(row["status"] == "unresolved_floor"
                                                   for row in prepared["tasks"])},
                         ensure_ascii=False))
        return 0
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    for row in prepared["tasks"]:
        if row["status"] == "ready":
            _atomic_write(output_dir / f"{row['task_id']}.mission.json", row["mission"])
    _atomic_write(output_dir / "preparation_report.json", report)
    return 0 if any(row["status"] == "ready" for row in prepared["tasks"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
