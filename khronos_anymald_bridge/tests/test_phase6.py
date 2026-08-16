"""Phase 6: NavigationGoalCompiler + MockExecutor 单元测试。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mock_anymald.executor import MockExecutor  # noqa: E402
from navigation_goal_compiler import InvalidGoal, NavigationGoalCompiler  # noqa: E402
from waypoint_graph.planner import WaypointGraph  # noqa: E402


def _make_semantic_index(path):
    # room R100/R101 各一个 waypoint，place 1000/1001 → waypoint 1000/1001
    r100 = (ord("R") << 56) | 100
    r101 = (ord("R") << 56) | 101
    idx = {
        "schema_version": 1,
        "frame": "map",
        "room_to_waypoint": {
            str(r100): {"waypoint_id": 1000, "room_index": 100, "status": "ok"},
            str(r101): {"waypoint_id": 1001, "room_index": 101, "status": "ok"},
        },
        "place_to_waypoint": {"1000": 1000, "1001": 1001},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(idx, f)
    return r100, r101


def _make_graph():
    nodes = [{"id": i, "pose_stamped": {"position": [float(i), 0, 1.0]}}
             for i in (1000, 1001)]
    edges = [{"id": 1, "start_node_id": 1000, "end_node_id": 1001,
              "data": {}}, {"id": 2, "start_node_id": 1001, "end_node_id": 1000,
                            "data": {}}]
    return {"nodes": nodes, "edges": edges}


def test_compiler_room_id():
    with tempfile.TemporaryDirectory() as d:
        sp = os.path.join(d, "sem.json")
        r100, _ = _make_semantic_index(sp)
        c = NavigationGoalCompiler(sp)
        assert c.compile(r100) == 1000
        assert c.compile("R101") == 1001
        # 纯数字小整数有歧义（room index vs place index），fail closed 拒绝
        try:
            c.compile("1000")
            assert False, "歧义 target_ref 应抛 InvalidGoal"
        except InvalidGoal:
            pass


def test_compiler_place_id():
    with tempfile.TemporaryDirectory() as d:
        sp = os.path.join(d, "sem.json")
        _make_semantic_index(sp)
        c = NavigationGoalCompiler(sp)
        pid = (ord("P") << 56) | 1001
        assert c.compile(pid) == 1001


def test_compiler_invalid():
    with tempfile.TemporaryDirectory() as d:
        sp = os.path.join(d, "sem.json")
        _make_semantic_index(sp)
        c = NavigationGoalCompiler(sp)
        try:
            c.compile("R999")
            assert False, "应抛 InvalidGoal"
        except InvalidGoal:
            pass


def test_mock_executor_reached():
    g = WaypointGraph(_make_graph())
    ex = MockExecutor(g)
    res = ex.run(1000, 1001)
    assert res["state"] == MockExecutor.REACHED
    assert res["route"] == [1000, 1001]


def test_mock_executor_invalid_goal():
    g = WaypointGraph(_make_graph())
    ex = MockExecutor(g)
    res = ex.run(1000, 9999)
    assert res["state"] == MockExecutor.INVALID_GOAL
