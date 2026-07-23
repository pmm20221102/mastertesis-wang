"""Graph-aware Ranking for Khronos 3DSG RAG.

Combines multiple signals for ranking retrieval results:
- Semantic similarity (from vector search)
- Graph structure (parent/child/neighbor relationships)
- Spatial distance
- Node type relevance
"""

from __future__ import annotations

import json
import math
from typing import Any


# Relation weight mapping
RELATION_WEIGHTS: dict[str, float] = {
    "contains": 1.0,
    "inside": 1.0,
    "adjacent": 0.8,
    "near": 0.6,
    "connected_to": 0.5,
    "far": 0.2,
}

# Node type priority for different query types
NODE_TYPE_PRIORITY: dict[str, dict[str, float]] = {
    "reverse_hierarchy": {
        "rooms": 1.0,
        "room": 1.0,
        "places": 0.8,
        "place": 0.8,
        "buildings": 0.6,
        "building": 0.6,
        "objects": 0.3,
        "object": 0.3,
    },
    "hierarchy": {
        "objects": 1.0,
        "object": 1.0,
        "places": 0.8,
        "place": 0.8,
        "rooms": 0.6,
        "room": 0.6,
    },
    "neighbor": {
        "objects": 1.0,
        "object": 1.0,
        "places": 0.8,
        "place": 0.8,
    },
    "spatial": {
        "objects": 1.0,
        "object": 1.0,
        "places": 0.8,
        "place": 0.8,
        "rooms": 0.6,
        "room": 0.6,
    },
}


def _position_distance(pos1: list[float], pos2: list[float]) -> float:
    """Euclidean distance between two positions."""
    if not pos1 or not pos2:
        return float("inf")
    p1 = pos1[:3] if len(pos1) >= 3 else pos1 + [0] * (3 - len(pos1))
    p2 = pos2[:3] if len(pos2) >= 3 else pos2 + [0] * (3 - len(pos2))
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(p1, p2)))


def compute_graph_score(
    metadata: dict[str, Any],
    seed_ids: list[str],
    query_type: str,
) -> float:
    """Compute a score based on graph structure.

    Args:
        metadata: Node metadata with parent_ids, child_ids, neighbor_ids.
        seed_ids: Seed node IDs from entity resolution.
        query_type: The query type.

    Returns:
        Score between 0 and 1.
    """
    if not seed_ids:
        return 0.0

    parent_ids = metadata.get("parent_ids", [])
    child_ids = metadata.get("child_ids", [])
    neighbor_ids = metadata.get("neighbor_ids", [])

    score = 0.0

    # Check relationship with seed nodes
    for sid in seed_ids:
        if query_type == "reverse_hierarchy":
            # For reverse hierarchy, result is parent, seed is child
            # So seed should be in result's child_ids
            if sid in child_ids:
                score += 0.8
            elif sid in parent_ids:
                score += 0.4
            elif sid in neighbor_ids:
                score += 0.2
        elif query_type == "hierarchy":
            # For hierarchy, result is child, seed is parent
            # So seed should be in result's parent_ids
            if sid in parent_ids:
                score += 0.8
            elif sid in child_ids:
                score += 0.4
        elif query_type == "neighbor":
            # For neighbor, we want neighbors
            if sid in neighbor_ids:
                score += 0.8
            elif sid in child_ids or sid in parent_ids:
                score += 0.4

    # Bonus for having graph structure
    if parent_ids or child_ids or neighbor_ids:
        score += 0.1

    return min(score, 1.0)


def compute_node_type_score(
    node_type: str,
    query_type: str,
) -> float:
    """Compute a score based on node type relevance.

    Args:
        node_type: The node type (e.g., "rooms", "objects").
        query_type: The query type.

    Returns:
        Score between 0 and 1.
    """
    priority_map = NODE_TYPE_PRIORITY.get(query_type, {})
    return priority_map.get(node_type, 0.5)


def compute_spatial_score(
    metadata: dict[str, Any],
    query_position: list[float] | None,
    use_2d: bool = True,
) -> float:
    """Compute a score based on spatial distance.

    Args:
        metadata: Node metadata with bounding_box_center.
        query_position: Target position [x, y, z].
        use_2d: If True, use only x,y for distance (ignore z).

    Returns:
        Score between 0 and 1 (closer = higher).
    """
    if not query_position:
        return 0.5

    bb_center = metadata.get("bounding_box_center", [])
    if isinstance(bb_center, str):
        try:
            bb_center = json.loads(bb_center)
        except (json.JSONDecodeError, TypeError):
            bb_center = []

    if not bb_center or len(bb_center) < 2:
        return 0.5

    # Use 2D distance if requested (ignore z)
    if use_2d and len(query_position) >= 2 and len(bb_center) >= 2:
        dx = query_position[0] - bb_center[0]
        dy = query_position[1] - bb_center[1]
        dist = math.sqrt(dx*dx + dy*dy)
    else:
        dist = _position_distance(query_position, bb_center)

    # Convert distance to score (closer = higher)
    # Using exponential decay with tighter decay for better ranking
    return math.exp(-dist / 5.0)


def hybrid_score(
    semantic_score: float,
    graph_score: float,
    node_type_score: float,
    spatial_score: float,
    query_type: str,
) -> float:
    """Combine multiple scores into a final hybrid score.

    Args:
        semantic_score: From vector similarity.
        graph_score: From graph structure.
        node_type_score: From node type relevance.
        spatial_score: From spatial distance.
        query_type: The query type for weight selection.

    Returns:
        Combined score between 0 and 1.
    """
    # Different weights for different query types
    weights = {
        "reverse_hierarchy": {"semantic": 0.2, "graph": 0.5, "type": 0.2, "spatial": 0.1},
        "hierarchy": {"semantic": 0.3, "graph": 0.4, "type": 0.2, "spatial": 0.1},
        "neighbor": {"semantic": 0.3, "graph": 0.4, "type": 0.1, "spatial": 0.2},
        "spatial": {"semantic": 0.2, "graph": 0.2, "type": 0.1, "spatial": 0.5},
        "semantic": {"semantic": 0.6, "graph": 0.2, "type": 0.1, "spatial": 0.1},
    }

    w = weights.get(query_type, weights["semantic"])

    return (
        w["semantic"] * semantic_score
        + w["graph"] * graph_score
        + w["type"] * node_type_score
        + w["spatial"] * spatial_score
    )


def rank_results(
    results: list[dict[str, Any]],
    seed_ids: list[str],
    query_type: str,
    query_position: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Re-rank results using hybrid scoring.

    Args:
        results: Initial retrieval results.
        seed_ids: Seed node IDs from entity resolution.
        query_type: The query type.
        query_position: Optional position for spatial queries.

    Returns:
        Re-ranked results with updated scores.
    """
    for result in results:
        meta = result.get("metadata", {})

        # Compute component scores
        semantic_score = result.get("score", 0.5)
        graph_score = compute_graph_score(meta, seed_ids, query_type)
        node_type_score = compute_node_type_score(meta.get("node_type", ""), query_type)
        spatial_score = compute_spatial_score(meta, query_position)

        # Combine into hybrid score
        result["hybrid_score"] = round(
            hybrid_score(semantic_score, graph_score, node_type_score, spatial_score, query_type),
            4,
        )
        result["graph_score"] = round(graph_score, 4)
        result["node_type_score"] = round(node_type_score, 4)

    # Re-sort by hybrid score
    results.sort(key=lambda x: x.get("hybrid_score", x.get("score", 0)), reverse=True)

    # Update ranks
    for i, result in enumerate(results):
        result["rank"] = i + 1

    return results
