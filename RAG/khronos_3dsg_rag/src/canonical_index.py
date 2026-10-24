"""Canonical graph index for efficient scene graph queries."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from api_schemas import SceneEntity, SceneRelation

logger = logging.getLogger(__name__)


class CanonicalGraphIndex:
    """Unified graph index built once and cached.

    Provides efficient lookups for:
    - node by ID
    - nodes by label/type
    - parents/children/neighbors
    - room/place/building ancestors
    """

    def __init__(self):
        self.nodes: dict[str, SceneEntity] = {}
        self.parents: dict[str, list[str]] = defaultdict(list)
        self.children: dict[str, list[str]] = defaultdict(list)
        self.neighbors: dict[str, list[str]] = defaultdict(list)
        self.labels: dict[str, list[str]] = defaultdict(list)
        self.types: dict[str, list[str]] = defaultdict(list)
        self._loaded = False

    def load_from_chromadb(self, db_dir: str | Path) -> None:
        """Load canonical index from ChromaDB."""
        if self._loaded:
            return

        import chromadb
        from chromadb.config import Settings

        client = chromadb.PersistentClient(
            path=str(db_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        collection = client.get_collection("scene_graph")

        all_meta = collection.get(include=["metadatas", "documents"])

        for i, doc_id in enumerate(all_meta.get("ids", [])):
            meta = all_meta["metadatas"][i] if i < len(all_meta["metadatas"]) else {}
            doc = all_meta["documents"][i] if i < len(all_meta["documents"]) else ""

            nid = meta.get("node_id", "")
            if not nid:
                continue

            # Parse JSON fields
            for field_name in ["parent_ids", "child_ids", "neighbor_ids"]:
                val = meta.get(field_name, [])
                if isinstance(val, str):
                    try:
                        meta[field_name] = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        meta[field_name] = []

            # Create or update SceneEntity
            if nid not in self.nodes:
                self.nodes[nid] = SceneEntity(
                    node_id=nid,
                    node_type=meta.get("node_type", ""),
                    label=meta.get("label", ""),
                    name=meta.get("name", ""),
                    object_symbol=meta.get("object_symbol", ""),
                    description=doc[:500] if doc else "",
                )

            entity = self.nodes[nid]

            # Update fields if present
            if meta.get("label") and not entity.label:
                entity.label = meta["label"]
            if meta.get("name") and not entity.name:
                entity.name = meta["name"]
            if meta.get("object_symbol"):
                entity.object_symbol = meta["object_symbol"]
            if meta.get("node_type") and not entity.node_type:
                entity.node_type = meta["node_type"]

            # Position
            if not entity.position and meta.get("position"):
                entity.position = meta["position"]

            # Bounding box
            bb_center = meta.get("bounding_box_center", [])
            if isinstance(bb_center, str):
                try:
                    bb_center = json.loads(bb_center)
                except (json.JSONDecodeError, TypeError):
                    bb_center = []
            if bb_center and not entity.bbox_center:
                entity.bbox_center = bb_center

            bb_dims = meta.get("bounding_box_dimensions", [])
            if isinstance(bb_dims, str):
                try:
                    bb_dims = json.loads(bb_dims)
                except (json.JSONDecodeError, TypeError):
                    bb_dims = []
            if bb_dims and not entity.bbox_dimensions:
                entity.bbox_dimensions = bb_dims

            # Graph relationships - correct direction
            parent_ids = meta.get("parent_ids", [])
            if parent_ids:
                entity.parent_ids = list(set(entity.parent_ids + parent_ids))
                for pid in entity.parent_ids:
                    # nid's parent is pid, so parents[nid] should contain pid
                    if pid not in self.parents[nid]:
                        self.parents[nid].append(pid)

            child_ids = meta.get("child_ids", [])
            if child_ids:
                entity.child_ids = list(set(entity.child_ids + child_ids))
                for cid in entity.child_ids:
                    # nid's child is cid, so children[nid] should contain cid
                    if cid not in self.children[nid]:
                        self.children[nid].append(cid)

            neighbor_ids = meta.get("neighbor_ids", [])
            if neighbor_ids:
                entity.neighbor_ids = list(set(entity.neighbor_ids + neighbor_ids))
                for nid2 in entity.neighbor_ids:
                    # nid's neighbor is nid2, so neighbors[nid] should contain nid2
                    if nid2 not in self.neighbors[nid]:
                        self.neighbors[nid].append(nid2)

            # Index by label and type (use set to avoid duplicates)
            if entity.label:
                if nid not in self.labels[entity.label.lower()]:
                    self.labels[entity.label.lower()].append(nid)
            if entity.node_type:
                if nid not in self.types[entity.node_type.lower()]:
                    self.types[entity.node_type.lower()].append(nid)

        # Complete bidirectional edges
        self._complete_bidirectional_edges()

        # Compute ancestors (room_id, place_id, building_id)
        self._compute_ancestors()

        self._loaded = True
        logger.info(f"Loaded canonical index: {len(self.nodes)} nodes")

    def load_from_json(self, json_path: str | Path) -> None:
        """Load canonical index from JSON file (faster than ChromaDB).

        Supports both v1 (ChromaDB-derived) and v2 (raw DSG direct export) formats.
        V2 format has schema_version=2 and uses parent_place_ids/parent_room_ids.
        """
        if self._loaded:
            return

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        schema_version = data.get("schema_version", 1)

        if schema_version >= 2:
            self._load_from_json_v2(data)
        else:
            self._load_from_json_v1(data)

        self._loaded = True
        logger.info(f"Loaded canonical index from JSON v{schema_version}: {len(self.nodes)} nodes")

    def _load_from_json_v2(self, data: dict) -> None:
        """Load from v2 canonical graph (direct from raw DSG).

        V2 uses parent_place_ids / parent_room_ids lists and pre-computed
        room_id / place_id / building_id.
        """
        for nid, node_data in data.get("nodes", {}).items():
            self.nodes[nid] = SceneEntity(
                node_id=node_data.get("node_id", nid),
                node_type=node_data.get("node_type", ""),
                label=node_data.get("label", ""),
                name=node_data.get("name", ""),
                object_symbol=node_data.get("object_symbol", ""),
                position=node_data.get("position", []),
                bbox_center=node_data.get("bbox_center", []),
                bbox_dimensions=node_data.get("bbox_dimensions", []),
                is_active=node_data.get("is_active"),
                first_seen_ns=node_data.get("first_seen_ns", 0),
                last_seen_ns=node_data.get("last_seen_ns", 0),
                parent_ids=node_data.get("parent_place_ids", []) + node_data.get("parent_room_ids", []) + node_data.get("parent_building_ids", []),
                child_ids=node_data.get("child_ids", []),
                neighbor_ids=node_data.get("neighbor_ids", []),
                room_id=node_data.get("room_id", ""),
                place_id=node_data.get("place_id", ""),
                building_id=node_data.get("building_id", ""),
                hierarchy_path_ids=node_data.get("hierarchy_path_ids", []),
                description=node_data.get("description", ""),
            )

        # Load edges
        edges = data.get("edges", {})
        # V2 uses hierarchy list and neighbor list (not parent/child dicts)
        for he in edges.get("hierarchy", []):
            parent_id = he.get("parent", "")
            child_id = he.get("child", "")
            if parent_id and child_id:
                if child_id not in self.parents:
                    self.parents[child_id] = []
                if parent_id not in self.parents[child_id]:
                    self.parents[child_id].append(parent_id)
                if parent_id not in self.children:
                    self.children[parent_id] = []
                if child_id not in self.children[parent_id]:
                    self.children[parent_id].append(child_id)

        for ne in edges.get("neighbors", []):
            src = ne.get("source", "")
            tgt = ne.get("target", "")
            if src and tgt:
                if src not in self.neighbors:
                    self.neighbors[src] = []
                if tgt not in self.neighbors[src]:
                    self.neighbors[src].append(tgt)
                if tgt not in self.neighbors:
                    self.neighbors[tgt] = []
                if src not in self.neighbors[tgt]:
                    self.neighbors[tgt].append(src)

        # Rebuild label and type indices
        for nid, entity in self.nodes.items():
            if entity.label:
                if nid not in self.labels[entity.label.lower()]:
                    self.labels[entity.label.lower()].append(nid)
            if entity.node_type:
                if nid not in self.types[entity.node_type.lower()]:
                    self.types[entity.node_type.lower()].append(nid)

    def _load_from_json_v1(self, data: dict) -> None:
        """Load from v1 canonical graph (legacy ChromaDB-derived format)."""
        for nid, node_data in data.get("nodes", {}).items():
            self.nodes[nid] = SceneEntity(
                node_id=node_data.get("node_id", nid),
                node_type=node_data.get("node_type", ""),
                label=node_data.get("label", ""),
                name=node_data.get("name", ""),
                object_symbol=node_data.get("object_symbol", ""),
                position=node_data.get("position", []),
                bbox_center=node_data.get("bbox_center", []),
                bbox_dimensions=node_data.get("bbox_dimensions", []),
                is_active=node_data.get("is_active"),
                first_seen_ns=node_data.get("first_seen_ns", 0),
                last_seen_ns=node_data.get("last_seen_ns", 0),
                parent_ids=node_data.get("parent_ids", []),
                child_ids=node_data.get("child_ids", []),
                neighbor_ids=node_data.get("neighbor_ids", []),
                room_id=node_data.get("room_id", ""),
                place_id=node_data.get("place_id", ""),
                building_id=node_data.get("building_id", ""),
                hierarchy_path_ids=node_data.get("hierarchy_path_ids", []),
                description=node_data.get("description", ""),
            )

        # Load edges
        edges = data.get("edges", {})
        for nid, parent_ids in edges.get("parents", {}).items():
            self.parents[nid] = parent_ids
        for nid, child_ids in edges.get("children", {}).items():
            self.children[nid] = child_ids
        for nid, neighbor_ids in edges.get("neighbors", {}).items():
            self.neighbors[nid] = neighbor_ids

        # Rebuild label and type indices
        for nid, entity in self.nodes.items():
            if entity.label:
                if nid not in self.labels[entity.label.lower()]:
                    self.labels[entity.label.lower()].append(nid)
            if entity.node_type:
                if nid not in self.types[entity.node_type.lower()]:
                    self.types[entity.node_type.lower()].append(nid)

    def _complete_bidirectional_edges(self) -> None:
        """Complete bidirectional edges for consistency.

        If A is parent of B, then B should be child of A.
        If A is neighbor of B, then B should be neighbor of A.
        """
        # Complete parent-child edges
        # Membership sets are local; keep the original lists and append order.
        child_members = defaultdict(set, ((nid, set(ids)) for nid, ids in self.children.items()))
        for nid, parent_ids in list(self.parents.items()):
            for pid in parent_ids:
                if nid not in child_members[pid]:
                    self.children[pid].append(nid)
                    child_members[pid].add(nid)

        parent_members = defaultdict(set, ((nid, set(ids)) for nid, ids in self.parents.items()))
        for nid, child_ids in list(self.children.items()):
            for cid in child_ids:
                if nid not in parent_members[cid]:
                    self.parents[cid].append(nid)
                    parent_members[cid].add(nid)

        # Complete neighbor edges (symmetric)
        neighbor_members = defaultdict(set, ((nid, set(ids)) for nid, ids in self.neighbors.items()))
        for nid, neighbor_ids in list(self.neighbors.items()):
            for nid2 in neighbor_ids:
                if nid not in neighbor_members[nid2]:
                    self.neighbors[nid2].append(nid)
                    neighbor_members[nid2].add(nid)

    def _compute_ancestors(self) -> None:
        """Compute room_id, place_id, building_id for all nodes."""
        for nid, entity in self.nodes.items():
            # Walk up parent chain to find room, place, building
            visited = set()
            current = nid
            for _ in range(10):  # Max depth
                if current in visited:
                    break
                visited.add(current)

                parent_ids = self.parents.get(current, [])
                if not parent_ids:
                    break

                parent_id = parent_ids[0]  # Take first parent
                parent = self.nodes.get(parent_id)
                if not parent:
                    break

                # Map layer to ancestor type
                if parent.node_type in ("room", "rooms"):
                    entity.room_id = parent_id
                elif parent.node_type in ("place", "places"):
                    entity.place_id = parent_id
                elif parent.node_type in ("building", "buildings"):
                    entity.building_id = parent_id

                # Build hierarchy path
                if parent_id not in entity.hierarchy_path_ids:
                    entity.hierarchy_path_ids.append(parent_id)

                current = parent_id

    def get_node(self, node_id: str) -> SceneEntity | None:
        """Get a node by ID."""
        return self.nodes.get(node_id)

    def get_nodes_by_label(self, label: str) -> list[SceneEntity]:
        """Get all nodes with a given label."""
        nids = self.labels.get(label.lower(), [])
        return [self.nodes[nid] for nid in nids if nid in self.nodes]

    def get_nodes_by_type(self, node_type: str) -> list[SceneEntity]:
        """Get all nodes of a given type."""
        nids = self.types.get(node_type.lower(), [])
        return [self.nodes[nid] for nid in nids if nid in self.nodes]

    def get_parents(self, node_id: str) -> list[SceneEntity]:
        """Get parent nodes."""
        parent_ids = self.parents.get(node_id, [])
        return [self.nodes[pid] for pid in parent_ids if pid in self.nodes]

    def get_children(self, node_id: str) -> list[SceneEntity]:
        """Get child nodes."""
        child_ids = self.children.get(node_id, [])
        return [self.nodes[cid] for cid in child_ids if cid in self.nodes]

    def get_neighbors(self, node_id: str) -> list[SceneEntity]:
        """Get neighbor nodes."""
        neighbor_ids = self.neighbors.get(node_id, [])
        return [self.nodes[nid] for nid in neighbor_ids if nid in self.nodes]

    def get_ancestors(
        self,
        node_id: str,
        target_type: str | None = None,
    ) -> list[SceneEntity]:
        """Get ancestor nodes, optionally filtered by type."""
        entity = self.nodes.get(node_id)
        if not entity:
            return []

        ancestors = []
        if target_type:
            # Return specific ancestor type
            if target_type == "room" and entity.room_id:
                ancestor = self.nodes.get(entity.room_id)
                if ancestor:
                    ancestors.append(ancestor)
            elif target_type == "place" and entity.place_id:
                ancestor = self.nodes.get(entity.place_id)
                if ancestor:
                    ancestors.append(ancestor)
            elif target_type == "building" and entity.building_id:
                ancestor = self.nodes.get(entity.building_id)
                if ancestor:
                    ancestors.append(ancestor)
        else:
            # Return all ancestors in hierarchy order
            for aid in entity.hierarchy_path_ids:
                ancestor = self.nodes.get(aid)
                if ancestor:
                    ancestors.append(ancestor)

        return ancestors

    def get_all_node_ids(self) -> set[str]:
        """Get all node IDs in the index."""
        return set(self.nodes.keys())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "nodes": {nid: {
                "node_id": e.node_id,
                "node_type": e.node_type,
                "label": e.label,
                "name": e.name,
                "room_id": e.room_id,
                "place_id": e.place_id,
                "building_id": e.building_id,
            } for nid, e in self.nodes.items()},
            "total_nodes": len(self.nodes),
        }

    def save_to_json(self, output_path: str | Path) -> None:
        """Save complete canonical graph to JSON file.

        This preserves ALL relationships (not truncated like metadata).
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        graph_data = {
            "total_nodes": len(self.nodes),
            "nodes": {},
            "edges": {
                "parents": dict(self.parents),
                "children": dict(self.children),
                "neighbors": dict(self.neighbors),
            },
        }

        for nid, entity in self.nodes.items():
            graph_data["nodes"][nid] = {
                "node_id": entity.node_id,
                "node_type": entity.node_type,
                "label": entity.label,
                "name": entity.name,
                "object_symbol": entity.object_symbol,
                "position": entity.position,
                "bbox_center": entity.bbox_center,
                "bbox_dimensions": entity.bbox_dimensions,
                "is_active": entity.is_active,
                "first_seen_ns": entity.first_seen_ns,
                "last_seen_ns": entity.last_seen_ns,
                "parent_ids": entity.parent_ids,
                "child_ids": entity.child_ids,
                "neighbor_ids": entity.neighbor_ids,
                "room_id": entity.room_id,
                "place_id": entity.place_id,
                "building_id": entity.building_id,
                "hierarchy_path_ids": entity.hierarchy_path_ids,
                "description": entity.description,
            }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(graph_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Saved canonical graph to {output_path}: {len(self.nodes)} nodes")


# Global cache - db_dir aware
_index_instances: dict[str, CanonicalGraphIndex] = {}


def get_canonical_index(db_dir: str | Path) -> CanonicalGraphIndex:
    """Get or create the canonical graph index (cached per db_dir).

    Loading priority:
    1. canonical_graph_v2.json (raw DSG direct export, authoritative)
    2. canonical_graph.json (legacy v1 format)
    3. ChromaDB fallback (development only)
    """
    key = str(Path(db_dir).resolve())
    if key not in _index_instances:
        index = CanonicalGraphIndex()

        # Try v2 first (authoritative)
        v2_path = Path(db_dir).parent / "canonical_graph_v2.json"
        if v2_path.exists():
            index.load_from_json(v2_path)
        else:
            # Try v1 legacy
            v1_path = Path(db_dir).parent / "canonical_graph.json"
            if v1_path.exists():
                index.load_from_json(v1_path)
            else:
                # ChromaDB fallback (development only)
                index.load_from_chromadb(db_dir)
                try:
                    index.save_to_json(v1_path)
                except Exception as e:
                    logger.warning(f"Failed to save canonical graph JSON: {e}")

        _index_instances[key] = index
    return _index_instances[key]


def reset_index(db_dir: str | Path | None = None) -> None:
    """Reset the global index (for testing).

    Args:
        db_dir: If provided, reset only this db's index. Otherwise reset all.
    """
    if db_dir:
        key = str(Path(db_dir).resolve())
        _index_instances.pop(key, None)
    else:
        _index_instances.clear()
