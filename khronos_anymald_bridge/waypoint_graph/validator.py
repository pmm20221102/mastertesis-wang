"""ANYmal D waypoints.json 严格校验 + 与 source PLACES 拓扑对比。

校验项（计划文档 §16）：
  JSON parse / header.frame / node id 唯一 / edge id 唯一 /
  edge source/target 存在 / 有限 XYZ / 合法四元数 / 重复有向边 / self-loop /
  连通分量 / 孤立节点
"""
import json
import math
from pathlib import Path


def _valid_quat(o) -> bool:
    if not isinstance(o, (list, tuple)) or len(o) != 4:
        return False
    try:
        n = math.sqrt(sum(float(v) * float(v) for v in o))
    except (TypeError, ValueError):
        return False
    return math.isfinite(n) and abs(n - 1.0) < 1e-3


def _components(nodes, edges):
    adj = {i: [] for i in nodes}
    for e in edges:
        adj.setdefault(e["start_node_id"], []).append(e["end_node_id"])
        adj.setdefault(e["end_node_id"], []).append(e["start_node_id"])
    visited, comps = set(), []
    for i in nodes:
        if i in visited:
            continue
        stack, comp = [i], []
        visited.add(i)
        while stack:
            c = stack.pop()
            comp.append(c)
            for nb in adj.get(c, []):
                if nb not in visited:
                    visited.add(nb)
                    stack.append(nb)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    return comps


def validate_waypoints(wp_path) -> dict:
    errors, warnings = [], []
    with open(wp_path, encoding="utf-8") as f:
        wp = json.load(f)

    header = wp.get("header", {})
    if header.get("frame") != "map":
        errors.append("header.frame != map")

    nodes = wp.get("nodes", [])
    edges = wp.get("edges", [])
    node_ids = [n.get("id") for n in nodes]
    if len(node_ids) != len(set(node_ids)):
        errors.append("node id 不唯一")

    edge_ids = [e.get("id") for e in edges]
    if len(edge_ids) != len(set(edge_ids)):
        errors.append("edge id 不唯一")

    node_set = set(node_ids)
    seen_pairs = set()
    for e in edges:
        s, t = e.get("start_node_id"), e.get("end_node_id")
        if s not in node_set:
            errors.append(f"edge {e.get('id')} 起点 {s} 不存在")
        if t not in node_set:
            errors.append(f"edge {e.get('id')} 终点 {t} 不存在")
        if s == t:
            errors.append(f"edge {e.get('id')} self-loop")
        pair = (s, t)
        if pair in seen_pairs:
            errors.append(f"重复有向边 {s}->{t}")
        seen_pairs.add(pair)

    for n in nodes:
        nid = n.get("id")
        pose = n.get("pose_stamped", {})
        pos = pose.get("position", [])
        if len(pos) < 3 or not all(math.isfinite(float(v)) for v in pos[:3]):
            errors.append(f"node {nid} position 非有限或缺失")
        if not _valid_quat(pose.get("orientation", [])):
            errors.append(f"node {nid} orientation 非法四元数")

    comps = _components(node_ids, edges)
    stats = {
        "n_nodes": len(nodes),
        "n_edges": len(edges),
        "n_components": len(comps),
        "component_sizes": [len(c) for c in comps],
        "isolated_nodes": sum(1 for c in comps if len(c) == 1),
        "n_duplicate_directed": len(edges) - len(seen_pairs),
    }
    return {"valid": len(errors) == 0, "errors": errors,
            "warnings": warnings, "stats": stats}


def compare_topology(source_graph, wp_path: str) -> dict:
    """source PLACES 无向图 vs exported 双向图：连通分量一致性对比。"""
    exported = validate_waypoints(wp_path)

    adj = {idx: [] for idx in source_graph.nodes}
    for e in source_graph.edges:
        adj.setdefault(e.source, []).append(e.target)
        adj.setdefault(e.target, []).append(e.source)
    visited, comps = set(), []
    for i in source_graph.nodes:
        if i in visited:
            continue
        stack, comp = [i], []
        visited.add(i)
        while stack:
            c = stack.pop()
            comp.append(c)
            for nb in adj.get(c, []):
                if nb not in visited:
                    visited.add(nb)
                    stack.append(nb)
        comps.append(comp)
    comps.sort(key=len, reverse=True)

    return {
        "source_n_nodes": len(source_graph.nodes),
        "source_n_edges": len(source_graph.edges),
        "source_n_components": len(comps),
        "source_component_sizes": [len(c) for c in comps],
        "exported_n_components": exported["stats"]["n_components"],
        "exported_component_sizes": exported["stats"]["component_sizes"],
        "topology_preserved": sorted(len(c) for c in comps)
            == sorted(exported["stats"]["component_sizes"]),
    }
