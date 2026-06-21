#!/usr/bin/env python3
"""Build evaluation cases from groundtruth data."""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rag_eval.groundtruth_loader import load_groundtruth_changes, load_groundtruth_objects
from rag_eval.query_generator import generate_all_cases


def main():
    parser = argparse.ArgumentParser(description="Build evaluation cases from groundtruth")
    parser.add_argument("--dataset", required=True, help="Dataset name (office, apartment)")
    parser.add_argument("--gt-dir", required=True, help="Groundtruth directory path")
    parser.add_argument("--output", required=True, help="Output JSONL file path")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    objects = load_groundtruth_objects(args.dataset, args.gt_dir)
    changes = load_groundtruth_changes(args.dataset, args.gt_dir)

    cases = generate_all_cases(objects, changes, seed=args.seed)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps({
                "case_id": case.case_id,
                "dataset": case.dataset,
                "query_type": case.query_type,
                "query": case.query,
                "expected": case.expected,
                "relevant_ids": case.relevant_ids,
                "difficulty": case.difficulty,
            }, ensure_ascii=False) + "\n")

    print(f"Generated {len(cases)} evaluation cases -> {output_path}")


if __name__ == "__main__":
    main()
