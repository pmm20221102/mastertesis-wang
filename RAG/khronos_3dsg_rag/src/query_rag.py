"""Query interface for the 3DSG RAG database."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import chromadb

from build_vector_db import COLLECTION_NAME, load_vector_db

logger = logging.getLogger(__name__)


def query_rag(
    query: str,
    db_dir: str | Path,
    top_k: int = 5,
    where_filter: dict | None = None,
) -> list[dict[str, Any]]:
    """Query the RAG database and return ranked results.

    Args:
        query: Natural language query string.
        db_dir: Path to the vector DB directory.
        top_k: Number of results to return.
        where_filter: Optional ChromaDB where filter for metadata.

    Returns:
        List of result dicts with rank, score, text, metadata.
    """
    collection = load_vector_db(db_dir)

    kwargs: dict[str, Any] = {
        "query_texts": [query],
        "n_results": min(top_k, collection.count()),
    }
    if where_filter:
        kwargs["where"] = where_filter

    results = collection.query(**kwargs)

    # Flatten ChromaDB batch results (always batch=1 here)
    ids = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0]
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    output = []
    for i, doc_id in enumerate(ids):
        meta = metadatas[i] if i < len(metadatas) else {}

        # Convert distance to similarity score (cosine distance -> similarity)
        distance = distances[i] if i < len(distances) else 0.0
        score = round(1.0 - distance, 4)

        # Parse related_nodes from JSON string if present
        related = []
        if "related_nodes" in meta:
            try:
                related = json.loads(meta["related_nodes"])
            except (json.JSONDecodeError, TypeError):
                pass

        output.append({
            "rank": i + 1,
            "score": score,
            "node_id": meta.get("node_id", doc_id),
            "node_type": meta.get("node_type", ""),
            "label": meta.get("label", ""),
            "text": documents[i] if i < len(documents) else "",
            "metadata": {
                "chunk_type": meta.get("chunk_type", ""),
                "room": meta.get("room", ""),
                "related_nodes": related,
            },
        })

    return output


def retrieve_scene_context(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """High-level retrieval function for LLM task planner integration.

    This function only returns retrieved scene context.
    It does not generate a task plan.

    Args:
        query: Natural language query.
        top_k: Number of results.

    Returns:
        List of relevant scene context dicts.
    """
    from pathlib import Path
    default_db = Path(__file__).parent.parent / "outputs" / "vector_db"
    return query_rag(query, str(default_db), top_k=top_k)
