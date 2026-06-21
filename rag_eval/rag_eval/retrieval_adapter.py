"""Graph-Aware RAG Retrieval Adapter.

Retrieval strategies:
1. Vector search - semantic similarity (default)
2. Graph expansion - traverse parent/child/neighbor after vector search
3. Spatial search - bbox distance calculation
4. Metadata filter - filter by node_type/chunk_type
5. Hybrid - combine strategies based on query type
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from pathlib import Path
from typing import Any

RAG_SRC = Path(__file__).parent.parent.parent / "RAG" / "khronos_3dsg_rag" / "src"
sys.path.insert(0, str(RAG_SRC))

from query_rag import query_rag
from .schemas import GroundTruthObject, RetrievedChunk

logger = logging.getLogger(__name__)


def _position_distance(pos1: list[float], pos2: list[float]) -> float:
    if not pos1 or not pos2:
        return float("inf")
    p1 = pos1[:3] if len(pos1) >= 3 else pos1 + [0] * (3 - len(pos1))
    p2 = pos2[:3] if len(pos2) >= 3 else pos2 + [0] * (3 - len(pos2))
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(p1, p2)))


def _parse_position_from_query(query: str) -> list[float] | None:
    x_match = re.search(r'x[=:\s]+(-?[\d.]+)', query)
    y_match = re.search(r'y[=:\s]+(-?[\d.]+)', query)
    z_match = re.search(r'z[=:\s]+(-?[\d.]+)', query)
    if x_match and y_match:
        x = float(x_match.group(1))
        y = float(y_match.group(1))
        z = float(z_match.group(1)) if z_match else 0.0
        return [x, y, z]
    return None


def _detect_query_type(query: str) -> str:
    q = query.lower()
    if any(kw in q for kw in ["inside", "contains", "what is in", "what's in", "list the contents", "show me everything in", "show me all in"]):
        return "hierarchy"
    if any(kw in q for kw in ["where is", "located", "parent of", "which room contains", "find the location"]):
        return "reverse_hierarchy"
    if any(kw in q for kw in ["connected to", "neighbor", "adjacent", "near", "what is connected"]):
        return "neighbor"
    if any(kw in q for kw in ["find all", "list all", "which nodes are"]):
        return "type_filter"
    if re.search(r'x[=:\s]+-?[\d.]+', q):
        return "spatial"
    return "semantic"


class GraphIndex:
    def __init__(self):
        self.parents: dict[str, list[str]] = {}
        self.children: dict[str, list[str]] = {}
        self.neighbors: dict[str, list[str]] = {}
        self.node_types: dict[str, str] = {}
        self.materialized: dict[str, list[str]] = {}
        self.positions: dict[str, list[float]] = {}

    def build_from_metadata(self, all_metadata: list[dict]) -> None:
        for meta in all_metadata:
            nid = meta.get("node_id", "")
            if not nid:
                continue

            self.node_types[nid] = meta.get("node_type", "")

            for field, store in [("parent_ids", self.parents), ("child_ids", self.children), ("neighbor_ids", self.neighbors)]:
                val = meta.get(field, [])
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        val = []
                store[nid] = val

            bb = meta.get("bounding_box_center", [])
            if isinstance(bb, str):
                try:
                    bb = json.loads(bb)
                except (json.JSONDecodeError, TypeError):
                    bb = []
            if isinstance(bb, list) and len(bb) >= 2:
                self.positions[nid] = bb

            if meta.get("chunk_type") == "materialized_relation":
                key = meta.get("key", "")
                node_ids = meta.get("node_ids", [])
                if isinstance(node_ids, str):
                    try:
                        node_ids = json.loads(node_ids)
                    except (json.JSONDecodeError, TypeError):
                        node_ids = []
                self.materialized[key] = node_ids

        logger.info(f"Graph index: {len(self.parents)} nodes, {len(self.positions)} with positions, {len(self.materialized)} materialized")

    def get_children(self, node_id: str) -> list[str]:
        return self.children.get(node_id, [])

    def get_parents(self, node_id: str) -> list[str]:
        return self.parents.get(node_id, [])

    def get_neighbors(self, node_id: str) -> list[str]:
        return self.neighbors.get(node_id, [])

    def get_position(self, node_id: str) -> list[float]:
        return self.positions.get(node_id, [])


class RetrievalAdapter:
    def __init__(self, db_dir: str):
        self.db_dir = db_dir
        self._index_cache: dict[str, dict] | None = None
        self._graph_index: GraphIndex | None = None

    def search(self, query: str, top_k: int = 10) -> list[RetrievedChunk]:
        try:
            raw_results = query_rag(query, self.db_dir, top_k=top_k)
        except Exception as e:
            logger.warning(f"RAG query failed: {e}")
            return []

        index_meta = self._load_index_metadata()
        chunks = []
        for r in raw_results:
            node_id = r.get("node_id", "")
            meta = index_meta.get(node_id, r.get("metadata", {}))
            chunks.append(RetrievedChunk(
                rank=r.get("rank", 0),
                score=r.get("score", 0.0),
                node_id=node_id,
                object_symbol=meta.get("object_symbol", ""),
                metadata=meta,
                text=r.get("text", ""),
            ))
        return chunks

    def search_with_graph_expansion(
        self,
        query: str,
        top_k: int = 10,
        expand_children: bool = False,
        expand_parents: bool = False,
        expand_neighbors: bool = False,
        max_expand: int = 5,
    ) -> list[RetrievedChunk]:
        seed_chunks = self.search(query, top_k=top_k)
        if not seed_chunks:
            return []

        graph_idx = self._get_graph_index()
        seen_ids = {c.node_id for c in seed_chunks}
        expanded = list(seed_chunks)

        for chunk in seed_chunks[:max_expand]:
            nid = chunk.node_id

            if expand_children:
                for child_id in graph_idx.get_children(nid):
                    if child_id not in seen_ids:
                        child_chunk = self._get_chunk_by_id(child_id)
                        if child_chunk:
                            child_chunk.rank = len(expanded) + 1
                            child_chunk.score = chunk.score * 0.8
                            expanded.append(child_chunk)
                            seen_ids.add(child_id)

            if expand_parents:
                for parent_id in graph_idx.get_parents(nid):
                    if parent_id not in seen_ids:
                        parent_chunk = self._get_chunk_by_id(parent_id)
                        if parent_chunk:
                            parent_chunk.rank = len(expanded) + 1
                            parent_chunk.score = chunk.score * 0.9
                            expanded.append(parent_chunk)
                            seen_ids.add(parent_id)

            if expand_neighbors:
                for neighbor_id in graph_idx.get_neighbors(nid):
                    if neighbor_id not in seen_ids:
                        neighbor_chunk = self._get_chunk_by_id(neighbor_id)
                        if neighbor_chunk:
                            neighbor_chunk.rank = len(expanded) + 1
                            neighbor_chunk.score = chunk.score * 0.7
                            expanded.append(neighbor_chunk)
                            seen_ids.add(neighbor_id)

        expanded.sort(key=lambda c: c.rank)
        return expanded[:top_k * 2]

    def search_spatial(self, query: str, top_k: int = 10) -> list[RetrievedChunk]:
        position = _parse_position_from_query(query)
        if not position:
            return self.search(query, top_k=top_k)

        graph_idx = self._get_graph_index()
        index_meta = self._load_index_metadata()

        scored: list[tuple[float, str]] = []
        for nid, pos in graph_idx.positions.items():
            dist = _position_distance(position, pos)
            scored.append((dist, nid))

        scored.sort(key=lambda x: x[0])

        chunks = []
        for dist, nid in scored[:top_k]:
            meta = index_meta.get(nid, {})
            chunks.append(RetrievedChunk(
                rank=len(chunks) + 1,
                score=1.0 / (1.0 + dist),
                node_id=nid,
                object_symbol=meta.get("object_symbol", ""),
                metadata=meta,
                text=f"{meta.get('node_type', 'entity')} {nid} at distance {dist:.2f}m.",
            ))
        return chunks

    def search_hierarchical(self, query: str, top_k: int = 10) -> list[RetrievedChunk]:
        """Search for 'what is inside X?' queries - return children of matched containers."""
        graph_idx = self._get_graph_index()
        index_meta = self._load_index_metadata()
        result_chunks = []
        seen_ids = set()

        # Try to find container node from query
        for nid, node_type in graph_idx.node_types.items():
            if node_type in ("room", "rooms", "place", "places", "building", "buildings"):
                if nid in query or str(nid) in query:
                    children = graph_idx.get_children(nid)
                    for child_id in children[:top_k]:
                        if child_id not in seen_ids:
                            meta = index_meta.get(child_id, {})
                            child_chunk = RetrievedChunk(
                                rank=len(result_chunks) + 1,
                                score=1.0,
                                node_id=child_id,
                                object_symbol=meta.get("object_symbol", ""),
                                metadata=meta,
                                text=f"{meta.get('node_type', 'entity')} {child_id}.",
                            )
                            result_chunks.append(child_chunk)
                            seen_ids.add(child_id)
                    if result_chunks:
                        return result_chunks[:top_k]

        # Fallback: use materialized relations
        seed_chunks = self.search(query, top_k=min(top_k * 2, 20))
        for chunk in seed_chunks:
            if chunk.metadata.get("chunk_type") == "materialized_relation":
                node_ids = chunk.metadata.get("node_ids", [])
                if isinstance(node_ids, str):
                    try:
                        node_ids = json.loads(node_ids)
                    except (json.JSONDecodeError, TypeError):
                        node_ids = []
                for child_id in node_ids[:top_k]:
                    if child_id not in seen_ids:
                        meta = index_meta.get(child_id, {})
                        child_chunk = RetrievedChunk(
                            rank=len(result_chunks) + 1,
                            score=0.8,
                            node_id=child_id,
                            object_symbol=meta.get("object_symbol", ""),
                            metadata=meta,
                            text=f"{meta.get('node_type', 'entity')} {child_id}.",
                        )
                        result_chunks.append(child_chunk)
                        seen_ids.add(child_id)

        return result_chunks[:top_k]

    def search_reverse_hierarchical(self, query: str, top_k: int = 10) -> list[RetrievedChunk]:
        """Search for 'where is X?' queries - return parents of matched objects."""
        graph_idx = self._get_graph_index()
        index_meta = self._load_index_metadata()
        result_chunks = []
        seen_ids = set()

        # Try to find node from query by ID
        for nid in list(graph_idx.parents.keys()):
            if nid in query or str(nid) in query:
                parents = graph_idx.get_parents(nid)
                for parent_id in parents[:3]:
                    if parent_id not in seen_ids:
                        meta = index_meta.get(parent_id, {})
                        parent_chunk = RetrievedChunk(
                            rank=len(result_chunks) + 1,
                            score=0.9,
                            node_id=parent_id,
                            object_symbol=meta.get("object_symbol", ""),
                            metadata=meta,
                            text=f"{meta.get('node_type', 'entity')} {parent_id}.",
                        )
                        result_chunks.append(parent_chunk)
                        seen_ids.add(parent_id)
                if result_chunks:
                    return result_chunks[:top_k]

        # Try to find node by label/name
        query_lower = query.lower()
        label_matches = []
        for nid, meta in list(index_meta.items()):
            label = meta.get("label", "")
            if label and label.lower() in query_lower:
                label_matches.append((nid, meta))

        # Return parents of all matching nodes
        for nid, meta in label_matches:
            parents = graph_idx.get_parents(nid)
            for parent_id in parents[:3]:
                if parent_id not in seen_ids:
                    parent_meta = index_meta.get(parent_id, {})
                    parent_chunk = RetrievedChunk(
                        rank=len(result_chunks) + 1,
                        score=0.8,
                        node_id=parent_id,
                        object_symbol=parent_meta.get("object_symbol", ""),
                        metadata=parent_meta,
                        text=f"{parent_meta.get('node_type', 'entity')} {parent_id}.",
                    )
                    result_chunks.append(parent_chunk)
                    seen_ids.add(parent_id)

        if result_chunks:
            return result_chunks[:top_k]

        # Fallback: use vector search + parent expansion
        seed_chunks = self.search(query, top_k=min(top_k * 2, 20))
        for chunk in seed_chunks:
            nid = chunk.node_id
            if nid.startswith("rel_"):
                parts = nid[4:].split("_", 1)
                if len(parts) == 2:
                    src_id, tgt_id = parts
                    for related_id in [src_id, tgt_id]:
                        parents = graph_idx.get_parents(related_id)
                        for parent_id in parents[:3]:
                            if parent_id not in seen_ids:
                                meta = index_meta.get(parent_id, {})
                                parent_chunk = RetrievedChunk(
                                    rank=len(result_chunks) + 1,
                                    score=0.7,
                                    node_id=parent_id,
                                    object_symbol=meta.get("object_symbol", ""),
                                    metadata=meta,
                                    text=f"{meta.get('node_type', 'entity')} {parent_id}.",
                                )
                                result_chunks.append(parent_chunk)
                                seen_ids.add(parent_id)
            else:
                parents = graph_idx.get_parents(nid)
                if parents:
                    for parent_id in parents[:3]:
                        if parent_id not in seen_ids:
                            meta = index_meta.get(parent_id, {})
                            parent_chunk = RetrievedChunk(
                                rank=len(result_chunks) + 1,
                                score=0.7,
                                node_id=parent_id,
                                object_symbol=meta.get("object_symbol", ""),
                                metadata=meta,
                                text=f"{meta.get('node_type', 'entity')} {parent_id}.",
                            )
                            result_chunks.append(parent_chunk)
                            seen_ids.add(parent_id)

        return result_chunks[:top_k]

    def search_neighbor(self, query: str, top_k: int = 10) -> list[RetrievedChunk]:
        """Search for 'what is connected to X?' queries - return neighbors."""
        graph_idx = self._get_graph_index()
        index_meta = self._load_index_metadata()
        result_chunks = []
        seen_ids = set()

        # Try to find node from query
        for nid in graph_idx.neighbors:
            if nid in query or str(nid) in query:
                neighbors = graph_idx.get_neighbors(nid)
                for neighbor_id in neighbors[:top_k]:
                    if neighbor_id not in seen_ids:
                        meta = index_meta.get(neighbor_id, {})
                        neighbor_chunk = RetrievedChunk(
                            rank=len(result_chunks) + 1,
                            score=0.8,
                            node_id=neighbor_id,
                            object_symbol=meta.get("object_symbol", ""),
                            metadata=meta,
                            text=f"{meta.get('node_type', 'entity')} {neighbor_id}.",
                        )
                        result_chunks.append(neighbor_chunk)
                        seen_ids.add(neighbor_id)
                if result_chunks:
                    return result_chunks[:top_k]

        # Fallback: use relation chunks
        seed_chunks = self.search(query, top_k=min(top_k * 2, 20))
        for chunk in seed_chunks:
            nid = chunk.node_id
            if nid.startswith("rel_"):
                parts = nid[4:].split("_", 1)
                if len(parts) == 2:
                    src_id, tgt_id = parts
                    for related_id in [src_id, tgt_id]:
                        neighbors = graph_idx.get_neighbors(related_id)
                        for neighbor_id in neighbors[:top_k]:
                            if neighbor_id not in seen_ids:
                                meta = index_meta.get(neighbor_id, {})
                                neighbor_chunk = RetrievedChunk(
                                    rank=len(result_chunks) + 1,
                                    score=0.7,
                                    node_id=neighbor_id,
                                    object_symbol=meta.get("object_symbol", ""),
                                    metadata=meta,
                                    text=f"{meta.get('node_type', 'entity')} {neighbor_id}.",
                                )
                                result_chunks.append(neighbor_chunk)
                                seen_ids.add(neighbor_id)

        return result_chunks[:top_k]

    def search_reverse_hierarchical(self, query: str, top_k: int = 10) -> list[RetrievedChunk]:
        """Search for 'where is X?' queries - return parents of matched objects."""
        seed_chunks = self.search(query, top_k=min(top_k * 2, 20))
        if not seed_chunks:
            return []

        graph_idx = self._get_graph_index()
        index_meta = self._load_index_metadata()
        result_chunks = []
        seen_ids = set()

        for chunk in seed_chunks:
            nid = chunk.node_id
            parents = graph_idx.get_parents(nid)

            if parents:
                for parent_id in parents[:3]:
                    if parent_id not in seen_ids:
                        meta = index_meta.get(parent_id, {})
                        parent_chunk = RetrievedChunk(
                            rank=len(result_chunks) + 1,
                            score=chunk.score * 0.9,
                            node_id=parent_id,
                            object_symbol=meta.get("object_symbol", ""),
                            metadata=meta,
                            text=f"{meta.get('node_type', 'entity')} {parent_id}.",
                        )
                        result_chunks.append(parent_chunk)
                        seen_ids.add(parent_id)
            else:
                if nid not in seen_ids and chunk.metadata.get("node_type") != "alias":
                    result_chunks.append(chunk)
                    seen_ids.add(nid)

        return result_chunks[:top_k]

    def search_by_type(self, query: str, top_k: int = 10) -> list[RetrievedChunk]:
        """Search for 'find all X' queries - filter by node_type."""
        target_type = ""
        q = query.lower()
        if "room" in q:
            target_type = "room"
        elif "place" in q:
            target_type = "place"
        elif "object" in q:
            target_type = "object"
        elif "agent" in q:
            target_type = "agent"
        elif "building" in q:
            target_type = "building"

        if not target_type:
            return self.search(query, top_k=top_k)

        index_meta = self._load_index_metadata()
        chunks = []
        for nid, meta in index_meta.items():
            node_type = meta.get("node_type", "")
            if node_type == target_type or target_type in node_type:
                chunks.append(RetrievedChunk(
                    rank=len(chunks) + 1,
                    score=1.0,
                    node_id=nid,
                    object_symbol=meta.get("object_symbol", ""),
                    metadata=meta,
                    text=f"{node_type} {nid}.",
                ))

        return chunks[:top_k]

    def search_with_strategy(self, query: str, top_k: int = 10) -> list[RetrievedChunk]:
        query_type = _detect_query_type(query)

        if query_type == "spatial":
            return self.search_spatial(query, top_k)

        if query_type == "hierarchy":
            return self.search_hierarchical(query, top_k)

        if query_type == "reverse_hierarchy":
            return self.search_reverse_hierarchical(query, top_k)

        if query_type == "neighbor":
            return self.search_neighbor(query, top_k)

        if query_type == "type_filter":
            return self.search_by_type(query, top_k)

        return self.search(query, top_k)

    def search_neighbor(self, query: str, top_k: int = 10) -> list[RetrievedChunk]:
        """Search for 'what is connected to X?' queries - return neighbors."""
        seed_chunks = self.search(query, top_k=min(top_k * 2, 20))
        if not seed_chunks:
            return []

        graph_idx = self._get_graph_index()
        index_meta = self._load_index_metadata()
        result_chunks = []
        seen_ids = set()

        for chunk in seed_chunks:
            nid = chunk.node_id

            if nid.startswith("rel_"):
                parts = nid[4:].split("_", 1)
                if len(parts) == 2:
                    src_id, tgt_id = parts
                    for related_id in [src_id, tgt_id]:
                        if related_id not in seen_ids:
                            neighbors = graph_idx.get_neighbors(related_id)
                            for neighbor_id in neighbors[:top_k]:
                                if neighbor_id not in seen_ids:
                                    meta = index_meta.get(neighbor_id, {})
                                    neighbor_chunk = RetrievedChunk(
                                        rank=len(result_chunks) + 1,
                                        score=chunk.score * 0.8,
                                        node_id=neighbor_id,
                                        object_symbol=meta.get("object_symbol", ""),
                                        metadata=meta,
                                        text=f"{meta.get('node_type', 'entity')} {neighbor_id}.",
                                    )
                                    result_chunks.append(neighbor_chunk)
                                    seen_ids.add(neighbor_id)
            else:
                neighbors = graph_idx.get_neighbors(nid)
                if neighbors:
                    for neighbor_id in neighbors[:top_k]:
                        if neighbor_id not in seen_ids:
                            meta = index_meta.get(neighbor_id, {})
                            neighbor_chunk = RetrievedChunk(
                                rank=len(result_chunks) + 1,
                                score=chunk.score * 0.8,
                                node_id=neighbor_id,
                                object_symbol=meta.get("object_symbol", ""),
                                metadata=meta,
                                text=f"{meta.get('node_type', 'entity')} {neighbor_id}.",
                            )
                            result_chunks.append(neighbor_chunk)
                            seen_ids.add(neighbor_id)

        return result_chunks[:top_k]

    def get_indexed_entity_ids(self) -> set[str]:
        try:
            from build_vector_db import load_vector_db
            collection = load_vector_db(self.db_dir)
            all_meta = collection.get(include=["metadatas"])
            ids = set()
            for meta in all_meta.get("metadatas", []):
                nid = meta.get("node_id", "")
                if nid:
                    ids.add(nid)
            return ids
        except Exception as e:
            logger.warning(f"Failed to load index: {e}")
            return set()

    def _get_graph_index(self) -> GraphIndex:
        if self._graph_index is not None:
            return self._graph_index

        self._graph_index = GraphIndex()
        try:
            index_meta = self._load_index_metadata()
            all_meta = list(index_meta.values())
            self._graph_index.build_from_metadata(all_meta)
        except Exception as e:
            logger.warning(f"Failed to build graph index: {e}")

        return self._graph_index

    def _load_index_metadata(self) -> dict[str, dict]:
        if self._index_cache is not None:
            return self._index_cache
        try:
            from build_vector_db import load_vector_db
            collection = load_vector_db(self.db_dir)
            result = collection.get(include=["metadatas"])
            self._index_cache = {}
            for meta in result.get("metadatas", []):
                nid = meta.get("node_id", "")
                if not nid:
                    continue

                for field in ["bounding_box_center", "child_ids", "parent_ids", "neighbor_ids", "related_nodes"]:
                    val = meta.get(field, [])
                    if isinstance(val, str):
                        try:
                            meta[field] = json.loads(val)
                        except (json.JSONDecodeError, TypeError):
                            meta[field] = [] if field != "bounding_box_center" else []

                layer = meta.get("layer")
                if layer is None:
                    meta["layer"] = 0
                else:
                    try:
                        meta["layer"] = int(layer)
                    except (ValueError, TypeError):
                        meta["layer"] = 0

                if nid in self._index_cache:
                    existing = self._index_cache[nid]
                    for field in ["child_ids", "parent_ids", "neighbor_ids"]:
                        new_vals = meta.get(field, [])
                        old_vals = existing.get(field, [])
                        if new_vals and not old_vals:
                            existing[field] = new_vals
                        elif new_vals and old_vals:
                            existing[field] = list(dict.fromkeys(old_vals + new_vals))
                else:
                    self._index_cache[nid] = meta
            return self._index_cache
        except Exception as e:
            logger.warning(f"Failed to load index metadata: {e}")
            return {}

    def _get_chunk_by_id(self, node_id: str) -> RetrievedChunk | None:
        meta = self._load_index_metadata().get(node_id)
        if not meta:
            return None
        return RetrievedChunk(
            rank=0,
            score=0.0,
            node_id=node_id,
            object_symbol=meta.get("object_symbol", ""),
            metadata=meta,
            text=f"{meta.get('node_type', 'entity')} {node_id}.",
        )

    def match_gt_to_index(self, gt_obj: GroundTruthObject, threshold: float = 5.0) -> str | None:
        index_meta = self._load_index_metadata()
        gt_pos = gt_obj.position
        if not gt_pos or len(gt_pos) < 3:
            return None

        best_id = None
        best_dist = float("inf")
        for nid, meta in index_meta.items():
            bb_center = meta.get("bounding_box_center", [])
            if not bb_center or len(bb_center) < 3:
                continue
            dist = _position_distance(gt_pos, bb_center)
            if dist < best_dist:
                best_dist = dist
                best_id = nid

        if best_dist <= threshold:
            return best_id
        return None

    def build_association_table(
        self, gt_objects: list[GroundTruthObject], threshold: float = 5.0
    ) -> dict[str, str]:
        table = {}
        for gt_obj in gt_objects:
            if not gt_obj.object_symbol:
                continue
            matched_id = self.match_gt_to_index(gt_obj, threshold)
            if matched_id:
                table[gt_obj.object_symbol] = matched_id
        return table
