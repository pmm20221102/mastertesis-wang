"""RAG-based scene context retrieval for LLM task planning.

Retrieves relevant entities from the Khronos DSG vector database
and formats them as scene_graph context for the LLM task planner.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# Add RAG module to path
RAG_SRC = Path(__file__).parent.parent / "RAG" / "khronos_3dsg_rag" / "src"
sys.path.insert(0, str(RAG_SRC))

from query_rag import query_rag

logger = logging.getLogger(__name__)

# Default vector DB path
DEFAULT_DB_DIR = str(Path(__file__).parent.parent / "RAG" / "khronos_3dsg_rag" / "outputs" / "vector_db")


def retrieve_scene_context(
    user_command: str,
    db_dir: str | None = None,
    top_k: int = 20,
) -> dict[str, Any]:
    """Retrieve relevant scene context from RAG for LLM task planning.

    Strategy: RAG vector retrieval + relation-based entity expansion.
    """
    if db_dir is None:
        db_dir = DEFAULT_DB_DIR

    # Stage 1: RAG retrieval (gets relation chunks that mention relevant entities)
    results = query_rag(user_command, db_dir, top_k=top_k)

    # Stage 2: Extract entity IDs from chunks
    # Keep entity, context, and region chunks (context chunks have valuable hierarchy info)
    NODE_CHUNK_TYPES = {"entity", "context", "region"}
    entity_ids: set[str] = set()
    for r in results:
        nid = r.get("node_id", "")
        meta = r.get("metadata", {})
        if meta.get("chunk_type") == "relation":
            # Parse doc_id: "rel_<source>_<target>"
            if nid.startswith("rel_"):
                parts = nid[4:].split("_", 1)
                if len(parts) == 2:
                    entity_ids.add(parts[0])
                    entity_ids.add(parts[1])
        elif meta.get("chunk_type") in NODE_CHUNK_TYPES and nid:
            entity_ids.add(nid)

    # Stage 3: Load full entity data from JSONL
    jsonl_path = Path(db_dir).parent / "rag_documents.jsonl"
    entity_docs: dict[str, dict] = {}
    if jsonl_path.exists():
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                doc = json.loads(line)
                if doc["metadata"]["chunk_type"] == "entity":
                    eid = doc["metadata"].get("node_id", doc.get("doc_id", ""))
                    if eid in entity_ids:
                        entity_docs[eid] = doc

    # Stage 4: Build entity list
    entities = []
    for eid in entity_ids:
        if eid in entity_docs:
            doc = entity_docs[eid]
            entities.append({
                "id": eid,
                "type": doc["metadata"].get("node_type", "unknown"),
                "name": doc["metadata"].get("label", ""),
                "function": "entity",
                "description": doc["text"],
                "location": doc["metadata"].get("room", ""),
            })
        else:
            # Entity not in JSONL, create minimal entry
            entities.append({
                "id": eid,
                "type": "unknown",
                "name": "",
                "function": "entity",
                "description": f"Entity {eid} referenced in scene graph.",
            })

    return {
        "scene_id": "khronos_rag",
        "scene_type": "khronos_office",
        "description": f"RAG-retrieved context for: {user_command}",
        "entities": entities,
        "relations": [],
    }


def extract_keywords_with_llm(
    user_command: str,
    model: str = "mistral",
    config: dict | None = None,
) -> dict[str, list[str]]:
    """Use LLM to decompose user command into structured keywords.

    Returns dict with keys: target_object, target_room, target_person,
    action, constraints.
    """
    from ollama import Client
    import yaml

    if config is None:
        config_path = Path(__file__).parent / "config_rag.yaml"
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
        else:
            config = {}

    # Get Ollama host
    ollama_host = config.get("ollama_host", "")
    if not ollama_host:
        try:
            import subprocess
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True, text=True, timeout=5,
            )
            parts = result.stdout.split()
            for i, p in enumerate(parts):
                if p == "via" and i + 1 < len(parts):
                    ollama_host = f"http://{parts[i+1]}:11434"
                    break
        except Exception:
            pass
    if not ollama_host:
        ollama_host = "http://127.0.0.1:11434"

    client = Client(host=ollama_host, timeout=60)

    keyword_prompt = f"""Analyze the following robot navigation instruction and extract structured keywords.

Instruction: "{user_command}"

Return a JSON object with these fields:
- "target_object": list of objects mentioned (e.g., ["chair", "table"])
- "target_room": list of rooms mentioned (e.g., ["room_201", "operating_room"])
- "target_person": list of people mentioned (e.g., ["patient", "doctor"])
- "action": the main action (e.g., "navigate", "escort", "guide", "avoid")
- "constraints": any constraints (e.g., ["avoid_restricted_area"])
- "source_location": where to start (if mentioned)
- "destination": where to go (if mentioned)

Return JSON only, no explanation."""

    options = config.get("options", {"temperature": 0.1, "num_ctx": 4096, "num_predict": 512})

    response = client.chat(
        model=model,
        messages=[{"role": "user", "content": keyword_prompt}],
        options=options,
    )

    raw = response.message.content.strip()
    # Extract JSON from response
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            pass

    # Fallback: simple keyword extraction
    return {
        "target_object": [],
        "target_room": [],
        "target_person": [],
        "action": "navigate",
        "constraints": [],
        "source_location": "",
        "destination": "",
    }


def retrieve_with_keywords(
    user_command: str,
    db_dir: str | None = None,
    top_k: int = 20,
    use_llm_keywords: bool = True,
    model: str = "mistral",
    config: dict | None = None,
) -> dict[str, Any]:
    """Two-stage retrieval: LLM keyword extraction + RAG search.

    1. LLM decomposes the instruction into structured keywords
    2. RAG retrieves relevant entities for each keyword category
    3. Results are merged into a single scene graph context
    """
    if db_dir is None:
        db_dir = DEFAULT_DB_DIR

    # Stage 1: Extract keywords
    if use_llm_keywords:
        keywords = extract_keywords_with_llm(user_command, model=model, config=config)
        logger.info(f"Extracted keywords: {keywords}")
    else:
        keywords = {"target_object": [], "target_room": [], "target_person": [], "action": "navigate"}

    # Stage 2: RAG retrieval with original query + keyword-enhanced queries
    all_results = []
    seen_ids: set[str] = set()

    # Primary retrieval with full instruction
    primary = query_rag(user_command, db_dir, top_k=top_k)
    for r in primary:
        nid = r.get("node_id", "")
        if nid and nid not in seen_ids and not nid.startswith("alias_"):
            seen_ids.add(nid)
            all_results.append(r)

    # Secondary retrieval with extracted keywords
    # Only iterate over list-type categories (target_object, target_room, target_person)
    for category in ("target_object", "target_room", "target_person"):
        terms = keywords.get(category, [])
        if not terms or not isinstance(terms, list):
            continue
        for term in terms:
            if not isinstance(term, str) or len(all_results) >= top_k * 2:
                break
            extra = query_rag(term, db_dir, top_k=5)
            for r in extra:
                nid = r.get("node_id", "")
                if nid and nid not in seen_ids and not nid.startswith("alias_"):
                    seen_ids.add(nid)
                    all_results.append(r)

    # Build scene graph context: extract entity IDs from all results
    # Keep entity, context, and region chunks (context chunks have valuable hierarchy info)
    NODE_CHUNK_TYPES = {"entity", "context", "region"}
    entity_ids: set[str] = set()
    for r in all_results:
        nid = r.get("node_id", "")
        meta = r.get("metadata", {})
        if meta.get("chunk_type") == "relation" and nid.startswith("rel_"):
            parts = nid[4:].split("_", 1)
            if len(parts) == 2:
                entity_ids.add(parts[0])
                entity_ids.add(parts[1])
        elif meta.get("chunk_type") in NODE_CHUNK_TYPES and nid:
            entity_ids.add(nid)
    # Limit to top_k entities to keep prompt manageable
    entity_ids = set(list(entity_ids)[:top_k])

    # Load entity data from JSONL
    jsonl_path = Path(db_dir).parent / "rag_documents.jsonl"
    entity_docs: dict[str, dict] = {}
    if jsonl_path.exists():
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                doc = json.loads(line)
                if doc["metadata"]["chunk_type"] == "entity":
                    eid = doc["metadata"].get("node_id", doc.get("doc_id", ""))
                    if eid in entity_ids:
                        entity_docs[eid] = doc
        # Also include all room entities for context
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                doc = json.loads(line)
                if doc["metadata"]["chunk_type"] == "entity" and doc["metadata"].get("node_type") == "rooms":
                    eid = doc["metadata"].get("node_id", doc.get("doc_id", ""))
                    if eid and eid not in entity_docs:
                        entity_docs[eid] = doc

    entities = []
    for eid in entity_ids:
        if eid in entity_docs:
            doc = entity_docs[eid]
            entities.append({
                "id": eid,
                "type": doc["metadata"].get("node_type", "unknown"),
                "name": doc["metadata"].get("label", ""),
                "function": "entity",
                "description": doc["text"],
                "location": doc["metadata"].get("room", ""),
            })
        else:
            entities.append({
                "id": eid,
                "type": "unknown",
                "name": "",
                "function": "entity",
                "description": f"Entity {eid} referenced in scene graph.",
            })

    return {
        "scene_id": "khronos_rag",
        "scene_type": "khronos_office",
        "description": f"RAG-retrieved context for: {user_command}",
        "entities": entities,
        "relations": [],
        "_keywords": keywords,  # Pass keywords to prompt for reference
    }
