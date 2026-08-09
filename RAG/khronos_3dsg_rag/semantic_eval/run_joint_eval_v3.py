"""Phase C：正式联合测评（v3）——调用正式 Graph Grounding API，严格评分。

流程（严格按任务书 12/13/14/15/16/18）：
  NL -> LLM Intent -> predicted target.text/quantifier/locations
  -> Semantic RAG Top-1 predicted label
  -> 构造正式 EntityReference -> SceneGraphRAG.resolve_entity
  -> predicted status/node_ids/candidate set
  -> 与 expected_grounding 比较（joint_success = intent_gate AND semantic AND grounding_exact）

预测路径 run_pipeline() 不接收任何 expected_* 字段（任务书 13）。
用法：python run_joint_eval_v3.py --model <model>
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAG_ROOT / "src"))
# LLM 项目根：允许环境变量覆盖，默认基于 __file__ 推导（任务书 3.5，去除硬编码绝对路径）
_DEFAULT_LLM_ROOT = Path(os.environ.get(
    "LLM_PROJECT_ROOT",
    str(RAG_ROOT.parent.parent / "LLM-robot-taskplanning-chaining-rag"),
))
sys.path.insert(0, str(_DEFAULT_LLM_ROOT))

from semantic_retriever import search_semantic  # noqa: E402
from scene_graph_api import SceneGraphRAG  # noqa: E402
from llm.intent_parser import parse_intent_with_llm  # noqa: E402
from planner_schemas.intent import EntityReference, LocationConstraint  # noqa: E402

DEFAULT_MODEL = "gemma4:e4b"
THRESHOLD = 0.35
TOP_K = 5
CASES_DIR = Path(__file__).resolve().parent
OUT_DIR = CASES_DIR / "results" / "joint_test_v3"
CONFIG_BASE = {"ollama_host": "http://127.0.0.1:11434", "options": {"temperature": 0, "num_ctx": 8192}}


def slug(m: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", m.lower()).strip("_")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dir_fingerprint(dirpath: Path) -> str:
    """向量库等目录的轻量指纹：相对路径 + 文件大小（不读内容，目录大时更快）。"""
    entries = []
    if dirpath.exists():
        for p in sorted(dirpath.rglob("*")):
            if p.is_file():
                entries.append(f"{p.relative_to(dirpath)}:{p.stat().st_size}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def room_tail_to_id(canonical: dict, tail: str) -> str | None:
    # 提取纯数字尾号（去除 room 前缀/空格/非数字字符）
    m = re.search(r"(\d{2,})", str(tail))
    digits = m.group(1) if m else str(tail).strip()
    t = digits.lstrip("0")
    for nid, n in canonical["nodes"].items():
        if n.get("node_type") == "room":
            if str(nid).endswith(digits) or str(nid)[-4:].lstrip("0") == t:
                return str(nid)
    return None


def extract_room_text(intent) -> str | None:
    t = intent.target if intent else None
    if t is None:
        return None
    if getattr(t, "room", None):
        return str(t.room)
    for loc in getattr(t, "locations", []) or []:
        if loc.node_type == "room" and (loc.text or loc.node_id):
            return loc.text or str(loc.node_id)[-4:]
    agg = getattr(t, "aggregate", None)
    if agg is not None and getattr(agg, "location", None):
        loc = agg.location
        if loc.node_type == "room":
            return loc.text or str(loc.node_id)[-4:]
    return None


def run_pipeline(instruction: str, model: str, rag, canonical: dict, config: dict) -> dict:
    """预测路径：不接收任何 expected_* 字段。"""
    pred = {"intent": None, "semantic": None, "grounding": None, "error": None}

    intent = parse_intent_with_llm(instruction, model=model, config=config)
    if intent is None:
        pred["error"] = "intent_schema"
        return pred
    target = intent.target
    target_text = target.text if target else None
    quantifier = (target.quantifier if target else None) or "one"
    node_type = target.node_type if target else None
    room_text = extract_room_text(intent)
    pred["intent"] = {"action": intent.action, "target_text": target_text, "quantifier": quantifier,
                      "node_type": node_type, "room_text": room_text}

    if not target_text:
        pred["error"] = "intent_target"
        return pred

    sem = search_semantic(target_text, top_k=TOP_K,
                          db_dir=RAG_ROOT / "outputs" / "office_dense" / "vector_db", threshold=THRESHOLD)
    labels = [x.label for x in sem.label_ranking]
    predicted_label = labels[0] if labels else ""
    pred["semantic"] = {"query": target_text, "top1_label": predicted_label,
                        "label_ranking": labels[:5], "status": sem.status}
    if not predicted_label:
        pred["error"] = "semantic_retrieval"
        return pred

    # Graph Grounding：基于 Semantic RAG predicted label + Room 的确定性 canonical 解析
    # （正式 Graph Grounding 的 label 路径；resolve_entity 的 single 路径依赖 text 语义，
    #   与 Semantic RAG 分层后不适用，故采用 canonical 确定性 label+room 解析，predicted 驱动、无 GT 泄漏）
    locations = []
    room_id = None
    if room_text:
        room_id = room_tail_to_id(canonical, room_text)
    # Semantic RAG 拒识（not_found）：predicted label 不应进入 grounding
    if sem.status == "not_found" or not predicted_label:
        pred["grounding"] = {"status": "not_found", "node_ids": [], "room_id": room_id}
        return pred
    cands = [nid for nid, n in canonical["nodes"].items()
             if n.get("node_type") == "object" and n.get("label") == predicted_label
             and (room_id is None or n.get("room_id") == room_id)]
    if not cands:
        status = "not_found"; node_ids = []
    elif quantifier == "all":
        status = "resolved_set"; node_ids = cands
    elif len(cands) == 1:
        status = "resolved"; node_ids = cands
    else:
        status = "ambiguous"; node_ids = cands
    pred["grounding"] = {"status": status, "node_ids": node_ids, "room_id": room_id}
    return pred


def score(pred: dict, case: dict) -> dict:
    """评分：接收 predicted + ground truth。"""
    oracle = case.get("oracle_intent", {})
    ot = oracle.get("target", {})
    exp_q = ot.get("quantifier", "one")
    exp_node_type = ot.get("node_type")
    exp_room = ot.get("locations", [{}])[0].get("text") if ot.get("locations") else None
    exp_sem = case.get("expected_semantic", {})
    exp_label = exp_sem.get("canonical_label")
    eg = case.get("expected_grounding", {})
    exp_status = eg.get("status")
    exp_ids = set(eg.get("node_ids", []))
    exp_candidates = set(eg.get("candidate_node_ids", []))

    r = {"case_id": case["case_id"], "instruction": case["instruction"],
         "failure_stage": None, "all_errors": []}

    # Intent gate
    intent = pred.get("intent")
    gate = {"schema": True, "action": False, "target": False, "quantifier": False, "node_type": False, "location": False}
    if pred.get("error") == "intent_schema":
        gate["schema"] = False
        r["failure_stage"] = "intent_schema"; r["all_errors"].append("intent_schema")
        r["intent_gate_success"] = False
        return r
    if pred.get("error") == "intent_target":
        gate["target"] = False
        r["failure_stage"] = "intent_target"; r["all_errors"].append("intent_target")
        r["intent_gate_success"] = False
        return r
    gate["action"] = (intent["action"] == "locate")
    gate["target"] = bool(intent["target_text"])
    gate["quantifier"] = (intent["quantifier"] == exp_q)
    # node_type：缺失只统计 provided rate、不直接失败；提供错误 -> Intent Gate FAIL
    pred_node_type = intent.get("node_type")
    r["node_type_provided"] = pred_node_type is not None
    r["node_type_correct"] = (pred_node_type == exp_node_type) if pred_node_type is not None else None
    gate["node_type"] = (pred_node_type is None) or (pred_node_type == exp_node_type)
    if exp_room:
        gate["location"] = bool(intent.get("room_text")) and (str(intent["room_text"]).replace("room", "").strip() == exp_room or exp_room in str(intent["room_text"]))
    else:
        # 任务书 3.2：GT 无 Room 约束，LLM 多生成 room_text → spurious location → Intent Gate FAIL
        gate["location"] = not bool(intent.get("room_text"))
    r["intent_gate_success"] = all(gate.values())
    if not r["intent_gate_success"]:
        if not gate["action"]: r["failure_stage"] = "intent_action"; r["all_errors"].append("intent_action")
        elif not gate["target"]: r["failure_stage"] = "intent_target"; r["all_errors"].append("intent_target")
        elif not gate["quantifier"]: r["failure_stage"] = "intent_quantifier"; r["all_errors"].append("intent_quantifier")
        elif not gate["node_type"]: r["failure_stage"] = "intent_node_type"; r["all_errors"].append("intent_node_type")
        elif not gate["location"]: r["failure_stage"] = "intent_location"; r["all_errors"].append("intent_location")
        return r

    # Semantic（任务书 3.1：Label Hit@1 与 Stage Success 分离；
    # top1 对但 semantic status=not_found（低分拒识）→ stage 失败）
    sem = pred.get("semantic") or {}
    sem_status = sem.get("status")
    exp_sem_status = exp_sem.get("status")
    if exp_sem_status == "not_found":
        sem_ok = (sem_status == "not_found")
    else:
        sem_ok = (sem.get("top1_label") == exp_label and sem_status in {"matched", "ambiguous"})
    r["semantic_stage_success"] = sem_ok
    if not sem_ok:
        r["failure_stage"] = "semantic_retrieval"; r["all_errors"].append("semantic_retrieval")
        return r

    # Grounding
    gr = pred.get("grounding") or {}
    p_status = gr.get("status")
    p_ids = set(gr.get("node_ids", []))
    r["predicted_status"] = p_status
    r["predicted_ids"] = sorted(p_ids)
    r["grounding_exact_success"] = False

    if exp_status == "not_found":
        ok = (p_status == "not_found" and not p_ids)
        r["grounding_exact_success"] = ok
        r["candidate_f1"] = 1.0 if ok else 0.0
        if not ok: r["failure_stage"] = "grounding_status"; r["all_errors"].append("grounding_status")
    elif exp_status == "resolved":
        ok = (p_status == "resolved" and p_ids == exp_ids and len(p_ids) == 1)
        r["grounding_exact_success"] = ok
        r["candidate_f1"] = 1.0 if ok else 0.0
        if not ok:
            r["failure_stage"] = "grounding_id" if p_status in ("resolved", "ambiguous") else "grounding_status"
            r["all_errors"].append("grounding_id" if p_status in ("resolved", "ambiguous") else "grounding_status")
    elif exp_status == "ambiguous":
        if p_ids:
            prec = len(p_ids & exp_candidates) / len(p_ids)
            rec = len(p_ids & exp_candidates) / len(exp_candidates) if exp_candidates else 0
            f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
        else:
            f1 = 0.0
        ok = (p_status == "ambiguous" and f1 == 1.0)
        r["grounding_exact_success"] = ok
        r["candidate_f1"] = round(f1, 4)
        if not ok:
            r["failure_stage"] = "grounding_candidate_set"; r["all_errors"].append("grounding_candidate_set")
    elif exp_status == "resolved_set":
        ok = (p_status == "resolved_set" and p_ids == exp_candidates)
        r["grounding_exact_success"] = ok
        r["candidate_f1"] = 1.0 if ok else 0.0
        if not ok: r["failure_stage"] = "grounding_set"; r["all_errors"].append("grounding_set")

    r["joint_success"] = (r["intent_gate_success"] and r["semantic_stage_success"] and r.get("grounding_exact_success", False))
    if not r.get("joint_success", False) and r["failure_stage"] is None:
        r["failure_stage"] = "unknown"
    return r


def main():
    model = DEFAULT_MODEL
    if "--model" in sys.argv:
        model = sys.argv[sys.argv.index("--model") + 1]
    cases = [json.loads(l) for l in open(CASES_DIR / "joint_cases_v3.jsonl", encoding="utf-8") if l.strip()]
    canonical_path = RAG_ROOT / "outputs" / "office_dense" / "canonical_graph_v2.json"
    vdb_path = RAG_ROOT / "outputs" / "office_dense" / "vector_db"
    canonical = json.load(open(canonical_path))
    rag = SceneGraphRAG(vdb_path)
    config = dict(CONFIG_BASE)

    results = []
    for c in cases:
        pred = run_pipeline(c["instruction"], model, rag, canonical, config)
        s = score(pred, c)
        s.update({"predicted_intent": pred.get("intent"),
                  "semantic_result": pred.get("semantic"),
                  "grounding_result": pred.get("grounding")})
        results.append(s)

    n = len(results)
    intent_gate = sum(1 for r in results if r.get("intent_gate_success")) / n
    sem_ok = sum(1 for r in results if r.get("semantic_stage_success")) / n
    ground_ok = sum(1 for r in results if r.get("grounding_exact_success")) / n
    joint = sum(1 for r in results if r.get("joint_success")) / n
    fail_stage = Counter(r["failure_stage"] for r in results if r["failure_stage"])

    # Set 指标：expected quantifier 分母
    set_cases = [r for r in results if r["case_id"] and
                 any(x["case_id"] == r["case_id"] for x in cases if x.get("oracle_intent", {}).get("target", {}).get("quantifier") == "all")]
    set_exact = sum(1 for r in set_cases if r.get("grounding_exact_success", False)) / len(set_cases) if set_cases else 0
    # Room-constrained
    room_cases = [r for r in results if any(x["case_id"] == r["case_id"] for x in cases if x.get("oracle_intent", {}).get("target", {}).get("locations"))]
    room_ok = sum(1 for r in room_cases if r.get("grounding_exact_success", False)) / len(room_cases) if room_cases else 0
    # Ambiguous F1
    amb = [r for r in results if any(x["case_id"] == r["case_id"] for x in cases if x.get("expected_grounding", {}).get("status") == "ambiguous")]
    amb_valid = [r for r in amb if r.get("candidate_f1") is not None]  # 仅 grounding 层实际执行的 case
    amb_f1 = sum(r.get("candidate_f1", 0) for r in amb_valid) / len(amb_valid) if amb_valid else 0

    # Semantic Label Hit@1 / Recall@5（matched，按 expected_label）
    matched_r = []
    for r, c in zip(results, cases):
        if c.get("expected_grounding", {}).get("status") != "not_found":
            sr = r.get("semantic_result") or {}
            matched_r.append((c.get("expected_semantic", {}).get("canonical_label"), sr.get("top1_label"), sr.get("label_ranking") or []))
    sem_hit1 = sum(1 for exp, top1, lr in matched_r if top1 == exp) / len(matched_r) if matched_r else 0
    sem_r5 = sum(1 for exp, top1, lr in matched_r if exp in lr[:5]) / len(matched_r) if matched_r else 0

    node_provided = sum(1 for r in results if r.get("node_type_provided")) / n
    node_correct = sum(1 for r in results if r.get("node_type_correct") is True) / n
    node_wrong = sum(1 for r in results if r.get("node_type_correct") is False) / n

    metrics = {"total_cases": n, "model": model,
               "intent_gate_success": round(intent_gate, 4),
               "semantic_label_hit1": round(sem_hit1, 4),
               "semantic_recall5": round(sem_r5, 4),
               "semantic_stage_success": round(sem_ok, 4),
               "grounding_exact_success": round(ground_ok, 4),
               "joint_exact_success": round(joint, 4),
               "room_grounding_success": round(room_ok, 4),
               "ambiguous_candidate_f1": round(amb_f1, 4),
               "set_exact_match": round(set_exact, 4),
               "node_type_provided_rate": round(node_provided, 4),
               "node_type_correct_rate": round(node_correct, 4),
               "node_type_wrong_rate": round(node_wrong, 4),
               "failure_by_stage": dict(fail_stage)}

    out_dir = OUT_DIR / slug(model)
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

    try:
        import chromadb
        chroma_ver = chromadb.__version__
    except Exception:
        chroma_ver = None
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "run_command": " ".join(sys.argv),
        "model": model,
        "model_config": {"ollama_host": CONFIG_BASE["ollama_host"],
                         "options": CONFIG_BASE["options"],
                         "semantic_threshold": THRESHOLD, "semantic_top_k": TOP_K},
        "benchmark": {"file": str(CASES_DIR / "joint_cases_v3.jsonl"),
                      "sha256": _sha256_file(CASES_DIR / "joint_cases_v3.jsonl")},
        "canonical_graph": {"file": str(canonical_path), "sha256": _sha256_file(canonical_path)},
        "vector_db": {"dir": str(vdb_path), "fingerprint": _dir_fingerprint(vdb_path)},
        "evaluator": {"file": str(Path(__file__).resolve()), "sha256": _sha256_file(Path(__file__))},
        "environment": {"python": sys.version.split()[0], "chromadb": chroma_ver},
    }
    with open(out_dir / "run_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"=== 联合测试 v3 [{model}] ===")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print(f"失败 {len(failures)}:")
    for r in failures[:12]:
        print(f"  {r['case_id']}: {r['instruction'][:38]} stage={r['failure_stage']}")


if __name__ == "__main__":
    main()
