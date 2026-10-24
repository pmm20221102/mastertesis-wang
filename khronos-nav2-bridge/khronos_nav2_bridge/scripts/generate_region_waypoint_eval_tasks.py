#!/usr/bin/env python3
"""Generate the 12-region waypoint corpus without touching frozen LLM+RAG."""

import argparse
import hashlib
import json
import pathlib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    config_path = pathlib.Path(args.config)
    config = json.loads(config_path.read_text())
    models = config["models"]
    cases = config["cases"]
    reachable_rooms = sorted(set(config["reachable_room_ids"]))
    if len(models) != 7 or len(set(models)) != 7:
        raise SystemExit("region evaluation requires exactly 7 unique models")
    if len(cases) != 12:
        raise SystemExit("region evaluation requires exactly 12 cases")

    rows = []
    for case in cases:
        room_ids = case["expected_room_ids"]
        waypoint_ids = case["waypoint_ids"]
        if len(room_ids) != len(waypoint_ids) or len(room_ids) < 2:
            raise SystemExit(f"invalid route shape: {case['case_id']}")
        rows.append({
            **case,
            "repeat": 0,
            "expected_waypoints": len(waypoint_ids),
        })

    covered_rooms = sorted({room for row in rows for room in row["expected_room_ids"]})
    if covered_rooms != reachable_rooms:
        raise SystemExit(
            "cases must cover every and only navigation-start reachable room"
        )

    body = "\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows
    ) + "\n"
    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body)
    manifest = {
        "schema_version": "region-waypoint-corpus-v1",
        "cases": len(rows),
        "models": models,
        "model_count": len(models),
        "distinct_rooms": len(covered_rooms),
        "reachable_room_count": len(reachable_rooms),
        "reachable_coverage_complete": covered_rooms == reachable_rooms,
        "coverage_limit_reason": config["coverage_limit_reason"],
        "covered_room_ids": covered_rooms,
        "total_waypoints": sum(row["expected_waypoints"] for row in rows),
        "min_waypoints": min(row["expected_waypoints"] for row in rows),
        "max_waypoints": max(row["expected_waypoints"] for row in rows),
        "route_buckets": {
            bucket: sum(row["route_bucket"] == bucket for row in rows)
            for bucket in ("short", "medium", "long")
        },
        "corpus_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "config": str(config_path),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
    }
    (output.parent / "corpus.manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
