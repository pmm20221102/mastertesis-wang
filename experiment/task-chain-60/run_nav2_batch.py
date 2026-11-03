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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, default=HERE / "public/tasks_en.jsonl")
    parser.add_argument("--bundle", type=Path, default=PROFILE / "hospital_floor_transitions.yaml")
    parser.add_argument("--defaults", type=Path, default=PROFILE / "hospital_nav2_plan_defaults.yaml")
    parser.add_argument("--graph", type=Path, default=DEMO / "rag/canonical_graph_v2.json")
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
    bundle = yaml.safe_load(args.bundle.read_text(encoding="utf-8"))
    defaults = yaml.safe_load(args.defaults.read_text(encoding="utf-8"))
    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    if defaults["map_bundle_id"] != bundle["map_bundle_id"]:
        parser.error("defaults and map bundle refer to different builds")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = []
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
            nav_result = json.loads(result_path.read_text(encoding="utf-8"))
            item["nav2_status"] = nav_result.get("status")
            item["failure_stage"] = nav_result.get("failure_stage")
        item["execution"] = (
            "succeeded" if run.returncode == 0 and item.get("nav2_status") == "succeeded"
            else "failed"
        )
        print(f"{task_id}: {item['execution']}", flush=True)

    outcomes = Counter(row["execution"] for row in report)
    submitted = outcomes["succeeded"] + outcomes["failed"]
    summary = {
        "predictions": len(records),
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


if __name__ == "__main__":
    main()
