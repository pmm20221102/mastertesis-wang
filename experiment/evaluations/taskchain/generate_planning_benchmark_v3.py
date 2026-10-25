"""Generate Planning Benchmark v3 — 60 generation + 20 gate cases.

Fixes from v2 review:
- [P0 #27] 0 duplicate instructions (use visible unique identifiers)
- [P0 #28] Escort/Guide instruction matches expected action
- [P0 #29, #30] expected_target_ids achievable with role_refs/role_ids
- [P0 #32] 10+ cases with step-specific refs
- [P0 #33] All 15 multi-step cases have real steps (count >= 2)
- [P0 #39] Status gate uses diverse labels per case
- [P0 #40] Status gate unsupported oracle action = "unsupported"

Usage:
    python -m eval.generate_planning_benchmark_v3 \\
        --canonical-graph /path/to/canonical_graph_v2.json \\
        --output eval/cases/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "llm-rag" / "planner"))


def _load_graph(path: str) -> dict:
    """Load canonical graph."""
    with open(path, "r", encoding="utf-8") as f:
        graph = json.load(f)
    raw_nodes = graph.get("nodes", {})
    nodes = list(raw_nodes.values()) if isinstance(raw_nodes, dict) else raw_nodes
    by_type: dict[str, list[dict]] = {"object": [], "place": [], "room": [], "agent": []}
    for n in nodes:
        nt = n.get("node_type", "")
        if nt in by_type:
            by_type[nt].append(n)
    return {"nodes": nodes, "by_type": by_type}


def _pick(graph: dict, nt: str, n: int = 1) -> list[dict]:
    return random.sample(graph["by_type"].get(nt, []), min(n, len(graph["by_type"].get(nt, []))))


def _id(node: dict) -> str:
    return node.get("node_id", "0")


def _label(node: dict, fallback: str = "object") -> str:
    """Extract visible unique label. Fixes P0 #27: no more bare 'place' labels.

    Appends last 4 digits of node_id to ensure uniqueness across nodes
    with the same label (e.g., multiple 'chairs' nodes).
    """
    l = node.get("label", "")
    nid = str(node.get("node_id", "0"))
    short_id = nid[-4:]
    if l and l != "unknown":
        return f"{l} #{short_id}"
    return f"{fallback} #{short_id}"


def generate_planning_benchmark_v3(
    canonical_graph_path: str,
    output_dir: str,
    seed: int = 42,
) -> dict:
    random.seed(seed)
    graph = _load_graph(canonical_graph_path)
    out = Path(output_dir)

    generation_cases: list[dict] = []
    gate_cases: list[dict] = []

    # --- Generation cases (60) ---

    # 1. Single-step target (15)
    objects = _pick(graph, "object", 15)
    for i, obj in enumerate(objects):
        lbl = _label(obj, "object")
        generation_cases.append({
            "case_id": f"plan_v3_gen_{i+1:03d}",
            "instruction": f"Navigate to the {lbl}.",
            "oracle_grounding": {
                "action": "navigate",
                "target": {"node_id": _id(obj), "node_type": "object", "label": lbl},
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

    # 2. Source -> destination (10) — uses visible identifiers
    places = _pick(graph, "place", 20)
    for i in range(10):
        src, dst = places[i * 2], places[i * 2 + 1]
        src_label = _label(src, "place")
        dst_label = _label(dst, "place")
        generation_cases.append({
            "case_id": f"plan_v3_gen_{15+i+1:03d}",
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

    # 3. Escort/guide (10) — P0 #28: instruction matches action
    agents = _pick(graph, "agent", 10) if graph["by_type"]["agent"] else [{"node_id": "agent_0", "label": "person"}] * 10
    dests = _pick(graph, "place", 10)
    for i in range(10):
        agent = agents[i % len(agents)]
        dest = dests[i % len(dests)]
        agent_label = _label(agent, "person")
        dest_label = _label(dest, "place")

        if i % 2 == 0:
            # Escort cases — use escort verbs
            generation_cases.append({
                "case_id": f"plan_v3_gen_{25+i+1:03d}",
                "instruction": f"Escort the {agent_label} to {dest_label}.",
                "oracle_grounding": {
                    "action": "escort",
                    "actor": {"node_id": _id(agent), "node_type": "agent", "label": agent_label},
                    "destination": {"node_id": _id(dest), "node_type": "place", "label": dest_label},
                },
                "expected_plan": {
                    "status": "valid",
                    "required_actions": ["escort_to"],
                    "max_steps": 3,
                    "expected_steps": [{
                        "step": 1, "action": "escort_to",
                        "role_refs": {"actor": "actor", "destination": "destination"},
                        "target_id": _id(agent),
                    }],
                    "expected_target_ids": [_id(agent), _id(dest)],
                },
                "tags": ["escort", "escort_guide"],
                "category": "escort_guide",
            })
        else:
            # Guide cases — use guide verbs (P0 #28)
            generation_cases.append({
                "case_id": f"plan_v3_gen_{25+i+1:03d}",
                "instruction": f"Guide the {agent_label} to {dest_label}.",
                "oracle_grounding": {
                    "action": "guide",
                    "actor": {"node_id": _id(agent), "node_type": "agent", "label": agent_label},
                    "destination": {"node_id": _id(dest), "node_type": "place", "label": dest_label},
                },
                "expected_plan": {
                    "status": "valid",
                    "required_actions": ["guide_to"],
                    "max_steps": 3,
                    "expected_steps": [{
                        "step": 1, "action": "guide_to",
                        "role_refs": {"actor": "actor", "destination": "destination"},
                        "target_id": _id(agent),
                    }],
                    "expected_target_ids": [_id(agent), _id(dest)],
                },
                "tags": ["guide", "escort_guide"],
                "category": "escort_guide",
            })

    # 4. Avoid/prefer constraints (10)
    targets = _pick(graph, "object", 5)
    avoids = _pick(graph, "place", 5)
    for i in range(10):
        if i < 5:
            obj = targets[i]
            avoid_place = avoids[i]
            lbl = _label(obj, "object")
            avoid_label = _label(avoid_place, "place")
            generation_cases.append({
                "case_id": f"plan_v3_gen_{35+i+1:03d}",
                "instruction": f"Navigate to {lbl}, avoid {avoid_label}.",
                "oracle_grounding": {
                    "action": "navigate",
                    "target": {"node_id": _id(obj), "node_type": "object", "label": lbl},
                    "constraints": [{"type": "avoid", "node_id": _id(avoid_place), "node_type": "place", "label": avoid_label}],
                },
                "expected_plan": {
                    "status": "valid",
                    "required_actions": ["navigate_to"],
                    "max_steps": 3,
                    "expected_steps": [{"step": 1, "action": "navigate_to", "target_ref": "target", "target_id": _id(obj)}],
                    "expected_target_ids": [_id(obj)],
                    "expected_avoid_ids": [_id(avoid_place)],
                },
                "tags": ["navigation", "constraint"],
                "category": "avoid_prefer",
            })
        else:
            obj = targets[i - 5]
            lbl = _label(obj, "object")
            generation_cases.append({
                "case_id": f"plan_v3_gen_{35+i+1:03d}",
                "instruction": f"Navigate to {lbl}, prefer shortest route.",
                "oracle_grounding": {
                    "action": "navigate",
                    "target": {"node_id": _id(obj), "node_type": "object", "label": lbl},
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

    # 5. Multi-step (15) — P0 #33: ALL have real steps (count >= 2)
    ms_objects = _pick(graph, "object", 10)
    ms_places = _pick(graph, "place", 10)
    ms_agents = _pick(graph, "agent", 5) if graph["by_type"]["agent"] else [{"node_id": "agent_0", "label": "person"}] * 5

    for i in range(15):
        if i % 3 == 0:
            # locate + navigate (2 steps) — P0 #32: step-specific refs
            obj = ms_objects[i % len(ms_objects)]
            place = ms_places[i % len(ms_places)]
            lbl = _label(obj, "object")
            p_label = _label(place, "place")
            generation_cases.append({
                "case_id": f"plan_v3_gen_{45+i+1:03d}",
                "instruction": f"First find the {lbl}, then navigate to {p_label}.",
                "oracle_grounding": {
                    "action": "navigate",
                    "target": {"node_id": _id(obj), "node_type": "object", "label": lbl},
                    "destination": {"node_id": _id(place), "node_type": "place", "label": p_label},
                    "steps": [
                        {"references": {"target": {"node_id": _id(obj), "node_type": "object", "label": lbl}}},
                        {"references": {"destination": {"node_id": _id(place), "node_type": "place", "label": p_label}}},
                    ],
                },
                "expected_plan": {
                    "status": "valid",
                    "required_actions": ["locate_entity", "navigate_to"],
                    "max_steps": 5,
                    "expected_steps": [
                        {"step": 1, "action": "locate_entity", "target_ref": "step_0_target", "target_id": _id(obj)},
                        {"step": 2, "action": "navigate_to", "target_ref": "step_1_destination", "target_id": _id(place)},
                    ],
                    "expected_target_ids": [_id(obj), _id(place)],
                },
                "tags": ["multi_step", "step_specific_refs"],
                "category": "multi_step",
            })
        elif i % 3 == 1:
            # find + escort (2 steps) — P0 #32: step-specific refs + role_refs
            obj = ms_objects[i % len(ms_objects)]
            agent = ms_agents[i % len(ms_agents)]
            place = ms_places[i % len(ms_places)]
            lbl = _label(obj, "object")
            agent_label = _label(agent, "person")
            p_label = _label(place, "place")
            generation_cases.append({
                "case_id": f"plan_v3_gen_{45+i+1:03d}",
                "instruction": f"Find the {lbl}, then escort {agent_label} to {p_label}.",
                "oracle_grounding": {
                    "action": "escort",
                    "target": {"node_id": _id(obj), "node_type": "object", "label": lbl},
                    "actor": {"node_id": _id(agent), "node_type": "agent", "label": agent_label},
                    "destination": {"node_id": _id(place), "node_type": "place", "label": p_label},
                    "steps": [
                        {"references": {"target": {"node_id": _id(obj), "node_type": "object", "label": lbl}}},
                        {"references": {"actor": {"node_id": _id(agent), "node_type": "agent", "label": agent_label},
                                        "destination": {"node_id": _id(place), "node_type": "place", "label": p_label}}},
                    ],
                },
                "expected_plan": {
                    "status": "valid",
                    "required_actions": ["locate_entity", "escort_to"],
                    "max_steps": 5,
                    "expected_steps": [
                        {"step": 1, "action": "locate_entity", "target_ref": "step_0_target", "target_id": _id(obj)},
                        {"step": 2, "action": "escort_to", "role_refs": {"actor": "step_1_actor", "destination": "step_1_destination"},
                         "target_id": _id(agent)},
                    ],
                    "expected_target_ids": [_id(obj), _id(agent), _id(place)],
                },
                "tags": ["multi_step", "step_specific_refs", "role_refs"],
                "category": "multi_step",
            })
        else:
            # locate + navigate + avoid (3 steps)
            obj = ms_objects[i % len(ms_objects)]
            place = ms_places[i % len(ms_places)]
            avoid_place = ms_places[(i + 1) % len(ms_places)]
            lbl = _label(obj, "object")
            p_label = _label(place, "place")
            avoid_label = _label(avoid_place, "place")
            generation_cases.append({
                "case_id": f"plan_v3_gen_{45+i+1:03d}",
                "instruction": f"Find the {lbl}, then navigate to {p_label} avoiding {avoid_label}.",
                "oracle_grounding": {
                    "action": "navigate",
                    "target": {"node_id": _id(obj), "node_type": "object", "label": lbl},
                    "destination": {"node_id": _id(place), "node_type": "place", "label": p_label},
                    "constraints": [{"type": "avoid", "node_id": _id(avoid_place), "node_type": "place", "label": avoid_label}],
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
                    "expected_avoid_ids": [_id(avoid_place)],
                },
                "tags": ["multi_step"],
                "category": "multi_step",
            })

    # --- Status gate cases (20) — P0 #39: diverse labels ---

    # Ambiguous (10) — use different entity types (P0 #39)
    ambiguous_objects = _pick(graph, "object", 10)
    ambiguous_verbs = ["Find a", "Locate a", "Search for a", "Where is a", "I need a"]
    for i, obj in enumerate(ambiguous_objects):
        lbl = _label(obj, "object")
        verb = ambiguous_verbs[i % len(ambiguous_verbs)]
        gate_cases.append({
            "case_id": f"plan_v3_gate_amb_{i+1:03d}",
            "instruction": f"{verb} {lbl}.",
            "oracle_grounding": {"action": "locate", "pipeline_status": "ambiguous"},
            "expected_plan": {"status": "ambiguous", "required_actions": [], "max_steps": 0},
            "tags": ["ambiguous"],
            "category": "ambiguous",
        })

    # Not found (5) — use different object types
    not_found_labels = ["refrigerator", "swimming pool", "elevator button", "fire extinguisher", "vending machine"]
    for i, label in enumerate(not_found_labels):
        gate_cases.append({
            "case_id": f"plan_v3_gate_nf_{i+1:03d}",
            "instruction": f"Go to the {label}.",
            "oracle_grounding": {"action": "navigate", "pipeline_status": "not_found"},
            "expected_plan": {"status": "target_not_found", "required_actions": [], "max_steps": 0},
            "tags": ["not_found"],
            "category": "not_found",
        })

    # Unsupported (5) — P0 #40: oracle action = "unsupported"
    unsupported_labels = ["chair", "box", "tool", "device", "object"]
    for i, label in enumerate(unsupported_labels):
        gate_cases.append({
            "case_id": f"plan_v3_gate_uns_{i+1:03d}",
            "instruction": f"Pick up the {label}.",
            "oracle_grounding": {"action": "unsupported", "pipeline_status": "unsupported"},
            "expected_plan": {"status": "unsupported", "required_actions": [], "max_steps": 0},
            "tags": ["unsupported"],
            "category": "unsupported",
        })

    # Combine and sort
    all_cases = generation_cases + gate_cases
    all_cases.sort(key=lambda c: c["case_id"])

    # Validation
    gen_instructions = [c["instruction"].lower().strip() for c in generation_cases]
    gen_dupes = [k for k, v in {x: gen_instructions.count(x) for x in set(gen_instructions)}.items() if v > 1]

    multi_step_cases = [c for c in generation_cases if c["category"] == "multi_step"]
    multi_step_ok = all(
        len(c.get("expected_plan", {}).get("expected_steps", [])) >= 2
        for c in multi_step_cases
    )

    gate_instructions = [c["instruction"].lower().strip() for c in gate_cases]
    gate_unique = len(set(gate_instructions))

    checks = {
        "no_duplicate_gen_instructions": (len(gen_dupes) == 0, f"{len(gen_dupes)} dupes: {gen_dupes[:3]}"),
        "all_multi_step_real": (multi_step_ok, f"{len(multi_step_cases)} multi-step cases"),
        "gate_diversity": (gate_unique >= 15, f"{gate_unique} unique gate instructions"),
        "escort_guide_consistency": (
            all(
                ("escort" in c["instruction"].lower() and c["oracle_grounding"]["action"] == "escort")
                or ("guide" in c["instruction"].lower() and c["oracle_grounding"]["action"] == "guide")
                or c["category"] not in ("escort_guide",)
                for c in generation_cases
            ),
            "escort/guide instruction matches action"
        ),
    }

    all_passed = all(v[0] for v in checks.values())
    if not all_passed:
        print("VALIDATION FAILED:")
        for name, (passed, detail) in checks.items():
            if not passed:
                print(f"  FAIL {name}: {detail}")
    else:
        print("All validation checks PASSED")

    # Write generation benchmark
    gen_path = out / "planning_generation_v3.jsonl"
    out.mkdir(parents=True, exist_ok=True)
    with open(gen_path, "w", encoding="utf-8") as f:
        for c in generation_cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    # Write gate benchmark
    gate_path = out / "planning_status_gate_v3.jsonl"
    with open(gate_path, "w", encoding="utf-8") as f:
        for c in gate_cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    # Compute hashes and manifests
    for p, label in [(gen_path, "generation"), (gate_path, "gate")]:
        with open(p, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()[:16]
        cases_list = []
        with open(p) as f:
            for line in f:
                cases_list.append(json.loads(line))
        cat_dist: dict[str, int] = {}
        for c in cases_list:
            cat = c["category"]
            cat_dist[cat] = cat_dist.get(cat, 0) + 1
        manifest_path = p.with_suffix(".manifest.json")
        with open(manifest_path, "w") as f:
            json.dump({
                "benchmark_status": "development",
                "benchmark_version": "v3",
                "benchmark_file_hash": h,
                "case_count": len(cases_list),
                "categories": cat_dist,
                "seed": seed,
                "validation": {k: {"passed": v[0], "detail": v[1]} for k, v in checks.items()},
            }, f, indent=2)

    print(f"Generation: {len(generation_cases)} cases -> {gen_path}")
    print(f"Status gate: {len(gate_cases)} cases -> {gate_path}")

    for name, (passed, detail) in checks.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}: {detail}")

    return {"generation": len(generation_cases), "gate": len(gate_cases)}


def main():
    parser = argparse.ArgumentParser(description="Generate Planning Benchmark v3")
    parser.add_argument("--canonical-graph", default="/home/nenlian/Masterthesis-wang/RAG/khronos_3dsg_rag/outputs/khronos_office_new/canonical_graph_v2.json")
    parser.add_argument("--output", default="eval/cases")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    generate_planning_benchmark_v3(args.canonical_graph, args.output, args.seed)


if __name__ == "__main__":
    main()
