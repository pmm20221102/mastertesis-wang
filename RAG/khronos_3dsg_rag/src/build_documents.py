"""Convert parsed SceneGraph into RAG document chunks.

Generates four types of chunks:
  1. Entity chunks — one per node
  2. Region chunks — aggregated per room/place/building
  3. Relation chunks — one per edge / spatial relationship
  4. Alias chunks — keyword expansion from config
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from schemas import RagDocument, SceneGraph, SceneNode

logger = logging.getLogger(__name__)

# Types that act as "containers" (region/room/place/building)
CONTAINER_TYPES = {"room", "rooms", "place", "places", "building", "buildings", "region", "floor"}


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


def build_entity_chunks(graph: SceneGraph) -> list[RagDocument]:
    """One document per node."""
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

        # Find which container this node belongs to (from edges)
        containers = _find_containers(node, graph)
        if containers:
            container_str = ", ".join(containers[:3])
            parts.append(f"Located in {container_str}.")

        # Find nearby nodes (edges from this node)
        nearby = _find_neighbors(node, graph, exclude=containers)
        if nearby:
            nearby_str = ", ".join(nearby[:5])
            parts.append(f"Connected to: {nearby_str}.")

        text = " ".join(parts)

        docs.append(RagDocument(
            doc_id=f"entity_{node.node_id}",
            text=text,
            metadata={
                "chunk_type": "entity",
                "node_id": node.node_id,
                "node_type": node.node_type,
                "label": node.label,
                "room": containers[0] if containers else "",
                "related_nodes": nearby[:5],
            },
        ))

    return docs


def _find_containers(node: SceneNode, graph: SceneGraph) -> list[str]:
    """Find room/place/building that contains this node (direct parent only).

    DSG edge direction: parent → child (Building → Room → Place → Object).
    We only look for edges where source layer > target layer (hierarchy, not adjacency).
    """
    containers = []
    for edge in graph.edges:
        if edge.target_id == node.node_id:
            source = graph.node_index.get(edge.source_id)
            if source and source.node_type in CONTAINER_TYPES:
                # Only accept if source layer is strictly higher (parent, not sibling)
                if source.layer > node.layer:
                    containers.append(source.node_id)
    return containers


def _find_neighbors(node: SceneNode, graph: SceneGraph, exclude: list[str] | None = None) -> list[str]:
    """Find nodes connected by edges."""
    exclude_set = set(exclude or [])
    exclude_set.add(node.node_id)
    neighbors = []
    for edge in graph.edges:
        other_id = None
        if edge.source_id == node.node_id:
            other_id = edge.target_id
        elif edge.target_id == node.node_id:
            other_id = edge.source_id
        if other_id and other_id not in exclude_set:
            other = graph.node_index.get(other_id)
            if other:
                neighbors.append(other_id)
    return neighbors


def build_region_chunks(graph: SceneGraph) -> list[RagDocument]:
    """Aggregate documents for container nodes (rooms, places, buildings)."""
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
) -> list[RagDocument]:
    """Generate all RAG documents from a parsed scene graph."""
    docs = []
    docs.extend(build_entity_chunks(graph))
    docs.extend(build_region_chunks(graph))
    docs.extend(build_relation_chunks(graph))
    if aliases:
        docs.extend(build_alias_chunks(aliases))
    logger.info(f"Generated {len(docs)} RAG documents")
    return docs
