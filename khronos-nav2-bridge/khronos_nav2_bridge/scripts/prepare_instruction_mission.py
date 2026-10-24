#!/usr/bin/env python3
"""Adapt a structured LLM/RAG PipelineResult to a Hospital Nav2 candidate mission."""

from __future__ import annotations

import math

from candidate_route_planner import CANDIDATE_MISSION_SCHEMA
from plan_candidate_task_chain import FLOOR_Z_TOLERANCE_M


SUPPORTED_NAV_ACTIONS = {"navigate_to", "escort_to", "guide_to"}


def _reference(grounded_task: dict, reference_name: str):
    if reference_name.startswith("step_"):
        parts = reference_name.split("_", 2)
        if len(parts) == 3 and parts[1].isdigit():
            steps = grounded_task.get("steps") or []
            index = int(parts[1])
            if index < len(steps) and isinstance(steps[index], dict):
                refs = steps[index].get("references") or {}
                return refs.get(parts[2]) if isinstance(refs, dict) else None
        return None
    refs = grounded_task.get("references") or {}
    return refs.get(reference_name) if isinstance(refs, dict) else None


def _navigation_candidates(reference: dict) -> dict[str, dict]:
    candidates: dict[str, dict] = {}
    node_id = reference.get("node_id")
    target = reference.get("navigation_target")
    if isinstance(node_id, str) and node_id and isinstance(target, dict):
        candidates[node_id] = target

    for field in ("candidate_navigation_targets", "selected_many"):
        items = reference.get(field) or []
        if isinstance(items, dict):
            items = [{"node_id": key, "navigation_target": value}
                     for key, value in items.items()]
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate_id = item.get("node_id")
            navigation_target = item.get("navigation_target")
            if (isinstance(candidate_id, str) and candidate_id
                    and isinstance(navigation_target, dict)):
                previous = candidates.get(candidate_id)
                if previous is not None and previous != navigation_target:
                    raise ValueError(f"conflicting navigation anchors for {candidate_id}")
                candidates[candidate_id] = navigation_target
    return candidates


def _room_place_floor(room_id: str, canonical_graph: dict | None,
                      floors: dict) -> tuple[int | None, dict]:
    nodes = canonical_graph.get("nodes") if isinstance(canonical_graph, dict) else None
    if not isinstance(nodes, dict):
        return None, {}
    counts = {int(floor_id): 0 for floor_id in floors}
    for node in nodes.values():
        if not isinstance(node, dict) or node.get("node_type") not in ("place", "places"):
            continue
        parents = node.get("parent_room_ids") or []
        if node.get("room_id") != room_id and room_id not in parents:
            continue
        position = node.get("position")
        if not isinstance(position, (list, tuple)) or len(position) != 3:
            continue
        try:
            z = float(position[2])
            floor_distances = [(abs(z - float(info["floor_z_m"])), int(floor_id))
                               for floor_id, info in floors.items()]
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(z) or not floor_distances:
            continue
        floor_distances.sort()
        if (len(floor_distances) > 1
                and math.isclose(floor_distances[0][0], floor_distances[1][0],
                                 rel_tol=0.0, abs_tol=1e-6)):
            continue
        counts[floor_distances[0][1]] += 1
    total = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if not total or ordered[0][1] * 2 <= total:
        return None, {"room_id": room_id, "place_floor_counts": counts,
                      "member_place_count": total, "status": "no_strict_majority"}
    return ordered[0][0], {"room_id": room_id, "place_floor_counts": counts,
                           "member_place_count": total,
                           "modal_floor_share": ordered[0][1] / total,
                           "status": "strict_majority"}


def _floor_for_target(target_id: str, navigation_target: dict, floors: dict,
                      frame_id: str, tolerance_m: float,
                      semantic_node: dict | None = None,
                      canonical_graph: dict | None = None):
    if navigation_target.get("frame_id") != frame_id:
        return None, f"target {target_id} is not in the {frame_id!r} frame", None, None
    position = navigation_target.get("position")
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        return None, f"target {target_id} lacks a finite XYZ navigation position", None, None
    try:
        xyz = [float(value) for value in position]
    except (TypeError, ValueError, OverflowError):
        return None, f"target {target_id} has an invalid navigation position", None, None
    if any(not math.isfinite(value) for value in xyz):
        return None, f"target {target_id} has a non-finite navigation position", None, None

    matches = []
    for raw_floor, config in floors.items():
        try:
            floor_id = int(raw_floor)
            floor_z = float(config["floor_z_m"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None, f"floor {raw_floor!r} has invalid floor_z_m"
        if math.isfinite(floor_z) and abs(xyz[2] - floor_z) <= tolerance_m:
            matches.append(floor_id)
    if len(matches) != 1:
        nearest = sorted((abs(xyz[2] - float(info["floor_z_m"])), int(floor_id))
                         for floor_id, info in floors.items())
        node_type = ((semantic_node or {}).get("node_type") or "").lower().rstrip("s")
        room_id = ((semantic_node or {}).get("room_id")
                   or navigation_target.get("room_id"))
        if node_type == "room":
            room_id = target_id
        if room_id:
            modal_floor, evidence = _room_place_floor(room_id, canonical_graph, floors)
            if modal_floor is not None and nearest and nearest[0][1] == modal_floor:
                normalized = dict(navigation_target)
                if node_type == "room":
                    position = list(normalized["position"])
                    position[2] = float(floors[str(modal_floor)]["floor_z_m"])
                    normalized["position"] = position
                evidence.update({"method": "room_member_places_majority",
                                 "target_z_delta_m": abs(xyz[2] - float(
                                     floors[str(modal_floor)]["floor_z_m"]))})
                return modal_floor, None, evidence, normalized
            if modal_floor is None:
                reason = f"room {room_id} has no strict majority Place floor"
            else:
                reason = (f"target {target_id} nearest z-floor {nearest[0][1]} conflicts "
                          f"with room {room_id} Place-majority floor {modal_floor}")
            return None, reason, evidence, None
        return None, (f"target {target_id} z={xyz[2]:.4f} m matches {len(matches)} "
                      f"floor planes within {tolerance_m:.3f} m"), None, None
    floor_id = matches[0]
    return floor_id, None, {"method": "navigation_target_z_within_tolerance",
                            "target_z_delta_m": abs(xyz[2] -
                                float(floors[str(floor_id)]["floor_z_m"]))}, navigation_target


def build_candidate_mission(pipeline_result: dict, map_bundle: dict,
                            initial_state: dict, task_id: str, *,
                            floor_tolerance_m: float = FLOOR_Z_TOLERANCE_M,
                            canonical_graph: dict | None = None) -> dict:
    """Convert an LLM/RAG result without choosing candidates or reading benchmark gold."""
    if not isinstance(pipeline_result, dict) or pipeline_result.get("status") != "valid":
        return {"status": "pipeline_not_valid", "mission": None,
                "reason": "structured pipeline result is not valid"}
    task_plan = pipeline_result.get("task_plan")
    grounded = pipeline_result.get("grounded_task")
    if (not isinstance(task_plan, dict) or task_plan.get("status") != "valid"
            or not isinstance(grounded, dict)):
        return {"status": "pipeline_contract_invalid", "mission": None,
                "reason": "valid task_plan and grounded_task are required"}
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-empty string")
    if not isinstance(map_bundle, dict) or not isinstance(map_bundle.get("floors"), dict):
        raise ValueError("map bundle must contain a floors mapping")
    floors = {}
    for raw_floor, config in map_bundle["floors"].items():
        if isinstance(raw_floor, bool):
            raise ValueError("map bundle floor IDs must be non-negative integers")
        try:
            floor_id = int(raw_floor)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("map bundle floor IDs must be non-negative integers") from error
        if floor_id < 0 or str(floor_id) in floors:
            raise ValueError("map bundle floor IDs must be unique non-negative integers")
        floors[str(floor_id)] = config
    bundle_id = map_bundle.get("map_bundle_id")
    frame_id = map_bundle.get("frame_id", "map")
    if not isinstance(bundle_id, str) or not bundle_id.strip():
        raise ValueError("map bundle must have a non-empty map_bundle_id")
    if not isinstance(frame_id, str) or not frame_id.strip():
        raise ValueError("map bundle frame_id must be a non-empty string")
    if (isinstance(floor_tolerance_m, bool) or not isinstance(floor_tolerance_m, (int, float))
            or not math.isfinite(float(floor_tolerance_m)) or floor_tolerance_m < 0):
        raise ValueError("floor_tolerance_m must be finite and non-negative")
    if not isinstance(initial_state, dict):
        raise ValueError("initial_state must be an object")
    initial_floor = initial_state.get("floor_id")
    initial_pose = initial_state.get("pose")
    if (isinstance(initial_floor, bool) or not isinstance(initial_floor, int)
            or str(initial_floor) not in floors
            or not isinstance(initial_pose, dict)
            or initial_pose.get("frame_id", frame_id) != frame_id):
        raise ValueError("initial_state must name a configured floor and map-frame pose")

    chain = task_plan.get("task_chain")
    if not isinstance(chain, list):
        raise ValueError("task_plan.task_chain must be a list")
    stops = []
    non_navigation_steps = []
    unresolved_targets = []
    for chain_index, step in enumerate(chain):
        if not isinstance(step, dict):
            raise ValueError(f"task-chain step {chain_index} must be an object")
        action = step.get("action")
        executor = step.get("executor")
        if executor != "nav2":
            non_navigation_steps.append({"step": step.get("step", chain_index + 1),
                                         "action": action, "executor": executor})
            continue
        if action not in SUPPORTED_NAV_ACTIONS:
            return {"status": "unsupported_navigation_action", "mission": None,
                    "reason": f"Nav2 path planning does not support action {action!r}",
                    "source_task_step": step.get("step", chain_index + 1)}

        constraints = step.get("constraints") or {}
        unapplied = [name for name in ("avoid", "prefer", "conditions")
                     if constraints.get(name)] if isinstance(constraints, dict) else ["constraints"]
        if unapplied:
            return {"status": "unsupported_navigation_constraint", "mission": None,
                    "reason": f"Nav2 map planner does not apply these task constraints: {unapplied}",
                    "source_task_step": step.get("step", chain_index + 1)}

        role_refs = step.get("role_refs") or {}
        if action in ("escort_to", "guide_to"):
            reference_name = role_refs.get("destination")
            role = "destination"
        else:
            reference_name = step.get("target_ref")
            role = "target"
        if not isinstance(reference_name, str) or not reference_name:
            return {"status": "grounding_reference_missing", "mission": None,
                    "reason": "navigation step has no grounded destination reference",
                    "source_task_step": step.get("step", chain_index + 1)}
        reference = _reference(grounded, reference_name)
        if not isinstance(reference, dict):
            return {"status": "grounded_target_missing", "mission": None,
                    "reason": f"grounded reference {reference_name!r} is missing",
                    "source_task_step": step.get("step", chain_index + 1)}

        role_candidates = (step.get("role_candidate_node_ids") or {}).get(role, [])
        role_id = (step.get("role_ids") or {}).get(role)
        candidate_ids = (role_candidates or step.get("candidate_node_ids") or [])
        if role_id:
            candidate_ids = [role_id]
        elif not candidate_ids and step.get("target_id"):
            candidate_ids = [step["target_id"]]
        elif not candidate_ids:
            candidate_ids = (reference.get("candidate_node_ids")
                             or reference.get("node_ids") or [])
            if not candidate_ids and reference.get("node_id"):
                candidate_ids = [reference["node_id"]]
            if not candidate_ids:
                candidate_ids = [item.get("node_id") for item in
                                 reference.get("selected_many", [])
                                 if isinstance(item, dict) and item.get("node_id")]
        if not isinstance(candidate_ids, list) or not candidate_ids:
            return {"status": "grounded_target_missing", "mission": None,
                    "reason": "navigation step has no concrete grounded target IDs",
                    "source_task_step": step.get("step", chain_index + 1)}
        if step.get("target_id") and step.get("candidate_node_ids"):
            return {"status": "pipeline_contract_invalid", "mission": None,
                    "reason": "step cannot contain both target_id and candidate_node_ids",
                    "source_task_step": step.get("step", chain_index + 1)}
        if len(set(candidate_ids)) != len(candidate_ids):
            return {"status": "pipeline_contract_invalid", "mission": None,
                    "reason": "navigation candidate IDs contain duplicates",
                    "source_task_step": step.get("step", chain_index + 1)}

        navigation_by_id = _navigation_candidates(reference)
        stop_candidates = []
        for node_id in candidate_ids:
            if not isinstance(node_id, str) or not node_id:
                return {"status": "pipeline_contract_invalid", "mission": None,
                        "reason": "navigation candidate ID must be a non-empty string",
                        "source_task_step": step.get("step", chain_index + 1)}
            navigation_target = navigation_by_id.get(node_id)
            if not isinstance(navigation_target, dict):
                return {"status": "navigation_target_missing", "mission": None,
                        "reason": f"RAG did not provide a navigation anchor for {node_id}",
                        "source_task_step": step.get("step", chain_index + 1)}
            semantic_node = ((canonical_graph or {}).get("nodes") or {}).get(node_id)
            floor_id, floor_error, floor_evidence, normalized_target = _floor_for_target(
                node_id, navigation_target, floors, frame_id,
                float(floor_tolerance_m), semantic_node, canonical_graph,
            )
            if floor_error:
                unresolved_targets.append({"target_id": node_id, "reason": floor_error,
                                           "floor_assignment_evidence": floor_evidence,
                                           "source_task_step": step.get("step", chain_index + 1)})
                continue
            stop_candidates.append({
                "target_id": node_id,
                "floor_id": floor_id,
                "floor_assignment_source": (f"{bundle_id}:{floor_evidence['method']}"),
                "floor_assignment_evidence": floor_evidence,
                "navigation_target": {
                    **normalized_target,
                    "frame_id": frame_id,
                    "floor_id": floor_id,
                    "floor_assignment_source": f"{bundle_id}:{floor_evidence['method']}",
                },
            })
        if len(stop_candidates) != len(candidate_ids):
            continue
        stops.append({"stop_index": len(stops),
                      "source_task_step": step.get("step", chain_index + 1),
                      "action": action,
                      "candidate_targets": stop_candidates})

    if unresolved_targets:
        return {"status": "floor_unresolved", "mission": None,
                "unresolved_targets": unresolved_targets}
    if not stops:
        return {"status": "no_navigation_stops", "mission": None,
                "non_navigation_steps": non_navigation_steps}
    return {
        "status": "ready",
        "non_navigation_steps": non_navigation_steps,
        "mission": {
            "schema_version": CANDIDATE_MISSION_SCHEMA,
            "task_id": task_id,
            "map_bundle_id": bundle_id,
            "initial_floor": initial_floor,
            "initial_pose": dict(initial_pose),
            "stops": stops,
        },
    }
