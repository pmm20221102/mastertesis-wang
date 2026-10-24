#!/usr/bin/env python3
"""Compile strict semantic selections into footprint-safe Nav2 route tasks."""

import argparse
import hashlib
import json
import math
import pathlib

from navigation_grid_geometry import (
    FOOTPRINT,
    known_free_bfs_candidate,
    loadmap,
    pose,
    safe,
)


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def nearest_safe(source, map_data, max_distance):
    _, width, height, resolution, origin = map_data
    center_x = round((source["x"] - origin[0]) / resolution)
    center_y = round((source["y"] - origin[1]) / resolution)
    radius = int(math.ceil(max_distance / resolution)) + 1
    candidates = []
    for cell_y in range(center_y - radius, center_y + radius + 1):
        for cell_x in range(center_x - radius, center_x + radius + 1):
            if not (0 <= cell_x < width and 0 <= cell_y < height):
                continue
            candidate = {
                "x": origin[0] + (cell_x + 0.5) * resolution,
                "y": origin[1] + (cell_y + 0.5) * resolution,
                "z": 0.0,
                "yaw": source.get("yaw", 0.0),
            }
            distance = math.hypot(
                candidate["x"] - source["x"], candidate["y"] - source["y"]
            )
            if distance <= max_distance and safe(candidate, map_data)[0]:
                candidates.append((distance, cell_y, cell_x, candidate))
    return min(candidates)[3] if candidates else None


def _known_free_component(start, map_data):
    values, width, height, resolution, origin = map_data
    start_cell = (
        round((start["x"] - origin[0]) / resolution),
        round((start["y"] - origin[1]) / resolution),
    )
    queue = [start_cell]
    seen = {start_cell}
    for cell_x, cell_y in queue:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbor = cell_x + dx, cell_y + dy
            nx, ny = neighbor
            if neighbor in seen or not (0 <= nx < width and 0 <= ny < height):
                continue
            if values[(height - 1 - ny) * width + nx] >= 250:
                seen.add(neighbor)
                queue.append(neighbor)
    return seen


def _obstacle_clearance(cell_x, cell_y, map_data, search_m=2.0):
    values, width, height, resolution, _ = map_data
    span = int(math.ceil(search_m / resolution))
    clearance = min(
        (cell_x + 0.5) * resolution,
        (cell_y + 0.5) * resolution,
        (width - cell_x - 0.5) * resolution,
        (height - cell_y - 0.5) * resolution,
        search_m,
    )
    for y in range(max(0, cell_y - span), min(height, cell_y + span + 1)):
        for x in range(max(0, cell_x - span), min(width, cell_x + span + 1)):
            if values[(height - 1 - y) * width + x] < 250:
                clearance = min(
                    clearance,
                    math.hypot(x - cell_x, y - cell_y) * resolution,
                )
    return clearance


def approach_candidates(
    source, map_data, navigation_start, max_distance=0.8, yaw_steps=16, limit=12
):
    """Return deterministic, start-connected and footprint-safe approach poses."""
    _, width, height, resolution, origin = map_data
    connected = _known_free_component(navigation_start, map_data)
    center_x = round((source["x"] - origin[0]) / resolution)
    center_y = round((source["y"] - origin[1]) / resolution)
    span = int(math.ceil(max_distance / resolution)) + 1
    candidates = []
    for cell_y in range(center_y - span, center_y + span + 1):
        for cell_x in range(center_x - span, center_x + span + 1):
            if (cell_x, cell_y) not in connected:
                continue
            x = origin[0] + (cell_x + 0.5) * resolution
            y = origin[1] + (cell_y + 0.5) * resolution
            distance = math.hypot(x - source["x"], y - source["y"])
            if distance > max_distance:
                continue
            clearance = _obstacle_clearance(cell_x, cell_y, map_data)
            for yaw_index in range(yaw_steps):
                yaw = -math.pi + 2.0 * math.pi * yaw_index / yaw_steps
                pose_data = {"x": x, "y": y, "z": 0.0, "yaw": yaw}
                if not safe(pose_data, map_data)[0]:
                    continue
                candidates.append({
                    "nav2_pose": pose_data,
                    "projection_distance_m": round(distance, 4),
                    "clearance_m": round(clearance, 4),
                    "start_connected": True,
                    "cell": [cell_x, cell_y],
                    "yaw_index": yaw_index,
                })
    candidates.sort(key=lambda row: (
        -row["clearance_m"],
        row["projection_distance_m"],
        row["cell"][1],
        row["cell"][0],
        row["yaw_index"],
    ))
    by_cell = {}
    cell_order = []
    for row in candidates:
        key = tuple(row["cell"])
        if key not in by_cell:
            by_cell[key] = []
            cell_order.append(key)
        by_cell[key].append(row)
    selected = []
    for key in cell_order:
        rows = sorted(
            by_cell[key],
            key=lambda row: abs(math.atan2(
                math.sin(row["nav2_pose"]["yaw"] - source.get("yaw", 0.0)),
                math.cos(row["nav2_pose"]["yaw"] - source.get("yaw", 0.0)),
            )),
        )
        selected.extend(rows[:2])
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        selected.extend(row for row in candidates if row not in selected)
    return selected[:limit]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--semantic-map", required=True)
    parser.add_argument("--graph", required=True)
    parser.add_argument("--original-graph", required=True)
    parser.add_argument("--map", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-projection-m", type=float, default=0.8)
    parser.add_argument("--start-x", type=float, required=True)
    parser.add_argument("--start-y", type=float, required=True)
    parser.add_argument("--start-yaw", type=float, required=True)
    parser.add_argument("--approach-overrides")
    args = parser.parse_args()

    cases = [json.loads(line) for line in pathlib.Path(args.corpus).read_text().splitlines() if line]
    selection = json.loads(pathlib.Path(args.selection).read_text())
    if not selection.get("all_cases_pass") or selection.get("semantic_passes") != len(cases):
        raise SystemExit("semantic best-of-seven gate is not complete")
    selected_by_case = {row["case_id"]: row for row in selection["rows"]}
    semantic_map = json.loads(pathlib.Path(args.semantic_map).read_text())["room_to_waypoint"]
    graph = json.loads(pathlib.Path(args.graph).read_text())
    original = json.loads(pathlib.Path(args.original_graph).read_text())
    map_data = loadmap(args.map)
    navigation_start = {"x": args.start_x, "y": args.start_y, "yaw": args.start_yaw}
    start_safe, start_reason = safe(navigation_start, map_data)
    if not start_safe:
        raise SystemExit(f"unsafe navigation start: {start_reason}")
    overrides = {}
    if args.approach_overrides:
        override_payload = json.loads(pathlib.Path(args.approach_overrides).read_text())
        if override_payload.get("schema_version") != "region-approach-overrides-v1":
            raise SystemExit("invalid approach override schema")
        overrides = override_payload.get("targets") or {}

    targets = {}
    tasks = []
    for case in cases:
        selected = selected_by_case.get(case["case_id"])
        if not selected or not selected["semantic_pass"]:
            raise SystemExit(f"missing semantic selection: {case['case_id']}")
        route = []
        for room_id, waypoint_id in zip(case["expected_room_ids"], case["waypoint_ids"]):
            mapped = semantic_map.get(str(room_id)) or {}
            if mapped.get("status") != "ok" or int(mapped.get("waypoint_id", -1)) != int(waypoint_id):
                raise SystemExit(f"semantic waypoint mismatch: {case['case_id']} {room_id}")
            key = str(waypoint_id)
            if key not in targets:
                source = pose(int(waypoint_id), graph, original)
                source_safe, source_reason = safe(source, map_data)
                nav_pose = {name: source[name] for name in ("x", "y", "z", "yaw")}
                method = "source_pose"
                override = overrides.get(key)
                if override:
                    if int(override.get("waypoint_id", -1)) != int(waypoint_id):
                        raise SystemExit(f"approach override ID mismatch: {waypoint_id}")
                    successes = int(override.get("trial_successes", 0))
                    trial_count = int(override.get("trial_count", 0))
                    if trial_count < 2 or successes != trial_count:
                        raise SystemExit(f"unproven approach override: {waypoint_id}")
                    try:
                        nav_pose = {
                            name: float(override["nav2_pose"].get(name, 0.0))
                            for name in ("x", "y", "z", "yaw")
                        }
                    except (AttributeError, TypeError, ValueError) as error:
                        raise SystemExit(
                            f"invalid approach override pose: {waypoint_id}"
                        ) from error
                    if not all(math.isfinite(value) for value in nav_pose.values()):
                        raise SystemExit(f"nonfinite approach override: {waypoint_id}")
                    if math.hypot(
                        nav_pose["x"] - source["x"], nav_pose["y"] - source["y"]
                    ) > args.max_projection_m:
                        raise SystemExit(f"approach override out of bounds: {waypoint_id}")
                    if not safe(nav_pose, map_data)[0]:
                        raise SystemExit(f"unsafe approach override: {waypoint_id}")
                    method = "validated_approach_override"
                elif not source_safe:
                    nav_pose = nearest_safe(source, map_data, args.max_projection_m)
                    if nav_pose is None:
                        raise SystemExit(
                            f"no safe projection within {args.max_projection_m}m: {waypoint_id}"
                        )
                    method = "bounded_known_free_projection"
                if override:
                    _, _, _, resolution, origin = map_data
                    override_cell = (
                        round((nav_pose["x"] - origin[0]) / resolution),
                        round((nav_pose["y"] - origin[1]) / resolution),
                    )
                    if override_cell not in _known_free_component(
                        navigation_start, map_data
                    ):
                        raise SystemExit(
                            f"approach override not start-connected: {waypoint_id}"
                        )
                    reachable = dict(nav_pose)
                else:
                    reachable = known_free_bfs_candidate(
                        navigation_start,
                        nav_pose,
                        map_data,
                        args.max_projection_m,
                        args.max_projection_m,
                    )
                if reachable is None:
                    raise SystemExit(
                        f"target not start-connected within {args.max_projection_m}m: "
                        f"{waypoint_id}"
                    )
                reachable_pose = {
                    name: reachable[name] for name in ("x", "y", "z", "yaw")
                }
                if math.hypot(
                    reachable_pose["x"] - nav_pose["x"],
                    reachable_pose["y"] - nav_pose["y"],
                ) > 1e-9:
                    nav_pose = reachable_pose
                    method = "start_component_bounded_projection"
                nav_safe, nav_reason = safe(nav_pose, map_data)
                distance = math.hypot(nav_pose["x"] - source["x"], nav_pose["y"] - source["y"])
                targets[key] = {
                    "waypoint_id": int(waypoint_id),
                    "room_id": str(room_id),
                    "source_pose": source,
                    "nav2_pose": nav_pose,
                    "source_footprint_safe": source_safe,
                    "source_failure_reason": None if source_safe else source_reason,
                    "projection_method": method,
                    "projection_distance_m": round(distance, 4),
                    "max_projection_m": args.max_projection_m,
                    "footprint_safe": nav_safe,
                    "failure_reason": None if nav_safe else nav_reason,
                }
            route.append(targets[key])
        tasks.append({
            "case_id": case["case_id"],
            "instruction": case["instruction"],
            "selected_model": selected["selected_model"],
            "waypoint_ids": case["waypoint_ids"],
            "route": route,
        })

    result = {
        "schema_version": "region-waypoint-missions-v1",
        "frame": "map",
        "navigation_start": navigation_start,
        "semantic_gate": {
            "cases": len(cases),
            "passes": selection["semantic_passes"],
            "selection_sha256": sha256(args.selection),
        },
        "geometry_gate": {
            "eligible": all(target["footprint_safe"] for target in targets.values()),
            "distinct_targets": len(targets),
            "start_connected_targets": len(targets),
            "total_waypoints": sum(len(task["route"]) for task in tasks),
            "max_projection_m": args.max_projection_m,
            "projected_targets": sum(
                target["projection_method"] != "source_pose" for target in targets.values()
            ),
        },
        "footprint": {"vertices": FOOTPRINT, "unknown_is_unsafe": True},
        "inputs": {
            path: sha256(path)
            for path in (
                args.corpus,
                args.selection,
                args.semantic_map,
                args.graph,
                args.original_graph,
                args.map,
            )
        },
        "targets": targets,
        "tasks": tasks,
    }
    if args.approach_overrides:
        result["inputs"][args.approach_overrides] = sha256(args.approach_overrides)
    unused_overrides = set(overrides) - set(targets)
    if unused_overrides:
        raise SystemExit(f"unused approach overrides: {sorted(unused_overrides)}")
    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["geometry_gate"]["eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
