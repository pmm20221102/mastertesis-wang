#!/usr/bin/env python3
"""Create the reproducible natural-language waypoint corpus for one attempt."""
import argparse, hashlib, json, pathlib

VARIANTS = [
    "Starting now, visit {targets} in order and pause briefly at each stop.",
    "Navigate sequentially to {targets}; keep the requested order and stop at every destination.",
    "Please go to {targets}, one after another, waiting briefly at each waypoint.",
    "Follow this route exactly: {targets}. Pause at each location before continuing.",
]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--waypoints", required=True); ap.add_argument("--plan", required=True); ap.add_argument("--output", required=True); ap.add_argument("--seed", type=int, default=20260829)
    a = ap.parse_args(); wp = json.loads(pathlib.Path(a.waypoints).read_text()); plan = json.loads(pathlib.Path(a.plan).read_text())
    steps = plan["task_chain"]
    labels = [s["human_name"] for s in steps]
    targets = ", then ".join(labels)
    cases = []
    for i in range(12):
        text = VARIANTS[i % len(VARIANTS)].format(targets=targets)
        cases.append({"case_id": f"waypoint-{i+1:02d}", "repeat": i // 4, "route_bucket": ("short" if i < 4 else "medium" if i < 8 else "long"), "instruction": text, "waypoint_ids": [s["waypoint_id"] for s in steps], "expected_waypoints": len(steps)})
    out = pathlib.Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in cases) + "\n"
    out.write_text(body)
    manifest = {"schema_version": "waypoint-corpus-v1", "seed": a.seed, "cases": len(cases), "min_waypoints": min(x["expected_waypoints"] for x in cases), "route_buckets": {b: sum(x["route_bucket"] == b for x in cases) for b in ("short", "medium", "long")}, "corpus_sha256": hashlib.sha256(body.encode()).hexdigest(), "waypoint_source": str(pathlib.Path(a.waypoints)), "semantic_plan_source": str(pathlib.Path(a.plan))}
    (out.parent / "corpus.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
if __name__ == "__main__": main()
