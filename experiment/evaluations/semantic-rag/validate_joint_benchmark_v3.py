"""Phase B：校验 joint_cases_v3.jsonl（15 项检查，从 canonical 重新执行查询）。"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[3] / "llm-rag" / "RAG" / "khronos_3dsg_rag"


def load_canonical() -> dict:
    return json.load(open(RAG_ROOT / "outputs" / "office_dense" / "canonical_graph_v2.json", encoding="utf-8"))


def object_node_ids(canonical, label, room_id=None):
    return [nid for nid, n in canonical["nodes"].items()
            if n.get("node_type") == "object" and n.get("label") == label
            and (room_id is None or n.get("room_id") == room_id)]


def room_tail_to_id(canonical, tail):
    for nid, n in canonical["nodes"].items():
        if n.get("node_type") == "room" and str(nid).endswith(tail):
            return str(nid)
    return None


UNSUPPORTED_CONCEPTS = ["hospital bed", "bicycle", "tree", "bathtub", "traffic light", "skateboard", "elevator"]


def validate(cases_path: str):
    cases = [json.loads(l) for l in open(cases_path, encoding="utf-8") if l.strip()]
    canonical = load_canonical()
    errors = []

    cids = [c["case_id"] for c in cases]
    if len(cids) != len(set(cids)):
        errors.append("case_id 存在重复")
    if len(cases) != 30:
        errors.append(f"case 数 {len(cases)} != 30")

    cat_expected = Counter({"global_ambiguous": 5, "global_set": 5, "room_resolved": 4,
                            "room_ambiguous": 4, "room_set": 4, "not_found": 6, "paraphrase": 2})
    cat_actual = Counter(c["category"] for c in cases)
    if dict(cat_actual) != dict(cat_expected):
        errors.append(f"类别分布不符: {dict(cat_actual)}")

    # 检查不支持语义（entrance/corridor/door）
    for c in cases:
        instr = c["instruction"].lower()
        for bad in ("entrance", "corridor", "door"):
            if bad in instr:
                errors.append(f"{c['case_id']}: 指令含不可靠语义 '{bad}'")

    for c in cases:
        cid = c["case_id"]
        oracle = c.get("oracle_intent", {})
        target = oracle.get("target", {})
        q = target.get("quantifier")
        locations = target.get("locations", [])
        exp_sem = c.get("expected_semantic", {})
        eg = c.get("expected_grounding", {})
        label = exp_sem.get("canonical_label")
        status = eg.get("status")

        # room 一致性
        room_tail = locations[0].get("text") if locations else None
        room_id = locations[0].get("node_id") if locations else None
        if room_tail:
            real_room_id = room_tail_to_id(canonical, room_tail)
            if real_room_id is None:
                errors.append(f"{cid}: Room '{room_tail}' 不存在")
            elif room_id != real_room_id:
                errors.append(f"{cid}: Room ID 与 suffix 不一致 {room_id} vs {real_room_id}")
        else:
            if room_id:
                errors.append(f"{cid}: 有 room_id 但无 room text")

        # label 真实存在
        if label and label not in {n.get("label") for n in canonical["nodes"].values() if n.get("label")}:
            errors.append(f"{cid}: label '{label}' 不在 canonical")

        # 从 canonical 重新查 candidate set（验证完整性）
        recomputed = object_node_ids(canonical, label, room_id) if label else []
        expected_ids = eg.get("candidate_node_ids", [])
        if set(recomputed) != set(expected_ids):
            errors.append(f"{cid}: candidate set 不完整（canonical 重新查 {len(recomputed)} vs 写入 {len(expected_ids)}）")

        # not_found 可靠性：instruction 目标必须在已确认 unsupported 概念列表
        if status == "not_found" and not any(u in c["instruction"].lower() for u in UNSUPPORTED_CONCEPTS):
            errors.append(f"{cid}: not_found 目标不在已确认 unsupported 列表（语义可能被粗分类，不可靠）")

        # status 规则
        if status == "not_found":
            if expected_ids:
                errors.append(f"{cid}: not_found 但 candidate 非空")
            if label:
                errors.append(f"{cid}: not_found 不应有 canonical_label")
        elif status == "resolved":
            if len(recomputed) != 1:
                errors.append(f"{cid}: resolved 但候选数 {len(recomputed)} != 1")
        elif status == "ambiguous":
            if len(recomputed) <= 1:
                errors.append(f"{cid}: ambiguous 但候选数 {len(recomputed)} <= 1")
        elif status == "resolved_set":
            if len(recomputed) == 0:
                errors.append(f"{cid}: resolved_set 但候选数 0")
        else:
            errors.append(f"{cid}: 非法 status '{status}'")

        # expected node_ids：resolved 时应 == candidate；其余应为空
        if status == "resolved":
            if set(eg.get("node_ids", [])) != set(recomputed):
                errors.append(f"{cid}: resolved node_ids 与候选不一致")
        else:
            if eg.get("node_ids"):
                errors.append(f"{cid}: {status} 但 node_ids 非空")

        # instruction quantifier/room 与 oracle 一致
        low = c["instruction"].lower()
        if q == "all" and not any(w in low for w in ("all", "every")):
            errors.append(f"{cid}: instruction 未体现 all")
        if room_tail and f"room {room_tail}" not in low:
            errors.append(f"{cid}: instruction 未提 room {room_tail}")

    if errors:
        print(f"校验失败（{len(errors)}）:")
        for e in errors[:30]:
            print("  -", e)
        sys.exit(1)
    print(f"VALIDATION PASSED（{len(cases)} 条，类别分布 {dict(cat_actual)}）")


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else "joint_cases_v3.jsonl"
    validate(p)
