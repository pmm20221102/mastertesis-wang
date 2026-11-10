#!/usr/bin/env python3
"""Compile fresh Hospital predictions and execute eligible Nav2 missions."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml

from score_taskchain_benchmark import read_jsonl

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEMO = HERE.parent / "navigation-ui-demo"
PROFILE = DEMO / "nav2-profile"
BRIDGE = ROOT / "khronos-nav2-bridge/khronos_nav2_bridge/scripts"
sys.path.insert(0, str(BRIDGE))
from prepare_instruction_mission import build_candidate_mission


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_prediction_sources(records, source, database_sha256):
    """Accept pinned current records or exactly matching historical snapshots."""
    legacy = {"graph_sha256": source["canonical_graph_sha256"],
              "database_sha256": database_sha256,
              "raw_dsg_sha256": source["source_dsg_sha256"]}
    for row in records:
        provenance = row.get("provenance")
        if not isinstance(provenance, dict) or not provenance:
            raise ValueError(f"{row.get('task_id')}: unknown prediction provenance; regenerate predictions")
        current = any(key in provenance for key in source)
        expected = source if current else legacy
        for key, value in expected.items():
            if key not in provenance or provenance[key] != value:
                raise ValueError(f"{row.get('task_id')}: prediction provenance {key} missing/mismatch; "
                                 "select the original verified build/profile or regenerate predictions")
        # Also reject contradictory legacy aliases on a current record.
        for key, value in legacy.items():
            if key in provenance and provenance[key] != value:
                raise ValueError(f"{row.get('task_id')}: prediction provenance {key} mismatch")


def load_batch_sources(db_dir, bundle_path, defaults_path, graph_path=None):
    """Use the same profile and reproducible RAG checks as plan_nav2."""
    planner = ROOT / "llm-rag/planner"
    if str(planner) not in sys.path:
        sys.path.insert(0, str(planner))
    from plan_nav2 import _load_defaults, _sha256_file, verify_rag_build
    from floor_transition_core import validate_transition_bundle

    bundle = yaml.safe_load(bundle_path.read_text(encoding="utf-8"))
    bundle = validate_transition_bundle(bundle, verify_files=True)
    defaults = _load_defaults(defaults_path, bundle)
    database = db_dir.expanduser().resolve(strict=True)
    # Read a legacy snapshot hash before Chroma opens and updates SQLite metadata.
    database_hash = _sha256_file(database / "chroma.sqlite3")
    rag = verify_rag_build(database, expected_source=defaults["rag_source"])
    graph_path = graph_path or database.parent / "canonical_graph_v2.json"
    if _sha256_file(graph_path) != rag["canonical_graph_sha256"]:
        raise ValueError("selected graph does not match the verified RAG canonical_graph_sha256")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    source = {
        "rag_build_id": rag["build_id"],
        "rag_manifest_sha256": rag["manifest_sha256"],
        "rag_vector_content_sha256": rag.get("vector_content_sha256"),
        "source_dsg_sha256": rag["source_dsg_sha256"],
        "semantic_review_csv_sha256": rag["semantic_review_csv_sha256"],
        "canonical_graph_sha256": rag["canonical_graph_sha256"],
        "map_bundle_id": bundle["map_bundle_id"],
        "map_bundle_sha256": _sha256_file(bundle_path),
        "start_profile_sha256": _sha256_file(defaults_path),
    }
    return bundle, defaults, graph, source, database_hash


def runner_allows_next_task(result):
    return (isinstance(result, dict) and result.get("batch_safe") is True
            and result.get("status") in ("succeeded", "failed"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, default=HERE / "public/tasks_en.jsonl")
    parser.add_argument("--bundle", type=Path, default=PROFILE / "hospital_floor_transitions.yaml")
    parser.add_argument("--defaults", type=Path, default=PROFILE / "hospital_nav2_plan_defaults.yaml")
    parser.add_argument("--db-dir", type=Path, default=DEMO / "rag/vector_db")
    parser.add_argument("--graph", type=Path,
                        help="defaults to the canonical graph beside --db-dir")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if not args.prepare_only and not re.fullmatch(r"[1-9][0-9]*", os.environ.get("ROS_DOMAIN_ID", "")):
        parser.error("set ROS_DOMAIN_ID to the dedicated simulation domain")
    records = read_jsonl(args.predictions)
    task_ids = [row["task_id"] for row in read_jsonl(args.tasks)]
    keys = [(row.get("task_id"), row.get("repeat")) for row in records]
    expected = {(task_id, 1) for task_id in task_ids}
    if (not task_ids or len(expected) != len(task_ids) or len(keys) != len(expected)
            or set(keys) != expected or any(not re.fullmatch(r"[A-Za-z0-9_-]+", key) for key in task_ids)):
        parser.error("expected one repeat-1 prediction per unique, filename-safe task ID")
    try:
        bundle, defaults, graph, provenance, database_hash = load_batch_sources(
            args.db_dir, args.bundle, args.defaults, args.graph)
        validate_prediction_sources(records, provenance, database_hash)
    except (ValueError, OSError, ImportError) as error:
        parser.error(str(error))

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = []
    batch_stopped = False
    for row in records:
        task_id = row["task_id"]
        result = row.get("result")
        prepared = build_candidate_mission(
            result, bundle, defaults["initial_state"], task_id,
            floor_tolerance_m=float(defaults["floor_z_tolerance_m"]), canonical_graph=graph,
        )
        mission = prepared.pop("mission")
        item = {"task_id": task_id, "preparation": prepared, "execution": "not_submitted"}
        report.append(item)
        if prepared["status"] != "ready":
            continue
        mission_path = output / f"{task_id}.mission.json"
        write_json(mission_path, mission)
        if args.prepare_only:
            continue

        nav_result = None
        result_path = output / f"{task_id}.nav2.json"
        with (output / f"{task_id}.nav2.log").open("w", encoding="utf-8") as log:
            run = subprocess.run(
                ["/usr/bin/python3", str(BRIDGE / "run_task_chain_nav2.py"),
                 "--mission", str(mission_path), "--bundle", str(args.bundle.resolve()),
                 "--output", str(result_path), "--robot-model", defaults["robot_model"]],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False,
            )
        item["returncode"] = run.returncode
        if result_path.exists():
            try:
                nav_result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                item["result_error"] = str(error)
            if isinstance(nav_result, dict):
                item["nav2_status"] = nav_result.get("status")
                item["failure_stage"] = nav_result.get("failure_stage")
        item["execution"] = (
            "succeeded" if run.returncode == 0 and item.get("nav2_status") == "succeeded"
            else "failed"
        )
        print(f"{task_id}: {item['execution']}", flush=True)
        item["batch_safe"] = runner_allows_next_task(nav_result)
        if not item["batch_safe"]:
            batch_stopped = True
            item["batch_stop_reason"] = "runner did not confirm the navigation goal is terminal"
            break

    outcomes = Counter(row["execution"] for row in report)
    submitted = outcomes["succeeded"] + outcomes["failed"]
    summary = {
        "predictions": len(records),
        "provenance": provenance,
        "batch_stopped": batch_stopped,
        "unprocessed_task_ids": [row["task_id"] for row in records[len(report):]],
        "preparation_counts": dict(Counter(row["preparation"]["status"] for row in report)),
        "submitted": submitted,
        "succeeded": outcomes["succeeded"],
        "failed": outcomes["failed"],
        "navigation_success_rate": outcomes["succeeded"] / submitted if submitted else None,
        "prepare_only": args.prepare_only,
        "map_bundle_id": bundle["map_bundle_id"],
        "tasks": report,
    }
    write_json(output / "summary.json", summary)
    print(f"Completed {outcomes['succeeded']}/{submitted} submitted missions. Summary: {output / 'summary.json'}")
    return 2 if batch_stopped else 0


if __name__ == "__main__":
    sys.exit(main())
