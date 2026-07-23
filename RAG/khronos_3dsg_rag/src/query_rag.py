"""Query interface for the 3DSG RAG database.

Graph-aware retrieval with query routing:
  - reverse_hierarchy: direct parent traversal (no embedding)
  - hierarchy: direct children traversal
  - neighbor: neighbor graph lookup
  - spatial: position-based search
  - semantic: vector search (default)
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

import chromadb

from build_vector_db import COLLECTION_NAME, load_vector_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Query classification
# ---------------------------------------------------------------------------

def classify_query(query: str) -> str:
    """Classify query into retrieval strategy type.

    Returns one of: reverse_hierarchy, hierarchy, neighbor, spatial, semantic
    """
    q = query.lower()

    # Reverse hierarchy: "Where is X?", "Which room contains X?", "What is the parent of X?"
    if any(kw in q for kw in [
        "where is", "located", "parent of", "which room contains",
        "find the location", "what room", "which place",
    ]):
        return "reverse_hierarchy"

    # Hierarchy: "What is inside X?", "List contents of X"
    if any(kw in q for kw in [
        "inside", "contains", "what is in", "what's in",
        "list the contents", "show me everything in", "show me all in",
    ]):
        return "hierarchy"

    # Neighbor: "What is connected to X?", "What is near X?"
    if any(kw in q for kw in [
        "connected to", "neighbor", "adjacent", "near", "next to",
    ]):
        return "neighbor"

    # Spatial: "Object at position x=1.0 y=2.0"
    if re.search(r'x[=:\s]+-?[\d.]+', q):
        return "spatial"

    return "semantic"


# ---------------------------------------------------------------------------
# Graph index builder (from ChromaDB metadata)
# ---------------------------------------------------------------------------

def _build_adjacency_index(collection: chromadb.Collection) -> dict[str, dict[str, list[str]]]:
    """Build adjacency index from all stored metadata.

    Returns: node_id -> {"parents": [...], "children": [...], "neighbors": [...]}
    """
    all_meta = collection.get(include=["metadatas"])
    index: dict[str, dict[str, list[str]]] = {}

    for meta in all_meta.get("metadatas", []):
        nid = meta.get("node_id", "")
        if not nid:
            continue

        def _parse_list(field: str) -> list[str]:
            val = meta.get(field, [])
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    val = []
            return val if isinstance(val, list) else []

        index[nid] = {
            "parents": _parse_list("parent_ids"),
            "children": _parse_list("child_ids"),
            "neighbors": _parse_list("neighbor_ids"),
            "node_type": meta.get("node_type", ""),
        }

    return index


def _parse_position_from_query(query: str) -> list[float] | None:
    """Extract x,y,z coordinates from query string."""
    x_match = re.search(r'x[=:\s]+(-?[\d.]+)', query)
    y_match = re.search(r'y[=:\s]+(-?[\d.]+)', query)
    z_match = re.search(r'z[=:\s]+(-?[\d.]+)', query)
    if x_match and y_match:
        x = float(x_match.group(1))
        y = float(y_match.group(1))
        z = float(z_match.group(1)) if z_match else 0.0
        return [x, y, z]
    return None


def _position_distance(pos1: list[float], pos2: list[float]) -> float:
    """Euclidean distance between two 3D positions."""
    if not pos1 or not pos2:
        return float("inf")
    p1 = pos1[:3] if len(pos1) >= 3 else pos1 + [0] * (3 - len(pos1))
    p2 = pos2[:3] if len(pos2) >= 3 else pos2 + [0] * (3 - len(pos2))
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(p1, p2)))


# ---------------------------------------------------------------------------
# Graph traversal retrieval (no embedding)
# ---------------------------------------------------------------------------

def graph_lookup(
    db_dir: str | Path,
    query: str,
    query_type: str,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Direct graph traversal retrieval without embedding.

    For reverse_hierarchy: use Entity Resolver to find node, return its parents.
    For hierarchy: find node, return its children.
    For neighbor: find node, return its neighbors.
    For spatial: find nodes by position distance.
    """
    collection = load_vector_db(db_dir)
    adj_index = _build_adjacency_index(collection)

    # Get all documents for result construction
    all_docs = collection.get(include=["documents", "metadatas"])
    doc_map: dict[str, tuple[str, dict]] = {}
    for i, doc_id in enumerate(all_docs.get("ids", [])):
        meta = all_docs["metadatas"][i] if i < len(all_docs["metadatas"]) else {}
        text = all_docs["documents"][i] if i < len(all_docs["documents"]) else ""
        # Use node_id as key (extract from doc_id like "entity_12345" -> "12345")
        node_id = meta.get("node_id", "")
        if not node_id and "_" in doc_id:
            node_id = doc_id.split("_", 1)[1] if doc_id.count("_") == 1 else doc_id
        if node_id:
            doc_map[node_id] = (text, meta)
        else:
            doc_map[doc_id] = (text, meta)

    # Use Entity Resolver for reverse_hierarchy queries
    if query_type == "reverse_hierarchy":
        from entity_resolver import resolve_to_parents
        parent_candidates = resolve_to_parents(query, db_dir, top_k=top_k * 3)

        results: list[dict[str, Any]] = []
        for pc in parent_candidates:
            pid = pc["node_id"]
            if pid in doc_map:
                text, meta = doc_map[pid]
                results.append({
                    "rank": len(results) + 1,
                    "score": pc["score"],
                    "node_id": pid,
                    "node_type": meta.get("node_type", ""),
                    "label": meta.get("label", ""),
                    "text": text,
                    "metadata": {
                        "chunk_type": meta.get("chunk_type", ""),
                        "room": meta.get("room", ""),
                        "parent_ids": meta.get("parent_ids", []),
                        "child_ids": meta.get("child_ids", []),
                        "neighbor_ids": meta.get("neighbor_ids", []),
                    },
                })
        return results

    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add_result(node_id: str, score: float):
        if node_id in seen_ids or node_id not in doc_map:
            return
        seen_ids.add(node_id)
        text, meta = doc_map[node_id]
        results.append({
            "rank": len(results) + 1,
            "score": round(score, 4),
            "node_id": node_id,
            "node_type": meta.get("node_type", ""),
            "label": meta.get("label", ""),
            "text": text,
            "metadata": {
                "chunk_type": meta.get("chunk_type", ""),
                "room": meta.get("room", ""),
                "parent_ids": json.loads(meta.get("parent_ids", "[]")) if isinstance(meta.get("parent_ids"), str) else meta.get("parent_ids", []),
                "child_ids": json.loads(meta.get("child_ids", "[]")) if isinstance(meta.get("child_ids"), str) else meta.get("child_ids", []),
                "neighbor_ids": json.loads(meta.get("neighbor_ids", "[]")) if isinstance(meta.get("neighbor_ids"), str) else meta.get("neighbor_ids", []),
            },
        })

    # Step 1: Find seed nodes that match the query
    seed_nodes: list[str] = []

    # Try to find node by ID in query
    for nid in adj_index:
        if nid in query or str(nid) in query:
            seed_nodes.append(nid)

    # Try to find node by label/name in query
    if not seed_nodes:
        for nid, (_, meta) in doc_map.items():
            label = meta.get("label", "")
            node_type = meta.get("node_type", "")
            if label and label.lower() in query.lower():
                seed_nodes.append(nid)
            elif node_type and node_type.rstrip("s") in query.lower():
                seed_nodes.append(nid)

    # For spatial: use position lookup with 2D distance
    if query_type == "spatial":
        position = _parse_position_from_query(query)
        if position:
            # Get all positions from metadata, use 2D distance (ignore z)
            scored: list[tuple[float, str]] = []
            for nid, (_, meta) in doc_map.items():
                bb_center = meta.get("bounding_box_center", "")
                if isinstance(bb_center, str):
                    try:
                        bb_center = json.loads(bb_center)
                    except (json.JSONDecodeError, TypeError):
                        bb_center = []
                if isinstance(bb_center, list) and len(bb_center) >= 2:
                    # Use 2D distance (ignore z)
                    dx = position[0] - bb_center[0]
                    dy = position[1] - bb_center[1]
                    dist = math.sqrt(dx*dx + dy*dy)
                    scored.append((dist, nid))
            scored.sort(key=lambda x: x[0])
            for dist, nid in scored[:top_k]:
                score = math.exp(-dist / 5.0)  # Exponential decay
                _add_result(nid, score)
            return results

    # Step 2: Traverse graph based on query type
    for seed in seed_nodes[:3]:  # Limit to 3 seeds
        info = adj_index.get(seed, {})

        if query_type == "reverse_hierarchy":
            # Return parents of seed
            parents = info.get("parents", [])
            for pid in parents[:top_k]:
                _add_result(pid, 0.9)
            # If no parents, return the seed itself
            if not parents:
                _add_result(seed, 0.5)

        elif query_type == "hierarchy":
            # Return children of seed
            children = info.get("children", [])
            for cid in children[:top_k]:
                _add_result(cid, 0.9)

        elif query_type == "neighbor":
            # Return neighbors of seed
            neighbors = info.get("neighbors", [])
            for nid in neighbors[:top_k]:
                _add_result(nid, 0.8)

    # If no results from graph traversal, fall back to seed nodes themselves
    if not results:
        for seed in seed_nodes[:top_k]:
            _add_result(seed, 0.5)

    # Apply graph-aware ranking
    if results and seed_nodes:
        results = graph_rank_results(
            results,
            seed_ids=seed_nodes,
            query_type=query_type,
        )

    return results[:top_k]


# ---------------------------------------------------------------------------
# Spatial search (for evaluation adapter)
# ---------------------------------------------------------------------------

def spatial_search(
    db_dir: str | Path,
    query: str,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Find nodes by position distance from coordinates in query."""
    return graph_lookup(db_dir, query, "spatial", top_k)


# ---------------------------------------------------------------------------
# Hybrid ranking using graph_ranker module
# ---------------------------------------------------------------------------

from graph_ranker import rank_results as graph_rank_results


# ---------------------------------------------------------------------------
# Main query function (graph-aware)
# ---------------------------------------------------------------------------

def query_rag(
    query: str,
    db_dir: str | Path,
    top_k: int = 5,
    where_filter: dict | None = None,
    query_type: str | None = None,
) -> list[dict[str, Any]]:
    """Query the RAG database with graph-aware routing.

    Args:
        query: Natural language query string.
        db_dir: Path to the vector DB directory.
        top_k: Number of results to return.
        where_filter: Optional ChromaDB where filter for metadata.
        query_type: Optional explicit query type override. If None, auto-classified.

    Returns:
        List of result dicts with rank, score, text, metadata.
    """
    # Classify query if not provided
    if query_type is None:
        query_type = classify_query(query)

    logger.info(f"Query type: {query_type}")

    # Route to appropriate retrieval strategy
    if query_type in ("reverse_hierarchy", "hierarchy", "neighbor", "spatial"):
        results = graph_lookup(db_dir, query, query_type, top_k)
        if results:
            return results
        # Fall through to vector search if graph lookup returns nothing

    # Default: vector search (semantic queries)
    collection = load_vector_db(db_dir)

    kwargs: dict[str, Any] = {
        "query_texts": [query],
        "n_results": min(top_k, collection.count()),
    }
    if where_filter:
        kwargs["where"] = where_filter

    results = collection.query(**kwargs)

    # Flatten ChromaDB batch results (always batch=1 here)
    ids = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0]
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    output = []
    for i, doc_id in enumerate(ids):
        meta = metadatas[i] if i < len(metadatas) else {}

        # Convert distance to similarity score (cosine distance -> similarity)
        distance = distances[i] if i < len(distances) else 0.0
        score = round(1.0 - distance, 4)

        # Parse JSON metadata fields
        def _parse_list(field: str) -> list:
            val = meta.get(field, [])
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    val = []
            return val if isinstance(val, list) else []

        output.append({
            "rank": i + 1,
            "score": score,
            "node_id": meta.get("node_id", doc_id),
            "node_type": meta.get("node_type", ""),
            "label": meta.get("label", ""),
            "text": documents[i] if i < len(documents) else "",
            "metadata": {
                "chunk_type": meta.get("chunk_type", ""),
                "room": meta.get("room", ""),
                "related_nodes": _parse_list("related_nodes"),
                "parent_ids": _parse_list("parent_ids"),
                "child_ids": _parse_list("child_ids"),
                "neighbor_ids": _parse_list("neighbor_ids"),
            },
        })

    return output


def retrieve_scene_context(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """High-level retrieval function for LLM task planner integration.

    This function only returns retrieved scene context.
    It does not generate a task plan.

    Args:
        query: Natural language query.
        top_k: Number of results.

    Returns:
        List of relevant scene context dicts.
    """
    default_db = Path(__file__).parent.parent / "outputs" / "vector_db"
    return query_rag(query, str(default_db), top_k=top_k)
