#!/usr/bin/env python3
"""MD 修正版：真实 LLM+RAG 驱动 5 目标 task_plan（禁止反向伪造）。

复用 run_joint_eval_v3.run_pipeline（benchmark 验证的稳定路径）：
  自然语言 → parse_intent_with_llm(LLM) → search_semantic(Semantic RAG 预测 label)
  → room_tail_to_id + canonical 确定性 grounding（label + room 过滤）→ resolved object/room
→ room_id → semantic_to_waypoint → waypoint → task_plan（navigate_to）

目标：5 个 object 语义 + room 编号（5 个不同 room，均 ≠ start R0）。

用法:
  /opt/conda/envs/llm-task-planner/bin/python demo/build_real_taskplan.py [--model mistral:latest]
"""
import argparse
import json
import sys
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent
LLM_RAG = Path("/home/ulmmt/Masterthesis-wang/LLM-robot-taskplanning-chaining-rag")
RAG_SRC = Path("/home/ulmmt/Masterthesis-wang/RAG/khronos_3dsg_rag/src")
EVAL_DIR = Path("/home/ulmmt/Masterthesis-wang/RAG/khronos_3dsg_rag/semantic_eval")
DSG_RAG_OUT = Path("/home/ulmmt/Masterthesis-wang/RAG/khronos_3dsg_rag/outputs/office_dense")
GEN = BRIDGE / "generated"

for p in (str(LLM_RAG), str(RAG_SRC), str(EVAL_DIR)):
    sys.path.insert(0, p)

from run_joint_eval_v3 import run_pipeline  # noqa: E402
from main_rag import load_config  # noqa: E402
from waypoint_graph.planner import WaypointGraph, shortest_path  # noqa: E402

# 5 个语义目标：object + room 编号（5 个不同 room ≠ R0；R4/R7/R12/R15 唯一 object，R6 room_set）
TARGETS = [
    {"human_name": "the chair in room 0756",
     "instruction": "navigate to a chair in room 0756"},
    {"human_name": "the wall-mounted storage unit in room 0759",
     "instruction": "navigate to a wall-mounted storage unit in room 0759"},
    {"human_name": "the large office machine in room 0764",
     "instruction": "navigate to the large office machine in room 0764"},
    {"human_name": "the small office item in room 0767",
     "instruction": "navigate to a small office item in room 0767"},
    {"human_name": "all the small office items in room 0758",
     "instruction": "navigate to all the small office items in room 0758"},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mistral:latest")
    args = ap.parse_args()

    config = load_config(str(LLM_RAG / "config_rag.yaml"))
    canonical = json.load(open(DSG_RAG_OUT / "canonical_graph_v2.json", encoding="utf-8"))
    sem = json.load(open(GEN / "semantic_to_waypoint.json", encoding="utf-8"))
    room_to_wp = sem["room_to_waypoint"]

    task_chain = []
    trace_lines = ["[Intent] PASS", "[Planner] PASS"]
    all_ok = True
    print("=== 真实 LLM+RAG grounding（run_pipeline）===")
    for i, t in enumerate(TARGETS, 1):
        pred = run_pipeline(t["instruction"], args.model, None, canonical, config)
        if pred.get("error"):
            print(f"[{i}] ERROR: {pred['error']}  instr={t['instruction']}")
            all_ok = False
            continue
        intent = pred.get("intent", {})
        gr = pred.get("grounding", {})
        room_id = gr.get("room_id")
        status = gr.get("status")
        node_ids = gr.get("node_ids", [])
        # room → waypoint
        wp = None
        if room_id:
            entry = room_to_wp.get(str(room_id))
            if entry and entry.get("status") == "ok":
                wp = entry["waypoint_id"]
        ridx = (int(room_id) & ((1 << 56) - 1)) if room_id else None
        print(f"[{i}] instr={t['instruction']}")
        print(f"    intent.action={intent.get('action')} target={intent.get('target_text')} "
              f"room_text={intent.get('room_text')}")
        print(f"    semantic.top1={pred.get('semantic', {}).get('top1_label')}")
        print(f"    grounding.status={status} room=R{ridx} objects={len(node_ids)} "
              f"waypoint={wp}")
        if status not in ("resolved", "resolved_set") or wp is None:
            print(f"    ** 目标 {i} grounding 非 resolved/resolved_set / 无 waypoint")
            all_ok = False
            continue
        task_chain.append({
            "step": i,
            "action": "navigate_to",
            "target_type": "room",
            "target_ref": f"R{ridx}",
            "target_id": str(room_id),
            "human_name": t["human_name"],
            "instruction": t["instruction"],
            "llm_intent": intent,
            "waypoint_id": wp,
        })
        trace_lines.append(f"[Grounding:R{ridx}] PASS (waypoint {wp})")

    if len(task_chain) != 5:
        print(f"\nERROR: 只有 {len(task_chain)}/5 目标 grounding 成功，需全部成功")
        return 1

    # TSP 最近邻排序（从 start R0 出发），使视频路线最短
    graph = WaypointGraph.load(str(GEN / "waypoints_sparsified_R3.0.json"),
                               str(GEN / "witness_paths.json"))
    start_wp = None
    for v in room_to_wp.values():
        if v.get("room_index") == 0 and v.get("status") == "ok":
            start_wp = v["waypoint_id"]
            break
    remaining = list(task_chain)
    ordered = []
    cur = start_wp
    while remaining:
        best = min(remaining, key=lambda s: shortest_path(graph, cur, s["waypoint_id"])[1])
        ordered.append(best)
        cur = best["waypoint_id"]
        remaining.remove(best)
    task_chain = ordered
    # 回写 step 顺序
    for i, s in enumerate(task_chain, 1):
        s["step"] = i

    task_plan = {"status": "valid", "source": "real LLM+RAG (run_pipeline) grounding",
                 "task_chain": task_chain}
    (BRIDGE / "demo" / "office_5checkpoint_task_plan.json").write_text(
        json.dumps(task_plan, indent=2, ensure_ascii=False), encoding="utf-8")
    # 自然语言指令（人类可读）
    names = [t["human_name"] for t in TARGETS]
    instr = ("机器人从当前位置出发，先前往" + "，然后依次前往".join(f"【{n}】" for n in names) +
             "。请严格按照这个顺序导航，每到一个目标后短暂停留，再继续前往下一个地点。")
    (BRIDGE / "demo" / "office_5checkpoint_instruction.txt").write_text(instr + "\n",
                                                                        encoding="utf-8")
    # 真实 grounding trace（按 task_chain 实际顺序）
    trace = {
        "natural_language_instruction": instr,
        "targets": [{
            "human_name": s["human_name"],
            "instruction": s["instruction"],
            "llm_intent": s["llm_intent"],
            "target_ref": s["target_ref"],
            "target_id": s["target_id"],
            "waypoint_id": s["waypoint_id"],
        } for s in task_chain],
    }
    (BRIDGE / "demo" / "office_5checkpoint_rag_trace.json").write_text(
        json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== 5-Checkpoint 目标表（真实 LLM+RAG grounding）===")
    print(f"{'#':<2}{'自然语言语义':<42}{'room':<6}{'waypoint'}")
    for i, s in enumerate(task_chain, 1):
        print(f"{i:<2}{s['human_name']:<42}{s['target_ref']:<6}{s['waypoint_id']}")
    print("\n自然语言指令:")
    print(instr)
    print("\n写 demo/office_5checkpoint_task_plan.json + instruction.txt + rag_trace.json")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
