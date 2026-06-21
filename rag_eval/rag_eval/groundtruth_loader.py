from __future__ import annotations

import csv
import json
import logging
import sys
from pathlib import Path

RAG_SRC = Path(__file__).parent.parent.parent / "RAG" / "khronos_3dsg_rag" / "src"
sys.path.insert(0, str(RAG_SRC))

from .schemas import (
    GraphEdge,
    GraphGroundTruth,
    GraphNode,
    GroundTruthChange,
    GroundTruthObject,
)

logger = logging.getLogger(__name__)

# Khronos semantic label -> human-readable name
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

LAYER_TYPE_MAP: dict[int, str] = {
    2: "object",
    3: "place",
    4: "room",
    5: "building",
}

CONTAINER_TYPES = {"room", "rooms", "place", "places", "building", "buildings"}


def _find_gt_dir(base_dir: str | Path) -> Path:
    base = Path(base_dir)
    if (base / "gt_dsg_consolidated.json").exists():
        return base
    for sub in base.iterdir():
        if sub.is_dir() and (sub / "gt_dsg_consolidated.json").exists():
            return sub
    raise FileNotFoundError(f"gt_dsg_consolidated.json not found under {base_dir}")


def _parse_object_symbol(name: str) -> str:
    if not name:
        return ""
    return name.strip()


def load_groundtruth_objects(dataset: str, gt_dir: str | Path) -> list[GroundTruthObject]:
    gt_path = _find_gt_dir(gt_dir) / "gt_dsg_consolidated.json"
    logger.info(f"Loading groundtruth from {gt_path}")

    with open(gt_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    objects = []
    for node in data.get("nodes", []):
        attrs = node.get("attributes", {})
        bbox = attrs.get("bounding_box", {})
        center = bbox.get("world_P_center", [0, 0, 0])
        dims = bbox.get("dimensions", [0, 0, 0])

        first_seen = attrs.get("first_observed_ns", 0)
        if isinstance(first_seen, list):
            first_seen = first_seen[0] if first_seen else 0
        last_seen = attrs.get("last_observed_ns", 0)
        if isinstance(last_seen, list):
            last_seen = last_seen[0] if last_seen else 0

        objects.append(GroundTruthObject(
            dataset=dataset,
            object_id=node.get("id", 0),
            object_symbol=_parse_object_symbol(attrs.get("name", "")),
            position=[float(x) for x in center[:3]] if isinstance(center, list) else [0, 0, 0],
            dimensions=[float(x) for x in dims[:3]] if isinstance(dims, list) else [0, 0, 0],
            first_observed_ns=int(first_seen) if first_seen else 0,
            last_observed_ns=int(last_seen) if last_seen else 0,
            is_active=bool(attrs.get("is_active", False)),
        ))

    logger.info(f"Loaded {len(objects)} groundtruth objects for {dataset}")
    return objects


def load_groundtruth_changes(dataset: str, gt_dir: str | Path) -> list[GroundTruthChange]:
    gt_base = _find_gt_dir(gt_dir)
    csv_path = gt_base / "gt_changes.csv"

    if not csv_path.exists():
        logger.warning(f"No gt_changes.csv found at {csv_path}")
        return []

    changes = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = row.get("ObjectSymbol", "").strip()
            appeared = int(row.get("AppearedAt", 0) or 0)
            disappeared = int(row.get("DisappearedAt", 0) or 0)
            changes.append(GroundTruthChange(
                dataset=dataset,
                object_symbol=symbol,
                appeared_at=appeared,
                disappeared_at=disappeared,
            ))

    logger.info(f"Loaded {len(changes)} changes for {dataset}")
    return changes


def _determine_node_type(attrs: dict, layer: int) -> str:
    attr_type = attrs.get("type", "")
    if isinstance(attr_type, str) and attr_type:
        clean = attr_type.replace("NodeAttributes", "").lower()
        if clean in ("agent", "object", "place", "room", "semantic"):
            return clean
    return LAYER_TYPE_MAP.get(layer, "unknown")


def _get_label(attrs: dict, layer: int) -> str:
    sem_label = attrs.get("semantic_label")
    if isinstance(sem_label, int) and sem_label in KHRONOS_LABEL_MAP:
        return KHRONOS_LABEL_MAP[sem_label]
    return LAYER_TYPE_MAP.get(layer, "unknown")


def load_graph_groundtruth(dataset: str, dsg_path: str | Path) -> GraphGroundTruth:
    logger.info(f"Loading graph groundtruth from {dsg_path}")

    with open(dsg_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes = []
    node_index = {}
    for raw_node in data.get("nodes", []):
        attrs = raw_node.get("attributes", {})
        layer = raw_node.get("layer", 0)
        node_id = str(raw_node.get("id", ""))

        bbox = attrs.get("bounding_box", {})
        center = bbox.get("world_P_center", [0, 0, 0])

        node = GraphNode(
            node_id=node_id,
            layer=layer,
            node_type=_determine_node_type(attrs, layer),
            label=_get_label(attrs, layer),
            name=attrs.get("name", "") or "",
            position=[float(x) for x in center[:3]] if isinstance(center, list) else [0, 0, 0],
        )
        nodes.append(node)
        node_index[node_id] = node

    edges = []
    for raw_edge in data.get("edges", []):
        src = str(raw_edge.get("source", ""))
        tgt = str(raw_edge.get("target", ""))
        info = raw_edge.get("info", {})
        relation = info.get("relation", "connected_to") if isinstance(info, dict) else "connected_to"

        edges.append(GraphEdge(source_id=src, target_id=tgt, relation=relation))

        if src in node_index and tgt in node_index:
            src_node = node_index[src]
            tgt_node = node_index[tgt]
            if src_node.layer > tgt_node.layer:
                src_node.child_ids.append(tgt)
                tgt_node.parent_ids.append(src)
            elif tgt_node.layer > src_node.layer:
                tgt_node.child_ids.append(src)
                src_node.parent_ids.append(tgt)
            else:
                src_node.neighbor_ids.append(tgt)
                tgt_node.neighbor_ids.append(src)

    gt = GraphGroundTruth(dataset=dataset, nodes=nodes, edges=edges, node_index=node_index)
    logger.info(f"Loaded graph groundtruth: {len(nodes)} nodes, {len(edges)} edges")
    return gt


def load_graph_groundtruth_from_rag(dataset: str, rag_jsonl_path: str | Path) -> GraphGroundTruth:
    logger.info(f"Loading graph groundtruth from RAG JSONL: {rag_jsonl_path}")

    nodes = []
    node_index = {}
    edges = []

    with open(rag_jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            doc = json.loads(line)
            meta = doc.get("metadata", {})
            chunk_type = meta.get("chunk_type", "")

            if chunk_type == "entity":
                node_id = meta.get("node_id", "")
                if not node_id:
                    continue

                parent_ids = meta.get("parent_ids", [])
                if isinstance(parent_ids, str):
                    try:
                        parent_ids = json.loads(parent_ids)
                    except (json.JSONDecodeError, TypeError):
                        parent_ids = []

                child_ids = meta.get("child_ids", [])
                if isinstance(child_ids, str):
                    try:
                        child_ids = json.loads(child_ids)
                    except (json.JSONDecodeError, TypeError):
                        child_ids = []

                neighbor_ids = meta.get("neighbor_ids", [])
                if isinstance(neighbor_ids, str):
                    try:
                        neighbor_ids = json.loads(neighbor_ids)
                    except (json.JSONDecodeError, TypeError):
                        neighbor_ids = []

                node = GraphNode(
                    node_id=node_id,
                    layer=meta.get("layer", 0),
                    node_type=meta.get("node_type", "unknown"),
                    label=meta.get("label", ""),
                    name=meta.get("object_symbol", ""),
                    position=meta.get("bounding_box_center", []) if isinstance(meta.get("bounding_box_center"), list) else [],
                    parent_ids=parent_ids,
                    child_ids=child_ids,
                    neighbor_ids=neighbor_ids,
                )
                nodes.append(node)
                node_index[node_id] = node

    gt = GraphGroundTruth(dataset=dataset, nodes=nodes, edges=edges, node_index=node_index)
    logger.info(f"Loaded graph groundtruth from RAG: {len(nodes)} nodes")
    return gt
