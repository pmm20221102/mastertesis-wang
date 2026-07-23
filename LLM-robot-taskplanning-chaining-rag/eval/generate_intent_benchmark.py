"""Generate Intent Benchmark from Grounding Benchmark.

Extracts instruction + oracle_intent from grounding_benchmark_100_v3.jsonl
to create a controlled intent evaluation benchmark.

Usage:
    python -m eval.generate_intent_benchmark \
        --grounding-benchmark eval/cases/grounding_benchmark_100_v3.jsonl \
        --output eval/cases/intent_benchmark_100_v1.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# Category mapping from grounding tags to intent categories
TAG_TO_CATEGORY = {
    "obj_nav": "object_navigation",
    "place_constraint": "place_constraint",
    "room_constraint": "room_constraint",
    "set_query": "set_query",
    "spatial": "spatial",
    "exact_id": "exact_id",
    "ambiguous": "ambiguous",
    "not_found": "not_found",
    "complex": "complex",
    "relation": "relation",
}


def _extract_required_roles(oracle_intent: dict) -> list[str]:
    """Extract required roles from oracle intent."""
    roles = []
    for role in ["actor", "target", "source", "destination"]:
        if oracle_intent.get(role) is not None:
            roles.append(role)
    return sorted(roles)


def _extract_category(tags: list[str]) -> str:
    """Map grounding tags to intent category."""
    for tag in tags:
        if tag in TAG_TO_CATEGORY:
            return TAG_TO_CATEGORY[tag]
    return "unknown"


def generate_intent_benchmark(
    grounding_benchmark_path: str | Path,
    output_path: str | Path,
) -> dict:
    """Generate intent benchmark from grounding benchmark.

    Returns:
        Manifest dict with hashes and counts.
    """
    grounding_path = Path(grounding_benchmark_path)
    output = Path(output_path)

    # Load grounding cases
    cases = []
    with open(grounding_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))

    # Generate intent cases
    intent_cases = []
    for i, case in enumerate(cases):
        oracle_intent = case.get("oracle_intent", {})
        if not oracle_intent:
            continue

        tags = case.get("tags", [])
        category = _extract_category(tags)
        required_roles = _extract_required_roles(oracle_intent)

        intent_case = {
            "case_id": f"intent_v1_{i+1:03d}",
            "source_case_id": case["case_id"],
            "instruction": case["instruction"],
            "language_style": "controlled",
            "category": category,
            "oracle_intent": oracle_intent,
            "required_roles": required_roles,
            "tags": tags,
        }
        intent_cases.append(intent_case)

    # Sort by case_id for stability
    intent_cases.sort(key=lambda c: c["case_id"])

    # Write benchmark file
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        for case in intent_cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    # Compute hashes (after writing, read back bytes)
    with open(output, "rb") as f:
        benchmark_bytes = f.read()
    benchmark_hash = hashlib.sha256(benchmark_bytes).hexdigest()[:16]

    with open(grounding_path, "rb") as f:
        source_bytes = f.read()
    source_hash = hashlib.sha256(source_bytes).hexdigest()[:16]

    # Write manifest
    manifest = {
        "benchmark_status": "ready",
        "benchmark_file": str(output.name),
        "benchmark_file_hash": benchmark_hash,
        "source_benchmark": str(grounding_path.name),
        "source_benchmark_hash": source_hash,
        "case_count": len(intent_cases),
        "categories": {},
        "language_style_distribution": {},
        "required_roles_distribution": {},
    }

    # Category counts
    for case in intent_cases:
        cat = case["category"]
        manifest["categories"][cat] = manifest["categories"].get(cat, 0) + 1

    # Language style counts
    for case in intent_cases:
        style = case["language_style"]
        manifest["language_style_distribution"][style] = (
            manifest["language_style_distribution"].get(style, 0) + 1
        )

    # Required roles distribution
    for case in intent_cases:
        roles_key = "+".join(case["required_roles"])
        manifest["required_roles_distribution"][roles_key] = (
            manifest["required_roles_distribution"].get(roles_key, 0) + 1
        )

    manifest_path = output.with_name(output.stem + ".manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"Generated {len(intent_cases)} intent cases")
    print(f"Benchmark: {output}")
    print(f"Manifest: {manifest_path}")
    print(f"Benchmark hash: {benchmark_hash}")
    print(f"Categories: {manifest['categories']}")

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate Intent Benchmark")
    parser.add_argument(
        "--grounding-benchmark",
        default="eval/cases/grounding_benchmark_100_v3.jsonl",
        help="Path to grounding benchmark JSONL",
    )
    parser.add_argument(
        "--output",
        default="eval/cases/intent_benchmark_100_v1.jsonl",
        help="Output path for intent benchmark JSONL",
    )
    args = parser.parse_args()

    generate_intent_benchmark(args.grounding_benchmark, args.output)


if __name__ == "__main__":
    main()
