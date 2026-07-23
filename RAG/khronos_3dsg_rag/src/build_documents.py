"""Convert parsed SceneGraph into RAG document chunks.

Generates five types of chunks:
  1. Entity chunks — one per node (with graph-aware metadata)
  2. Context chunks — rich context with resolved names and hierarchy paths
  3. Region chunks — aggregated per room/place/building
  4. Relation chunks — one per edge / spatial relationship
  5. Materialized relation chunks — pre-materialized paths
  6. Alias chunks — keyword expansion from config

Graph-Aware Enhancements:
  - parent_ids, child_ids, neighbor_ids in metadata
  - depth (hierarchy level) in metadata
  - pre-materialized relation paths for fast traversal
  - Context chunks with resolved room/building names
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from schemas import RagDocument, SceneGraph, SceneNode

logger = logging.getLogger(__name__)

# Types that act as "containers" (region/room/place/building)
CONTAINER_TYPES = {"room", "rooms", "place", "places", "building", "buildings", "region", "floor"}

# Layer -> depth mapping
LAYER_DEPTH: dict[int, int] = {
    5: 0,  # building
    4: 1,  # room
    3: 2,  # place
    2: 3,  # object
}


def _fmt_pos(position: list[float]) -> str:
    if not position or len(position) < 2:
        return ""
    parts = [f"x={position[0]:.2f}"]
    if len(position) > 1:
        parts.append(f"y={position[1]:.2f}")
    if len(position) > 2:
        parts.append(f"z={position[2]:.2f}")
    return ", ".join(parts)


def _ns_to_s(ns: int | list | None) -> str:
    """Convert nanoseconds to seconds string. Handles int, list, or None."""
    if ns is None or ns == 0:
        return ""
    if isinstance(ns, list):
        ns = ns[0] if ns else 0
    if not isinstance(ns, (int, float)):
        return ""
    if ns == 0:
        return ""
    return f"{ns / 1e9:.1f}s"


def _build_graph_index(graph: SceneGraph) -> dict[str, dict[str, list[str]]]:
    """Build adjacency index: node_id -> {"parents": [...], "children": [...], "neighbors": [...]}."""
    index: dict[str, dict[str, list[str]]] = {}
    for node in graph.nodes:
        index[node.node_id] = {"parents": [], "children": [], "neighbors": []}

    for edge in graph.edges:
        src = graph.node_index.get(edge.source_id)
        tgt = graph.node_index.get(edge.target_id)
        if not src or not tgt:
            continue

        if src.node_id in index and tgt.node_id in index:
            if src.layer > tgt.layer:
                index[src.node_id]["children"].append(tgt.node_id)
                index[tgt.node_id]["parents"].append(src.node_id)
            elif tgt.layer > src.layer:
                index[tgt.node_id]["children"].append(src.node_id)
                index[src.node_id]["parents"].append(tgt.node_id)
            else:
                index[src.node_id]["neighbors"].append(tgt.node_id)
                index[tgt.node_id]["neighbors"].append(src.node_id)

    return index


def _build_relation_materialization(graph: SceneGraph) -> dict[str, list[str]]:
    """Pre-materialize common relation paths for fast traversal."""
    materialized: dict[str, list[str]] = {}

    # Group children by parent
    parent_children: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        src = graph.node_index.get(edge.source_id)
        tgt = graph.node_index.get(edge.target_id)
        if src and tgt:
            if src.layer > tgt.layer:
                parent_children[tgt.node_id].append(src.node_id)
            elif tgt.layer > src.layer:
                parent_children[src.node_id].append(tgt.node_id)

    # Store as JSON-serializable format
    for parent_id, children_ids in parent_children.items():
        materialized[f"{parent_id}_contains"] = children_ids

    return materialized


def build_entity_chunks(
    graph: SceneGraph,
    dataset: str = "",
    source_file: str = "",
    graph_index: dict[str, dict[str, list[str]]] | None = None,
) -> list[RagDocument]:
    """One document per node with graph-aware metadata."""
    if graph_index is None:
        graph_index = _build_graph_index(graph)

    docs = []
    for node in graph.nodes:
        parts: list[str] = []

        # Header
        noun = node.node_type.capitalize() or "Entity"
        label_str = f" ({node.label})" if node.label and node.label != node.node_type else ""
        parts.append(f"{noun} {node.node_id}{label_str}.")

        # Name
        if node.name and node.name != node.label:
            parts.append(f"Name: {node.name}.")

        # Position
        if node.position:
            parts.append(f"Position: {_fmt_pos(node.position)}.")

        # Bounding box size
        if node.bbox and any(d > 0 for d in node.bbox.dimensions):
            d = node.bbox.dimensions
            parts.append(f"Size: {d[0]:.2f} x {d[1]:.2f} x {d[2]:.2f}m.")

        # Activity status
        if node.is_active is not None:
            parts.append(f"Active: {'yes' if node.is_active else 'no'}.")

        # Time range
        t_first = _ns_to_s(node.first_seen_ns)
        t_last = _ns_to_s(node.last_seen_ns)
        if t_first or t_last:
            time_str = f"Observed from {t_first}" if t_first else "Observed"
            if t_last:
                time_str += f" to {t_last}"
            parts.append(time_str + ".")

        # Graph-aware: parents, children, neighbors
        node_index = graph_index.get(node.node_id, {"parents": [], "children": [], "neighbors": []})
        parents = node_index["parents"]
        children = node_index["children"]
        neighbors = node_index["neighbors"]

        if parents:
            parts.append(f"Parent: {', '.join(parents[:3])}.")
        if children:
            parts.append(f"Contains: {', '.join(children[:5])}.")
        if neighbors:
            parts.append(f"Connected to: {', '.join(neighbors[:5])}.")

        text = " ".join(parts)

        docs.append(RagDocument(
            doc_id=f"entity_{node.node_id}",
            text=text,
            metadata={
                "chunk_type": "entity",
                "node_id": node.node_id,
                "node_type": node.node_type,
                "label": node.label,
                "room": parents[0] if parents else "",
                "related_nodes": neighbors[:5],
                # Graph-aware metadata
                "parent_ids": parents[:5],
                "child_ids": children[:10],
                "neighbor_ids": neighbors[:10],
                "depth": LAYER_DEPTH.get(node.layer, -1),
                # Existing metadata
                "object_symbol": node.name if node.name else "",
                "bounding_box_center": node.bbox.center if node.bbox else [],
                "bounding_box_dimensions": node.bbox.dimensions if node.bbox else [],
                "first_observed_ns": node.first_seen_ns if node.first_seen_ns else 0,
                "last_observed_ns": node.last_seen_ns if node.last_seen_ns else 0,
                "is_active": node.is_active if node.is_active is not None else False,
                "layer": node.layer,
                "dataset": dataset,
                "source_file": source_file,
            },
        ))

    return docs


# ---------------------------------------------------------------------------
# Context chunks: rich context with resolved names and hierarchy paths
# ---------------------------------------------------------------------------

def _resolve_node_name(node_id: str, graph: SceneGraph) -> str:
    """Resolve a node ID to a human-readable name or label."""
    node = graph.node_index.get(node_id)
    if not node:
        return node_id
    if node.name and node.name != node.label:
        return f"{node.label or node.node_type} '{node.name}'"
    if node.label:
        return node.label
    if node.node_type:
        return node.node_type
    return node_id


def _build_hierarchy_path(
    node_id: str,
    graph_index: dict[str, dict[str, list[str]]],
    graph: SceneGraph,
) -> str:
    """Build human-readable hierarchy path from building to node.

    Example: "Building -> Room 201 -> Place -> Object"
    """
    path_parts: list[str] = []
    current = node_id
    visited: set[str] = set()

    # Walk up the tree
    for _ in range(10):  # Max depth limit
        if current in visited:
            break
        visited.add(current)

        node = graph.node_index.get(current)
        if not node:
            break

        name = _resolve_node_name(current, graph)
        path_parts.append(name)

        parents = graph_index.get(current, {}).get("parents", [])
        if not parents:
            break
        current = parents[0]  # Take first parent

    path_parts.reverse()
    return " -> ".join(path_parts)


def build_context_chunks(
    graph: SceneGraph,
    dataset: str = "",
    source_file: str = "",
    graph_index: dict[str, dict[str, list[str]]] | None = None,
) -> list[RagDocument]:
    """Build context-aware chunks for each object node.

    Each chunk includes:
    - Semantic label and name
    - Resolved parent room name (not just ID)
    - Full hierarchy path (Building -> Room -> Object)
    - Neighboring objects with names
    - Position and size
    """
    if graph_index is None:
        graph_index = _build_graph_index(graph)

    docs = []
    for node in graph.nodes:
        # Only create context chunks for object nodes (layer 2)
        if node.layer != 2:
            continue

        node_index = graph_index.get(node.node_id, {"parents": [], "children": [], "neighbors": []})
        parents = node_index["parents"]
        children = node_index["children"]
        neighbors = node_index["neighbors"]

        # Resolve parent room names
        parent_names: list[str] = []
        for pid in parents[:3]:
            parent_names.append(_resolve_node_name(pid, graph))

        # Build hierarchy path
        hierarchy_path = _build_hierarchy_path(node.node_id, graph_index, graph)

        # Resolve neighbor names
        neighbor_names: list[str] = []
        for nid in neighbors[:5]:
            neighbor_names.append(_resolve_node_name(nid, graph))

        # Build embedding text with rich context
        parts: list[str] = []

        # Header with label
        noun = node.label or node.node_type or "Object"
        parts.append(f"{noun} {node.node_id}.")

        # Location context (critical for reverse_hierarchy queries)
        if parent_names:
            parts.append(f"Located in {', '.join(parent_names)}.")

        # Hierarchy path
        parts.append(f"Parent hierarchy: {hierarchy_path}.")

        # Position
        if node.position:
            parts.append(f"Position: {_fmt_pos(node.position)}.")

        # Size
        if node.bbox and any(d > 0 for d in node.bbox.dimensions):
            d = node.bbox.dimensions
            parts.append(f"Size: {d[0]:.2f} x {d[1]:.2f} x {d[2]:.2f}m.")

        # Neighbors
        if neighbor_names:
            parts.append(f"Connected to: {', '.join(neighbor_names)}.")

        # Children (for container nodes that might be queried)
        if children:
            child_names = [_resolve_node_name(cid, graph) for cid in children[:5]]
            parts.append(f"Contains: {', '.join(child_names)}.")

        text = " ".join(parts)

        docs.append(RagDocument(
            doc_id=f"context_{node.node_id}",
            text=text,
            metadata={
                "chunk_type": "context",
                "node_id": node.node_id,
                "node_type": node.node_type,
                "label": node.label,
                "room": parents[0] if parents else "",
                "room_name": _resolve_node_name(parents[0], graph) if parents else "",
                "hierarchy_path": hierarchy_path,
                "related_nodes": neighbors[:5],
                "parent_ids": parents[:5],
                "child_ids": children[:10],
                "neighbor_ids": neighbors[:10],
                "depth": LAYER_DEPTH.get(node.layer, -1),
                "object_symbol": node.name if node.name else "",
                "bounding_box_center": node.bbox.center if node.bbox else [],
                "bounding_box_dimensions": node.bbox.dimensions if node.bbox else [],
                "first_observed_ns": node.first_seen_ns if node.first_seen_ns else 0,
                "last_observed_ns": node.last_seen_ns if node.last_seen_ns else 0,
                "is_active": node.is_active if node.is_active is not None else False,
                "layer": node.layer,
                "dataset": dataset,
                "source_file": source_file,
            },
        ))

    return docs


def build_region_chunks(
    graph: SceneGraph,
    graph_index: dict[str, dict[str, list[str]]] | None = None,
) -> list[RagDocument]:
    """Aggregate documents for container nodes (rooms, places, buildings)."""
    if graph_index is None:
        graph_index = _build_graph_index(graph)

    docs = []

    # Group children by parent container
    parent_children: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        parent = graph.node_index.get(edge.target_id)
        child = graph.node_index.get(edge.source_id)
        if parent and child and parent.node_type in CONTAINER_TYPES:
            parent_children[parent.node_id].append(child.node_id)

    for container_id, children_ids in parent_children.items():
        container = graph.node_index.get(container_id)
        if not container:
            continue

        noun = container.node_type.capitalize()
        label_str = f" ({container.label})" if container.label else ""
        name_str = f" named '{container.name}'" if container.name and container.name != container.label else ""

        # Summarize children by type
        type_counts: dict[str, int] = defaultdict(int)
        for cid in children_ids:
            child = graph.node_index.get(cid)
            if child:
                type_counts[child.node_type] += 1

        child_summary = ", ".join(f"{cnt} {typ}(s)" for typ, cnt in type_counts.items())

        parts = [
            f"{noun} {container_id}{label_str}{name_str}.",
            f"Contains {len(children_ids)} items: {child_summary}.",
        ]
        if container.position:
            parts.append(f"Position: {_fmt_pos(container.position)}.")

        # List some child IDs
        if children_ids:
            id_list = ", ".join(children_ids[:10])
            parts.append(f"Children: {id_list}.")

        text = " ".join(parts)

        # Get parent info
        node_index = graph_index.get(container_id, {"parents": [], "children": [], "neighbors": []})

        docs.append(RagDocument(
            doc_id=f"region_{container_id}",
            text=text,
            metadata={
                "chunk_type": "region",
                "node_id": container_id,
                "node_type": container.node_type,
                "label": container.label,
                "child_count": len(children_ids),
                "children": children_ids[:20],
                "child_ids": children_ids[:20],
                "parent_ids": node_index["parents"][:5],
                "neighbor_ids": node_index["neighbors"][:10],
                "depth": LAYER_DEPTH.get(container.layer, -1),
            },
        ))

    return docs


def build_relation_chunks(graph: SceneGraph) -> list[RagDocument]:
    """One document per edge / spatial relationship."""
    docs = []
    seen = set()

    for edge in graph.edges:
        src = graph.node_index.get(edge.source_id)
        tgt = graph.node_index.get(edge.target_id)
        if not src or not tgt:
            continue

        # Deduplicate
        pair = tuple(sorted([edge.source_id, edge.target_id]))
        if pair in seen:
            continue
        seen.add(pair)

        src_label = src.label or src.node_type
        tgt_label = tgt.label or tgt.node_type

        text = (
            f"{src.node_id} ({src_label}) is {edge.relation} "
            f"{tgt.node_id} ({tgt_label})."
        )

        docs.append(RagDocument(
            doc_id=f"rel_{edge.source_id}_{edge.target_id}",
            text=text,
            metadata={
                "chunk_type": "relation",
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "relation": edge.relation,
                "source_type": src.node_type,
                "target_type": tgt.node_type,
            },
        ))

    return docs


def build_materialized_relation_chunks(
    materialized: dict[str, list[str]],
) -> list[RagDocument]:
    """Generate chunks for pre-materialized relation paths."""
    docs = []
    for key, node_ids in materialized.items():
        if not node_ids:
            continue
        parts = key.split("_", 1)
        if len(parts) == 2:
            parent_id, relation = parts
            text = f"{parent_id} {relation} {', '.join(node_ids[:10])}."
        else:
            text = f"Relation {key}: {', '.join(node_ids[:10])}."

        docs.append(RagDocument(
            doc_id=f"mat_{key}",
            text=text,
            metadata={
                "chunk_type": "materialized_relation",
                "key": key,
                "node_ids": node_ids[:20],
                "count": len(node_ids),
            },
        ))
    return docs


def build_alias_chunks(aliases: dict[str, list[str]]) -> list[RagDocument]:
    """Generate alias expansion documents from config."""
    docs = []
    for keyword, synonyms in aliases.items():
        if not synonyms:
            continue
        text = f"Keywords related to '{keyword}': {', '.join(synonyms)}."
        docs.append(RagDocument(
            doc_id=f"alias_{keyword}",
            text=text,
            metadata={
                "chunk_type": "alias",
                "keyword": keyword,
                "synonyms": synonyms,
            },
        ))
    return docs


def build_all_documents(
    graph: SceneGraph,
    aliases: dict[str, list[str]] | None = None,
    dataset: str = "",
    source_file: str = "",
    include_relations: bool = False,
) -> list[RagDocument]:
    """Generate all RAG documents from a parsed scene graph.

    Args:
        graph: Parsed scene graph.
        aliases: Optional keyword aliases.
        dataset: Dataset name.
        source_file: Source file path.
        include_relations: If False, skip relation chunks to reduce pollution.
    """
    graph_index = _build_graph_index(graph)
    materialized = _build_relation_materialization(graph)

    docs = []
    docs.extend(build_entity_chunks(graph, dataset=dataset, source_file=source_file, graph_index=graph_index))
    docs.extend(build_context_chunks(graph, dataset=dataset, source_file=source_file, graph_index=graph_index))
    docs.extend(build_region_chunks(graph, graph_index=graph_index))

    # Only include relation chunks if explicitly requested (default: skip to reduce pollution)
    if include_relations:
        docs.extend(build_relation_chunks(graph))
        docs.extend(build_materialized_relation_chunks(materialized))

    if aliases:
        docs.extend(build_alias_chunks(aliases))
    logger.info(f"Generated {len(docs)} RAG documents (with graph-aware metadata)")
    return docs
