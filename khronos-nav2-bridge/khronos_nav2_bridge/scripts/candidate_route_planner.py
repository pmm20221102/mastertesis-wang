#!/usr/bin/env python3
"""Select the lowest-cost fully feasible ordered candidate sequence."""

from itertools import product
import math

CANDIDATE_MISSION_SCHEMA = "hospital-multifloor-candidate-mission-v1"
MISSION_SCHEMA = "hospital-multifloor-mission-v1"


def _validate_candidate_stops(candidate_stops):
    if not isinstance(candidate_stops, list) or not candidate_stops:
        raise ValueError("candidate_stops must be a non-empty list")
    for stop_index, candidates in enumerate(candidate_stops):
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f"stop {stop_index} must have at least one candidate")
        seen = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError(f"stop {stop_index} candidate must be an object")
            target_id = candidate.get("target_id")
            floor_id = candidate.get("floor_id")
            if not isinstance(target_id, str) or not target_id.strip():
                raise ValueError(f"stop {stop_index} candidate target_id is required")
            if target_id in seen:
                raise ValueError(f"stop {stop_index} has duplicate candidate {target_id}")
            seen.add(target_id)
            if isinstance(floor_id, bool) or not isinstance(floor_id, int) or floor_id < 0:
                raise ValueError(f"stop {stop_index} candidate {target_id} has invalid floor_id")
            if not isinstance(candidate.get("navigation_target"), dict):
                raise ValueError(f"stop {stop_index} candidate {target_id} has no navigation_target")


def _validated_cost(evaluation):
    if not isinstance(evaluation, dict) or evaluation.get("status") != "feasible":
        raise ValueError("route evaluation must have status='feasible'")
    raw_length = evaluation.get("total_path_length_m")
    if isinstance(raw_length, bool):
        raise ValueError("total_path_length_m must be finite and non-negative")
    try:
        length = float(raw_length)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError("total_path_length_m must be finite and non-negative") from error
    if not math.isfinite(length) or length < 0:
        raise ValueError("total_path_length_m must be finite and non-negative")
    raw_transitions = evaluation.get("transition_count")
    if (isinstance(raw_transitions, bool) or not isinstance(raw_transitions, int)
            or raw_transitions < 0):
        raise ValueError("transition_count must be a non-negative integer")
    return length, raw_transitions


def select_candidate_chain(candidate_stops, evaluate_sequence):
    """Exhaustively evaluate candidate sequences and return the best feasible one.

    ``evaluate_sequence`` is called exactly once for every ordered combination.
    It must return a feasible route with total path length and transition count,
    or an infeasible result with a human-readable reason. No candidate is
    selected by input order when its route is infeasible.
    """
    _validate_candidate_stops(candidate_stops)
    if not callable(evaluate_sequence):
        raise ValueError("evaluate_sequence must be callable")

    feasible = []
    rejected = []
    evaluated = 0
    for sequence in product(*candidate_stops):
        evaluated += 1
        target_ids = [candidate["target_id"] for candidate in sequence]
        try:
            evaluation = evaluate_sequence(sequence)
            if (isinstance(evaluation, dict)
                    and evaluation.get("status") == "preflight_state_restore_failed"):
                return {"status": "preflight_state_restore_failed",
                        "reason": str(evaluation.get("reason", "unknown")),
                        "evaluated_sequence_count": evaluated,
                        "rejected_sequences": rejected + [{
                            "target_ids": target_ids,
                            "reason": str(evaluation.get("reason", "unknown")),
                            "route_actions": evaluation.get("route_actions", []),
                            "segment_results": evaluation.get("segment_results", []),
                        }]}
            if not isinstance(evaluation, dict) or evaluation.get("status") != "feasible":
                reason = (evaluation.get("reason", "route is infeasible")
                          if isinstance(evaluation, dict) else "invalid route evaluation")
                rejected.append({"target_ids": target_ids, "reason": str(reason),
                                 "route_actions": (evaluation.get("route_actions", [])
                                                   if isinstance(evaluation, dict) else []),
                                 "segment_results": (evaluation.get("segment_results", [])
                                                     if isinstance(evaluation, dict) else [])})
                continue
            length, transitions = _validated_cost(evaluation)
        except (TypeError, ValueError, RuntimeError) as error:
            rejected.append({"target_ids": target_ids,
                             "reason": f"invalid_route_evaluation: {error}",
                             "route_actions": [], "segment_results": []})
            continue
        feasible.append((length, transitions, tuple(target_ids), sequence, evaluation))

    if not feasible:
        return {"status": "no_feasible_candidate_route",
                "evaluated_sequence_count": evaluated,
                "rejected_sequences": rejected}

    length, transitions, target_ids, sequence, evaluation = min(
        feasible, key=lambda item: (item[0], item[1], item[2]))
    return {
        "status": "selected",
        "selected_candidates": [dict(candidate) for candidate in sequence],
        "selected_target_ids": list(target_ids),
        "total_path_length_m": length,
        "transition_count": transitions,
        "route_actions": list(evaluation.get("route_actions", [])),
        "segment_results": list(evaluation.get("segment_results", [])),
        "evaluated_sequence_count": evaluated,
        "feasible_sequence_count": len(feasible),
        "rejected_sequences": rejected,
    }


def evaluate_candidate_sequence(sequence, initial_state, bundle, adapter):
    """Preflight one ordered candidate sequence without issuing motion goals."""
    from floor_transition_core import find_floor_path

    if not isinstance(sequence, (list, tuple)) or not sequence:
        return {"status": "no_path", "reason": "candidate sequence is empty",
                "route_actions": [], "segment_results": []}
    if not isinstance(initial_state, dict):
        raise ValueError("initial_state must be an object")
    initial_floor = initial_state.get("floor_id")
    initial_pose = initial_state.get("pose")
    if (isinstance(initial_floor, bool) or not isinstance(initial_floor, int)
            or initial_floor < 0 or not isinstance(initial_pose, dict)):
        raise ValueError("initial_state requires a non-negative floor and pose")
    if not isinstance(bundle, dict) or not isinstance(bundle.get("transitions"), list):
        raise ValueError("bundle requires a transitions list")
    floors = bundle.get("floors")
    if not isinstance(floors, dict):
        raise ValueError("bundle requires floor configurations")

    active_floor = initial_floor
    current_pose = dict(initial_pose)
    total_path_length = 0.0
    transition_count = 0
    route_actions = []
    segment_results = []
    failure = None

    def validated_path_length(result, label):
        if not isinstance(result, dict) or result.get("status") != "path_found":
            reason = (result.get("reason", result.get("status", "no path"))
                      if isinstance(result, dict) else "invalid path result")
            raise ValueError(f"{label}: {reason}")
        raw = result.get("path_length_m")
        if isinstance(raw, bool):
            raise ValueError(f"{label}: invalid path length")
        try:
            length = float(raw)
        except (OverflowError, TypeError, ValueError) as error:
            raise ValueError(f"{label}: invalid path length") from error
        if not math.isfinite(length) or length < 0:
            raise ValueError(f"{label}: invalid path length")
        return length

    try:
        for stop_index, candidate in enumerate(sequence):
            if not isinstance(candidate, dict):
                raise ValueError(f"stop {stop_index} candidate must be an object")
            target_id = candidate.get("target_id")
            target_floor = candidate.get("floor_id")
            if not isinstance(target_id, str) or not target_id.strip():
                raise ValueError(f"stop {stop_index} candidate target_id is required")
            if (isinstance(target_floor, bool) or not isinstance(target_floor, int)
                    or target_floor < 0 or str(target_floor) not in floors):
                raise ValueError(f"stop {stop_index} candidate has invalid floor_id")

            floor_path = find_floor_path(bundle["transitions"], active_floor, target_floor)
            for edge in floor_path:
                if edge["from_floor"] != active_floor:
                    raise ValueError("floor route is not contiguous with current state")
                leg_result = adapter.plan_to_pose(active_floor, current_pose,
                                                  edge["entry_pose"])
                length = validated_path_length(leg_result,
                                               f"portal {edge['transition_id']} entry")
                total_path_length += length
                segment_results.append({"kind": "portal_entry",
                                        "transition_id": edge["transition_id"],
                                        "floor_id": active_floor,
                                        "path_length_m": length,
                                        "path": leg_result.get("path", [])})
                route_actions.append({"kind": "navigate_to_entry",
                                      "transition_id": edge["transition_id"],
                                      "floor_id": active_floor,
                                      "pose": edge["entry_pose"]})

                map_path = floors[str(edge["to_floor"])]["map_yaml_path"]
                switched = adapter.switch_floor(edge, map_path)
                if not isinstance(switched, dict) or switched.get("status") != "succeeded":
                    reason = (switched.get("reason", switched.get("status", "map/pose switch failed"))
                              if isinstance(switched, dict) else "invalid map/pose switch result")
                    raise ValueError(f"portal {edge['transition_id']} switch: {reason}")
                route_actions.extend([
                    {"kind": "load_map", "transition_id": edge["transition_id"],
                     "floor_id": edge["to_floor"], "map_yaml_path": map_path},
                    {"kind": "set_pose", "transition_id": edge["transition_id"],
                     "floor_id": edge["to_floor"], "pose": edge["exit_pose"]},
                ])
                segment_results.append({"kind": "floor_transition",
                                        "transition_id": edge["transition_id"],
                                        "from_floor": edge["from_floor"],
                                        "to_floor": edge["to_floor"],
                                        "status": "succeeded"})
                active_floor = edge["to_floor"]
                current_pose = dict(edge["exit_pose"])
                transition_count += 1

            if active_floor != target_floor:
                raise ValueError(f"stop {stop_index} candidate floor was not reached")
            target_result = adapter.plan_candidate(active_floor, candidate)
            length = validated_path_length(target_result, f"candidate {target_id}")
            goal = target_result.get("goal")
            if not isinstance(goal, dict):
                raise ValueError(f"candidate {target_id}: projected goal is missing")
            total_path_length += length
            segment_results.append({"kind": "candidate_goal", "stop_index": stop_index,
                                    "target_id": target_id, "floor_id": active_floor,
                                    "path_length_m": length,
                                    "path": target_result.get("path", []),
                                    "projection": target_result.get("projection")})
            route_actions.append({"kind": "navigate_to_stop", "stop_index": stop_index,
                                  "target_id": target_id, "floor_id": active_floor,
                                  "goal": goal})
            virtual = adapter.set_virtual_pose(active_floor, goal)
            if not isinstance(virtual, dict) or virtual.get("status") != "succeeded":
                reason = (virtual.get("reason", virtual.get("status", "virtual arrival pose failed"))
                          if isinstance(virtual, dict) else "invalid virtual pose result")
                raise ValueError(f"candidate {target_id} virtual arrival: {reason}")
            current_pose = dict(goal)
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        failure = str(error)

    try:
        restored = adapter.restore_start(initial_state)
    except Exception as error:
        restored = {"status": "failed", "reason": f"{type(error).__name__}: {error}"}
    if not isinstance(restored, dict) or restored.get("status") != "succeeded":
        reason = (restored.get("reason", restored.get("status", "unknown"))
                  if isinstance(restored, dict) else "invalid restoration result")
        return {"status": "preflight_state_restore_failed", "reason": str(reason),
                "route_actions": route_actions, "segment_results": segment_results}
    if failure is not None:
        return {"status": "no_path", "reason": failure,
                "route_actions": route_actions, "segment_results": segment_results}
    return {"status": "feasible", "total_path_length_m": total_path_length,
            "transition_count": transition_count, "route_actions": route_actions,
            "segment_results": segment_results}


def build_resolved_mission(candidate_mission, selected_candidates):
    """Materialize a selected cross-floor sequence for the existing compiler."""
    if (not isinstance(candidate_mission, dict)
            or candidate_mission.get("schema_version") != CANDIDATE_MISSION_SCHEMA):
        raise ValueError(f"schema_version must be {CANDIDATE_MISSION_SCHEMA}")
    task_id = candidate_mission.get("task_id")
    map_bundle_id = candidate_mission.get("map_bundle_id")
    initial_floor = candidate_mission.get("initial_floor")
    initial_pose = candidate_mission.get("initial_pose")
    stops = candidate_mission.get("stops")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("candidate mission task_id is required")
    if not isinstance(map_bundle_id, str) or not map_bundle_id.strip():
        raise ValueError("candidate mission map_bundle_id is required")
    if (isinstance(initial_floor, bool) or not isinstance(initial_floor, int)
            or initial_floor < 0 or not isinstance(initial_pose, dict)):
        raise ValueError("candidate mission initial state is invalid")
    if not isinstance(stops, list) or not stops:
        raise ValueError("candidate mission requires ordered stops")
    if not isinstance(selected_candidates, (list, tuple)) or len(selected_candidates) != len(stops):
        raise ValueError("selected candidate count must match stop count")

    resolved_stops = []
    for index, (stop, selected) in enumerate(zip(stops, selected_candidates)):
        if not isinstance(stop, dict) or stop.get("stop_index") != index:
            raise ValueError("candidate mission stop indices must be contiguous")
        options = stop.get("candidate_targets")
        if not isinstance(options, list) or not options:
            raise ValueError(f"stop {index} must have candidate targets")
        if not isinstance(selected, dict):
            raise ValueError(f"selected candidate at stop {index} must be an object")
        selected_id = selected.get("target_id")
        option_ids = {item.get("target_id") for item in options if isinstance(item, dict)}
        if not isinstance(selected_id, str) or selected_id not in option_ids:
            raise ValueError(f"selected candidate at stop {index} is not in frozen candidate set")
        matching = [item for item in options if item.get("target_id") == selected_id]
        if len(matching) != 1 or matching[0] != selected:
            raise ValueError(f"selected candidate at stop {index} does not match frozen target data")
        floor_id = selected.get("floor_id")
        assignment_source = selected.get("floor_assignment_source")
        if (isinstance(floor_id, bool) or not isinstance(floor_id, int) or floor_id < 0
                or not isinstance(assignment_source, str) or not assignment_source.strip()):
            raise ValueError(f"selected candidate at stop {index} has invalid floor provenance")
        resolved_stops.append({
            "stop_index": index,
            "floor_id": floor_id,
            "floor_assignment_source": assignment_source,
            "candidate_targets": [dict(selected)],
        })

    return {"schema_version": MISSION_SCHEMA, "task_id": task_id,
            "map_bundle_id": map_bundle_id, "initial_floor": initial_floor,
            "initial_pose": dict(initial_pose), "stops": resolved_stops}


def resolve_candidate_mission(candidate_mission, bundle, adapter):
    """Select a full candidate chain using the supplied Nav2 route adapter."""
    if (not isinstance(candidate_mission, dict)
            or candidate_mission.get("schema_version") != CANDIDATE_MISSION_SCHEMA):
        raise ValueError(f"schema_version must be {CANDIDATE_MISSION_SCHEMA}")
    stops = candidate_mission.get("stops")
    if not isinstance(stops, list) or not stops:
        raise ValueError("candidate mission requires ordered stops")
    candidate_stops = []
    for index, stop in enumerate(stops):
        if not isinstance(stop, dict) or stop.get("stop_index") != index:
            raise ValueError("candidate mission stop indices must be contiguous")
        candidate_stops.append(stop.get("candidate_targets"))
    initial_state = {"floor_id": candidate_mission.get("initial_floor"),
                     "pose": candidate_mission.get("initial_pose")}
    selection = select_candidate_chain(
        candidate_stops,
        lambda sequence: evaluate_candidate_sequence(sequence, initial_state,
                                                     bundle, adapter))
    if selection["status"] != "selected":
        return {"status": selection["status"], "route_selection": selection}
    resolved = build_resolved_mission(candidate_mission,
                                      selection["selected_candidates"])
    return {"status": "selected", "resolved_mission": resolved,
            "route_selection": selection}
