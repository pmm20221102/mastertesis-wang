#!/usr/bin/env python3
"""最终 Demo 上层语义真实性修正：一条完整指令 → 正式 LLM Planner → 5 步 → RAG/Grounding。

对照 final_demo_llm_rag_authenticity_fix.md（2026-08-11）：
  - 整条 final_instruction 一次进入冻结 Intent Parser（parse_intent_with_llm），
    由其分解为 ordered navigation steps（禁止 Python 预先拆句 / TSP 重排）。
  - 每个 step 真实进入冻结 Semantic RAG（search_semantic → predicted label）
    与 Graph Grounding（canonical room + label 一致性，predicted 驱动、无 GT 泄漏）。
  - room number 是显式空间约束（本数据集缺乏足够 room-level 语义 label/attributes，
    见 docs/office_semantic_diversity_analysis.md）；语义 object label 仍由 LLM+RAG
    解析与 grounding。本 Demo 不声称验证无编号 semantic room discovery。
  - 5/5 action=navigate 必须由 LLM 真实输出，禁止 locate→navigate 改写。
  - 同一份 final_task_plan.json 直接驱动 run_office_e2e 与 RViz mock robot。

用法:
  /opt/conda/envs/llm-task-planner/bin/python demo/build_final_multistep_taskplan.py \
      [--model mistral:latest]
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent
DEMO = BRIDGE / "demo"
LLM_ROOT = Path("/home/ulmmt/Masterthesis-wang/LLM-robot-taskplanning-chaining-rag")
RAG_ROOT = Path("/home/ulmmt/Masterthesis-wang/RAG/khronos_3dsg_rag")
DSG_RAG_OUT = RAG_ROOT / "outputs" / "office_dense"
GEN = BRIDGE / "generated"
VECTOR_DB = DSG_RAG_OUT / "vector_db"

for p in (str(BRIDGE), str(LLM_ROOT), str(RAG_ROOT / "src"),
          str(RAG_ROOT / "semantic_eval")):
    sys.path.insert(0, p)

from llm.intent_parser import parse_intent_with_llm  # noqa: E402
from main_rag import load_config  # noqa: E402
from semantic_retriever import search_semantic  # noqa: E402
from waypoint_graph.planner import WaypointGraph, shortest_path  # noqa: E402

TOP_K = 5
THRESHOLD = 0.35
START_ROOM_INDEX = 0
EXPECTED_STEPS = 5

# 五个目标：语义 object + room number（benchmark 已验证 room 级稳定）。
# room number 为显式空间约束；顺序即用户给定顺序，禁止 TSP 重排。
TARGETS = [
    {"text": "chair", "room": "0756"},
    {"text": "wall-mounted storage unit", "room": "0759"},
    {"text": "small office item", "room": "0758"},
    {"text": "large office machine", "room": "0764"},
    {"text": "wall-mounted storage unit", "room": "0767"},
]


def build_instruction(targets):
    leads = ["first", "then", "then", "then", "and finally"]
    parts = ", ".join(f"{leads[i]} navigate to the {t['text']} in room {t['room']}"
                      for i, t in enumerate(targets))
    return ("Starting from your current position, " + parts +
            ". Stop briefly at each destination before continuing to the next one.")


def room_tail_to_id(canonical, tail):
    m = re.search(r"(\d{2,})", str(tail))
    digits = m.group(1) if m else str(tail).strip()
    t = digits.lstrip("0")
    for nid, n in canonical["nodes"].items():
        if n.get("node_type") == "room":
            if str(nid).endswith(digits) or str(nid)[-4:].lstrip("0") == t:
                return str(nid)
    return None


def build_label_room_index(canonical):
    idx = defaultdict(lambda: defaultdict(int))
    for n in canonical["nodes"].values():
        if n.get("node_type") == "object" and n.get("label") and n.get("room_id"):
            idx[n["label"]][n["room_id"]] += 1
    return idx


def step_target(step):
    for attr in ("target", "destination"):
        ref = getattr(step, attr, None)
        if ref is not None and getattr(ref, "text", None):
            return ref
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mistral:latest")
    args = ap.parse_args()
    model = args.model

    config = load_config(str(LLM_ROOT / "config_rag.yaml"))
    canonical = json.load(open(DSG_RAG_OUT / "canonical_graph_v2.json", encoding="utf-8"))
    label_room = build_label_room_index(canonical)
    sem = json.load(open(GEN / "semantic_to_waypoint.json", encoding="utf-8"))
    room_to_wp = sem["room_to_waypoint"]

    instruction = build_instruction(TARGETS)
    print("=== 最终自然语言指令（一条完整英文）===")
    print(instruction)

    # STEP 1: 整条指令 → 冻结 LLM Planner（正式 Intent Parser 分解）
    intent = parse_intent_with_llm(instruction, model=model, config=config)
    llm_plan = intent.model_dump(mode="json")
    steps = list(intent.steps) if intent.steps else [intent]

    # STEP 2: 验证 —— exactly 5 navigate steps、顺序 = 用户顺序（无 TSP）
    actions = [s.action for s in steps]
    rooms_llm = []
    for s in steps:
        ref = step_target(s)
        rooms_llm.append(ref.room if ref else None)
    expected_rooms = [t["room"] for t in TARGETS]
    ok_plan = (len(steps) == EXPECTED_STEPS
               and all(a == "navigate" for a in actions)
               and rooms_llm == expected_rooms)
    if not ok_plan:
        print(f"\nERROR: LLM Planner 输出 {len(steps)} 步 actions={actions}")
        print(f"  rooms={rooms_llm}  expected={expected_rooms}"
              f"（要求 exactly 5、5/5 navigate、顺序保留）")
        print("  本轮不做任何 locate→navigate 改写 / TSP 重排；请调整指令后重跑。")
        return 1

    # STEP 3: 每个 step → 冻结 Semantic RAG + Graph Grounding
    trace_steps = []
    task_chain = []
    print("\n=== 每步 Semantic RAG + Graph Grounding ===")
    for i, (s, tgt) in enumerate(zip(steps, TARGETS), 1):
        ref = step_target(s)
        text = ref.text
        room_text = ref.room
        # Semantic RAG（真实检索 → predicted label）
        semres = search_semantic(text, top_k=TOP_K, db_dir=str(VECTOR_DB),
                                 threshold=THRESHOLD)
        plabel = semres.label_ranking[0].label if semres.label_ranking else ""
        ranking = [x.label for x in semres.label_ranking]
        # Graph Grounding（确定性 canonical room + label 一致性，predicted 驱动）
        room_id = room_tail_to_id(canonical, room_text) if room_text else None
        n_in_room = (label_room.get(plabel, {}).get(room_id, 0)
                     if (plabel and room_id) else 0)
        status = "resolved" if (room_id and n_in_room > 0) else "not_found"
        entry = room_to_wp.get(str(room_id)) if room_id else None
        wp = entry["waypoint_id"] if entry and entry.get("status") == "ok" else None
        ridx = (int(room_id) & ((1 << 56) - 1)) if room_id else None
        print(f"  step{i}: text={text!r} room={room_text} → R{ridx} "
              f"semantic.top1={plabel} n_in_room={n_in_room} status={status} wp={wp}")
        trace_steps.append({
            "step": i,
            "llm_action": s.action,
            "target_text": text,
            "room_number": room_text,
            "semantic": {"query": text, "top1_label": plabel, "label_ranking": ranking},
            "grounding": {"status": status, "room_id": room_id,
                          "n_label_objects_in_room": n_in_room, "room_index": ridx},
            "waypoint_id": wp,
        })
        if status == "resolved" and wp is not None:
            task_chain.append({
                "step": i,
                "action": "navigate_to",
                "target_type": "room",
                "target_ref": f"R{ridx}",
                "target_id": str(room_id),
                "human_name": f"the {text} in room {room_text}",
                "waypoint_id": wp,
            })
        else:
            print(f"  ** step{i} grounding 失败（{status}），不进入最终 task_plan")
            return 1

    # STEP 4: 验证 —— 5 个不同 room、≠ start
    rooms_plan = [int(c["target_ref"][1:]) for c in task_chain]
    if len(set(rooms_plan)) != EXPECTED_STEPS:
        print(f"\nERROR: 目标房间不互异 {rooms_plan}")
        return 1
    if any(r == START_ROOM_INDEX for r in rooms_plan):
        print(f"\nERROR: 目标包含 start R{START_ROOM_INDEX}")
        return 1

    # STEP 5: route 可达性验证（5 段非零，witness projected_xy cost）
    graph = WaypointGraph.load(str(GEN / "waypoints_sparsified_R3.0.json"),
                               str(GEN / "witness_paths.json"))
    start_wp = None
    for v in room_to_wp.values():
        if v.get("room_index") == START_ROOM_INDEX and v.get("status") == "ok":
            start_wp = v["waypoint_id"]
            break
    routes = []
    cur = start_wp
    for c in task_chain:
        path, cost = shortest_path(graph, cur, c["waypoint_id"])
        if path is None:
            print(f"\nERROR: NO_PATH {cur} → {c['waypoint_id']}")
            return 1
        routes.append({"from_wp": cur, "to_wp": c["waypoint_id"],
                       "cost_m": round(cost, 1)})
        cur = c["waypoint_id"]
        print(f"  route {routes[-1]['from_wp']}→{routes[-1]['to_wp']} cost={cost:.1f}m")
    total_cost = round(sum(r["cost_m"] for r in routes), 1)

    # STEP 6: 上游验证证据（非硬编码，供 run_office_e2e 实际检查）
    upstream = {
        "expected_navigation_steps": EXPECTED_STEPS,
        "actual_navigation_steps": len(task_chain),
        "all_action_navigate": all(s["llm_action"] == "navigate" for s in trace_steps),
        "order_preserved": rooms_llm == expected_rooms,
        "no_tsp_reorder": True,
        "grounding_success": sum(1 for s in trace_steps
                                 if s["grounding"]["status"] == "resolved"),
        "grounding_total": len(trace_steps),
        "semantic_retrieval_success": sum(1 for s in trace_steps
                                          if s["semantic"]["top1_label"]),
        "target_rooms": rooms_plan,
        "target_rooms_unique": len(set(rooms_plan)) == EXPECTED_STEPS,
        "start_excluded": all(r != START_ROOM_INDEX for r in rooms_plan),
        "routes": routes,
        "total_route_cost_m": total_cost,
        "all_routes_reachable": True,
        "steps": trace_steps,
    }

    task_plan = {
        "status": "valid",
        "source": "final multistep: frozen LLM Planner → Semantic RAG → Graph Grounding",
        "input_instruction": instruction,
        "task_chain": task_chain,
        "upstream_validation": upstream,
    }

    # STEP 7: 写最终产物
    DEMO.mkdir(exist_ok=True)
    (DEMO / "final_instruction.txt").write_text(instruction + "\n", encoding="utf-8")
    (DEMO / "final_llm_plan.json").write_text(
        json.dumps({"instruction": instruction, "intent": llm_plan},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    (DEMO / "final_grounding_trace.json").write_text(
        json.dumps({"instruction": instruction, "steps": trace_steps},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    (DEMO / "final_task_plan.json").write_text(
        json.dumps(task_plan, indent=2, ensure_ascii=False), encoding="utf-8")

    # STEP 8: manifest（模型复现信息，md §24）
    manifest = {
        "pipeline": "Natural Language → Frozen LLM Intent/Planner → Frozen Semantic RAG → "
                    "Frozen Graph Grounding → semantic_to_waypoint → waypoint → witness route",
        "model": {"name": model},
        "inference": {"options": config.get("options", {}),
                      "ollama_host": config.get("ollama_host", "")},
        "timestamp": datetime.now().isoformat(),
        "datasets": {
            "canonical_graph": str(DSG_RAG_OUT / "canonical_graph_v2.json"),
            "semantic_to_waypoint": str(GEN / "semantic_to_waypoint.json"),
            "waypoints": str(GEN / "waypoints_sparsified_R3.0.json"),
            "witness": str(GEN / "witness_paths.json"),
        },
        "task_plan": str(DEMO / "final_task_plan.json"),
    }
    try:
        from ollama import Client
        from main_rag import get_ollama_host
        client = Client(host=get_ollama_host(config))
        for m in client.list().models:
            if m.model == model:
                manifest["model"]["digest"] = m.digest
                manifest["model"]["size_bytes"] = m.size
                if hasattr(m.details, "model_dump"):
                    manifest["model"]["details"] = m.details.model_dump()
                break
        import subprocess
        try:
            out = subprocess.run(["ollama", "--version"], capture_output=True,
                                 text=True, timeout=30)
            manifest["ollama_version"] = (out.stdout or out.stderr).strip()
        except Exception as e:
            manifest["ollama_version_error"] = str(e)
    except Exception as e:
        manifest["model"]["digest_error"] = str(e)
    (DEMO / "final_demo_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== 最终验证 ===")
    print(f"  LLM Planner: {len(task_chain)} steps, 5/5 navigate, 顺序保留（无 TSP）")
    print(f"  Semantic RAG: {upstream['semantic_retrieval_success']}/5 top1 label")
    print(f"  Graph Grounding: {upstream['grounding_success']}/5 resolved")
    print(f"  目标房间: {rooms_plan}（互异、≠ start R0）")
    print(f"  路线总 cost: {total_cost}m（5 段全可达）")
    print("\n写 demo/final_instruction.txt, final_llm_plan.json, final_grounding_trace.json,"
          " final_task_plan.json, final_demo_manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
