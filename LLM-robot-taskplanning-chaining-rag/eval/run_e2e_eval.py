"""E2E 评估 runner（E2E端到端测试详细实施指导 第 11/13/20/21 节）。

真实生产模块串联（无 Oracle 泄漏）：
  NL -> Intent Parser -> Semantic RAG -> 确定性 Graph Grounding -> Simple Planner v3
      -> compile_simple_plan -> Validator -> Final TaskPlan

run_e2e(instruction, model, config) 不接收任何 expected_* 字段。
evaluate_e2e(pred, case) 对照 Ground Truth 分阶段评分。

Grounding 规则（确定性，复用 joint v3 已验证逻辑）：
- room 目标（target.text 含 room / node_type=room）：room_tail_to_id
- object 目标：Semantic top1 label + room 过滤 -> resolved/ambiguous/not_found
- source/destination：room_tail_to_id

已知架构限制（如实反映，不改系统）：
- agents 无语义标签 -> escort actor 无法 Semantic 定位
- avoid/prefer 约束未被 Intent Parser 解析 -> 约束无法从 NL 传递
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path

LLM_ROOT = Path(__file__).resolve().parent.parent
RAG_ROOT = LLM_ROOT.parent / "RAG" / "khronos_3dsg_rag"
sys.path.insert(0, str(RAG_ROOT / "src"))
sys.path.insert(0, str(LLM_ROOT))
sys.path.insert(0, str(LLM_ROOT / "eval"))

from semantic_retriever import search_semantic  # noqa: E402
from llm.intent_parser import parse_intent_with_llm  # noqa: E402
from compile_simple_plan import compile_simple_plan  # noqa: E402
from main_rag import _validate_grounded_plan, get_ollama_host  # noqa: E402
from grounding.orchestrator import GroundingResult  # noqa: E402
from api_schemas import ResolutionResult, ResolutionCandidate  # noqa: E402
from planner_schemas.intent import EntityReference  # noqa: E402
from run_planner_simple_eval import build_simple_prompt, generate_simple_plan, build_mock_grounding  # noqa: E402

DEFAULT_MODEL = "granite4.1:3b"
THRESHOLD = 0.35
TOP_K = 5
CASES_DIR = Path(__file__).resolve().parent / "cases"
OUT_BASE = Path(__file__).resolve().parent / "results" / "e2e_v1"
CONFIG_BASE = {"ollama_host": "http://127.0.0.1:11434", "options": {"temperature": 0, "num_ctx": 8192}}
GATE_STATUSES = {"ambiguous", "not_found", "unsupported"}


def slug(m: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", m.lower()).strip("_")


def room_tail_to_id(canonical: dict, tail: str) -> str | None:
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
    if t is not None:
        if getattr(t, "room", None):
            return str(t.room)
        for loc in getattr(t, "locations", []) or []:
            if loc.node_type == "room" and (loc.text or loc.node_id):
                return loc.text or str(loc.node_id)[-4:]
        # granite 常把 room 合进 target.text（如 "sign in room 0756"）
        if getattr(t, "text", None):
            m = re.search(r"room\s*(\d{3,})", str(t.text), re.I)
            if m:
                return m.group(1)
    return None


def _role_text(ref) -> str | None:
    if ref is None:
        return None
    return getattr(ref, "text", None) or None


def _clean_target_text(tt: str) -> str:
    """去掉 target.text 中的 room 子串（granite 常把 room 合进 target.text），保留对象短语。"""
    s = re.sub(r"\s+in\s+room\s+\d+", "", str(tt), flags=re.I)
    s = re.sub(r"\s+room\s+\d+", "", s, flags=re.I)
    return s.strip()


def _norm_room(text) -> str | None:
    """提取 room 数字（三审 5.1：role value 匹配用）；无数字返回 None。"""
    if text is None:
        return None
    m = re.search(r"(\d{3,})", str(text))
    return m.group(1) if m else None


def _node_ref(canonical: dict, node_id: str | None, node_type: str | None) -> dict | None:
    if not node_id or node_id not in canonical["nodes"]:
        return None
    n = canonical["nodes"][node_id]
    return {"node_id": node_id, "node_type": n.get("node_type") or node_type or "unknown",
            "label": n.get("label") or ""}


def run_e2e(instruction: str, model: str, config: dict, canonical: dict) -> dict:
    """预测路径：不接收任何 expected_* 字段。"""
    pred = {"intent": None, "semantic": None, "grounding": None, "planner": None,
            "compiled": None, "error": None, "latency": {}}
    t_all = time.time()

    # Stage 1: Intent
    t0 = time.time()
    intent = parse_intent_with_llm(instruction, model=model, config=config)
    pred["latency"]["intent_ms"] = (time.time() - t0) * 1000
    if intent is None:
        pred["error"] = "intent_schema"
        return pred
    pred["intent"] = intent
    if intent.action == "unsupported":
        pred["grounding"] = {"status": "unsupported", "node_ids": [], "candidate_node_ids": []}
        pred["latency"]["total_ms"] = (time.time() - t_all) * 1000
        return pred

    target = intent.target
    target_text = target.text if target else None
    quantifier = (target.quantifier if target else None) or "one"
    room_text = extract_room_text(intent)
    is_room_target = bool(target and (target.node_type == "room"
                                      or (target_text and re.match(r"^\s*(?:the\s+)?room\s*\d", str(target_text), re.I))))

    # Stage 2: Semantic RAG (object target)
    sem = {"query": target_text, "status": None, "top1_label": None, "label_ranking": []}
    predicted_label = None
    if target_text and not is_room_target:
        t0 = time.time()
        q = _clean_target_text(target_text) or target_text
        s = search_semantic(q, top_k=TOP_K, db_dir=RAG_ROOT / "outputs" / "office_dense" / "vector_db",
                            threshold=THRESHOLD)
        pred["latency"]["semantic_ms"] = (time.time() - t0) * 1000
        labels = [x.label for x in s.label_ranking]
        sem = {"query": q, "status": s.status, "top1_label": labels[0] if labels else None,
               "label_ranking": labels[:5]}
        predicted_label = labels[0] if labels else None
    pred["semantic"] = sem

    # Stage 3: Deterministic Grounding
    t0 = time.time()
    room_id = room_tail_to_id(canonical, room_text) if room_text else None
    grounding = {"status": None, "node_ids": [], "candidate_node_ids": [], "room_id": room_id}
    refs = {}

    src_id = room_tail_to_id(canonical, _role_text(intent.source)) if intent.source is not None else None
    dst_id = room_tail_to_id(canonical, _role_text(intent.destination)) if intent.destination is not None else None
    if src_id or dst_id:
        # source/destination 导航：room 目标（确定性，不靠 semantic）
        if src_id:
            refs["source"] = _node_ref(canonical, src_id, "room")
        if dst_id:
            refs["destination"] = _node_ref(canonical, dst_id, "room")
        grounding["status"] = "resolved"
        grounding["node_ids"] = [dst_id] if dst_id else [src_id]
        grounding["candidate_node_ids"] = [dst_id] if dst_id else [src_id]
    elif is_room_target:
        # room 目标：granite 常把 room 编号放进 target.text 而非 room 字段 → 从 target.text 提取
        if room_id is None and target_text:
            m = re.search(r"room\s*(\d{3,})", str(target_text), re.I)
            if m:
                room_id = room_tail_to_id(canonical, m.group(1))
        if room_id:
            grounding["status"] = "resolved"
            grounding["node_ids"] = [room_id]
            grounding["candidate_node_ids"] = [room_id]
            refs["target"] = _node_ref(canonical, room_id, "room")
        else:
            grounding["status"] = "not_found"
    elif sem.get("status") == "not_found" or not predicted_label:
        grounding["status"] = "not_found"
    else:
        cands = [nid for nid, n in canonical["nodes"].items()
                 if n.get("node_type") == "object" and n.get("label") == predicted_label
                 and (room_id is None or n.get("room_id") == room_id)]
        grounding["candidate_node_ids"] = cands
        if not cands:
            grounding["status"] = "not_found"
        elif len(cands) == 1:
            grounding["status"] = "resolved"
            grounding["node_ids"] = cands
            refs["target"] = _node_ref(canonical, cands[0], "object")
        else:
            grounding["status"] = "ambiguous"
    pred["latency"]["grounding_ms"] = (time.time() - t0) * 1000

    pred["grounding"] = grounding
    pred["latency"]["grounding_ms"] = pred["latency"].get("grounding_ms", 0)

    # Stage 4: Planner gate（仅 resolved 调用 planner）
    if grounding["status"] in GATE_STATUSES or grounding["status"] is None:
        pred["planner"] = {"called": False, "final_symbolic_plan": None}
        pred["latency"]["total_ms"] = (time.time() - t_all) * 1000
        return pred

    grounded_task = {"action": "navigate", "description": instruction, "references": refs, "constraints": []}
    if not refs:
        pred["planner"] = {"called": False, "final_symbolic_plan": None}
        pred["error"] = "grounding_no_refs"
        pred["latency"]["total_ms"] = (time.time() - t_all) * 1000
        return pred

    # Stage 5: Simple Planner v3
    t0 = time.time()
    planner = {"called": True, "first_pass_raw": None, "first_pass_symbolic_plan": None,
               "repair_used": False, "final_symbolic_plan": None}
    draft = None
    errors = []
    try:
        draft = generate_simple_plan(instruction, grounded_task, model, config)
        planner["first_pass_raw"] = json.loads(draft.model_dump_json())
    except Exception as e:
        try:
            draft = generate_simple_plan(instruction, grounded_task, model, config, errors=[str(e)])
            planner["repair_used"] = True
        except Exception as e2:
            pred["error"] = f"planner_schema: {e2}"
            pred["planner"] = planner
            pred["latency"]["planner_ms"] = (time.time() - t0) * 1000
            pred["latency"]["total_ms"] = (time.time() - t_all) * 1000
            return pred
    pred["latency"]["planner_ms"] = (time.time() - t0) * 1000
    planner["final_symbolic_plan"] = json.loads(draft.model_dump_json()) if draft else None
    pred["planner"] = planner

    # Stage 6: Deterministic Compiler
    plan = compile_simple_plan(draft, grounded_task)
    pred["compiled"] = json.loads(plan.model_dump_json())

    # Stage 7: Validator
    mock = build_mock_grounding(grounded_task)
    errors = _validate_grounded_plan(plan, mock, grounded_task=grounded_task)
    pred["validation_errors"] = errors

    pred["latency"]["total_ms"] = (time.time() - t_all) * 1000
    return pred


SCORER_VERSION = "office_e2e_role_values_v2"
EVALUATION_SCOPE = "restricted_office"


def evaluate_e2e(pred: dict, case: dict) -> dict:
    """对照 Ground Truth 评分。接收 predicted + ground truth（仅评分用）。"""
    r = {"case_id": case["case_id"], "category": case.get("category"), "instruction": case["instruction"],
         "failure_stage": None, "all_errors": [], "stage_scores": {},
         "scorer_version": SCORER_VERSION, "evaluation_scope": EVALUATION_SCOPE}
    exp_intent = case.get("oracle_intent", {})
    exp_sem = case.get("expected_semantic")
    exp_ground = case.get("expected_grounding", {})
    exp_sym = case.get("expected_symbolic_plan")
    exp_compiled = case.get("expected_compiled_plan")
    exp_final = case.get("expected_final_status")

    intent = pred.get("intent")
    # ---- Intent ----
    g = {}
    if pred.get("error") == "intent_schema":
        g["intent_success"] = False
        r["failure_stage"] = "intent_schema"
        r["all_errors"].append("intent_schema")
        r["stage_scores"] = g
        r["e2e_success"] = False
        return r
    exp_action = exp_intent.get("action", "navigate")
    g["intent_success"] = (intent.action == exp_action)
    # 角色检查（P0-2）：pred 是否多/少生成 source/destination（spurious role）
    g["role_ok"] = (bool(exp_intent.get("source")) == (getattr(intent, "source", None) is not None)) and \
                   (bool(exp_intent.get("destination")) == (getattr(intent, "destination", None) is not None))
    # 三审 5.1：role value 匹配（source/destination 的 room 值是否正确）
    g["role_value_ok"] = True
    for _role in ("source", "destination"):
        _exp = exp_intent.get(_role)
        _pred = getattr(intent, _role, None)
        if _exp:
            _ev = _norm_room(_exp.get("text", "") if isinstance(_exp, dict) else getattr(_exp, "text", ""))
            _pv = _norm_room(getattr(_pred, "text", "")) if _pred is not None else None
            if _ev and _ev != _pv:
                g["role_value_ok"] = False
    if intent.action == "unsupported" or exp_action == "unsupported":
        g["quantifier_ok"] = True
        g["location_ok"] = True
        g["role_ok"] = True
    else:
        et = exp_intent.get("target")
        if not et:
            # 无 target 动作（source_destination / escort）：quantifier/location 不适用
            g["quantifier_ok"] = True
            g["location_ok"] = True
        else:
            exp_q = et.get("quantifier", "one")
            pred_q = intent.target.quantifier if intent.target else None
            g["quantifier_ok"] = (pred_q == exp_q)
            exp_room = et.get("locations", [{}])[0].get("text") if et.get("locations") else et.get("room")
            pred_room = extract_room_text(intent)
            if exp_room:
                g["location_ok"] = bool(pred_room) and (str(exp_room).replace("room", "").strip() in str(pred_room)
                                                        or str(pred_room).replace("room", "").strip() in str(exp_room))
            else:
                g["location_ok"] = not bool(pred_room)  # spurious location 处罚
    if not (g["intent_success"] and g["quantifier_ok"] and g["role_ok"] and g["role_value_ok"] and g["location_ok"]):
        r["stage_scores"]["intent_success"] = False
        r["stage_scores"]["quantifier_ok"] = g["quantifier_ok"]
        r["stage_scores"]["location_ok"] = g["location_ok"]
        r["stage_scores"]["role_ok"] = g["role_ok"]
        r["stage_scores"]["role_value_ok"] = g["role_value_ok"]
        if not g["intent_success"]:
            r["failure_stage"] = "intent_action"
        elif not g["quantifier_ok"]:
            r["failure_stage"] = "intent_quantifier"
        elif not g["role_ok"]:
            r["failure_stage"] = "intent_role"
        elif not g["role_value_ok"]:
            r["failure_stage"] = "intent_role_value"
        else:
            r["failure_stage"] = "intent_location"
        r["all_errors"].append(r["failure_stage"])
        r["e2e_success"] = False
        return r
    r["stage_scores"]["intent_success"] = True
    r["stage_scores"]["quantifier_ok"] = g["quantifier_ok"]
    r["stage_scores"]["location_ok"] = g["location_ok"]
    r["stage_scores"]["role_ok"] = g["role_ok"]
    r["stage_scores"]["role_value_ok"] = g["role_value_ok"]

    # ---- Semantic ----
    sem = pred.get("semantic") or {}
    if exp_sem is not None:
        if exp_sem.get("status") == "not_found":
            sem_ok = (sem.get("status") == "not_found")
        else:
            sem_ok = (sem.get("top1_label") == exp_sem.get("canonical_label")
                      and sem.get("status") in {"matched", "ambiguous"})
    else:
        sem_ok = True  # 无语义目标（room 目标 / source_destination）
    r["stage_scores"]["semantic_success"] = sem_ok
    if not sem_ok:
        r["failure_stage"] = "semantic_retrieval"
        r["all_errors"].append("semantic_retrieval")
        r["e2e_success"] = False
        return r

    # ---- Grounding ----
    gr = pred.get("grounding") or {}
    exp_gs = exp_ground.get("status")
    exp_ids = set(exp_ground.get("node_ids", []))
    exp_cands = set(exp_ground.get("candidate_node_ids", []))
    p_ids = set(gr.get("node_ids", []))
    if exp_gs == "not_found":
        gr_ok = (gr.get("status") == "not_found" and not p_ids)
    elif exp_gs == "resolved":
        gr_ok = (gr.get("status") == "resolved" and p_ids == exp_ids and len(p_ids) == 1)
    elif exp_gs == "ambiguous":
        p_cands = set(gr.get("candidate_node_ids", []))
        f1 = None
        if p_cands or exp_cands:
            prec = len(p_cands & exp_cands) / len(p_cands) if p_cands else 0
            rec = len(p_cands & exp_cands) / len(exp_cands) if exp_cands else 0
            f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
        gr_ok = (gr.get("status") == "ambiguous" and f1 == 1.0)
        r["candidate_f1"] = f1
    else:
        gr_ok = (gr.get("status") == exp_gs)
    r["stage_scores"]["grounding_success"] = gr_ok
    if not gr_ok:
        r["failure_stage"] = "grounding_status" if gr.get("status") != exp_gs else "grounding_id"
        r["all_errors"].append(r["failure_stage"])
        r["e2e_success"] = False
        return r

    # ---- Status gate case：blocked，无 executable plan ----
    if exp_final in ("blocked", "ambiguous", "target_not_found", "unsupported"):
        planner = pred.get("planner") or {}
        blocked_ok = (not planner.get("called") and gr.get("status") in GATE_STATUSES)
        r["stage_scores"]["planner_success"] = True
        r["stage_scores"]["compiler_success"] = True
        r["stage_scores"]["validator_success"] = True
        r["stage_scores"]["blocked_ok"] = blocked_ok
        r["e2e_success"] = blocked_ok
        # 三审 6.1：gate case 的 fidelity = 正确阻塞
        r["full_instruction_fidelity_success"] = bool(blocked_ok)
        if not blocked_ok:
            r["failure_stage"] = "grounding_status"
            r["all_errors"].append("blocked_required_but_planner_called")
        return r

    # ---- Planner / Compiler / Validator ----
    planner = pred.get("planner") or {}
    draft = planner.get("final_symbolic_plan")
    exp_actions = [s["action"] for s in exp_sym["steps"]] if exp_sym else []
    exp_refs = [s.get("ref") for s in exp_sym["steps"]] if exp_sym else []
    pred_actions = [s["action"] for s in draft["steps"]] if draft else []
    pred_refs = [s.get("ref") for s in draft["steps"]] if draft else []
    pl_ok = bool(draft) and draft.get("status") == exp_sym.get("status") and pred_actions == exp_actions
    if len(pred_refs) == len(exp_refs) and exp_refs:
        ref_acc = sum(1 for e, p in zip(exp_refs, pred_refs) if e == p) / len(exp_refs)
    else:
        ref_acc = 1.0 if not exp_refs and not pred_refs else 0.0
    r["stage_scores"]["planner_success"] = pl_ok and ref_acc == 1.0
    if not r["stage_scores"]["planner_success"]:
        r["failure_stage"] = "planner_action" if pred_actions != exp_actions else "planner_ref"
        r["all_errors"].append(r["failure_stage"])
        r["e2e_success"] = False
        return r

    compiled = pred.get("compiled")
    r["stage_scores"]["compiler_success"] = compiled is not None
    val_ok = not pred.get("validation_errors")
    r["stage_scores"]["validator_success"] = val_ok
    r["validation_errors"] = pred.get("validation_errors", [])
    if not val_ok:
        r["failure_stage"] = "validator"
        r["all_errors"].append("validator")
        r["e2e_success"] = False
        return r

    # compiled exact match（主指标，非 e2e_success 必需）
    if exp_compiled and compiled:
        ec = exp_compiled.get("steps", [])
        if len(ec) == len(compiled.get("task_chain", [])):
            r["compiled_exact_match"] = all(
                s.get("action") == ts["action"] and s.get("target_id") == ts.get("target_id")
                and s.get("executor") == ts.get("executor")
                and set(s.get("constraints", {}).get("avoid", [])) == set(ts.get("constraints", {}).get("avoid", []))
                and set(s.get("constraints", {}).get("prefer", [])) == set(ts.get("constraints", {}).get("prefer", []))
                for s, ts in zip(ec, compiled.get("task_chain", []))
            )
        else:
            r["compiled_exact_match"] = False
    else:
        r["compiled_exact_match"] = None

    # P0-3：e2e_success 强制 compiled_exact_match（到达编译阶段的 case 必须与 expected compiled 完全一致）
    exact = r.get("compiled_exact_match")
    r["e2e_success"] = bool(exact)
    if not exact:
        r["failure_stage"] = "compiler"
        r["all_errors"].append("compiled_exact_mismatch")
    # 三审 6.1：full-instruction fidelity（avoid/prefer/escort 约束/actor 未进入计划 → fidelity=false）
    cat = case.get("category", "")
    if cat in ("navigate_with_avoid", "navigate_with_prefer", "escort"):
        r["full_instruction_fidelity_success"] = False
        r["fidelity_failure_reason"] = ("constraint_not_propagated" if "constraint" in cat
                                        else "actor_not_grounded")
    else:
        r["full_instruction_fidelity_success"] = bool(exact)
    return r


def _cond(results, stage, prev):
    """条件成功率：前一阶段通过的前提下，本阶段通过的比例（P0-4）。"""
    prev_pass = [r for r in results if r["stage_scores"].get(prev)]
    if not prev_pass:
        return None
    return round(sum(1 for r in prev_pass if r["stage_scores"].get(stage)) / len(prev_pass), 4)


def run_eval(model: str, repeats: int, cases_path: Path, output_dir: Path):
    canonical = json.load(open(RAG_ROOT / "outputs" / "office_dense" / "canonical_graph_v2.json"))
    cases = [json.loads(l) for l in open(cases_path, encoding="utf-8") if l.strip()]
    config = dict(CONFIG_BASE)
    results = []
    for repeat in range(repeats):
        for case in cases:
            pred = run_e2e(case["instruction"], model, config, canonical)
            s = evaluate_e2e(pred, case)
            s["repeat"] = repeat
            s["latency"] = pred.get("latency", {})
            it = pred.get("intent")
            s["predicted_intent_summary"] = ({
                "action": it.action,
                "target_text": it.target.text if it.target else None,
                "room": extract_room_text(it),
                "quantifier": it.target.quantifier if it.target else None,
                "source": _role_text(it.source),
                "destination": _role_text(it.destination),
            }) if it else None
            semr = pred.get("semantic") or {}
            s["semantic_summary"] = {"top1": semr.get("top1_label"), "status": semr.get("status"),
                                     "query": semr.get("query")}
            s["predicted_intent_action"] = it.action if it else None
            s["predicted_grounding_status"] = (pred.get("grounding") or {}).get("status")
            s["predicted_symbolic"] = (pred.get("planner") or {}).get("final_symbolic_plan")
            s["compiled_plan"] = pred.get("compiled")
            s["error"] = pred.get("error")
            results.append(s)

    n = len(results)
    stages = ["intent_success", "semantic_success", "grounding_success",
              "planner_success", "compiler_success", "validator_success"]
    stage_rates = {st: sum(1 for r in results if r["stage_scores"].get(st)) / n for st in stages}
    e2e = sum(1 for r in results if r["e2e_success"]) / n
    fail_stage = {}
    for r in results:
        if r.get("failure_stage"):
            fail_stage[r["failure_stage"]] = fail_stage.get(r["failure_stage"], 0) + 1
    gate_cases = [r for r in results if r.get("category", "").endswith("_gate")]
    gate_ok = sum(1 for r in gate_cases if r["e2e_success"]) / len(gate_cases) if gate_cases else None

    summary = {
        "model": model, "repeats": repeats, "total_cases": n,
        "scorer_version": SCORER_VERSION, "evaluation_scope": EVALUATION_SCOPE,
        "e2e_strict_success": round(e2e, 4),
        "final_validation_pass": round(stage_rates["validator_success"], 4),
        "intent_gate_success": round(stage_rates["intent_success"], 4),
        "semantic_stage_success": round(stage_rates["semantic_success"], 4),
        "grounding_exact_success": round(stage_rates["grounding_success"], 4),
        "planner_strict_success": round(stage_rates["planner_success"], 4),
        "compiler_success": round(stage_rates["compiler_success"], 4),
        "validator_success": round(stage_rates["validator_success"], 4),
        "status_gate_accuracy": gate_ok,
        "failure_by_stage": fail_stage,
        # 三审 6.1：full-instruction fidelity（约束/actor 语义保真）
        "full_instruction_fidelity": round(
            sum(1 for r in results if r.get("full_instruction_fidelity_success")) / n, 4) if n else None,
        # P0-4：条件成功率（前一阶段通过的前提下）
        "conditional": {
            "semantic_given_intent": _cond(results, "semantic_success", "intent_success"),
            "grounding_given_semantic": _cond(results, "grounding_success", "semantic_success"),
            "planner_given_grounding": _cond(results, "planner_success", "grounding_success"),
            "compiler_given_planner": _cond(results, "compiler_success", "planner_success"),
            "validator_given_compiler": _cond(results, "validator_success", "compiler_success"),
        },
    }
    compiled_exact = [r for r in results if r.get("compiled_exact_match") is not None]
    if compiled_exact:
        summary["final_compiled_exact_match"] = round(
            sum(1 for r in compiled_exact if r["compiled_exact_match"]) / len(compiled_exact), 4)
    # ID safety
    bad_ids = sum(1 for r in results if r.get("error") or r.get("validation_errors"))
    summary["error_rate"] = round(sum(1 for r in results if r.get("error")) / n, 4)
    total_lat = [r.get("latency", {}).get("total_ms", 0) for r in results]
    summary["avg_total_latency_ms"] = round(sum(total_lat) / len(total_lat), 1) if total_lat else None
    summary["failure_count"] = sum(1 for r in results if r.get("failure_stage"))

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "per_case_results.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    failures = [r for r in results if r.get("failure_stage")]
    with open(output_dir / "failures.jsonl", "w", encoding="utf-8") as f:
        for r in failures:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    with open(output_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    _write_manifest(output_dir, model, repeats, cases_path)

    print(f"=== E2E [{model}] ===")
    for k, v in summary.items():
        if v is not None:
            print(f"  {k}: {v}")
    return summary


def _write_manifest(output_dir: Path, model: str, repeats: int, cases_path: Path):
    manifest = {
        "created_at": "2026-08-06",
        "run_command": f"python -m eval.e2e.run_e2e_eval --model {model} --repeats {repeats} --cases {cases_path.name}",
        "model": model, "repeats": repeats,
        "benchmark": {"file": str(cases_path), "sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest()},
        "evaluator": {"file": str(Path(__file__).resolve()), "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "compiler": {"file": "compile_simple_plan.py", "sha256": hashlib.sha256(
            (LLM_ROOT / "compile_simple_plan.py").read_bytes()).hexdigest()},
        "intent_parser": {"file": "llm/intent_parser.py"},
        "semantic_threshold": THRESHOLD, "semantic_top_k": TOP_K,
        "environment": {"python": "3.12.13 (llm-task-planner)", "chromadb": "1.5.9"},
    }
    with open(output_dir / "run_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--cases", default="e2e_dev_v1.jsonl")
    ap.add_argument("--output")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    cases_path = CASES_DIR / args.cases
    out = Path(args.output) if args.output else OUT_BASE / slug(args.model)
    run_eval(args.model, args.repeats, cases_path, out)


if __name__ == "__main__":
    main()
