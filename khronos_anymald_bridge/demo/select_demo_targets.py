#!/usr/bin/env python3
"""MD §7/§15: 离线选题辅助 —— 只用于挑选候选语义 phrase，不产生最终 task_plan。

从当前真实 Office RAG/DSG 确定性统计每个 object label 的 room 分布，
报告 "the room with the most X" 类 aggregate 候选的答案与唯一性。

注意（真实数据结论，见 docs/office_semantic_diversity_analysis.md）：
  本数据集中所有 object label 的"最多房间"都是 R19（最大的开放空间），
  即 aggregate "the room with the most X" 收敛到同一房间，无法构造 5 个不同
  room 的纯语义目标。因此最终 Demo 采用 room-number constrained semantic
  navigation（demo/build_final_multistep_taskplan.py）；本工具仅作数据检查。

用法:
  /opt/conda/envs/llm-task-planner/bin/python demo/select_demo_targets.py
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent
DSG_RAG_OUT = Path("/home/ulmmt/Masterthesis-wang/RAG/khronos_3dsg_rag/outputs/office_dense")
GEN = BRIDGE / "generated"

LABEL_PHRASE = {
    "chairs": "the room with the most chairs",
    "small_office_objects": "the room with the most small office items",
    "large_static_wall_furniture": "the room with the most wall furniture",
    "large_office_objects": "the room with the most large office objects",
    "signs": "the room with the most signs",
}


def main() -> int:
    cg = json.load(open(DSG_RAG_OUT / "canonical_graph_v2.json", encoding="utf-8"))
    sem = json.load(open(GEN / "semantic_to_waypoint.json", encoding="utf-8"))
    room_to_wp = sem["room_to_waypoint"]

    # label -> room_id -> count（确定性 canonical 统计）
    label_room = defaultdict(lambda: defaultdict(int))
    for n in cg["nodes"].values():
        if n.get("node_type") == "object" and n.get("label") and n.get("room_id"):
            label_room[n["label"]][n["room_id"]] += 1

    print("=== 离线选题检查：aggregate 'the room with the most X'（真实 canonical 统计）===")
    candidates = []
    for lbl, phrase in LABEL_PHRASE.items():
        if lbl not in label_room:
            print(f"  skip label={lbl}: 无 object")
            continue
        rooms = sorted(label_room[lbl].items(), key=lambda x: -x[1])
        best = rooms[0]
        tie = any(c == best[1] for _, c in rooms[1:])
        ridx = int(best[0]) & ((1 << 56) - 1)
        wp_entry = room_to_wp.get(str(best[0]))
        wp = wp_entry["waypoint_id"] if wp_entry and wp_entry.get("status") == "ok" else None
        candidates.append({
            "human_name": phrase,
            "target_ref": f"R{ridx}",
            "dsg_room_id": str(best[0]),
            "room_index": ridx,
            "waypoint_id": wp,
            "semantic_label": lbl,
            "n_objects": best[1],
            "unique_room": not tie,
        })
        print(f"  {phrase} -> R{ridx} wp={wp} objects={best[1]} unique={not tie}")

    out_dir = BRIDGE / "demo"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / "office_demo_candidates.json"
    out.write_text(json.dumps({
        "selected": candidates,
        "n": len(candidates),
        "note": ("仅用于离线选题/数据检查（md §7）。本数据集所有 label 的 max room 收敛 R19，"
                 "aggregate 无法构造 5 个不同 room；最终 Demo 采用 room-number constrained "
                 "semantic navigation（demo/build_final_multistep_taskplan.py），见 "
                 "docs/office_semantic_diversity_analysis.md"),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n写 {out}（只用于选题，不进入最终 task_plan）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
