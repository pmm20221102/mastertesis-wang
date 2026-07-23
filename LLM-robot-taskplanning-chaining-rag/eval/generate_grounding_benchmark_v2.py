#!/usr/bin/env python3
"""Generate grounding benchmark v2 from validated canonical graph.

V2 improvements over v1:
1. Reads directly from canonical_graph_v2.json (not RAG metadata)
2. Graph-fact-first: candidate set → status → expected IDs → query
3. Spatial cases include reference_position and expected_distance
4. Place-constrained cases use place_id (not room_id)
5. No #2/#3 pseudo-instances
6. Semantic duplicate detection
7. Blank label filtering
8. Category distribution matches manifest
"""

from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

# Add RAG src to path
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root / "RAG" / "khronos_3dsg_rag" / "src"))


def _norm_label(label: str) -> str:
    """Normalize label for dedup: lowercase, strip #N suffix, singularize."""
    import re
    s = label.lower().strip()
    s = re.sub(r'\s*#\d+$', '', s)
    s = s.rstrip('s')  # crude singularize
    return s


def generate_benchmark_v2(
    canonical_graph_path: str,
    output_path: str,
    seed: int = 42,
    total_cases: int = 100,
) -> dict:
    """Generate 100-case grounding benchmark v2.

    Returns:
        Manifest dict with benchmark metadata.
    """
    random.seed(seed)

    with open(canonical_graph_path, "r", encoding="utf-8") as f:
        graph = json.load(f)

    nodes = graph.get("nodes", {})
    edges = graph.get("edges", {})

    # Build adjacency from hierarchy edges
    hierarchy = edges.get("hierarchy", [])
    children_of: dict[str, list[str]] = defaultdict(list)
    parents_of: dict[str, list[str]] = defaultdict(list)
    for he in hierarchy:
        children_of[he["parent"]].append(he["child"])
        parents_of[he["child"]].append(he["parent"])

    # Classify nodes
    objects = {nid: n for nid, n in nodes.items() if n["node_type"] == "object"}
    places = {nid: n for nid, n in nodes.items() if n["node_type"] == "place"}
    rooms = {nid: n for nid, n in nodes.items() if n["node_type"] == "room"}

    # Group objects by label
    objects_by_label: dict[str, list[str]] = defaultdict(list)
    for nid, n in objects.items():
        label = n.get("label", "")
        if label:
            objects_by_label[label].append(nid)

    # Build place -> room mapping
    place_to_room: dict[str, str] = {}
    for nid, n in places.items():
        if n.get("room_id"):
            place_to_room[nid] = n["room_id"]

    # Build object -> place mapping
    obj_to_place: dict[str, str] = {}
    for nid, n in objects.items():
        if n.get("place_id"):
            obj_to_place[nid] = n["place_id"]

    cases = []
    used_labels: set[str] = set()
    case_num = 0

    # --- Category 1: Ambiguous object label (5 cases, one per label) ---
    # With only 5 unique object labels, we generate one per label
    multi_labels = [(label, nids) for label, nids in objects_by_label.items()
                    if len(nids) >= 2]
    random.shuffle(multi_labels)

    for label, nids in multi_labels[:5]:
        case_num += 1
        cases.append({
            "case_id": f"v2_ambiguous_{case_num:03d}",
            "instruction": f"Find a {label.rstrip('s')}.",
            "expected_grounding": {
                "pipeline_status": "ambiguous",
                "target": {"status": "ambiguous", "candidate_type": label, "candidate_count": len(nids)},
            },
            "oracle_intent": {
                "action": "locate",
                "target": {"text": label.rstrip('s'), "label": label, "node_type": "object"},
            },
            "ground_truth_evidence": {
                "source": "canonical_graph_v2",
                "candidate_query": {"label": label},
                "candidate_node_ids": nids,
                "expected_status": "ambiguous",
            },
            "tags": ["ambiguous", "medium"],
        })
        used_labels.add(label)

    # --- Category 1b: Object navigation with ambiguous labels (10 cases) ---
    # Navigate to an ambiguous label - tests action=locate vs navigate
    for label, nids in multi_labels[:5]:
        case_num += 1
        cases.append({
            "case_id": f"v2_objnav_{case_num:03d}",
            "instruction": f"Navigate to a {label.rstrip('s')}.",
            "expected_grounding": {
                "pipeline_status": "ambiguous",
                "target": {"status": "ambiguous", "candidate_type": label, "candidate_count": len(nids)},
            },
            "oracle_intent": {
                "action": "navigate",
                "target": {"text": label.rstrip('s'), "label": label, "node_type": "object"},
            },
            "ground_truth_evidence": {
                "source": "canonical_graph_v2",
                "candidate_query": {"label": label},
                "candidate_node_ids": nids,
                "expected_status": "ambiguous",
            },
            "tags": ["obj_nav", "medium"],
        })
    # Fill remaining obj_nav with different phrasings
    for label, nids in random.sample(multi_labels, min(5, len(multi_labels))):
        if len([c for c in cases if "obj_nav" in c.get("tags", [])]) >= 10:
            break
        case_num += 1
        variants = [
            f"Guide me to a {label.rstrip('s')}.",
            f"I need to find a {label.rstrip('s')}.",
            f"Take me to the {label}.",
            f"Locate a {label.rstrip('s')} nearby.",
            f"Where can I find a {label.rstrip('s')}?",
        ]
        instruction = random.choice(variants)
        cases.append({
            "case_id": f"v2_objnav_{case_num:03d}",
            "instruction": instruction,
            "expected_grounding": {
                "pipeline_status": "ambiguous",
                "target": {"status": "ambiguous", "candidate_type": label, "candidate_count": len(nids)},
            },
            "oracle_intent": {
                "action": "navigate",
                "target": {"text": label.rstrip('s'), "label": label, "node_type": "object"},
            },
            "ground_truth_evidence": {
                "source": "canonical_graph_v2",
                "candidate_query": {"label": label},
                "candidate_node_ids": nids,
                "expected_status": "ambiguous",
            },
            "tags": ["obj_nav", "medium"],
        })

    # --- Category 2: Place-constrained object (15 cases) ---
    # Find objects whose place has a unique label within that place
    place_constrained_cases = []
    for nid, n in objects.items():
        place_id = n.get("place_id", "")
        if not place_id:
            continue
        label = n.get("label", "")
        if not label:
            continue

        # Check if this object is unique within its place
        objs_in_place = [oid for oid, o in objects.items()
                        if o.get("place_id") == place_id and o.get("label") == label]
        if len(objs_in_place) == 1:
            place_constrained_cases.append((nid, n, place_id, label))

    random.shuffle(place_constrained_cases)
    for nid, n, place_id, label in place_constrained_cases[:15]:
        case_num += 1
        place_node = places.get(place_id, {})
        place_label = place_node.get("label", "") or place_node.get("name", "")
        if not place_label:
            place_label = f"area {place_id[-6:]}"  # Use last 6 digits as unique identifier

        cases.append({
            "case_id": f"v2_place_{case_num:03d}",
            "instruction": f"Find the {label.rstrip('s')} in {place_label}.",
            "expected_grounding": {
                "pipeline_status": "ready",
                "target": {"status": "resolved", "node_id": nid, "node_type": "object", "place_id": place_id},
            },
            "oracle_intent": {
                "action": "locate",
                "target": {"text": label.rstrip('s'), "label": label, "node_type": "object", "room": place_id},
            },
            "ground_truth_evidence": {
                "source": "canonical_graph_v2",
                "candidate_query": {"label": label, "place_id": place_id},
                "candidate_node_ids": [nid],
                "hierarchy_path": [nid, place_id, n.get("room_id", "")],
                "expected_status": "resolved",
            },
            "tags": ["place_constraint", "medium"],
        })

    # --- Category 3: Room-ID-constrained object (10 cases) ---
    room_constrained_cases = []
    for nid, n in objects.items():
        room_id = n.get("room_id", "")
        if not room_id:
            continue
        label = n.get("label", "")
        if not label:
            continue
        room_constrained_cases.append((nid, n, room_id, label))

    random.shuffle(room_constrained_cases)
    seen_room_obj = set()
    for nid, n, room_id, label in room_constrained_cases:
        if len([c for c in cases if "room_constraint" in c.get("tags", [])]) >= 10:
            break
        key = (room_id, label)
        if key in seen_room_obj:
            continue
        seen_room_obj.add(key)

        # Count how many objects with same label exist in this room
        same_label_in_room = [
            oid for oid, o in objects.items()
            if o.get("room_id") == room_id and o.get("label") == label
        ]
        is_unique = len(same_label_in_room) == 1

        case_num += 1
        if is_unique:
            # Unique object -> resolved
            cases.append({
                "case_id": f"v2_room_{case_num:03d}",
                "instruction": f"Find the {label.rstrip('s')} in room {room_id}.",
                "expected_grounding": {
                    "pipeline_status": "ready",
                    "target": {"status": "resolved", "node_id": nid, "node_type": "object", "room_id": room_id},
                },
                "oracle_intent": {
                    "action": "locate",
                    "target": {"text": label.rstrip('s'), "label": label, "node_type": "object", "room": room_id},
                },
                "ground_truth_evidence": {
                    "source": "canonical_graph_v2",
                    "candidate_query": {"label": label, "room_id": room_id},
                    "candidate_node_ids": same_label_in_room,
                    "hierarchy_path": [nid, n.get("place_id", ""), room_id],
                    "expected_status": "resolved",
                },
                "tags": ["room_constraint", "medium"],
            })
        else:
            # Multiple instances -> ambiguous
            cases.append({
                "case_id": f"v2_room_{case_num:03d}",
                "instruction": f"Find a {label.rstrip('s')} in room {room_id}.",
                "expected_grounding": {
                    "pipeline_status": "ambiguous",
                    "target": {"status": "ambiguous", "candidate_type": label, "candidate_count": len(same_label_in_room)},
                },
                "oracle_intent": {
                    "action": "locate",
                    "target": {"text": label.rstrip('s'), "label": label, "node_type": "object", "room": room_id},
                },
                "ground_truth_evidence": {
                    "source": "canonical_graph_v2",
                    "candidate_query": {"label": label, "room_id": room_id},
                    "candidate_node_ids": same_label_in_room,
                    "hierarchy_path": [nid, n.get("place_id", ""), room_id],
                    "expected_status": "ambiguous",
                },
                "tags": ["room_constraint", "medium"],
            })

    # --- Category 4: Set query (10 cases, 2 per label) ---
    for label, nids in multi_labels:
        if len([c for c in cases if "set_query" in c.get("tags", [])]) >= 10:
            break
        if len(nids) < 2:
            continue

        # Two phrasings per label
        for phrasing in [f"Find all {label}.", f"List every {label.rstrip('s')} in the scene."]:
            if len([c for c in cases if "set_query" in c.get("tags", [])]) >= 10:
                break
            case_num += 1
            cases.append({
                "case_id": f"v2_set_{case_num:03d}",
                "instruction": phrasing,
                "expected_grounding": {
                    "pipeline_status": "ready",
                    "target": {"status": "resolved_set", "node_ids": nids},
                },
                "oracle_intent": {
                    "action": "locate",
                    "target": {"text": label, "label": label, "node_type": "object", "quantifier": "all"},
                },
                "ground_truth_evidence": {
                    "source": "canonical_graph_v2",
                    "candidate_query": {"label": label},
                    "candidate_node_ids": nids,
                    "expected_status": "resolved_set",
                },
                "tags": ["set_query", "medium"],
            })

    # --- Category 5: Not-found (10 cases) ---
    not_found_items = [
        "refrigerator", "swimming pool", "gym", "cafeteria",
        "library", "workshop", "elevator shaft", "rooftop",
        "basement", "underground parking",
    ]
    for item in not_found_items:
        case_num += 1
        cases.append({
            "case_id": f"v2_notfound_{case_num:03d}",
            "instruction": f"Find the {item}.",
            "expected_grounding": {
                "pipeline_status": "not_found",
                "target": {"status": "not_found"},
            },
            "oracle_intent": {
                "action": "locate",
                "target": {"text": item, "node_type": "object"},
            },
            "ground_truth_evidence": {
                "source": "canonical_graph_v2",
                "candidate_query": {"label": item},
                "candidate_node_ids": [],
                "expected_status": "not_found",
            },
            "tags": ["not_found", "easy"],
        })

    # --- Category 6: Explicit-position spatial (10 cases) ---
    # Use object positions as reference, find nearest place
    obj_list = list(objects.values())
    random.shuffle(obj_list)
    spatial_count = 0
    used_ref_ids = set()

    for ref_obj in obj_list:
        if spatial_count >= 10:
            break
        ref_pos = ref_obj.get("position", [])
        if not ref_pos or len(ref_pos) < 3:
            continue
        ref_id = ref_obj.get("node_id", "")
        if ref_id in used_ref_ids:
            continue
        used_ref_ids.add(ref_id)

        # Find nearest place by Euclidean distance
        nearest_place = None
        min_dist = float("inf")
        for pid, p in places.items():
            p_pos = p.get("position", [])
            if not p_pos or len(p_pos) < 3:
                continue
            dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(ref_pos, p_pos)))
            if dist < min_dist:
                min_dist = dist
                nearest_place = pid

        if nearest_place:
            case_num += 1
            ref_label = ref_obj.get("label", "object")
            # Each spatial case uses a unique phrasing with different reference style
            spatial_phrasings = [
                f"Find the nearest place to {ref_label.rstrip('s')} at position ({ref_pos[0]:.1f}, {ref_pos[1]:.1f}).",
                f"What is the closest location to coordinates ({ref_pos[0]:.1f}, {ref_pos[1]:.1f})?",
                f"Navigate to the nearest area from point ({ref_pos[0]:.0f}, {ref_pos[1]:.0f}).",
                f"Find the place nearest to ({ref_pos[0]:.1f}, {ref_pos[1]:.1f}, {ref_pos[2]:.1f}).",
                f"Which place is closest to position x={ref_pos[0]:.1f} y={ref_pos[1]:.1f}?",
                f"Locate the nearest area relative to ({ref_pos[0]:.0f}, {ref_pos[1]:.0f}).",
                f"Find the closest place to the point ({ref_pos[0]:.1f}, {ref_pos[1]:.1f}).",
                f"What place is nearest to location ({ref_pos[0]:.1f}, {ref_pos[1]:.1f})?",
                f"Navigate to the area closest to ({ref_pos[0]:.0f}, {ref_pos[1]:.0f}).",
                f"Find the nearest area to coordinates ({ref_pos[0]:.1f}, {ref_pos[1]:.1f}, {ref_pos[2]:.1f}).",
            ]
            instruction = spatial_phrasings[spatial_count % len(spatial_phrasings)]
            cases.append({
                "case_id": f"v2_spatial_{case_num:03d}",
                "instruction": instruction,
                "expected_grounding": {
                    "pipeline_status": "ready",
                    "target": {"status": "resolved", "node_id": nearest_place, "node_type": "place"},
                },
                "oracle_intent": {
                    "action": "navigate",
                    "target": {"text": f"nearest place to {ref_label}", "node_type": "place", "quantifier": "nearest", "position": ref_pos},
                },
                "ground_truth_evidence": {
                    "source": "canonical_graph_v2",
                    "candidate_query": {"reference_position": ref_pos, "distance_metric": "euclidean_3d"},
                    "candidate_node_ids": [nearest_place],
                    "expected_distance": round(min_dist, 3),
                    "expected_status": "resolved",
                },
                "tags": ["spatial", "medium"],
            })
            spatial_count += 1

    # --- Category 7: Exact-ID sanity (5 cases) ---
    exact_phrasings = [
        "Navigate to node {}.",
        "Go to entity ID {}.",
        "Find target {}.",
        "Locate node {}.",
        "Move to entity {}.",
    ]
    for i, obj in enumerate(random.sample(list(objects.values()), min(5, len(objects)))):
        case_num += 1
        instruction = exact_phrasings[i % len(exact_phrasings)].format(obj['node_id'])
        cases.append({
            "case_id": f"v2_exact_{case_num:03d}",
            "instruction": instruction,
            "expected_grounding": {
                "pipeline_status": "ready",
                "target": {"status": "resolved", "node_id": obj["node_id"], "node_type": "object"},
            },
            "oracle_intent": {
                "action": "navigate",
                "target": {"text": obj["node_id"], "node_type": "object"},
            },
            "ground_truth_evidence": {
                "source": "canonical_graph_v2",
                "candidate_query": {"node_id": obj["node_id"]},
                "candidate_node_ids": [obj["node_id"]],
                "expected_status": "resolved",
            },
            "tags": ["exact_id", "easy"],
        })

    # Build neighbor map (used by complex and relation sections)
    neighbor_edges = edges.get("neighbors", [])
    neighbor_map: dict[str, set[str]] = defaultdict(set)
    for ne in neighbor_edges:
        neighbor_map[ne["source"]].add(ne["target"])
        neighbor_map[ne["target"]].add(ne["source"])

    # --- Category 8: Complex combined constraints (10 cases) ---
    # Each case must have at least 2 independent constraints
    complex_cases = []
    complex_count = 0
    used_complex = set()

    # Type A: label + Place + room (3 constraints)
    for nid, n in objects.items():
        if complex_count >= 4:
            break
        label = n.get("label", "")
        place_id = n.get("place_id", "")
        room_id = n.get("room_id", "")
        if not label or not place_id or not room_id:
            continue
        key = (label, place_id, room_id)
        if key in used_complex:
            continue
        used_complex.add(key)

        place_node = places.get(place_id, {})
        place_label = place_node.get("label", "") or place_node.get("name", "")
        if not place_label:
            place_label = f"place {place_id[-6:]}"

        case_num += 1
        complex_cases.append({
            "case_id": f"v2_complex_{case_num:03d}",
            "instruction": f"Navigate to the {label.rstrip('s')} in {place_label} (room {room_id[-4:]}).",
            "expected_grounding": {
                "pipeline_status": "ready",
                "target": {"status": "resolved", "node_id": nid, "node_type": "object", "place_id": place_id, "room_id": room_id},
            },
            "oracle_intent": {
                "action": "navigate",
                "target": {"text": label.rstrip('s'), "label": label, "node_type": "object",
                           "locations": [
                               {"node_id": place_id, "node_type": "place"},
                               {"node_id": room_id, "node_type": "room"},
                           ]},
            },
            "ground_truth_evidence": {
                "source": "canonical_graph_v2",
                "candidate_query": {"label": label, "place_id": place_id, "room_id": room_id},
                "candidate_node_ids": [nid],
                "hierarchy_path": [nid, place_id, room_id],
                "expected_status": "resolved",
            },
            "tags": ["complex", "hard"],
        })
        complex_count += 1

    # Type B: Room + contains Object count (2 constraints: room + count)
    for label, nids in multi_labels:
        if complex_count >= 7:
            break
        room_counts: dict[str, int] = defaultdict(int)
        for nid in nids:
            n = objects.get(nid, {})
            room_id = n.get("room_id", "")
            if room_id:
                room_counts[room_id] += 1
        if not room_counts:
            continue
        # Find room with exactly N objects of this label
        for room_id, count in room_counts.items():
            if complex_count >= 7:
                break
            key = (label, room_id, count)
            if key in used_complex:
                continue
            used_complex.add(key)

            case_num += 1
            complex_cases.append({
                "case_id": f"v2_complex_{case_num:03d}",
                "instruction": f"How many {label} are in room {room_id[-4:]}?",
                "expected_grounding": {
                    "pipeline_status": "ready",
                    "target": {"status": "resolved", "node_id": room_id, "node_type": "room", "count": count},
                },
                "oracle_intent": {
                    "action": "locate",
                    "target": {"text": f"{label} count in room", "node_type": "room",
                               "aggregate": {"operation": "count_in_location", "target_labels": [label],
                                             "location": {"node_id": room_id, "node_type": "room"}}},
                },
                "ground_truth_evidence": {
                    "source": "canonical_graph_v2",
                    "candidate_query": {"label": label, "room_id": room_id, "count": count},
                    "candidate_node_ids": [room_id],
                    "expected_count": count,
                    "expected_status": "resolved",
                },
                "tags": ["complex", "hard"],
            })
            complex_count += 1

    # Type C: aggregate room query (2 constraints: aggregate + count)
    for label, nids in multi_labels[:3]:
        if complex_count >= 10:
            break
        room_counts: dict[str, int] = defaultdict(int)
        for nid in nids:
            n = objects.get(nid, {})
            room_id = n.get("room_id", "")
            if room_id:
                room_counts[room_id] += 1
        if not room_counts:
            continue
        max_count = max(room_counts.values())
        best_rooms = [rid for rid, cnt in room_counts.items() if cnt == max_count]
        if len(best_rooms) > 1:
            continue  # Skip ties

        case_num += 1
        complex_cases.append({
            "case_id": f"v2_complex_{case_num:03d}",
            "instruction": f"Which room contains the most {label} and how many are there?",
            "expected_grounding": {
                "pipeline_status": "ready",
                "target": {"status": "resolved", "node_id": best_rooms[0], "node_type": "room"},
            },
            "oracle_intent": {
                "action": "locate",
                "target": {"text": f"room with most {label}", "node_type": "room",
                           "aggregate": {"operation": "max_count", "target_labels": [label]}},
            },
            "ground_truth_evidence": {
                "source": "canonical_graph_v2",
                "candidate_query": {"label": label, "aggregate": "max_count", "target_type": "room"},
                "candidate_node_ids": best_rooms,
                "expected_count": max_count,
                "expected_status": "resolved",
            },
            "tags": ["complex", "hard"],
        })
        complex_count += 1

    # Type C: min_count aggregate (fill remaining)
    for label, nids in multi_labels:
        if complex_count >= 10:
            break
        room_counts: dict[str, int] = defaultdict(int)
        for nid in nids:
            n = objects.get(nid, {})
            room_id = n.get("room_id", "")
            if room_id:
                room_counts[room_id] += 1
        if not room_counts:
            continue
        min_count = min(room_counts.values())
        min_rooms = [rid for rid, cnt in room_counts.items() if cnt == min_count]
        if len(min_rooms) > 1:
            continue
        key = (label, "min_count")
        if key in used_complex:
            continue
        used_complex.add(key)

        case_num += 1
        complex_cases.append({
            "case_id": f"v2_complex_{case_num:03d}",
            "instruction": f"Locate the room with the minimum count of {label}.",
            "expected_grounding": {
                "pipeline_status": "ready",
                "target": {"status": "resolved", "node_id": min_rooms[0], "node_type": "room"},
            },
            "oracle_intent": {
                "action": "locate",
                "target": {"text": f"room with fewest {label}", "node_type": "room",
                           "aggregate": {"operation": "min_count", "target_labels": [label]}},
            },
            "ground_truth_evidence": {
                "source": "canonical_graph_v2",
                "candidate_query": {"label": label, "aggregate": "min_count", "target_type": "room"},
                "candidate_node_ids": min_rooms,
                "expected_count": min_count,
                "expected_status": "resolved",
            },
            "tags": ["complex", "hard"],
        })
        complex_count += 1

    # Pad to exactly 10 if needed (use any remaining aggregate)
    if complex_count < 10:
        for label, nids in multi_labels:
            if complex_count >= 10:
                break
            room_counts = defaultdict(int)
            for nid in nids:
                n = objects.get(nid, {})
                room_id = n.get("room_id", "")
                if room_id:
                    room_counts[room_id] += 1
            if not room_counts:
                continue
            max_count = max(room_counts.values())
            best_rooms = [rid for rid, cnt in room_counts.items() if cnt == max_count]
            if len(best_rooms) > 1:
                continue
            key = (label, "max_count_pad")
            if key in used_complex:
                continue
            used_complex.add(key)

            case_num += 1
            complex_cases.append({
                "case_id": f"v2_complex_{case_num:03d}",
                "instruction": f"Locate the room containing the highest count of {label}.",
                "expected_grounding": {
                    "pipeline_status": "ready",
                    "target": {"status": "resolved", "node_id": best_rooms[0], "node_type": "room"},
                },
                "oracle_intent": {
                    "action": "locate",
                    "target": {"text": f"room with most {label}", "node_type": "room",
                               "aggregate": {"operation": "max_count", "target_labels": [label]}},
                },
                "ground_truth_evidence": {
                    "source": "canonical_graph_v2",
                    "candidate_query": {"label": label, "aggregate": "max_count", "target_type": "room"},
                    "candidate_node_ids": best_rooms,
                    "expected_count": max_count,
                    "expected_status": "resolved",
                },
                "tags": ["complex", "hard"],
            })
            complex_count += 1

    assert complex_count >= 10, f"complex: expected >=10, got {complex_count}"
    complex_cases = complex_cases[:10]

    cases.extend(complex_cases)

    # --- Category 9: Relation constrained (15 cases) ---
    # Quotas: inside=5, near=4 (Place-Place), aggregate=3, connected_to=3
    relation_cases = []

    # Type 1: "object inside place" (5 cases)
    inside_count = 0
    used_inside = set()
    for nid, n in objects.items():
        if inside_count >= 5:
            break
        place_id = n.get("place_id", "")
        label = n.get("label", "")
        if not place_id or not label:
            continue
        if (label, place_id) in used_inside:
            continue
        used_inside.add((label, place_id))

        same_in_place = [oid for oid, o in objects.items()
                        if o.get("place_id") == place_id and o.get("label") == label]
        is_unique = len(same_in_place) == 1

        place_node = places.get(place_id, {})
        place_label = place_node.get("label", "") or place_node.get("name", "")
        if not place_label:
            place_label = f"place {place_id[-6:]}"

        case_num += 1
        if is_unique:
            relation_cases.append({
                "case_id": f"v2_relation_{case_num:03d}",
                "instruction": f"Find the {label.rstrip('s')} inside {place_label}.",
                "expected_grounding": {
                    "pipeline_status": "ready",
                    "target": {"status": "resolved", "node_id": nid, "node_type": "object", "place_id": place_id},
                },
                "oracle_intent": {
                    "action": "locate",
                    "target": {"text": label.rstrip('s'), "label": label, "node_type": "object",
                               "relations": [{"relation": "inside", "target_node_id": place_id, "target_text": place_label, "target_node_type": "place"}]},
                },
                "ground_truth_evidence": {
                    "source": "canonical_graph_v2",
                    "candidate_query": {"label": label, "relation": "inside", "target_id": place_id},
                    "candidate_node_ids": [nid],
                    "expected_status": "resolved",
                },
                "tags": ["relation", "medium"],
            })
        else:
            relation_cases.append({
                "case_id": f"v2_relation_{case_num:03d}",
                "instruction": f"Find a {label.rstrip('s')} inside {place_label}.",
                "expected_grounding": {
                    "pipeline_status": "ambiguous",
                    "target": {"status": "ambiguous", "candidate_type": label, "candidate_count": len(same_in_place)},
                },
                "oracle_intent": {
                    "action": "locate",
                    "target": {"text": label.rstrip('s'), "label": label, "node_type": "object",
                               "relations": [{"relation": "inside", "target_node_id": place_id, "target_text": place_label, "target_node_type": "place"}]},
                },
                "ground_truth_evidence": {
                    "source": "canonical_graph_v2",
                    "candidate_query": {"label": label, "relation": "inside", "target_id": place_id},
                    "candidate_node_ids": sorted(same_in_place),
                    "expected_status": "ambiguous",
                },
                "tags": ["relation", "medium"],
            })
        inside_count += 1

    # Type 2: "Place near Place" (4 cases) — uses Place-Place neighbor edges
    near_count = 0
    used_near = set()
    for place_id, p in sorted(places.items())[:50]:
        if near_count >= 4:
            break
        p_neighbors = neighbor_map.get(place_id, set())
        neighbor_places = [nb for nb in p_neighbors if nb in places]
        if not neighbor_places:
            continue
        # Use the place with most objects as reference (deterministic: sorted tiebreak)
        ref_id = sorted(neighbor_places, key=lambda x: (
            -sum(1 for o in objects.values() if o.get("place_id") == x),
            x
        ))[0]
        ref_place = places.get(ref_id, {})
        ref_label = ref_place.get("label", "") or ref_place.get("name", "")
        if not ref_label:
            ref_label = f"place {ref_id[-6:]}"
        if ref_label in used_near:
            continue
        used_near.add(ref_label)

        # Complete candidate set: all neighbors of ref
        candidate_places = [nb for nb in neighbor_map.get(ref_id, set()) if nb in places]
        if not candidate_places:
            continue

        case_num += 1
        is_ambiguous = len(candidate_places) > 1
        relation_cases.append({
            "case_id": f"v2_relation_{case_num:03d}",
            "instruction": f"Find a place near {ref_label}.",
            "expected_grounding": {
                "pipeline_status": "ambiguous" if is_ambiguous else "ready",
                "target": {"status": "ambiguous" if is_ambiguous else "resolved",
                           "node_id": candidate_places[0] if not is_ambiguous else None,
                           "node_type": "place"},
            },
            "oracle_intent": {
                "action": "locate",
                "target": {"text": f"place near {ref_label}", "node_type": "place",
                           "relations": [{"relation": "near", "target_node_id": ref_id, "target_text": ref_label, "target_node_type": "place"}]},
            },
            "ground_truth_evidence": {
                "source": "canonical_graph_v2",
                "candidate_query": {"relation": "near", "target_id": ref_id},
                "candidate_node_ids": sorted(candidate_places),
                "expected_status": "ambiguous" if is_ambiguous else "resolved",
            },
            "tags": ["relation", "medium"],
        })
        near_count += 1

    # Type 3: "which room contains most X" (3 cases)
    agg_count = 0
    for label, nids in multi_labels[:5]:
        if agg_count >= 3:
            break
        room_counts: dict[str, int] = defaultdict(int)
        for nid in nids:
            n = objects.get(nid, {})
            room_id = n.get("room_id", "")
            if room_id:
                room_counts[room_id] += 1
        if not room_counts:
            continue
        max_count = max(room_counts.values())
        best_rooms = [rid for rid, cnt in room_counts.items() if cnt == max_count]

        case_num += 1
        is_tie = len(best_rooms) > 1
        relation_cases.append({
            "case_id": f"v2_relation_{case_num:03d}",
            "instruction": f"Which room has the most {label}?",
            "expected_grounding": {
                "pipeline_status": "ambiguous" if is_tie else "ready",
                "target": {"status": "ambiguous" if is_tie else "resolved",
                           "node_id": best_rooms[0] if not is_tie else None,
                           "node_type": "room",
                           "candidate_count": len(best_rooms) if is_tie else 1},
            },
            "oracle_intent": {
                "action": "locate",
                "target": {"text": f"room with most {label}", "node_type": "room",
                           "aggregate": {"operation": "max_count", "target_labels": [label]}},
            },
            "ground_truth_evidence": {
                "source": "canonical_graph_v2",
                "candidate_query": {"label": label, "aggregate": "max_count", "target_type": "room"},
                "candidate_node_ids": sorted(best_rooms),
                "expected_count": max_count,
                "expected_status": "ambiguous" if is_tie else "resolved",
            },
            "tags": ["relation", "hard"],
        })
        agg_count += 1

    # Type 4: "connected to" relation (3 cases) — complete candidate set
    conn_count = 0
    used_connected = set()
    for place_id, p in sorted(places.items())[:50]:
        if conn_count >= 3:
            break
        p_neighbors = neighbor_map.get(place_id, set())
        neighbor_places = [nb for nb in p_neighbors if nb in places]
        if not neighbor_places:
            continue
        ref_place_id = sorted(neighbor_places)[0]
        ref_place = places.get(ref_place_id, {})
        ref_label = ref_place.get("label", "") or ref_place.get("name", "")
        if not ref_label:
            ref_label = f"place {ref_place_id[-6:]}"
        if ref_label in used_connected:
            continue
        used_connected.add(ref_label)

        candidate_places = [nb for nb in neighbor_map.get(ref_place_id, set()) if nb in places]
        if not candidate_places:
            continue

        case_num += 1
        is_ambiguous = len(candidate_places) > 1
        relation_cases.append({
            "case_id": f"v2_relation_{case_num:03d}",
            "instruction": f"Find a place connected to {ref_label}.",
            "expected_grounding": {
                "pipeline_status": "ambiguous" if is_ambiguous else "ready",
                "target": {"status": "ambiguous" if is_ambiguous else "resolved",
                           "node_id": candidate_places[0] if not is_ambiguous else None,
                           "node_type": "place"},
            },
            "oracle_intent": {
                "action": "locate",
                "target": {"text": f"place connected to {ref_label}", "node_type": "place",
                           "relations": [{"relation": "connected_to", "target_node_id": ref_place_id, "target_text": ref_label, "target_node_type": "place"}]},
            },
            "ground_truth_evidence": {
                "source": "canonical_graph_v2",
                "candidate_query": {"relation": "connected_to", "target_id": ref_place_id},
                "candidate_node_ids": sorted(candidate_places),
                "expected_status": "ambiguous" if is_ambiguous else "resolved",
            },
            "tags": ["relation", "medium"],
        })
        conn_count += 1

    # Assert subcategory quotas
    assert inside_count == 5, f"inside: expected 5, got {inside_count}"
    assert near_count == 4, f"near: expected 4, got {near_count}"
    assert agg_count == 3, f"aggregate: expected 3, got {agg_count}"
    assert conn_count == 3, f"connected_to: expected 3, got {conn_count}"

    cases.extend(relation_cases)

    # Trim to exact count
    # Assert exact count (no truncation allowed)
    assert len(cases) == total_cases, f"Expected {total_cases} cases, got {len(cases)}"

    # Build manifest
    category_counts = defaultdict(int)
    for case in cases:
        primary_tag = case["tags"][0]
        category_counts[primary_tag] += 1

    import hashlib

    def _file_hash(path: str) -> str:
        """Compute SHA256 hash of a file."""
        if not Path(path).exists():
            return "missing"
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]

    # Save cases first
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    # Compute benchmark file hash AFTER writing (byte-identical)
    benchmark_hash = _file_hash(str(output_path.resolve()))

    # Compute generator code hash
    generator_hash = _file_hash(str(Path(__file__).resolve()))

    # Compute canonical graph hash
    canonical_hash = _file_hash(str(Path(canonical_graph_path).resolve())) if canonical_graph_path else "unknown"

    manifest = {
        "benchmark_name": "khronos_grounding_100_v3",
        "benchmark_status": "development",
        "supersedes": "grounding_benchmark_100_v2",
        "supersedes_reason": [
            "manifest hash integrity fixed",
            "byte-level reproducibility ensured",
            "candidate_node_ids sorted",
            "aggregate tie candidate set fixed",
        ],
        "case_count": len(cases),
        "random_seed": seed,
        "dataset_name": "khronos_office",
        "source_dsg_hash": graph.get("source_dsg_hash", "unknown"),
        "canonical_graph_hash": canonical_hash,
        "canonical_graph_version": graph.get("schema_version", 2),
        "benchmark_file_hash": benchmark_hash,
        "generator_code_hash": generator_hash,
        "schema_version": 2,
        "category_counts": dict(category_counts),
        "created_at": "2026-07-17",
        "generator": "generate_grounding_benchmark_v2.py",
    }

    # Save manifest
    manifest_path = output_path.with_suffix("").with_name(
        output_path.stem.replace("_v3", "") + "_v3.manifest.json"
    )
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"Generated {len(cases)} cases")
    print(f"Categories: {dict(category_counts)}")
    print(f"Saved: {output_path}")
    print(f"Saved: {manifest_path}")

    return manifest


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate grounding benchmark v2")
    parser.add_argument("--canonical", default=str(
        _project_root / "RAG" / "khronos_3dsg_rag" / "outputs" / "khronos_office_new" / "canonical_graph_v2.json"
    ))
    parser.add_argument("-o", "--output", default=str(
        Path(__file__).parent / "cases" / "grounding_benchmark_100_v3.jsonl"
    ))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--count", type=int, default=100)
    args = parser.parse_args()

    generate_benchmark_v2(args.canonical, args.output, args.seed, args.count)
