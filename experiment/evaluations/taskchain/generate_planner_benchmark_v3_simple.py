"""生成 Planner v3 Simple Benchmark（50 条）——Planner阶段全面审查与简化改进指导 第 9/10 节。

类别（50）：single_navigate 12 / ordered_source_destination 10 / escort 8 /
navigate_with_avoid 6 / navigate_with_prefer 4 / return_to_base 4 /
ambiguous_gate 2 / not_found_gate 2 / unsupported_gate 2。

原则：
- LLM 只需输出 {action, ref}（expected_symbolic_plan）；executor/id/constraints 由 Compiler 绑定
- 不再包含 single_locate / locate_then_navigate / 错误 multi_step / step-specific refs / constraint ref 生成
- source→destination 指令明确（First go to A, then go to B）
- escort 为高层宏动作（escort_to destination，Compiler 绑定 actor）
- prefer 是 route policy（非 node ID），与 avoid（真实 node ID）分离
- instruction 不暴露内部 label（避免 "room room_0752"）
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
OUT = Path(__file__).resolve().parent / "cases" / "planner_benchmark_50_v3_simple.jsonl"
MANIFEST = Path(__file__).resolve().parent / "cases" / "planner_benchmark_50_v3_simple.manifest.json"

LABELS = ["chairs", "signs", "small_office_objects", "large_static_wall_furniture", "large_office_objects"]
ROOMS = ["0752", "0753", "0754", "0755", "0756", "0757", "0758", "0759", "0760"]


def load_canonical() -> dict:
    return json.load(open(CANONICAL, encoding="utf-8"))


def _objects_by_label(c, label):
    return [n for n in c["nodes"].values() if n.get("node_type") == "object" and n.get("label") == label]


def _rooms(c):
    return [n for n in c["nodes"].values() if n.get("node_type") == "room"]


def _places_of(c, room_id):
    return [n for n in c["nodes"].values() if n.get("node_type") == "place" and n.get("room_id") == room_id]


def _room_by_tail(c, tail):
    for r in _rooms(c):
        if str(r["node_id"]).endswith(tail):
            return r
    raise KeyError(f"room tail {tail}")


def obj_ref(c, label, idx=0):
    objs = _objects_by_label(c, label)
    o = objs[idx % len(objs)]
    places = _places_of(c, o.get("room_id"))
    return {"node_id": str(o["node_id"]), "node_type": "object", "label": o.get("label") or label,
            "navigation_target": str(places[0]["node_id"]) if places else str(o.get("place_id"))}


def room_ref(c, tail):
    r = _room_by_tail(c, tail)
    places = _places_of(c, r["node_id"])
    return {"node_id": str(r["node_id"]), "node_type": "room", "label": f"room {tail}",
            "navigation_target": str(places[0]["node_id"]) if places else None}


def agent_ref(c, idx=0):
    agents = [n for n in c["nodes"].values() if n.get("node_type") == "agent"]
    a = agents[idx % len(agents)]
    return {"node_id": str(a["node_id"]), "node_type": "agent", "label": a.get("label") or "person"}


def nat(label):
    return label.replace("_", " ")


def sym(action, ref=None):
    return {"action": action, "ref": ref}


def build(c):
    cases = []
    cid = [0]

    def add(category, instruction, og, exp_sym, exp_comp=None):
        cid[0] += 1
        cases.append({
            "case_id": f"planner_v3_{cid[0]:03d}",
            "category": category,
            "instruction": instruction,
            "oracle_grounding": og,
            "expected_symbolic_plan": exp_sym,
            "expected_compiled_plan": exp_comp,
        })

    def exp_valid(steps):
        return {"status": "valid", "steps": steps}

    # ---- single_navigate (12) ----
    for i in range(12):
        label = LABELS[i % len(LABELS)]
        o = obj_ref(c, label, i)
        add("single_navigate",
            f"Navigate to the {nat(label)}.",
            {"action": "navigate", "target": o},
            exp_valid([sym("navigate_to", "target")]),
            {"status": "valid", "steps": [{"action": "navigate_to", "target_id": o["node_id"], "executor": "nav2", "constraints": {"avoid": [], "prefer": []}}]})

    # ---- ordered_source_destination (10) ----
    for i in range(10):
        src = room_ref(c, ROOMS[(i * 2) % len(ROOMS)])
        dst = room_ref(c, ROOMS[(i * 2 + 1) % len(ROOMS)])
        add("ordered_source_destination",
            f"First go to {src['label']}, then go to {dst['label']}.",
            {"action": "navigate", "source": src, "destination": dst},
            exp_valid([sym("navigate_to", "source"), sym("navigate_to", "destination")]),
            {"status": "valid", "steps": [
                {"action": "navigate_to", "target_id": src["node_id"], "executor": "nav2", "constraints": {"avoid": [], "prefer": []}},
                {"action": "navigate_to", "target_id": dst["node_id"], "executor": "nav2", "constraints": {"avoid": [], "prefer": []}}]})

    # ---- escort (8)：宏动作，Compiler 绑定 actor ----
    for i in range(8):
        a = agent_ref(c, i)
        dst = room_ref(c, ROOMS[(i + 3) % len(ROOMS)])
        add("escort",
            f"Escort the person to {dst['label']}.",
            {"action": "escort", "actor": a, "destination": dst},
            exp_valid([sym("escort_to", "destination")]),
            {"status": "valid", "steps": [{"action": "escort_to", "target_id": dst["node_id"], "executor": "nav2",
                                           "role_ids": {"actor": a["node_id"], "destination": dst["node_id"]},
                                           "constraints": {"avoid": [], "prefer": []}}]})

    # ---- navigate_with_avoid (6)：avoid 真实 node ID，Compiler 自动附加 ----
    for i in range(6):
        label = LABELS[(i + 1) % len(LABELS)]
        o = obj_ref(c, label, i + 1)
        av = room_ref(c, ROOMS[(i + 5) % len(ROOMS)])
        add("navigate_with_avoid",
            f"Navigate to the {nat(label)}, avoiding {av['label']}.",
            {"action": "navigate", "target": o,
             "constraints": [{"type": "avoid", "node_id": av["node_id"], "node_type": "room", "label": av["label"]}]},
            exp_valid([sym("navigate_to", "target")]),
            {"status": "valid", "steps": [{"action": "navigate_to", "target_id": o["node_id"], "executor": "nav2",
                                           "constraints": {"avoid": [av["node_id"]], "prefer": []}}]})

    # ---- navigate_with_prefer (4)：route policy（非 node ID） ----
    for i in range(4):
        label = LABELS[(i + 2) % len(LABELS)]
        o = obj_ref(c, label, i + 2)
        add("navigate_with_prefer",
            f"Navigate to the {nat(label)}, preferring the shortest path.",
            {"action": "navigate", "target": o,
             "constraints": [{"type": "prefer", "value": "shortest_path", "label": "shortest_path"}]},
            exp_valid([sym("navigate_to", "target")]),
            {"status": "valid", "steps": [{"action": "navigate_to", "target_id": o["node_id"], "executor": "nav2",
                                           "constraints": {"avoid": [], "prefer": ["shortest_path"]}}]})

    # ---- return_to_base (4)：无 ref，Compiler 处理 base ----
    for i in range(4):
        add("return_to_base",
            "Return to base.",
            {"action": "return_to_base"},
            exp_valid([sym("return_to_base", None)]),
            {"status": "valid", "steps": [{"action": "return_to_base", "target_id": None, "executor": "nav2",
                                           "constraints": {"avoid": [], "prefer": []}}]})

    # ---- status gate (6)：不调用 Planner，返回 blocked ----
    for i in range(2):
        add("ambiguous_gate", "Find the chair near the desks.",
            {"pipeline_status": "ambiguous"},
            {"status": "ambiguous", "steps": []})
    for i in range(2):
        add("not_found_gate", "Find a printer.",
            {"pipeline_status": "not_found"},
            {"status": "target_not_found", "steps": []})
    for i in range(2):
        add("unsupported_gate", "Pick up the chair and carry it.",
            {"pipeline_status": "unsupported"},
            {"status": "unsupported", "steps": []})

    return cases


def main():
    c = load_canonical()
    cases = build(c)

    assert len(cases) == 50, f"case 数 {len(cases)} != 50"
    cids = [x["case_id"] for x in cases]
    assert len(cids) == len(set(cids)), "case_id 重复"
    cat = Counter(x["category"] for x in cases)
    print("类别分布:", dict(cat))

    with open(OUT, "w", encoding="utf-8") as f:
        for x in cases:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    manifest = {
        "benchmark_status": "ready",
        "benchmark_file": OUT.name,
        "benchmark_file_sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
        "case_count": len(cases),
        "categories": dict(cat),
        "canonical_graph": str(CANONICAL),
        "canonical_graph_hash": hashlib.sha256(CANONICAL.read_bytes()).hexdigest(),
        "seed": 42,
    }
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"生成 {len(cases)} 条 -> {OUT}")
    for x in cases:
        print(f"  {x['case_id']} [{x['category']}] {x['instruction'][:44]}")


if __name__ == "__main__":
    sys.exit(main())
