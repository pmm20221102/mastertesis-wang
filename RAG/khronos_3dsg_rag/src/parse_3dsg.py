"""Parse Khronos / Hydra Dynamic Scene Graph JSON into SceneGraph.

Supports two formats:
  1. SPARK-DSG format (Khronos output): nodes as list, edges with source/target
  2. Flat format (task planner style): entities + relations lists

The parser is deliberately lenient — missing fields get defaults, no crashes.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from schemas import BBox, SceneEdge, SceneGraph, SceneNode

logger = logging.getLogger(__name__)

# Khronos semantic label -> human-readable name (from hydra_config.yaml)
KHRONOS_LABEL_MAP: dict[int, str] = {
    0: "unknown",
    1: "ventilation_piping",
    2: "small_office_objects",
    3: "large_static_wall_furniture",
    4: "large_office_objects",
    5: "floor",
    6: "ceiling",
    7: "jail_cell",
    8: "bathroom",
    9: "bedroom",
    10: "shelving",
    11: "chairs",
    12: "walls",
    13: "doors",
    14: "elevator",
    15: "signs",
    16: "lighting",
    17: "humans",
    18: "railing",
}

# layer number -> human-readable type name
LAYER_TYPE_MAP: dict[int, str] = {
    2: "object",
    3: "place",
    4: "room",
    5: "building",
}


def load_json(path: str | Path) -> dict[str, Any]:
    """Load a JSON file, return dict."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _safe_get(d: dict, *keys: str, default: Any = None) -> Any:
    """Safely traverse nested dict keys."""
    current = d
    for k in keys:
        if isinstance(current, dict):
            current = current.get(k, default)
        else:
            return default
    return current


def _parse_bbox(attrs: dict) -> BBox | None:
    """Extract bounding box from node attributes."""
    bb = attrs.get("bounding_box")
    if not isinstance(bb, dict):
        return None
    center = bb.get("world_P_center", [0, 0, 0])
    dims = bb.get("dimensions", [0, 0, 0])
    if not isinstance(center, list) or not isinstance(dims, list):
        return None
    return BBox(center=[float(x) for x in center[:3]],
                dimensions=[float(x) for x in dims[:3]])


def _bbox_is_valid(bbox: BBox | None) -> bool:
    """Check if bounding box has non-zero dimensions (not invalid placeholder)."""
    if bbox is None:
        return False
    return any(abs(v) > 1e-9 for v in bbox.dimensions)


def _valid_position(pos) -> bool:
    """Check if position is a valid non-zero 3D coordinate."""
    if not isinstance(pos, list) or len(pos) < 3:
        return False
    try:
        return all(math.isfinite(v) for v in pos[:3]) and any(abs(v) > 1e-6 for v in pos[:3])
    except (TypeError, ValueError):
        return False


def _determine_type(attrs: dict, layer: int, partition: int) -> str:
    """Determine human-readable node type from attributes and layer."""
    # Check explicit type field in attributes
    attr_type = attrs.get("type", "")
    if isinstance(attr_type, str) and attr_type:
        # e.g. "ObjectNodeAttributes", "AgentNodeAttributes"
        clean = attr_type.replace("NodeAttributes", "").lower()
        if clean:
            return clean

    # Check layer name in metadata
    node_type = LAYER_TYPE_MAP.get(layer, "unknown")
    if partition == 97:
        return "agent"
    return node_type


def _parse_spark_dsg(data: dict) -> SceneGraph:
    """Parse SPARK-DSG format (Khronos / Hydra native format)."""
    graph = SceneGraph()

    # Scene metadata
    header = data.get("SPARK_DSG_header", {})
    graph.scene_id = f"khronos_{header.get('project_name', 'unknown')}"

    # Layer name mapping: (layer, partition) -> name
    layer_names: dict[tuple[int, int], str] = {}
    for name, info in data.get("layer_names", {}).items():
        if isinstance(info, dict):
            key = (info.get("layer", 0), info.get("partition", 0))
            layer_names[key] = name.lower()

    # Parse nodes
    nodes_raw = data.get("nodes", [])
    for node in nodes_raw:
        if not isinstance(node, dict):
            continue
        attrs = node.get("attributes", {})
        layer = node.get("layer", 0)
        partition = node.get("partition", 0)
        raw_id = str(node.get("id", ""))

        # Determine semantic label
        sem_label_num = attrs.get("semantic_label")
        label = ""
        if isinstance(sem_label_num, int) and sem_label_num in KHRONOS_LABEL_MAP:
            label = KHRONOS_LABEL_MAP[sem_label_num]
        elif isinstance(sem_label_num, str):
            label = sem_label_num

        # Position: raw position > valid bbox center > empty
        # Raw position is authoritative; invalid bbox [0,0,0] should not override it
        bbox = _parse_bbox(attrs)
        raw_pos = attrs.get("position")
        if _valid_position(raw_pos):
            position = [float(x) for x in raw_pos[:3]]
        elif _bbox_is_valid(bbox):
            position = bbox.center
        else:
            position = []

        first_seen = attrs.get("first_observed_ns")
        if isinstance(first_seen, list):
            first_seen = first_seen[0] if first_seen else None
        last_seen = attrs.get("last_observed_ns")
        if isinstance(last_seen, list):
            last_seen = last_seen[0] if last_seen else None

        sn = SceneNode(
            node_id=raw_id,
            layer=layer,
            partition=partition,
            node_type=_determine_type(attrs, layer, partition),
            label=label,
            name=attrs.get("name", "") or label,
            position=position,
            bbox=bbox,
            color=attrs.get("color") if isinstance(attrs.get("color"), dict) else None,
            is_active=attrs.get("is_active") if isinstance(attrs.get("is_active"), bool) else None,
            first_seen_ns=first_seen,
            last_seen_ns=last_seen,
            raw_attrs=attrs,
        )
        graph.nodes.append(sn)
        graph.node_index[raw_id] = sn

    # Override node_type with layer_names if available
    for sn in graph.nodes:
        key = (sn.layer, sn.partition)
        if key in layer_names:
            sn.node_type = layer_names[key]

    # Parse edges
    for edge in data.get("edges", []):
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source", ""))
        tgt = str(edge.get("target", ""))
        info = edge.get("info", {})
        weight = info.get("weight", 1.0) if isinstance(info, dict) else 1.0
        graph.edges.append(SceneEdge(source_id=src, target_id=tgt, weight=weight))

    return graph


def _parse_flat_format(data: dict) -> SceneGraph:
    """Parse flat format with 'entities' and 'relations' keys."""
    graph = SceneGraph(
        scene_id=data.get("scene_id", "unknown"),
        description=data.get("description", ""),
    )

    for ent in data.get("entities", []):
        if not isinstance(ent, dict):
            continue
        eid = str(ent.get("id", ""))
        pos = ent.get("position")
        position = [float(x) for x in pos[:3]] if isinstance(pos, list) and len(pos) >= 3 else []

        sn = SceneNode(
            node_id=eid,
            node_type=ent.get("type", "unknown"),
            label=ent.get("type", ""),
            name=ent.get("name", ""),
            position=position,
        )
        graph.nodes.append(sn)
        graph.node_index[eid] = sn

    for rel in data.get("relations", []):
        if not isinstance(rel, dict):
            continue
        graph.edges.append(SceneEdge(
            source_id=str(rel.get("from", "")),
            target_id=str(rel.get("to", "")),
            relation=rel.get("relation", "related_to"),
        ))

    return graph


def parse_3dsg(path: str | Path) -> SceneGraph:
    """Auto-detect format and parse 3DSG JSON.

    Returns a SceneGraph with nodes, edges, and node_index populated.
    """
    data = load_json(path)

    # Detect SPARK-DSG format by checking for characteristic keys
    if "nodes" in data and isinstance(data["nodes"], list) and len(data["nodes"]) > 0:
        first = data["nodes"][0]
        if isinstance(first, dict) and "layer" in first and "attributes" in first:
            logger.info("Detected SPARK-DSG format (Khronos / Hydra)")
            return _parse_spark_dsg(data)

    # Flat format with entities + relations
    if "entities" in data and isinstance(data["entities"], list):
        logger.info("Detected flat format (entities + relations)")
        return _parse_flat_format(data)

    # Fallback: try flat format anyway
    logger.warning("Unknown format, attempting flat parse")
    return _parse_flat_format(data)
