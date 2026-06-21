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


def compute_metrics(retrieved_ids: list[str], relevant_ids: list[str], k: int = 10) -> dict[str, float]:
    return {
        f"hit@{k}": float(hit_at_k(retrieved_ids, relevant_ids, k)),
        f"mrr@{k}": mrr_at_k(retrieved_ids, relevant_ids, k),
        f"recall@{k}": recall_at_k(retrieved_ids, relevant_ids, k),
        f"precision@{k}": precision_at_k(retrieved_ids, relevant_ids, k),
        f"f1@{k}": f1_at_k(retrieved_ids, relevant_ids, k),
    }
