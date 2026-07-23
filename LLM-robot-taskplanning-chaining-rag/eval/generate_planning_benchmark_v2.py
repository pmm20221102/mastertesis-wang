"""Generate Planning Benchmark v2 — 80 cases with full coverage.

Split into two benchmarks (§35 Phase D):
- planner_generation_60_v2: 60 cases that truly invoke the Planner
- planner_status_gate_20_v2: 20 cases for status gate (ambiguous/not_found/unsupported)

Coverage (§20.6):
  single-step target: 15, source→destination: 10, escort/guide: 10,
  avoid/prefer: 10, multi-step: 15

Status gate:
  ambiguous: 10, not_found: 5, unsupported: 5

Usage:
    python -m eval.generate_planning_benchmark_v2 \
        --canonical-graph /path/to/canonical_graph_v2.json \
        --output eval/cases/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _load_graph(path: str) -> dict:
    """Load canonical graph."""
    with open(path, "r", encoding="utf-8") as f:
        graph = json.load(f)
    raw_nodes = graph.get("nodes", {})
    nodes = list(raw_nodes.values()) if isinstance(raw_nodes, dict) else raw_nodes
    by_type = {"object": [], "place": [], "room": [], "agent": []}
    for n in nodes:
        nt = n.get("node_type", "")
        if nt in by_type:
            by_type[nt].append(n)
    return {"nodes": nodes, "by_type": by_type}


def _pick(graph, nt, n=1):
    return random.sample(graph["by_type"].get(nt, []), min(n, len(graph["by_type"].get(nt, []))))


def _id(node):
    return node.get("node_id", "0")


def _label(node, fallback="object"):
    l = node.get("label", "")
    return l if l and l != "unknown" else fallback


def generate_planning_benchmark_v2(
    canonical_graph_path: str,
    output_dir: str,
    seed: int = 42,
) -> dict:
    random.seed(seed)
    graph = _load_graph(canonical_graph_path)
    out = Path(output_dir)

    generation_cases = []
    gate_cases = []

    # --- Generation cases (60) ---

    # 1. Single-step target (15)
    objects = _pick(graph, "object", 15)
    for i, obj in enumerate(objects):
        label = _label(obj, "object")
        generation_cases.append({
            "case_id": f"plan_v2_gen_{i+1:03d}",
            "instruction": f"Navigate to the {label}.",
            "oracle_grounding": {
                "action": "navigate",
                "target": {"node_id": _id(obj), "node_type": "object", "label": label},
            },
            "expected_plan": {
                "status": "valid",
                "required_actions": ["navigate_to"],
                "max_steps": 3,
                "expected_steps": [{"step": 1, "action": "navigate_to", "target_ref": "target", "target_id": _id(obj)}],
                "expected_target_ids": [_id(obj)],
            },
            "tags": ["navigation", "single_step"],
            "category": "single_step_target",
        })

    # 2. Source → destination (10)
    places = _pick(graph, "place", 20)
    for i in range(10):
        src, dst = places[i*2], places[i*2+1]
        src_label = _label(src, "place")
        dst_label = _label(dst, "place")
        generation_cases.append({
            "case_id": f"plan_v2_gen_{15+i+1:03d}",
            "instruction": f"Navigate from {src_label} to {dst_label}.",
            "oracle_grounding": {
                "action": "navigate",
                "source": {"node_id": _id(src), "node_type": "place", "label": src_label},
                "destination": {"node_id": _id(dst), "node_type": "place", "label": dst_label},
            },
            "expected_plan": {
                "status": "valid",
                "required_actions": ["navigate_to"],
                "max_steps": 3,
                "expected_steps": [{"step": 1, "action": "navigate_to", "target_ref": "destination", "target_id": _id(dst)}],
                "expected_target_ids": [_id(dst)],
            },
            "tags": ["navigation", "source_destination"],
            "category": "source_destination",
        })

    # 3. Escort/guide (10)
    agents = _pick(graph, "agent", 10) if graph["by_type"]["agent"] else [{"node_id": "agent_0", "label": "person"}] * 10
    dests = _pick(graph, "place", 10)
    for i in range(10):
        agent = agents[i % len(agents)]
        dest = dests[i % len(dests)]
        agent_label = _label(agent, "person")
        dest_label = _label(dest, "place")
        action = "escort_to" if i % 2 == 0 else "guide_to"
        generation_cases.append({
            "case_id": f"plan_v2_gen_{25+i+1:03d}",
            "instruction": f"Escort the {agent_label} to {dest_label}.",
            "oracle_grounding": {
                "action": "escort" if "escort" in action else "guide",
                "actor": {"node_id": _id(agent), "node_type": "agent", "label": agent_label},
                "destination": {"node_id": _id(dest), "node_type": "place", "label": dest_label},
            },
            "expected_plan": {
                "status": "valid",
                "required_actions": [action],
                "max_steps": 3,
                "expected_steps": [{"step": 1, "action": action, "target_ref": "actor", "target_id": _id(agent)}],
                "expected_target_ids": [_id(agent), _id(dest)],
            },
            "tags": [action.split("_")[0], "escort"],
            "category": "escort_guide",
        })

    # 4. Avoid/prefer constraints (10)
    targets = _pick(graph, "object", 5)
    avoids = _pick(graph, "place", 5)
    for i in range(10):
        if i < 5:
            # avoid constraint
            obj = targets[i]
            avoid_place = avoids[i]
            label = _label(obj, "object")
            avoid_label = _label(avoid_place, "place")
            generation_cases.append({
                "case_id": f"plan_v2_gen_{35+i+1:03d}",
                "instruction": f"Navigate to {label}, avoid {avoid_label}.",
                "oracle_grounding": {
                    "action": "navigate",
                    "target": {"node_id": _id(obj), "node_type": "object", "label": label},
                    "constraints": [{"type": "avoid", "node_id": _id(avoid_place), "node_type": "place", "label": avoid_label}],
                },
                "expected_plan": {
                    "status": "valid",
                    "required_actions": ["navigate_to"],
                    "max_steps": 3,
                    "expected_steps": [{"step": 1, "action": "navigate_to", "target_ref": "target", "target_id": _id(obj)}],
                    "expected_target_ids": [_id(obj)],
                },
                "tags": ["navigation", "constraint"],
                "category": "avoid_prefer",
            })
        else:
            # prefer constraint
            obj = targets[i - 5]
            label = _label(obj, "object")
            generation_cases.append({
                "case_id": f"plan_v2_gen_{35+i+1:03d}",
                "instruction": f"Navigate to {label}, prefer shortest route.",
                "oracle_grounding": {
                    "action": "navigate",
                    "target": {"node_id": _id(obj), "node_type": "object", "label": label},
                    "constraints": [{"type": "prefer", "node_id": "", "node_type": "unknown", "label": "shortest_route"}],
                },
                "expected_plan": {
                    "status": "valid",
                    "required_actions": ["navigate_to"],
                    "max_steps": 3,
                    "expected_steps": [{"step": 1, "action": "navigate_to", "target_ref": "target", "target_id": _id(obj)}],
                    "expected_target_ids": [_id(obj)],
                },
                "tags": ["navigation", "constraint"],
                "category": "avoid_prefer",
            })

    # 5. Multi-step (15)
    ms_objects = _pick(graph, "object", 10)
    ms_places = _pick(graph, "place", 10)
    ms_rooms = _pick(graph, "room", 5)
    for i in range(15):
        if i % 3 == 0:
            # locate + navigate
            obj = ms_objects[i % len(ms_objects)]
            place = ms_places[i % len(ms_places)]
            label = _label(obj, "object")
            p_label = _label(place, "place")
            generation_cases.append({
                "case_id": f"plan_v2_gen_{45+i+1:03d}",
                "instruction": f"First find the {label}, then navigate to {p_label}.",
                "oracle_grounding": {
                    "action": "navigate",
                    "target": {"node_id": _id(obj), "node_type": "object", "label": label},
                    "destination": {"node_id": _id(place), "node_type": "place", "label": p_label},
                },
                "expected_plan": {
                    "status": "valid",
                    "required_actions": ["locate_entity", "navigate_to"],
                    "max_steps": 5,
                    "expected_steps": [
                        {"step": 1, "action": "locate_entity", "target_ref": "target", "target_id": _id(obj)},
                        {"step": 2, "action": "navigate_to", "target_ref": "destination", "target_id": _id(place)},
                    ],
                    "expected_target_ids": [_id(obj), _id(place)],
                },
                "tags": ["multi_step"],
                "category": "multi_step",
            })
        elif i % 3 == 1:
            # find + escort
            obj = ms_objects[i % len(ms_objects)]
            agent = agents[i % len(agents)]
            place = ms_places[i % len(ms_places)]
            label = _label(obj, "object")
            agent_label = _label(agent, "person")
            p_label = _label(place, "place")
            generation_cases.append({
                "case_id": f"plan_v2_gen_{45+i+1:03d}",
                "instruction": f"Find the {label}, then escort {agent_label} to {p_label}.",
                "oracle_grounding": {
                    "action": "escort",
                    "target": {"node_id": _id(obj), "node_type": "object", "label": label},
                    "actor": {"node_id": _id(agent), "node_type": "agent", "label": agent_label},
                    "destination": {"node_id": _id(place), "node_type": "place", "label": p_label},
                },
                "expected_plan": {
                    "status": "valid",
                    "required_actions": ["locate_entity", "escort_to"],
                    "max_steps": 5,
                    "expected_steps": [
                        {"step": 1, "action": "locate_entity", "target_ref": "target", "target_id": _id(obj)},
                        {"step": 2, "action": "escort_to", "target_ref": "actor", "target_id": _id(agent)},
                    ],
                    "expected_target_ids": [_id(obj), _id(agent), _id(place)],
                },
                "tags": ["multi_step"],
                "category": "multi_step",
            })
        else:
            # navigate + avoid
            obj = ms_objects[i % len(ms_objects)]
            avoid_place = ms_places[(i + 1) % len(ms_places)]
            label = _label(obj, "object")
            avoid_label = _label(avoid_place, "place")
            generation_cases.append({
                "case_id": f"plan_v2_gen_{45+i+1:03d}",
                "instruction": f"Navigate to {label}, avoiding {avoid_label}.",
                "oracle_grounding": {
                    "action": "navigate",
                    "target": {"node_id": _id(obj), "node_type": "object", "label": label},
                    "constraints": [{"type": "avoid", "node_id": _id(avoid_place), "node_type": "place", "label": avoid_label}],
                },
                "expected_plan": {
                    "status": "valid",
                    "required_actions": ["navigate_to"],
                    "max_steps": 3,
                    "expected_steps": [{"step": 1, "action": "navigate_to", "target_ref": "target", "target_id": _id(obj)}],
                    "expected_target_ids": [_id(obj)],
                },
                "tags": ["navigation", "constraint"],
                "category": "multi_step",
            })

    # --- Status gate cases (20) ---

    # Ambiguous (10)
    for i in range(10):
        gate_cases.append({
            "case_id": f"plan_v2_gate_amb_{i+1:03d}",
            "instruction": f"Find a small_office_object.",
            "oracle_grounding": {"action": "locate", "pipeline_status": "ambiguous"},
            "expected_plan": {"status": "ambiguous", "required_actions": [], "max_steps": 0},
            "tags": ["ambiguous"],
            "category": "ambiguous",
        })

    # Not found (5)
    for i in range(5):
        gate_cases.append({
            "case_id": f"plan_v2_gate_nf_{i+1:03d}",
            "instruction": f"Go to the refrigerator.",
            "oracle_grounding": {"action": "navigate", "pipeline_status": "not_found"},
            "expected_plan": {"status": "target_not_found", "required_actions": [], "max_steps": 0},
            "tags": ["not_found"],
            "category": "not_found",
        })

    # Unsupported (5)
    for i in range(5):
        gate_cases.append({
            "case_id": f"plan_v2_gate_uns_{i+1:03d}",
            "instruction": f"Pick up the chair.",
            "oracle_grounding": {"action": "navigate", "pipeline_status": "unsupported"},
            "expected_plan": {"status": "unsupported", "required_actions": [], "max_steps": 0},
            "tags": ["unsupported"],
            "category": "unsupported",
        })

    # Combine and sort
    all_cases = generation_cases + gate_cases
    all_cases.sort(key=lambda c: c["case_id"])

    # Write generation benchmark
    gen_path = out / "planning_generation_60_v2.jsonl"
    out.mkdir(parents=True, exist_ok=True)
    with open(gen_path, "w", encoding="utf-8") as f:
        for c in generation_cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    # Write gate benchmark
    gate_path = out / "planning_status_gate_20_v2.jsonl"
    with open(gate_path, "w", encoding="utf-8") as f:
        for c in gate_cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    # Compute hashes
    for p in [gen_path, gate_path]:
        with open(p, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()[:16]
        manifest_path = p.with_suffix(".manifest.json")
        cases_list = []
        with open(p) as f:
            for line in f:
                cases_list.append(json.loads(line))
        cat_dist = {}
        for c in cases_list:
            cat = c["category"]
            cat_dist[cat] = cat_dist.get(cat, 0) + 1
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({
                "benchmark_status": "ready",
                "benchmark_version": "v2",
                "benchmark_file_hash": h,
                "case_count": len(cases_list),
                "categories": cat_dist,
                "seed": seed,
            }, f, indent=2)

    print(f"Generation: {len(generation_cases)} cases → {gen_path}")
    print(f"Status gate: {len(gate_cases)} cases → {gate_path}")
    print(f"Generation categories: {json.dumps({c['category']: sum(1 for x in generation_cases if x['category'] == c['category']) for c in generation_cases}, indent=2)}")

    return {"generation": len(generation_cases), "gate": len(gate_cases)}


def main():
    parser = argparse.ArgumentParser(description="Generate Planning Benchmark v2")
    parser.add_argument("--canonical-graph", default="/home/nenlian/Masterthesis-wang/RAG/khronos_3dsg_rag/outputs/khronos_office_new/canonical_graph_v2.json")
    parser.add_argument("--output", default="eval/cases")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    generate_planning_benchmark_v2(args.canonical_graph, args.output, args.seed)


if __name__ == "__main__":
    main()
