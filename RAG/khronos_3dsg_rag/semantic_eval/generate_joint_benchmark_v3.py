"""Phase A：生成 joint_cases_v3.jsonl（30 条合理联合测试，GT 从 canonical 自动生成）。

类别：全局 ambiguous(5) / 全局 resolved_set(5) / Room-resolved(4) / Room-ambiguous(4)
      / Room-resolved_set(4) / Not-found(6) / 语义改写(2)
生成顺序：固定 label + Room -> 查 canonical -> candidate set -> 按 quantifier + count 定 status -> 写 instruction。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

RAG_ROOT = Path("/home/ulmmt/Masterthesis-wang/RAG/khronos_3dsg_rag")
OUT = Path(__file__).resolve().parent / "joint_cases_v3.jsonl"


def load_canonical() -> dict:
    return json.load(open(RAG_ROOT / "outputs" / "office_dense" / "canonical_graph_v2.json", encoding="utf-8"))


def room_tail_to_id(canonical: dict, tail: str) -> str | None:
    for nid, n in canonical["nodes"].items():
        if n.get("node_type") == "room" and str(nid).endswith(tail):
            return str(nid)
    return None


def object_node_ids(canonical: dict, label: str, room_id: str | None = None) -> list[str]:
    out = []
    for nid, n in canonical["nodes"].items():
        if n.get("node_type") != "object" or n.get("label") != label:
            continue
        if room_id is not None and n.get("room_id") != room_id:
            continue
        out.append(str(nid))
    return out


def status_for(quantifier: str, count: int) -> str:
    if count == 0:
        return "not_found"
    if quantifier == "all":
        return "resolved_set"
    if count == 1:
        return "resolved"
    return "ambiguous"


# (case_id, category, label, room_tail, quantifier, instruction)
PLAN = [
    # 全局 ambiguous（5）
    ("joint_v3_001", "global_ambiguous", "chairs", None, "one", "Find something people can sit on."),
    ("joint_v3_002", "global_ambiguous", "signs", None, "one", "Find one directional marker."),
    ("joint_v3_003", "global_ambiguous", "small_office_objects", None, "one", "Find one small desk accessory."),
    ("joint_v3_004", "global_ambiguous", "large_static_wall_furniture", None, "one", "Find one wall-mounted storage unit."),
    ("joint_v3_005", "global_ambiguous", "large_office_objects", None, "one", "Find one large office machine."),
    # 全局 resolved_set（5）
    ("joint_v3_006", "global_set", "chairs", None, "all", "Find all chairs."),
    ("joint_v3_007", "global_set", "signs", None, "all", "Find all signs."),
    ("joint_v3_008", "global_set", "small_office_objects", None, "all", "Find all small office objects."),
    ("joint_v3_009", "global_set", "large_static_wall_furniture", None, "all", "Find all wall-mounted storage units."),
    ("joint_v3_010", "global_set", "large_office_objects", None, "all", "Find all large office machines."),
    # Room-resolved（4）
    ("joint_v3_011", "room_resolved", "chairs", "0756", "one", "Find a chair in room 0756."),
    ("joint_v3_012", "room_resolved", "signs", "0756", "one", "Find a sign in room 0756."),
    ("joint_v3_013", "room_resolved", "large_office_objects", "0764", "one", "Find a large office machine in room 0764."),
    ("joint_v3_014", "room_resolved", "large_static_wall_furniture", "0767", "one", "Find a wall-mounted storage unit in room 0767."),
    # Room-ambiguous（4）
    ("joint_v3_015", "room_ambiguous", "chairs", "0752", "one", "Find a chair in room 0752."),
    ("joint_v3_016", "room_ambiguous", "signs", "0752", "one", "Find a sign in room 0752."),
    ("joint_v3_017", "room_ambiguous", "small_office_objects", "0752", "one", "Find a small office item in room 0752."),
    ("joint_v3_018", "room_ambiguous", "large_static_wall_furniture", "0760", "one", "Find a wall-mounted storage unit in room 0760."),
    # Room-resolved_set（4）
    ("joint_v3_019", "room_set", "chairs", "0752", "all", "Find all chairs in room 0752."),
    ("joint_v3_020", "room_set", "signs", "0752", "all", "Find all signs in room 0752."),
    ("joint_v3_021", "room_set", "small_office_objects", "0752", "all", "Find all small office items in room 0752."),
    ("joint_v3_022", "room_set", "large_static_wall_furniture", "0760", "all", "Find all wall-mounted storage units in room 0760."),
    # Not-found（6，unsupported ontology）
    ("joint_v3_023", "not_found", None, None, "one", "Find a hospital bed."),
    ("joint_v3_024", "not_found", None, None, "one", "Find a bicycle."),
    ("joint_v3_025", "not_found", None, None, "one", "Find a tree."),
    ("joint_v3_026", "not_found", None, None, "one", "Find a bathtub."),
    ("joint_v3_027", "not_found", None, None, "one", "Find a traffic light."),
    ("joint_v3_028", "not_found", None, None, "one", "Find a skateboard."),
    # 语义改写（2）
    ("joint_v3_029", "paraphrase", "chairs", None, "one", "Find something employees can sit on during a break."),
    ("joint_v3_030", "paraphrase", "small_office_objects", None, "one", "Find one item people use for writing at work."),
]


def main():
    canonical = load_canonical()
    out = []
    for cid, cat, label, room_tail, quantifier, instr in PLAN:
        room_id = room_tail_to_id(canonical, room_tail) if room_tail else None
        node_ids = object_node_ids(canonical, label, room_id) if label else []
        status = status_for(quantifier, len(node_ids))

        case = {
            "case_id": cid,
            "category": cat,
            "instruction": instr,
            "oracle_intent": {
                "action": "locate",
                "target": {
                    "text": _target_text(instr),
                    "node_type": "object",
                    "quantifier": quantifier,
                    "locations": ([{"node_type": "room", "text": room_tail, "node_id": room_id}]
                                  if room_id else []),
                },
            },
            "expected_semantic": {
                "status": "not_found" if status == "not_found" else "matched",
                "canonical_label": label,
            },
            "expected_grounding": {
                "status": status,
                "node_ids": node_ids if status == "resolved" else [],
                "candidate_node_ids": node_ids,
                "room_id": room_id,
            },
        }
        out.append(case)

    with open(OUT, "w", encoding="utf-8") as f:
        for c in out:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"生成 {len(out)} 条 joint_cases_v3.jsonl")
    cat_counts = Counter(c["category"] for c in out)
    print("类别分布:", dict(cat_counts))
    for c in out:
        eg = c["expected_grounding"]
        print(f"  {c['case_id']} [{c['category']}] {c['instruction'][:40]} status={eg['status']} ids={len(eg['candidate_node_ids'])}")


def _target_text(instr: str) -> str:
    # 提取 "Find X" 中 X；简单提取目标短语
    low = instr.lower()
    for prefix in ("find all ", "find a ", "find an ", "find one ", "find "):
        if low.startswith(prefix):
            t = instr[len(prefix):].strip().rstrip(".")
            # 去掉 room 后缀
            for sep in (" in room ", " in the building", " at work"):
                if sep in t:
                    t = t.split(sep)[0]
            return t
    return instr


if __name__ == "__main__":
    main()
