"""Generate Planning Benchmark from Grounding Benchmark.

Creates oracle-grounded planning cases for isolated Planner evaluation.
Input: oracle grounded task (no LLM Intent Parser involved).

Usage:
    python -m eval.generate_planning_benchmark \
        --grounding-benchmark eval/cases/grounding_benchmark_100_v3.jsonl \
        --output eval/cases/planning_benchmark_50_v1.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "llm-rag" / "planner"))


# Action mapping: grounding action → expected planner actions
ACTION_MAP = {
    "navigate": ["navigate_to"],
    "locate": ["locate_entity"],
    "escort": ["escort_to"],
    "guide": ["guide_to"],
    "avoid": ["avoid_area"],
    "wait": ["wait"],
    "ask_clarification": ["ask_clarification"],
    "unsupported": [],
}

# Category → expected plan structure
CATEGORY_PLAN_TEMPLATE = {
    "obj_nav": {
        "status": "valid",
        "actions": ["navigate_to"],
        "max_steps": 3,
    },
    "place_constraint": {
        "status": "valid",
        "actions": ["navigate_to"],
        "max_steps": 3,
    },
    "room_constraint": {
        "status": "valid",
        "actions": ["navigate_to"],
        "max_steps": 3,
    },
    "set_query": {
        "status": "valid",
        "actions": ["locate_entity"],
        "max_steps": 2,
    },
    "spatial": {
        "status": "valid",
        "actions": ["navigate_to"],
        "max_steps": 3,
    },
    "exact_id": {
        "status": "valid",
        "actions": ["navigate_to"],
        "max_steps": 3,
    },
    "complex": {
        "status": "valid",
        "actions": ["navigate_to"],
        "max_steps": 4,
    },
    "relation": {
        "status": "valid",
        "actions": ["navigate_to"],
        "max_steps": 3,
    },
    "ambiguous": {
        "status": "ambiguous",
        "actions": [],
        "max_steps": 0,
    },
    "not_found": {
        "status": "target_not_found",
        "actions": [],
        "max_steps": 0,
    },
}


def _build_oracle_grounding(case: dict) -> dict:
    """Build oracle grounding from grounding benchmark case."""
    expected = case.get("expected_grounding", {})
    oracle_intent = case.get("oracle_intent", {})

    grounding = {
        "action": oracle_intent.get("action", "navigate"),
        "constraints": [],
    }

    # For ambiguous/not_found cases, use pipeline_status directly
    pipeline_status = expected.get("pipeline_status")
    if pipeline_status in ("ambiguous", "not_found"):
        grounding["pipeline_status"] = pipeline_status
        return grounding

    # Copy resolved roles
    for role in ["actor", "target", "source", "destination"]:
        role_data = expected.get(role)
        if role_data and role_data.get("status") == "resolved":
            grounding[role] = {
                "node_id": role_data["node_id"],
                "node_type": role_data.get("node_type", "unknown"),
                "label": role_data.get("label", ""),
            }
            # Add room_id/place_id if available
            if role_data.get("room_id"):
                grounding[role]["room_id"] = role_data["room_id"]
            if role_data.get("place_id"):
                grounding[role]["place_id"] = role_data["place_id"]

    # Copy constraints
    if "constraints" in expected:
        for constr in expected["constraints"]:
            if constr.get("node_id"):
                grounding["constraints"].append({
                    "type": constr.get("type", "avoid"),
                    "node_id": constr["node_id"],
                    "node_type": constr.get("node_type", "unknown"),
                    "label": constr.get("label", ""),
                })

    return grounding


def _build_expected_plan(case: dict, oracle_grounding: dict) -> dict:
    """Build expected plan from case category and oracle grounding."""
    tags = case.get("tags", [])
    primary_tag = tags[0] if tags else "unknown"

    # Check pipeline_status from grounding benchmark first
    expected_grounding = case.get("expected_grounding", {})
    pipeline_status = expected_grounding.get("pipeline_status")

    # If pipeline_status is ambiguous/not_found, use that directly
    if pipeline_status == "ambiguous":
        return {"status": "ambiguous", "required_actions": [], "max_steps": 0}
    if pipeline_status == "not_found":
        return {"status": "target_not_found", "required_actions": [], "max_steps": 0}

    # If oracle_grounding has pipeline_status, use that
    if oracle_grounding.get("pipeline_status") == "ambiguous":
        return {"status": "ambiguous", "required_actions": [], "max_steps": 0}
    if oracle_grounding.get("pipeline_status") == "not_found":
        return {"status": "target_not_found", "required_actions": [], "max_steps": 0}

    template = CATEGORY_PLAN_TEMPLATE.get(primary_tag, {
        "status": "valid",
        "actions": ["navigate_to"],
        "max_steps": 3,
    })

    # Use oracle action to determine expected planner actions
    oracle_action = oracle_grounding.get("action", "navigate")
    expected_actions = ACTION_MAP.get(oracle_action, ["navigate_to"])

    expected = {
        "status": template["status"],
        "required_actions": expected_actions,
        "max_steps": template["max_steps"],
    }

    # Build expected steps with target_ref and target_id
    if template["status"] == "valid":
        expected_steps = []
        step_num = 1

        for action in expected_actions:
            step = {
                "step": step_num,
                "action": action,
                "target_ref": _infer_target_ref(action, oracle_grounding),
            }
            # Add target_id if the referenced role has one
            target_ref = step["target_ref"]
            if target_ref in oracle_grounding and oracle_grounding[target_ref].get("node_id"):
                step["target_id"] = oracle_grounding[target_ref]["node_id"]

            expected_steps.append(step)
            step_num += 1

        expected["expected_steps"] = expected_steps

        # Collect expected target IDs
        expected_target_ids = []
        for step in expected_steps:
            if step.get("target_id"):
                expected_target_ids.append(step["target_id"])
        if expected_target_ids:
            expected["expected_target_ids"] = list(set(expected_target_ids))

    return expected


def _infer_target_ref(action: str, grounding: dict) -> str:
    """Infer the target_ref for a planner action based on grounded task."""
    if action == "locate_entity":
        return "target"
    elif action == "navigate_to":
        if grounding.get("destination"):
            return "destination"
        return "target"
    elif action in ("escort_to", "guide_to"):
        return "actor"
    elif action == "avoid_area":
        return "constraint_avoid_0"
    elif action == "return_to_base":
        return ""
    return "target"


def generate_planning_benchmark(
    grounding_benchmark_path: str | Path,
    output_path: str | Path,
    seed: int = 42,
    target_count: int = 50,
) -> dict:
    """Generate planning benchmark from grounding benchmark."""
    random.seed(seed)

    grounding_path = Path(grounding_benchmark_path)
    output = Path(output_path)

    # Load grounding cases
    cases = []
    with open(grounding_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))

    # Select cases: prioritize diversity across categories
    category_cases = {}
    for case in cases:
        tags = case.get("tags", [])
        cat = tags[0] if tags else "unknown"
        if cat not in category_cases:
            category_cases[cat] = []
        category_cases[cat].append(case)

    selected = []
    # First pass: one from each category
    for cat, cat_cases in sorted(category_cases.items()):
        if len(selected) >= target_count:
            break
        selected.append(random.choice(cat_cases))

    # Second pass: fill remaining with random selection
    remaining = [c for c in cases if c not in selected]
    random.shuffle(remaining)
    for case in remaining:
        if len(selected) >= target_count:
            break
        selected.append(case)

    # Build planning cases
    planning_cases = []
    for i, case in enumerate(selected):
        oracle_grounding = _build_oracle_grounding(case)

        # Skip cases with no resolved references
        has_resolved = any(
            oracle_grounding.get(role, {}).get("node_id")
            for role in ["actor", "target", "source", "destination"]
        )
        if not has_resolved and case.get("expected_grounding", {}).get("pipeline_status") not in ("ambiguous", "not_found"):
            continue

        expected_plan = _build_expected_plan(case, oracle_grounding)

        tags = case.get("tags", [])
        planning_case = {
            "case_id": f"plan_v1_{i+1:03d}",
            "source_case_id": case["case_id"],
            "instruction": case["instruction"],
            "category": tags[0] if tags else "unknown",
            "oracle_grounding": oracle_grounding,
            "expected_plan": expected_plan,
            "tags": tags,
        }
        planning_cases.append(planning_case)

    # Trim to target count
    planning_cases = planning_cases[:target_count]

    # Sort by case_id
    planning_cases.sort(key=lambda c: c["case_id"])

    # Write
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        for case in planning_cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    # Compute hashes
    with open(output, "rb") as f:
        bench_hash = hashlib.sha256(f.read()).hexdigest()[:16]
    with open(grounding_path, "rb") as f:
        source_hash = hashlib.sha256(f.read()).hexdigest()[:16]

    # Manifest
    cat_dist = {}
    status_dist = {}
    for c in planning_cases:
        cat = c["category"]
        cat_dist[cat] = cat_dist.get(cat, 0) + 1
        status = c["expected_plan"]["status"]
        status_dist[status] = status_dist.get(status, 0) + 1

    manifest = {
        "benchmark_status": "ready",
        "benchmark_file": str(output.name),
        "benchmark_file_hash": bench_hash,
        "source_benchmark": str(grounding_path.name),
        "source_benchmark_hash": source_hash,
        "case_count": len(planning_cases),
        "categories": cat_dist,
        "status_distribution": status_dist,
        "seed": seed,
    }

    manifest_path = output.with_name(output.stem + ".manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"Generated {len(planning_cases)} planning cases")
    print(f"Benchmark: {output}")
    print(f"Manifest: {manifest_path}")
    print(f"Categories: {cat_dist}")
    print(f"Status: {status_dist}")

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate Planning Benchmark")
    parser.add_argument(
        "--grounding-benchmark",
        default="eval/cases/grounding_benchmark_100_v3.jsonl",
    )
    parser.add_argument(
        "--output",
        default="eval/cases/planning_benchmark_50_v1.jsonl",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--count", type=int, default=50)
    args = parser.parse_args()

    generate_planning_benchmark(
        args.grounding_benchmark, args.output, args.seed, args.count
    )


if __name__ == "__main__":
    main()
