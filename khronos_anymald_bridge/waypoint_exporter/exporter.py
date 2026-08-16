"""Khronos PLACES → ANYmal D waypoints.json exporter（MVP）。

核心逻辑（依据 dsg_place_schema_report.md §6/§8）：
- node.id = Place index（保留 Khronos Place ID）
- node.position = attributes.position（原样导出，z 高度差异标注在 export_report，不做静默转换）
- node.orientation = attributes.orientation（当前全 identity → MVP 直接导出，标注 fallback）
- header.frame = map
- 无向 PLACES 边展开为双向有向边（A→B 与 B→A）
- edge policy = config/default_anymal_edge_policy.yaml（ANYmal 私有字段配置化默认值）
"""
import json
from pathlib import Path

import yaml

from dsg_reader.loader import PlacesGraph, load_places
from waypoint_exporter import schema


def load_policy(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def export(graph: PlacesGraph, policy: dict, frame: str | None = None,
           stamp: int = 0) -> dict:
    frame = frame or graph.frame
    waypoints = {"header": {"frame": frame, "stamp": stamp}, "nodes": [], "edges": []}

    for idx in sorted(graph.nodes):
        node = graph.nodes[idx]
        waypoints["nodes"].append(
            schema.make_node(idx, node.position, node.orientation, frame=frame, stamp=stamp)
        )

    edge_id = 1
    for e in graph.edges:
        # 无向 → 双向有向展开
        for a, b in ((e.source, e.target), (e.target, e.source)):
            waypoints["edges"].append(schema.make_edge(edge_id, a, b, policy))
            edge_id += 1

    return waypoints


def write_waypoints(waypoints: dict, out_path: str) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(waypoints, indent=2, ensure_ascii=False), encoding="utf-8")
