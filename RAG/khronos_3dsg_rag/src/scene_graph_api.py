"""Public API for scene graph RAG queries."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from api_schemas import (
    EntityReference,
    NavigationTarget,
    ResolutionCandidate,
    ResolutionResult,
    SceneEntity,
    SceneGraphResult,
    SceneRelation,
)
from canonical_index import CanonicalGraphIndex, get_canonical_index

logger = logging.getLogger(__name__)


class SceneGraphRAG:
    """Public API for scene graph RAG queries.

    This is the main entry point for the LLM task planner.
    It provides high-level methods for entity resolution,
    graph traversal, and navigation target computation.
    """

    def __init__(self, db_dir: str | Path):
        """Initialize with vector DB directory.

        Args:
            db_dir: Path to ChromaDB vector database.
        """
        self.db_dir = Path(db_dir)
        self._index: CanonicalGraphIndex | None = None
        self._resolver = None  # Lazy-loaded EntityResolver

    def _get_index(self) -> CanonicalGraphIndex:
        """Get or load the canonical graph index."""
        if self._index is None:
            self._index = get_canonical_index(self.db_dir)
        return self._index

    def _get_resolver(self):
        """Get or create cached EntityResolver."""
        if self._resolver is None:
            from entity_resolver import EntityResolver
            self._resolver = EntityResolver(self.db_dir)
        return self._resolver

    def get_node(self, node_id: str) -> SceneEntity | None:
        """Get a node by ID.

        Args:
            node_id: The scene graph node ID.

        Returns:
            SceneEntity if found, None otherwise.
        """
        index = self._get_index()
        return index.get_node(node_id)

    def _normalize_type(self, value: str) -> str:
        """Normalize node type for comparison (singular, lowercase)."""
        return value.lower().rstrip("s")

    def _make_candidate(self, node: SceneEntity, score: float, matched: list[str]) -> ResolutionCandidate:
        """Create a ResolutionCandidate from a SceneEntity."""
        return ResolutionCandidate(
            node_id=node.node_id,
            node_type=node.node_type,
            label=node.label,
            name=node.name,
            score=score,
            room_id=node.room_id,
            place_id=node.place_id,
            position=node.position,
            is_active=node.is_active,
            matched_constraints=matched,
        )

    def _resolve_nearest_by_position(self, reference: EntityReference, index: 'CanonicalGraphIndex') -> ResolutionResult:
        """Resolve nearest node by position using canonical index directly.

        Always returns the strictly nearest node (no tie threshold).
        Applies label and location constraints before distance ranking.
        """
        import math

        ref_pos = reference.position
        if not ref_pos or len(ref_pos) < 3:
            return ResolutionResult(
                status="not_found",
                reference=reference,
                reason="No reference position provided for nearest query",
            )

        # Get complete type pool
        expected_type = self._normalize_type(reference.node_type) if reference.node_type else None
        candidates = []
        for nid, node in index.nodes.items():
            if expected_type and self._normalize_type(node.node_type) != expected_type:
                continue
            if not node.position or len(node.position) < 3:
                continue
            candidates.append(node)

        # Apply label hard filter
        if reference.label:
            expected_label = reference.label.lower()
            candidates = [c for c in candidates if c.label.lower() == expected_label]

        # Apply location constraints
        candidates = self._apply_location_filter(reference, candidates, index)

        if not candidates:
            return ResolutionResult(
                status="not_found",
                reference=reference,
                reason=f"No {reference.node_type or 'nodes'} matching all constraints found",
            )

        # Rank by distance
        dists = []
        for node in candidates:
            dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(ref_pos, node.position)))
            dists.append((node, dist))
        dists.sort(key=lambda x: x[1])

        nearest_node, nearest_dist = dists[0]
        selected = self._make_candidate(nearest_node, 1.0, ["nearest"])
        all_candidates = [self._make_candidate(c, 1.0 / (1 + d), ["nearest"]) for c, d in dists[:10]]

        return ResolutionResult(
            status="resolved",
            reference=reference,
            selected=selected,
            candidates=all_candidates,
        )

    def _resolve_all_by_label(self, reference: EntityReference, index: 'CanonicalGraphIndex') -> ResolutionResult:
        """Resolve all nodes matching label/type using canonical index directly.

        Applies label, type, and location constraints.
        """
        # Get all nodes by label
        if reference.label:
            candidates = index.get_nodes_by_label(reference.label)
        elif reference.node_type:
            candidates = index.get_nodes_by_type(reference.node_type)
        else:
            return ResolutionResult(
                status="not_found",
                reference=reference,
                reason="No label or node_type specified for all query",
            )

        # Hard filter: node_type
        if reference.node_type:
            expected_type = self._normalize_type(reference.node_type)
            candidates = [c for c in candidates if self._normalize_type(c.node_type) == expected_type]

        # Apply location constraints
        candidates = self._apply_location_filter(reference, candidates, index)

        if not candidates:
            return ResolutionResult(
                status="not_found",
                reference=reference,
                reason=f"No nodes matching label='{reference.label}' type='{reference.node_type}' with location constraints",
            )

        result_candidates = [self._make_candidate(c, 1.0, ["label_exact"]) for c in candidates]

        return ResolutionResult(
            status="resolved_set",
            reference=reference,
            selected=result_candidates[0],
            selected_many=result_candidates,
            candidates=result_candidates,
        )

    def _apply_location_filter(self, reference: EntityReference, candidates: list, index: 'CanonicalGraphIndex') -> list:
        """Apply location constraints to filter candidates.

        Supports both legacy `room` field and new `locations` list.
        Each location constraint filters candidates by room_id or place_id.
        """
        import re

        # Apply locations list (new schema)
        if hasattr(reference, 'locations') and reference.locations:
            for loc in reference.locations:
                if not candidates:
                    break
                if loc.node_type == "room" and loc.node_id:
                    candidates = [c for c in candidates if c.room_id == loc.node_id or c.node_id == loc.node_id]
                elif loc.node_type == "place" and loc.node_id:
                    candidates = [c for c in candidates if c.place_id == loc.node_id or c.node_id == loc.node_id]

        # Apply legacy room field
        if reference.room:
            if re.match(r'^\d{12,20}$', reference.room):
                target_node = index.get_node(reference.room)
                if target_node and target_node.node_type in ("room", "rooms"):
                    room_ids = {reference.room}
                    candidates = [c for c in candidates if c.room_id in room_ids or c.node_id in room_ids]
                elif target_node and target_node.node_type in ("place", "places"):
                    place_ids = {reference.room}
                    candidates = [c for c in candidates if c.place_id in place_ids or c.node_id in place_ids]
                else:
                    room_ids = {reference.room}
                    candidates = [c for c in candidates if c.room_id in room_ids or c.place_id in room_ids or c.node_id in room_ids]
            else:
                room_nodes = index.get_nodes_by_label(reference.room)
                room_node_ids = {n.node_id for n in room_nodes if n.node_type in ("room", "rooms")}
                place_node_ids = {n.node_id for n in room_nodes if n.node_type in ("place", "places")}
                if room_node_ids:
                    candidates = [c for c in candidates if c.room_id in room_node_ids or c.node_id in room_node_ids]
                elif place_node_ids:
                    candidates = [c for c in candidates if c.place_id in place_node_ids or c.node_id in place_node_ids]

        return candidates

    def _apply_relation_constraints(self, relations, candidates, index):
        """Apply relation constraints to filter candidates."""
        for rel in relations:
            if not candidates:
                break

            # Get target node ID (prefer target_node_id over text lookup)
            target_node_id = rel.target_node_id
            if not target_node_id and rel.target_text:
                # Try to find target by text
                target_node = index.get_node(rel.target_text)
                if target_node:
                    target_node_id = target_node.node_id

            if not target_node_id:
                continue

            target_node = index.get_node(target_node_id)
            if not target_node:
                continue

            if rel.relation == "inside":
                # Filter to descendants of target (transitive: children, grandchildren, etc.)
                descendants = self._get_descendants(target_node_id, index)
                descendant_ids = {d.node_id for d in descendants}
                descendant_ids.add(target_node_id)  # Include target itself
                candidates = [c for c in candidates if c.node_id in descendant_ids]

            elif rel.relation == "near":
                # Filter to neighbors of target
                neighbors = self.get_neighbors(target_node_id)
                neighbor_ids = {n.node_id for n in neighbors}
                candidates = [c for c in candidates if c.node_id in neighbor_ids]

            elif rel.relation == "connected_to":
                # Filter to neighbors of target (same as near for now)
                neighbors = self.get_neighbors(target_node_id)
                neighbor_ids = {n.node_id for n in neighbors}
                candidates = [c for c in candidates if c.node_id in neighbor_ids]

            elif rel.relation == "contains":
                # Filter to descendants of target (transitive: things that contain the target)
                descendants = self._get_descendants(target_node_id, index)
                descendant_ids = {d.node_id for d in descendants}
                descendant_ids.add(target_node_id)
                candidates = [c for c in candidates if c.node_id in descendant_ids]

        return candidates

    def _get_descendants(self, node_id: str, index: 'CanonicalGraphIndex', max_depth: int = 5) -> list:
        """Get all descendants of a node (transitive children)."""
        descendants = []
        visited = {node_id}
        queue = [node_id]
        for _ in range(max_depth):
            next_queue = []
            for nid in queue:
                children = self.get_children(nid)
                for child in children:
                    if child.node_id not in visited:
                        visited.add(child.node_id)
                        descendants.append(child)
                        next_queue.append(child.node_id)
            queue = next_queue
            if not queue:
                break
        return descendants

    def resolve_entity(
        self,
        reference: EntityReference,
        top_k: int = 5,
    ) -> ResolutionResult:
        """Resolve a natural language entity reference to scene graph nodes.

        Priority:
        1. Exact full node ID (fast path)
        2. quantifier=nearest with position → canonical index nearest
        3. quantifier=all → canonical index by label/type
        4. Semantic EntityResolver with hard filters

        Hard constraints (always applied):
        - node_type: if specified, filter to matching nodes only
        - label: if specified, filter to exact canonical label match
        """
        index = self._get_index()

        # Fast path 1: Exact full node ID
        import re
        if reference.text and re.match(r'^\d{12,20}$', reference.text):
            node = index.get_node(reference.text)
            if node:
                candidates = [self._make_candidate(node, 1.0, ["exact_id"])]
                # Apply location filter
                candidates = self._apply_location_filter(reference, candidates, index)
                if candidates:
                    return ResolutionResult(
                        status="resolved",
                        reference=reference,
                        selected=candidates[0],
                        candidates=candidates,
                    )
                return ResolutionResult(
                    status="not_found",
                    reference=reference,
                    reason=f"Node {reference.text} exists but doesn't match location constraints",
                )

        # Fast path 2: quantifier=nearest with position → spatial
        if reference.quantifier == "nearest" and reference.position:
            return self._resolve_nearest_by_position(reference, index)

        # Fast path 3: quantifier=all → canonical index by label/type
        if reference.quantifier == "all":
            return self._resolve_all_by_label(reference, index)

        # Standard path: Semantic EntityResolver with hard filters
        resolver = self._get_resolver()
        base_candidates = resolver.resolve(
            reference.text,
            top_k=max(top_k * 10, 200),
        )

        # Convert to ResolutionCandidate format
        candidates = []
        for bc in base_candidates:
            node_id = bc["node_id"]
            node = index.get_node(node_id)

            if node:
                matched = []
                if bc.get("score", 0) >= 0.95:
                    matched.append("exact_match")
                elif bc.get("score", 0) >= 0.8:
                    matched.append("label_match")
                else:
                    matched.append("partial_match")

                candidates.append(self._make_candidate(node, bc.get("score", 0.5), matched))

        # Hard filter: node_type
        if reference.node_type:
            expected_type = self._normalize_type(reference.node_type)
            candidates = [
                c for c in candidates
                if self._normalize_type(c.node_type) == expected_type
            ]

        # Hard filter: label (exact canonical match)
        if reference.label:
            expected_label = reference.label.lower()
            candidates = [
                c for c in candidates
                if c.label.lower() == expected_label
            ]

        # Apply location constraints
        candidates = self._apply_location_filter(reference, candidates, index)

        # Apply relation constraints (inside, near, connected_to)
        if reference.relations and candidates:
            candidates = self._apply_relation_constraints(reference.relations, candidates, index)

        # Determine status
        if not candidates:
            return ResolutionResult(
                status="not_found",
                reference=reference,
                reason=f"No nodes found matching '{reference.text}'",
            )

        # Sort by score
        candidates.sort(key=lambda c: c.score, reverse=True)

        # Check for ambiguity
        if len(candidates) > 1 and candidates[0].score - candidates[1].score < 0.1:
            return ResolutionResult(
                status="ambiguous",
                reference=reference,
                candidates=candidates,
                reason=f"Multiple candidates with similar scores: {candidates[0].score:.2f} vs {candidates[1].score:.2f}",
            )

        # Resolved
        return ResolutionResult(
            status="resolved",
            reference=reference,
            selected=candidates[0],
            candidates=candidates,
        )

    def get_children(self, node_id: str) -> list[SceneEntity]:
        """Get child nodes.

        Args:
            node_id: Parent node ID.

        Returns:
            List of child SceneEntity objects.
        """
        index = self._get_index()
        return index.get_children(node_id)

    def get_parents(self, node_id: str) -> list[SceneEntity]:
        """Get parent nodes.

        Args:
            node_id: Child node ID.

        Returns:
            List of parent SceneEntity objects.
        """
        index = self._get_index()
        return index.get_parents(node_id)

    def get_neighbors(self, node_id: str) -> list[SceneEntity]:
        """Get neighbor nodes.

        Args:
            node_id: Target node ID.

        Returns:
            List of neighbor SceneEntity objects.
        """
        index = self._get_index()
        return index.get_neighbors(node_id)

    def get_ancestors(
        self,
        node_id: str,
        target_type: str | None = None,
    ) -> list[SceneEntity]:
        """Get ancestor nodes.

        Args:
            node_id: Target node ID.
            target_type: Optional filter by ancestor type (room, place, building).

        Returns:
            List of ancestor SceneEntity objects.
        """
        index = self._get_index()
        return index.get_ancestors(node_id, target_type)

    def get_navigation_target(self, node_id: str) -> NavigationTarget | None:
        """Get navigation target for a node.

        For object/agent targets, returns the parent place position (navigable).
        For room/place targets, returns the node position directly.

        Args:
            node_id: Target node ID.

        Returns:
            NavigationTarget with navigable position and room/place info, or None.
        """
        node = self.get_node(node_id)
        if not node:
            return None

        # For objects/agents, navigate to the parent place (not object center)
        if node.node_type in ("object", "objects", "agent", "agents"):
            # Find parent place
            if node.place_id:
                place_node = self.get_node(node.place_id)
                if place_node:
                    return NavigationTarget(
                        target_entity_id=node.node_id,
                        navigation_node_id=node.place_id,
                        position=place_node.position or node.bbox_center,
                        room_id=node.room_id,
                        place_id=node.place_id,
                        building_id=node.building_id,
                        goal_type="place",
                        position_source="parent_place",
                    )
            # Fallback to first parent with position
            parents = self.get_parents(node_id)
            for parent in parents:
                if parent.position:
                    return NavigationTarget(
                        target_entity_id=node.node_id,
                        navigation_node_id=parent.node_id,
                        position=parent.position,
                        room_id=node.room_id,
                        place_id=node.place_id,
                        building_id=node.building_id,
                        goal_type="place",
                        position_source="fallback_parent",
                    )

        # For rooms/places/buildings, use node position directly
        return NavigationTarget(
            target_entity_id=node.node_id,
            navigation_node_id=node.node_id,
            position=node.position or node.bbox_center,
            room_id=node.room_id,
            place_id=node.place_id,
            building_id=node.building_id,
            goal_type="node",
            position_source="node_position",
        )

    def get_relations(
        self,
        node_id: str,
        relation_types: list[str] | None = None,
    ) -> list[SceneRelation]:
        """Get relations for a node.

        Args:
            node_id: Target node ID.
            relation_types: Optional filter by relation type.

        Returns:
            List of SceneRelation objects.
        """
        index = self._get_index()
        relations = []

        node = index.get_node(node_id)
        if not node:
            return relations

        # Parent relations
        for pid in node.parent_ids:
            parent = index.get_node(pid)
            if parent:
                relations.append(SceneRelation(
                    subject_id=node_id,
                    relation="inside",
                    object_id=pid,
                    subject_type=node.node_type,
                    object_type=parent.node_type,
                ))

        # Child relations
        for cid in node.child_ids:
            child = index.get_node(cid)
            if child:
                relations.append(SceneRelation(
                    subject_id=node_id,
                    relation="contains",
                    object_id=cid,
                    subject_type=node.node_type,
                    object_type=child.node_type,
                ))

        # Neighbor relations
        for nid in node.neighbor_ids:
            neighbor = index.get_node(nid)
            if neighbor:
                relations.append(SceneRelation(
                    subject_id=node_id,
                    relation="connected_to",
                    object_id=nid,
                    subject_type=node.node_type,
                    object_type=neighbor.node_type,
                ))

        # Filter by relation type if specified
        if relation_types:
            relations = [r for r in relations if r.relation in relation_types]

        return relations

    def search_by_label(self, label: str, top_k: int = 10) -> list[SceneEntity]:
        """Search for nodes by label.

        Args:
            label: Label to search for.
            top_k: Maximum results.

        Returns:
            List of matching SceneEntity objects.
        """
        index = self._get_index()
        return index.get_nodes_by_label(label)[:top_k]

    def search_by_type(self, node_type: str, top_k: int = 10) -> list[SceneEntity]:
        """Search for nodes by type.

        Args:
            node_type: Node type to search for.
            top_k: Maximum results.

        Returns:
            List of matching SceneEntity objects.
        """
        index = self._get_index()
        return index.get_nodes_by_type(node_type)[:top_k]
