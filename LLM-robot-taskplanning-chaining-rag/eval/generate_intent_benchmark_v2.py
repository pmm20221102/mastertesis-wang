"""Generate Intent Benchmark v2 — 150 cases with full role/action coverage.

Unlike v1 (extracted from grounding benchmark), v2 is synthetically generated
to ensure coverage of all intent types required by the review.

Coverage targets (§7.5):
  navigate_target: 15, locate_target: 15, room/place_constraint: 20,
  relation: 20, spatial: 15, set_quantifier: 10, aggregate: 10,
  source_destination: 10, escort_guide: 10, avoid_prefer: 10,
  unsupported: 5, ambiguous: 5, multi_step: 15

Usage:
    python -m eval.generate_intent_benchmark_v2 \
        --canonical-graph /path/to/canonical_graph_v2.json \
        --output eval/cases/intent_benchmark_150_v2.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _load_canonical_graph(path: str) -> dict:
    """Load canonical graph and extract nodes by type."""
    with open(path, "r", encoding="utf-8") as f:
        graph = json.load(f)

    # nodes is a dict: {node_id: node_data}
    raw_nodes = graph.get("nodes", {})
    if isinstance(raw_nodes, dict):
        nodes = list(raw_nodes.values())
    else:
        nodes = raw_nodes

    by_type = {"object": [], "place": [], "room": [], "agent": []}
    for n in nodes:
        nt = n.get("node_type", "")
        if nt in by_type:
            by_type[nt].append(n)

    # Build neighbor map
    raw_edges = graph.get("edges", {})
    neighbor_map = {}
    parent_map = {}

    if isinstance(raw_edges, dict):
        # v2 format: {"hierarchy": [...], "neighbors": [...]}
        for e in raw_edges.get("neighbors", []):
            src, tgt = e["source"], e["target"]
            neighbor_map.setdefault(src, set()).add(tgt)
            neighbor_map.setdefault(tgt, set()).add(src)
        for e in raw_edges.get("hierarchy", []):
            parent_map.setdefault(e["child"], set()).add(e["parent"])
    elif isinstance(raw_edges, list):
        # v1 format: list of edge dicts
        for e in raw_edges:
            if e.get("relation") == "neighbor":
                src, tgt = e["source"], e["target"]
                neighbor_map.setdefault(src, set()).add(tgt)
                neighbor_map.setdefault(tgt, set()).add(src)
            elif e.get("relation") == "contains":
                parent_map.setdefault(e["target"], set()).add(e["source"])

    return {"nodes": nodes, "by_type": by_type, "neighbor_map": neighbor_map, "parent_map": parent_map}


def _pick_random(graph, node_type, n=1):
    """Pick random nodes of given type."""
    nodes = graph["by_type"].get(node_type, [])
    return random.sample(nodes, min(n, len(nodes)))


def _room_label(node):
    """Extract human-readable room label. Use short ID suffix as fallback."""
    label = node.get("label", "")
    if label and label != "unknown":
        return label
    # Use last 4 digits of node_id as room identifier
    nid = node.get("node_id", "0")
    return f"room_{nid[-4:]}"


def _place_label(node):
    """Extract human-readable place label. Use short ID suffix as fallback."""
    label = node.get("label", "")
    if label and label != "unknown":
        return label
    nid = node.get("node_id", "0")
    return f"place_{nid[-4:]}"


def _object_label(node):
    """Extract human-readable object label."""
    return node.get("label", "unknown object")


def _generate_navigate_target(graph, count=15):
    """Simple navigate to target."""
    cases = []
    objects = _pick_random(graph, "object", count)
    for i, obj in enumerate(objects):
        label = _object_label(obj)
        cases.append({
            "case_id": f"intent_v2_nav_{i+1:03d}",
            "instruction": f"Navigate to the {label}.",
            "oracle_intent": {
                "action": "navigate",
                "target": {"text": label, "label": label, "node_type": "object"},
            },
            "required_roles": ["target"],
            "category": "navigate_target",
            "tags": ["navigate", "target"],
        })
    return cases


def _generate_locate_target(graph, count=15):
    """Simple locate/find target."""
    cases = []
    objects = _pick_random(graph, "object", count)
    for i, obj in enumerate(objects):
        label = _object_label(obj)
        cases.append({
            "case_id": f"intent_v2_loc_{i+1:03d}",
            "instruction": f"Find the {label}.",
            "oracle_intent": {
                "action": "locate",
                "target": {"text": label, "label": label, "node_type": "object"},
            },
            "required_roles": ["target"],
            "category": "locate_target",
            "tags": ["locate", "target"],
        })
    return cases


def _generate_room_place_constraint(graph, count=20):
    """Navigate with room or place constraint."""
    cases = []
    objects = _pick_random(graph, "object", count)
    rooms = _pick_random(graph, "room", count)
    places = _pick_random(graph, "place", count)

    for i in range(count):
        obj = objects[i % len(objects)]
        label = _object_label(obj)

        if i < count // 2:
            # Room constraint
            room = rooms[i % len(rooms)]
            room_label = _room_label(room)
            cases.append({
                "case_id": f"intent_v2_rpc_{i+1:03d}",
                "instruction": f"Navigate to the {label} in room {room_label}.",
                "oracle_intent": {
                    "action": "navigate",
                    "target": {"text": label, "label": label, "node_type": "object", "room": room_label},
                },
                "required_roles": ["target"],
                "category": "room_place_constraint",
                "tags": ["navigate", "room_constraint"],
            })
        else:
            # Place constraint
            place = places[i % len(places)]
            place_label = _place_label(place)
            cases.append({
                "case_id": f"intent_v2_rpc_{i+1:03d}",
                "instruction": f"Find the {label} in {place_label}.",
                "oracle_intent": {
                    "action": "locate",
                    "target": {"text": label, "label": label, "node_type": "object",
                              "locations": [{"text": place_label, "node_type": "place"}]},
                },
                "required_roles": ["target"],
                "category": "room_place_constraint",
                "tags": ["locate", "place_constraint"],
            })
    return cases


def _generate_relation(graph, count=20):
    """Navigate with relation constraint."""
    cases = []
    objects = _pick_random(graph, "object", count)
    places = _pick_random(graph, "place", count)
    rooms = _pick_random(graph, "room", count)

    for i in range(count):
        obj = objects[i % len(objects)]
        label = _object_label(obj)

        if i % 3 == 0:
            # near relation
            place = places[i % len(places)]
            p_label = _place_label(place)
            cases.append({
                "case_id": f"intent_v2_rel_{i+1:03d}",
                "instruction": f"Find the {label} near {p_label}.",
                "oracle_intent": {
                    "action": "locate",
                    "target": {"text": label, "label": label, "node_type": "object",
                              "relations": [{"relation": "near", "target_text": p_label, "target_label": p_label}]},
                },
                "required_roles": ["target"],
                "category": "relation",
                "tags": ["locate", "relation"],
            })
        elif i % 3 == 1:
            # inside relation
            place = places[i % len(places)]
            p_label = _place_label(place)
            cases.append({
                "case_id": f"intent_v2_rel_{i+1:03d}",
                "instruction": f"Find the {label} inside {p_label}.",
                "oracle_intent": {
                    "action": "locate",
                    "target": {"text": label, "label": label, "node_type": "object",
                              "relations": [{"relation": "inside", "target_text": p_label, "target_label": p_label}]},
                },
                "required_roles": ["target"],
                "category": "relation",
                "tags": ["locate", "relation"],
            })
        else:
            # connected_to relation
            place = places[i % len(places)]
            p_label = _place_label(place)
            cases.append({
                "case_id": f"intent_v2_rel_{i+1:03d}",
                "instruction": f"Find the {label} connected to {p_label}.",
                "oracle_intent": {
                    "action": "locate",
                    "target": {"text": label, "label": label, "node_type": "object",
                              "relations": [{"relation": "connected_to", "target_text": p_label, "target_label": p_label}]},
                },
                "required_roles": ["target"],
                "category": "relation",
                "tags": ["locate", "relation"],
            })
    return cases


def _generate_spatial(graph, count=15):
    """Spatial queries with nearest quantifier."""
    cases = []
    objects = _pick_random(graph, "object", count)
    for i, obj in enumerate(objects):
        label = _object_label(obj)
        cases.append({
            "case_id": f"intent_v2_spa_{i+1:03d}",
            "instruction": f"Find the closest {label}.",
            "oracle_intent": {
                "action": "locate",
                "target": {"text": label, "label": label, "node_type": "object", "quantifier": "nearest"},
            },
            "required_roles": ["target"],
            "category": "spatial",
            "tags": ["locate", "spatial"],
        })
    return cases


def _generate_set_quantifier(graph, count=10):
    """Set/quantifier queries."""
    cases = []
    objects = _pick_random(graph, "object", count)
    for i, obj in enumerate(objects):
        label = _object_label(obj)
        cases.append({
            "case_id": f"intent_v2_set_{i+1:03d}",
            "instruction": f"Find all {label}.",
            "oracle_intent": {
                "action": "locate",
                "target": {"text": label, "label": label, "node_type": "object", "quantifier": "all"},
            },
            "required_roles": ["target"],
            "category": "set_quantifier",
            "tags": ["locate", "set_query"],
        })
    return cases


def _generate_aggregate(graph, count=10):
    """Aggregate queries."""
    cases = []
    objects = _pick_random(graph, "object", count)
    rooms = _pick_random(graph, "room", count)

    for i in range(count):
        obj = objects[i % len(objects)]
        label = _object_label(obj)

        if i < 5:
            # count_in_location
            room = rooms[i % len(rooms)]
            r_label = _room_label(room)
            cases.append({
                "case_id": f"intent_v2_agg_{i+1:03d}",
                "instruction": f"How many {label} are in {r_label}?",
                "oracle_intent": {
                    "action": "locate",
                    "target": {"text": label, "label": label, "node_type": "object",
                              "quantifier": "all",
                              "aggregate": {"operation": "count_in_location", "target_labels": [label],
                                           "location": {"text": r_label, "node_type": "room"}}},
                },
                "required_roles": ["target"],
                "category": "aggregate",
                "tags": ["locate", "aggregate"],
            })
        else:
            # max_count
            cases.append({
                "case_id": f"intent_v2_agg_{i+1:03d}",
                "instruction": f"Which room has the most {label}?",
                "oracle_intent": {
                    "action": "locate",
                    "target": {"text": label, "label": label, "node_type": "object",
                              "quantifier": "all",
                              "aggregate": {"operation": "max_count", "target_labels": [label]}},
                },
                "required_roles": ["target"],
                "category": "aggregate",
                "tags": ["locate", "aggregate"],
            })
    return cases


def _generate_source_destination(graph, count=10):
    """Source → destination navigation."""
    cases = []
    places = _pick_random(graph, "place", count * 2)
    for i in range(count):
        src = places[i * 2]
        dst = places[i * 2 + 1]
        src_label = _place_label(src)
        dst_label = _place_label(dst)
        cases.append({
            "case_id": f"intent_v2_sd_{i+1:03d}",
            "instruction": f"Navigate from {src_label} to {dst_label}.",
            "oracle_intent": {
                "action": "navigate",
                "source": {"text": src_label, "node_type": "place"},
                "destination": {"text": dst_label, "node_type": "place"},
            },
            "required_roles": ["source", "destination"],
            "category": "source_destination",
            "tags": ["navigate", "source_destination"],
        })
    return cases


def _generate_escort_guide(graph, count=10):
    """Escort/guide instructions."""
    cases = []
    agents = _pick_random(graph, "agent", count) if graph["by_type"]["agent"] else [{"label": "person"}] * count
    places = _pick_random(graph, "place", count)

    for i in range(count):
        agent = agents[i % len(agents)]
        place = places[i % len(places)]
        agent_label = agent.get("label", "person")
        place_label = _place_label(place)
        action = "escort" if i % 2 == 0 else "guide"
        cases.append({
            "case_id": f"intent_v2_esc_{i+1:03d}",
            "instruction": f"Escort the {agent_label} to {place_label}.",
            "oracle_intent": {
                "action": action,
                "actor": {"text": agent_label, "node_type": "agent"},
                "destination": {"text": place_label, "node_type": "place"},
            },
            "required_roles": ["actor", "destination"],
            "category": "escort_guide",
            "tags": [action, "escort_guide"],
        })
    return cases


def _generate_avoid_prefer(graph, count=10):
    """Avoid/prefer constraint instructions."""
    cases = []
    objects = _pick_random(graph, "object", count)
    places = _pick_random(graph, "place", count)

    for i in range(count):
        obj = objects[i % len(objects)]
        place = places[i % len(places)]
        label = _object_label(obj)
        p_label = _place_label(place)

        if i % 2 == 0:
            cases.append({
                "case_id": f"intent_v2_avd_{i+1:03d}",
                "instruction": f"Navigate to {label}, avoid {p_label}.",
                "oracle_intent": {
                    "action": "navigate",
                    "target": {"text": label, "label": label, "node_type": "object"},
                    "constraints": [{"type": "avoid", "description": f"Avoid {p_label}", "target_text": p_label}],
                },
                "required_roles": ["target"],
                "category": "avoid_prefer",
                "tags": ["navigate", "constraint"],
            })
        else:
            cases.append({
                "case_id": f"intent_v2_avd_{i+1:03d}",
                "instruction": f"Navigate to {label}, prefer the shortest route.",
                "oracle_intent": {
                    "action": "navigate",
                    "target": {"text": label, "label": label, "node_type": "object"},
                    "constraints": [{"type": "prefer", "description": "Shortest route"}],
                },
                "required_roles": ["target"],
                "category": "avoid_prefer",
                "tags": ["navigate", "constraint"],
            })
    return cases


def _generate_unsupported(count=5):
    """Unsupported manipulation instructions."""
    cases = []
    verbs = ["Pick up", "Grab", "Carry", "Lift", "Place"]
    nouns = ["the chair", "the box", "the tool", "the device", "the object"]
    for i in range(count):
        cases.append({
            "case_id": f"intent_v2_uns_{i+1:03d}",
            "instruction": f"{verbs[i]} {nouns[i]}.",
            "oracle_intent": {"action": "unsupported"},
            "required_roles": [],
            "category": "unsupported",
            "tags": ["unsupported"],
        })
    return cases


def _generate_ambiguous(graph, count=5):
    """Ambiguous instructions (multiple possible targets)."""
    cases = []
    objects = _pick_random(graph, "object", count)
    for i, obj in enumerate(objects):
        label = _object_label(obj)
        cases.append({
            "case_id": f"intent_v2_amb_{i+1:03d}",
            "instruction": f"Find a {label}.",
            "oracle_intent": {
                "action": "locate",
                "target": {"text": label, "label": label, "node_type": "object"},
            },
            "required_roles": ["target"],
            "category": "ambiguous",
            "tags": ["locate", "ambiguous"],
        })
    return cases


def _generate_multi_step(graph, count=15):
    """Multi-step instructions."""
    cases = []
    objects = _pick_random(graph, "object", count)
    places = _pick_random(graph, "place", count)

    for i in range(count):
        obj = objects[i % len(objects)]
        place = places[i % len(places)]
        label = _object_label(obj)
        p_label = _place_label(place)

        if i % 3 == 0:
            # locate + navigate
            cases.append({
                "case_id": f"intent_v2_ms_{i+1:03d}",
                "instruction": f"First find the {label}, then navigate to {p_label}.",
                "oracle_intent": {
                    "action": "navigate",
                    "target": {"text": label, "label": label, "node_type": "object"},
                    "destination": {"text": p_label, "node_type": "place"},
                    "steps": [
                        {"action": "locate", "target": {"text": label, "label": label, "node_type": "object"}},
                        {"action": "navigate", "destination": {"text": p_label, "node_type": "place"}},
                    ],
                },
                "required_roles": ["target", "destination"],
                "category": "multi_step",
                "tags": ["multi_step"],
            })
        elif i % 3 == 1:
            # find + escort
            agent_label = "person"
            cases.append({
                "case_id": f"intent_v2_ms_{i+1:03d}",
                "instruction": f"Find the {label}, then escort the person to {p_label}.",
                "oracle_intent": {
                    "action": "escort",
                    "actor": {"text": agent_label, "node_type": "agent"},
                    "destination": {"text": p_label, "node_type": "place"},
                    "steps": [
                        {"action": "locate", "target": {"text": label, "label": label, "node_type": "object"}},
                        {"action": "escort", "actor": {"text": agent_label, "node_type": "agent"},
                         "destination": {"text": p_label, "node_type": "place"}},
                    ],
                },
                "required_roles": ["actor", "destination"],
                "category": "multi_step",
                "tags": ["multi_step"],
            })
        else:
            # navigate + avoid
            avoid_label = _place_label(places[(i + 1) % len(places)])
            cases.append({
                "case_id": f"intent_v2_ms_{i+1:03d}",
                "instruction": f"Navigate to {label}, avoiding {avoid_label}.",
                "oracle_intent": {
                    "action": "navigate",
                    "target": {"text": label, "label": label, "node_type": "object"},
                    "constraints": [{"type": "avoid", "description": f"Avoid {avoid_label}", "target_text": avoid_label}],
                },
                "required_roles": ["target"],
                "category": "multi_step",
                "tags": ["multi_step"],
            })
    return cases


def generate_intent_benchmark_v2(
    canonical_graph_path: str,
    output_path: str,
    seed: int = 42,
) -> dict:
    """Generate v2 intent benchmark with full coverage."""
    random.seed(seed)

    graph = _load_canonical_graph(canonical_graph_path)
    output = Path(output_path)

    all_cases = []
    all_cases.extend(_generate_navigate_target(graph, 15))
    all_cases.extend(_generate_locate_target(graph, 15))
    all_cases.extend(_generate_room_place_constraint(graph, 20))
    all_cases.extend(_generate_relation(graph, 20))
    all_cases.extend(_generate_spatial(graph, 15))
    all_cases.extend(_generate_set_quantifier(graph, 10))
    all_cases.extend(_generate_aggregate(graph, 10))
    all_cases.extend(_generate_source_destination(graph, 10))
    all_cases.extend(_generate_escort_guide(graph, 10))
    all_cases.extend(_generate_avoid_prefer(graph, 10))
    all_cases.extend(_generate_unsupported(5))
    all_cases.extend(_generate_ambiguous(graph, 5))
    all_cases.extend(_generate_multi_step(graph, 15))

    # Sort by case_id
    all_cases.sort(key=lambda c: c["case_id"])

    # Deduplicate instructions
    seen_instructions = set()
    unique_cases = []
    for case in all_cases:
        instr = case["instruction"].lower().strip()
        if instr not in seen_instructions:
            seen_instructions.add(instr)
            unique_cases.append(case)
        else:
            # Regenerate with different phrasing
            case["instruction"] = case["instruction"].replace(".", " please.")
            unique_cases.append(case)

    all_cases = unique_cases

    # Validate oracle_intents
    from planner_schemas.intent import TaskIntent
    errors = []
    for case in all_cases:
        try:
            TaskIntent.model_validate(case["oracle_intent"])
        except Exception as e:
            errors.append(f'{case["case_id"]}: {e}')

    if errors:
        print(f"WARNING: {len(errors)} invalid oracle intents:")
        for e in errors[:5]:
            print(f"  {e}")

    # Write
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        for case in all_cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    # Compute hashes
    with open(output, "rb") as f:
        bench_hash = hashlib.sha256(f.read()).hexdigest()[:16]

    # Manifest
    cat_dist = {}
    role_dist = {}
    for c in all_cases:
        cat = c["category"]
        cat_dist[cat] = cat_dist.get(cat, 0) + 1
        roles = "+".join(sorted(c["required_roles"]))
        role_dist[roles] = role_dist.get(roles, 0) + 1

    manifest = {
        "benchmark_status": "ready",
        "benchmark_version": "v2",
        "benchmark_file": str(output.name),
        "benchmark_file_hash": bench_hash,
        "case_count": len(all_cases),
        "categories": cat_dist,
        "required_roles_distribution": role_dist,
        "seed": seed,
        "coverage_targets": {
            "navigate_target": 15, "locate_target": 15,
            "room_place_constraint": 20, "relation": 20,
            "spatial": 15, "set_quantifier": 10, "aggregate": 10,
            "source_destination": 10, "escort_guide": 10,
            "avoid_prefer": 10, "unsupported": 5, "ambiguous": 5,
            "multi_step": 15,
        },
    }

    manifest_path = output.with_name(output.stem + ".manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"Generated {len(all_cases)} intent cases (v2)")
    print(f"Benchmark: {output}")
    print(f"Manifest: {manifest_path}")
    print(f"Categories: {cat_dist}")
    print(f"Roles: {role_dist}")

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate Intent Benchmark v2")
    parser.add_argument(
        "--canonical-graph",
        default="RAG/khronos_3dsg_rag/outputs/khronos_office_new/canonical_graph_v2.json",
    )
    parser.add_argument(
        "--output",
        default="eval/cases/intent_benchmark_150_v2.jsonl",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    generate_intent_benchmark_v2(args.canonical_graph, args.output, args.seed)


if __name__ == "__main__":
    main()
