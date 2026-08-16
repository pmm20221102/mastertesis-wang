"""Phase 8.5R: planner witness cost + witness reverse expansion + launch 静态检查。

对应 phase85_reaudit §1/§2/§3 Fix 3 新增测试。
"""
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "khronos_anymald_rviz"))

from waypoint_graph.planner import WaypointGraph, shortest_path  # noqa: E402
from khronos_anymald_rviz.witness_expand import expand_route  # noqa: E402


# ---------- Fix 1: planner uses witness geodesic cost ----------
def _make_wp():
    return {
        "nodes": [
            {"id": 1, "pose_stamped": {"position": [0.0, 0.0, 0.0]}},
            {"id": 2, "pose_stamped": {"position": [2.0, 0.0, 0.0]}},
            {"id": 3, "pose_stamped": {"position": [1.0, 20.0, 0.0]}},
        ],
        "edges": [
            {"id": 1, "start_node_id": 1, "end_node_id": 2, "data": {}},
            {"id": 2, "start_node_id": 2, "end_node_id": 3, "data": {}},
            {"id": 3, "start_node_id": 1, "end_node_id": 3, "data": {}},
        ],
    }


def test_planner_uses_witness_cost():
    # (1,2) witness geodesic=100（远大于欧氏 2），planner 应使用 witness cost
    wit = {"(1,2)": {"geodesic_m": 100.0}}
    g = WaypointGraph(_make_wp(), witness=wit)
    # 1→2 cost 应为 100（witness），不是欧氏 2
    path, cost = shortest_path(g, 1, 2)
    assert cost == 100.0
    # 1→3: witness 只影响 (1,2)；1-3 直连（欧氏 ~20.02）vs 1-2-3（100 + ~20.02）
    #   → 应走直连 1→3
    path, cost = shortest_path(g, 1, 3)
    assert path == [1, 3]
    assert abs(cost - math.dist([0.0, 0.0, 0.0], [1.0, 20.0, 0.0])) < 1e-6


def test_planner_fallback_euclidean_without_witness():
    g = WaypointGraph(_make_wp(), witness=None)
    path, cost = shortest_path(g, 1, 2)
    assert abs(cost - 2.0) < 1e-6   # fallback 欧氏


# ---------- Fix 2: witness reverse expansion ----------
def _positions():
    # dense 点：150 在 100 与 200 之间
    return {
        100: (0.0, 0.0, 0.0), 150: (1.0, 0.0, 0.0), 200: (2.0, 0.0, 0.0),
    }


def _witness_fixture():
    # stored witness 固定 min→max：(100,200) path=[100,150,200]
    return {"(100,200)": {"path": [100, 150, 200]}}


def test_witness_route_forward():
    pos = _positions()
    wit = _witness_fixture()
    pts = expand_route([100, 200], wit, pos.get, pos.get)
    assert pts == [pos[100], pos[150], pos[200]]


def test_witness_route_reverse():
    pos = _positions()
    wit = _witness_fixture()
    # route 方向 200→100（与 stored min→max 相反）→ 必须 reverse witness
    pts = expand_route([200, 100], wit, pos.get, pos.get)
    assert pts == [pos[200], pos[150], pos[100]]


def test_route_positions_no_endpoint_jump():
    pos = _positions()
    wit = _witness_fixture()
    # 正向与反向展开都应无端点跳跃（相邻点间距 = 1.0m，无 6-10m 瞬移）
    for route in ([100, 200], [200, 100]):
        pts = expand_route(route, wit, pos.get, pos.get)
        for i in range(len(pts) - 1):
            d = math.dist(pts[i], pts[i + 1])
            assert d <= 1.0 + 1e-6, f"route {route} 出现端点跳跃 {d:.1f}m"
            assert d > 1e-9


# ---------- Fix 4: launch / package.xml 静态检查 ----------
def test_launch_config_static():
    pkg = Path(__file__).resolve().parent.parent / "khronos_anymald_rviz"
    launch = pkg / "launch" / "mock_navigation.launch.py"
    assert launch.exists(), "launch 文件缺失"
    text = launch.read_text(encoding="utf-8")
    assert "IfCondition" in text, "launch 应使用 IfCondition"
    assert "publish_stretch" in text, "launch 应传 publish_stretch"
    # package.xml 声明 rviz2 / launch_ros 依赖
    pkgxml = (pkg / "package.xml").read_text(encoding="utf-8")
    for dep in ("ament_index_python", "launch_ros", "rviz2"):
        assert f"<exec_depend>{dep}</exec_depend>" in pkgxml, f"package.xml 缺 {dep}"
