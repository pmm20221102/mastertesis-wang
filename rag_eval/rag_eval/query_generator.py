from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from typing import Sequence

from .schemas import EvalCase, GraphGroundTruth, GraphNode, GroundTruthChange, GroundTruthObject


def _case_id(dataset: str, query_type: str, key: str) -> str:
    h = hashlib.md5(f"{dataset}:{query_type}:{key}".encode()).hexdigest()[:8]
    return f"{dataset}_{query_type}_{h}"


def generate_hierarchy_queries(gt: GraphGroundTruth) -> list[EvalCase]:
    """Generate 'what is inside X?' queries for container nodes."""
    cases = []
    templates = [
        "What is inside {name}?",
        "What objects are in {name}?",
        "List the contents of {name}.",
        "Show me everything in {name}.",
    ]

    for node in gt.nodes:
        if not node.child_ids:
            continue
        name = node.name or node.node_id
        tmpl = templates[node.layer % len(templates)]
        query = tmpl.format(name=name)
        cases.append(EvalCase(
            case_id=_case_id(gt.dataset, "hierarchy", node.node_id),
            dataset=gt.dataset,
            query_type="hierarchy",
            query=query,
            expected={"node_id": node.node_id, "child_count": len(node.child_ids)},
            relevant_ids=node.child_ids,
            source_node_id=node.node_id,
            source_node_type=node.node_type,
            difficulty="easy",
        ))

    return cases


def generate_reverse_hierarchy_queries(gt: GraphGroundTruth) -> list[EvalCase]:
    """Generate 'where is X?' queries for leaf nodes."""
    cases = []
    templates = [
        "Where is {name} located?",
        "Which room contains {name}?",
        "Find the location of {name}.",
        "What is the parent of {name}?",
    ]

    for node in gt.nodes:
        if not node.parent_ids:
            continue
        name = node.name or node.label or node.node_id
        if not name:
            continue
        tmpl = templates[hash(node.node_id) % len(templates)]
        query = tmpl.format(name=name)
        cases.append(EvalCase(
            case_id=_case_id(gt.dataset, "reverse_hierarchy", node.node_id),
            dataset=gt.dataset,
            query_type="reverse_hierarchy",
            query=query,
            expected={"node_id": node.node_id, "parent_count": len(node.parent_ids)},
            relevant_ids=node.parent_ids,
            source_node_id=node.node_id,
            source_node_type=node.node_type,
            difficulty="easy",
        ))

    return cases


def generate_neighbor_queries(gt: GraphGroundTruth) -> list[EvalCase]:
    """Generate 'what is connected to X?' queries."""
    cases = []
    templates = [
        "What is connected to {name}?",
        "Which nodes are neighbors of {name}?",
        "Show me adjacent nodes to {name}.",
    ]

    for node in gt.nodes:
        if not node.neighbor_ids:
            continue
        name = node.name or node.node_id
        tmpl = templates[hash(node.node_id) % len(templates)]
        query = tmpl.format(name=name)
        cases.append(EvalCase(
            case_id=_case_id(gt.dataset, "neighbor", node.node_id),
            dataset=gt.dataset,
            query_type="neighbor",
            query=query,
            expected={"node_id": node.node_id, "neighbor_count": len(node.neighbor_ids)},
            relevant_ids=node.neighbor_ids,
            source_node_id=node.node_id,
            source_node_type=node.node_type,
            difficulty="medium",
        ))

    return cases


def generate_type_queries(gt: GraphGroundTruth) -> list[EvalCase]:
    """Generate 'find all X' queries for each node type."""
    cases = []
    type_groups: dict[str, list[str]] = defaultdict(list)
    for node in gt.nodes:
        type_groups[node.node_type].append(node.node_id)

    type_templates = {
        "room": ["Find all rooms.", "List all rooms in the scene.", "Which nodes are rooms?"],
        "place": ["Find all places.", "List all places in the scene.", "Which nodes are places?"],
        "object": ["Find all objects.", "List all objects in the scene.", "Which nodes are objects?"],
        "agent": ["Find all agents.", "List all agents in the scene.", "Which nodes are agents?"],
    }

    for node_type, node_ids in type_groups.items():
        if len(node_ids) < 2:
            continue
        templates = type_templates.get(node_type, [f"Find all {node_type}s."])
        query = templates[0]
        cases.append(EvalCase(
            case_id=_case_id(gt.dataset, "type_filter", node_type),
            dataset=gt.dataset,
            query_type="type_filter",
            query=query,
            expected={"node_type": node_type, "count": len(node_ids)},
            relevant_ids=node_ids,
            source_node_id="",
            source_node_type=node_type,
            difficulty="easy",
        ))

    return cases


def generate_semantic_queries(gt: GraphGroundTruth) -> list[EvalCase]:
    """Generate semantic queries for nodes with meaningful labels."""
    cases = []
    label_groups: dict[str, list[str]] = defaultdict(list)
    for node in gt.nodes:
        if node.label and node.label not in ("unknown", "object", "place", "room", "building"):
            label_groups[node.label].append(node.node_id)

    for label, node_ids in label_groups.items():
        if len(node_ids) < 2:
            continue
        query = f"Find all {label}."
        cases.append(EvalCase(
            case_id=_case_id(gt.dataset, "semantic", label),
            dataset=gt.dataset,
            query_type="semantic",
            query=query,
            expected={"label": label, "count": len(node_ids)},
            relevant_ids=node_ids,
            source_node_id="",
            source_node_type="semantic",
            difficulty="medium",
        ))

    return cases


def generate_spatial_queries(gt: GraphGroundTruth) -> list[EvalCase]:
    """Generate spatial queries based on node positions."""
    cases = []
    for node in gt.nodes:
        if not node.position or len(node.position) < 2:
            continue
        x, y = node.position[0], node.position[1]
        query = f"Object at position x={x:.1f}, y={y:.1f}"
        cases.append(EvalCase(
            case_id=_case_id(gt.dataset, "spatial", node.node_id),
            dataset=gt.dataset,
            query_type="spatial",
            query=query,
            expected={"node_id": node.node_id, "position": node.position[:2]},
            relevant_ids=[node.node_id],
            source_node_id=node.node_id,
            source_node_type=node.node_type,
            difficulty="medium",
        ))

    return cases


def generate_symbol_queries(objects: Sequence[GroundTruthObject]) -> list[EvalCase]:
    cases = []
    templates = [
        "Retrieve information about object {sym}.",
        "Find object {sym}.",
        "Show the data for {sym}.",
        "What is known about {sym}?",
        "Locate {sym} in the scene.",
    ]
    for obj in objects:
        if not obj.object_symbol:
            continue
        tmpl = templates[obj.object_id % len(templates)]
        query = tmpl.format(sym=obj.object_symbol)
        cases.append(EvalCase(
            case_id=_case_id(obj.dataset, "symbol_retrieval", obj.object_symbol),
            dataset=obj.dataset,
            query_type="symbol_retrieval",
            query=query,
            expected={"object_symbol": obj.object_symbol, "object_id": obj.object_id},
            relevant_ids=[str(obj.object_id)],
            difficulty="easy",
        ))
    return cases


def generate_change_queries(changes: Sequence[GroundTruthChange]) -> list[EvalCase]:
    cases = []

    disappeared = [c for c in changes if c.disappeared_at > 0]
    if disappeared:
        cases.append(EvalCase(
            case_id=_case_id(changes[0].dataset if changes else "unknown", "change_retrieval", "disappeared_list"),
            dataset=changes[0].dataset if changes else "unknown",
            query_type="change_retrieval",
            query="Which objects disappeared during the sequence?",
            expected={"change_type": "disappeared", "count": len(disappeared)},
            relevant_ids=[c.object_symbol for c in disappeared],
            difficulty="easy",
        ))

    appeared = [c for c in changes if c.appeared_at > 0]
    if appeared:
        cases.append(EvalCase(
            case_id=_case_id(changes[0].dataset if changes else "unknown", "change_retrieval", "appeared_list"),
            dataset=changes[0].dataset if changes else "unknown",
            query_type="change_retrieval",
            query="Which objects appeared during the sequence?",
            expected={"change_type": "appeared", "count": len(appeared)},
            relevant_ids=[c.object_symbol for c in appeared],
            difficulty="easy",
        ))

    for ch in changes:
        if ch.disappeared_at > 0:
            cases.append(EvalCase(
                case_id=_case_id(ch.dataset, "change_retrieval", f"{ch.object_symbol}_disappeared"),
                dataset=ch.dataset,
                query_type="change_retrieval",
                query=f"Did {ch.object_symbol} disappear?",
                expected={"object_symbol": ch.object_symbol, "disappeared": True, "disappeared_at": ch.disappeared_at},
                relevant_ids=[ch.object_symbol],
                difficulty="easy",
            ))
        else:
            cases.append(EvalCase(
                case_id=_case_id(ch.dataset, "change_retrieval", f"{ch.object_symbol}_present"),
                dataset=ch.dataset,
                query_type="change_retrieval",
                query=f"Is {ch.object_symbol} still present at the end?",
                expected={"object_symbol": ch.object_symbol, "disappeared": False},
                relevant_ids=[ch.object_symbol],
                difficulty="easy",
            ))

        if ch.appeared_at > 0:
            cases.append(EvalCase(
                case_id=_case_id(ch.dataset, "change_retrieval", f"{ch.object_symbol}_appeared"),
                dataset=ch.dataset,
                query_type="change_retrieval",
                query=f"When did {ch.object_symbol} appear?",
                expected={"object_symbol": ch.object_symbol, "appeared_at": ch.appeared_at},
                relevant_ids=[ch.object_symbol],
                difficulty="easy",
            ))

    return cases


def generate_all_graph_cases(
    gt: GraphGroundTruth,
    seed: int = 42,
) -> list[EvalCase]:
    """Generate all evaluation cases from graph groundtruth."""
    cases = []
    cases.extend(generate_hierarchy_queries(gt))
    cases.extend(generate_reverse_hierarchy_queries(gt))
    cases.extend(generate_neighbor_queries(gt))
    cases.extend(generate_type_queries(gt))
    cases.extend(generate_semantic_queries(gt))
    cases.extend(generate_spatial_queries(gt))

    rng = random.Random(seed)
    rng.shuffle(cases)
    return cases


def generate_all_cases(
    objects: Sequence[GroundTruthObject],
    changes: Sequence[GroundTruthChange],
    seed: int = 42,
) -> list[EvalCase]:
    """Generate legacy evaluation cases (for backward compatibility)."""
    cases = []
    cases.extend(generate_symbol_queries(objects))
    cases.extend(generate_change_queries(changes))
    rng = random.Random(seed)
    rng.shuffle(cases)
    return cases
