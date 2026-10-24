"""Pure validation and route compilation for Hospital P1000 floor maps."""

from collections import deque
import hashlib
import math
from pathlib import Path
import re

import yaml


MAP_BUNDLE_SCHEMA = "hospital-floor-transitions-v1"
MISSION_SCHEMA = "hospital-multifloor-mission-v1"
MAX_COORDINATE_M = 1_000_000.0
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _floor_id(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _pose(value, name, frame_id):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    if value.get("frame_id", frame_id) != frame_id:
        raise ValueError(f"{name}.frame_id must be {frame_id}")
    result = {"frame_id": frame_id}
    for key in ("x", "y", "yaw"):
        raw = value.get(key)
        if isinstance(raw, bool):
            raise ValueError(f"{name}.{key} must be finite")
        try:
            number = float(raw)
        except (OverflowError, TypeError, ValueError) as error:
            raise ValueError(f"{name}.{key} must be finite") from error
        if not math.isfinite(number) or abs(number) > MAX_COORDINATE_M:
            raise ValueError(f"{name}.{key} must be finite")
        result[key] = number
    result["yaw"] = math.atan2(math.sin(result["yaw"]), math.cos(result["yaw"]))
    return result


def _navigation_target(value, name, frame_id):
    if not isinstance(value, dict) or value.get("frame_id") != frame_id:
        raise ValueError(f"{name}.frame_id must be {frame_id}")
    position = value.get("position")
    if not isinstance(position, (list, tuple)) or len(position) not in (2, 3):
        raise ValueError(f"{name}.position must contain x, y and optional z")
    normalized_position = []
    for axis, raw in zip(("x", "y", "z"), position):
        if isinstance(raw, bool):
            raise ValueError(f"{name}.position.{axis} must be finite")
        try:
            number = float(raw)
        except (OverflowError, TypeError, ValueError) as error:
            raise ValueError(f"{name}.position.{axis} must be finite") from error
        if not math.isfinite(number) or abs(number) > MAX_COORDINATE_M:
            raise ValueError(f"{name}.position.{axis} must be finite")
        normalized_position.append(number)
    return {**value, "position": normalized_position, "frame_id": frame_id}


def _verify_map_files(floor_id, config):
    yaml_path = Path(config["map_yaml_path"]).expanduser().resolve(strict=True)
    pgm_path = Path(config["map_pgm_path"]).expanduser().resolve(strict=True)
    report_path = Path(config["report_path"]).expanduser().resolve(strict=True)
    actual_yaml_hash = hashlib.sha256(yaml_path.read_bytes()).hexdigest()
    actual_pgm_hash = hashlib.sha256(pgm_path.read_bytes()).hexdigest()
    if actual_yaml_hash != config["map_yaml_sha256"]:
        raise ValueError(f"floor {floor_id} map YAML hash mismatch")
    if actual_pgm_hash != config["map_pgm_sha256"]:
        raise ValueError(f"floor {floor_id} map PGM hash mismatch")
    metadata = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    image_path = (yaml_path.parent / metadata["image"]).resolve(strict=True)
    if image_path != pgm_path:
        raise ValueError(f"floor {floor_id} map YAML references a different PGM")
    if not report_path.is_file():
        raise ValueError(f"floor {floor_id} report is not a file")


def validate_transition_bundle(bundle, verify_files=True):
    """Validate floor maps and directed portal edges; return normalized data."""
    if not isinstance(bundle, dict) or bundle.get("schema_version") != MAP_BUNDLE_SCHEMA:
        raise ValueError(f"schema_version must be {MAP_BUNDLE_SCHEMA}")
    bundle_id = bundle.get("map_bundle_id")
    frame_id = bundle.get("frame_id")
    floors = bundle.get("floors")
    transitions = bundle.get("transitions")
    if not isinstance(bundle_id, str) or not bundle_id.strip():
        raise ValueError("map_bundle_id must be a non-empty string")
    if not isinstance(frame_id, str) or not frame_id.strip():
        raise ValueError("frame_id must be a non-empty string")
    if not isinstance(floors, dict) or not floors:
        raise ValueError("floors must be a non-empty object")
    normalized_floors = {}
    for raw_floor, raw_config in floors.items():
        try:
            floor = int(raw_floor)
        except (TypeError, ValueError) as error:
            raise ValueError("floor keys must be non-negative integers") from error
        _floor_id(floor, "floor key")
        if str(floor) in normalized_floors:
            raise ValueError(f"duplicate floor key {floor}")
        if not isinstance(raw_config, dict):
            raise ValueError(f"floor {floor} configuration must be an object")
        config = dict(raw_config)
        for key in ("report_path", "map_yaml_path", "map_pgm_path"):
            if not isinstance(config.get(key), str) or not config[key].strip():
                raise ValueError(f"floor {floor} {key} must be a non-empty path")
        for key in ("map_yaml_sha256", "map_pgm_sha256"):
            if not isinstance(config.get(key), str) or not _SHA256_RE.fullmatch(config[key]):
                raise ValueError(f"floor {floor} {key} must be a lowercase SHA-256")
        normalized_floors[str(floor)] = config
        if verify_files:
            _verify_map_files(floor, config)

    if not isinstance(transitions, list):
        raise ValueError("transitions must be a list")
    normalized_edges = []
    seen_ids = set()
    seen_pairs = set()
    for index, raw_edge in enumerate(transitions):
        if not isinstance(raw_edge, dict):
            raise ValueError(f"transition {index} must be an object")
        edge = dict(raw_edge)
        edge_id = edge.get("transition_id")
        if not isinstance(edge_id, str) or not edge_id.strip() or edge_id in seen_ids:
            raise ValueError(f"transition {index} has a missing or duplicate transition_id")
        seen_ids.add(edge_id)
        source = _floor_id(edge.get("from_floor"), f"transition {edge_id}.from_floor")
        target = _floor_id(edge.get("to_floor"), f"transition {edge_id}.to_floor")
        if source == target:
            raise ValueError(f"transition {edge_id} must change floor")
        if str(source) not in normalized_floors or str(target) not in normalized_floors:
            raise ValueError(f"transition {edge_id} references an unknown floor")
        pair = (source, target)
        if pair in seen_pairs:
            raise ValueError(f"duplicate directed transition {source}->{target}")
        seen_pairs.add(pair)
        edge["entry_pose"] = _pose(edge.get("entry_pose"), f"transition {edge_id}.entry_pose", frame_id)
        edge["exit_pose"] = _pose(edge.get("exit_pose"), f"transition {edge_id}.exit_pose", frame_id)
        normalized_edges.append(edge)

    normalized = dict(bundle)
    normalized["floors"] = normalized_floors
    normalized["transitions"] = normalized_edges
    return normalized


def find_floor_path(transitions, source_floor, target_floor):
    """Return a deterministic shortest directed edge path between floor IDs."""
    source = _floor_id(source_floor, "source_floor")
    target = _floor_id(target_floor, "target_floor")
    if source == target:
        return []
    adjacency = {}
    for edge in transitions:
        adjacency.setdefault(edge["from_floor"], []).append(edge)
    for edges in adjacency.values():
        edges.sort(key=lambda item: item["transition_id"])
    queue = deque([source])
    previous = {source: None}
    while queue:
        floor = queue.popleft()
        for edge in adjacency.get(floor, []):
            next_floor = edge["to_floor"]
            if next_floor in previous:
                continue
            previous[next_floor] = (floor, edge)
            if next_floor == target:
                route = []
                cursor = target
                while cursor != source:
                    parent, parent_edge = previous[cursor]
                    route.append(parent_edge)
                    cursor = parent
                return list(reversed(route))
            queue.append(next_floor)
    raise ValueError(f"no floor route from {source} to {target}")


def compile_mission(mission, bundle, verify_files=True):
    """Compile ordered task stops into same-floor goals and floor switches."""
    normalized_bundle = validate_transition_bundle(bundle, verify_files=verify_files)
    if not isinstance(mission, dict) or mission.get("schema_version") != MISSION_SCHEMA:
        raise ValueError(f"schema_version must be {MISSION_SCHEMA}")
    task_id = mission.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-empty string")
    current_floor = _floor_id(mission.get("initial_floor"), "initial_floor")
    floors = normalized_bundle["floors"]
    if str(current_floor) not in floors:
        raise ValueError(f"unknown initial floor {current_floor}")
    initial_pose = _pose(mission.get("initial_pose"), "initial_pose", normalized_bundle["frame_id"])
    stops = mission.get("stops")
    if not isinstance(stops, list) or not stops:
        raise ValueError("stops must be a non-empty list")

    normalized_stops = []
    for index, raw_stop in enumerate(stops):
        if not isinstance(raw_stop, dict):
            raise ValueError(f"stop {index} must be an object")
        stop_index = raw_stop.get("stop_index")
        if isinstance(stop_index, bool) or stop_index != index:
            raise ValueError("stop_index values must be contiguous and match list order")
        floor = _floor_id(raw_stop.get("floor_id"), f"stop {index}.floor_id")
        if str(floor) not in floors:
            raise ValueError(f"stop {index} references an unknown floor")
        assignment_source = raw_stop.get("floor_assignment_source")
        if not isinstance(assignment_source, str) or not assignment_source.strip():
            raise ValueError(f"stop {index} floor_assignment_source is required")
        candidates = raw_stop.get("candidate_targets")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f"stop {index} candidate_targets must be a non-empty list")
        normalized_candidates = []
        seen_candidate_ids = set()
        for candidate_index, raw_candidate in enumerate(candidates):
            name = f"stop {index} candidate {candidate_index}"
            if not isinstance(raw_candidate, dict):
                raise ValueError(f"{name} must be an object")
            target_id = raw_candidate.get("target_id")
            if not isinstance(target_id, str) or not target_id.strip():
                raise ValueError(f"{name} target_id must be a non-empty string")
            if target_id in seen_candidate_ids:
                raise ValueError(f"stop {index} has duplicate candidate target_id")
            seen_candidate_ids.add(target_id)
            candidate_floor = _floor_id(raw_candidate.get("floor_id"), f"{name}.floor_id")
            if candidate_floor != floor:
                raise ValueError(f"{name} candidate floor_id does not match stop floor_id")
            candidate_source = raw_candidate.get("floor_assignment_source")
            if candidate_source != assignment_source:
                raise ValueError(f"{name} floor assignment provenance mismatch")
            nav_target = _navigation_target(raw_candidate.get("navigation_target"),
                                            f"{name}.navigation_target",
                                            normalized_bundle["frame_id"])
            if (nav_target.get("floor_id") != floor
                    or nav_target.get("floor_assignment_source") != assignment_source):
                raise ValueError(f"{name} navigation target floor assignment mismatch")
            normalized_candidates.append({
                "target_id": target_id,
                "floor_id": floor,
                "floor_assignment_source": assignment_source,
                "navigation_target": nav_target,
            })
        normalized_stops.append({"stop_index": index, "floor_id": floor,
                                 "floor_assignment_source": assignment_source,
                                 "candidate_targets": normalized_candidates})

    actions = []
    for stop in normalized_stops:
        for edge in find_floor_path(normalized_bundle["transitions"], current_floor,
                                    stop["floor_id"]):
            target_floor = edge["to_floor"]
            actions.extend([
                {"kind": "navigate_to_entry", "transition_id": edge["transition_id"],
                 "floor_id": current_floor, "pose": edge["entry_pose"]},
                {"kind": "load_map", "transition_id": edge["transition_id"],
                 "floor_id": target_floor,
                 "map_yaml_path": floors[str(target_floor)]["map_yaml_path"]},
                {"kind": "set_pose", "transition_id": edge["transition_id"],
                 "floor_id": target_floor, "pose": edge["exit_pose"]},
            ])
            current_floor = target_floor
        actions.append({"kind": "navigate_to_stop", "stop_index": stop["stop_index"],
                        "floor_id": current_floor,
                        "candidate_targets": stop["candidate_targets"]})

    return {
        "task_id": task_id,
        "map_bundle_id": normalized_bundle["map_bundle_id"],
        "initial_state": {"floor_id": _floor_id(mission["initial_floor"], "initial_floor"),
                          "map_yaml_path": floors[str(mission["initial_floor"])]["map_yaml_path"],
                          "pose": initial_pose},
        "actions": actions,
    }
