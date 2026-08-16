"""Khronos SPARK-DSG → PLACES 拓扑图加载（只读，不修改 Khronos）。

依据 Phase 1 审计（docs/dsg_place_schema_report.md）：
- PLACES layer = 3, partition = 0
- node id = NodeSymbol（高 8 位层字符 + 低 56 位 index）
- position = attributes.position [x,y,z]
- orientation = attributes.orientation {w,x,y,z}（当前全 identity）
- edges 无向（directed=false），weight = 欧氏距离
"""
import json
from dataclasses import dataclass, field


def decode_symbol(nid: int) -> tuple[str, int]:
    """NodeSymbol 解码：高 8 位为层字符 key，低 56 位为 index。"""
    key = (nid >> 56) & 0xFF
    index = nid & ((1 << 56) - 1)
    return chr(key), index


@dataclass
class PlaceNode:
    index: int          # NodeSymbol index（P368 → 368）
    node_id: int        # 64 位原始 NodeId
    position: list      # [x, y, z]
    orientation: dict   # {w, x, y, z}
    distance: float     # GVD 到最近障碍距离（信息参考，不用于自动推断）


@dataclass
class PlaceEdge:
    source: int         # Place index
    target: int         # Place index
    weight: float       # 欧氏距离


@dataclass
class PlacesGraph:
    frame: str
    nodes: dict[int, PlaceNode]   # key = index
    edges: list[PlaceEdge]
    n_total_nodes: int = 0
    n_total_edges: int = 0


def load_places(dsg_path: str, layer: int = 3, partition: int = 0,
                frame: str = "map") -> PlacesGraph:
    with open(dsg_path, encoding="utf-8") as f:
        data = json.load(f)

    nodes: dict[int, PlaceNode] = {}
    for n in data.get("nodes", []):
        if not isinstance(n, dict):
            continue
        if n.get("layer") != layer or n.get("partition") != partition:
            continue
        nid = int(n["id"])
        _, idx = decode_symbol(nid)
        attrs = n.get("attributes", {}) or {}
        pos = attrs.get("position")
        orientation = attrs.get("orientation", {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0})
        nodes[idx] = PlaceNode(
            index=idx,
            node_id=nid,
            position=[float(v) for v in pos[:3]] if pos else [],
            orientation=orientation,
            distance=float(attrs.get("distance", 0.0)),
        )

    place_ids = {v.node_id for v in nodes.values()}
    edges: list[PlaceEdge] = []
    for e in data.get("edges", []):
        if not isinstance(e, dict):
            continue
        src, tgt = int(e.get("source", 0)), int(e.get("target", 0))
        if src not in place_ids or tgt not in place_ids:
            continue
        _, si = decode_symbol(src)
        _, ti = decode_symbol(tgt)
        info = e.get("info", {}) or {}
        edges.append(PlaceEdge(si, ti, float(info.get("weight", 1.0))))

    return PlacesGraph(
        frame=frame,
        nodes=nodes,
        edges=edges,
        n_total_nodes=len(data.get("nodes", [])),
        n_total_edges=len(data.get("edges", [])),
    )
