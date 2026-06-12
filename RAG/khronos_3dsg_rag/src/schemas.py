"""Data models for Khronos 3DSG RAG pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BBox:
    """Bounding box."""
    center: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    dimensions: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])


@dataclass
class SceneNode:
    """A single node from the DSG."""
    node_id: str
    layer: int = 0
    partition: int = 0
    node_type: str = ""          # human-readable: "object", "room", "place", "agent", ...
    label: str = ""              # semantic label: "chair", "corridor", ...
    name: str = ""               # from attributes.name
    position: list[float] = field(default_factory=list)
    bbox: BBox | None = None
    color: dict[str, int] | None = None
    is_active: bool | None = None
    first_seen_ns: int | None = None
    last_seen_ns: int | None = None
    raw_attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class SceneEdge:
    """A single edge from the DSG."""
    source_id: str
    target_id: str
    relation: str = "connected_to"
    weight: float = 1.0


@dataclass
class SceneGraph:
    """Parsed scene graph."""
    scene_id: str = ""
    description: str = ""
    nodes: list[SceneNode] = field(default_factory=list)
    edges: list[SceneEdge] = field(default_factory=list)
    # node_id -> SceneNode lookup
    node_index: dict[str, SceneNode] = field(default_factory=dict)


@dataclass
class RagDocument:
    """A single RAG document chunk."""
    doc_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
