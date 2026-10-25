"""E2E 集成测试（E2E端到端测试详细实施指导 第 27 节）。

防泄漏：run_e2e 只接收 instruction + model + config + canonical，无 expected 字段。
Status gate：ambiguous/not_found/unsupported 不调用 planner。
评分：spurious location → intent 失败；e2e_success 需要所有阶段。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

LLM_ROOT = Path(__file__).resolve().parents[3] / "llm-rag" / "planner"
sys.path.insert(0, str(LLM_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "experiment" / "evaluations" / "taskchain"))

import pytest  # noqa: E402

from run_e2e_eval import run_e2e, evaluate_e2e  # noqa: E402


# ---------- 防泄漏 ----------

def test_e2e_prediction_receives_only_instruction():
    src = Path(Path(__file__).resolve().parents[3] / "experiment" / "evaluations" / "taskchain" / "run_e2e_eval.py").read_text()
    tree = ast.parse(src)
    sig = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_e2e":
            sig = node
    args = [a.arg for a in sig.args.args] if sig else []
    assert "instruction" in args
    # 不允许 expected / oracle 字段进入预测路径
    assert not any("expected" in a or "oracle" in a for a in args), f"泄漏字段: {args}"


def test_no_expected_label_in_prediction_path():
    src = Path(Path(__file__).resolve().parents[3] / "experiment" / "evaluations" / "taskchain" / "run_e2e_eval.py").read_text()
    tree = ast.parse(src)
    r = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_e2e")
    names = {n.id for n in ast.walk(r) if isinstance(n, ast.Name)}
    assert not any("expected" in x for x in names), f"预测路径引用了 expected 字段: {[x for x in names if 'expected' in x]}"


# ---------- Status gate：不调用 planner ----------

def _make_case(status, final):
    return {"case_id": "t1", "category": f"{status}_gate", "instruction": "x",
            "oracle_intent": {"action": "navigate", "target": {"text": "x"}},
            "expected_semantic": None,
            "expected_grounding": {"status": status, "node_ids": [], "candidate_node_ids": []},
            "expected_symbolic_plan": {"status": final, "steps": []},
            "expected_compiled_plan": None, "expected_final_status": final}


def test_ambiguous_does_not_call_planner():
    pred = {"error": None, "intent": type("I", (), {"action": "navigate",
                                       "target": type("T", (), {"text": "x", "quantifier": "one", "room": None})()})(),
            "semantic": {"status": "matched", "top1_label": "chairs", "label_ranking": ["chairs"], "query": "chair"},
            "grounding": {"status": "ambiguous", "node_ids": [], "candidate_node_ids": ["a", "b"]},
            "planner": {"called": False}, "compiled": None, "validation_errors": []}
    case = _make_case("ambiguous", "ambiguous")
    case["expected_grounding"]["candidate_node_ids"] = ["a", "b"]  # 候选集与 pred 一致
    r = evaluate_e2e(pred, case)
    assert r["stage_scores"].get("blocked_ok") is True
    assert r["e2e_success"] is True


def test_not_found_does_not_call_planner():
    pred = {"error": None, "intent": type("I", (), {"action": "navigate",
                                       "target": type("T", (), {"text": "x", "quantifier": "one", "room": None})()})(),
            "semantic": {"status": "not_found", "top1_label": None, "label_ranking": [], "query": "x"},
            "grounding": {"status": "not_found", "node_ids": [], "candidate_node_ids": []},
            "planner": {"called": False}, "compiled": None, "validation_errors": []}
    r = evaluate_e2e(pred, _make_case("not_found", "target_not_found"))
    assert r["stage_scores"].get("blocked_ok") is True


def test_unsupported_does_not_call_planner():
    pred = {"error": None, "intent": type("I", (), {"action": "unsupported", "target": None})(),
            "semantic": {}, "grounding": {"status": "unsupported", "node_ids": [], "candidate_node_ids": []},
            "planner": {"called": False}, "compiled": None, "validation_errors": []}
    case = _make_case("unsupported", "unsupported")
    case["oracle_intent"] = {"action": "unsupported"}
    case["expected_semantic"] = None
    r = evaluate_e2e(pred, case)
    assert r["stage_scores"].get("blocked_ok") is True


def test_blocked_case_has_no_executable_plan():
    # gate case 若 planner 被错误调用（called=True）→ 失败
    pred = {"error": None, "intent": type("I", (), {"action": "navigate",
                                       "target": type("T", (), {"text": "x", "quantifier": "one", "room": None})()})(),
            "semantic": {"status": "matched", "top1_label": "chairs", "label_ranking": [], "query": "x"},
            "grounding": {"status": "ambiguous", "node_ids": [], "candidate_node_ids": ["a"]},
            "planner": {"called": True}, "compiled": None, "validation_errors": []}
    r = evaluate_e2e(pred, _make_case("ambiguous", "ambiguous"))
    assert r["e2e_success"] is False


# ---------- 评分 ----------

def test_spurious_location_causes_intent_failure():
    # GT 无 location，LLM 多生成 room → intent_location 失败
    pred = {"error": None,
            "intent": type("I", (), {"action": "navigate",
                                     "target": type("T", (), {"text": "chair", "quantifier": "one", "room": "0756"})(),
                                     "source": None, "destination": None})(),
            "semantic": {}, "grounding": {}, "planner": {}, "compiled": None, "validation_errors": []}
    case = {"case_id": "t2", "category": "single_navigate", "instruction": "Go to the chair.",
            "oracle_intent": {"action": "navigate", "target": {"text": "chair", "quantifier": "one"}},
            "expected_semantic": None, "expected_grounding": {"status": "resolved", "node_ids": [], "candidate_node_ids": []},
            "expected_symbolic_plan": None, "expected_compiled_plan": None, "expected_final_status": "ready"}
    r = evaluate_e2e(pred, case)
    assert r["failure_stage"] == "intent_location"


def test_e2e_requires_all_stages():
    # planner 失败 → e2e_success False
    pred = {"error": None, "intent": type("I", (), {"action": "navigate",
                                       "target": type("T", (), {"text": "x", "quantifier": "one", "room": None})()})(),
            "semantic": {"status": "matched", "top1_label": "chairs", "label_ranking": [], "query": "x"},
            "grounding": {"status": "resolved", "node_ids": ["a"], "candidate_node_ids": ["a"]},
            "planner": {"called": True, "final_symbolic_plan": {"status": "valid", "steps": [{"action": "navigate_to", "ref": "source"}]}},
            "compiled": None, "validation_errors": []}
    case = {"case_id": "t3", "category": "single_navigate", "instruction": "x",
            "oracle_intent": {"action": "navigate", "target": {"text": "chair", "quantifier": "one"}},
            "expected_semantic": {"status": "matched", "canonical_label": "chairs"},
            "expected_grounding": {"status": "resolved", "node_ids": ["a"], "candidate_node_ids": ["a"]},
            "expected_symbolic_plan": {"status": "valid", "steps": [{"action": "navigate_to", "ref": "target"}]},
            "expected_compiled_plan": None, "expected_final_status": "ready"}
    r = evaluate_e2e(pred, case)
    assert r["failure_stage"] == "planner_ref"  # action 对但 ref 应为 target，模型给 source
    assert r["e2e_success"] is False


def test_final_ids_exist():
    # run_e2e 的 grounding 只用 canonical 真实节点（room_tail_to_id / canonical 查询）
    import json
    from run_e2e_eval import room_tail_to_id
    canonical = json.load(open(LLM_ROOT.parent / "RAG" / "khronos_3dsg_rag" / "outputs" / "office_dense"
                               / "canonical_graph_v2.json"))
    rid = room_tail_to_id(canonical, "0756")
    assert rid and rid in canonical["nodes"]
