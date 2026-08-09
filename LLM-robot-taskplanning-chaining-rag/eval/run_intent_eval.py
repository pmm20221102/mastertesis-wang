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
    """Compare predicted vs expected EntityReference.

    Returns semantic_match (with identity fallback) and surface_exact.
    P0 #9 fix: when no label, text IS the semantic identity.
    P0 #10 fix: locations compare text + node_type.
    P0 #12.3: position comparison with 2D tolerance.
    P0 #12.4: aggregate.location and aggregate.value comparison.
    """
    if exp is None and pred is None:
        return {"semantic_match": True, "surface_match": True, "fields": {}}
    if exp is None:
        return {"semantic_match": False, "surface_match": False, "fields": {}, "spurious": True}
    if pred is None:
        return {"semantic_match": False, "surface_match": False, "fields": {}, "missing": True}

    fields: dict[str, bool] = {}

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

    # text — always tracked for surface match
    pred_text = _normalize_text(pred.get("text"))
    exp_text = _normalize_text(exp.get("text"))
    fields["text_match"] = pred_text == exp_text

    # P0 #9: Semantic identity fallback
    # If label exists → compare label + node_type
    # If no label but visible identifier (text looks like place_XXXX/room_XXXX) → compare text + node_type
    # Else → compare text
    import re as _re
    has_label = bool(exp_label)  # True if oracle has a canonical label
    is_visible_id = bool(_re.match(r"^(place|room|object|agent|person)_?\d+", exp_text))

    if has_label:
        # Standard case: label + node_type defines identity
        semantic_identity = fields["label"] and fields["node_type"]
    elif is_visible_id:
        # Visible identifier: text IS the identity (e.g., place_1234, room_0755)
        semantic_identity = pred_text == exp_text and fields["node_type"]
    else:
        # No label, no visible ID: text is the only identity
        semantic_identity = pred_text == exp_text

    fields["semantic_identity"] = semantic_identity

    # quantifier
    pred_quant = pred.get("quantifier", "one")
    exp_quant = exp.get("quantifier", "one")
    fields["quantifier"] = pred_quant == exp_quant

    # P0 #10: locations — compare node_type + text
    pred_locs = pred.get("locations", [])
    exp_locs = exp.get("locations", [])
    if exp_locs:
        pred_loc_set = {(_normalize_text(l.get("node_type")), _normalize_text(l.get("text", ""))) for l in pred_locs}
        exp_loc_set = {(_normalize_text(l.get("node_type")), _normalize_text(l.get("text", ""))) for l in exp_locs}
        fields["locations"] = pred_loc_set == exp_loc_set
    else:
        fields["locations"] = len(pred_locs) == 0

    # relations
    pred_rels = pred.get("relations", [])
    exp_rels = exp.get("relations", [])
    pred_rel_set = {(r.get("relation"), _normalize_text(r.get("target_text"))) for r in pred_rels}
    exp_rel_set = {(r.get("relation"), _normalize_text(r.get("target_text"))) for r in exp_rels}
    fields["relations"] = pred_rel_set == exp_rel_set

    # position (P0 #12.3) — compare with 2D tolerance (x, y only)
    pred_pos = pred.get("position")
    exp_pos = exp.get("position")
    if exp_pos is None:
        fields["position"] = pred_pos is None
    elif pred_pos is None:
        fields["position"] = False
    else:
        # Compare first 2 dimensions (x, y) with 10cm tolerance
        import math
        dim = min(len(pred_pos), len(exp_pos), 2)
        if dim >= 2:
            dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(pred_pos[:2], exp_pos[:2])))
            fields["position"] = dist < 0.1
        else:
            fields["position"] = pred_pos == exp_pos

    # aggregate (P0 #12.4) — includes location and value
    pred_agg = pred.get("aggregate")
    exp_agg = exp.get("aggregate")
    if exp_agg is None:
        fields["aggregate"] = pred_agg is None
    elif pred_agg is None:
        fields["aggregate"] = False
    else:
        agg_ok = (
            pred_agg.get("operation") == exp_agg.get("operation")
            and pred_agg.get("target_labels") == exp_agg.get("target_labels")
        )
        # aggregate.location comparison
        exp_loc = exp_agg.get("location")
        pred_loc = pred_agg.get("location")
        if exp_loc:
            if pred_loc:
                agg_ok = agg_ok and (
                    _normalize_text(pred_loc.get("text")) == _normalize_text(exp_loc.get("text"))
                    and pred_loc.get("node_type") == exp_loc.get("node_type")
                )
            else:
                agg_ok = False
        # aggregate.value comparison
        if exp_agg.get("value") is not None:
            agg_ok = agg_ok and pred_agg.get("value") == exp_agg.get("value")
        fields["aggregate"] = agg_ok

    # Semantic match: uses identity fallback (not just label)
    semantic_match = semantic_identity and fields["node_type"] and fields["room"]

    # Surface exact: includes text_match and all other fields
    surface_match = all(fields.values())

    return {
        "semantic_match": semantic_match,
        "surface_match": surface_match,
        "fields": fields,
    }


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
    """Classify the primary failure category.

    Full taxonomy per P0 #14: checks actor, source, destination, constraints,
    position, step roles — not just target.
    """
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

    # Check each role's fields
    for role in roles:
        pred_ref = predicted.get(role) or {}
        exp_ref = expected.get(role) or {}
        if not exp_ref:
            continue

        if exp_ref.get("label") and _normalize_label(pred_ref.get("label")) != _normalize_label(exp_ref.get("label")):
            return f"wrong_{role}_label"

        if exp_ref.get("node_type") and _normalize_text(pred_ref.get("node_type")) != _normalize_text(exp_ref.get("node_type")):
            return f"wrong_{role}_node_type"

        if role == "target":
            if exp_ref.get("locations") and not pred_ref.get("locations"):
                return "location_error"

            if exp_ref.get("relations"):
                pred_rels = pred_ref.get("relations", [])
                exp_rels = exp_ref.get("relations", [])
                if len(pred_rels) != len(exp_rels) or set((r.get("relation"), r.get("target_text")) for r in pred_rels) != set((r.get("relation"), r.get("target_text")) for r in exp_rels):
                    return "relation_error"

            if exp_ref.get("quantifier") and pred_ref.get("quantifier") != exp_ref.get("quantifier"):
                return "quantifier_error"

            if exp_ref.get("aggregate"):
                pred_agg = pred_ref.get("aggregate")
                if not pred_agg or pred_agg.get("operation") != exp_ref["aggregate"].get("operation"):
                    return "aggregate_error"

            # Position check (P0 #12.3)
            if exp_ref.get("position") and not pred_ref.get("position"):
                return "position_error"

    # Constraints check (P0 #12.2)
    pred_constraints = predicted.get("constraints", [])
    exp_constraints = expected.get("constraints", [])
    if exp_constraints and not pred_constraints:
        return "missing_constraint"
    if exp_constraints:
        pred_set = {(c.get("type"), c.get("description", "")) for c in pred_constraints}
        exp_set = {(c.get("type"), c.get("description", "")) for c in exp_constraints}
        if pred_set != exp_set:
            return "constraint_error"

    # Multi-step
    if expected.get("steps"):
        if not predicted.get("steps"):
            return "step_decomposition_error"
        pred_steps = predicted.get("steps", [])
        exp_steps = expected.get("steps", [])
        if len(pred_steps) != len(exp_steps):
            return "step_count_error"
        # Check per-step role binding (P0 #13)
        for i, (ps, es) in enumerate(zip(pred_steps, exp_steps)):
            if ps.get("action") != es.get("action"):
                return f"step_{i}_action_error"
            for r in ["actor", "target", "source", "destination"]:
                if es.get(r) and ps.get(r):
                    if _normalize_label(ps.get(r, {}).get("label")) != _normalize_label(es.get(r, {}).get("label")):
                        return f"step_{i}_{r}_error"

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

    # Semantic strict — excludes text match (P0 #12.1)
    all_role_semantic_correct = all(
        rfm.get("semantic_match", True)
        for rfm in role_field_metrics.values()
    )

    # Surface exact — includes text match
    all_role_surface_correct = all(
        rfm.get("surface_match", True)
        for rfm in role_field_metrics.values()
    )

    # Constraints comparison (P0 #12.2)
    pred_constraints = predicted.get("constraints", [])
    exp_constraints = oracle.get("constraints", [])
    pred_constr_set = {(c.get("type"), _normalize_text(c.get("description", ""))) for c in pred_constraints}
    exp_constr_set = {(c.get("type"), _normalize_text(c.get("description", ""))) for c in exp_constraints}
    constraints_correct = pred_constr_set == exp_constr_set

    # Step-level comparison (P0 #13)
    step_semantic_exact = False
    step_role_accuracy = 0.0
    if exp_steps is None:
        step_semantic_exact = pred_steps is None
    elif pred_steps is not None and len(pred_steps) == len(exp_steps):
        step_matches = 0
        for ps, es in zip(pred_steps, exp_steps):
            step_ok = ps.get("action") == es.get("action")
            # Compare step-level roles using full EntityReference comparator
            for role in ["actor", "target", "source", "destination"]:
                pred_ref = ps.get(role)
                exp_ref = es.get(role)
                if exp_ref is not None or pred_ref is not None:
                    comp = _compare_entity_ref(pred_ref, exp_ref)
                    step_ok = step_ok and comp.get("semantic_match", False)
            if step_ok:
                step_matches += 1
        step_role_accuracy = step_matches / len(exp_steps)
        step_semantic_exact = step_matches == len(exp_steps)

    # Strict exact match (semantic — excludes text)
    strict_exact = (
        schema_valid
        and action_correct
        and role_metrics["f1"] == 1.0
        and all_role_semantic_correct
        and (location_metrics is None or location_metrics["exact_match"])
        and (relation_metrics is None or relation_metrics["exact_match"])
        and quantifier_correct
        and aggregate_correct
        and constraints_correct
        and multi_step_exact
        and step_semantic_exact
    )

    # Surface exact (includes text)
    surface_exact = (
        strict_exact
        and all_role_surface_correct
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
        # Constraints (P0 #12.2)
        "constraints_correct": constraints_correct,
        # Aggregate
        "aggregate_correct": aggregate_correct,
        # Multi-step
        "step_count_correct": step_count_correct,
        "step_actions_correct": step_actions_correct,
        "multi_step_exact": multi_step_exact,
        # Step-level (P0 #13)
        "step_semantic_exact": step_semantic_exact,
        "step_role_accuracy": step_role_accuracy,
        # Strict (semantic vs surface)
        "strict_exact_match": strict_exact,
        "surface_exact_match": surface_exact,
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

    # Strict exact match (semantic)
    strict_exact = sum(1 for r in results if r["strict_exact_match"])

    # Surface exact match (P0 #12.1)
    surface_exact = sum(1 for r in results if r.get("surface_exact_match", False))

    # Constraints accuracy (P0 #12.2)
    oracle_has_constraints = [r for r in results if r.get("oracle_intent", {}).get("constraints")]
    constraints_correct_count = sum(1 for r in oracle_has_constraints if r.get("constraints_correct", False))

    # Step-level metrics (P0 #13)
    step_role_accs = [r.get("step_role_accuracy", 0.0) for r in multi_step_cases if r.get("step_role_accuracy") is not None]
    avg_step_role_accuracy = sum(step_role_accs) / len(step_role_accs) if step_role_accs else None

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

    # Raw action accuracy — report BOTH denominators (P0 #15)
    raw_action_correct = sum(1 for r in results
                             if r.get("raw_json") and r["raw_json"].get("action") == r.get("oracle_intent", {}).get("action"))
    raw_json_count = sum(1 for r in results if r.get("raw_json") and "_raw_text" not in r["raw_json"])

    return {
        "total_cases": total,
        "schema_validity_rate": schema_valid_count / total,
        # Action accuracy (post-canonicalization, all cases)
        "action_accuracy": action_correct / total,
        # Raw action accuracy — two denominators (P0 #15)
        "raw_action_accuracy_all_cases": raw_action_correct / total if total > 0 else None,
        "raw_action_accuracy_schema_valid_only": raw_action_correct / raw_json_count if raw_json_count > 0 else None,
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
        # Constraints (P0 #12.2)
        "constraints_accuracy": constraints_correct_count / len(oracle_has_constraints) if oracle_has_constraints else None,
        "constraints_case_count": len(oracle_has_constraints),
        "aggregate_positive_accuracy": agg_positive_correct / len(oracle_has_agg) if oracle_has_agg else None,
        "aggregate_positive_count": len(oracle_has_agg),
        "spurious_aggregate_count": spurious_agg,
        "multi_step_exact_rate": multi_step_exact / len(multi_step_cases) if multi_step_cases else None,
        "multi_step_case_count": len(multi_step_cases),
        # Step-level (P0 #13)
        "avg_step_role_accuracy": avg_step_role_accuracy,
        # Strict (semantic) vs surface (P0 #12.1)
        "strict_exact_match_rate": strict_exact / total,
        "strict_exact_match_count": strict_exact,
        "surface_exact_match_rate": surface_exact / total if total > 0 else None,
        "surface_exact_match_count": surface_exact,
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
    import time as _time
    run_start_time = _time.strftime("%Y-%m-%dT%H:%M:%S")
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

        # Run manifest (complete provenance)
        import hashlib
        import time as _time

        with open(benchmark_path, "rb") as f:
            bench_hash = hashlib.sha256(f.read()).hexdigest()[:16]

        # Prompt hash
        from llm.intent_parser import INTENT_SYSTEM_PROMPT
        prompt_hash = hashlib.sha256(INTENT_SYSTEM_PROMPT.encode()).hexdigest()[:16]

        # Schema hash
        from planner_schemas.intent import TaskIntent
        schema_hash = hashlib.sha256(
            json.dumps(TaskIntent.model_json_schema(), sort_keys=True).encode()
        ).hexdigest()[:16]

        # Config hash
        config = load_config(CONFIG_PATH)
        config_hash = hashlib.sha256(
            json.dumps(config, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

        # Code commit (best effort)
        try:
            import subprocess
            code_commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(Path(__file__).parent.parent),
                text=True, stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            code_commit = "unknown"

        # Model digest (if available via Ollama)
        model_digest = "unknown"
        try:
            from ollama import Client
            from main_rag import get_ollama_host
            client = Client(host=get_ollama(config), timeout=10)
            for m in client.list().get("models", []):
                if m.get("model") == model:
                    model_digest = m.get("digest", "unknown")[:16]
                    break
        except Exception:
            pass

        manifest = {
            "model": model,
            "model_digest": model_digest,
            "repeats": repeats,
            "temperature": temperature,
            "benchmark": benchmark_path,
            "benchmark_hash": bench_hash,
            "prompt_hash": prompt_hash,
            "schema_hash": schema_hash,
            "config_hash": config_hash,
            "code_commit": code_commit,
            "total_cases": len(cases),
            "total_evaluations": len(all_results),
            "output_dir": str(out),
            "started_at": run_start_time if 'run_start_time' in dir() else "unknown",
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "duration_seconds": round(sum(r.get("latency_ms", 0) for r in all_results) / 1000, 1) if all_results else 0,
            "completion_status": "completed",
            "options": config.get("options", {}),
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
