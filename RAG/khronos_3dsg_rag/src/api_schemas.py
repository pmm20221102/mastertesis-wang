"""API schemas for RAG public interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class SceneEntity:
    """Canonical representation of a scene graph node."""
    node_id: str
    node_type: str
    label: str
    name: str = ""
    object_symbol: str = ""
    position: list[float] = field(default_factory=list)
    bbox_center: list[float] = field(default_factory=list)
    bbox_dimensions: list[float] = field(default_factory=list)
    is_active: bool | None = None
    first_seen_ns: int = 0
    last_seen_ns: int = 0
    parent_ids: list[str] = field(default_factory=list)
    child_ids: list[str] = field(default_factory=list)
    neighbor_ids: list[str] = field(default_factory=list)
    place_id: str = ""
    room_id: str = ""
    building_id: str = ""
    hierarchy_path_ids: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class EntityReference:
    """Reference to an entity in natural language.

    Rich constraint objects use the API's attribute-based protocol; the RAG DTO
    does not import planner schemas or validate/compile aggregate operations.
    """
    reference_id: str
    text: str
    node_type: str | None = None
    label: str | None = None
    name: str | None = None
    room: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    position: list[float] | None = None
    quantifier: Literal["one", "all", "nearest", "farthest", "any"] = "one"
    locations: list[Any] = field(default_factory=list)
    relations: list[Any] = field(default_factory=list)
    aggregate: Any | None = None


@dataclass
class ResolutionCandidate:
    """A candidate for entity resolution."""
    node_id: str
    node_type: str
    label: str
    name: str = ""
    score: float = 0.0
    room_id: str = ""
    place_id: str = ""
    position: list[float] = field(default_factory=list)
    is_active: bool | None = None
    matched_constraints: list[str] = field(default_factory=list)
    unmatched_constraints: list[str] = field(default_factory=list)


@dataclass
class ResolutionResult:
    """Result of entity resolution."""
    status: Literal["resolved", "resolved_set", "ambiguous", "not_found"]
    reference: EntityReference
    selected: ResolutionCandidate | None = None
    selected_many: list[ResolutionCandidate] = field(default_factory=list)
    candidates: list[ResolutionCandidate] = field(default_factory=list)
    reason: str | None = None
    aggregate_value: int | None = None  # For count queries


@dataclass
class NavigationTarget:
    """Navigation target for robot."""
    target_entity_id: str
    navigation_node_id: str
    position: list[float] = field(default_factory=list)
    frame_id: str = "map"
    room_id: str = ""
    place_id: str = ""
    building_id: str = ""
    goal_type: str = "place"
    position_source: str = ""


@dataclass
class SceneRelation:
    """Relation between two scene entities."""
    subject_id: str
    relation: str
    object_id: str
    subject_type: str = ""
    object_type: str = ""


@dataclass
class SceneGraphResult:
    """Result from scene graph query."""
    entities: list[SceneEntity] = field(default_factory=list)
    relations: list[SceneRelation] = field(default_factory=list)
    total_count: int = 0
