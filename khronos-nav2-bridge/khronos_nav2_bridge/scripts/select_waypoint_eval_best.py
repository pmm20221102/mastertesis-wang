#!/usr/bin/env python3
"""Select the best strict semantic result for every case from seven models."""

import argparse
import hashlib
import json
import pathlib


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def evaluate(case, planner_output):
    chain = ((planner_output.get("task_plan") or {}).get("task_chain") or [])
    grounding = (planner_output.get("grounding") or {}).get("references") or {}
    actual_rooms = []
    refs_resolved = True
    ids_consistent = True
    target_refs = []
    actions = []
    for step in chain:
        action = step.get("action")
        target_ref = step.get("target_ref")
        target_id = step.get("target_id")
        actions.append(action)
        target_refs.append(target_ref)
        grounded = grounding.get(target_ref) if target_ref else None
        selected = (grounded or {}).get("selected") or {}
        if not grounded or grounded.get("status") != "resolved" or not selected:
            refs_resolved = False
            actual_rooms.append(None)
            ids_consistent = False
            continue
        selected_id = str(selected.get("node_id") or "")
        selected_type = str(selected.get("node_type") or "").lower().rstrip("s")
        room_id = (
            selected_id
            if selected_type == "room"
            else str(selected.get("room_id") or selected_id)
        )
        actual_rooms.append(room_id)
        if str(target_id or "") != selected_id:
            ids_consistent = False

    expected_rooms = [str(value) for value in case["expected_room_ids"]]
    room_matches = sum(
        actual == expected
        for actual, expected in zip(actual_rooms, expected_rooms)
    )
    exact_count = len(chain) == case["expected_waypoints"]
    exact_order = exact_count and actual_rooms == expected_rooms
    all_navigate = exact_count and all(action == "navigate_to" for action in actions)
    refs_present = exact_count and all(target_refs)
    valid_status = planner_output.get("status") == "valid"
    no_validation_errors = not planner_output.get("validation_errors")
    semantic_pass = all((
        valid_status,
        exact_count,
        exact_order,
        all_navigate,
        refs_present,
        refs_resolved,
        ids_consistent,
        no_validation_errors,
    ))
    score = (
        1000 * int(semantic_pass)
        + 100 * room_matches
        + 30 * sum(action == "navigate_to" for action in actions)
        + 20 * sum(bool(value) for value in target_refs)
        + 50 * int(valid_status)
        + 40 * int(ids_consistent)
        + 20 * int(no_validation_errors)
    )
    return {
        "semantic_pass": semantic_pass,
        "score": score,
        "expected_rooms": expected_rooms,
        "actual_rooms": actual_rooms,
        "expected_waypoints": case["expected_waypoints"],
        "actual_waypoints": len(chain),
        "actions": actions,
        "target_refs": target_refs,
        "refs_resolved": refs_resolved,
        "ids_consistent": ids_consistent,
        "valid_status": valid_status,
        "no_validation_errors": no_validation_errors,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--batches-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-models", type=int, default=7)
    args = parser.parse_args()

    corpus_path = pathlib.Path(args.corpus)
    cases = [json.loads(line) for line in corpus_path.read_text().splitlines() if line]
    batches = []
    for manifest_path in sorted(pathlib.Path(args.batches_root).glob(
        "*/semantic_batch.manifest.json"
    )):
        manifest = json.loads(manifest_path.read_text())
        batches.append((manifest["model"], manifest_path.parent, manifest))
    models = [item[0] for item in batches]
    if len(batches) != args.expected_models or len(set(models)) != args.expected_models:
        raise SystemExit(
            f"expected {args.expected_models} unique model batches, got {models}"
        )

    output_dir = pathlib.Path(args.output)
    selected_dir = output_dir / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    result_rows = []
    for case in cases:
        candidates = []
        for model_index, (model, batch_dir, manifest) in enumerate(batches):
            manifest_row = next(
                (row for row in manifest["rows"] if row["case_id"] == case["case_id"]),
                None,
            )
            if manifest_row is None:
                raise SystemExit(f"missing {case['case_id']} for model {model}")
            planner_path = batch_dir / f"{case['case_id']}.json"
            if not planner_path.is_file():
                raise SystemExit(f"missing output {planner_path}")
            planner_output = json.loads(planner_path.read_text())
            evaluation = evaluate(case, planner_output)
            candidates.append({
                "model": model,
                "model_index": model_index,
                "duration_s": manifest_row.get("duration_s"),
                "output": str(planner_path),
                "output_sha256": sha256(planner_path),
                "evaluation": evaluation,
                "planner_output": planner_output,
            })

        best = max(
            candidates,
            key=lambda row: (
                row["evaluation"]["semantic_pass"],
                row["evaluation"]["score"],
                -(row["duration_s"] or 1e9),
                -row["model_index"],
            ),
        )
        selected_path = selected_dir / f"{case['case_id']}.json"
        selected_path.write_text(json.dumps({
            "schema_version": "selected-semantic-result-v1",
            "case": case,
            "selected_model": best["model"],
            "evaluation": best["evaluation"],
            "source_output": best["output"],
            "source_output_sha256": best["output_sha256"],
            "planner_output": best["planner_output"],
        }, indent=2, ensure_ascii=False) + "\n")
        result_rows.append({
            "case_id": case["case_id"],
            "selected_model": best["model"],
            "semantic_pass": best["evaluation"]["semantic_pass"],
            "score": best["evaluation"]["score"],
            "expected_waypoints": case["expected_waypoints"],
            "selected_output": str(selected_path),
            "selected_output_sha256": sha256(selected_path),
            "model_results": [
                {
                    "model": candidate["model"],
                    "semantic_pass": candidate["evaluation"]["semantic_pass"],
                    "score": candidate["evaluation"]["score"],
                    "actual_rooms": candidate["evaluation"]["actual_rooms"],
                    "actual_waypoints": candidate["evaluation"]["actual_waypoints"],
                    "duration_s": candidate["duration_s"],
                    "output_sha256": candidate["output_sha256"],
                }
                for candidate in candidates
            ],
        })

    manifest = {
        "schema_version": "best-of-seven-semantic-v1",
        "corpus": str(corpus_path),
        "corpus_sha256": sha256(corpus_path),
        "models": models,
        "model_count": len(models),
        "cases": len(result_rows),
        "semantic_passes": sum(row["semantic_pass"] for row in result_rows),
        "all_cases_pass": all(row["semantic_pass"] for row in result_rows),
        "rows": result_rows,
    }
    (output_dir / "best_of_seven.manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0 if manifest["all_cases_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
