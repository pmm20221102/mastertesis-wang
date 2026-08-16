"""semantic_waypoint_mapper 单元测试。"""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dsg_reader.loader import load_places  # noqa: E402
from semantic_waypoint_mapper.mapper import build_semantic_index, load_dsg_rooms  # noqa: E402
from waypoint_graph.sparsifier import sparsify_graphaware  # noqa: E402


def _make_dsg(path):
    """两个 room + 各自 place：R0 有 2 place，R1 有 2 place，互相连接。"""
    nodes, edges = [], []
    def pid(i):
        return (ord("P") << 56) | i
    def rid(i):
        return (ord("R") << 56) | i
    # places: P1000..P1003
    positions = {
        1000: [0.0, 0.0, 1.0], 1001: [2.0, 0.0, 1.0],
        1002: [10.0, 0.0, 1.0], 1003: [12.0, 0.0, 1.0],
    }
    for i, pos in positions.items():
        nodes.append({"id": pid(i), "layer": 3, "partition": 0,
                      "attributes": {"position": pos,
                                     "orientation": {"w": 1, "x": 0, "y": 0, "z": 0}}})
    # rooms R100 (centroid 1,0), R101 (centroid 11,0)
    rooms = {100: [1.0, 0.0, 1.0], 101: [11.0, 0.0, 1.0]}
    for i, pos in rooms.items():
        nodes.append({"id": rid(i), "layer": 4, "partition": 0,
                      "attributes": {"position": pos, "name": ""}})
    # place-place edges: chain P1000-P1001-P1002-P1003
    for a, b in ((1000, 1001), (1001, 1002), (1002, 1003)):
        edges.append({"source": pid(a), "target": pid(b),
                      "info": {"weight": 2.0}})
    # room-place hierarchy: R100 -> P1000,P1001 ; R101 -> P1002,P1003
    for r, pls in ((100, [1000, 1001]), (101, [1002, 1003])):
        for p in pls:
            edges.append({"source": rid(r), "target": pid(p),
                          "info": {"weight": 0.1}})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"SPARK_DSG_header": {"version": {}}, "directed": False,
                   "nodes": nodes, "edges": edges}, f)


def test_load_dsg_rooms():
    with tempfile.TemporaryDirectory() as d:
        dsg = os.path.join(d, "dsg.json")
        _make_dsg(dsg)
        rooms, room_place = load_dsg_rooms(dsg)
        assert set(rooms) == {(ord("R") << 56) | 100, (ord("R") << 56) | 101}
        assert room_place[(ord("R") << 56) | 100] == {1000, 1001}
        assert room_place[(ord("R") << 56) | 101] == {1002, 1003}


def test_build_semantic_index():
    with tempfile.TemporaryDirectory() as d:
        dsg = os.path.join(d, "dsg.json")
        _make_dsg(dsg)
        g = load_places(dsg)
        res = sparsify_graphaware(g, R_geo=3.0)
        idx = build_semantic_index(dsg, res.graph, res.assigned)
        # 每个 place 有映射
        assert len(idx["place_to_waypoint"]) == 4
        # room R100 anchor 应接近 centroid (1,0) → 代表在 P1000 附近
        r100 = idx["room_to_waypoint"][(ord("R") << 56) | 100]
        assert r100["status"] == "ok"
        assert r100["distance_m"] <= 1.0
        r101 = idx["room_to_waypoint"][(ord("R") << 56) | 101]
        assert r101["status"] == "ok"
        assert r101["distance_m"] <= 1.0
        # 两个 room 的 anchor 不同（不同区域）
        assert r100["waypoint_id"] != r101["waypoint_id"]


def test_fail_closed_no_places():
    with tempfile.TemporaryDirectory() as d:
        dsg = os.path.join(d, "dsg.json")
        _make_dsg(dsg)
        # 移除 R101 的所有 place 边
        with open(dsg) as f:
            data = json.load(f)
        data["edges"] = [e for e in data["edges"]
                         if e["target"] == (ord("R") << 56) | 101
                         and e["source"] != (ord("P") << 56) | 1002
                         and e["source"] != (ord("P") << 56) | 1003]
        with open(dsg, "w") as f:
            json.dump(data, f)
        g = load_places(dsg)
        res = sparsify_graphaware(g, R_geo=3.0)
        idx = build_semantic_index(dsg, res.graph, res.assigned)
        r101 = idx["room_to_waypoint"][(ord("R") << 56) | 101]
        assert r101["waypoint_id"] is None
        assert r101["status"] != "ok"
