"""Phase 1：为联合测试 benchmark 补充 expected_action + expected_grounding（由真实 canonical graph 生成）。"""

from __future__ import annotations

import json
from pathlib import Path

RAG_ROOT = Path("/home/ulmmt/Masterthesis-wang/RAG/khronos_3dsg_rag")
CASES = Path(__file__).resolve().parent / "joint_cases.jsonl"
OUT = Path(__file__).resolve().parent / "joint_cases_v2.jsonl"


def infer_action(instr: str) -> str:
    i = instr.lower().strip()
    if i.startswith(("take", "go ", "navigate", "bring")): return "navigate"
    if i.startswith(("escort", "accompany")): return "escort"
    if i.startswith(("guide", "lead")): return "guide"
    if i.startswith(("avoid", "bypass", "stay away")): return "avoid"
    if i.startswith(("wait", "stay", "pause")): return "wait"
    if i.startswith(("what should", "what do")): return "ask_clarification"
    return "locate"  # find / where / search


def load_canonical() -> dict:
    return json.load(open(RAG_ROOT / "outputs" / "office_dense" / "canonical_graph_v2.json", encoding="utf-8"))


def label_to_node_ids(canonical: dict, label: str) -> list[str]:
    return [nid for nid, n in canonical["nodes"].items()
            if n.get("node_type") == "object" and n.get("label") == label]


def build_grounding(canonical: dict, case: dict) -> dict:
    status = case.get("expected_status", "matched")
    label = case.get("expected_label")
    if status == "not_found" or not label:
        return {"status": "not_found", "node_ids": [], "candidate_node_ids": [], "room_id": None, "place_id": None}
    node_ids = label_to_node_ids(canonical, label)
    quantifier = case.get("expected_quantifier")
    if quantifier == "all":
        return {"status": "resolved_set", "node_ids": node_ids, "candidate_node_ids": node_ids,
                "room_id": None, "place_id": None}
    return {"status": "resolved", "node_ids": node_ids, "candidate_node_ids": node_ids,
            "room_id": None, "place_id": None}


def main():
    canonical = load_canonical()
    cases = [json.loads(l) for l in open(CASES, encoding="utf-8") if l.strip()]
    out = []
    for c in cases:
        c = dict(c)
        c["expected_action"] = infer_action(c.get("instruction", ""))
        c["expected_grounding"] = build_grounding(canonical, c)
        out.append(c)
    with open(OUT, "w", encoding="utf-8") as f:
        for c in out:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"生成 {len(out)} 条 joint_cases_v2.jsonl（含 expected_action + expected_grounding）")


if __name__ == "__main__":
    main()
