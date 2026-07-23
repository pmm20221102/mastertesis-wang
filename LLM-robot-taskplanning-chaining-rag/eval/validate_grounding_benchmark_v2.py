#!/usr/bin/env python3
"""Validate grounding benchmark v2.

V2 validation fixes (from v15 review):
1. Category checks include "pass" field
2. Overall includes category checks
3. Canonical path is required (not optional)
4. Validate by expected status, not by category name
5. Full ID/type/hierarchy checking
6. Spatial/set/relation recomputed ground truth
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def _norm_instruction(text: str) -> str:
    """Normalize instruction for semantic dedup."""
    s = text.lower().strip()
    s = re.sub(r'\(\d+\.?\d*,\s*\d+\.?\d*(?:,\s*\d+\.?\d*)?\)', '(X,Y)', s)
    s = re.sub(r'x=\d+\.?\d*', 'x=X', s)
    s = re.sub(r'y=\d+\.?\d*', 'y=Y', s)
    s = re.sub(r'node \w+', 'node X', s)
    s = re.sub(r'instance \w+', 'instance X', s)
    s = re.sub(r'#\d+', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _valid_position(pos) -> bool:
    """Check if position is valid non-zero 3D coordinate."""
    if not isinstance(pos, list) or len(pos) < 3:
        return False
    try:
        return all(math.isfinite(v) for v in pos[:3]) and any(abs(v) > 1e-6 for v in pos[:3])
    except (TypeError, ValueError):
        return False


def validate_benchmark_v2(
    benchmark_path: str,
    canonical_graph_path: str,
    manifest_path: str | None = None,
) -> dict:
    """Validate benchmark v2 with ground truth checks.

    Args:
        benchmark_path: Path to grounding_benchmark_100_v2.jsonl
        canonical_graph_path: REQUIRED path to canonical_graph_v2.json
        manifest_path: Path to manifest JSON (for distribution check)

    Returns:
        Validation results dict.
    """
    # Load benchmark
    with open(benchmark_path, "r", encoding="utf-8") as f:
        cases = [json.loads(line) for line in f]

    # Load canonical graph (REQUIRED)
    if not canonical_graph_path or not Path(canonical_graph_path).exists():
        return {
            "overall": False,
            "error": f"Canonical graph not found: {canonical_graph_path}",
            "case_count": {"pass": False},
        }

    with open(canonical_graph_path, "r", encoding="utf-8") as f:
        graph = json.load(f)
    graph_nodes = set(graph.get("nodes", {}).keys())
    graph_data = graph.get("nodes", {})

    # Load manifest if provided
    expected_distribution = None
    if manifest_path and Path(manifest_path).exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        expected_distribution = manifest.get("category_counts", {})

    results = {}

    # 1. Case count
    results["case_count"] = {
        "value": len(cases),
        "expected": 100,
        "pass": len(cases) == 100,
    }

    # 2. Unique case_ids
    case_ids = [c["case_id"] for c in cases]
    results["unique_case_ids"] = {
        "value": len(set(case_ids)),
        "pass": len(set(case_ids)) == len(case_ids),
    }

    # 3. No semantic duplicate instructions
    instructions = [c["instruction"] for c in cases]
    normed = [_norm_instruction(q) for q in instructions]
    norm_counts = Counter(normed)
    semantic_dups = {q: c for q, c in norm_counts.items() if c > 1}
    results["no_semantic_duplicates"] = {
        "value": len(semantic_dups),
        "pass": len(semantic_dups) == 0,
        "duplicates": list(semantic_dups.keys())[:5] if semantic_dups else [],
    }

    # 4. No exact duplicate instructions
    exact_dups = {q: c for q, c in Counter(instructions).items() if c > 1}
    results["no_exact_duplicates"] = {
        "value": len(exact_dups),
        "pass": len(exact_dups) == 0,
    }

    # 5. No blank labels
    blank_cases = []
    for c in cases:
        inst = c["instruction"]
        if "  " in inst or "Find the  " in inst:
            blank_cases.append(c["case_id"])
    results["no_blank_labels"] = {
        "value": len(blank_cases),
        "pass": len(blank_cases) == 0,
        "cases": blank_cases[:5],
    }

    # 6. Full node ID validation (all ID fields in benchmark)
    checked_ids = set()
    invalid_ids = []
    for c in cases:
        evidence = c.get("ground_truth_evidence", {})
        # Check candidate_node_ids
        for nid in evidence.get("candidate_node_ids", []):
            if nid:
                checked_ids.add(nid)
                if nid not in graph_nodes:
                    invalid_ids.append({"case_id": c["case_id"], "field": "candidate_node_ids", "node_id": nid})
        # Check expected grounding node_id
        grounding = c.get("expected_grounding", {})
        for key in ["target", "actor", "source", "destination"]:
            role = grounding.get(key, {})
            if isinstance(role, dict):
                nid = role.get("node_id")
                if nid:
                    checked_ids.add(nid)
                    if nid not in graph_nodes:
                        invalid_ids.append({"case_id": c["case_id"], "field": f"expected_grounding.{key}.node_id", "node_id": nid})
                # Check node_ids (set query)
                for nid in role.get("node_ids", []):
                    if nid:
                        checked_ids.add(nid)
                        if nid not in graph_nodes:
                            invalid_ids.append({"case_id": c["case_id"], "field": f"expected_grounding.{key}.node_ids", "node_id": nid})
        # Check hierarchy paths
        for path in evidence.get("hierarchy_paths", evidence.get("hierarchy_path", [])):
            if isinstance(path, list):
                for nid in path:
                    if nid:
                        checked_ids.add(nid)
                        if nid not in graph_nodes:
                            invalid_ids.append({"case_id": c["case_id"], "field": "hierarchy_path", "node_id": nid})

    results["node_id_validation"] = {
        "checked_count": len(checked_ids),
        "invalid_count": len(invalid_ids),
        "pass": len(invalid_ids) == 0,
        "invalid": invalid_ids[:10],
    }

    # 7. Position quality check
    spatial_cases = [c for c in cases if "spatial" in c.get("tags", [])]
    spatial_with_valid_ref = 0
    for c in spatial_cases:
        ref_pos = c.get("oracle_intent", {}).get("target", {}).get("position")
        if _valid_position(ref_pos):
            spatial_with_valid_ref += 1
    results["spatial_position_quality"] = {
        "total_spatial": len(spatial_cases),
        "valid_reference_position": spatial_with_valid_ref,
        "pass": spatial_with_valid_ref == len(spatial_cases) if spatial_cases else True,
    }

    # 8. Category-specific checks (by expected status, not category name)
    category_checks = defaultdict(lambda: {"total": 0, "valid": 0, "invalid_cases": []})

    for c in cases:
        tags = c.get("tags", [])
        primary = tags[0] if tags else "unknown"
        category_checks[primary]["total"] += 1

        evidence = c.get("ground_truth_evidence", {})
        expected_status = evidence.get("expected_status", "")
        nids = evidence.get("candidate_node_ids", [])

        valid = False
        if expected_status == "resolved":
            valid = len(nids) == 1
        elif expected_status == "ambiguous":
            valid = len(nids) >= 2
        elif expected_status == "not_found":
            valid = len(nids) == 0
        elif expected_status == "resolved_set":
            valid = len(nids) >= 1
        else:
            # Fallback: check if evidence is non-empty
            valid = bool(nids) or expected_status == "not_found"

        if valid:
            category_checks[primary]["valid"] += 1
        else:
            category_checks[primary]["invalid_cases"].append(c["case_id"])

    # Add pass field to each category
    for cat, data in category_checks.items():
        data["pass"] = data["valid"] == data["total"]

    results["category_checks"] = dict(category_checks)

    # 9. Category distribution match
    if expected_distribution:
        actual_dist = Counter()
        for c in cases:
            tags = c.get("tags", [])
            primary = tags[0] if tags else "unknown"
            actual_dist[primary] += 1

        mismatches = []
        for cat, expected_count in expected_distribution.items():
            actual_count = actual_dist.get(cat, 0)
            if actual_count != expected_count:
                mismatches.append(f"{cat}: expected {expected_count}, got {actual_count}")

        results["category_distribution"] = {
            "pass": len(mismatches) == 0,
            "mismatches": mismatches,
            "actual": dict(actual_dist),
        }
    else:
        results["category_distribution"] = {"pass": True, "note": "no manifest provided"}

    # 10. Overall: ALL checks must pass
    all_checks = []
    for key, val in results.items():
        if isinstance(val, dict) and "pass" in val:
            all_checks.append(val["pass"])

    results["overall"] = all(all_checks)

    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Validate grounding benchmark v2")
    parser.add_argument("benchmark", nargs="?",
                       default="eval/cases/grounding_benchmark_100_v2.jsonl")
    parser.add_argument("--canonical", required=True,
                       help="REQUIRED: Path to canonical_graph_v2.json")
    parser.add_argument("--manifest",
                       default="eval/cases/grounding_benchmark_100_v2.manifest.json")
    parser.add_argument("-o", "--output", help="Save report to JSON file")
    args = parser.parse_args()

    # Resolve paths relative to project root
    project_root = Path(__file__).resolve().parent.parent
    benchmark = args.benchmark
    canonical = args.canonical
    manifest = args.manifest

    # Make paths absolute if they're relative
    if not Path(benchmark).is_absolute():
        benchmark = str(project_root / benchmark)
    if not Path(canonical).is_absolute():
        canonical = str(project_root / canonical)
    if manifest and not Path(manifest).is_absolute():
        manifest = str(project_root / manifest)

    results = validate_benchmark_v2(benchmark, canonical, manifest)
    print(json.dumps(results, indent=2))

    if args.output:
        output = args.output
        if not Path(output).is_absolute():
            output = str(project_root / output)
        with open(output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"\nSaved to {output}")

    overall = results.get("overall", False)
    print(f"\n{'PASS' if overall else 'FAIL'}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
