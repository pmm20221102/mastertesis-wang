"""验证 planner_benchmark_50_v3_simple.jsonl（Planner阶段全面审查 第 21 节 benchmark 项）。

检查：case 数 50、ID 唯一、类别分布、oracle IDs 真实、
expected_symbolic_plan（status/action/ref 合法、无 raw node id）、
status gate 无可执行计划、expected_compiled_plan（id 真实、executor 正确、avoid 真实、prefer 为 policy）、
source→destination 指令明确、无重复 locate、instruction 不暴露内部 label、manifest hash。
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

LLM_ROOT = Path(__file__).resolve().parents[3] / "llm-rag" / "planner"
RAG_ROOT = LLM_ROOT.parent / "RAG" / "khronos_3dsg_rag"
CANONICAL = RAG_ROOT / "outputs" / "office_dense" / "canonical_graph_v2.json"
CASES_PATH = Path(__file__).resolve().parent / "cases" / "planner_benchmark_50_v3_simple.jsonl"
MANIFEST_PATH = Path(__file__).resolve().parent / "cases" / "planner_benchmark_50_v3_simple.manifest.json"

sys.path.insert(0, str(LLM_ROOT))
from schema import AllowedAction, AllowedStatus, ACTION_EXECUTOR_MAP  # noqa: E402

EXPECTED_CATEGORIES = {
    "single_navigate": 12, "ordered_source_destination": 10, "escort": 8,
    "navigate_with_avoid": 6, "navigate_with_prefer": 4, "return_to_base": 4,
    "ambiguous_gate": 2, "not_found_gate": 2, "unsupported_gate": 2,
}
ALLOWED_SIMPLE_ACTIONS = {"navigate_to", "escort_to", "wait", "return_to_base"}
ALLOWED_STATUS = set(AllowedStatus.__args__)
VALID_ROLES = {"actor", "target", "source", "destination"}
GATE_STATUSES = {"ambiguous", "target_not_found", "unsupported"}


def validate():
    canonical = json.load(open(CANONICAL, encoding="utf-8"))
    all_ids = {str(nid) for nid in canonical["nodes"].keys()}
    cases = [json.loads(l) for l in open(CASES_PATH, encoding="utf-8") if l.strip()]
    errors = []

    if len(cases) != 50:
        errors.append(f"case 数 {len(cases)} != 50")
    cids = [x["case_id"] for x in cases]
    if len(cids) != len(set(cids)):
        errors.append("case_id 重复")
    cat = Counter(x["category"] for x in cases)
    if dict(cat) != EXPECTED_CATEGORIES:
        errors.append(f"类别分布不符: {dict(cat)}")

    for x in cases:
        cid = x["case_id"]
        og = x.get("oracle_grounding", {})
        es = x.get("expected_symbolic_plan", {})
        ec = x.get("expected_compiled_plan")

        # instruction 不暴露内部 label 格式
        if "room room" in x["instruction"].lower():
            errors.append(f"{cid}: instruction 含 'room room' 内部 label")
        # 不允许重复 locate（仅对会调用 Planner 的可执行类别；status gate 不调用 Planner）
        exec_cats = {"single_navigate", "ordered_source_destination", "escort",
                     "navigate_with_avoid", "navigate_with_prefer", "return_to_base"}
        if x["category"] in exec_cats and any(w in x["instruction"].lower() for w in ("find", "locate")):
            errors.append(f"{cid}: 含 find/locate（Grounding 已完成，不应重复）")

        # oracle IDs 真实
        for role in VALID_ROLES:
            ref = og.get(role)
            if ref and ref.get("node_id") and str(ref["node_id"]) not in all_ids:
                errors.append(f"{cid}: oracle {role} node_id 不存在")
        for cstr in og.get("constraints", []):
            if isinstance(cstr, dict) and cstr.get("type") == "avoid":
                if str(cstr["node_id"]) not in all_ids:
                    errors.append(f"{cid}: avoid node_id 不存在")

        # expected_symbolic_plan
        status = es.get("status")
        if status not in ALLOWED_STATUS:
            errors.append(f"{cid}: 非法 symbolic status '{status}'")
        for s in es.get("steps", []):
            if s.get("action") not in ALLOWED_SIMPLE_ACTIONS:
                errors.append(f"{cid}: 非法 simple action '{s.get('action')}'")
            ref = s.get("ref")
            if s["action"] in ("navigate_to", "escort_to"):
                if not ref or ref not in VALID_ROLES:
                    errors.append(f"{cid}: {s['action']} 缺/非法 ref '{ref}'")
            elif ref:
                errors.append(f"{cid}: {s['action']} 不应有 ref")
            # no raw node id in symbolic plan
            if ref and str(ref).isdigit():
                errors.append(f"{cid}: symbolic plan 含 raw node id '{ref}'")

        # status gate：无可执行计划
        if status in GATE_STATUSES:
            if es.get("steps"):
                errors.append(f"{cid}: status={status} 但 symbolic 含 steps")
            if ec and ec.get("steps"):
                errors.append(f"{cid}: status={status} 但 compiled 含 steps")

        # expected_compiled_plan
        if ec:
            if ec.get("status") != "valid":
                errors.append(f"{cid}: compiled status 应 valid")
            for s in ec.get("steps", []):
                if s.get("executor") != ACTION_EXECUTOR_MAP.get(s["action"]):
                    errors.append(f"{cid}: compiled executor 与 map 不符")
                tid = s.get("target_id")
                if tid and str(tid) not in all_ids:
                    errors.append(f"{cid}: compiled target_id {tid} 不存在")
                for aid in s.get("constraints", {}).get("avoid", []):
                    if str(aid) not in all_ids:
                        errors.append(f"{cid}: compiled avoid_id {aid} 不存在")
                # prefer 必须是 policy（非 node id）
                for p in s.get("constraints", {}).get("prefer", []):
                    if str(p).isdigit():
                        errors.append(f"{cid}: prefer 被当成 node id '{p}'")

        # source→destination 指令明确
        if x["category"] == "ordered_source_destination":
            low = x["instruction"].lower()
            if not (low.startswith("first") and "then" in low):
                errors.append(f"{cid}: source_destination 指令未明确 First...then...")

    manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
    h = hashlib.sha256(CASES_PATH.read_bytes()).hexdigest()
    if manifest.get("benchmark_file_sha256") != h:
        errors.append("manifest hash 不符")

    if errors:
        print(f"校验失败（{len(errors)}）:")
        for e in errors[:30]:
            print("  -", e)
        sys.exit(1)
    print(f"VALIDATION PASSED（50 条，类别分布 {dict(cat)}，hash {h}）")


if __name__ == "__main__":
    validate()
