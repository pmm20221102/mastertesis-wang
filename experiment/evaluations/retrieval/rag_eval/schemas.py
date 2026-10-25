from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GroundTruthObject:
    dataset: str
    object_id: int
    object_symbol: str
    position: list[float]
    dimensions: list[float]
    first_observed_ns: int
    last_observed_ns: int
    is_active: bool


@dataclass
class GroundTruthChange:
    dataset: str
    object_symbol: str
    appeared_at: int
    disappeared_at: int


@dataclass
class GraphNode:
    node_id: str
    layer: int
    node_type: str
    label: str
    name: str
    position: list[float]
    parent_ids: list[str] = field(default_factory=list)
    child_ids: list[str] = field(default_factory=list)
    neighbor_ids: list[str] = field(default_factory=list)


@dataclass
class GraphEdge:
    source_id: str
    target_id: str
    relation: str = "connected_to"


@dataclass
class GraphGroundTruth:
    dataset: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    node_index: dict[str, GraphNode] = field(default_factory=dict)


@dataclass
class EvalCase:
    case_id: str
    dataset: str
    query_type: str
    query: str
    expected: dict[str, Any]
    relevant_ids: list[str]
    source_node_id: str = ""
    source_node_type: str = ""
    difficulty: str = "easy"
    notes: str = ""


@dataclass
class RetrievedChunk:
    rank: int
    score: float
    node_id: str
    object_symbol: str
    metadata: dict[str, Any]
    text: str


@dataclass
class CaseResult:
    case_id: str
    query: str
    query_type: str
    retrieved_ids: list[str]
    relevant_ids: list[str]
    hit: bool
    metrics: dict[str, float]
    graph_accuracy: float = 0.0
    failure_reason: str = ""


@dataclass
class EvalReport:
    dataset: str
    total_cases: int
    metrics_by_type: dict[str, dict[str, float]]
    overall_metrics: dict[str, float]
    case_results: list[CaseResult]
    failures: list[CaseResult]
    index_coverage: float = 0.0
    total_gt_objects: int = 0
    matched_gt_objects: int = 0
    graph_accuracy_by_type: dict[str, float] = field(default_factory=dict)
