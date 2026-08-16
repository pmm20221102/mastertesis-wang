"""Phase 3R: graph-aware sparsifier 单元测试。

关键验证：
- 连通分量不被合并（source 分量数 = sparse 分量数）
- 不产生跨 source component 的边
- geodesic 覆盖保证（每个 place 到代表 <= R_geo）
- 每条 sparse edge 有 dense witness path
"""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dsg_reader.loader import load_places  # noqa: E402
from waypoint_graph.sparsifier import (  # noqa: E402
    SparseResult, build_adj, connected_components, sparsify_graphaware,
)


def _make_dsg(path, n=6, spacing=0.5, add_isolated=True):
    """n×n 网格（间距 0.5m）+ 可选孤立分量（远处 3 节点链）。"""
    nodes, edges = [], []
    def nid(i, j):
        return (ord("P") << 56) | (1000 + i * 100 + j)
    def add_place(idx, pos):
        nodes.append({"id": (ord("P") << 56) | idx, "layer": 3, "partition": 0,
                      "attributes": {"position": pos,
                                     "orientation": {"w": 1, "x": 0, "y": 0, "z": 0}}})
    for i in range(n):
        for j in range(n):
            add_place(1000 + i * 100 + j, [i * spacing, j * spacing, 1.0])
    for i in range(n):
        for j in range(n):
            if i + 1 < n:
                edges.append({"source": nid(i, j), "target": nid(i + 1, j),
                              "info": {"weight": spacing}})
            if j + 1 < n:
                edges.append({"source": nid(i, j), "target": nid(i, j + 1),
                              "info": {"weight": spacing}})
    if add_isolated:
        # 远处的孤立分量：3 节点链，距离主网格 > 100m
        for k, idx in enumerate([7000, 7001, 7002]):
            add_place(idx, [100.0 + k * 0.5, 100.0, 1.0])
        edges.append({"source": (ord("P") << 56) | 7000,
                      "target": (ord("P") << 56) | 7001, "info": {"weight": 0.5}})
        edges.append({"source": (ord("P") << 56) | 7001,
                      "target": (ord("P") << 56) | 7002, "info": {"weight": 0.5}})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"SPARK_DSG_header": {"version": {}}, "directed": False,
                   "nodes": nodes, "edges": edges}, f)


def test_components_not_merged():
    with tempfile.TemporaryDirectory() as d:
        dsg = os.path.join(d, "grid.json")
        _make_dsg(dsg, n=6, spacing=0.5, add_isolated=True)
        g = load_places(dsg)
        assert len(connected_components(g)) == 2
        res = sparsify_graphaware(g, R_geo=1.0)
        # sparse graph 分量数 == source 分量数（2）
        sparse_comps = connected_components(res.graph)
        assert len(sparse_comps) == 2, f"sparse 分量 {len(sparse_comps)}"


def test_no_cross_component_edges():
    with tempfile.TemporaryDirectory() as d:
        dsg = os.path.join(d, "grid.json")
        _make_dsg(dsg, n=6, spacing=0.5, add_isolated=True)
        g = load_places(dsg)
        res = sparsify_graphaware(g, R_geo=1.0)
        # 每个 sparse edge 的两个端点必须属于 source 的同一分量
        comp_of = {}
        for cid, comp in enumerate(res.components):
            for idx in comp:
                comp_of[idx] = cid
        for e in res.graph.edges:
            assert comp_of[e.source] == comp_of[e.target], \
                f"跨分量边 {e.source}-{e.target}"


def test_geodesic_cover_within_R():
    with tempfile.TemporaryDirectory() as d:
        dsg = os.path.join(d, "grid.json")
        _make_dsg(dsg, n=6, spacing=0.5, add_isolated=False)
        g = load_places(dsg)
        res = sparsify_graphaware(g, R_geo=1.0)
        # 每个 place 到代表 geodesic <= R_geo
        assert all(d <= 1.0 + 1e-6 for d in res.cover_dist.values()), \
            f"max cover = {max(res.cover_dist.values())}"


def test_witness_path_exists():
    with tempfile.TemporaryDirectory() as d:
        dsg = os.path.join(d, "grid.json")
        _make_dsg(dsg, n=6, spacing=0.5, add_isolated=False)
        g = load_places(dsg)
        res = sparsify_graphaware(g, R_geo=1.0)
        assert len(res.graph.edges) > 0
        for (a, b), path in res.witnesses.items():
            assert path[0] == a and path[-1] == b
            assert len(path) >= 2


def test_cover_assign_geodesic():
    with tempfile.TemporaryDirectory() as d:
        dsg = os.path.join(d, "grid.json")
        _make_dsg(dsg, n=6, spacing=0.5, add_isolated=True)
        g = load_places(dsg)
        res = sparsify_graphaware(g, R_geo=1.0)
        # assigned 的每个 place 归属同分量代表
        comp_of = {}
        for cid, comp in enumerate(res.components):
            for idx in comp:
                comp_of[idx] = cid
        for idx, rep in res.assigned.items():
            assert comp_of[idx] == comp_of[rep], "跨分量 assignment"
