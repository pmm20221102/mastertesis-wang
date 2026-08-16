#!/usr/bin/env python3
"""MD Stage 5/6: 从选定的 5 个语义目标构造 task_plan + demo_manifest + 自然语言指令。

target_id 来自 select_demo_targets 的真实 RAG canonical aggregate grounding
（非人工输入）。输出:
  demo/office_5checkpoint_task_plan.json   （5 个 navigate_to）
  demo/office_5checkpoint_instruction.txt  （人类自然语言指令）
  generated/demo_manifest.json             （数据一致性 manifest）
"""
import json
import sys
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent
DSG = "/home/ulmmt/Masterthesis-wang/scene-graph-khronos/snapshots/office_launch_dense/2026_08_03-16_46_38/dsg.json"


def main() -> int:
    targets = json.load(open(BRIDGE / "demo" / "office_5checkpoint_targets.json",
                             encoding="utf-8"))
    sel = targets["selected"]
    if len(sel) != 5:
        print(f"ERROR: 需要 5 个目标，当前 {len(sel)}")
        return 1

    # TSP 最近邻排序：从 start(R0) 出发，每次去最近的未访问目标（witness cost），
    # 使最终视频路线最短（MD §14 ~114m 预期）
    sys.path.insert(0, str(BRIDGE))
    from waypoint_graph.planner import WaypointGraph, shortest_path  # noqa: E402
    graph = WaypointGraph.load(str(BRIDGE / "generated" / "waypoints_sparsified_R3.0.json"),
                               str(BRIDGE / "generated" / "witness_paths.json"))
    sem = json.load(open(BRIDGE / "generated" / "semantic_to_waypoint.json", encoding="utf-8"))
    start_wp = None
    for v in sem["room_to_waypoint"].values():
        if v.get("room_index") == 0 and v.get("status") == "ok":
            start_wp = v["waypoint_id"]
            break
    if start_wp is None:
        print("ERROR: 无 R0 anchor")
        return 1
    remaining = list(sel)
    ordered = []
    cur = start_wp
    while remaining:
        best = min(remaining, key=lambda t: shortest_path(graph, cur, t["waypoint_id"])[1])
        ordered.append(best)
        cur = best["waypoint_id"]
        remaining.remove(best)
    sel = ordered
    # 回写 targets 文件（顺序一致）
    targets["selected"] = sel
    (BRIDGE / "demo" / "office_5checkpoint_targets.json").write_text(
        json.dumps(targets, indent=2, ensure_ascii=False), encoding="utf-8")

    task_chain = []
    for i, t in enumerate(sel):
        task_chain.append({
            "step": i + 1,
            "action": "navigate_to",
            "target_type": "room",
            "target_ref": t["target_ref"],
            "target_id": t["dsg_room_id"],
            "human_name": t["human_name"],
            "waypoint_id": t["waypoint_id"],
            "description": t["human_name"],
        })
    task_plan = {
        "status": "valid",
        "source": "office_dense canonical RAG aggregate grounding",
        "task_chain": task_chain,
    }
    (BRIDGE / "demo" / "office_5checkpoint_task_plan.json").write_text(
        json.dumps(task_plan, indent=2, ensure_ascii=False), encoding="utf-8")

    # 自然语言指令（人类可读，无 R* ID）
    names = [t["human_name"] for t in sel]
    instr = ("机器人从当前位置出发，先前往" + "，然后依次前往".join(
        f"【{n}】" for n in names) +
        "。请严格按照这个顺序导航，每到一个目标后短暂停留，再继续前往下一个地点。")
    (BRIDGE / "demo" / "office_5checkpoint_instruction.txt").write_text(
        instr + "\n", encoding="utf-8")

    # demo_manifest（MD Stage 0）
    import hashlib
    dsg_hash = hashlib.sha256(open(DSG, "rb").read()).hexdigest()[:16]
    manifest = {
        "dsg_snapshot": DSG,
        "dsg_sha256": dsg_hash,
        "rag_output_dir": "/home/ulmmt/Masterthesis-wang/RAG/khronos_3dsg_rag/outputs/office_dense",
        "waypoint_graph": "generated/waypoints_sparsified_R3.0.json",
        "semantic_index": "generated/semantic_to_waypoint.json",
        "witness": "generated/witness_paths.json",
        "R_geo": 3.0,
        "ground_projection": 0.0,
    }
    (BRIDGE / "generated" / "demo_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=== 5-Checkpoint 目标表 ===")
    print(f"{'#':<2}{'自然语言语义':<45}{'RAG room':<9}{'waypoint'}")
    for i, t in enumerate(sel, 1):
        print(f"{i:<2}{t['human_name']:<45}{t['target_ref']:<9}{t['waypoint_id']}")
    print("\n自然语言指令:")
    print(instr)
    print("\n写 demo/office_5checkpoint_task_plan.json + instruction.txt + demo_manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
