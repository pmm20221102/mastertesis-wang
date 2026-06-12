"""Build and query a ChromaDB vector database from RAG documents."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import chromadb
from chromadb.config import Settings

from schemas import RagDocument

logger = logging.getLogger(__name__)

# Default embedding model — multilingual, lightweight
DEFAULT_EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# Collection name
COLLECTION_NAME = "scene_graph"


def build_vector_db(
    documents: list[RagDocument],
    output_dir: str | Path,
    embed_model: str = DEFAULT_EMBED_MODEL,
) -> chromadb.Collection:
    """Create a ChromaDB collection from RAG documents.

    Args:
        documents: List of RagDocument objects.
        output_dir: Directory to persist the database.
        embed_model: Sentence-transformers model name.

    Returns:
        The ChromaDB collection.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize ChromaDB with persistent storage
    client = chromadb.PersistentClient(
        path=str(output_dir),
        settings=Settings(anonymized_telemetry=False),
    )

    # Delete existing collection if present (rebuild)
    try:
        client.delete_collection(COLLECTION_NAME)
        logger.info(f"Deleted existing collection '{COLLECTION_NAME}'")
    except Exception:
        pass

    # Create collection with sentence-transformers embedding
    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    if not documents:
        logger.warning("No documents to index")
        return collection

    # Prepare batch data
    ids = []
    texts = []
    metadatas = []

    for doc in documents:
        ids.append(doc.doc_id)
        texts.append(doc.text)
        # ChromaDB metadata values must be str, int, float, or bool
        safe_meta = {}
        for k, v in doc.metadata.items():
            if isinstance(v, (str, int, float, bool)):
                safe_meta[k] = v
            elif isinstance(v, list):
                safe_meta[k] = json.dumps(v, ensure_ascii=False)
            else:
                safe_meta[k] = str(v)
        metadatas.append(safe_meta)

    # Add in batches (ChromaDB default batch size is ~5461)
    batch_size = 500
    for i in range(0, len(ids), batch_size):
        end = min(i + batch_size, len(ids))
        collection.add(
            ids=ids[i:end],
            documents=texts[i:end],
            metadatas=metadatas[i:end],
        )
        logger.info(f"Indexed batch {i // batch_size + 1}: {end - i} documents")

    logger.info(f"Built vector DB with {len(ids)} documents at {output_dir}")
    return collection


def load_vector_db(
    db_dir: str | Path,
    embed_model: str = DEFAULT_EMBED_MODEL,
) -> chromadb.Collection:
    """Load an existing ChromaDB collection."""
    db_dir = Path(db_dir)
    if not db_dir.exists():
        raise FileNotFoundError(f"Vector DB not found at {db_dir}")

    client = chromadb.PersistentClient(
        path=str(db_dir),
        settings=Settings(anonymized_telemetry=False),
    )
    return client.get_collection(COLLECTION_NAME)
