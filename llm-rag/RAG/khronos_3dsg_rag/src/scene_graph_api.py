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

NEAR_RADIUS_M = 3.0
SEMANTIC_RELATION_TOP_K = 5
SEMANTIC_RELATION_THRESHOLD = 0.35


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

    def get_label_ontology(self) -> list[str]:
        """Return current canonical labels only, suitable for scene_ontology.

        The detached, sorted list contains no IDs, positions, relationships or
        evaluation annotations. It does not load a model or invoke a resolver.
        """
        index = self._get_index()
        return sorted(label for label, ids in index.labels.items()
                      if label and any(nid in index.nodes and
                                       index.nodes[nid].label.lower() == label
                                       for nid in ids))

    @staticmethod
    def _explicit_reference(text: str) -> tuple[str, str | None] | None:
        """Read a full numeric ID or an explicit place/room ID, never guess."""
        import re

        match = re.fullmatch(r"\s*(?:inside\s+)?(place|room)\s+(?:id\s+)?([0-9]{1,20})\s*",
                             text, flags=re.IGNORECASE)
        if match:
            return match.group(2), match.group(1).lower()
        if re.fullmatch(r"\s*[0-9]{1,20}\s*", text):
            return text.strip(), None
        return None

    def _constraint_node(self, text, index, node_id=None, node_type=None, label=None):
        """Resolve a constraint anchor; reject conflicting IDs/types/labels."""
        explicit = self._explicit_reference(text or "")
        expected_type = self._normalize_type(node_type) if node_type else None
        if explicit:
            text_id, text_type = explicit
            if node_id and node_id != text_id:
                return None
            if expected_type and text_type and expected_type != text_type:
                return None
            node_id = text_id
            expected_type = expected_type or text_type

        if node_id:
            node = index.get_node(node_id)
        else:
            node = index.get_node(text)
            if node is None:
                canonical = self._canonical_label(label or text, index)
                nodes = index.get_nodes_by_label(canonical)
                if expected_type:
                    nodes = [n for n in nodes if self._normalize_type(n.node_type) == expected_type]
                node = nodes[0] if len(nodes) == 1 else None
        if node is None:
            return None
        if expected_type and self._normalize_type(node.node_type) != expected_type:
            return None
        if label:
            generic = expected_type and label.lower().strip() in {expected_type, expected_type + "s"}
            if not generic and node.label.lower() != self._canonical_label(label, index).lower():
                return None
        return node

    @staticmethod
    def _label_forms(label: str) -> set[str]:
        from semantic_labels import label_forms
        return label_forms(label)

    def _canonical_label(self, label: str, index: CanonicalGraphIndex) -> str:
        from semantic_labels import normalize_label
        labels = [key for key, ids in index.labels.items() if ids]
        return normalize_label(label, labels) or label

    @staticmethod
    def _is_type_label(reference: EntityReference) -> bool:
        """A label naming the explicit type is generic, not a semantic label."""
        if not reference.node_type or not reference.label:
            return False
        kind = reference.node_type.lower().strip()
        singular = kind[:-1] if kind.endswith("s") else kind
        return reference.label.lower().strip() in {singular, singular + "s"}

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
        if not ref_pos or len(ref_pos) < 3 or not all(math.isfinite(value) for value in ref_pos):
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
            if not node.position or len(node.position) < 3 or not all(math.isfinite(value) for value in node.position):
                continue
            candidates.append(node)

        # Apply location constraints
        candidates = self._apply_hard_filters(reference, candidates, index)

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
        farthest = reference.quantifier == "farthest"
        dists.sort(key=lambda x: (-x[1] if farthest else x[1], x[0].node_id))

        nearest_node, nearest_dist = dists[0]
        mode = "farthest" if farthest else "nearest"
        selected = self._make_candidate(nearest_node, 1.0, [mode])
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
        if reference.label and not self._is_type_label(reference):
            candidates = index.get_nodes_by_label(self._canonical_label(reference.label, index))
        elif reference.node_type:
            expected_type = self._normalize_type(reference.node_type)
            candidates = [node for node in index.nodes.values()
                          if self._normalize_type(node.node_type) == expected_type]
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
        candidates = self._apply_hard_filters(reference, candidates, index)

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

    @staticmethod
    def _room_description(text):
        """Parse a complete room-with/containing category description."""
        import re
        match = re.fullmatch(
            r'(?:the\s+)?(?:room|space)\s+(?:containing|with)\s+(.+?)(?:\s+(?:without|excluding)\s+(.+))?',
            text.strip(), re.I)
        if not match:
            return None
        def labels(part):
            if part is None:
                return []
            values = re.split(r'\s+and\s+|,\s*', part)
            if any(not re.fullmatch(r'(?:a |an |the )?[A-Za-z][A-Za-z _-]*', value.strip())
                   for value in values):
                return None
            return [re.sub(r'^(?:a|an|the)\s+', '', value.strip(), flags=re.I)
                    for value in values]
        positive, negative = labels(match[1]), labels(match[2])
        return (positive, negative) if positive is not None and negative is not None else None

    def _room_nodes(self, text, index):
        parsed = self._room_description(text)
        if parsed is None:
            return []
        from semantic_labels import normalize_label
        ontology = self.get_label_ontology()
        positive, negative = ([normalize_label(value, ontology) for value in part]
                              for part in parsed)
        if any(value is None for value in [*positive, *negative]):
            return []
        objects_by_room = {}
        for node in index.nodes.values():
            if self._normalize_type(node.node_type) == 'object':
                objects_by_room.setdefault(node.room_id, set()).add(node.label.lower())
        return [room for room in index.nodes.values()
                if self._normalize_type(room.node_type) == 'room'
                and set(positive) <= objects_by_room.get(room.node_id, set())
                and not set(negative) & objects_by_room.get(room.node_id, set())]

    def _apply_hard_filters(self, reference, candidates, index):
        """Intersect independent extrema on one complete non-ranking pool."""
        def restrict(pool):
            if reference.node_type:
                expected = self._normalize_type(reference.node_type)
                pool = [c for c in pool if self._normalize_type(c.node_type) == expected]
            if reference.label and not self._is_type_label(reference):
                label = self._canonical_label(reference.label, index).lower()
                pool = [c for c in pool if c.label.lower() == label]
            if getattr(reference, "name", None):
                pool = [c for c in pool if c.name.lower() == reference.name.lower()]
            if getattr(reference, "attributes", None):
                return []
            return self._apply_location_filter(reference, pool, index)
        relations = getattr(reference, "relations", [])
        origin_rank = reference.quantifier in ("nearest", "farthest")
        ranking = [r for r in relations if r.relation in ("near", "nearest_to", "farthest_from")]
        structural = [r for r in relations if r not in ranking]
        complete = bool(ranking) or origin_rank
        eligible = restrict(list(index.nodes.values()) if complete else candidates)
        if structural:
            eligible = self._apply_relation_constraints(structural, eligible, index)
        allowed = {c.node_id for c in eligible}
        if ranking:
            allowed.intersection_update(c.node_id for c in
                self._apply_relation_constraints(ranking, eligible, index))
        if origin_rank:
            from types import SimpleNamespace
            if self._position(reference.position) is None:
                return []
            anchors = [SimpleNamespace(position=reference.position)]
            allowed.intersection_update(c.node_id for c in self._rank_pool(
                eligible, anchors, reference.quantifier == "farthest"))
        return [c for c in candidates if c.node_id in allowed]

    def _apply_location_filter(self, reference: EntityReference, candidates: list, index: 'CanonicalGraphIndex') -> list:
        """Apply location constraints to filter candidates.

        Supports both legacy `room` field and new `locations` list.
        Each location constraint filters candidates by room_id or place_id.
        """
        # Apply locations list (new schema)
        if hasattr(reference, 'locations') and reference.locations:
            for loc in reference.locations:
                if not candidates:
                    break
                import re
                if loc.node_type == 'room' and re.match(
                        r'^(?:the\s+)?(?:room|space)\s+(?:containing|with)\b', loc.text, re.I):
                    rooms = {node.node_id for node in self._room_nodes(loc.text, index)}
                    if loc.node_id:
                        rooms.intersection_update([loc.node_id])
                    candidates = [node for node in candidates
                                  if node.room_id in rooms or node.node_id in rooms]
                    continue
                location = self._constraint_node(loc.text, index, loc.node_id, loc.node_type)
                if location is None:
                    return []
                location_id = location.node_id
                if loc.node_type == "room":
                    candidates = [c for c in candidates if c.room_id == location_id or c.node_id == location_id]
                elif loc.node_type == "place":
                    candidates = [c for c in candidates if c.place_id == location_id or c.node_id == location_id]

        # Apply legacy room field
        if reference.room:
            import re
            if re.match(r'^(?:the\s+)?(?:room|space)\s+(?:containing|with)\b',
                        reference.room, re.I):
                rooms = {node.node_id for node in self._room_nodes(reference.room, index)}
                return [node for node in candidates
                        if node.room_id in rooms or node.node_id in rooms]
            location = self._constraint_node(reference.room, index)
            if location is None:
                return []
            kind = self._normalize_type(location.node_type)
            if kind == "room":
                candidates = [c for c in candidates if c.room_id == location.node_id or c.node_id == location.node_id]
            elif kind == "place":
                candidates = [c for c in candidates if c.place_id == location.node_id or c.node_id == location.node_id]
            else:
                return []

        return candidates

    def _constraint_nodes(self, text, index, node_id=None, node_type=None, label=None):
        """Explicit anchors stay exact; category anchors denote complete sets."""
        if label:
            from semantic_labels import normalize_reference_label
            label = normalize_reference_label(label, self.get_label_ontology(), node_type, text)
            if label is None:
                return []
        explicit = self._explicit_reference(text or "")
        if node_id or explicit or index.get_node(text or "") is not None:
            node = self._constraint_node(text, index, node_id, node_type, label)
            return [node] if node else []
        expected = self._normalize_type(node_type) if node_type else None
        if expected == 'room' and self._room_description(text or '') is not None:
            return self._room_nodes(text, index)
        generic = expected and (label or text or "").lower().strip() in (expected, expected+"s")
        nodes = (list(index.nodes.values()) if generic else
                 index.get_nodes_by_label(self._canonical_label(label or text or "", index)))
        if expected:
            nodes = [n for n in nodes if self._normalize_type(n.node_type) == expected]
        if nodes or label or expected != "object" or not text.strip():
            return nodes

        # Resolve an original object phrase semantically, then expand selected
        # canonical labels to all graph instances before relation traversal.
        from semantic_retriever import search_semantic
        semantic = search_semantic(
            text,
            top_k=SEMANTIC_RELATION_TOP_K,
            node_type="object",
            db_dir=self.db_dir,
            threshold=SEMANTIC_RELATION_THRESHOLD,
        )
        ranking = [
            item for item in semantic.label_ranking
            if item.score >= SEMANTIC_RELATION_THRESHOLD
            and item.label
            and item.label.strip().lower() != "unknown"
        ]
        if semantic.status == "not_found" or not ranking:
            return []
        if semantic.status == "ambiguous":
            best_score = ranking[0].score
            ranking = [item for item in ranking if best_score - item.score < 0.05]
        else:
            ranking = ranking[:1]

        resolved: dict[str, Any] = {}
        for item in ranking:
            for node in index.get_nodes_by_label(item.label):
                if self._normalize_type(node.node_type) == "object":
                    resolved[node.node_id] = node
        return [resolved[node_id] for node_id in sorted(resolved)]

    @staticmethod
    def _position(value):
        import math
        try:
            return value if len(value) == 3 and all(math.isfinite(v) for v in value) else None
        except (TypeError, ValueError):
            return None

    def _rank_pool(self, candidates, anchors, farthest=False):
        import math
        if (not candidates or not anchors
                or any(self._position(n.position) is None for n in [*candidates, *anchors])):
            return []
        distances = {c.node_id: min(math.dist(c.position, a.position) for a in anchors)
                     for c in candidates}
        extreme = (max if farthest else min)(distances.values())
        return [c for c in candidates if abs(distances[c.node_id]-extreme) <= 1e-6]

    def _near_pool(self, candidates, anchors):
        """Keep every candidate within the registered raw-3D radius of any anchor."""
        import math
        if (not candidates or not anchors
                or any(self._position(n.position) is None for n in [*candidates, *anchors])):
            return []
        return [c for c in candidates if min(math.dist(c.position, a.position)
                                             for a in anchors) <= NEAR_RADIUS_M]

    def _apply_relation_constraints(self, relations, candidates, index):
        """Apply complete-set topology, bounded near, then explicit extrema."""
        rankings = []
        near_constraints = []
        for rel in relations:
            if not candidates:
                return []
            anchors = self._constraint_nodes(rel.target_text, index, rel.target_node_id,
                getattr(rel, "target_node_type", None), rel.target_label)
            if not anchors:
                return []
            if rel.relation == "near":
                near_constraints.append(anchors)
                continue
            if rel.relation in ("nearest_to", "farthest_from"):
                rankings.append((rel, anchors))
                continue
            allowed = set()
            for target in anchors:
                identifier = target.node_id
                if rel.relation == "inside":
                    allowed.add(identifier)
                    allowed.update(n.node_id for n in self._get_descendants(identifier, index))
                elif rel.relation == "connected_to":
                    allowed.update(n.node_id for n in self.get_neighbors(identifier))
                elif rel.relation == "contains":
                    pending, visited = [identifier], {identifier}
                    while pending:
                        for parent in self.get_parents(pending.pop()):
                            allowed.add(parent.node_id)
                            if parent.node_id not in visited:
                                visited.add(parent.node_id)
                                pending.append(parent.node_id)
                elif rel.relation == "parent_of":
                    allowed.update(n.node_id for n in self.get_parents(identifier))
                elif rel.relation == "child_of":
                    allowed.update(n.node_id for n in self.get_children(identifier))
                else:
                    return []
            candidates = [c for c in candidates if c.node_id in allowed]
        for anchors in near_constraints:
            candidates = self._near_pool(candidates, anchors)
        # Each rank constraint is evaluated on the same eligible pool. Intersect
        # its optima, avoiding instruction-order-dependent sequential reranking.
        allowed = {c.node_id for c in candidates}
        for rel, anchors in rankings:
            optimum = self._rank_pool(candidates, anchors, rel.relation == "farthest_from")
            allowed.intersection_update(c.node_id for c in optimum)
        return [c for c in candidates if c.node_id in allowed]

    def _resolve_ranked_relations(self, reference, index):
        candidates = self._apply_hard_filters(reference, list(index.nodes.values()), index)
        if not candidates:
            return ResolutionResult(status="not_found", reference=reference,
                reason="No candidates satisfy the declared relative-distance contract")
        candidates.sort(key=lambda c: c.node_id)
        tags = [("near:radius3m:raw3d:min_anchor" if r.relation == "near" else
                 r.relation+":raw3d:min_anchor:tie1e-6")
                for r in reference.relations if r.relation in ("near", "nearest_to", "farthest_from")]
        converted = [self._make_candidate(c, 1.0, tags) for c in candidates]
        if reference.quantifier == "all":
            return ResolutionResult(status="resolved_set", reference=reference,
                selected=converted[0], selected_many=converted, candidates=converted)
        return ResolutionResult(status="resolved", reference=reference, selected=converted[0],
                                candidates=converted, reason="Complete spatial-relation candidate set")

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
        """Normalize a private working reference, preserving raw model output."""
        from copy import copy

        index = self._get_index()
        working = copy(reference)
        if reference.label:
            from semantic_labels import normalize_reference_label
            working.label = normalize_reference_label(reference.label,
                self.get_label_ontology(), reference.node_type, reference.text)
            if working.label is None:
                return ResolutionResult(status="not_found", reference=reference,
                    reason="Unknown, ambiguous or text-contradictory label")
        explicit = self._explicit_reference(reference.text)
        if explicit:
            node_id, kind = explicit
            if kind and reference.node_type and self._normalize_type(reference.node_type) != kind:
                return ResolutionResult(status="not_found", reference=reference,
                                        reason="Explicit ID type conflicts with declared node_type")
            working.text = node_id
            working.node_type = reference.node_type or kind
            if index.get_node(node_id) is None:
                return ResolutionResult(status="not_found", reference=reference,
                                        reason=f"Node {node_id} does not exist")
        elif not reference.label:
            # Without a model label, treat text as an ontology category. Partial
            # resolver aliases must not turn unknown classes into known ones.
            working.label = reference.text
            if not self._is_type_label(working):
                canonical = self._canonical_label(reference.text, index)
                if not index.labels.get(canonical.lower()):
                    # Articles are surface grammar, not semantic class aliases.
                    # Keep exact ontology matches first and the raw ref intact.
                    import re
                    surface = re.sub(r'^\s*(?:the|a|an)\s+', '', reference.text,
                                     flags=re.IGNORECASE).strip()
                    canonical = self._canonical_label(surface, index)
                if not index.labels.get(canonical.lower()):
                    return ResolutionResult(status="not_found", reference=reference,
                                            reason="Text has no unique canonical label")
                working.label = canonical
        result = self._resolve_entity(working, top_k)
        for candidate in [*result.candidates, *([result.selected] if result.selected else [])]:
            if any(r.relation == "near" for r in getattr(reference, "relations", [])):
                tag = "near:radius3m:raw3d:min_anchor"
                if tag not in candidate.matched_constraints:
                    candidate.matched_constraints.append(tag)
        result.reference = reference
        return result

    def _resolve_entity(
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
        - label: unique ontology spelling match, or an explicit type generic
        """
        index = self._get_index()

        # Fast path 1: Exact full node ID
        import re
        if reference.text and re.fullmatch(r'[0-9]{1,20}', reference.text):
            node = index.get_node(reference.text)
            if node:
                candidates = [self._make_candidate(node, 1.0, ["exact_id"])]
                # Apply location filter
                candidates = self._apply_hard_filters(reference, candidates, index)
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
            return ResolutionResult(
                status="not_found",
                reference=reference,
                reason=f"Node {reference.text} does not exist",
            )

        # Fast path 2: quantifier=nearest with position → spatial
        quantifier = getattr(reference, "quantifier", "one")
        if quantifier in ("nearest", "farthest"):
            return self._resolve_nearest_by_position(reference, index)

        if any(r.relation in ("near", "nearest_to", "farthest_from")
               for r in getattr(reference, "relations", [])):
            return self._resolve_ranked_relations(reference, index)

        # Fast path 3: quantifier=all → canonical index by label/type
        if quantifier == "all":
            return self._resolve_all_by_label(reference, index)

        # Standard path: Semantic EntityResolver with hard filters
        resolver = self._get_resolver()
        base_candidates = resolver.resolve(
            reference.text,
            top_k=max(top_k * 10, 200),
        )
        # Supply normalized labels to candidate matching as well as hard filters.
        # Keep the raw reference (including location/relation constraints) intact.
        if reference.label:
            query_label = (reference.node_type if self._is_type_label(reference)
                           else self._canonical_label(reference.label, index))
            if query_label != reference.text:
                extra = resolver.resolve(query_label, top_k=max(top_k * 10, 200))
                by_id = {candidate["node_id"]: candidate for candidate in base_candidates}
                for candidate in extra:
                    previous = by_id.get(candidate["node_id"])
                    if previous is None or candidate.get("score", 0) > previous.get("score", 0):
                        by_id[candidate["node_id"]] = candidate
                base_candidates = list(by_id.values())

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

        candidates = self._apply_hard_filters(reference, candidates, index)

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

    def search_by_type(self, node_type: str, top_k: int | None = 10) -> list[SceneEntity]:
        """Search for nodes by type.

        Args:
            node_type: Node type to search for.
            top_k: Maximum results.

        Returns:
            List of matching SceneEntity objects.
        """
        index = self._get_index()
        return index.get_nodes_by_type(node_type)[:top_k]
