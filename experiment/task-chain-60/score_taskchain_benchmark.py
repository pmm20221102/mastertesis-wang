#!/usr/bin/env python3
"""Offline scorer for frozen Hospital task-chain benchmark runs."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
DEFAULT_GOLD = HERE / "sealed" / "gold.jsonl"
DEFAULT_BENCHMARK_MANIFEST = HERE / "artifact_manifest.json"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _task_chain(result: dict[str, Any]) -> list[dict[str, Any]]:
    plan = result.get("task_plan")
    chain = plan.get("task_chain", []) if isinstance(plan, dict) else []
    return chain if isinstance(chain, list) else []


def _predicted_room_set(step: dict[str, Any]) -> set[str]:
    target_id = step.get("target_id")
    if target_id is not None:
        return {str(target_id)}
    candidates = step.get("candidate_node_ids") or []
    return {str(node_id) for node_id in candidates if node_id is not None}


def score_trial(trial: dict[str, Any], gold_row: dict[str, Any]) -> dict[str, Any]:
    result = trial.get("result") or {}
    status = result.get("status")
    chain = _task_chain(result)
    behavior = gold_row["expected_behavior"]
    goals = gold_row.get("goals", [])

    if behavior == "navigate":
        expected_count = len(goals)
        plan = result.get("task_plan") or {}
        status_valid = status == "valid" and plan.get("status", "valid") == "valid"
        stop_correctness = []
        stop_details = []
        for index, goal in enumerate(goals):
            if index >= len(chain):
                stop_correctness.append(False)
                stop_details.append({
                    "stop_order": index + 1,
                    "accepted_room_ids": goal.get("accepted_room_ids", []),
                    "predicted_room_ids": [],
                    "correct": False,
                    "failure_reason": "missing_stop",
                })
                continue
            step = chain[index]
            expected_step_number = index + 1
            step_number_ok = step.get("step", expected_step_number) == expected_step_number
            action_ok = step.get("action") == "navigate_to"
            predicted = _predicted_room_set(step)
            accepted = {str(room_id) for room_id in goal.get("accepted_room_ids", [])}
            # A deferred candidate set is safe only when every option is an
            # accepted room for this stop; no candidate may fall outside gold.
            room_set_ok = bool(predicted) and predicted <= accepted
            correct = step_number_ok and action_ok and room_set_ok
            if correct:
                reason = None
            elif not step_number_ok:
                reason = "step_order_mismatch"
            elif not action_ok:
                reason = "wrong_action"
            elif not predicted:
                reason = "missing_room_target"
            else:
                reason = "target_outside_accepted_room_set"
            stop_correctness.append(correct)
            stop_details.append({
                "stop_order": index + 1,
                "accepted_room_ids": sorted(accepted),
                "predicted_room_ids": sorted(predicted),
                "correct": correct,
                "failure_reason": reason,
            })
        exact_chain = (
            status_valid
            and len(chain) == expected_count
            and all(stop_correctness)
            and not result.get("validation_errors")
        )
        hits = sum(stop_correctness)
        failures = []
        if not status_valid:
            failures.append(f"pipeline_status_{status or 'missing'}")
        if len(chain) != expected_count:
            failures.append("stop_count_mismatch")
        failures.extend(
            f"stop_{row['stop_order']}_{row['failure_reason']}"
            for row in stop_details if row["failure_reason"]
        )
        if result.get("validation_errors"):
            failures.append("plan_validation_errors")
        return {
            "task_id": gold_row["task_id"],
            "repeat": trial.get("repeat"),
            "expected_behavior": behavior,
            "predicted_status": status,
            "behavior_correct": status_valid,
            "expected_stop_count": expected_count,
            "predicted_stop_count": len(chain),
            "stop_hits": hits,
            "stop_correctness": stop_correctness,
            "stop_details": stop_details,
            "extra_stop_count": max(0, len(chain) - expected_count),
            "exact_chain_success": exact_chain,
            "trial_success": exact_chain,
            "failure_reasons": failures,
            "elapsed_ms": trial.get("elapsed_ms"),
            "stage_latency_ms": result.get("latency_ms", {}),
        }

    plan = result.get("task_plan") or {}
    clarification_present = (
        status == "ambiguous"
        or (isinstance(result.get("intent"), dict) and result["intent"].get("action") == "ask_clarification")
        or bool(plan.get("clarification_request"))
        or any(step.get("action") == "ask_clarification" for step in chain)
    )
    if behavior == "report_no_matching_destination":
        behavior_correct = status == "target_not_found" and len(chain) == 0
    elif behavior == "request_clarification":
        behavior_correct = clarification_present and not any(
            step.get("action") == "navigate_to" for step in chain
        )
    else:
        raise ValueError(f"Unknown expected behavior {behavior!r} for {gold_row.get('task_id')}")
    return {
        "task_id": gold_row["task_id"],
        "repeat": trial.get("repeat"),
        "expected_behavior": behavior,
        "predicted_status": status,
        "behavior_correct": behavior_correct,
        "expected_stop_count": 0,
        "predicted_stop_count": len(chain),
        "stop_hits": 0,
        "stop_correctness": [],
        "stop_details": [],
        "extra_stop_count": len(chain),
        "exact_chain_success": None,
        "trial_success": behavior_correct,
        "failure_reasons": [] if behavior_correct else [
            "expected_no_match_got_" + str(status or "missing")
            if behavior == "report_no_matching_destination"
            else "explicit_clarification_not_requested"
        ],
        "elapsed_ms": trial.get("elapsed_ms"),
        "stage_latency_ms": result.get("latency_ms", {}),
    }


def score_records(
    records: list[dict[str, Any]], gold_rows: list[dict[str, Any]], repeats: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if repeats < 1:
        raise ValueError("repeats must be positive")
    gold_by_id = {row["task_id"]: row for row in gold_rows}
    if len(gold_by_id) != len(gold_rows):
        raise ValueError("Duplicate task IDs in gold")
    expected_keys = {(task_id, repeat) for task_id in gold_by_id for repeat in range(1, repeats + 1)}
    seen: set[tuple[str, int]] = set()
    by_id_repeat = {}
    for record in records:
        key = (record.get("task_id"), record.get("repeat"))
        if key not in expected_keys:
            raise ValueError(f"Unexpected task/repeat record: {key}")
        if key in seen:
            raise ValueError(f"Duplicate task/repeat record: {key}")
        seen.add(key)
        by_id_repeat[key] = record
    if seen != expected_keys:
        missing = sorted(expected_keys - seen)
        raise ValueError(f"Incomplete run; missing {len(missing)} task/repeat records")

    scored = [
        score_trial(by_id_repeat[(task_id, repeat)], gold_by_id[task_id])
        for repeat in range(1, repeats + 1)
        for task_id in gold_by_id
    ]
    navigable = [row for row in scored if row["expected_behavior"] == "navigate"]
    boundaries = [row for row in scored if row["expected_behavior"] != "navigate"]
    all_elapsed = [float(row["elapsed_ms"]) for row in scored if row.get("elapsed_ms") is not None]
    stage_values: dict[str, list[float]] = defaultdict(list)
    for row in scored:
        for name, value in (row.get("stage_latency_ms") or {}).items():
            if isinstance(value, (int, float)) and math.isfinite(value):
                stage_values[name].append(float(value))
    total_expected_stops = sum(row["expected_stop_count"] for row in navigable)
    total_stop_hits = sum(row["stop_hits"] for row in navigable)
    successful = sum(bool(row["trial_success"]) for row in scored)
    summary = {
        "task_count": len(gold_rows),
        "repeats": repeats,
        "trial_count": len(scored),
        "navigable_trial_count": len(navigable),
        "boundary_trial_count": len(boundaries),
        "exact_chain_success_count": sum(bool(row["exact_chain_success"]) for row in navigable),
        "exact_chain_success_rate": (
            sum(bool(row["exact_chain_success"]) for row in navigable) / len(navigable) if navigable else None
        ),
        "stop_hits": total_stop_hits,
        "expected_stops": total_expected_stops,
        "stop_accuracy": total_stop_hits / total_expected_stops if total_expected_stops else None,
        "boundary_correct_count": sum(bool(row["behavior_correct"]) for row in boundaries),
        "boundary_accuracy": (
            sum(bool(row["behavior_correct"]) for row in boundaries) / len(boundaries) if boundaries else None
        ),
        "overall_trial_success_rate": successful / len(scored) if scored else None,
        "mean_elapsed_ms": statistics.fmean(all_elapsed) if all_elapsed else None,
        "median_elapsed_ms": statistics.median(all_elapsed) if all_elapsed else None,
        "mean_stage_latency_ms": {
            name: statistics.fmean(values) for name, values in sorted(stage_values.items()) if values
        },
        "failure_counts": dict(sorted(Counter(
            reason for row in scored for reason in row.get("failure_reasons", [])
        ).items())),
        "navigation_or_route_success_claimed": False,
    }
    return scored, summary


def score_run(run_dir: Path, output_dir: Path, gold_path: Path = DEFAULT_GOLD) -> dict[str, Any]:
    run_manifest_path = run_dir / "run_manifest.json"
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    benchmark_manifest = json.loads(DEFAULT_BENCHMARK_MANIFEST.read_text(encoding="utf-8"))
    if run_manifest.get("benchmark_id") != benchmark_manifest.get("benchmark_id"):
        raise ValueError("Run belongs to a different benchmark")
    if run_manifest.get("benchmark_manifest_sha256") != _sha256(DEFAULT_BENCHMARK_MANIFEST):
        raise ValueError("Run benchmark manifest hash does not match the current frozen manifest")
    if run_manifest.get("gold_sha256") is not None:
        raise ValueError("Runner provenance must not include a gold-file hash")
    if run_manifest.get("run_status") != "complete":
        raise ValueError("Cannot score an incomplete run")
    if run_manifest.get("run_kind") != "benchmark":
        raise ValueError("Diagnostic runs are not eligible for benchmark scoring")
    records = read_jsonl(run_dir / "trials.jsonl")
    gold_rows = read_jsonl(gold_path)
    if _sha256(gold_path) != benchmark_manifest.get("sealed_gold_sha256"):
        raise ValueError("Sealed gold hash does not match the frozen benchmark manifest")
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite score directory: {output_dir}")
    scored, summary = score_records(records, gold_rows, int(run_manifest["repeats"]))
    for field in (
        "evaluation_status", "intent_prompt_profile", "intent_prompt_sha256",
        "intent_schema_sha256", "planner_source_sha256", "graph_sha256",
        "model", "previous_pretest_run", "task_text_and_gold_unchanged",
    ):
        if field in run_manifest:
            summary[field] = run_manifest[field]
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "per_trial.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in scored), encoding="utf-8"
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a completed Hospital task-chain run offline")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    args = parser.parse_args()
    print(json.dumps(score_run(args.run_dir, args.output_dir, args.gold), indent=2))


if __name__ == "__main__":
    main()
