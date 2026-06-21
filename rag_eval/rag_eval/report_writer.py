from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from .schemas import EvalReport

logger = logging.getLogger(__name__)


def write_report(report: EvalReport, output_dir: str | Path) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    _write_summary_json(report, output_dir / "eval_summary.json")
    _write_summary_csv(report, output_dir / "eval_summary.csv")
    _write_summary_md(report, output_dir / "eval_summary.md")
    _write_per_case_results(report, output_dir / "per_case_results.jsonl")
    _write_failures(report, output_dir / "failures.jsonl")

    logger.info(f"Reports written to {output_dir}")


def _write_summary_json(report: EvalReport, path: Path) -> None:
    data = {
        "dataset": report.dataset,
        "total_cases": report.total_cases,
        "index_coverage": report.index_coverage,
        "total_gt_objects": report.total_gt_objects,
        "matched_gt_objects": report.matched_gt_objects,
        "overall_metrics": report.overall_metrics,
        "metrics_by_type": report.metrics_by_type,
        "graph_accuracy_by_type": report.graph_accuracy_by_type,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _write_summary_csv(report: EvalReport, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["query_type", "count", "hit", "mrr", "recall", "precision", "f1", "graph_accuracy"])
        for qt, m in report.metrics_by_type.items():
            writer.writerow([
                qt,
                m.get("count", 0),
                f"{m.get('hit', 0):.4f}",
                f"{m.get('mrr', 0):.4f}",
                f"{m.get('recall', 0):.4f}",
                f"{m.get('precision', 0):.4f}",
                f"{m.get('f1', 0):.4f}",
                f"{m.get('graph_accuracy', 0):.4f}",
            ])
        writer.writerow([])
        writer.writerow(["overall", report.total_cases] + [f"{v:.4f}" for v in report.overall_metrics.values()])


def _write_summary_md(report: EvalReport, path: Path) -> None:
    lines = [
        f"# RAG Evaluation Report — {report.dataset}",
        "",
        f"**Total cases**: {report.total_cases}",
        f"**Index coverage**: {report.index_coverage:.2%} ({report.matched_gt_objects}/{report.total_gt_objects})",
        "",
        "## Overall Metrics",
        "",
    ]
    for k, v in report.overall_metrics.items():
        lines.append(f"- **{k}**: {v:.4f}")

    lines.extend(["", "## Metrics by Query Type", "", "| Query Type | Count | Hit | MRR | Recall | Precision | F1 | Graph Acc |", "|-----------|-------|-----|-----|--------|-----------|-----|-----------|"])
    for qt, m in report.metrics_by_type.items():
        lines.append(f"| {qt} | {m.get('count', 0)} | {m.get('hit', 0):.4f} | {m.get('mrr', 0):.4f} | {m.get('recall', 0):.4f} | {m.get('precision', 0):.4f} | {m.get('f1', 0):.4f} | {m.get('graph_accuracy', 0):.4f} |")

    if report.failures:
        lines.extend(["", "## Failure Examples", ""])
        for f in report.failures[:10]:
            lines.append(f"- **{f.case_id}** (`{f.query_type}`): {f.failure_reason}")
            lines.append(f"  - Query: {f.query}")
            lines.append(f"  - Expected: {f.relevant_ids[:5]}...")
            lines.append(f"  - Retrieved: {f.retrieved_ids[:5]}...")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _write_per_case_results(report: EvalReport, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in report.case_results:
            f.write(json.dumps({
                "case_id": r.case_id,
                "query": r.query,
                "query_type": r.query_type,
                "retrieved_ids": r.retrieved_ids,
                "relevant_ids": r.relevant_ids,
                "hit": r.hit,
                "metrics": r.metrics,
                "graph_accuracy": r.graph_accuracy,
            }, ensure_ascii=False) + "\n")


def _write_failures(report: EvalReport, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in report.failures:
            f.write(json.dumps({
                "case_id": r.case_id,
                "query": r.query,
                "query_type": r.query_type,
                "retrieved_ids": r.retrieved_ids,
                "relevant_ids": r.relevant_ids,
                "failure_reason": r.failure_reason,
            }, ensure_ascii=False) + "\n")
