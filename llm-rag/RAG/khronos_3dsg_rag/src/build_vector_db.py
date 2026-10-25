"""Build and query a ChromaDB vector database from RAG documents."""

from __future__ import annotations

import json
import hashlib
import logging
from pathlib import Path

import chromadb
from chromadb.config import Settings
from chromadb.errors import NotFoundError

from schemas import RagDocument

logger = logging.getLogger(__name__)

# Default embedding model — multilingual, lightweight
DEFAULT_EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_EMBED_MODEL_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
DEFAULT_EMBED_DEVICE = "cpu"

# Collection name
COLLECTION_NAME = "scene_graph"


def collection_content_sha256(collection) -> str:
    """Hash sorted Chroma records, including stored vectors, not mutable sqlite bytes."""
    result = collection.get(include=["embeddings", "documents", "metadatas"])
    ids = result.get("ids") or []
    documents = result.get("documents") or [None] * len(ids)
    metadatas = result.get("metadatas") or [None] * len(ids)
    embeddings = result.get("embeddings")
    if embeddings is None:
        embeddings = [None] * len(ids)
    if not (len(ids) == len(documents) == len(metadatas) == len(embeddings)):
        raise ValueError("Chroma collection returned inconsistent record fields")
    records = []
    for record_id, document, metadata, embedding in zip(
            ids, documents, metadatas, embeddings, strict=True):
        vector = None if embedding is None else [float(value) for value in embedding]
        records.append({"id": str(record_id), "document": document,
                        "metadata": metadata, "embedding": vector})
    records.sort(key=lambda record: record["id"])
    payload = json.dumps(records, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class SentenceTransformerEmbeddingFunction(chromadb.EmbeddingFunction):
    """Custom embedding function using sentence-transformers."""

    def __init__(self, model_name: str = DEFAULT_EMBED_MODEL,
                 revision: str | None = None, device: str | None = None):
        self.model_name = model_name
        self.revision = revision
        self.device = device
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                options = {}
                if self.revision:
                    options["revision"] = self.revision
                if self.device:
                    options["device"] = self.device
                self._model = SentenceTransformer(self.model_name, **options)
                logger.info(f"Loaded embedding model: {self.model_name}")
            except ImportError:
                logger.warning(
                    f"sentence-transformers not installed, using default embeddings. "
                    f"Install with: pip install sentence-transformers"
                )
                return False
        return True

    def __call__(self, input: list[str]) -> list[list[float]]:
        if not self._load_model():
            # Fallback: return empty list to use ChromaDB default
            return []

        embeddings = self._model.encode(input, show_progress_bar=False)
        return embeddings.tolist()


def build_vector_db(
    documents: list[RagDocument],
    output_dir: str | Path,
    embed_model: str = DEFAULT_EMBED_MODEL,
    embed_model_revision: str | None = None,
    embed_model_device: str | None = None,
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

    # Build separately: failures must not destroy the active collection.
    from uuid import uuid4
    token = uuid4().hex
    try:
        previous = client.get_collection(COLLECTION_NAME)
    except NotFoundError:
        previous = None
    staged_name = f"{COLLECTION_NAME}_staged_{token}"
    backup_name = f"{COLLECTION_NAME}_backup_{token}"

    # Create embedding function
    embedding_fn = SentenceTransformerEmbeddingFunction(embed_model,
                                                        revision=embed_model_revision,
                                                        device=embed_model_device)

    # Create collection with sentence-transformers embedding
    collection_metadata = {"hnsw:space": "cosine", "embedding_model": embed_model}
    if embed_model_revision:
        collection_metadata["embedding_model_revision"] = embed_model_revision
    if embed_model_device:
        collection_metadata["embedding_device"] = embed_model_device
    collection = client.create_collection(
        name=staged_name,
        embedding_function=embedding_fn,
        metadata=collection_metadata,
    )

    if not documents:
        logger.warning("No documents to index")

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

    if previous is not None:
        previous.modify(name=backup_name)
    try:
        collection.modify(name=COLLECTION_NAME)
    except Exception:
        if previous is not None:
            previous.modify(name=COLLECTION_NAME)
        raise
    if previous is not None:
        logger.info(f"Preserved previous collection as '{backup_name}'")
    logger.info(f"Built vector DB with {len(ids)} documents at {output_dir}")
    return collection


def load_vector_db(
    db_dir: str | Path,
    embed_model: str = DEFAULT_EMBED_MODEL,
    embed_model_revision: str | None = None,
    embed_model_device: str | None = None,
) -> chromadb.Collection:
    """Load an existing ChromaDB collection."""
    db_dir = Path(db_dir)
    if not db_dir.exists():
        raise FileNotFoundError(f"Vector DB not found at {db_dir}")

    client = chromadb.PersistentClient(
        path=str(db_dir),
        settings=Settings(anonymized_telemetry=False),
    )

    # Reuse the exact model revision recorded when this collection was built.
    metadata = client.get_collection(COLLECTION_NAME).metadata or {}
    resolved_model = metadata.get("embedding_model", embed_model)
    resolved_revision = metadata.get("embedding_model_revision", embed_model_revision)
    resolved_device = metadata.get("embedding_device", embed_model_device)
    embedding_fn = SentenceTransformerEmbeddingFunction(
        resolved_model, revision=resolved_revision, device=resolved_device)
    return client.get_collection(COLLECTION_NAME, embedding_function=embedding_fn)
