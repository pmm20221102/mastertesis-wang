#!/usr/bin/env python3
"""Plan a PipelineResult task chain with Nav2; never command robot movement.

Usage (after sourcing ROS 2 Jazzy):
  ros2 run khronos_nav2_bridge plan_candidate_task_chain.py --input pipeline_result.json --floor-report /path/to/hospital_p1000/floor_0/report.json --output paths.json

Later navigation legs start at the previous selected goal only hypothetically.
Their paths do not show that the robot actually reached any earlier goal.
"""

import argparse
import contextlib
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import yaml

MAX_NAVIGATION_COORDINATE_M = 1_000_000.0
FLOOR_Z_TOLERANCE_M = 0.25
MAX_APPROACH_PROJECTION_M = 2.0
MAX_APPROACH_CANDIDATES = 64
MAX_SAFE_NAV2_FOOTPRINT_COST = 252


class PlannerStateUncertainError(RuntimeError):
    """Nav2 may still own an active path goal; no further goals are safe."""


def nav2_footprint_cost_is_safe(cost):
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        raise ValueError("cost must be an integer")
    if not math.isfinite(float(cost)) or not float(cost).is_integer():
        raise ValueError("cost must be an integer")
    cost = int(cost)
    if not 0 <= cost <= 255:
        raise ValueError("cost must be in the Nav2 costmap byte range 0..255")
    return cost <= MAX_SAFE_NAV2_FOOTPRINT_COST


def _reference(grounded_task, target_ref):
    if not isinstance(grounded_task, dict) or not isinstance(target_ref, str):
        return None
    if target_ref.startswith("step_"):
        parts = target_ref.split("_", 2)
        if len(parts) == 3 and parts[1].isdigit():
            steps = grounded_task.get("steps") or []
            index = int(parts[1])
            if index < len(steps) and isinstance(steps[index], dict):
                references = steps[index].get("references") or {}
                return references.get(parts[2]) if isinstance(references, dict) else None
        return None
    references = grounded_task.get("references") or {}
    return references.get(target_ref) if isinstance(references, dict) else None


def _goals(reference, step):
    if not isinstance(reference, dict):
        return {}
    candidate_ids = step.get("candidate_node_ids") or []
    if candidate_ids:
        if (not isinstance(candidate_ids, list)
                or any(not isinstance(node_id, str) or not node_id.strip()
                       for node_id in candidate_ids)
                or len(set(candidate_ids)) != len(candidate_ids)):
            raise ValueError("candidate_node_ids must contain non-empty strings")
        targets = reference.get("candidate_navigation_targets") or []
        if isinstance(targets, dict):
            if any(not isinstance(node_id, str) or not node_id.strip()
                   for node_id in targets):
                raise ValueError("candidate_navigation_targets keys must be non-empty strings")
            if any(not isinstance(target, dict) for target in targets.values()):
                raise ValueError("candidate_navigation_targets values must be navigation_target objects")
            lookup = targets
        elif isinstance(targets, list):
            if any(not isinstance(entry, dict)
                   or not isinstance(entry.get("node_id"), str)
                   or not entry["node_id"].strip()
                   or not isinstance(entry.get("navigation_target"), dict)
                   for entry in targets):
                raise ValueError("candidate_navigation_targets entries must contain node_id and navigation_target objects")
            target_ids = [entry["node_id"] for entry in targets]
            if len(set(target_ids)) != len(target_ids):
                raise ValueError("duplicate candidate node_id in candidate_navigation_targets")
            lookup = {entry["node_id"]: entry.get("navigation_target") for entry in targets}
        else:
            raise ValueError("candidate_navigation_targets must be an object or list")
        return {node_id: lookup.get(node_id) for node_id in sorted(set(candidate_ids))}
    node_id = step.get("target_id")
    if node_id and node_id == reference.get("node_id"):
        return {node_id: reference.get("navigation_target")}
    return {}


def _goal_pose(target):
    if not isinstance(target, dict) or target.get("frame_id") != "map":
        return None
    position = target.get("position")
    if not isinstance(position, (list, tuple)) or len(position) not in (2, 3):
        return None
    try:
        x, y = float(position[0]), float(position[1])
    except (OverflowError, TypeError, ValueError):
        return None
    if (not math.isfinite(x) or not math.isfinite(y)
            or abs(x) > MAX_NAVIGATION_COORDINATE_M
            or abs(y) > MAX_NAVIGATION_COORDINATE_M):
        return None
    if len(position) == 3:
        try:
            z = float(position[2])
        except (OverflowError, TypeError, ValueError):
            return None
        if not math.isfinite(z) or abs(z) > MAX_NAVIGATION_COORDINATE_M:
            return None
    has_quaternion = "orientation" in target or "quaternion" in target
    has_yaw = "yaw" in target
    if ("orientation" in target and "quaternion" in target) or (has_quaternion and has_yaw):
        return None
    if has_quaternion:
        orientation = _validated_quaternion(target.get("orientation", target.get("quaternion")))
        if orientation is None:
            return None
        source = "navigation_target_quaternion"
    elif has_yaw:
        try:
            yaw = float(target["yaw"])
        except (OverflowError, TypeError, ValueError):
            return None
        if not math.isfinite(yaw):
            return None
        orientation = {"x": 0.0, "y": 0.0,
                       "z": math.sin(yaw / 2.0), "w": math.cos(yaw / 2.0)}
        source = "navigation_target_yaw"
    else:
        orientation = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
        source = "default_identity"
    return {"x": x, "y": y, "orientation": orientation,
            "orientation_source": source}


def _target_floor_z(target):
    position = target.get("position") if isinstance(target, dict) else None
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        return None
    if isinstance(position[2], bool):
        return None
    try:
        value = float(position[2])
    except (OverflowError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) and abs(value) <= MAX_NAVIGATION_COORDINATE_M else None


def _target_floor_assignment(target):
    """Return validated explicit floor metadata, or legacy/invalid markers."""
    if not isinstance(target, dict) or "floor_id" not in target:
        return None, None, None
    floor_id = target.get("floor_id")
    source = target.get("floor_assignment_source")
    if (isinstance(floor_id, bool) or not isinstance(floor_id, int)
            or floor_id < 0 or not isinstance(source, str) or not source.strip()):
        return None, None, "invalid_floor_assignment"
    return floor_id, source.strip(), None


def load_floor_report(path):
    """Load the generated per-floor map report used by the active Nav2 map."""
    report_path = Path(path).expanduser().resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("invalid generated map floor report")
    floor = report.get("floor")
    raw_floor_z = report.get("floor_z")
    if isinstance(raw_floor_z, bool):
        raise ValueError("invalid generated map floor report")
    try:
        floor_z = float(raw_floor_z)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError("invalid generated map floor report") from error
    hashes = report.get("hashes")
    if (report.get("status") != "GENERATED"
            or isinstance(floor, bool) or not isinstance(floor, int) or floor < 0
            or not math.isfinite(floor_z)
            or abs(floor_z) > MAX_NAVIGATION_COORDINATE_M
            or not isinstance(hashes, dict)
            or not all(isinstance(hashes.get(key), str) and hashes[key]
                       for key in ("pgm", "yaml"))):
        raise ValueError("invalid generated map floor report")
    map_pgm_path = report_path.parent / "map.pgm"
    map_yaml_path = report_path.parent / "map.yaml"
    try:
        map_pgm_hash = hashlib.sha256(map_pgm_path.read_bytes()).hexdigest()
        map_yaml_hash = hashlib.sha256(map_yaml_path.read_bytes()).hexdigest()
    except OSError as error:
        raise ValueError("floor report map assets are missing") from error
    if hashes["pgm"] != map_pgm_hash or hashes["yaml"] != map_yaml_hash:
        raise ValueError("floor report hashes do not match map files")
    try:
        map_metadata = yaml.safe_load(map_yaml_path.read_text(encoding="utf-8"))
        image_name = map_metadata.get("image") if isinstance(map_metadata, dict) else None
        image_path = (map_yaml_path.parent / image_name).resolve(strict=True)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as error:
        raise ValueError("invalid map yaml image reference") from error
    if image_path != map_pgm_path.resolve(strict=True):
        raise ValueError("YAML image does not match verified map.pgm")
    return {"floor": floor, "floor_z": floor_z,
            "report_path": str(report_path),
            "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "map_yaml_path": str(map_yaml_path.resolve()),
            "map_pgm_path": str(map_pgm_path.resolve()),
            "map_pgm_sha256": map_pgm_hash,
            "map_yaml_sha256": map_yaml_hash}


def read_pgm(path):
    """Read the project's 8-bit binary PGM map (P5), fail closed otherwise."""
    data = Path(path).read_bytes()
    offset = 0
    whitespace = b" \t\r\n\v\f"

    def token():
        nonlocal offset
        while offset < len(data):
            if data[offset] in whitespace:
                offset += 1
            elif data[offset] == ord("#"):
                newline = data.find(b"\n", offset)
                if newline < 0:
                    raise ValueError("malformed PGM comment")
                offset = newline + 1
            else:
                break
        start = offset
        while offset < len(data) and data[offset] not in whitespace + b"#":
            offset += 1
        if start == offset:
            raise ValueError("malformed PGM header")
        return data[start:offset].decode("ascii")

    magic = token()
    if magic != "P5":
        raise ValueError("only binary PGM P5 maps are supported")
    width, height, max_value = int(token()), int(token()), int(token())
    if width <= 0 or height <= 0 or max_value != 255:
        raise ValueError("unsupported PGM dimensions or max value")
    if offset >= len(data) or data[offset] not in whitespace:
        raise ValueError("missing PGM raster delimiter")
    if data[offset:offset + 2] == b"\r\n":
        offset += 2
    else:
        offset += 1
    raster = data[offset:]
    if len(raster) != width * height:
        raise ValueError("PGM raster size does not match its dimensions")
    return list(raster), width, height


def load_floor_map(floor_contract):
    """Load the report-verified floor occupancy grid for approach checking."""
    yaml_path = Path(floor_contract["map_yaml_path"]).resolve(strict=True)
    metadata = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    resolution = float(metadata["resolution"])
    origin = metadata["origin"]
    if (not math.isfinite(resolution) or resolution <= 0
            or not isinstance(origin, list) or len(origin) < 2):
        raise ValueError("invalid floor map geometry")
    pgm_path = (yaml_path.parent / metadata["image"]).resolve(strict=True)
    if pgm_path != Path(floor_contract["map_pgm_path"]).resolve(strict=True):
        raise ValueError("floor map image does not match the verified PGM")
    if hashlib.sha256(pgm_path.read_bytes()).hexdigest() != floor_contract["map_pgm_sha256"]:
        raise ValueError("floor PGM changed after report validation")
    values, width, height = read_pgm(pgm_path)
    return values, width, height, resolution, [float(origin[0]), float(origin[1])]


def _yaw_from_quaternion(orientation):
    return math.atan2(
        2.0 * (orientation["w"] * orientation["z"]
               + orientation["x"] * orientation["y"]),
        1.0 - 2.0 * (orientation["y"] ** 2 + orientation["z"] ** 2),
    )


def verify_active_map_yaml(expected_map_yaml, active_map_yaml):
    """Fail closed unless Nav2's map_server loaded the reported map file."""
    try:
        expected = Path(expected_map_yaml).expanduser().resolve(strict=True)
        active = Path(active_map_yaml).expanduser().resolve(strict=True)
    except (OSError, TypeError, ValueError) as error:
        raise RuntimeError("cannot resolve active Nav2 map yaml") from error
    if expected != active:
        raise RuntimeError("active Nav2 map does not match floor report")


def project_approach_goals(original_goal, navigation_start, map_data,
                           include_costmap_fallbacks=False):
    """Return bounded, footprint-safe, start-connected goals around a blocked target."""
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from navigation_grid_geometry import safe
    from prepare_region_waypoint_missions import approach_candidates

    orientation = original_goal["orientation"]
    yaw = _yaw_from_quaternion(orientation)
    source = {"x": original_goal["x"], "y": original_goal["y"],
              "z": 0.0, "yaw": yaw}
    source_is_static_safe = safe(source, map_data)[0]
    source_goal = {
            "goal": original_goal,
            "projection_method": "source_target",
            "projection_distance_m": 0.0,
            "start_connected": True,
        }
    if source_is_static_safe and not include_costmap_fallbacks:
        return [source_goal]
    approaches = approach_candidates(
        source, map_data, navigation_start,
        max_distance=MAX_APPROACH_PROJECTION_M,
        yaw_steps=16,
        limit=MAX_APPROACH_CANDIDATES,
    )
    result = [source_goal] if source_is_static_safe else []
    for approach in approaches:
        pose = approach["nav2_pose"]
        projected_yaw = float(pose["yaw"])
        result.append({
            "goal": {
                "x": float(pose["x"]), "y": float(pose["y"]),
                "orientation": {"x": 0.0, "y": 0.0,
                                "z": math.sin(projected_yaw / 2.0),
                                "w": math.cos(projected_yaw / 2.0)},
                "orientation_source": "bounded_approach_projection_yaw",
            },
            "projection_method": "bounded_approach_projection",
            "projection_distance_m": approach["projection_distance_m"],
            "clearance_m": approach["clearance_m"],
            "cell": list(approach["cell"]),
            "yaw_index": approach["yaw_index"],
            "start_connected": approach["start_connected"],
        })
    return result


def point_map_cell_is_free(x, y, map_data):
    """Check only the occupancy cell under a zero-size point pose."""
    values, width, height, resolution, origin = map_data
    try:
        x, y = float(x), float(y)
        resolution = float(resolution)
        origin_x, origin_y = float(origin[0]), float(origin[1])
    except (IndexError, OverflowError, TypeError, ValueError):
        return False
    if (not all(math.isfinite(v) for v in (x, y, resolution, origin_x, origin_y))
            or resolution <= 0):
        return False
    cell_x = round((x - origin_x) / resolution)
    cell_y = round((y - origin_y) / resolution)
    if not (0 <= cell_x < width and 0 <= cell_y < height):
        return False
    return values[(height - 1 - cell_y) * width + cell_x] >= 250


def project_point_approach_goals(original_goal, navigation_start, map_data,
                                 include_costmap_fallbacks=True):
    """Project a target to reachable free cell centers without robot geometry."""
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from prepare_region_waypoint_missions import _known_free_component

    values, width, height, resolution, origin = map_data
    source_x, source_y = float(original_goal["x"]), float(original_goal["y"])
    if not math.isfinite(source_x) or not math.isfinite(source_y):
        return []
    connected = _known_free_component(navigation_start, map_data)
    source_cell = (round((source_x - origin[0]) / resolution),
                   round((source_y - origin[1]) / resolution))
    source_is_reachable_free = (
        source_cell in connected and point_map_cell_is_free(source_x, source_y, map_data))
    source_goal = {
        "goal": original_goal,
        "projection_method": "source_point_target",
        "projection_distance_m": 0.0,
        "start_connected": source_is_reachable_free,
    }
    if source_is_reachable_free and not include_costmap_fallbacks:
        return [source_goal]

    center_x = round((source_x - origin[0]) / resolution)
    center_y = round((source_y - origin[1]) / resolution)
    span = int(math.ceil(MAX_APPROACH_PROJECTION_M / resolution)) + 1
    candidates = []
    for cell_y in range(max(0, center_y - span), min(height, center_y + span + 1)):
        for cell_x in range(max(0, center_x - span), min(width, center_x + span + 1)):
            cell = (cell_x, cell_y)
            if cell not in connected or values[(height - 1 - cell_y) * width + cell_x] < 250:
                continue
            x = origin[0] + (cell_x + 0.5) * resolution
            y = origin[1] + (cell_y + 0.5) * resolution
            distance = math.hypot(x - source_x, y - source_y)
            if distance > MAX_APPROACH_PROJECTION_M:
                continue
            candidates.append((distance, cell_y, cell_x, x, y))
    candidates.sort()

    result = [source_goal] if source_is_reachable_free else []
    seen = {source_cell} if source_is_reachable_free else set()
    orientation = original_goal.get("orientation")
    for distance, cell_y, cell_x, x, y in candidates:
        cell = (cell_x, cell_y)
        if cell in seen:
            continue
        seen.add(cell)
        result.append({
            "goal": {"x": x, "y": y, "orientation": orientation,
                     "orientation_source": "point_robot_cell_projection"},
            "projection_method": "point_robot_cell_projection",
            "projection_distance_m": round(distance, 4),
            "cell": [cell_x, cell_y],
            "start_connected": True,
        })
        if len(result) >= MAX_APPROACH_CANDIDATES:
            break
    return result


def map_server_yaml_filename(response, string_type):
    values = getattr(response, "values", None)
    if not isinstance(values, list) or len(values) != 1:
        raise RuntimeError("invalid map_server yaml_filename parameter response")
    value = values[0]
    filename = getattr(value, "string_value", "")
    if getattr(value, "type", None) != string_type or not filename:
        raise RuntimeError("invalid map_server yaml_filename parameter response")
    return filename


def _validated_quaternion(value):
    """Return finite unit quaternion fields from a mapping or ROS message."""
    if isinstance(value, dict):
        if set(value) != {"x", "y", "z", "w"}:
            return None
        components = value
    else:
        components = {axis: getattr(value, axis, None) for axis in ("x", "y", "z", "w")}
    try:
        quaternion = {axis: float(components[axis]) for axis in ("x", "y", "z", "w")}
    except (KeyError, OverflowError, TypeError, ValueError):
        return None
    norm = math.hypot(*quaternion.values())
    if (not all(math.isfinite(component) for component in quaternion.values())
            or not math.isfinite(norm)
            or not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3)):
        return None
    return quaternion


def _path_length(poses):
    points = []
    for pose in poses:
        if not isinstance(pose, dict):
            return None
        try:
            x, y = float(pose["x"]), float(pose["y"])
        except (KeyError, OverflowError, TypeError, ValueError):
            return None
        if (not math.isfinite(x) or not math.isfinite(y)
                or abs(x) > MAX_NAVIGATION_COORDINATE_M
                or abs(y) > MAX_NAVIGATION_COORDINATE_M):
            return None
        points.append((x, y))
    if not points:
        return None
    length = 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        segment = math.hypot(bx - ax, by - ay)
        if not math.isfinite(segment):
            return None
        length += segment
        if not math.isfinite(length):
            return None
    return length


def plan_task_chain(pipeline, live_start, compute_path, *, map_floor, map_floor_z,
                    map_data=None, allow_costmap_fallbacks=False,
                    point_robot=False):
    """Select shortest feasible candidate per Nav2 step using explicit starts.

    compute_path(start_xy, goal_xy) returns {status, path: [{x, y}, ...]}.
    It is called for every candidate. A rejected, failed or empty path is infeasible.
    """
    planning_started = time.perf_counter()
    if not isinstance(point_robot, bool):
        raise ValueError("point_robot must be a boolean")
    if not isinstance(pipeline, dict) or pipeline.get("status") != "valid":
        raise ValueError("PipelineResult status must be valid")
    task_plan = pipeline.get("task_plan")
    if not isinstance(task_plan, dict):
        raise ValueError("PipelineResult task_plan must be an object")
    if task_plan.get("status") != "valid":
        raise ValueError("task_plan status must be valid")
    chain = task_plan.get("task_chain")
    if not isinstance(chain, list):
        raise ValueError("task_plan.task_chain must be a list")
    try:
        start = {"x": float(live_start["x"]), "y": float(live_start["y"])}
    except (KeyError, OverflowError, TypeError, ValueError) as error:
        raise ValueError("invalid live TF start") from error
    if (not all(math.isfinite(value) for value in start.values())
            or any(abs(value) > MAX_NAVIGATION_COORDINATE_M for value in start.values())):
        raise ValueError("invalid live TF start")
    if isinstance(map_floor, bool) or not isinstance(map_floor, int) or map_floor < 0:
        raise ValueError("map_floor must be a non-negative integer")
    if isinstance(map_floor_z, bool):
        raise ValueError("map_floor_z must be finite")
    try:
        map_floor_z = float(map_floor_z)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError("map_floor_z must be finite") from error
    if not math.isfinite(map_floor_z) or abs(map_floor_z) > MAX_NAVIGATION_COORDINATE_M:
        raise ValueError("map_floor_z must be finite")
    live_orientation = None
    if "orientation" in live_start:
        live_orientation = _validated_quaternion(live_start["orientation"])
        if live_orientation is None:
            raise ValueError("invalid live TF orientation")
    if map_data is not None:
        if point_robot:
            if not point_map_cell_is_free(start["x"], start["y"], map_data):
                raise ValueError("live TF start is not point-safe on active map")
        else:
            scripts_dir = str(Path(__file__).resolve().parent)
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from navigation_grid_geometry import safe as footprint_safe
            start_orientation = live_orientation or {"x": 0.0, "y": 0.0,
                                                      "z": 0.0, "w": 1.0}
            start_pose = {"x": start["x"], "y": start["y"], "z": 0.0,
                          "yaw": _yaw_from_quaternion(start_orientation)}
            if not footprint_safe(start_pose, map_data)[0]:
                raise ValueError("live TF start is not footprint-safe on active map")
    grounded = pipeline.get("grounded_task")
    if not isinstance(grounded, dict):
        raise ValueError("PipelineResult grounded_task must be an object")
    if not isinstance(grounded.get("references", {}), dict):
        raise ValueError("grounded_task.references must be an object")
    if not isinstance(grounded.get("steps", []), list):
        raise ValueError("grounded_task.steps must be a list")
    rows = []
    blocked = False
    has_prior_goal = False
    for index, step in enumerate(chain):
        step_started = time.perf_counter()
        if not isinstance(step, dict):
            raise ValueError(f"task_chain step {index + 1} must be an object")
        candidate_ids = step.get("candidate_node_ids", [])
        if (not isinstance(candidate_ids, list)
                or any(not isinstance(node_id, str) or not node_id.strip()
                       for node_id in candidate_ids)
                or len(set(candidate_ids)) != len(candidate_ids)):
            raise ValueError(f"task_chain step {index + 1} candidate_node_ids must contain non-empty strings")
        row = {"step": step.get("step"), "action": step.get("action"),
               "target_ref": step.get("target_ref"), "actual_arrival_confirmed": False}
        if step.get("executor") != "nav2":
            row["status"] = "not_navigation"
        elif blocked:
            row["status"] = "blocked_by_prior_step"
        else:
            row["start"] = dict(start)
            row["start_source"] = ("hypothetical_previous_goal" if has_prior_goal
                                   else "live_tf_map_to_base_link")
            navigation_ref = ((step.get("role_refs") or {}).get("destination")
                              if step.get("action") in ("escort_to", "guide_to")
                              else step.get("target_ref"))
            reference = _reference(grounded, navigation_ref)
            goals = _goals(reference, step)
            row["candidates"] = []
            for node_id, target in goals.items():
                goal = _goal_pose(target)
                target_floor_z = _target_floor_z(target)
                explicit_floor_id, floor_assignment_source, floor_error = (
                    _target_floor_assignment(target))
                candidate = {"node_id": node_id, "goal": goal,
                             "target_floor_z": target_floor_z,
                             "planning_latency_ms": 0.0}
                if floor_error:
                    candidate["status"] = floor_error
                elif explicit_floor_id is not None:
                    candidate["assigned_floor_id"] = explicit_floor_id
                    candidate["floor_assignment_source"] = floor_assignment_source
                    if target_floor_z is not None:
                        candidate["floor_z_delta_m"] = abs(target_floor_z - map_floor_z)
                if candidate.get("status"):
                    pass
                elif goal is None:
                    candidate["status"] = "invalid_navigation_target"
                elif explicit_floor_id is not None and explicit_floor_id != map_floor:
                    candidate["status"] = "wrong_floor"
                elif explicit_floor_id is None and target_floor_z is None:
                    candidate["status"] = "missing_floor_elevation"
                elif (explicit_floor_id is None
                      and abs(target_floor_z - map_floor_z) > FLOOR_Z_TOLERANCE_M):
                    candidate["status"] = "wrong_floor"
                else:
                    candidate_started = time.perf_counter()
                    try:
                        request_start = dict(start)
                        if not has_prior_goal and live_orientation is not None:
                            request_start["orientation"] = live_orientation
                        if map_data is None:
                            approach_goals = [{
                                "goal": goal, "projection_method": "source_target",
                                "projection_distance_m": 0.0,
                                "start_connected": True,
                            }]
                        else:
                            navigation_start = {
                                **start,
                                "yaw": _yaw_from_quaternion(request_start.get(
                                    "orientation", goal["orientation"])),
                            }
                            if point_robot:
                                approach_goals = project_point_approach_goals(
                                    goal, navigation_start, map_data,
                                    include_costmap_fallbacks=allow_costmap_fallbacks)
                            else:
                                approach_goals = project_approach_goals(
                                    goal, navigation_start, map_data,
                                    include_costmap_fallbacks=allow_costmap_fallbacks)
                    except PlannerStateUncertainError:
                        raise
                    except Exception as error:
                        candidate.update(status="planner_error", error=str(error))
                    else:
                        candidate["approaches"] = []
                        if not approach_goals:
                            candidate["status"] = "no_safe_approach_within_2m"
                        else:
                            for approach in approach_goals:
                                approach_result = dict(approach)
                                approach_started = time.perf_counter()
                                try:
                                    response = compute_path(request_start, approach["goal"])
                                    status = response.get("status") if isinstance(response, dict) else None
                                    poses = response.get("path") if isinstance(response, dict) else None
                                    cost = response.get("costmap_footprint_cost") if isinstance(response, dict) else None
                                    if cost is not None:
                                        nav2_footprint_cost_is_safe(cost)
                                        approach_result["costmap_footprint_cost"] = cost
                                except PlannerStateUncertainError:
                                    raise
                                except Exception as error:
                                    approach_result.update(status="planner_error", error=str(error))
                                else:
                                    if status != "succeeded":
                                        approach_result["status"] = status or "planner_error"
                                    elif not isinstance(poses, list) or not poses:
                                        approach_result["status"] = "empty_path"
                                    else:
                                        length = _path_length(poses)
                                        if length is None:
                                            approach_result["status"] = "invalid_path_length"
                                        else:
                                            approach_result.update(status="path_found", path=poses,
                                                                   path_length_m=length)
                                finally:
                                    approach_result["planning_latency_ms"] = max(
                                        0.0, (time.perf_counter() - approach_started) * 1000.0)
                                candidate["approaches"].append(approach_result)
                            feasible_approaches = [
                                approach for approach in candidate["approaches"]
                                if approach["status"] == "path_found"
                            ]
                            if feasible_approaches:
                                selected_approach = min(
                                    feasible_approaches,
                                    key=lambda approach: (
                                        approach["path_length_m"],
                                        approach["projection_distance_m"],
                                        tuple(approach.get("cell", [])),
                                        approach.get("yaw_index", 0),
                                    ),
                                )
                                candidate.update(
                                    status="path_found",
                                    path=selected_approach["path"],
                                    path_length_m=selected_approach["path_length_m"],
                                    projected_goal=selected_approach["goal"],
                                    projection={
                                        "method": selected_approach["projection_method"],
                                        "distance_m": selected_approach["projection_distance_m"],
                                        **{key: selected_approach[key]
                                           for key in ("clearance_m", "cell", "yaw_index",
                                                       "start_connected")
                                           if key in selected_approach},
                                    },
                                )
                            else:
                                if candidate["approaches"] and all(
                                        approach["status"] == "unsafe_goal_costmap"
                                        for approach in candidate["approaches"]):
                                    candidate["status"] = "no_costmap_safe_approach"
                                elif (len(candidate["approaches"]) == 1
                                      and candidate["approaches"][0]["projection_method"] == "source_target"):
                                    candidate["status"] = candidate["approaches"][0]["status"]
                                else:
                                    candidate["status"] = "no_feasible_path"
                    finally:
                        candidate["planning_latency_ms"] = max(
                            0.0, (time.perf_counter() - candidate_started) * 1000.0)
                row["candidates"].append(candidate)
            feasible = [item for item in row["candidates"]
                        if item["status"] == "path_found"]
            if feasible:
                chosen = min(feasible, key=lambda item: (item["path_length_m"],
                                                         item["node_id"]))
                row.update(status="path_found", selected_node_id=chosen["node_id"],
                           original_goal=chosen["goal"],
                           goal=chosen["projected_goal"],
                           projection=chosen["projection"],
                           path=chosen["path"], path_length_m=chosen["path_length_m"])
                start = {key: chosen["projected_goal"][key]
                         for key in ("x", "y", "orientation")}
                has_prior_goal = True
            else:
                if not goals:
                    row["status"] = "missing_navigation_target"
                elif all(candidate["status"] in ("wrong_floor", "missing_floor_elevation")
                         for candidate in row["candidates"]):
                    row["status"] = "no_candidate_on_active_floor"
                elif all(candidate["status"] in (
                        "wrong_floor", "missing_floor_elevation", "no_safe_approach_within_2m",
                        "no_costmap_safe_approach")
                         for candidate in row["candidates"]):
                    row["status"] = ("no_costmap_safe_approach"
                                      if any(candidate["status"] == "no_costmap_safe_approach"
                                             for candidate in row["candidates"])
                                      else "no_safe_approach_within_2m")
                else:
                    row["status"] = "no_feasible_path"
                blocked = True
        row["planning_latency_ms"] = max(
            0.0, (time.perf_counter() - step_started) * 1000.0)
        rows.append(row)
    complete = all(row["status"] in ("path_found", "not_navigation") for row in rows)
    return {"status": "planning_only_complete" if complete else "planning_only_incomplete",
            "map_floor": map_floor, "map_floor_z": map_floor_z,
            "floor_z_tolerance_m": FLOOR_Z_TOLERANCE_M,
            "planning_latency_ms": max(0.0, (time.perf_counter() - planning_started) * 1000.0),
            "movement_executed": False, "steps": rows}


def _cancel_and_confirm_goal(handle, result_future, wait, succeeded_status,
                             canceled_status, aborted_status):
    try:
        cancellation = handle.cancel_goal_async()
        response = wait(cancellation, "goal cancellation")
        matching = response and any(
            info.goal_id == handle.goal_id for info in response.goals_canceling)
        if not matching:
            terminal_future = result_future or handle.get_result_async()
            terminal = wait(terminal_future, "goal result after cancel race")
            if terminal is None or terminal.status not in (
                    succeeded_status, canceled_status, aborted_status):
                raise PlannerStateUncertainError("goal cancellation unconfirmed")
            return terminal
        terminal_future = result_future or handle.get_result_async()
        terminal = wait(terminal_future, "canceled goal result")
        if terminal is None or terminal.status not in (
                succeeded_status, canceled_status, aborted_status):
            raise PlannerStateUncertainError("goal terminal state unconfirmed")
        return terminal
    except Exception as error:
        raise PlannerStateUncertainError("goal cancellation or result terminal state uncertain") from error


def _request_path(client, request, wait, succeeded_status, canceled_status,
                  aborted_status=6):
    """Return one path result, or fail closed if goal liveness is unknown."""
    try:
        acceptance = client.send_goal_async(request)
        handle = wait(acceptance, "goal acceptance")
    except Exception as error:
        raise PlannerStateUncertainError("goal acceptance state uncertain") from error
    if handle is None or not isinstance(getattr(handle, "accepted", None), bool):
        raise PlannerStateUncertainError("goal acceptance state uncertain")
    if not handle.accepted:
        return {"status": "rejected", "path": []}
    result_future = None
    try:
        result_future = handle.get_result_async()
        wrapped = wait(result_future, "path result")
    except TimeoutError:
        wrapped = _cancel_and_confirm_goal(handle, result_future, wait,
                                           succeeded_status, canceled_status,
                                           aborted_status)
        if wrapped.status == canceled_status:
            return {"status": "canceled_after_timeout", "path": []}
    except Exception as error:
        if isinstance(error, PlannerStateUncertainError):
            raise
        wrapped = _cancel_and_confirm_goal(handle, result_future, wait,
                                           succeeded_status, canceled_status,
                                           aborted_status)
        if wrapped.status != succeeded_status:
            return {"status": "planner_error_after_cancel", "path": [], "error": str(error)}
    if wrapped is None:
        raise PlannerStateUncertainError("goal result state uncertain")
    status = getattr(wrapped, "status", None)
    if status not in (succeeded_status, canceled_status, aborted_status):
        raise PlannerStateUncertainError("goal terminal state uncertain")
    if status != succeeded_status:
        return {"status": "failed", "path": []}
    return {"status": "succeeded", "path": [
        {"x": pose.pose.position.x, "y": pose.pose.position.y}
        for pose in wrapped.result.path.poses]}


def ros_plan(pipeline, timeout, planner_id, floor_contract):
    """Query live TF and Nav2 ComputePathToPose without execution clients."""
    if not isinstance(floor_contract, dict):
        raise ValueError("valid map floor report contract is required")
    floor = floor_contract.get("floor")
    raw_floor_z = floor_contract.get("floor_z")
    if isinstance(raw_floor_z, bool):
        raise ValueError("valid map floor report contract is required")
    try:
        floor_z = float(raw_floor_z)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError("valid map floor report contract is required") from error
    if (isinstance(floor, bool) or not isinstance(floor, int) or floor < 0
            or not math.isfinite(floor_z)
            or not isinstance(floor_contract.get("report_path"), str)
            or not isinstance(floor_contract.get("report_sha256"), str)
            or not isinstance(floor_contract.get("map_yaml_path"), str)
            or not isinstance(floor_contract.get("map_pgm_sha256"), str)
            or not isinstance(floor_contract.get("map_yaml_sha256"), str)):
        raise ValueError("valid map floor report contract is required")
    map_data = load_floor_map(floor_contract)
    import rclpy
    import tf2_ros
    from action_msgs.msg import GoalStatus
    from geometry_msgs.msg import PoseStamped
    from rcl_interfaces.msg import ParameterType
    from nav2_msgs.action import ComputePathToPose
    from nav2_msgs.srv import GetCost
    from rclpy.action import ActionClient
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from rclpy.parameter_client import AsyncParameterClient

    class Planner(Node):
        def __init__(self):
            super().__init__("candidate_task_chain_planner")
            self._planning_executor = None
            try:
                self.tf_buffer = tf2_ros.Buffer()
                self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self,
                                                             spin_thread=False)
                self._planning_executor = SingleThreadedExecutor()
                self._planning_executor.add_node(self)
                self.map_parameters = AsyncParameterClient(self, "/map_server")
                self.cost_client = self.create_client(
                    GetCost, "/global_costmap/get_cost_global_costmap")
                self.path_client = ActionClient(self, ComputePathToPose,
                                                "/compute_path_to_pose")
            except Exception:
                if self._planning_executor is not None:
                    with contextlib.suppress(Exception):
                        self._planning_executor.remove_node(self)
                    with contextlib.suppress(Exception):
                        self._planning_executor.shutdown()
                with contextlib.suppress(Exception):
                    self.destroy_node()
                raise

        def wait(self, future, label):
            self._planning_executor.spin_until_future_complete(future, timeout_sec=timeout)
            if not future.done():
                raise TimeoutError(f"timeout waiting for {label}")
            return future.result()

        def current_start(self):
            deadline = time.monotonic() + timeout
            while not self.tf_buffer.can_transform("map", "base_link",
                                                    rclpy.time.Time()):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("map->base_link TF unavailable")
                self._planning_executor.spin_once(timeout_sec=min(0.1, remaining))
            transform = self.tf_buffer.lookup_transform("map", "base_link",
                                                        rclpy.time.Time())
            return {"x": transform.transform.translation.x,
                    "y": transform.transform.translation.y,
                    "orientation": transform.transform.rotation}

        def verify_active_map(self):
            if not self.map_parameters.wait_for_services(timeout_sec=timeout):
                raise RuntimeError("/map_server parameter services unavailable")
            values = self.wait(self.map_parameters.get_parameters(["yaml_filename"]),
                               "map_server yaml_filename")
            active_map_yaml = map_server_yaml_filename(
                values, ParameterType.PARAMETER_STRING)
            verify_active_map_yaml(floor_contract["map_yaml_path"], active_map_yaml)
            if not self.cost_client.wait_for_service(timeout_sec=timeout):
                raise RuntimeError("/global_costmap/get_cost_global_costmap unavailable")

        def footprint_cost(self, pose):
            request = GetCost.Request()
            request.use_footprint = True
            request.x = pose["x"]
            request.y = pose["y"]
            request.theta = _yaw_from_quaternion(pose["orientation"])
            response = self.wait(self.cost_client.call_async(request),
                                 "global costmap footprint cost")
            return response.cost

        def pose(self, xy):
            message = PoseStamped()
            message.header.frame_id = "map"
            message.header.stamp = self.get_clock().now().to_msg()
            message.pose.position.x = xy["x"]
            message.pose.position.y = xy["y"]
            orientation = xy.get("orientation")
            if isinstance(orientation, dict):
                for axis in ("x", "y", "z", "w"):
                    setattr(message.pose.orientation, axis, orientation[axis])
            elif orientation is not None:
                message.pose.orientation = orientation
            else:
                message.pose.orientation.w = 1.0
            return message

        def compute(self, start, goal):
            cost = self.footprint_cost(goal)
            if not nav2_footprint_cost_is_safe(cost):
                return {"status": "unsafe_goal_costmap", "path": [],
                        "costmap_footprint_cost": cost}
            request = ComputePathToPose.Goal()
            request.start = self.pose(start)
            request.goal = self.pose(goal)
            request.use_start = True
            request.planner_id = planner_id
            result = _request_path(self.path_client, request, self.wait,
                                   GoalStatus.STATUS_SUCCEEDED, GoalStatus.STATUS_CANCELED,
                                   GoalStatus.STATUS_ABORTED)
            result["costmap_footprint_cost"] = cost
            return result

    rclpy.init()
    planner = None
    try:
        planner = Planner()
        planner.verify_active_map()
        if not planner.path_client.wait_for_server(timeout_sec=timeout):
            raise RuntimeError("/compute_path_to_pose unavailable")
        live = planner.current_start()
        start_orientation = _validated_quaternion(live["orientation"])
        if start_orientation is None:
            raise RuntimeError("live TF start orientation is invalid")
        start_cost = planner.footprint_cost({
            "x": live["x"], "y": live["y"],
            "orientation": start_orientation,
        })
        if not nav2_footprint_cost_is_safe(start_cost):
            raise RuntimeError(f"live TF start is unsafe in Nav2 costmap (cost={start_cost})")
        result = plan_task_chain(pipeline, live, planner.compute,
                                 map_floor=floor,
                                 map_floor_z=floor_z,
                                 map_data=map_data)
        result["map_floor_report"] = {
            "path": floor_contract.get("report_path"),
            "sha256": floor_contract.get("report_sha256"),
            "map_yaml_path": floor_contract.get("map_yaml_path"),
            "map_pgm_sha256": floor_contract.get("map_pgm_sha256"),
            "map_yaml_sha256": floor_contract.get("map_yaml_sha256"),
        }
        result["live_start_costmap_footprint_cost"] = start_cost
        return result
    finally:
        if planner is not None:
            planner._planning_executor.remove_node(planner)
            planner._planning_executor.shutdown()
            planner.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--planner-id", default="GridBased")
    parser.add_argument("--floor-report", required=True, type=Path,
                        help="report.json matching the active Hospital P1000 floor map")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or not math.isfinite(args.timeout):
        parser.error("--timeout must be positive and finite")
    floor_contract = load_floor_report(args.floor_report)
    result = ros_plan(json.loads(args.input.read_text(encoding="utf-8")),
                      args.timeout, args.planner_id, floor_contract)
    encoded = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)


if __name__ == "__main__":
    main()
