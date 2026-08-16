"""Khronos → ANYmal D waypoint exporter 单元测试。

用一个小 fixture DSG 验证 loader / exporter / validator 的核心逻辑。
运行: cd khronos_anymald_bridge && /opt/conda/envs/llm-task-planner/bin/python -m pytest tests/ -q
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dsg_reader.loader import decode_symbol, load_places  # noqa: E402
from waypoint_exporter import schema  # noqa: E402
from waypoint_exporter.exporter import export  # noqa: E402
from waypoint_graph.validator import compare_topology, validate_waypoints  # noqa: E402


def _make_fixture_dsg(path, n_places=5):
    """构造 SPARK-DSG 格式的迷你 DSG：5 个 place 组成一条链。"""
    nodes, edges = [], []
    for i in range(n_places):
        node_id = (ord("P") << 56) | (100 + i)   # P100..P104
        nodes.append({
            "id": node_id, "layer": 3, "partition": 0,
            "attributes": {
                "position": [float(i), 0.0, 1.5],
                "orientation": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
                "distance": 0.8,
                "type": "PlaceNodeAttributes",
            },
        })
    for i in range(n_places - 1):
        a = (ord("P") << 56) | (100 + i)
        b = (ord("P") << 56) | (100 + i + 1)
        edges.append({"source": a, "target": b,
                      "info": {"type": "EdgeAttributes", "weight": 1.0, "weighted": True}})
    data = {"SPARK_DSG_header": {"version": {"major": 1, "minor": 1, "patch": 3}},
            "directed": False, "nodes": nodes, "edges": edges}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _make_policy():
    return {
        "local_planner": {"mode_name": "default", "planner_name": "default",
                          "planner_parameters": ""},
        "locomotion_controller": {"motion_parameters": "default", "motion_state": "default"},
        "max_deviation": 3,
        "traversability": 1,
        "perception_parameters": {"nfm_preset": "autodeduce"},
        "version": 1,
    }


def test_decode_symbol():
    nid = (ord("P") << 56) | 368
    key, idx = decode_symbol(nid)
    assert key == "P" and idx == 368


def test_load_places():
    with tempfile.TemporaryDirectory() as d:
        dsg = os.path.join(d, "dsg.json")
        _make_fixture_dsg(dsg, n_places=5)
        g = load_places(dsg)
        assert len(g.nodes) == 5
        assert len(g.edges) == 4
        assert 100 in g.nodes and 104 in g.nodes
        assert g.nodes[102].position == [2.0, 0.0, 1.5]
        assert g.nodes[102].orientation["w"] == 1.0


def test_export_schema():
    wp = export(load_places(_tmp_dsg()), _make_policy())
    # 5 nodes, 4 无向边 → 8 有向边
    assert len(wp["nodes"]) == 5
    assert len(wp["edges"]) == 8
    assert wp["header"]["frame"] == "map"
    n0 = wp["nodes"][0]
    assert n0["id"] == 100
    assert n0["pose_stamped"]["position"] == [0.0, 0.0, 1.5]
    assert n0["pose_stamped"]["orientation"] == [1.0, 0.0, 0.0, 0.0]
    assert n0["pose_stamped"]["tolerance"] == [0, 0]
    e0 = wp["edges"][0]
    assert e0["data"]["max_deviation"] == 3
    assert e0["data"]["traversability"] == 1
    assert e0["data"]["perception_parameters"]["nfm_preset"] == "autodeduce"


def test_export_bidirectional():
    wp = export(load_places(_tmp_dsg()), _make_policy())
    directed = {(e["start_node_id"], e["end_node_id"]) for e in wp["edges"]}
    # 链 P100-P101-P102：应有 (100,101) 和 (101,100) 等双向
    assert (100, 101) in directed and (101, 100) in directed
    assert (101, 102) in directed and (102, 101) in directed
    # 不应有自环
    assert all(e["start_node_id"] != e["end_node_id"] for e in wp["edges"])


def test_validator_and_topology(tmp_path):
    wp = export(load_places(_tmp_dsg()), _make_policy())
    out = tmp_path / "waypoints.json"
    out.write_text(json.dumps(wp, indent=2), encoding="utf-8")
    v = validate_waypoints(str(out))
    assert v["valid"] is True, v["errors"]
    assert v["stats"]["n_nodes"] == 5
    assert v["stats"]["n_edges"] == 8
    assert v["stats"]["n_components"] == 1
    t = compare_topology(load_places(_tmp_dsg()), str(out))
    assert t["topology_preserved"] is True


def _tmp_dsg():
    import tempfile
    d = tempfile.mkdtemp()
    dsg = os.path.join(d, "dsg.json")
    _make_fixture_dsg(dsg, n_places=5)
    return dsg


def test_validator_rejects_duplicate_edge(tmp_path):
    wp = export(load_places(_tmp_dsg()), _make_policy())
    # 人为制造重复有向边
    wp["edges"].append(dict(wp["edges"][0], id=9999))
    out = tmp_path / "bad.json"
    out.write_text(json.dumps(wp), encoding="utf-8")
    v = validate_waypoints(str(out))
    assert v["valid"] is False
    assert any("重复有向边" in e for e in v["errors"])
