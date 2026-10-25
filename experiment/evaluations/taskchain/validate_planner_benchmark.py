"""验证 planner_benchmark_50_v2.jsonl（任务书第 21 节）。

检查：case 数 50、ID 唯一、类别分布、expected actions/target_refs/executors 合法、
oracle grounded IDs 真实存在于 canonical、status gate case 无可执行计划、
multi-step refs 与 steps 一致、manifest hash。
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
CASES_PATH = Path(__file__).resolve().parent / "cases" / "planner_benchmark_50_v2.jsonl"
MANIFEST_PATH = Path(__file__).resolve().parent / "cases" / "planner_benchmark_50_v2.manifest.json"

sys.path.insert(0, str(LLM_ROOT))
from schema import AllowedAction, AllowedStatus, ExecutorType  # noqa: E402

EXPECTED_CATEGORIES = {
    "single_locate": 5, "single_navigate": 8, "locate_then_navigate": 7,
    "source_destination": 6, "escort": 5, "avoid_constraint": 4,
    "prefer_constraint": 3, "ambiguous_grounding": 3, "not_found_grounding": 3,
    "unsupported_action": 2, "multi_step": 4,
}
ALLOWED_ACTIONS = set(AllowedAction.__args__)
ALLOWED_STATUS = set(AllowedStatus.__args__)
ALLOWED_EXECUTORS = set(ExecutorType.__args__)
VALID_ROLES = {"actor", "target", "source", "destination"}


def validate():
    canonical = json.load(open(CANONICAL, encoding="utf-8"))
    all_ids = {str(nid) for nid in canonical["nodes"].keys()}
    cases = [json.loads(l) for l in open(CASES_PATH, encoding="utf-8") if l.strip()]
    errors = []

    if len(cases) != 50:
        errors.append(f"case 数 {len(cases)} != 50")
    cids = [c["case_id"] for c in cases]
    if len(cids) != len(set(cids)):
        errors.append("case_id 重复")
    actual_cat = Counter(c["category"] for c in cases)
    if dict(actual_cat) != EXPECTED_CATEGORIES:
        errors.append(f"类别分布不符: {dict(actual_cat)}")

    for c in cases:
        cid = c["case_id"]
        og = c.get("oracle_grounding", {})
        ep = c.get("expected_plan", {})
        status = ep.get("status")
        if status not in ALLOWED_STATUS:
            errors.append(f"{cid}: 非法 expected status '{status}'")

        # oracle grounded IDs 真实存在
        has_executable = False
        for role in VALID_ROLES:
            ref = og.get(role)
            if ref and ref.get("node_id"):
                nid = str(ref["node_id"])
                if nid not in all_ids:
                    errors.append(f"{cid}: oracle {role} node_id {nid} 不存在于 canonical")
                else:
                    has_executable = True
        # steps 里的 refs
        for i, step_data in enumerate(og.get("steps", [])):
            for role in VALID_ROLES:
                ref = step_data.get(role)
                if ref and ref.get("node_id"):
                    nid = str(ref["node_id"])
                    if nid not in all_ids:
                        errors.append(f"{cid}: oracle steps[{i}].{role} node_id {nid} 不存在")

        # actions / steps
        req_actions = ep.get("required_actions", [])
        for a in req_actions:
            if a not in ALLOWED_ACTIONS:
                errors.append(f"{cid}: 非法 action '{a}'")
        for s in ep.get("expected_steps", []):
            if s.get("action") not in ALLOWED_ACTIONS:
                errors.append(f"{cid}: step {s.get('step')} 非法 action '{s.get('action')}'")
            if s.get("executor") and s.get("executor") not in ALLOWED_EXECUTORS:
                errors.append(f"{cid}: step {s.get('step')} 非法 executor '{s.get('executor')}'")
            tr = s.get("target_ref")
            if tr and tr not in VALID_ROLES and not tr.startswith("step_") and not tr.startswith("constraint_"):
                errors.append(f"{cid}: step {s.get('step')} 非法 target_ref '{tr}'")
            tid = s.get("target_id")
            if tid and str(tid) not in all_ids:
                errors.append(f"{cid}: step {s.get('step')} target_id {tid} 不存在于 canonical")

        # status gate case：无可执行 expected plan
        if status in ("ambiguous", "target_not_found", "unsupported"):
            if req_actions or ep.get("expected_steps"):
                errors.append(f"{cid}: status={status} 但含可执行 expected plan")

        # multi-step refs 与 steps 一致
        if og.get("steps"):
            for s in ep.get("expected_steps", []):
                tr = s.get("target_ref")
                if tr and tr.startswith("step_"):
                    parts = tr.split("_")  # step_N_role
                    if len(parts) != 3 or not parts[1].isdigit():
                        errors.append(f"{cid}: 非法 step ref '{tr}'")
                    else:
                        si, role = int(parts[1]), parts[2]
                        if si >= len(og["steps"]):
                            errors.append(f"{cid}: step ref '{tr}' 超出 steps 范围")
                        elif role not in og["steps"][si]:
                            errors.append(f"{cid}: step ref '{tr}' 的 role 不在 steps[{si}]")

        # expected target IDs 真实
        for tid in ep.get("expected_target_ids", []):
            if str(tid) not in all_ids:
                errors.append(f"{cid}: expected_target_ids {tid} 不存在于 canonical")
        for aid in ep.get("expected_avoid_ids", []):
            if str(aid) not in all_ids and not str(aid).startswith("prefer_"):
                errors.append(f"{cid}: expected_avoid_ids {aid} 不存在于 canonical")

    # manifest hash
    manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
    payload = "\n".join(json.dumps(c, ensure_ascii=False, sort_keys=True) for c in cases)
    h = hashlib.sha256(payload.encode()).hexdigest()[:16]
    if manifest.get("benchmark_file_hash") != h:
        errors.append(f"manifest hash 不符: {manifest.get('benchmark_file_hash')} vs {h}")
    if manifest.get("case_count") != len(cases):
        errors.append("manifest case_count 不符")

    if errors:
        print(f"校验失败（{len(errors)}）:")
        for e in errors[:30]:
            print("  -", e)
        sys.exit(1)
    print(f"VALIDATION PASSED（50 条，类别分布 {dict(actual_cat)}，hash {h}）")


if __name__ == "__main__":
    validate()
