"""LLM Intent Parser 语义测试（只调用，不修改 LLM 代码）。

用法：python run_llm_intent_eval.py --model <ollama_model>
指标：Schema validity / Action accuracy / Target role F1 / Node-type 提供率 / Spurious-role rate / Location(room) extraction
输出：results/llm_intent/<model_slug>/
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
from pathlib import Path

LLM_PROJECT = Path(__file__).resolve().parents[3] / "llm-rag" / "planner"
sys.path.insert(0, str(LLM_PROJECT))

from llm.intent_parser import parse_intent_with_llm  # noqa: E402

DEFAULT_MODEL = "gemma4:e4b"
OLLAMA_HOST = "http://127.0.0.1:11434"
CASES_DIR = Path(__file__).resolve().parent
TOP_DIR = CASES_DIR / "results" / "llm_intent"


def slug(m: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", m.lower()).strip("_")


def _has_room(intent) -> bool:
    t = intent.target if intent else None
    if t is not None:
        if getattr(t, "room", None):
            return True
        for loc in getattr(t, "locations", []) or []:
            if getattr(loc, "node_type", None) in ("room", "place"):
                return True
        agg = getattr(t, "aggregate", None)
        if agg is not None:
            agg_loc = getattr(agg, "location", None)
            if agg_loc is not None and getattr(agg_loc, "node_type", None) in ("room", "place"):
                return True
    if intent is not None:
        for c in intent.constraints:
            loc = getattr(c, "location", None)
            if loc is not None and getattr(loc, "node_type", None) in ("room", "place"):
                return True
            if getattr(c, "room", None):
                return True
    return False


def evaluate_one(case: dict, model: str, config: dict) -> dict:
    instr = case["instruction"]
    t0 = time.time()
    try:
        intent = parse_intent_with_llm(instr, model=model, config=config)
        latency = (time.time() - t0) * 1000
        r = {"case_id": case["case_id"], "instruction": instr,
             "expected_action": case.get("expected_action"),
             "expected_node_type": case.get("expected_node_type"),
             "expected_target": case.get("expected_target", False),
             "expected_room": case.get("expected_room", False),
             "latency_ms": round(latency, 1)}
        r["schema_valid"] = 1 if intent is not None else 0
        if intent is None:
            r.update({"action_correct": 0, "target_present": 0, "node_type_provided": 0,
                      "node_type_correct": 0, "spurious_role": 0, "room_correct": 0,
                      "predicted_action": None, "predicted_node_type": None})
            return r
        target = intent.target
        r["predicted_action"] = intent.action
        r["action_correct"] = 1 if intent.action == case.get("expected_action") else 0
        r["target_present"] = 1 if target is not None else 0
        r["spurious_role"] = 1 if (not case.get("expected_target", False) and target is not None) else 0
        nt = (target.node_type or "").lower() if target else ""
        r["node_type_provided"] = 1 if nt else 0
        r["predicted_node_type"] = nt or ""
        r["node_type_correct"] = 0
        if case.get("expected_node_type") and target is not None and nt:
            r["node_type_correct"] = 1 if nt == case["expected_node_type"] else 0
        r["room_correct"] = 0
        if case.get("expected_room", False):
            r["room_correct"] = 1 if _has_room(intent) else 0
        return r
    except Exception as e:
        return {"case_id": case["case_id"], "instruction": instr,
                "expected_action": case.get("expected_action"),
                "expected_node_type": case.get("expected_node_type"),
                "expected_target": case.get("expected_target", False),
                "expected_room": case.get("expected_room", False),
                "schema_valid": 0, "action_correct": 0, "target_present": 0,
                "node_type_provided": 0, "node_type_correct": 0, "spurious_role": 0,
                "room_correct": 0, "error": str(e)[:200]}


def summarize(results: list[dict]) -> dict:
    n = len(results)
    schema = sum(r["schema_valid"] for r in results) / n if n else 0
    action = sum(r["action_correct"] for r in results) / n if n else 0
    tp = sum(1 for r in results if r["expected_target"] and r["target_present"])
    fp = sum(1 for r in results if not r["expected_target"] and r["target_present"])
    fn = sum(1 for r in results if r["expected_target"] and not r["target_present"])
    precision = tp / (tp + fp) if (tp + fp) else 0
    recall = tp / (tp + fn) if (tp + fn) else 0
    role_f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0
    nt_provided = sum(r["node_type_provided"] for r in results) / n if n else 0
    nt_cases = [r for r in results if r["expected_node_type"] and r["expected_target"]]
    nt_acc = sum(r["node_type_correct"] for r in nt_cases) / len(nt_cases) if nt_cases else 0
    spurious = sum(r["spurious_role"] for r in results) / n if n else 0
    room_cases = [r for r in results if r["expected_room"]]
    room_acc = sum(r["room_correct"] for r in room_cases) / len(room_cases) if room_cases else 0
    latencies = [r["latency_ms"] for r in results if r.get("latency_ms") is not None]
    return {"total_cases": n, "schema_validity": round(schema, 4),
            "action_accuracy": round(action, 4),
            "target_role_precision": round(precision, 4),
            "target_role_recall": round(recall, 4),
            "target_role_f1": round(role_f1, 4),
            "node_type_provided_rate": round(nt_provided, 4),
            "node_type_accuracy_when_provided": round(nt_acc, 4),
            "spurious_role_rate": round(spurious, 4),
            "room_extraction_accuracy": round(room_acc, 4),
            "warm_mean_ms": round(statistics.mean(latencies), 1) if latencies else 0,
            "warm_median_ms": round(statistics.median(latencies), 1) if latencies else 0}


def main():
    model = DEFAULT_MODEL
    if "--model" in sys.argv:
        model = sys.argv[sys.argv.index("--model") + 1]
    config = {"ollama_host": OLLAMA_HOST, "options": {"temperature": 0, "num_ctx": 8192}}
    cases = [json.loads(l) for l in open(CASES_DIR / "llm_intent_cases.jsonl", encoding="utf-8") if l.strip()]
    results = [evaluate_one(c, model, config) for c in cases]
    metrics = summarize(results)

    out_dir = TOP_DIR / slug(model)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "per_case_results.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    failures = [r for r in results if not r["schema_valid"] or not r["action_correct"]]
    with open(out_dir / "failures.jsonl", "w", encoding="utf-8") as f:
        for r in failures:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump({"model": model, **metrics}, f, ensure_ascii=False, indent=2)

    print(f"=== LLM Intent [{model}] ===")
    print(f"  schema={metrics['schema_validity']:.2%} action={metrics['action_accuracy']:.2%} "
          f"roleF1={metrics['target_role_f1']:.3f} spurious={metrics['spurious_role_rate']:.2%} "
          f"nt_provided={metrics['node_type_provided_rate']:.2%} room={metrics['room_extraction_accuracy']:.2%}")
    print(f"  失败: {len(failures)}")


if __name__ == "__main__":
    main()
