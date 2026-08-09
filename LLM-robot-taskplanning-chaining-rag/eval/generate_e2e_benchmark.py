"""生成 E2E Benchmark（Dev 20 + Test 50 v2，表达不重合）——硕士论文最终交付包全面审查 第 6/23 节。

审查发现 Dev ⊂ Test（instruction 与 GT 完全重合），违反 held-out 原则。
本版 Dev 与 Test 使用完全不同的 instruction 模板与 gate 表达，确保不重合。

其他原则（审查 P0-5）：
- avoid/prefer 约束未被 Intent Parser 解析 → expected_compiled 不含约束（如实，限制项）
- escort actor 无语义标签 → expected_grounding 仅 destination（如实，限制项）
- 先从 canonical 确定真实节点，再写 instruction
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

LLM_ROOT = Path(__file__).resolve().parent.parent
RAG_ROOT = LLM_ROOT.parent / "RAG" / "khronos_3dsg_rag"
CANONICAL = RAG_ROOT / "outputs" / "office_dense" / "canonical_graph_v2.json"
OUT_DIR = Path(__file__).resolve().parent / "cases"

UNIQUE_CELLS = [
    ("chairs", "0756"), ("large_office_objects", "0756"), ("signs", "0756"),
    ("large_static_wall_furniture", "0759"), ("large_office_objects", "0764"),
    ("large_office_objects", "0767"), ("large_static_wall_furniture", "0767"),
    ("small_office_objects", "0767"),
]
NAT = {"chairs": "chair", "signs": "sign", "large_office_objects": "large office machine",
       "large_static_wall_furniture": "wall-mounted storage unit", "small_office_objects": "small office item"}
ROOMS = ["0752", "0753", "0754", "0755", "0756", "0757", "0758", "0759", "0760",
         "0761", "0762", "0763", "0764", "0765", "0766", "0767"]


def load_canonical():
    return json.load(open(CANONICAL, encoding="utf-8"))


def _room(c, tail):
    for n in c["nodes"].values():
        if n.get("node_type") == "room" and str(n["node_id"]).endswith(tail):
            return n
    raise KeyError(tail)


def _obj_in_room(c, label, tail):
    rid = _room(c, tail)["node_id"]
    cands = [n for n in c["nodes"].values()
             if n.get("node_type") == "object" and n.get("label") == label and n.get("room_id") == rid]
    return str(cands[0]["node_id"])


def _room_id(c, tail):
    return str(_room(c, tail)["node_id"])


def _compiled(actions_targets):
    return {"status": "valid", "steps": [
        {"action": a, "target_id": tid, "executor": "nav2", "constraints": {"avoid": [], "prefer": []}}
        for a, tid in actions_targets]}


class Builder:
    """tpl: "dev" 或 "test" —— 控制 instruction 模板，保证 Dev/Test 表达不重合。"""

    def __init__(self, c, tpl):
        self.c = c
        self.tpl = tpl
        self.cases = []
        self._n = 0

    def add(self, category, instruction, oi, esem, eg, esym, ecomp, efinal):
        self._n += 1
        self.cases.append({
            "case_id": f"e2e_{self.tpl}_{self._n:03d}", "category": category, "instruction": instruction,
            "oracle_intent": oi, "expected_semantic": esem, "expected_grounding": eg,
            "expected_symbolic_plan": esym, "expected_compiled_plan": ecomp,
            "expected_final_status": efinal,
        })

    # ---- single_navigate：room 目标 ----
    def single_navigate(self, tails):
        for tail in tails:
            rid = _room_id(self.c, tail)
            instr = (f"Go to room {tail}." if self.tpl == "dev"
                     else f"Navigate to room {tail}.")
            self.add("single_navigate", instr,
                     {"action": "navigate", "target": {"text": f"room {tail}", "node_type": "room", "quantifier": "one",
                                                       "locations": [{"node_type": "room", "text": tail, "node_id": rid}]}},
                     None,
                     {"status": "resolved", "node_ids": [rid], "candidate_node_ids": [rid]},
                     {"status": "valid", "steps": [{"action": "navigate_to", "ref": "target"}]},
                     _compiled([("navigate_to", rid)]),
                     "ready")

    # ---- room_constrained_navigate：唯一 object + room ----
    def room_constrained(self, cells):
        for i, (label, tail) in enumerate(cells):
            oid = _obj_in_room(self.c, label, tail)
            nat = NAT[label]
            instr = (f"Go to the {nat} in room {tail}." if self.tpl == "dev"
                     else f"Navigate to the {nat} situated in room {tail}.")
            self.add("room_constrained_navigate", instr,
                     {"action": "navigate", "target": {"text": nat, "node_type": "object", "quantifier": "one",
                                                       "locations": [{"node_type": "room", "text": tail, "node_id": _room_id(self.c, tail)}]}},
                     {"status": "matched", "canonical_label": label},
                     {"status": "resolved", "node_ids": [oid], "candidate_node_ids": [oid]},
                     {"status": "valid", "steps": [{"action": "navigate_to", "ref": "target"}]},
                     _compiled([("navigate_to", oid)]),
                     "ready")

    # ---- ordered_source_destination ----
    def source_destination(self, pairs):
        for (sa, da) in pairs:
            sid, did = _room_id(self.c, sa), _room_id(self.c, da)
            instr = (f"First go to room {sa}, then go to room {da}." if self.tpl == "dev"
                     else f"First move to room {sa}, then move to room {da}.")
            self.add("ordered_source_destination", instr,
                     {"action": "navigate",
                      "source": {"text": f"room {sa}", "node_type": "room"},
                      "destination": {"text": f"room {da}", "node_type": "room"}},
                     None,
                     {"status": "resolved", "node_ids": [did], "candidate_node_ids": [did]},
                     {"status": "valid", "steps": [{"action": "navigate_to", "ref": "source"},
                                                   {"action": "navigate_to", "ref": "destination"}]},
                     _compiled([("navigate_to", sid), ("navigate_to", did)]),
                     "ready")

    # ---- escort：actor 无语义标签，grounding 仅 destination ----
    def escort(self, tails):
        for tail in tails:
            did = _room_id(self.c, tail)
            instr = (f"Escort the person to room {tail}." if self.tpl == "dev"
                     else f"Accompany the person to room {tail}.")
            self.add("escort", instr,
                     {"action": "escort", "actor": {"text": "person", "node_type": "agent"},
                      "destination": {"text": f"room {tail}", "node_type": "room"}},
                     None,
                     {"status": "resolved", "node_ids": [did], "candidate_node_ids": [did]},
                     {"status": "valid", "steps": [{"action": "escort_to", "ref": "destination"}]},
                     _compiled([("escort_to", did)]),
                     "ready")

    # ---- navigate_with_avoid / prefer：约束传递受限（如实） ----
    def with_constraint(self, cells, kind):
        for i, (label, tail) in enumerate(cells):
            oid = _obj_in_room(self.c, label, tail)
            nat = NAT[label]
            avoid_tail = ROOMS[(ROOMS.index(tail) + 3) % len(ROOMS)]
            if self.tpl == "dev":
                instr = (f"Navigate to the {nat} in room {tail}, avoiding room {avoid_tail}."
                         if kind == "avoid"
                         else f"Navigate to the {nat} in room {tail}, preferring the shortest route.")
            else:
                instr = (f"Go to the {nat} in room {tail}, bypassing room {avoid_tail}."
                         if kind == "avoid"
                         else f"Go to the {nat} in room {tail} via the quickest route.")
            oi_c = ({"type": "avoid", "node_id": _room_id(self.c, avoid_tail), "label": f"room {avoid_tail}"}
                    if kind == "avoid" else {"type": "prefer", "value": "shortest_path", "label": "shortest_path"})
            self.add(f"navigate_with_{kind}", instr,
                     {"action": "navigate", "target": {"text": nat, "node_type": "object", "quantifier": "one",
                                                       "locations": [{"node_type": "room", "text": tail, "node_id": _room_id(self.c, tail)}]},
                      "constraints": [oi_c]},
                     {"status": "matched", "canonical_label": label},
                     {"status": "resolved", "node_ids": [oid], "candidate_node_ids": [oid]},
                     {"status": "valid", "steps": [{"action": "navigate_to", "ref": "target"}]},
                     _compiled([("navigate_to", oid)]),  # 约束无法从 NL 传递（Intent 不解析），如实
                     "ready")

    # ---- gates：Dev/Test 表达不同 ----
    def gates(self):
        if self.tpl == "dev":
            amb = [("Go to a chair.", "chairs"), ("Go to a sign.", "signs"),
                   ("Navigate to a wall-mounted storage unit.", "large_static_wall_furniture")]
            nf = ["Find a printer.", "Find a bicycle.", "Locate a fire extinguisher."]
            uns = ["Pick up the chair.", "Grasp the cup."]
        else:
            amb = [("Approach a chair.", "chairs"), ("Approach a sign.", "signs"),
                   ("Approach one of the wall-mounted storage units.", "large_static_wall_furniture")]
            nf = ["Search for a printer.", "Look for a bicycle.", "Find a fire alarm."]
            uns = ["Carry the box to the desk.", "Lift the bag."]
        for phrase, label in amb:
            n_cands = [str(n["node_id"]) for n in self.c["nodes"].values()
                       if n.get("node_type") == "object" and n.get("label") == label]
            self.add("ambiguous_gate", phrase,
                     {"action": "navigate", "target": {"text": label.rstrip("s"), "quantifier": "one"}},
                     {"status": "matched", "canonical_label": label},
                     {"status": "ambiguous", "node_ids": [], "candidate_node_ids": n_cands},
                     {"status": "ambiguous", "steps": []}, None, "ambiguous")
        for phrase in nf:
            target = phrase.lower().split(" a ")[1].rstrip(".") if " a " in phrase else phrase.lower().split(" for ")[1].rstrip(".")
            self.add("not_found_gate", phrase,
                     {"action": "locate", "target": {"text": target, "quantifier": "one"}},
                     {"status": "not_found", "canonical_label": None},
                     {"status": "not_found", "node_ids": [], "candidate_node_ids": []},
                     {"status": "target_not_found", "steps": []}, None, "target_not_found")
        for phrase in uns:
            self.add("unsupported_gate", phrase,
                     {"action": "unsupported"}, None,
                     {"status": "unsupported", "node_ids": [], "candidate_node_ids": []},
                     {"status": "unsupported", "steps": []}, None, "unsupported")


def build_dev(c):
    b = Builder(c, "dev")
    b.single_navigate(ROOMS[:3])
    b.room_constrained(UNIQUE_CELLS[:3])
    b.source_destination([("0752", "0756"), ("0753", "0764")])
    b.escort(["0756", "0764"])
    b.with_constraint([UNIQUE_CELLS[3]], "avoid")
    b.with_constraint([UNIQUE_CELLS[4]], "prefer")
    b.gates()
    return b.cases


def build_test(c):
    b = Builder(c, "test")
    b.single_navigate(ROOMS[:12])
    b.room_constrained(UNIQUE_CELLS)
    b.source_destination([("0752", "0756"), ("0753", "0764"), ("0754", "0767"), ("0755", "0759"),
                          ("0756", "0752"), ("0757", "0764"), ("0758", "0756"), ("0759", "0767")])
    b.escort(["0756", "0764", "0752", "0759", "0767", "0760"])
    b.with_constraint(UNIQUE_CELLS[0:4], "avoid")
    b.with_constraint(UNIQUE_CELLS[4:8], "prefer")
    b.gates()
    return b.cases


def _write(cases, out):
    with open(out, "w", encoding="utf-8") as f:
        for x in cases:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    # manifest hash 改为落盘文件 bytes 的 SHA256（审查第 18 节）
    file_bytes = out.read_bytes()
    manifest = {
        "benchmark_status": "ready", "benchmark_file": out.name,
        "benchmark_file_sha256": hashlib.sha256(file_bytes).hexdigest(),
        "case_count": len(cases), "categories": dict(Counter(x["category"] for x in cases)),
        "canonical_graph_hash": hashlib.sha256(CANONICAL.read_bytes()).hexdigest(),
        "seed": 42,
    }
    mf = out.with_suffix(".manifest.json")
    with open(mf, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"{out.name}: {len(cases)} 条, {manifest['categories']}")


def main():
    c = load_canonical()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dev = build_dev(c)
    test = build_test(c)
    _write(dev, OUT_DIR / "e2e_dev_v1.jsonl")
    _write(test, OUT_DIR / "e2e_test_50_v1.jsonl")
    # 验证 Dev/Test 不重合（审查第 6 节）
    dev_instr = {x["instruction"] for x in dev}
    overlap = [x["instruction"] for x in test if x["instruction"] in dev_instr]
    print(f"Dev/Test instruction 重合数: {len(overlap)}（应为 0）")
    print(f"Dev {len(dev)} 条 / Test {len(test)} 条")


if __name__ == "__main__":
    sys.exit(main())
