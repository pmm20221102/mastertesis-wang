#!/usr/bin/env python3
"""Generate independent single-waypoint trials for safer region approach poses."""

import argparse
import json
import pathlib

from navigation_grid_geometry import loadmap, safe
from prepare_region_waypoint_missions import approach_candidates


def build_repeat_trials(trial_payloads, case_ids, repeats=2):
    if isinstance(trial_payloads, dict):
        trial_payloads = [trial_payloads]
    source_tasks = {
        task["case_id"]: task
        for payload in trial_payloads
        for task in payload["tasks"]
    }
    tasks = []
    for case_id in case_ids:
        if case_id not in source_tasks:
            raise ValueError(f"missing selected trial {case_id}")
        for repeat in range(repeats):
            task = json.loads(json.dumps(source_tasks[case_id]))
            task["case_id"] = f"{case_id}-r{repeat + 1:02d}"
            tasks.append(task)
    target_ids = {task["waypoint_ids"][0] for task in tasks}
    return {
        "schema_version": "region-approach-trials-v1",
        "navigation_start": trial_payloads[0]["navigation_start"],
        "experiment_gate": {"targets": len(target_ids), "trials": len(tasks)},
        "geometry_gate": {
            "eligible": True,
            "distinct_targets": len(target_ids),
            "total_waypoints": len(tasks),
            "max_projection_m": 0.8,
        },
        "tasks": tasks,
    }


def directional_candidates(source, map_data, start, limit):
    candidates = approach_candidates(
        source, map_data, start, max_distance=0.8, limit=10000
    )
    positions = {}
    for candidate in candidates:
        positions.setdefault(tuple(candidate["cell"]), candidate)
    quadrants = {}
    for candidate in positions.values():
        pose = candidate["nav2_pose"]
        quadrant = (pose["x"] >= source["x"], pose["y"] >= source["y"])
        rank = (candidate["projection_distance_m"], candidate["clearance_m"])
        if quadrant not in quadrants or rank > quadrants[quadrant][0]:
            quadrants[quadrant] = rank, candidate
    return [quadrants[key][1] for key in sorted(quadrants)][:limit]


def build_trials(
    missions, map_data, target_ids, candidates_per_target=4, strategy="clearance"
):
    start = missions["navigation_start"]
    tasks = []
    candidate_rows = {}
    for waypoint_id in target_ids:
        target = missions["targets"][str(waypoint_id)]
        if strategy == "directional":
            candidates = directional_candidates(
                target["source_pose"], map_data, start, candidates_per_target
            )
        else:
            candidates = approach_candidates(
                target["source_pose"],
                map_data,
                start,
                max_distance=0.8,
                limit=candidates_per_target,
            )
        if len(candidates) != candidates_per_target:
            raise ValueError(f"insufficient candidates for waypoint {waypoint_id}")
        candidate_rows[str(waypoint_id)] = candidates
        for candidate_index, candidate in enumerate(candidates):
            pose = candidate["nav2_pose"]
            footprint_safe = safe(pose, map_data)[0]
            route_target = {
                "waypoint_id": waypoint_id,
                "room_id": target["room_id"],
                "source_pose": target["source_pose"],
                "nav2_pose": pose,
                "projection_method": "clearance_ranked_approach_candidate",
                "projection_distance_m": candidate["projection_distance_m"],
                "clearance_m": candidate["clearance_m"],
                "candidate_index": candidate_index,
                "footprint_safe": footprint_safe,
            }
            tasks.append({
                "case_id": f"approach-{waypoint_id}-c{candidate_index:02d}",
                "selected_model": "not_applicable",
                "waypoint_ids": [waypoint_id],
                "route": [route_target],
            })
    return {
        "schema_version": "region-approach-trials-v1",
        "navigation_start": start,
        "experiment_gate": {
            "targets": len(target_ids),
            "trials": len(tasks),
        },
        "geometry_gate": {
            "eligible": all(
                task["route"][0]["footprint_safe"] for task in tasks
            ),
            "distinct_targets": len(target_ids),
            "total_waypoints": len(tasks),
            "max_projection_m": 0.8,
        },
        "candidates": candidate_rows,
        "tasks": tasks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--missions", required=True)
    parser.add_argument("--map", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--targets", type=int, nargs="+", required=True)
    parser.add_argument("--candidates-per-target", type=int, default=4)
    parser.add_argument(
        "--strategy", choices=("clearance", "directional"), default="clearance"
    )
    args = parser.parse_args()
    missions = json.loads(pathlib.Path(args.missions).read_text())
    result = build_trials(
        missions,
        loadmap(args.map),
        args.targets,
        candidates_per_target=args.candidates_per_target,
        strategy=args.strategy,
    )
    pathlib.Path(args.output).write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    )
    print(json.dumps(result["experiment_gate"]))


if __name__ == "__main__":
    main()
