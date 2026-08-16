from __future__ import annotations


def hit_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> bool:
    if not relevant_ids:
        return True
    top_k = retrieved_ids[:k]
    return bool(set(top_k) & set(relevant_ids))


def mrr_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    if not relevant_ids:
        return 1.0
    rel_set = set(relevant_ids)
    for i, rid in enumerate(retrieved_ids[:k], start=1):
        if rid in rel_set:
            return 1.0 / i
    return 0.0


def recall_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    if not relevant_ids:
        return 1.0
    rel_set = set(relevant_ids)
    top_k = set(retrieved_ids[:k])
    return len(rel_set & top_k) / len(rel_set)


def precision_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    if k == 0:
        return 0.0
    rel_set = set(relevant_ids)
    top_k = retrieved_ids[:k]
    return len([r for r in top_k if r in rel_set]) / k


def f1_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    p = precision_at_k(retrieved_ids, relevant_ids, k)
    r = recall_at_k(retrieved_ids, relevant_ids, k)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def graph_accuracy(retrieved_metadata: dict, expected_ids: list[str]) -> float:
    """Evaluate graph structure understanding from chunk metadata.

    Checks if:
    1. child_ids contains expected_ids (for hierarchy queries)
    2. parent_ids contains expected_ids (for reverse_hierarchy queries)
    3. neighbor_ids contains expected_ids (for neighbor queries)
    4. node_id is in expected_ids (for direct matches)
    """
    if not expected_ids:
        return 1.0

    expected_set = set(expected_ids)

    node_id = retrieved_metadata.get("node_id", "")
    if node_id in expected_set:
        return 1.0

    child_ids = retrieved_metadata.get("child_ids", [])
    if isinstance(child_ids, str):
        try:
            import json
            child_ids = json.loads(child_ids)
        except (json.JSONDecodeError, TypeError):
            child_ids = []
    if child_ids:
        child_set = set(child_ids)
        overlap = child_set & expected_set
        if overlap:
            return len(overlap) / len(expected_set)

    parent_ids = retrieved_metadata.get("parent_ids", [])
    if isinstance(parent_ids, str):
        try:
            import json
            parent_ids = json.loads(parent_ids)
        except (json.JSONDecodeError, TypeError):
            parent_ids = []
    if parent_ids:
        parent_set = set(parent_ids)
        overlap = parent_set & expected_set
        if overlap:
            return len(overlap) / len(expected_set)

    neighbor_ids = retrieved_metadata.get("neighbor_ids", [])
    if isinstance(neighbor_ids, str):
        try:
            import json
            neighbor_ids = json.loads(neighbor_ids)
        except (json.JSONDecodeError, TypeError):
            neighbor_ids = []
    if neighbor_ids:
        neighbor_set = set(neighbor_ids)
        overlap = neighbor_set & expected_set
        if overlap:
            return len(overlap) / len(expected_set)

    return 0.0


def entity_resolution_accuracy(
    resolved_ids: list[str],
    expected_source_id: str,
) -> float:
    """Check if entity resolution found the correct source node.

    Args:
        resolved_ids: List of resolved node IDs from entity resolver.
        expected_source_id: The expected source node ID.

    Returns:
        1.0 if found in top results, 0.0 otherwise.
    """
    if not expected_source_id:
        return 1.0
    if expected_source_id in resolved_ids[:5]:
        return 1.0
    return 0.0


def parent_retrieval_accuracy(
    retrieved_ids: list[str],
    expected_parent_ids: list[str],
) -> float:
    """Check if parent nodes were correctly retrieved.

    Args:
        retrieved_ids: List of retrieved node IDs.
        expected_parent_ids: List of expected parent node IDs.

    Returns:
        Proportion of expected parents found in retrieved results.
    """
    if not expected_parent_ids:
        return 1.0
    retrieved_set = set(retrieved_ids)
    expected_set = set(expected_parent_ids)
    overlap = retrieved_set & expected_set
    return len(overlap) / len(expected_set) if expected_set else 0.0


def graph_traversal_success(
    retrieved_ids: list[str],
    expected_ids: list[str],
    traversal_type: str,
) -> float:
    """Check if graph traversal was successful.

    Args:
        retrieved_ids: List of retrieved node IDs.
        expected_ids: List of expected node IDs.
        traversal_type: Type of traversal (parent, child, neighbor).

    Returns:
        1.0 if traversal successful, 0.0 otherwise.
    """
    if not expected_ids:
        return 1.0
    retrieved_set = set(retrieved_ids)
    expected_set = set(expected_ids)
    overlap = retrieved_set & expected_set
    return 1.0 if overlap else 0.0


def compute_metrics(retrieved_ids: list[str], relevant_ids: list[str], k: int = 10) -> dict[str, float]:
    """Compute all evaluation metrics at multiple cutoffs.

    Returns metrics for k=1, k=3, and k=k (default 10).
    """
    metrics = {}

    # Metrics at k=1 (top result quality)
    metrics["hit@1"] = float(hit_at_k(retrieved_ids, relevant_ids, 1))
    metrics["mrr@1"] = mrr_at_k(retrieved_ids, relevant_ids, 1)
    metrics["recall@1"] = recall_at_k(retrieved_ids, relevant_ids, 1)
    metrics["precision@1"] = precision_at_k(retrieved_ids, relevant_ids, 1)

    # Metrics at k=3 (short list quality)
    metrics["hit@3"] = float(hit_at_k(retrieved_ids, relevant_ids, 3))
    metrics["mrr@3"] = mrr_at_k(retrieved_ids, relevant_ids, 3)
    metrics["recall@3"] = recall_at_k(retrieved_ids, relevant_ids, 3)
    metrics["precision@3"] = precision_at_k(retrieved_ids, relevant_ids, 3)
    metrics["f1@3"] = f1_at_k(retrieved_ids, relevant_ids, 3)

    # Metrics at k (full list quality)
    metrics[f"hit@{k}"] = float(hit_at_k(retrieved_ids, relevant_ids, k))
    metrics[f"mrr@{k}"] = mrr_at_k(retrieved_ids, relevant_ids, k)
    metrics[f"recall@{k}"] = recall_at_k(retrieved_ids, relevant_ids, k)
    metrics[f"precision@{k}"] = precision_at_k(retrieved_ids, relevant_ids, k)
    metrics[f"f1@{k}"] = f1_at_k(retrieved_ids, relevant_ids, k)

    return metrics
