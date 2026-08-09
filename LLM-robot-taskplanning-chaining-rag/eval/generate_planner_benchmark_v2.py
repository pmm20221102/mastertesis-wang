"""生成 Planner Benchmark 50 条 v2（按 LLM_Planner阶段详细实施计划 第 10 节类别）。

类别分布（50）：
  single_locate 5 / single_navigate 8 / locate_then_navigate 7 / source_destination 6
  escort 5 / avoid_constraint 4 / prefer_constraint 3 / ambiguous_grounding 3
  not_found_grounding 3 / unsupported_action 2 / multi_step 4

原则：
- oracle_grounded_task 从真实 canonical graph 取 node IDs（可验证）
- expected_plan 先定义，再配 instruction
- Grounding 已完成 → single_locate 期望一个 locate_entity（scene_graph 确认步骤）
- status gate case（ambiguous/not_found/unsupported）不产生可执行计划
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
OUT = Path(__file__).resolve().parent / "cases" / "planner_benchmark_50_v2.jsonl"
MANIFEST = Path(__file__).resolve().parent / "cases" / "planner_benchmark_50_v2.manifest.json"


def load_canonical() -> dict:
    return json.load(open(CANONICAL, encoding="utf-8"))


def _nodes(canonical):
    return canonical["nodes"]


def _objects_by_label(canonical, label):
    return [n for n in _nodes(canonical).values()
            if n.get("node_type") == "object" and n.get("label") == label]


def _rooms(canonical):
    return [n for n in _nodes(canonical).values() if n.get("node_type") == "room"]


def _places_of(canonical, room_id):
    return [n for n in _nodes(canonical).values()
            if n.get("node_type") == "place" and n.get("room_id") == room_id]


def _room_by_tail(canonical, tail):
    for r in _rooms(canonical):
        if str(r["node_id"]).endswith(tail):
            return r
    raise KeyError(f"room tail {tail} not found")


def obj_ref(canonical, label, idx=0):
    objs = _objects_by_label(canonical, label)
    o = objs[idx % len(objs)]
    places = _places_of(canonical, o.get("room_id"))
    return {
        "node_id": str(o["node_id"]),
        "node_type": "object",
        "label": o.get("label") or label,
        "room_id": str(o["room_id"]) if o.get("room_id") else None,
        "place_id": str(o.get("place_id")) if o.get("place_id") else None,
        "navigation_target": str(places[0]["node_id"]) if places else str(o.get("place_id")),
    }


def room_ref(canonical, tail):
    r = _room_by_tail(canonical, tail)
    places = _places_of(canonical, r["node_id"])
    return {
        "node_id": str(r["node_id"]),
        "node_type": "room",
        "label": f"room_{tail}",
        "navigation_target": str(places[0]["node_id"]) if places else None,
    }


def agent_ref(canonical, idx=0):
    agents = [n for n in _nodes(canonical).values() if n.get("node_type") == "agent"]
    a = agents[idx % len(agents)]
    return {"node_id": str(a["node_id"]), "node_type": "agent", "label": a.get("label") or "person"}


# 全局 label 池（真实存在的 label）
LABELS = ["chairs", "signs", "small_office_objects", "large_static_wall_furniture", "large_office_objects"]
ROOMS = ["0752", "0753", "0754", "0755", "0756", "0757", "0758", "0759", "0760"]


def build(canonical):
    cases = []
    cid = [0]

    def add(category, instruction, oracle_grounding, expected_plan):
        cid[0] += 1
        cases.append({
            "case_id": f"planner_{cid[0]:03d}",
            "category": category,
            "instruction": instruction,
            "oracle_grounding": oracle_grounding,
            "expected_plan": expected_plan,
        })

    def exp_valid(actions, steps, target_ids, avoid=None, prefer=None):
        return {
            "status": "valid",
            "required_actions": actions,
            "max_steps": max(6, len(steps) + 3),
            "expected_steps": steps,
            "expected_target_ids": target_ids,
            "expected_avoid_ids": avoid or [],
            "expected_prefer_ids": prefer or [],
        }

    # ---- single_locate (5)：Grounding 已完成 → 一个 locate_entity 确认步骤 ----
    for i, label in enumerate(LABELS):
        o = obj_ref(canonical, label, i)
        oid = o["node_id"]
        add("single_locate",
            f"Find the {label.replace('_', ' ')}.",
            {"action": "locate", "target": o},
            exp_valid(["locate_entity"],
                      [{"step": 1, "action": "locate_entity", "target_ref": "target", "target_id": oid, "executor": "scene_graph"}],
                      [oid]))

    # ---- single_navigate (8) ----
    for i in range(8):
        label = LABELS[i % len(LABELS)]
        o = obj_ref(canonical, label, i + 1)
        oid = o["node_id"]
        add("single_navigate",
            f"Navigate to the {label.replace('_', ' ')}.",
            {"action": "navigate", "target": o},
            exp_valid(["navigate_to"],
                      [{"step": 1, "action": "navigate_to", "target_ref": "target", "target_id": oid, "executor": "nav2"}],
                      [oid]))

    # ---- locate_then_navigate (7) ----
    for i in range(7):
        label = LABELS[i % len(LABELS)]
        o = obj_ref(canonical, label, i + 2)
        oid = o["node_id"]
        add("locate_then_navigate",
            f"Find the {label.replace('_', ' ')} and then go to it.",
            {"action": "navigate", "target": o},
            exp_valid(["locate_entity", "navigate_to"],
                      [{"step": 1, "action": "locate_entity", "target_ref": "target", "target_id": oid, "executor": "scene_graph"},
                       {"step": 2, "action": "navigate_to", "target_ref": "target", "target_id": oid, "executor": "nav2"}],
                      [oid]))

    # ---- source_destination (6)：room A → room B ----
    for i in range(6):
        src = room_ref(canonical, ROOMS[(i * 2) % len(ROOMS)])
        dst = room_ref(canonical, ROOMS[(i * 2 + 1) % len(ROOMS)])
        add("source_destination",
            f"Navigate from room {src['label']} to room {dst['label']}.",
            {"action": "navigate", "source": src, "destination": dst},
            exp_valid(["navigate_to", "navigate_to"],
                      [{"step": 1, "action": "navigate_to", "target_ref": "source", "target_id": src["node_id"], "executor": "nav2"},
                       {"step": 2, "action": "navigate_to", "target_ref": "destination", "target_id": dst["node_id"], "executor": "nav2"}],
                      [src["node_id"], dst["node_id"]]))

    # ---- escort (5)：navigate to actor → escort actor to destination ----
    for i in range(5):
        a = agent_ref(canonical, i)
        dst = room_ref(canonical, ROOMS[(i + 3) % len(ROOMS)])
        add("escort",
            f"Escort the person to room {dst['label']}.",
            {"action": "escort", "actor": a, "destination": dst},
            exp_valid(["navigate_to", "escort_to"],
                      [{"step": 1, "action": "navigate_to", "target_ref": "actor", "target_id": a["node_id"], "executor": "nav2"},
                       {"step": 2, "action": "escort_to", "target_ref": "", "target_id": None,
                        "role_refs": {"actor": "actor", "destination": "destination"}, "executor": "nav2"}],
                      [a["node_id"], dst["node_id"]]))

    # ---- avoid_constraint (4) ----
    for i in range(4):
        label = LABELS[i % len(LABELS)]
        o = obj_ref(canonical, label, i + 3)
        av = room_ref(canonical, ROOMS[(i + 5) % len(ROOMS)])
        oid, av_id = o["node_id"], av["node_id"]
        add("avoid_constraint",
            f"Navigate to the {label.replace('_', ' ')} while avoiding room {av['label']}.",
            {"action": "navigate", "target": o,
             "constraints": [{"type": "avoid", "node_id": av_id, "node_type": "room", "label": av["label"]}]},
            exp_valid(["navigate_to"],
                      [{"step": 1, "action": "navigate_to", "target_ref": "target", "target_id": oid,
                        "executor": "nav2", "constraints": {"avoid": ["constraint_avoid_0"], "prefer": [], "conditions": []}}],
                      [oid], avoid=[av_id]))

    # ---- prefer_constraint (3) ----
    for i in range(3):
        label = LABELS[(i + 1) % len(LABELS)]
        o = obj_ref(canonical, label, i + 4)
        oid = o["node_id"]
        add("prefer_constraint",
            f"Navigate to the {label.replace('_', ' ')} preferring the shortest path.",
            {"action": "navigate", "target": o,
             "constraints": [{"type": "prefer", "node_id": "prefer_shortest_path", "label": "shortest_path"}]},
            exp_valid(["navigate_to"],
                      [{"step": 1, "action": "navigate_to", "target_ref": "target", "target_id": oid,
                        "executor": "nav2", "constraints": {"avoid": [], "prefer": ["constraint_prefer_0"], "conditions": []}}],
                      [oid], prefer=["prefer_shortest_path"]))

    # ---- ambiguous_grounding (3)：不产生可执行计划 ----
    for i in range(3):
        add("ambiguous_grounding",
            "Find the chair near the desks.",
            {"pipeline_status": "ambiguous"},
            {"status": "ambiguous", "required_actions": [], "max_steps": 0,
             "expected_steps": [], "expected_target_ids": [], "expected_avoid_ids": [], "expected_prefer_ids": []})

    # ---- not_found_grounding (3) ----
    for i in range(3):
        add("not_found_grounding",
            "Find a printer.",
            {"pipeline_status": "not_found"},
            {"status": "target_not_found", "required_actions": [], "max_steps": 0,
             "expected_steps": [], "expected_target_ids": [], "expected_avoid_ids": [], "expected_prefer_ids": []})

    # ---- unsupported_action (2) ----
    for i in range(2):
        add("unsupported_action",
            "Pick up the chair and carry it to the desk.",
            {"pipeline_status": "unsupported"},
            {"status": "unsupported", "required_actions": [], "max_steps": 0,
             "expected_steps": [], "expected_target_ids": [], "expected_avoid_ids": [], "expected_prefer_ids": []})

    # ---- multi_step (4)：step-specific refs ----
    for i in range(4):
        label = LABELS[(i + 2) % len(LABELS)]
        o = obj_ref(canonical, label, i + 5)
        r1 = room_ref(canonical, ROOMS[(i + 6) % len(ROOMS)])
        oid, r1id = o["node_id"], r1["node_id"]
        add("multi_step",
            f"Find the {label.replace('_', ' ')} in room {r1['label']}.",
            {"action": "navigate", "steps": [
                {"target": o, "location": r1},
                {"destination": r1},
            ]},
            exp_valid(["locate_entity", "navigate_to", "navigate_to"],
                      [{"step": 1, "action": "locate_entity", "target_ref": "step_0_target", "target_id": oid, "executor": "scene_graph"},
                       {"step": 2, "action": "navigate_to", "target_ref": "step_0_target", "target_id": oid, "executor": "nav2"},
                       {"step": 3, "action": "navigate_to", "target_ref": "step_1_destination", "target_id": r1id, "executor": "nav2"}],
                      [oid, r1id]))

    return cases


def main():
    canonical = load_canonical()
    cases = build(canonical)

    assert len(cases) == 50, f"case 数 {len(cases)} != 50"
    cids = [c["case_id"] for c in cases]
    assert len(cids) == len(set(cids)), "case_id 重复"
    cat = Counter(c["category"] for c in cases)
    print("类别分布:", dict(cat))

    with open(OUT, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    payload = "\n".join(json.dumps(c, ensure_ascii=False, sort_keys=True) for c in cases)
    manifest = {
        "benchmark_status": "ready",
        "benchmark_file": OUT.name,
        "benchmark_file_hash": hashlib.sha256(payload.encode()).hexdigest()[:16],
        "case_count": len(cases),
        "categories": dict(cat),
        "canonical_graph": str(CANONICAL),
        "canonical_graph_hash": hashlib.sha256(CANONICAL.read_bytes()).hexdigest()[:16],
        "seed": 42,
    }
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"生成 {len(cases)} 条 -> {OUT}")
    for c in cases:
        print(f"  {c['case_id']} [{c['category']}] {c['instruction'][:44]} -> {c['expected_plan']['status']}")


if __name__ == "__main__":
    sys.exit(main())
