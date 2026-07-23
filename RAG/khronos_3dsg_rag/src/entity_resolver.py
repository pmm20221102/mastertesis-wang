"""Entity Resolver for Khronos 3DSG RAG.

Resolves natural language entity references to specific node IDs in the scene graph.

Flow:
    User Query
        |
    Entity Extraction
        |
    Candidate Node Search
        |
    Node Ranking
        |
    Resolved Node ID(s)
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Alias mapping from config
ALIASES: dict[str, list[str]] = {
    "chair": ["seat", "chairs"],
    "table": ["desk", "tables", "desks"],
    "door": ["entrance", "exit", "gate", "doors"],
    "room": ["area", "space", "rooms"],
    "person": ["human", "agent", "people"],
    "shelf": ["shelving", "rack", "shelves"],
    "wall": ["partition", "walls"],
    "floor": ["ground", "level"],
    "corridor": ["hallway", "passage"],
    "sign": ["signs", "label"],
    "light": ["lighting", "lamp"],
}

# Reverse alias: canonical -> aliases
REVERSE_ALIASES: dict[str, list[str]] = {}
for canonical, aliases in ALIASES.items():
    for alias in aliases:
        REVERSE_ALIASES[alias.lower()] = canonical.lower()
    REVERSE_ALIASES[canonical.lower()] = canonical.lower()


class NodeRecord:
    """Canonical record for a scene graph node, aggregating all chunks."""
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.labels: set[str] = set()
        self.names: set[str] = set()
        self.object_symbols: set[str] = set()
        self.node_types: set[str] = set()
        self.parent_ids: set[str] = set()
        self.child_ids: set[str] = set()
        self.neighbor_ids: set[str] = set()
        self.position: list[float] = []
        self.bounding_box_center: list[float] = []
        self.room: str = ""
        self.chunk_types: set[str] = set()

    def to_dict(self) -> dict:
        """Convert to dictionary for compatibility."""
        return {
            "node_id": self.node_id,
            "label": next(iter(self.labels), ""),
            "labels": list(self.labels),
            "name": next(iter(self.names), ""),
            "names": list(self.names),
            "object_symbol": next(iter(self.object_symbols), ""),
            "object_symbols": list(self.object_symbols),
            "node_type": next(iter(self.node_types), ""),
            "node_types": list(self.node_types),
            "parent_ids": list(self.parent_ids),
            "child_ids": list(self.child_ids),
            "neighbor_ids": list(self.neighbor_ids),
            "position": self.position,
            "bounding_box_center": self.bounding_box_center,
            "room": self.room,
        }


class EntityResolver:
    """Resolves natural language entity references to scene graph node IDs."""

    def __init__(self, db_dir: str | Path):
        self.db_dir = Path(db_dir)
        self._index_cache: dict[str, dict] | None = None
        self._node_records: dict[str, NodeRecord] | None = None
        self._label_index: dict[str, list[str]] | None = None
        self._type_index: dict[str, list[str]] | None = None

    def _load_index(self) -> dict[str, dict]:
        """Load all node metadata from ChromaDB with node-level aggregation."""
        if self._index_cache is not None:
            return self._index_cache

        import chromadb
        from chromadb.config import Settings

        client = chromadb.PersistentClient(
            path=str(self.db_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        collection = client.get_collection("scene_graph")

        all_meta = collection.get(include=["metadatas"])
        self._node_records = {}

        for meta in all_meta.get("metadatas", []):
            nid = meta.get("node_id", "")
            if not nid:
                continue

            # Parse JSON fields
            for field in ["parent_ids", "child_ids", "neighbor_ids"]:
                val = meta.get(field, [])
                if isinstance(val, str):
                    try:
                        meta[field] = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        meta[field] = []
                elif not isinstance(val, list):
                    meta[field] = []

            # Get or create node record
            if nid not in self._node_records:
                self._node_records[nid] = NodeRecord(nid)

            record = self._node_records[nid]

            # Merge metadata from this chunk
            if meta.get("label"):
                record.labels.add(meta["label"])
            if meta.get("name"):
                record.names.add(meta["name"])
            if meta.get("object_symbol"):
                record.object_symbols.add(meta["object_symbol"])
            if meta.get("node_type"):
                record.node_types.add(meta["node_type"])
            if meta.get("room"):
                record.room = meta["room"]
            if meta.get("chunk_type"):
                record.chunk_types.add(meta["chunk_type"])

            # Merge graph relationships
            record.parent_ids.update(meta.get("parent_ids", []))
            record.child_ids.update(meta.get("child_ids", []))
            record.neighbor_ids.update(meta.get("neighbor_ids", []))

            # Merge position (take first non-empty)
            if not record.position and meta.get("position"):
                record.position = meta["position"]
            if not record.bounding_box_center and meta.get("bounding_box_center"):
                bb = meta["bounding_box_center"]
                if isinstance(bb, str):
                    try:
                        bb = json.loads(bb)
                    except (json.JSONDecodeError, TypeError):
                        bb = []
                if isinstance(bb, list) and len(bb) >= 2:
                    record.bounding_box_center = bb

        # Convert to dict for compatibility
        self._index_cache = {nid: record.to_dict() for nid, record in self._node_records.items()}

        logger.info(f"Loaded {len(self._index_cache)} nodes for entity resolution")
        return self._index_cache

    def _build_label_index(self) -> dict[str, list[str]]:
        """Build label -> node_ids index."""
        if self._label_index is not None:
            return self._label_index

        index = self._load_index()
        self._label_index = defaultdict(list)

        for nid, meta in index.items():
            label = meta.get("label", "").lower()
            if label:
                self._label_index[label].append(nid)

            # Also index the node_type
            node_type = meta.get("node_type", "").lower().rstrip("s")
            if node_type:
                self._label_index[node_type].append(nid)

        return self._label_index

    def _build_type_index(self) -> dict[str, list[str]]:
        """Build node_type -> node_ids index."""
        if self._type_index is not None:
            return self._type_index

        index = self._load_index()
        self._type_index = defaultdict(list)

        for nid, meta in index.items():
            node_type = meta.get("node_type", "")
            if node_type:
                self._type_index[node_type].append(nid)

        return self._type_index

    def _resolve_exact_node_id(self, query: str) -> str | None:
        """Extract exact node ID from query (12-20 digit number)."""
        # Pattern for numeric node IDs (12-20 digits)
        pattern = r"\b(\d{12,20})\b"
        matches = re.findall(pattern, query)
        if matches:
            index = self._load_index()
            for nid in matches:
                if nid in index:
                    return nid
        return None

    def _resolve_exact_object_symbol(self, query: str) -> str | None:
        """Extract exact object symbol from query (e.g., O(370))."""
        # Pattern for object symbols like O(370)
        pattern = r"\b([A-Z]\(\d+\))\b"
        matches = re.findall(pattern, query)
        if matches:
            index = self._load_index()
            for symbol in matches:
                for nid, meta in index.items():
                    if meta.get("object_symbol") == symbol:
                        return nid
        return None

    def _extract_entity_terms(self, query: str) -> list[str]:
        """Extract potential entity terms from a query."""
        # Common patterns to extract entity references
        patterns = [
            r"(?:where is|located|parent of|which room contains|find the location of)\s+(.+?)(?:\s+located)?(?:\s*\?)?$",
            r"(?:what is inside|contains|list the contents of)\s+(.+?)(?:\s*\?)?$",
            r"(?:what is connected to|neighbor of|adjacent to)\s+(.+?)(?:\s*\?)?$",
            r"(?:find all|list all|which nodes are)\s+(.+?)(?:s)?(?:\s+in|\s*\?)?$",
        ]

        terms = []
        query_lower = query.lower().strip()

        for pattern in patterns:
            match = re.search(pattern, query_lower)
            if match:
                term = match.group(1).strip()
                if term:
                    terms.append(term)

        # Also try to extract entity-like terms (capitalized words, known types)
        words = query_lower.split()
        entity_keywords = [
            "chair", "table", "desk", "door", "room", "person", "shelf",
            "wall", "floor", "corridor", "sign", "light", "bedroom",
            "bathroom", "kitchen", "office", "building", "object",
            "small_office_objects", "large_office_objects", "large_static_wall_furniture",
        ]

        for word in words:
            if word in entity_keywords or word.rstrip("s") in entity_keywords:
                if word not in terms:
                    terms.append(word)

        return terms

    def _normalize_term(self, term: str) -> str:
        """Normalize a term using aliases."""
        term_lower = term.lower().strip()

        # Check direct alias match
        if term_lower in REVERSE_ALIASES:
            return REVERSE_ALIASES[term_lower]

        # Check singular form
        if term_lower.endswith("s"):
            singular = term_lower[:-1]
            if singular in REVERSE_ALIASES:
                return REVERSE_ALIASES[singular]

        return term_lower

    def _compute_match_score(
        self,
        query_term: str,
        node_id: str,
        meta: dict,
    ) -> float:
        """Compute a match score between query term and node metadata."""
        score = 0.0
        normalized_term = self._normalize_term(query_term)

        # Label match (highest weight)
        label = meta.get("label", "").lower()
        if label:
            if normalized_term == label:
                score += 1.0
            elif normalized_term in label or label in normalized_term:
                score += 0.7
            elif self._normalize_term(label) == normalized_term:
                score += 0.9

        # Node type match
        node_type = meta.get("node_type", "").lower().rstrip("s")
        if node_type and normalized_term == node_type:
            score += 0.8

        # Check aliases
        if normalized_term in ALIASES:
            for alias in ALIASES[normalized_term]:
                if alias.lower() in label:
                    score += 0.6
                    break

        return min(score, 1.0)

    def resolve(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.1,
    ) -> list[dict[str, Any]]:
        """Resolve entity references in a query to node IDs.

        Priority:
        1. Exact node ID
        2. Exact object symbol
        3. Label/alias matching

        Args:
            query: Natural language query.
            top_k: Maximum number of candidates to return.
            min_score: Minimum score threshold.

        Returns:
            List of resolved candidates with scores.
        """
        index = self._load_index()

        # Priority 1: Exact node ID
        exact_nid = self._resolve_exact_node_id(query)
        if exact_nid:
            logger.info(f"Resolved exact node ID: {exact_nid}")
            meta = index.get(exact_nid, {})
            return [{
                "node_id": exact_nid,
                "score": 1.0,
                "label": meta.get("label", ""),
                "node_type": meta.get("node_type", ""),
                "parent_ids": meta.get("parent_ids", []),
                "child_ids": meta.get("child_ids", []),
                "neighbor_ids": meta.get("neighbor_ids", []),
            }]

        # Priority 2: Exact object symbol
        exact_symbol_nid = self._resolve_exact_object_symbol(query)
        if exact_symbol_nid:
            logger.info(f"Resolved exact object symbol: {exact_symbol_nid}")
            meta = index.get(exact_symbol_nid, {})
            return [{
                "node_id": exact_symbol_nid,
                "score": 0.95,
                "label": meta.get("label", ""),
                "node_type": meta.get("node_type", ""),
                "parent_ids": meta.get("parent_ids", []),
                "child_ids": meta.get("child_ids", []),
                "neighbor_ids": meta.get("neighbor_ids", []),
            }]

        # Priority 3: Label/alias matching
        label_index = self._build_label_index()

        # Extract entity terms from query
        terms = self._extract_entity_terms(query)
        if not terms:
            # Fallback: use entire query as search term
            terms = [query]

        logger.info(f"Extracted terms: {terms}")

        # Score candidates
        candidate_scores: dict[str, float] = defaultdict(float)

        for term in terms:
            # Direct label match
            normalized = self._normalize_term(term)
            if normalized in label_index:
                for nid in label_index[normalized]:
                    candidate_scores[nid] += 0.8

            # Partial match
            for label, nids in label_index.items():
                if term in label or label in term:
                    for nid in nids:
                        candidate_scores[nid] += 0.5

            # Search all nodes for semantic match
            for nid, meta in index.items():
                score = self._compute_match_score(term, nid, meta)
                if score > 0:
                    candidate_scores[nid] += score

        # Sort by score
        candidates = sorted(
            candidate_scores.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        # Build result
        results = []
        seen_ids = set()
        for nid, score in candidates:
            if len(results) >= top_k:
                break
            if nid in seen_ids:
                continue
            if score < min_score:
                continue

            meta = index.get(nid, {})

            # Check for ambiguity: if multiple candidates have similar scores
            is_ambiguous = False
            if results and len(results) >= 2:
                top_score = results[0]["score"]
                if score >= top_score * 0.9:  # Within 90% of top score
                    is_ambiguous = True

            results.append({
                "node_id": nid,
                "score": round(score / len(terms), 4),  # Average score
                "is_ambiguous": is_ambiguous,
                "label": meta.get("label", ""),
                "node_type": meta.get("node_type", ""),
                "parent_ids": meta.get("parent_ids", []),
                "child_ids": meta.get("child_ids", []),
                "neighbor_ids": meta.get("neighbor_ids", []),
            })
            seen_ids.add(nid)

        return results

    def resolve_single(self, query: str) -> str | None:
        """Resolve to a single best-matching node ID.

        Args:
            query: Natural language query.

        Returns:
            Best matching node ID, or None if no match.
        """
        results = self.resolve(query, top_k=1, min_score=0.1)
        if results:
            return results[0]["node_id"]
        return None

    def resolve_to_parents(
        self,
        query: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Resolve query and return parent nodes.

        This is the main entry point for reverse_hierarchy queries.

        Args:
            query: Natural language query (e.g., "Where is chair?").
            top_k: Maximum number of parent candidates.

        Returns:
            List of parent node candidates with scores.
        """
        # Step 1: Resolve entity (get more candidates)
        entities = self.resolve(query, top_k=10)
        if not entities:
            return []

        # Step 2: Collect parent IDs
        parent_scores: dict[str, float] = defaultdict(float)
        for entity in entities:
            entity_score = entity["score"]
            for pid in entity.get("parent_ids", []):
                parent_scores[pid] += entity_score

        # Step 3: Sort and return
        sorted_parents = sorted(
            parent_scores.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        index = self._load_index()
        results = []
        for pid, score in sorted_parents[:top_k]:
            meta = index.get(pid, {})
            results.append({
                "node_id": pid,
                "score": round(score / len(entities), 4),
                "label": meta.get("label", ""),
                "node_type": meta.get("node_type", ""),
            })

        return results


def resolve_entity(
    query: str,
    db_dir: str | Path,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Convenience function to resolve entities from a query.

    Args:
        query: Natural language query.
        db_dir: Path to vector DB directory.
        top_k: Maximum candidates.

    Returns:
        List of resolved candidates.
    """
    resolver = EntityResolver(db_dir)
    return resolver.resolve(query, top_k=top_k)


def resolve_to_parents(
    query: str,
    db_dir: str | Path,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Convenience function to resolve query to parent nodes.

    Args:
        query: Natural language query.
        db_dir: Path to vector DB directory.
        top_k: Maximum parent candidates.

    Returns:
        List of parent node candidates.
    """
    resolver = EntityResolver(db_dir)
    return resolver.resolve_to_parents(query, top_k=top_k)
