"""生成文件与用户 ANYmal waypoint 样本的 schema 结构一致性对比。

只验证字段结构 / 类型（接口契约），不断言任何具体取值或取值分布，
避免把样本的 groundtruth 数据写入测试判据。
运行: /opt/conda/envs/llm-task-planner/bin/python -m pytest tests/ -q
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BRIDGE = Path(__file__).resolve().parent.parent
SAMPLE = BRIDGE / "inputs" / "waypoints_sample.json"
GENERATED = BRIDGE / "generated" / "waypoints.json"


def _keys(d):
    if isinstance(d, dict):
        return set(d.keys())
    return set()


def test_schema_top_level():
    s = json.load(open(SAMPLE, encoding="utf-8"))
    g = json.load(open(GENERATED, encoding="utf-8"))
    assert _keys(s) == _keys(g) == {"header", "nodes", "edges"}
    assert _keys(s["header"]) == _keys(g["header"]) == {"frame", "stamp"}


def test_node_schema():
    s = json.load(open(SAMPLE, encoding="utf-8"))
    g = json.load(open(GENERATED, encoding="utf-8"))
    sn, gn = s["nodes"][0], g["nodes"][0]
    # node 顶层字段一致（结构契约）
    assert _keys(sn) == _keys(gn) == {"id", "pose_stamped"}
    assert _keys(sn["pose_stamped"]) == _keys(gn["pose_stamped"]) == {
        "header", "orientation", "position", "tolerance"}
    assert _keys(sn["pose_stamped"]["header"]) == _keys(gn["pose_stamped"]["header"]) == {
        "frame", "stamp"}
    # 类型一致
    assert len(gn["pose_stamped"]["orientation"]) == 4
    assert len(gn["pose_stamped"]["position"]) == 3
    assert len(gn["pose_stamped"]["tolerance"]) == 2
    assert gn["pose_stamped"]["header"]["frame"] == "map"


def test_edge_schema():
    s = json.load(open(SAMPLE, encoding="utf-8"))
    g = json.load(open(GENERATED, encoding="utf-8"))
    se, ge = s["edges"][0], g["edges"][0]
    assert _keys(se) == _keys(ge) == {"id", "start_node_id", "end_node_id", "data"}
    # data 顶层字段一致（结构契约）
    assert _keys(se["data"]) == _keys(ge["data"])
    assert _keys(se["data"]["local_planner"]) == _keys(ge["data"]["local_planner"])
    assert _keys(se["data"]["locomotion_controller"]) == _keys(ge["data"]["locomotion_controller"])
    assert _keys(se["data"]["perception_parameters"]) == _keys(ge["data"]["perception_parameters"])
    # 只验证类型与结构，不断言具体取值 / 取值分布
    assert isinstance(ge["data"]["traversability"], (int, float))
    assert isinstance(ge["data"]["max_deviation"], (int, float))
    assert isinstance(ge["data"]["version"], (int, float))
    assert isinstance(ge["data"]["perception_parameters"]["nfm_preset"], str)
    assert all(isinstance(v, str) for v in ge["data"]["local_planner"].values())
    assert all(isinstance(v, str) for v in ge["data"]["locomotion_controller"].values())


def test_edge_values_are_positive_finite():
    """中立断言：策略值必须是有穷正数（不依赖样本的取值分布）。"""
    g = json.load(open(GENERATED, encoding="utf-8"))
    import math
    for e in g["edges"]:
        assert isinstance(e["data"]["traversability"], (int, float))
        assert math.isfinite(e["data"]["traversability"])
        assert e["data"]["traversability"] > 0
        assert isinstance(e["data"]["max_deviation"], (int, float))
        assert math.isfinite(e["data"]["max_deviation"])
        assert e["data"]["max_deviation"] > 0
