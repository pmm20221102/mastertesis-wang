"""最终 Demo 多步集成测试（final_demo_llm_rag_authenticity_fix.md §17）。

验证最终产物（demo/build_final_multistep_taskplan.py 生成）满足上层语义约束：
  - final_instruction 是完整纯英文；room number 为显式空间约束（本数据集缺乏
    room-level 语义 label/attributes，见 docs/office_semantic_diversity_analysis.md），
    但不得出现 R* 内部 ref / DSG 大整数 ID / waypoint ID
  - 整条指令由冻结 LLM Planner 分解为 exactly 5 navigate steps（禁止改写）
  - 顺序保留（禁止 TSP 重排）
  - 5/5 Semantic RAG + Graph Grounding resolved
  - 5 target_ref 全部可转 waypoint、5 route 全部 reachable、目标房间互异且 ≠ start
不调 LLM（产物已由真实冻结 pipeline 生成）；本测试验证产物满足全部验收约束。

运行: /opt/conda/envs/llm-task-planner/bin/python -m pytest tests/test_final_demo_multistep.py -q
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from navigation_goal_compiler import NavigationGoalCompiler  # noqa: E402
from waypoint_graph.planner import WaypointGraph, shortest_path  # noqa: E402

BRIDGE = Path(__file__).resolve().parent.parent
DEMO = BRIDGE / "demo"
GEN = BRIDGE / "generated"
START_ROOM_INDEX = 0
EXPECTED_STEPS = 5


def _load(name):
    return json.load(open(DEMO / name, encoding="utf-8"))


def _instruction_text():
    return (DEMO / "final_instruction.txt").read_text(encoding="utf-8").strip()


def _instruction_room_order(instruction):
    return re.findall(r"room (\d{2,})", instruction)


def test_final_instruction_exists_and_english():
    instr = _instruction_text()
    assert instr, "final_instruction.txt 为空"
    # 纯英文：无中文字符
    assert not re.search(r"[一-鿿]", instr), "指令含中文字符"
    # 无内部 ID：R* 内部 ref、DSG 大整数 ID、waypoint 字样
    assert not re.search(r"\bR\d+\b", instr), "指令含 R* 内部 ref"
    assert not re.search(r"\b\d{12,20}\b", instr), "指令含 DSG 大整数 ID"
    assert "waypoint" not in instr.lower(), "指令含 waypoint ID 字样"


def test_llm_plan_exactly_5_navigate_steps():
    plan = _load("final_llm_plan.json")
    intent = plan["intent"]
    steps = intent.get("steps") or [intent]
    assert len(steps) == EXPECTED_STEPS, \
        f"LLM 分解 {len(steps)} 步，要求 exactly {EXPECTED_STEPS}"
    for i, s in enumerate(steps, 1):
        assert s["action"] == "navigate", \
            f"step{i} action={s['action']}，要求 navigate（禁止 locate→navigate 改写）"


def test_llm_plan_order_matches_instruction():
    plan = _load("final_llm_plan.json")
    intent = plan["intent"]
    steps = intent.get("steps") or [intent]
    rooms = []
    for s in steps:
        t = s.get("target") or s.get("destination") or {}
        rooms.append(t.get("room"))
    instr_rooms = _instruction_room_order(_instruction_text())
    assert rooms == instr_rooms, \
        f"LLM step 顺序 {rooms} != 指令顺序 {instr_rooms}（禁止 TSP 重排）"


def test_grounding_all_resolved():
    trace = _load("final_grounding_trace.json")
    steps = trace["steps"]
    assert len(steps) == EXPECTED_STEPS
    for s in steps:
        assert s["llm_action"] == "navigate", f"step{s['step']} action={s['llm_action']}"
        assert s["semantic"]["top1_label"], f"step{s['step']} Semantic RAG 无 top1 label"
        assert s["grounding"]["status"] == "resolved", \
            f"step{s['step']} grounding={s['grounding']['status']}"
        assert s["waypoint_id"] is not None, f"step{s['step']} 无 waypoint"


def test_task_plan_structure_and_upstream():
    tp = _load("final_task_plan.json")
    chain = tp["task_chain"]
    assert tp["status"] == "valid"
    assert len(chain) == EXPECTED_STEPS
    for c in chain:
        assert c["action"] == "navigate_to"
        assert c["target_id"] and c["target_ref"] and c["waypoint_id"]
    uv = tp["upstream_validation"]
    assert uv["actual_navigation_steps"] == uv["expected_navigation_steps"] == EXPECTED_STEPS
    assert uv["all_action_navigate"] is True
    assert uv["order_preserved"] is True
    assert uv["no_tsp_reorder"] is True
    assert uv["grounding_success"] == EXPECTED_STEPS
    assert uv["semantic_retrieval_success"] == EXPECTED_STEPS
    assert uv["target_rooms_unique"] is True
    assert uv["start_excluded"] is True
    assert uv["all_routes_reachable"] is True


def test_target_rooms_unique_and_not_start():
    tp = _load("final_task_plan.json")
    rooms = [int(c["target_ref"][1:]) for c in tp["task_chain"]]
    assert len(set(rooms)) == EXPECTED_STEPS, f"目标房间不互异 {rooms}"
    assert all(r != START_ROOM_INDEX for r in rooms), "目标包含 start R0"


def test_target_refs_compile_to_waypoints():
    tp = _load("final_task_plan.json")
    compiler = NavigationGoalCompiler(str(GEN / "semantic_to_waypoint.json"))
    for c in tp["task_chain"]:
        wp = compiler.compile(c["target_id"])  # DSG room id → waypoint
        assert wp == c["waypoint_id"], \
            f"compile({c['target_id']})={wp} != task_plan waypoint {c['waypoint_id']}"


def test_all_routes_reachable():
    tp = _load("final_task_plan.json")
    graph = WaypointGraph.load(str(GEN / "waypoints_sparsified_R3.0.json"),
                               str(GEN / "witness_paths.json"))
    compiler = NavigationGoalCompiler(str(GEN / "semantic_to_waypoint.json"))
    start_wp = compiler.compile("R0")
    cur = start_wp
    for c in tp["task_chain"]:
        path, cost = shortest_path(graph, cur, c["waypoint_id"])
        assert path is not None, f"NO_PATH {cur} → {c['waypoint_id']}"
        assert cost > 0, f"route cost 非正（段 cost={cost}，需非零导航）"
        cur = c["waypoint_id"]


def test_manifest_has_model_reproducibility():
    mf = _load("final_demo_manifest.json")
    assert mf["model"]["name"]
    assert mf["model"]["digest"], "manifest 缺 model digest（md §24 复现要求）"
    assert mf["timestamp"], "manifest 缺 timestamp"
    assert mf["inference"]["options"]["temperature"] is not None
