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
        # Parse with eval_mode to get raw + canonicalized
        result = parse_intent_with_llm(instruction, model=model, config=config, eval_mode=True)

        if isinstance(result, dict):
            raw_json = result["raw_lmm_output"]
            predicted = intent_to_dict(result["canonicalized_intent"])
            thinking_text = result.get("thinking_text")
        else:
            # Fallback for fast path (no LLM call)
            raw_json = intent_to_dict(result)
            predicted = raw_json
            thinking_text = None

        schema_valid = True

        # Detect which rules were applied
        rules_applied = []
        if raw_json and raw_json.get("action") != predicted.get("action"):
            rules_applied.append("action_override")
        if raw_json and raw_json.get("target", {}).get("quantifier") != predicted.get("target", {}).get("quantifier"):
            rules_applied.append("quantifier_fix")
        # Check for removed spurious roles
        for role in ["source", "destination", "actor"]:
            if raw_json and raw_json.get(role) and not predicted.get(role):
                rules_applied.append(f"removed_spurious_{role}")

    except Exception as e:
        predicted = {}
        raw_json = None
        thinking_text = None
        rules_applied = []
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

    # Strict exact match — includes all role fields (label, node_type, room)
    all_role_fields_correct = all(
        rfm.get("match", True)
        for rfm in role_field_metrics.values()
    )

    strict_exact = (
        schema_valid
        and action_correct
        and role_metrics["f1"] == 1.0
        and all_role_fields_correct
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
        # Raw vs canonicalized trace
        "raw_json": raw_json,
        "thinking_text": thinking_text[:500] if thinking_text else None,
        "canonicalized_intent": predicted,
        "rules_applied": rules_applied,
        "rules_applied_count": len(rules_applied),
        "raw_output": json.dumps(raw_json, ensure_ascii=False) if raw_json else None,
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

    # Quantifier — separate default-one vs non-default
    quant_correct = sum(1 for r in results if r["quantifier_correct"])
    # Non-default quantifier cases (oracle has all/nearest/any)
    non_default_quant_cases = [r for r in results if (r.get("oracle_intent", {}).get("target") or {}).get("quantifier", "one") != "one"]
    non_default_quant_correct = sum(1 for r in non_default_quant_cases if r["quantifier_correct"])
    # Spurious quantifier: predicted non-default but oracle is "one"
    spurious_quant = sum(1 for r in results if r.get("quantifier_correct") is False
                         and (r.get("oracle_intent", {}).get("target") or {}).get("quantifier", "one") == "one"
                         and (r.get("parsed_intent", {}).get("target") or {}).get("quantifier", "one") != "one")

    # Aggregate — positive-case accuracy only
    oracle_has_agg = [r for r in results if (r.get("oracle_intent", {}).get("target") or {}).get("aggregate") is not None]
    agg_positive_correct = sum(1 for r in oracle_has_agg if r["aggregate_correct"])
    spurious_agg = sum(1 for r in results if r.get("aggregate_correct") is False
                       and (r.get("oracle_intent", {}).get("target") or {}).get("aggregate") is None
                       and (r.get("parsed_intent", {}).get("target") or {}).get("aggregate") is not None)

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

    # Canonicalization metrics
    total_rules_applied = sum(r.get("rules_applied_count", 0) for r in results)
    cases_with_rules = sum(1 for r in results if r.get("rules_applied_count", 0) > 0)

    # Raw action accuracy (before canonicalization)
    raw_action_correct = sum(1 for r in results
                             if r.get("raw_json") and r["raw_json"].get("action") == r.get("oracle_intent", {}).get("action"))
    raw_json_count = sum(1 for r in results if r.get("raw_json") and "_raw_text" not in r["raw_json"])

    return {
        "total_cases": total,
        "schema_validity_rate": schema_valid_count / total,
        "action_accuracy": action_correct / total,
        "raw_action_accuracy": raw_action_correct / raw_json_count if raw_json_count > 0 else None,
        "raw_json_success_rate": raw_json_count / total,
        "canonicalization_rate": cases_with_rules / total,
        "avg_rules_per_case": total_rules_applied / total,
        "role_presence_precision": sum(role_precisions) / len(role_precisions),
        "role_presence_recall": sum(role_recalls) / len(role_recalls),
        "role_presence_f1": sum(role_f1s) / len(role_f1s),
        "spurious_role_rate": total_spurious / total_pred_roles if total_pred_roles > 0 else 0,
        "location_f1": loc_f1,
        "relation_f1": rel_f1,
        "quantifier_accuracy": quant_correct / total,
        "non_default_quantifier_accuracy": non_default_quant_correct / len(non_default_quant_cases) if non_default_quant_cases else None,
        "non_default_quantifier_count": len(non_default_quant_cases),
        "spurious_quantifier_count": spurious_quant,
        "aggregate_positive_accuracy": agg_positive_correct / len(oracle_has_agg) if oracle_has_agg else None,
        "aggregate_positive_count": len(oracle_has_agg),
        "spurious_aggregate_count": spurious_agg,
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
        f"- **Action accuracy (post-canonicalization)**: {summary['action_accuracy']:.4f}",
    ]

    if summary.get("raw_action_accuracy") is not None:
        lines.append(f"- **Action accuracy (raw LLM)**: {summary['raw_action_accuracy']:.4f}")
    if summary.get("canonicalization_rate") is not None:
        lines.append(f"- **Canonicalization rate**: {summary['canonicalization_rate']:.4f}")

    lines.extend([
        f"- **Role presence P/R/F1**: {summary['role_presence_precision']:.4f} / {summary['role_presence_recall']:.4f} / {summary['role_presence_f1']:.4f}",
        f"- **Spurious role rate**: {summary['spurious_role_rate']:.4f}",
        f"- **Quantifier accuracy**: {summary['quantifier_accuracy']:.4f}",
    ])

    if summary.get("aggregate_positive_accuracy") is not None:
        lines.append(f"- **Aggregate positive-case accuracy**: {summary['aggregate_positive_accuracy']:.4f} (n={summary['aggregate_positive_count']})")
    if summary.get("spurious_aggregate_count"):
        lines.append(f"- **Spurious aggregate count**: {summary['spurious_aggregate_count']}")

    lines.extend([
        f"- **Strict exact match**: {summary['strict_exact_match_rate']:.4f} ({summary['strict_exact_match_count']}/{summary['total_cases']})",
        f"- **Avg latency**: {summary['avg_latency_ms']:.1f}ms",
        "",
    ])

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
    out = Path(output_dir) if output_dir else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
        # Truncate append-only files at start of run
        for fname in ["per_case_results.jsonl"]:
            (out / fname).write_text("", encoding="utf-8")

    for repeat in range(repeats):
        logger.info(f"Repeat {repeat + 1}/{repeats}")
        repeat_results = []
        for i, case in enumerate(cases):
            result = evaluate_intent_case(case, model, config)
            result["repeat"] = repeat
            all_results.append(result)
            repeat_results.append(result)
            status = "OK" if result["strict_exact_match"] else f"FAIL({result['failure_category']})"
            logger.info(f"  [{repeat}] {case['case_id']}: {status} ({result['latency_ms']:.0f}ms)")

            # Incremental save: append-only JSONL
            if out:
                r_copy = {k: v for k, v in result.items()
                         if k not in ("parsed_intent", "oracle_intent", "thinking_text")}
                with open(out / "per_case_results.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(r_copy, ensure_ascii=False, default=str) + "\n")

        # After each repeat: recompute summary and save full outputs
        if out:
            summary = compute_intent_summary(all_results, cases)
            summary["model"] = model
            summary["repeats"] = repeats
            summary["temperature"] = temperature

            with open(out / "summary.json", "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)

            with open(out / "predictions.jsonl", "w", encoding="utf-8") as f:
                for r in all_results:
                    f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

            failures = [r for r in all_results if not r["strict_exact_match"]]
            with open(out / "failures.jsonl", "w", encoding="utf-8") as f:
                for r in failures:
                    f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

            with open(out / "error_breakdown.json", "w", encoding="utf-8") as f:
                json.dump(summary.get("failure_breakdown", {}), f, indent=2)

            write_intent_summary_md(summary, out / "report.md")
            logger.info(f"  Repeat {repeat + 1} saved to {out}")

    # Final summary
    summary = compute_intent_summary(all_results, cases)
    summary["model"] = model
    summary["repeats"] = repeats
    summary["temperature"] = temperature

    # Repeat stability (if multiple repeats)
    if repeats > 1:
        from collections import defaultdict
        by_case = defaultdict(list)
        for r in all_results:
            by_case[r["case_id"]].append(r)

        stable_action = sum(1 for reps in by_case.values()
                           if len(set(r["action_correct"] for r in reps)) == 1)
        stable_strict = sum(1 for reps in by_case.values()
                           if len(set(r["strict_exact_match"] for r in reps)) == 1)
        total_multi = sum(1 for v in by_case.values() if len(v) >= 2)

        stability = {
            "unique_case_count": len(by_case),
            "total_evaluations": len(all_results),
            "all_repeats_identical_rate": stable_strict / total_multi if total_multi > 0 else None,
            "action_agreement_rate": stable_action / total_multi if total_multi > 0 else None,
        }
        summary["repeat_stability"] = stability

        if output_dir:
            with open(Path(output_dir) / "repeat_stability.json", "w", encoding="utf-8") as f:
                json.dump(stability, f, indent=2)

    # Final save (run manifest + raw outputs)
    if output_dir:
        out = Path(output_dir)

        # Raw outputs
        with open(out / "raw_outputs.jsonl", "w", encoding="utf-8") as f:
            for r in all_results:
                f.write(json.dumps({
                    "case_id": r["case_id"],
                    "repeat": r["repeat"],
                    "raw_output": r["raw_output"],
                    "schema_valid": r["schema_valid"],
                }, ensure_ascii=False) + "\n")

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
