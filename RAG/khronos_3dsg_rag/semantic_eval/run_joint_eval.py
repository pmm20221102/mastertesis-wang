"""LLM + Semantic RAG + Graph Grounding 联合测试（v2，修复 Ground Truth 泄漏）。

关键修复：
- Graph Grounding 使用 Semantic RAG 的 **predicted** Top-1 label（禁止读取 expected_label）
- 禁止 target.text -> full instruction fallback（target 为空 = intent 失败）
- Intent 严格评分（action / target.text / quantifier）
- 三层预测完整保存（predicted_intent / semantic_result / grounding_result）
- Final node ID 与 Ground Truth（expected_grounding.node_ids）比较：Exact ID Match / Candidate F1 / Set Exact
- 严格 failure stage（intent_schema / intent_action / intent_target / semantic_retrieval / graph_grounding_*）
- 失败案例完整性（failure_count 与 failures.jsonl 一致）

用法：python run_joint_eval.py --model <model> --cases joint_cases_v2.jsonl
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAG_ROOT / "src"))
sys.path.insert(0, "/home/ulmmt/Masterthesis-wang/LLM-robot-taskplanning-chaining-rag")

from semantic_retriever import search_semantic  # noqa: E402
from llm.intent_parser import parse_intent_with_llm  # noqa: E402

DEFAULT_MODEL = "gemma4:e4b"
THRESHOLD = 0.35
TOP_K = 5
CASES_DIR = Path(__file__).resolve().parent
TOP_DIR = CASES_DIR / "results" / "joint_test_v2"
CONFIG_BASE = {"ollama_host": "http://127.0.0.1:11434", "options": {"temperature": 0, "num_ctx": 8192}}


def slug(m: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", m.lower()).strip("_")


def load_canonical() -> dict:
    return json.load(open(RAG_ROOT / "outputs" / "office_dense" / "canonical_graph_v2.json", encoding="utf-8"))


def ground_label(canonical: dict, label: str) -> list[str]:
    """确定性 grounding：canonical 中该 label 的全部 object 节点。"""
    return [nid for nid, n in canonical["nodes"].items()
            if n.get("node_type") == "object" and n.get("label") == label]


def intent_score(intent, case: dict) -> dict:
    """严格 intent 评分。"""
    expected_action = case.get("expected_action")
    s = {"action_correct": False, "target_present": False, "target_text": "",
         "quantifier": "", "quantifier_correct": False, "strict": False}
    if intent is None:
        return s
    s["action_correct"] = intent.action == expected_action
    t = intent.target
    if t is not None:
        s["target_present"] = True
        s["target_text"] = t.text or ""
        s["quantifier"] = t.quantifier or "one"
    exp_q = "all" if case.get("expected_quantifier") == "all" else "one"
    s["quantifier_correct"] = (s["quantifier"] == exp_q) if s["target_present"] else False
    s["strict"] = s["action_correct"] and s["target_present"] and bool(s["target_text"]) and s["quantifier_correct"]
    return s


def evaluate_one(case: dict, canonical: dict, model: str, config: dict) -> dict:
    instr = case["instruction"]
    expected_status = case.get("expected_status", "matched")
    exp_label = case.get("expected_label")
    eg = case.get("expected_grounding", {})

    r = {
        "case_id": case["case_id"], "instruction": instr,
        "expected_status": expected_status, "expected_label": exp_label,
        "expected_ids": eg.get("node_ids", []),
        "failure_stage": None, "all_errors": [],
    }

    # ---- Stage 1: LLM Intent ----
    intent = parse_intent_with_llm(instr, model=model, config=config)
    if intent is None:
        r["failure_stage"] = "intent_schema"
        r["all_errors"].append("intent_schema")
        return r
    isc = intent_score(intent, case)
    r["predicted_intent"] = {
        "action": intent.action,
        "target_text": isc["target_text"],
        "quantifier": isc["quantifier"],
    }
    r["intent_score"] = isc
    if not isc["action_correct"]:
        r["failure_stage"] = "intent_action"
        r["all_errors"].append("intent_action")
        return r
    if not isc["target_present"] or not isc["target_text"]:
        # 禁止 fallback：target 为空 = intent 失败
        r["failure_stage"] = "intent_target"
        r["all_errors"].append("intent_target")
        return r

    # ---- Stage 2: Semantic RAG（用 predicted target text）----
    target_text = isc["target_text"]
    sem = search_semantic(target_text, top_k=TOP_K,
                          db_dir=RAG_ROOT / "outputs" / "office_dense" / "vector_db", threshold=THRESHOLD)
    labels = [x.label for x in sem.label_ranking]
    predicted_label = labels[0] if labels else ""
    r["semantic_result"] = {
        "query": target_text,
        "top1_label": predicted_label,
        "label_ranking": labels[:5],
        "hit_at_1": predicted_label == exp_label,
        "recall_at_5": exp_label in labels[:5] if expected_status == "matched" else (sem.status == "not_found"),
        "status": sem.status,
    }

    if expected_status == "not_found":
        semantic_ok = (sem.status == "not_found")
    else:
        semantic_ok = (predicted_label == exp_label)
    r["semantic_correct"] = semantic_ok
    if not semantic_ok:
        r["failure_stage"] = "semantic_retrieval"
        r["all_errors"].append("semantic_retrieval")
        return r

    # ---- Stage 3: Graph Grounding（用 predicted label，禁止 expected_label）----
    if expected_status == "not_found":
        predicted_ids: list[str] = []
        grounding_status = "not_found"
    else:
        predicted_ids = ground_label(canonical, predicted_label)
        grounding_status = "resolved_set" if isc["quantifier"] == "all" else "resolved"
    r["grounding_result"] = {"status": grounding_status, "node_ids": predicted_ids}

    expected_ids = set(eg.get("node_ids", []))
    predicted_set = set(predicted_ids)

    # ---- Stage 4: Final scoring ----
    if expected_status == "not_found":
        grounding_ok = (grounding_status == "not_found")
        r["grounding_correct"] = grounding_ok
        r["exact_id_match"] = grounding_ok
        r["candidate_precision"] = 1.0 if grounding_ok else 0.0
        r["candidate_recall"] = 1.0 if grounding_ok else 0.0
        r["candidate_f1"] = 1.0 if grounding_ok else 0.0
        if not grounding_ok:
            r["failure_stage"] = "graph_grounding_status"
            r["all_errors"].append("graph_grounding_status")
        else:
            r["joint_success"] = True
        return r

    # matched
    r["grounding_correct"] = len(predicted_set) > 0
    r["exact_id_match"] = (predicted_set == expected_ids)
    if expected_ids:
        prec = len(predicted_set & expected_ids) / len(predicted_set) if predicted_set else 0.0
        rec = len(predicted_set & expected_ids) / len(expected_ids)
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        r["candidate_precision"] = round(prec, 4)
        r["candidate_recall"] = round(rec, 4)
        r["candidate_f1"] = round(f1, 4)
    else:
        r["candidate_f1"] = 0.0

    if not r["grounding_correct"]:
        r["failure_stage"] = "graph_grounding_status"
        r["all_errors"].append("graph_grounding_status")
    elif not r["exact_id_match"]:
        r["failure_stage"] = "graph_grounding_id"
        r["all_errors"].append("graph_grounding_id")
    else:
        r["joint_success"] = True

    return r


def main():
    model = DEFAULT_MODEL
    cases_name = "joint_cases_v2.jsonl"
    if "--model" in sys.argv:
        model = sys.argv[sys.argv.index("--model") + 1]
    if "--cases" in sys.argv:
        cases_name = sys.argv[sys.argv.index("--cases") + 1]
    config = dict(CONFIG_BASE)
    cases = [json.loads(l) for l in open(CASES_DIR / cases_name, encoding="utf-8") if l.strip()]
    canonical = load_canonical()
    results = [evaluate_one(c, canonical, model, config) for c in cases]

    n = len(results)
    matched = [r for r in results if r["expected_status"] == "matched"]
    nf = [r for r in results if r["expected_status"] == "not_found"]

    intent_strict = sum(1 for r in results if r.get("intent_score", {}).get("strict")) / n
    semantic_ok = sum(1 for r in results if r.get("semantic_correct")) / n
    grounding_ok = sum(1 for r in results if r.get("grounding_correct")) / n
    exact_id = sum(1 for r in results if r.get("exact_id_match")) / n
    joint_ok = sum(1 for r in results if r.get("joint_success")) / n
    cand_f1 = sum(r.get("candidate_f1", 0) for r in matched) / len(matched) if matched else 0
    set_cases = [r for r in matched if r.get("predicted_intent", {}).get("quantifier") == "all"]
    set_exact = sum(1 for r in set_cases if r.get("exact_id_match")) / len(set_cases) if set_cases else 0
    fail_stage = Counter(r["failure_stage"] for r in results if r["failure_stage"])

    matched = [r for r in results if r["expected_status"] == "matched"]
    semantic_recall5 = sum(1 for r in matched if r.get("semantic_result", {}).get("recall_at_5")) / len(matched) if matched else 0

    metrics = {
        "total_cases": n, "model": model,
        "intent_strict_success": round(intent_strict, 4),
        "semantic_label_hit1": round(sum(1 for r in matched if r.get("semantic_result", {}).get("hit_at_1")) / len(matched) if matched else 0, 4),
        "semantic_recall5": round(semantic_recall5, 4),
        "semantic_retrieval_success": round(semantic_ok, 4),
        "grounding_success": round(grounding_ok, 4),
        "exact_id_match": round(exact_id, 4),
        "candidate_f1_matched": round(cand_f1, 4),
        "set_exact_match": round(set_exact, 4),
        "joint_success": round(joint_ok, 4),
        "failure_by_stage": dict(fail_stage),
    }

    out_dir = TOP_DIR / slug(model)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "per_case_results.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    failures = [r for r in results if r["failure_stage"] is not None]
    with open(out_dir / "failures.jsonl", "w", encoding="utf-8") as f:
        for r in failures:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    metrics["failure_count"] = len(failures)
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    print(f"=== 联合测试 v2 [{model}] ===")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print(f"失败 {len(failures)}:")
    for r in failures[:12]:
        print(f"  {r['case_id']}: {r['instruction'][:38]} stage={r['failure_stage']}")


if __name__ == "__main__":
    main()
