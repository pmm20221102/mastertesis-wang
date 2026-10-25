"""E2E Benchmark Validator（硕士论文最终交付包全面审查 第 9 节）。

验证 e2e_dev_v1.jsonl / e2e_test_50_v1.jsonl：
- case 数、ID 唯一、类别分布
- oracle IDs 真实存在于 canonical
- expected semantic label 存在
- expected grounding candidate set 从 canonical 重算、与 status 一致
- expected symbolic refs / actions 合法
- gate case 无可执行 plan
- manifest 文件 bytes SHA256 一致（审查第 18 节）
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
CASES_DIR = Path(__file__).resolve().parent / "cases"

GATE_STATUSES = {"ambiguous", "target_not_found", "unsupported"}
VALID_ROLES = {"actor", "target", "source", "destination"}
SIMPLE_ACTIONS = {"navigate_to", "escort_to", "wait", "return_to_base"}


def validate(cases_path: Path):
    canonical = json.load(open(CANONICAL, encoding="utf-8"))
    all_ids = {str(nid) for nid in canonical["nodes"].keys()}
    labels = {n.get("label") for n in canonical["nodes"].values() if n.get("label")}
    cases = [json.loads(l) for l in open(cases_path, encoding="utf-8") if l.strip()]
    errors = []

    cids = [c["case_id"] for c in cases]
    if len(cids) != len(set(cids)):
        errors.append("case_id 重复")

    for c in cases:
        cid = c["case_id"]
        og = c.get("oracle_intent", {})
        es = c.get("expected_semantic")
        eg = c.get("expected_grounding", {})
        esym = c.get("expected_symbolic_plan")
        efinal = c.get("expected_final_status")

        # oracle IDs 真实
        for role in VALID_ROLES:
            ref = og.get(role)
            if ref and ref.get("node_id") and str(ref["node_id"]) not in all_ids:
                errors.append(f"{cid}: oracle {role} node_id 不存在")
        tgt = og.get("target") or {}
        for loc in tgt.get("locations", []) or []:
            if loc.get("node_id") and str(loc["node_id"]) not in all_ids:
                errors.append(f"{cid}: oracle location node_id 不存在")

        # expected semantic label 存在
        if es and es.get("canonical_label") and es["canonical_label"] not in labels:
            errors.append(f"{cid}: semantic label '{es['canonical_label']}' 不在 canonical")

        # expected grounding candidate 完整（从 canonical 重算 object/room）
        egs = eg.get("status")
        if egs == "resolved":
            ids = eg.get("node_ids", [])
            if len(ids) != 1 or str(ids[0]) not in all_ids:
                errors.append(f"{cid}: resolved node_ids 非法")
        elif egs == "ambiguous":
            if len(eg.get("candidate_node_ids", [])) <= 1:
                errors.append(f"{cid}: ambiguous 但候选 ≤1")
            for nid in eg.get("candidate_node_ids", []):
                if str(nid) not in all_ids:
                    errors.append(f"{cid}: ambiguous 候选 {nid} 不存在")
        elif egs == "not_found":
            if eg.get("node_ids") or eg.get("candidate_node_ids"):
                errors.append(f"{cid}: not_found 但候选非空")
        elif egs == "unsupported":
            pass
        else:
            errors.append(f"{cid}: 非法 grounding status '{egs}'")

        # P1-10：从 canonical 重算 object candidate set 并比较（label + room 约束）
        if es and es.get("status") == "matched" and es.get("canonical_label"):
            room_id = None
            tgt = og.get("target") or {}
            locs = tgt.get("locations") or []
            if locs and locs[0].get("node_id"):
                room_id = str(locs[0]["node_id"])
            recomputed = {str(nid) for nid, n in canonical["nodes"].items()
                          if n.get("node_type") == "object" and n.get("label") == es["canonical_label"]
                          and (room_id is None or n.get("room_id") == room_id)}
            expected_set = {str(x) for x in eg.get("candidate_node_ids", [])}
            if recomputed != expected_set:
                errors.append(f"{cid}: object candidate set 与 canonical 重算不符（{len(recomputed)} vs {len(expected_set)}）")

        # expected symbolic plan 合法
        if esym:
            status = esym.get("status")
            if status in GATE_STATUSES:
                if esym.get("steps"):
                    errors.append(f"{cid}: gate status 但 symbolic 含 steps")
                if eg.get("node_ids"):
                    errors.append(f"{cid}: gate status 但 grounding 含 node_ids")
            else:
                for s in esym.get("steps", []):
                    if s.get("action") not in SIMPLE_ACTIONS:
                        errors.append(f"{cid}: 非法 simple action '{s.get('action')}'")
                    ref = s.get("ref")
                    if s["action"] in ("navigate_to", "escort_to") and (not ref or ref not in VALID_ROLES):
                        errors.append(f"{cid}: {s['action']} 缺/非法 ref '{ref}'")
                    if ref and str(ref).isdigit():
                        errors.append(f"{cid}: symbolic 含 raw node id")

        # expected final status 与 grounding 一致
        if efinal in GATE_STATUSES and egs not in (GATE_STATUSES - {"target_not_found"}) and efinal not in ("ambiguous",):
            if egs not in ("ambiguous", "not_found", "unsupported"):
                errors.append(f"{cid}: final status {efinal} 但 grounding {egs} 不一致")

    # manifest hash（文件 bytes，审查第 18 节）
    mf = cases_path.with_suffix(".manifest.json")
    if mf.exists():
        manifest = json.load(open(mf, encoding="utf-8"))
        h = hashlib.sha256(cases_path.read_bytes()).hexdigest()
        if manifest.get("benchmark_file_sha256") != h:
            errors.append(f"{mf.name} hash 不符: {manifest.get('benchmark_file_sha256')} vs {h[:16]}...")
        if manifest.get("case_count") != len(cases):
            errors.append("manifest case_count 不符")

    if errors:
        print(f"校验失败（{len(errors)}）:")
        for e in errors[:30]:
            print("  -", e)
        sys.exit(1)
    print(f"VALIDATION PASSED（{cases_path.name}: {len(cases)} 条，类别 {dict(Counter(c['category'] for c in cases))}）")


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "e2e_test_50_v1.jsonl"
    validate(CASES_DIR / name)
