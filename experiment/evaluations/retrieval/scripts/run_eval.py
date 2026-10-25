#!/usr/bin/env python3
"""Run RAG evaluation with graph-based groundtruth."""

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from rag_eval.evaluator import Evaluator
from rag_eval.groundtruth_loader import load_graph_groundtruth_from_rag
from rag_eval.retrieval_adapter import RetrievalAdapter
from rag_eval.report_writer import write_report


def main():
    parser = argparse.ArgumentParser(description="Run RAG evaluation")
    parser.add_argument("--config", required=True, help="Config YAML file path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    dataset = config["dataset"]
    db_dir = config["db_dir"]
    rag_jsonl = config.get("rag_jsonl", "")
    top_k = config.get("top_k", 10)
    output_dir = config.get("output_dir", "data/reports")
    seed = config.get("seed", 42)

    if not rag_jsonl:
        rag_jsonl = str(Path(db_dir).parent / "rag_documents.jsonl")

    logger = logging.getLogger(__name__)
    logger.info(f"Loading graph groundtruth from {rag_jsonl}")
    graph_gt = load_graph_groundtruth_from_rag(dataset, rag_jsonl)

    adapter = RetrievalAdapter(db_dir)

    evaluator = Evaluator(adapter, graph_gt, top_k=top_k, seed=seed)
    report = evaluator.run(dataset)

    write_report(report, output_dir)

    print(f"\n=== Results for {dataset} ===")
    print(f"Total cases: {report.total_cases}")
    print(f"Index coverage: {report.index_coverage:.2%}")
    print(f"Failures: {len(report.failures)}")
    print(f"\nOverall metrics:")
    for k, v in report.overall_metrics.items():
        print(f"  {k}: {v:.4f}")


if __name__ == "__main__":
    main()
