"""Structured intent schemas for LLM task planning."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class LocationConstraint(BaseModel):
    """Location constraint supporting both Place and Room simultaneously."""
    node_id: str | None = None
    text: str = ""
    node_type: Literal["place", "room"] = "place"


class RelationConstraint(BaseModel):
    """Constraint on entity relationship."""
    relation: Literal["inside", "contains", "near", "connected_to", "parent_of", "child_of"]
    target_text: str = ""
    target_node_id: str | None = None
    target_label: str | None = None
    target_node_type: Literal["object", "place", "room", "agent"] | None = None


class AggregateConstraint(BaseModel):
    """Constraint for aggregate queries (e.g., 'room with most desks')."""
    operation: Literal["max_count", "min_count", "count_at_least", "count_in_location"]
    target_labels: list[str] = Field(default_factory=list)
    value: int | None = None
    location: LocationConstraint | None = None


class EntityReference(BaseModel):
    """Reference to an entity in natural language.

    LLM outputs this structure - no node IDs allowed.
    Supports:
    - Simple text reference
    - Label/type/name matching
    - Room constraint (backward compatible)
    - Location constraints (Place + Room simultaneously)
    - Relation constraints (near, inside, etc.)
    - Aggregate constraints (most desks, etc.)
    """
    text: str
    node_type: Literal["object", "place", "room", "agent"] | None = None
    label: str | None = None
    name: str | None = None
    room: str | None = None  # backward compatible
    locations: list[LocationConstraint] = Field(default_factory=list)  # new: multi-location
    relations: list[RelationConstraint] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)
    position: list[float] | None = None
    quantifier: Literal["one", "all", "nearest", "any"] = "one"
    aggregate: AggregateConstraint | None = None


class TaskConstraint(BaseModel):
    """Constraint for a task step."""
    type: Literal["avoid", "prefer", "condition"]
    description: str
    target_text: str | None = None
    target_label: str | None = None


class IntentStep(BaseModel):
    """A single step in a multi-step intent."""
    action: Literal["navigate", "escort", "guide", "avoid", "locate", "wait", "ask_clarification", "unsupported"]
    actor: EntityReference | None = None
    target: EntityReference | None = None
    source: EntityReference | None = None
    destination: EntityReference | None = None
    constraints: list[TaskConstraint] = Field(default_factory=list)
    description: str = ""


class TaskIntent(BaseModel):
    """Structured intent from LLM.

    Supports single or multi-step intents.
    This is what the LLM should output - no node IDs.
    """
    # Single-step intent (backward compatible)
    action: Literal["navigate", "escort", "guide", "avoid", "locate", "wait", "ask_clarification", "unsupported"]
    actor: EntityReference | None = None
    target: EntityReference | None = None
    source: EntityReference | None = None
    destination: EntityReference | None = None
    constraints: list[TaskConstraint] = Field(default_factory=list)
    description: str = ""

    # Multi-step intent (optional)
    steps: list[IntentStep] | None = None


class IntentParseResult(BaseModel):
    """Result from intent parsing with status."""
    status: Literal["supported", "unsupported", "ambiguous"]
    intent: TaskIntent | None = None
    reason: str | None = None


class ParsedTaskIntent(BaseModel):
    """Parsed task intent with all references."""
    action: str
    targets: list[EntityReference] = Field(default_factory=list)
    source: EntityReference | None = None
    destination: EntityReference | None = None
    constraints: list[TaskConstraint] = Field(default_factory=list)
