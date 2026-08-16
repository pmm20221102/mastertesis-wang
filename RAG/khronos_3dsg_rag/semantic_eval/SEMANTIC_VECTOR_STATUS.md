# SEMANTIC_VECTOR_STATUS — 向量数据库状态确认

日期：2026-08-05
数据源：Office GT（`office_launch_dense`，3096 节点 / 12335 边）

## 1. 基本状态

| 项 | 值 |
|---|---|
| Vector DB 路径 | `outputs/office_dense/vector_db` |
| Chroma collection 名称 | `scene_graph` |
| Embedding model | `paraphrase-multilingual-MiniLM-L12-v2`（sentence-transformers，cosine 距离）|
| 文档数量 | 6201 |
| 索引方式 | ChromaDB PersistentClient + HNSW(cosine) |

## 2. Metadata 字段（按 chunk_type）

| chunk_type | doc_id 前缀 | 关键 metadata | 是否有 node_id |
|---|---|---|---|
| `entity` | `entity_{node_id}` | node_id、label、node_type、layer、room | ✅ |
| `context` | `context_{node_id}` | node_id、label、node_type、layer、room、bounding_box 等完整信息 | ✅ |
| `alias` | `alias_{keyword}` | keyword、synonyms | ❌ 无 node_id |
| `region` / `relation` / `materialized_relation` | 对应前缀 | 图结构信息 | 部分 |

## 3. `collection.query()` 调用确认

- `src/query_rag.py:359` → `collection.query(**kwargs)`（真实 ChromaDB 调用）
- `query_rag()` 支持 `top_k`、`where_filter`（metadata 过滤）
- 测试查询 `"chair"` 返回：`alias_椅子`(dist 0.16)、`alias_chair`(dist 0.18)、`context_...`(label=chairs, type=objects, dist 0.37)

## 4. 四个确认问题的回答

1. **ChromaDB 是否能正常加载？** ✅ 能（collection `scene_graph`，6201 文档）
2. **collection.query() 是否能返回结果？** ✅ 能（返回 alias + context 文档，带距离分数）
3. **查询结果是否包含 node ID、label 和 node type？** ⚠️ 部分 —— **entity/context 文档有**（node_id/label/node_type）；**alias 文档没有**（只有 keyword/synonyms）。语义检索接口需过滤出带 node_id 的候选。
4. **当前向量库是否与 RAG documents 对应？** ✅ 对应（6201 文档 = `rag_documents.jsonl` 行数）

## 5. 对语义检索的影响

- 别名文档（`alias_chair` 等）分数高但无 node_id，不能作为最终实体候选
- 语义检索接口应：真实调用 `collection.query()`，然后**过滤出带 node_id 的实体/上下文文档**，用其 `label` 作为 canonical label
- 数据库真实 canonical label（entity/context 文档的 label 分布）需在评测前统计
