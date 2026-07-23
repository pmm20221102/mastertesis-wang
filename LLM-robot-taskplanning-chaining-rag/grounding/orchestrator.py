"""Grounding orchestrator for entity resolution.

Supports dependency-ordered grounding:
1. Resolve room references first
2. Use resolved rooms to filter entity candidates
3. Resolve relation targets
4. Apply hard filtering (room, relation, position)
5. Support aggregate queries (room with most X)
6. Support quantifier all/nearest
7. Support multi-step intent grounding
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

# Add parent directory to path for local schemas import
sys.path.insert(0, str(Path(__file__).parent.parent))

from planner_schemas.intent import EntityReference, TaskIntent, RelationConstraint

# Add RAG src to path for imports
RAG_SRC = Path(__file__).parent.parent.parent / "RAG" / "khronos_3dsg_rag" / "src"
sys.path.insert(0, str(RAG_SRC))

from api_schemas import ResolutionResult, SceneEntity

logger = logging.getLogger(__name__)

# Status priority (higher = more severe)
STATUS_PRIORITY = {
    "system_error": 5,
    "unsupported": 4,
    "not_found": 3,
    "ambiguous": 2,
    "ready": 1,
}


class GroundingResult:
    """Result of grounding all references in a task intent.

    Status priority: system_error > unsupported > not_found > ambiguous > ready
    """

    def __init__(self):
        self.references: dict[str, ResolutionResult] = {}
        self.constraints: dict[str, ResolutionResult] = {}
        self.status: str = "ready"
        self.errors: list[str] = []
        self._status_priority: int = 1

    def add_result(self, key: str, result: ResolutionResult, is_required: bool = True) -> None:
        """Add a resolution result for a reference.

        Args:
            key: Reference key (e.g., "actor", "target", "destination").
            result: Resolution result.
            is_required: If True, failure blocks planning. Only required references
                can change the overall status.
        """
        self.references[key] = result

        # Only required references can change the overall blocking status
        if is_required:
            new_priority = STATUS_PRIORITY.get(result.status, 0)
            if new_priority > self._status_priority:
                self._status_priority = new_priority
                self.status = result.status

            # Track errors for required references
            if result.status in ("ambiguous", "not_found"):
                self.errors.append(f"'{key}' ({result.reference.text}): {result.status}")

    def add_constraint_result(self, key: str, result: ResolutionResult) -> None:
        """Add a constraint resolution result (optional, doesn't block planning)."""
        self.constraints[key] = result

    def get_resolved_ids(self) -> set[str]:
        """Get all resolved node IDs from references."""
        ids = set()
        for result in self.references.values():
            if result.status == "resolved" and result.selected:
                ids.add(result.selected.node_id)
        return ids

    def get_resolved_id_by_role(self, role: str) -> str | None:
        """Get resolved node ID for a specific role."""
        result = self.references.get(role)
        if result and result.status == "resolved" and result.selected:
            return result.selected.node_id
        return None

    def get_constraint_avoid_ids(self) -> set[str]:
        """Get all node IDs that should be avoided."""
        ids = set()
        for key, result in self.constraints.items():
            if "avoid" in key and result.status == "resolved" and result.selected:
                ids.add(result.selected.node_id)
        return ids

    def get_constraint_ids(self) -> set[str]:
        """Get all resolved node IDs from constraints (avoid, prefer, etc.)."""
        ids = set()
        for result in self.constraints.values():
            if result.status == "resolved" and result.selected:
                ids.add(result.selected.node_id)
        return ids

    def get_all_resolved_ids(self) -> set[str]:
        """Get all resolved node IDs including set queries."""
        ids = set()
        for result in self.references.values():
            if result.status in ("resolved", "resolved_set"):
                if result.selected:
                    ids.add(result.selected.node_id)
                # Include all candidates for quantifier=all (resolved_set)
                if result.selected_many:
                    for c in result.selected_many:
                        ids.add(c.node_id)
        return ids

    def recompute_status(self) -> None:
        """Recompute overall status from all references.

        Call this after post-processing (aggregate, quantifier, etc.)
        that may have changed individual reference statuses.
        """
        self._status_priority = 1
        self.status = "ready"
        self.errors = []

        for key, result in self.references.items():
            new_priority = STATUS_PRIORITY.get(result.status, 0)
            if new_priority > self._status_priority:
                self._status_priority = new_priority
                self.status = result.status
            if result.status in ("ambiguous", "not_found"):
                self.errors.append(f"'{key}' ({result.reference.text}): {result.status}")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        refs_dict = {}
        for key, result in self.references.items():
            ref_data = {
                "status": result.status,
                "selected": {
                    "node_id": result.selected.node_id,
                    "node_type": result.selected.node_type,
                    "label": result.selected.label,
                    "room_id": result.selected.room_id,
                    "position": result.selected.position,
                } if result.selected else None,
                "candidates_count": len(result.candidates),
                "candidate_node_ids": sorted([c.node_id for c in result.candidates]),
                "reason": result.reason,
            }
            # Include selected_many for set queries (resolved_set status)
            if result.selected_many:
                ref_data["selected_node_ids"] = sorted([c.node_id for c in result.selected_many])
            # Include aggregate_value for count queries
            if result.aggregate_value is not None:
                ref_data["aggregate_value"] = result.aggregate_value
            refs_dict[key] = ref_data

        return {
            "status": self.status,
            "references": refs_dict,
            "constraints": {
                key: {
                    "status": result.status,
                    "selected_node_id": result.selected.node_id if result.selected else None,
                }
                for key, result in self.constraints.items()
            },
            "errors": self.errors,
        }


class GroundingOrchestrator:
    """Orchestrates entity grounding for task intents.

    Supports dependency-ordered grounding:
    1. Resolve room references first
    2. Use resolved rooms to filter entity candidates
    3. Resolve relation targets
    4. Apply hard filtering (room, relation, position)
    5. Support aggregate queries
    6. Support quantifier all/nearest
    7. Support multi-step intent grounding
    """

    def __init__(self, scene_graph_rag):
        """Initialize with SceneGraphRAG instance.

        Args:
            scene_graph_rag: SceneGraphRAG instance for entity resolution.
        """
        self.rag = scene_graph_rag
        self._resolved_cache: dict[str, ResolutionResult] = {}

    def ground_intent(self, intent: TaskIntent) -> GroundingResult:
        """Ground all entity references in a task intent with dependency order.

        Args:
            intent: The parsed task intent.

        Returns:
            GroundingResult with resolution results.
        """
        result = GroundingResult()

        # Step 1: Ground room references first (they filter other entities)
        room_refs = self._extract_room_references(intent)
        for key, ref in room_refs.items():
            logger.info(f"Grounding room '{key}': {ref.text}")
            resolution = self._resolve_with_room_context(ref, None)
            result.add_result(key, resolution, is_required=False)

        # Step 2: Ground main references with room context
        main_refs = {
            "actor": intent.actor,
            "target": intent.target,
            "source": intent.source,
            "destination": intent.destination,
        }

        for key, ref in main_refs.items():
            if ref is None:
                continue

            logger.info(f"Grounding '{key}': {ref.text}")

            # Get room context if available
            room_context = None
            if ref.room:
                # Try to find resolved room by label match or room number
                room_text = ref.room.lower()
                for room_key, room_result in result.references.items():
                    if room_result.status == "resolved" and room_result.selected:
                        label = room_result.selected.label.lower()
                        name = (room_result.selected.name or "").lower()
                        # Match by exact label, partial match, or room number
                        if (room_text in label or room_text in name or
                            label in room_text or name in room_text):
                            room_context = room_result.selected
                            break

            resolution = self._resolve_with_room_context(ref, room_context)

            # Apply relation filtering if present
            if ref.relations and resolution.status in ("resolved", "ambiguous"):
                resolution = self._apply_relation_filter(ref, resolution)

            # Apply quantifier if present
            if ref.quantifier != "one" and resolution.status in ("resolved", "ambiguous"):
                resolution = self._apply_quantifier(ref, resolution)

            result.add_result(key, resolution)

            if resolution.status == "resolved":
                logger.info(f"  Resolved to: {resolution.selected.node_id}")
            elif resolution.status == "ambiguous":
                logger.info(f"  Ambiguous: {len(resolution.candidates)} candidates")
            else:
                logger.info(f"  Not found: {resolution.reason}")

        # Step 3: Handle aggregate constraints
        for i, constraint in enumerate(intent.constraints):
            if constraint.target_text:
                ref = EntityReference(
                    text=constraint.target_text,
                    label=constraint.target_label,
                )
                key = f"constraint_{constraint.type}_{i}"
                resolution = self.rag.resolve_entity(ref)
                result.add_constraint_result(key, resolution)

        # Step 4: Handle aggregate queries on target
        if intent.target and intent.target.aggregate:
            self._handle_aggregate(intent.target, result)
            # Recompute status after aggregate may have changed target resolution
            result.recompute_status()

        # Step 5: Ground multi-step intent
        if intent.steps:
            self._ground_multi_step(intent.steps, result)

        return result

    def _extract_room_references(self, intent: TaskIntent) -> dict[str, EntityReference]:
        """Extract room references from intent for dependency resolution."""
        room_refs = {}

        # Check all roles for room references
        for role in ["actor", "target", "source", "destination"]:
            ref = getattr(intent, role, None)
            if ref and ref.room:
                room_refs[f"{role}_room"] = EntityReference(
                    text=ref.room,
                    node_type="room",
                )

        return room_refs

    def _resolve_with_room_context(
        self,
        ref: EntityReference,
        room_context: SceneEntity | None,
    ) -> ResolutionResult:
        """Resolve entity reference with optional room context.

        Note: location and relation filtering is now handled by
        SceneGraphRAG.resolve_entity() via the shared constraint pipeline.
        This method only applies legacy room_context for backward compatibility.
        """
        # Base resolution (API applies location/relation filters internally)
        resolution = self.rag.resolve_entity(ref)

        # Apply legacy room filtering if available and resolution is ambiguous
        if room_context and resolution.status == "ambiguous" and resolution.candidates:
            filtered = [
                c for c in resolution.candidates
                if c.room_id == room_context.node_id
            ]
            if filtered:
                resolution.candidates = filtered
                if len(filtered) == 1:
                    resolution.status = "resolved"
                    resolution.selected = filtered[0]
                    resolution.reason = None

        return resolution

    def _apply_relation_filter(
        self,
        ref: EntityReference,
        resolution: ResolutionResult,
    ) -> ResolutionResult:
        """Apply relation constraints to filter candidates.

        For example, "object near desk" filters to objects that are neighbors of desk.
        """
        if not ref.relations or not resolution.candidates:
            return resolution

        for relation in ref.relations:
            # Resolve the relation target
            # Prefer target_node_id (full ID) over text lookup
            target_node_id = relation.target_node_id
            if not target_node_id:
                # Fallback: try direct node lookup if target_text looks like a node ID
                import re
                if relation.target_text and re.match(r'^\d{12,20}$', relation.target_text):
                    target_node = self.rag.get_node(relation.target_text)
                    if target_node:
                        target_node_id = target_node.node_id

            if not target_node_id:
                target_ref = EntityReference(
                    text=relation.target_text,
                    label=relation.target_label,
                    node_type=relation.target_node_type,
                )
                target_result = self.rag.resolve_entity(target_ref)

                # Propagate relation target ambiguity/not_found
                if target_result.status == "ambiguous":
                    resolution.status = "ambiguous"
                    resolution.selected = None
                    resolution.reason = f"Relation target '{relation.target_text}' is ambiguous"
                    return resolution
                elif target_result.status == "not_found":
                    resolution.status = "not_found"
                    resolution.selected = None
                    resolution.reason = f"Relation target '{relation.target_text}' not found"
                    return resolution

                if not target_result.selected:
                    continue

                target_node_id = target_result.selected.node_id

            # Filter candidates based on relation type
            if relation.relation == "near":
                # Filter to neighbors of target
                neighbors = self.rag.get_neighbors(target_node_id)
                neighbor_ids = {n.node_id for n in neighbors}
                resolution.candidates = [
                    c for c in resolution.candidates
                    if c.node_id in neighbor_ids
                ]
            elif relation.relation == "inside":
                # Filter to children of target (things inside the target)
                children = self.rag.get_children(target_node_id)
                child_ids = {c.node_id for c in children}
                resolution.candidates = [
                    c for c in resolution.candidates
                    if c.node_id in child_ids
                ]
            elif relation.relation == "contains":
                # Filter to parents of target (things that contain the target)
                parents = self.rag.get_parents(target_node_id)
                parent_ids = {p.node_id for p in parents}
                resolution.candidates = [
                    c for c in resolution.candidates
                    if c.node_id in parent_ids
                ]
            elif relation.relation == "connected_to":
                # Filter to neighbors of target
                neighbors = self.rag.get_neighbors(target_node_id)
                neighbor_ids = {n.node_id for n in neighbors}
                resolution.candidates = [
                    c for c in resolution.candidates
                    if c.node_id in neighbor_ids
                ]

            # Update status based on filtered candidates
            if not resolution.candidates:
                resolution.status = "not_found"
                resolution.selected = None
                resolution.reason = f"No candidates match relation '{relation.relation}' with '{relation.target_text}'"
            elif len(resolution.candidates) == 1:
                resolution.status = "resolved"
                resolution.selected = resolution.candidates[0]
                resolution.reason = None
            else:
                resolution.status = "ambiguous"
                resolution.reason = f"Multiple candidates match relation constraint"

        return resolution

    def _apply_quantifier(
        self,
        ref: EntityReference,
        resolution: ResolutionResult,
    ) -> ResolutionResult:
        """Apply quantifier to resolution result.

        For "all": return all candidates as resolved set.
        For "nearest": return the closest candidate.
        For "any": return the first candidate.
        """
        if not resolution.candidates:
            return resolution

        # If API already resolved (e.g., nearest fast path), don't override
        if resolution.status in ("resolved", "resolved_set", "ambiguous"):
            return resolution

        if ref.quantifier == "all":
            # Mark as resolved_set with all candidates
            resolution.status = "resolved_set"
            resolution.selected = resolution.candidates[0]  # Primary
            resolution.selected_many = resolution.candidates  # All candidates for set evaluation
            resolution.reason = f"Resolved {len(resolution.candidates)} entities (quantifier: all)"
        elif ref.quantifier == "nearest":
            # Sort by position if available, otherwise use score
            if resolution.candidates[0].position and ref.position:
                # Calculate distance to each candidate
                import math
                ref_pos = ref.position
                def dist(c):
                    if c.position:
                        return math.sqrt(sum((a - b) ** 2 for a, b in zip(ref_pos, c.position)))
                    return float('inf')
                resolution.candidates.sort(key=dist)
            # else sort by score (already sorted)
            resolution.status = "resolved"
            resolution.selected = resolution.candidates[0]
        elif ref.quantifier == "any":
            resolution.status = "resolved"
            resolution.selected = resolution.candidates[0]

        return resolution

    def _handle_aggregate(
        self,
        target_ref: EntityReference,
        result: GroundingResult,
    ) -> None:
        """Handle aggregate queries like 'room with most desks'.

        This modifies the target resolution to select based on aggregate criteria.
        Uses object.room_id to count objects in each room (handles Room -> Place -> Object hierarchy).
        """
        if not target_ref.aggregate:
            return

        aggregate = target_ref.aggregate

        # Get all rooms (v2 type index uses singular: "room")
        rooms = self.rag.search_by_type("room", top_k=50)
        if not rooms:
            rooms = self.rag.search_by_type("rooms", top_k=50)
        if not rooms:
            return

        # Count target labels in each room using object.room_id (handles hierarchy)
        room_counts = {}
        for room in rooms:
            # Get all objects that belong to this room (via room_id field)
            all_objects = self.rag.search_by_type("object", top_k=500)
            if not all_objects:
                all_objects = self.rag.search_by_type("objects", top_k=500)
            count = sum(
                1 for obj in all_objects
                if obj.room_id == room.node_id
                and any(label.lower() in obj.label.lower()
                        for label in aggregate.target_labels)
            )
            if count > 0:
                room_counts[room.node_id] = (room, count)

        if not room_counts:
            return

        # Check for ties (multiple rooms with same count)
        max_count = max(c for _, c in room_counts.values())
        tied_rooms = [rid for rid, (_, count) in room_counts.items() if count == max_count]

        # Select room based on operation
        if aggregate.operation == "max_count":
            if len(tied_rooms) > 1:
                # Tie → ambiguous
                result.status = "ambiguous"
                result.errors.append(f"Aggregate tie: {len(tied_rooms)} rooms have {max_count} items")
                return
            best_room_id = tied_rooms[0]
        elif aggregate.operation == "min_count":
            min_count = min(c for _, c in room_counts.values())
            tied_min = [rid for rid, (_, count) in room_counts.items() if count == min_count]
            if len(tied_min) > 1:
                result.status = "ambiguous"
                result.errors.append(f"Aggregate tie: {len(tied_min)} rooms have {min_count} items")
                return
            best_room_id = tied_min[0]
        elif aggregate.operation == "count_at_least":
            threshold = aggregate.value or 1
            candidates = [rid for rid, (_, count) in room_counts.items() if count >= threshold]
            if not candidates:
                return
            if len(candidates) > 1:
                # Multiple rooms meet threshold → ambiguous
                result.status = "ambiguous"
                result.errors.append(f"Aggregate: {len(candidates)} rooms meet count>={threshold}")
                return
            best_room_id = candidates[0]
        elif aggregate.operation == "count_in_location":
            # Count objects of target labels in a specific location
            if not aggregate.location or not aggregate.location.node_id:
                return
            loc_node_id = aggregate.location.node_id
            loc_type = aggregate.location.node_type

            all_objects = self.rag.search_by_type("object", top_k=500)
            if not all_objects:
                all_objects = self.rag.search_by_type("objects", top_k=500)

            count = 0
            for obj in all_objects:
                # Check location match
                if loc_type == "room" and obj.room_id != loc_node_id:
                    continue
                if loc_type == "place" and obj.place_id != loc_node_id:
                    continue
                # Check label match
                if any(label.lower() in obj.label.lower() for label in aggregate.target_labels):
                    count += 1

            # Return result with aggregate_value
            from api_schemas import ResolutionCandidate
            loc_node = self.rag.get_node(loc_node_id)
            if loc_node:
                selected = ResolutionCandidate(
                    node_id=loc_node_id,
                    node_type=loc_node.node_type,
                    label=loc_node.label,
                    name=loc_node.name,
                    score=0.95,
                    room_id=loc_node.room_id,
                    place_id=loc_node.place_id,
                    position=loc_node.position,
                    is_active=loc_node.is_active,
                    matched_constraints=["aggregate"],
                )
                if "target" in result.references:
                    result.references["target"].status = "resolved"
                    result.references["target"].selected = selected
                    result.references["target"].aggregate_value = count
                    result.references["target"].reason = f"Count: {count} {aggregate.target_labels} in {loc_type} {loc_node_id}"
                else:
                    ref = EntityReference(text=target_ref.text, node_type="room")
                    result.references["target"] = ResolutionResult(
                        status="resolved",
                        reference=ref,
                        selected=selected,
                        aggregate_value=count,
                        reason=f"Count: {count} {aggregate.target_labels} in {loc_type} {loc_node_id}",
                    )
            return
        else:
            return

        best_room, count = room_counts[best_room_id]

        # Update the target resolution
        from api_schemas import ResolutionCandidate
        selected = ResolutionCandidate(
            node_id=best_room.node_id,
            node_type=best_room.node_type,
            label=best_room.label,
            name=best_room.name,
            score=0.95,
            room_id=best_room.room_id,
            place_id=best_room.place_id,
            position=best_room.position,
            is_active=best_room.is_active,
            matched_constraints=["aggregate"],
        )

        # Create or update resolution
        if "target" in result.references:
            result.references["target"].status = "resolved"
            result.references["target"].selected = selected
            result.references["target"].reason = f"Aggregate: {aggregate.operation} of {aggregate.target_labels} = {count}"
        else:
            ref = EntityReference(text=target_ref.text, node_type="room")
            result.references["target"] = ResolutionResult(
                status="resolved",
                reference=ref,
                selected=selected,
                reason=f"Aggregate: {aggregate.operation} of {aggregate.target_labels} = {count}",
            )

    def _ground_multi_step(
        self,
        steps: list,
        result: GroundingResult,
    ) -> None:
        """Ground multi-step intents.

        Each step has its own actor/target/source/destination that need grounding.
        """
        for i, step in enumerate(steps):
            step_key = f"step_{i}"

            # Ground step's target
            if step.target:
                resolution = self.rag.resolve_entity(step.target)
                result.add_result(f"{step_key}_target", resolution, is_required=False)

            # Ground step's actor
            if step.actor:
                resolution = self.rag.resolve_entity(step.actor)
                result.add_result(f"{step_key}_actor", resolution, is_required=False)

            # Ground step's destination
            if step.destination:
                resolution = self.rag.resolve_entity(step.destination)
                result.add_result(f"{step_key}_destination", resolution, is_required=False)

    def ground_references(
        self,
        references: list[EntityReference],
    ) -> dict[str, ResolutionResult]:
        """Ground a list of entity references.

        Args:
            references: List of entity references to resolve.

        Returns:
            Dict mapping reference text to resolution result.
        """
        results = {}
        for ref in references:
            resolution = self.rag.resolve_entity(ref)
            results[ref.text] = resolution
        return results
