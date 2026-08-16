"""Phase 5: Mock waypoint planner 单元测试。"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from waypoint_graph.planner import WaypointGraph, shortest_path  # noqa: E402


def _make_waypoints(nodes_pos, edges):
    """构造 waypoints.json 结构。nodes_pos: {id: [x,y,z]}，edges: [(a,b)] 双向。"""
    nodes = [{"id": i, "pose_stamped": {"position": p}} for i, p in nodes_pos.items()]
    edge_list = []
    eid = 1
    for a, b in edges:
        for x, y in ((a, b), (b, a)):
            edge_list.append({"id": eid, "start_node_id": x, "end_node_id": y,
                              "data": {"traversability": 1, "max_deviation": 3}})
            eid += 1
    return {"header": {"frame": "map"}, "nodes": nodes, "edges": edge_list}


def test_shortest_path_chain():
    # 链 1-2-3-4：1→4 最短路走全链
    wp = _make_waypoints({1: [0, 0, 0], 2: [2, 0, 0], 3: [4, 0, 0], 4: [6, 0, 0]},
                         [(1, 2), (2, 3), (3, 4)])
    g = WaypointGraph(wp)
    path, cost = shortest_path(g, 1, 4)
    assert path == [1, 2, 3, 4]
    assert abs(cost - 6.0) < 1e-6


def test_shortest_path_respects_cost():
    # 欧氏图上 1→4 最短路 cost = 6.0（直连），planner 应给出该最小 cost
    wp = _make_waypoints({1: [0, 0, 0], 2: [2, 0, 0], 3: [4, 0, 0], 4: [6, 0, 0]},
                         [(1, 4), (1, 2), (2, 3), (3, 4)])
    g = WaypointGraph(wp)
    path, cost = shortest_path(g, 1, 4)
    assert cost == 6.0
    assert path[0] == 1 and path[-1] == 4


def test_unreachable_goal():
    # 节点 5 孤立
    wp = _make_waypoints({1: [0, 0, 0], 2: [2, 0, 0], 5: [100, 0, 0]},
                         [(1, 2)])
    g = WaypointGraph(wp)
    path, cost = shortest_path(g, 1, 5)
    assert path is None
    assert cost == float("inf")


def test_invalid_goal():
    wp = _make_waypoints({1: [0, 0, 0], 2: [2, 0, 0]}, [(1, 2)])
    g = WaypointGraph(wp)
    path, cost = shortest_path(g, 1, 999)
    assert path is None


def test_same_node():
    wp = _make_waypoints({1: [0, 0, 0], 2: [2, 0, 0]}, [(1, 2)])
    g = WaypointGraph(wp)
    path, cost = shortest_path(g, 1, 1)
    assert path == [1] and cost == 0.0
