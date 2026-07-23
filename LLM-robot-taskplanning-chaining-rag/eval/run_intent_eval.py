"""Intent Parser Evaluation Runner.

Evaluates LLM Intent Parser against oracle intents.
Does NOT run Grounding or Planning — pure intent parsing evaluation.

Usage:
    python -m eval.run_intent_eval \
        --benchmark eval/cases/intent_benchmark_100_v1.jsonl \
        --model qwen2.5:0.5b \
        --repeats 3 \
        --output eval/results/intent_qwen_05b_v1/
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from planner_schemas.intent import TaskIntent, EntityReference

logger = logging.getLogger(__name__)


# --- Normalization ---

def _normalize_text(text: str | None) -> str:
    """Normalize text for comparison: lowercase, strip, collapse spaces."""
    if text is None:
        return ""
    import re
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = text.replace("-", "_")
    return text


def _normalize_label(label: str | None) -> str:
    """Normalize canonical label."""
    return _normalize_text(label)


# --- Comparison helpers ---

def _compare_entity_ref(pred: dict | None, exp: dict | None) -> dict[str, Any]:
    """Compare predicted vs expected EntityReference."""
    if exp is None and pred is None:
        return {"match": True, "fields": {}}
    if exp is None:
        return {"match": False, "fields": {}, "spurious": True}
    if pred is None:
        return {"match": False, "fields": {}, "missing": True}

    fields = {}
    # label
    pred_label = _normalize_label(pred.get("label"))
    exp_label = _normalize_label(exp.get("label"))
    fields["label"] = pred_label == exp_label

    # node_type
    pred_type = _normalize_text(pred.get("node_type"))
    exp_type = _normalize_text(exp.get("node_type"))
    fields["node_type"] = pred_type == exp_type

    # room
    pred_room = _normalize_text(pred.get("room"))
    exp_room = _normalize_text(exp.get("room"))
    fields["room"] = pred_room == exp_room

    # text (soft match — not required to be identical)
    pred_text = _normalize_text(pred.get("text"))
    exp_text = _normalize_text(exp.get("text"))
    fields["text_match"] = pred_text == exp_text

    # quantifier
    pred_quant = pred.get("quantifier", "one")
    exp_quant = exp.get("quantifier", "one")
    fields["quantifier"] = pred_quant == exp_quant

    # locations
    pred_locs = pred.get("locations", [])
    exp_locs = exp.get("locations", [])
    pred_loc_types = sorted([_normalize_text(l.get("node_type")) for l in pred_locs])
    exp_loc_types = sorted([_normalize_text(l.get("node_type")) for l in exp_locs])
    fields["locations"] = pred_loc_types == exp_loc_types

    # relations
    pred_rels = pred.get("relations", [])
    exp_rels = exp.get("relations", [])
    pred_rel_set = {(r.get("relation"), _normalize_text(r.get("target_text"))) for r in pred_rels}
    exp_rel_set = {(r.get("relation"), _normalize_text(r.get("target_text"))) for r in exp_rels}
    fields["relations"] = pred_rel_set == exp_rel_set

    # aggregate
    pred_agg = pred.get("aggregate")
    exp_agg = exp.get("aggregate")
    if exp_agg is None:
        fields["aggregate"] = pred_agg is None
    elif pred_agg is None:
        fields["aggregate"] = False
    else:
        fields["aggregate"] = (
            pred_agg.get("operation") == exp_agg.get("operation")
            and pred_agg.get("target_labels") == exp_agg.get("target_labels")
        )

    overall = all(fields.values())
    return {"match": overall, "fields": fields}


def _compute_role_metrics(predicted: dict, expected: dict) -> dict[str, Any]:
    """Compute role presence precision/recall/F1."""
    roles = ["actor", "target", "source", "destination"]
    pred_present = {r for r in roles if predicted.get(r) is not None}
    exp_present = {r for r in roles if expected.get(r) is not None}

    tp = len(pred_present & exp_present)
    fp = len(pred_present - exp_present)
    fn = len(exp_present - pred_present)

    precision = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if fn == 0 else 0.0)
    recall = tp / (tp + fn) if (tp + fn) > 0 else (1.0 if fp == 0 else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "predicted_roles": sorted(pred_present),
        "expected_roles": sorted(exp_present),
        "spurious_roles": sorted(pred_present - exp_present),
        "missing_roles": sorted(exp_present - pred_present),
    }


def _compute_relation_f1(pred_rels: list, exp_rels: list) -> dict[str, Any]:
    """Compute relation extraction F1."""
    pred_set = {(r.get("relation"), _normalize_text(r.get("target_text"))) for r in pred_rels}
    exp_set = {(r.get("relation"), _normalize_text(r.get("target_text"))) for r in exp_rels}

    tp = len(pred_set & exp_set)
    fp = len(pred_set - exp_set)
    fn = len(exp_set - pred_set)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "exact_match": pred_set == exp_set,
    }


def _compute_location_f1(pred_locs: list, exp_locs: list) -> dict[str, Any]:
    """Compute location extraction F1."""
    pred_set = {(_normalize_text(l.get("text")), _normalize_text(l.get("node_type"))) for l in pred_locs}
    exp_set = {(_normalize_text(l.get("text")), _normalize_text(l.get("node_type"))) for l in exp_locs}

    tp = len(pred_set & exp_set)
    fp = len(pred_set - exp_set)
    fn = len(exp_set - pred_set)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "exact_match": pred_set == exp_set,
    }


# --- Failure classification ---

def _classify_failure(predicted: dict, expected: dict, schema_valid: bool) -> str:
    """Classify the primary failure category."""
    if not schema_valid:
        return "schema_error"

    # Action wrong
    if predicted.get("action") != expected.get("action"):
        return "wrong_action"

    # Missing role
    roles = ["actor", "target", "source", "destination"]
    for r in roles:
        if expected.get(r) is not None and predicted.get(r) is None:
            return "missing_role"

    # Spurious role
    for r in roles:
        if predicted.get(r) is not None and expected.get(r) is None:
            return "spurious_role"

    # Check target fields
    pred_target = predicted.get("target") or {}
    exp_target = expected.get("target") or {}

    if exp_target.get("label") and _normalize_label(pred_target.get("label")) != _normalize_label(exp_target.get("label")):
        return "wrong_label"

    if exp_target.get("node_type") and _normalize_text(pred_target.get("node_type")) != _normalize_text(exp_target.get("node_type")):
        return "wrong_node_type"

    if exp_target.get("locations") and not pred_target.get("locations"):
        return "location_error"

    if exp_target.get("relations"):
        pred_rels = pred_target.get("relations", [])
        exp_rels = exp_target.get("relations", [])
        if len(pred_rels) != len(exp_rels) or set((r.get("relation"), r.get("target_text")) for r in pred_rels) != set((r.get("relation"), r.get("target_text")) for r in exp_rels):
            return "relation_error"

    if exp_target.get("quantifier") and pred_target.get("quantifier") != exp_target.get("quantifier"):
        return "quantifier_error"

    if exp_target.get("aggregate"):
        pred_agg = pred_target.get("aggregate")
        if not pred_agg or pred_agg.get("operation") != exp_target["aggregate"].get("operation"):
            return "aggregate_error"

    # Multi-step
    if expected.get("steps"):
        if not predicted.get("steps"):
            return "step_decomposition_error"
        if len(predicted.get("steps", [])) != len(expected.get("steps", [])):
            return "step_decomposition_error"

    return "unknown_error"


# --- Single case evaluation ---

def evaluate_intent_case(
    case: dict[str, Any],
    model: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate a single intent case."""
    from llm.intent_parser import parse_intent_with_llm, intent_to_dict

    instruction = case["instruction"]
    oracle = case["oracle_intent"]

    start_time = time.time()

    try:
        predicted_intent = parse_intent_with_llm(instruction, model=model, config=config)
        predicted = intent_to_dict(predicted_intent)
        schema_valid = True
        raw_output = json.dumps(predicted, ensure_ascii=False)
    except Exception as e:
        predicted = {}
        schema_valid = False
        raw_output = str(e)

    elapsed_ms = (time.time() - start_time) * 1000

    # Action accuracy
    action_correct = predicted.get("action") == oracle.get("action")

    # Role metrics
    role_metrics = _compute_role_metrics(predicted, oracle)

    # Per-role field comparison
    role_field_metrics = {}
    for role in ["actor", "target", "source", "destination"]:
        pred_ref = predicted.get(role)
        exp_ref = oracle.get(role)
        if exp_ref is not None or pred_ref is not None:
            role_field_metrics[role] = _compare_entity_ref(pred_ref, exp_ref)

    # Location F1
    pred_locs = (predicted.get("target") or {}).get("locations", [])
    exp_locs = (oracle.get("target") or {}).get("locations", [])
    location_metrics = _compute_location_f1(pred_locs, exp_locs) if exp_locs else None

    # Relation F1
    pred_rels = (predicted.get("target") or {}).get("relations", [])
    exp_rels = (oracle.get("target") or {}).get("relations", [])
    relation_metrics = _compute_relation_f1(pred_rels, exp_rels) if exp_rels else None

    # Quantifier accuracy
    pred_quant = (predicted.get("target") or {}).get("quantifier", "one")
    exp_quant = (oracle.get("target") or {}).get("quantifier", "one")
    quantifier_correct = pred_quant == exp_quant

    # Aggregate accuracy
    pred_agg = (predicted.get("target") or {}).get("aggregate")
    exp_agg = (oracle.get("target") or {}).get("aggregate")
    aggregate_correct = False
    if exp_agg is None:
        aggregate_correct = pred_agg is None
    elif pred_agg is not None:
        aggregate_correct = (
            pred_agg.get("operation") == exp_agg.get("operation")
            and pred_agg.get("target_labels") == exp_agg.get("target_labels")
        )

    # Multi-step
    pred_steps = predicted.get("steps")
    exp_steps = oracle.get("steps")
    step_count_correct = False
    step_actions_correct = False
    multi_step_exact = False
    if exp_steps is None:
        step_count_correct = pred_steps is None
        multi_step_exact = pred_steps is None
    elif pred_steps is not None:
        step_count_correct = len(pred_steps) == len(exp_steps)
        pred_step_actions = [s.get("action") for s in pred_steps]
        exp_step_actions = [s.get("action") for s in exp_steps]
        step_actions_correct = pred_step_actions == exp_step_actions
        multi_step_exact = step_count_correct and step_actions_correct

    # Strict exact match
    strict_exact = (
        action_correct
        and role_metrics["f1"] == 1.0
        and (location_metrics is None or location_metrics["exact_match"])
        and (relation_metrics is None or relation_metrics["exact_match"])
        and quantifier_correct
        and aggregate_correct
        and multi_step_exact
    )

    # Failure classification
    failure_category = None
    if not strict_exact:
        failure_category = _classify_failure(predicted, oracle, schema_valid)

    return {
        "case_id": case["case_id"],
        "source_case_id": case.get("source_case_id"),
        "instruction": instruction,
        "category": case.get("category", "unknown"),
        "language_style": case.get("language_style", "controlled"),
        # Schema validity
        "schema_valid": schema_valid,
        # Action
        "action_correct": action_correct,
        "predicted_action": predicted.get("action"),
        "expected_action": oracle.get("action"),
        # Role metrics
        "role_metrics": role_metrics,
        # Per-role field comparison
        "role_field_metrics": role_field_metrics,
        # Location
        "location_metrics": location_metrics,
        # Relation
        "relation_metrics": relation_metrics,
        # Quantifier
        "quantifier_correct": quantifier_correct,
        # Aggregate
        "aggregate_correct": aggregate_correct,
        # Multi-step
        "step_count_correct": step_count_correct,
        "step_actions_correct": step_actions_correct,
        "multi_step_exact": multi_step_exact,
        # Strict
        "strict_exact_match": strict_exact,
        # Failure
        "failure_category": failure_category,
        # Raw
        "raw_output": raw_output,
        "parsed_intent": predicted,
        "oracle_intent": oracle,
        "latency_ms": elapsed_ms,
        "error": None if schema_valid else raw_output,
    }


# --- Summary ---

def compute_intent_summary(results: list[dict], cases: list[dict]) -> dict[str, Any]:
    """Compute summary metrics from intent evaluation results."""
    total = len(results)
    if total == 0:
        return {}

    # Schema validity
    schema_valid_count = sum(1 for r in results if r["schema_valid"])

    # Action accuracy
    action_correct = sum(1 for r in results if r["action_correct"])

    # Role metrics (macro average)
    role_precisions = [r["role_metrics"]["precision"] for r in results]
    role_recalls = [r["role_metrics"]["recall"] for r in results]
    role_f1s = [r["role_metrics"]["f1"] for r in results]

    # Spurious roles
    total_spurious = sum(len(r["role_metrics"]["spurious_roles"]) for r in results)
    total_pred_roles = sum(len(r["role_metrics"]["predicted_roles"]) for r in results)

    # Location metrics
    location_cases = [r for r in results if r["location_metrics"] is not None]
    loc_f1 = sum(r["location_metrics"]["f1"] for r in location_cases) / len(location_cases) if location_cases else None

    # Relation metrics
    relation_cases = [r for r in results if r["relation_metrics"] is not None]
    rel_f1 = sum(r["relation_metrics"]["f1"] for r in relation_cases) / len(relation_cases) if relation_cases else None

    # Quantifier
    quant_correct = sum(1 for r in results if r["quantifier_correct"])

    # Aggregate
    agg_correct = sum(1 for r in results if r["aggregate_correct"])

    # Multi-step
    multi_step_cases = [r for r in results if r.get("oracle_intent", {}).get("steps") is not None]
    multi_step_exact = sum(1 for r in multi_step_cases if r["multi_step_exact"])

    # Strict exact match
    strict_exact = sum(1 for r in results if r["strict_exact_match"])

    # Failure breakdown
    failure_breakdown = {}
    for r in results:
        if r["failure_category"]:
            cat = r["failure_category"]
            failure_breakdown[cat] = failure_breakdown.get(cat, 0) + 1

    # Per-category breakdown
    case_categories = {c["case_id"]: c.get("category", "unknown") for c in cases}
    from collections import defaultdict
    cat_metrics = defaultdict(lambda: {"total": 0, "action_correct": 0, "strict_exact": 0})
    for r in results:
        cat = case_categories.get(r["case_id"], r.get("category", "unknown"))
        cat_metrics[cat]["total"] += 1
        if r["action_correct"]:
            cat_metrics[cat]["action_correct"] += 1
        if r["strict_exact_match"]:
            cat_metrics[cat]["strict_exact"] += 1

    category_breakdown = {}
    for cat, m in sorted(cat_metrics.items()):
        category_breakdown[cat] = {
            "total": m["total"],
            "action_accuracy": m["action_correct"] / m["total"],
            "strict_exact_rate": m["strict_exact"] / m["total"],
        }

    # Latency
    avg_latency = sum(r["latency_ms"] for r in results) / total

    return {
        "total_cases": total,
        "schema_validity_rate": schema_valid_count / total,
        "action_accuracy": action_correct / total,
        "role_presence_precision": sum(role_precisions) / len(role_precisions),
        "role_presence_recall": sum(role_recalls) / len(role_recalls),
        "role_presence_f1": sum(role_f1s) / len(role_f1s),
        "spurious_role_rate": total_spurious / total_pred_roles if total_pred_roles > 0 else 0,
        "location_f1": loc_f1,
        "relation_f1": rel_f1,
        "quantifier_accuracy": quant_correct / total,
        "aggregate_accuracy": agg_correct / total,
        "multi_step_exact_rate": multi_step_exact / len(multi_step_cases) if multi_step_cases else None,
        "multi_step_case_count": len(multi_step_cases),
        "strict_exact_match_rate": strict_exact / total,
        "strict_exact_match_count": strict_exact,
        "failure_breakdown": failure_breakdown,
        "category_breakdown": category_breakdown,
        "avg_latency_ms": avg_latency,
    }


def write_intent_summary_md(summary: dict, path: Path) -> None:
    """Write intent evaluation summary as markdown."""
    lines = [
        "# Intent Parser Evaluation Report",
        "",
        f"**Model**: {summary.get('model', 'unknown')}",
        f"**Repeats**: {summary.get('repeats', 1)}",
        f"**Temperature**: {summary.get('temperature', 0)}",
        "",
        "## Overall Metrics",
        "",
        f"- **Total cases**: {summary['total_cases']}",
        f"- **Schema validity**: {summary['schema_validity_rate']:.4f}",
        f"- **Action accuracy**: {summary['action_accuracy']:.4f}",
        f"- **Role presence P/R/F1**: {summary['role_presence_precision']:.4f} / {summary['role_presence_recall']:.4f} / {summary['role_presence_f1']:.4f}",
        f"- **Spurious role rate**: {summary['spurious_role_rate']:.4f}",
        f"- **Quantifier accuracy**: {summary['quantifier_accuracy']:.4f}",
        f"- **Aggregate accuracy**: {summary['aggregate_accuracy']:.4f}",
        f"- **Strict exact match**: {summary['strict_exact_match_rate']:.4f} ({summary['strict_exact_match_count']}/{summary['total_cases']})",
        f"- **Avg latency**: {summary['avg_latency_ms']:.1f}ms",
        "",
    ]

    if summary.get("location_f1") is not None:
        lines.append(f"- **Location F1**: {summary['location_f1']:.4f}")
    if summary.get("relation_f1") is not None:
        lines.append(f"- **Relation F1**: {summary['relation_f1']:.4f}")
    if summary.get("multi_step_exact_rate") is not None:
        lines.append(f"- **Multi-step exact**: {summary['multi_step_exact_rate']:.4f} (n={summary['multi_step_case_count']})")

    lines.extend(["", "## Failure Breakdown", ""])
    for cat, count in sorted(summary.get("failure_breakdown", {}).items()):
        lines.append(f"- **{cat}**: {count}")

    lines.extend(["", "## Category Breakdown", ""])
    for cat, m in sorted(summary.get("category_breakdown", {}).items()):
        lines.append(f"- **{cat}**: action={m['action_accuracy']:.4f}, strict={m['strict_exact_rate']:.4f} (n={m['total']})")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# --- Main runner ---

def run_intent_evaluation(
    benchmark_path: str,
    model: str,
    repeats: int = 1,
    temperature: float = 0,
    output_dir: str | None = None,
    category_filter: str | None = None,
) -> dict[str, Any]:
    """Run intent evaluation."""
    from main_rag import load_config, CONFIG_PATH

    config = load_config(CONFIG_PATH)
    # Override temperature
    config["options"] = config.get("options", {})
    config["options"]["temperature"] = temperature

    # Load benchmark
    cases = []
    with open(benchmark_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                case = json.loads(line)
                if category_filter and case.get("category") != category_filter:
                    continue
                cases.append(case)

    logger.info(f"Loaded {len(cases)} intent cases (filter: {category_filter or 'all'})")

    all_results = []
    for repeat in range(repeats):
        logger.info(f"Repeat {repeat + 1}/{repeats}")
        for i, case in enumerate(cases):
            result = evaluate_intent_case(case, model, config)
            result["repeat"] = repeat
            all_results.append(result)
            status = "OK" if result["strict_exact_match"] else f"FAIL({result['failure_category']})"
            logger.info(f"  [{repeat}] {case['case_id']}: {status} ({result['latency_ms']:.0f}ms)")

    # Compute summary
    summary = compute_intent_summary(all_results, cases)
    summary["model"] = model
    summary["repeats"] = repeats
    summary["temperature"] = temperature

    # Save results
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Summary
        with open(out / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        # Per-case results (without large fields)
        with open(out / "per_case_results.jsonl", "w", encoding="utf-8") as f:
            for r in all_results:
                r_copy = {k: v for k, v in r.items() if k not in ("parsed_intent", "oracle_intent", "raw_output")}
                f.write(json.dumps(r_copy, ensure_ascii=False) + "\n")

        # Predictions with full data
        with open(out / "predictions.jsonl", "w", encoding="utf-8") as f:
            for r in all_results:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

        # Raw outputs
        with open(out / "raw_outputs.jsonl", "w", encoding="utf-8") as f:
            for r in all_results:
                f.write(json.dumps({
                    "case_id": r["case_id"],
                    "repeat": r["repeat"],
                    "raw_output": r["raw_output"],
                    "schema_valid": r["schema_valid"],
                }, ensure_ascii=False) + "\n")

        # Failures
        failures = [r for r in all_results if not r["strict_exact_match"]]
        with open(out / "failures.jsonl", "w", encoding="utf-8") as f:
            for r in failures:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

        # Error breakdown
        with open(out / "error_breakdown.json", "w", encoding="utf-8") as f:
            json.dump(summary.get("failure_breakdown", {}), f, indent=2)

        # Run manifest
        import hashlib
        with open(benchmark_path, "rb") as f:
            bench_hash = hashlib.sha256(f.read()).hexdigest()[:16]

        manifest = {
            "model": model,
            "repeats": repeats,
            "temperature": temperature,
            "benchmark": benchmark_path,
            "benchmark_hash": bench_hash,
            "total_cases": len(cases),
            "total_evaluations": len(all_results),
            "output_dir": str(out),
        }
        with open(out / "run_manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # Summary markdown
        write_intent_summary_md(summary, out / "report.md")

        logger.info(f"Results saved to {out}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Intent Parser Evaluation Runner")
    parser.add_argument("--benchmark", required=True, help="Path to intent benchmark JSONL")
    parser.add_argument("--model", default="qwen2.5:0.5b", help="LLM model name")
    parser.add_argument("--repeats", type=int, default=1, help="Number of repeats")
    parser.add_argument("--temperature", type=float, default=0, help="Temperature")
    parser.add_argument("--output", help="Output directory")
    parser.add_argument("--category", help="Filter to specific category")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    summary = run_intent_evaluation(
        benchmark_path=args.benchmark,
        model=args.model,
        repeats=args.repeats,
        temperature=args.temperature,
        output_dir=args.output,
        category_filter=args.category,
    )

    print("\n=== Intent Evaluation Summary ===")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
