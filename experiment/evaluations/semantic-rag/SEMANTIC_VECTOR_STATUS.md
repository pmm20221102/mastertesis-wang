# SEMANTIC_VECTOR_STATUS: Vector Database Verification

Date: 2026-08-05
Data source: Office ground truth (`office_launch_dense`, 3,096 nodes / 12,335 edges)

## 1. Basic Status

| Item | Value |
|---|---|
| Vector DB path | `outputs/office_dense/vector_db` |
| Chroma collection | `scene_graph` |
| Embedding model | `paraphrase-multilingual-MiniLM-L12-v2` (sentence-transformers, cosine distance) |
| Document count | 6,201 |
| Index | ChromaDB PersistentClient + HNSW (cosine) |

## 2. Metadata Fields by `chunk_type`

| `chunk_type` | `doc_id` prefix | Key metadata | Has `node_id`? |
|---|---|---|---|
| `entity` | `entity_{node_id}` | node_id, label, node_type, layer, room | Yes |
| `context` | `context_{node_id}` | node_id, label, node_type, layer, room, bounding box, and other full context | Yes |
| `alias` | `alias_{keyword}` | keyword, synonyms | No |
| `region` / `relation` / `materialized_relation` | corresponding prefix | graph-structure information | Partial |

## 3. `collection.query()` Verification

- `src/query_rag.py:359` calls `collection.query(**kwargs)` against ChromaDB.
- `query_rag()` supports `top_k` and `where_filter` metadata filtering.
- A test query for `chair` returned an alias entry for the Chinese term meaning “chair” (distance 0.16), `alias_chair` (distance 0.18), and a context document with `label=chairs`, `type=objects` (distance 0.37).

## 4. Answers to Four Verification Questions

1. **Can ChromaDB load the database?** Yes (`scene_graph` collection, 6,201 documents).
2. **Does `collection.query()` return results?** Yes (alias and context documents with distance scores).
3. **Do results include node ID, label, and node type?** Partially. Entity/context documents include `node_id`, `label`, and `node_type`; alias documents include only keywords/synonyms. The semantic retrieval interface must filter for candidates with `node_id`.
4. **Does the current vector database correspond to the RAG documents?** Yes (6,201 documents = the number of lines in `rag_documents.jsonl`).

## 5. Implications for Semantic Retrieval

- Alias documents such as `alias_chair` can score highly but have no `node_id`; they cannot be final entity candidates.
- The semantic retrieval interface should call `collection.query()` and then filter for entity/context documents that have a `node_id`; use their `label` as the canonical label.
- Count the actual canonical labels in entity/context documents before evaluation.
