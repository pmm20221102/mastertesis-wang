# Khronos 3D Scene Graph RAG

基于 Khronos 3D 动态场景图的 RAG（检索增强生成）系统。提供结构化场景图解析、规范图导出、向量检索和确定性 Grounding 后端。

Khronos 3D Dynamic Scene Graph RAG system. Provides structured scene graph parsing, canonical graph export, vector retrieval, and deterministic Grounding backend.

---

## 概述 / Overview

将 Khronos SLAM 输出的结构化场景图（DSG JSON）转换为规范图和可检索的向量数据库，支持自然语言和结构化查询场景中的物体、房间、空间关系和层级约束。

Converts structured scene graphs (DSG JSON) from Khronos SLAM into a canonical graph and searchable vector database, supporting natural language and structured queries for objects, rooms, spatial relationships, and hierarchical constraints.

---

## 核心功能 / Core Features

- **Canonical Graph Export**: 直接从 raw DSG 导出规范图，100% 层级有效率 / **规范图导出**：直接从 raw DSG 导出，100% hierarchy validity
- **Hard Constraint Filtering**: node_type / label / location / relation 硬约束 / **硬约束过滤**：node_type / label / location / relation 硬约束
- **Fast Path Resolution**: Exact ID / Nearest-by-position / All-by-label 直接查询 / **快速路径解析**：Exact ID / Nearest-by-position / All-by-label 直接查询
- **Multi-Location Constraints**: LocationConstraint 支持 Place + Room 同时约束 / **多位置约束**：LocationConstraint 支持 Place + Room 同时约束
- **Relation Filtering**: inside / near / connected_to / contains（含传递） / **关系过滤**：inside / near / connected_to / contains（含传递）
- **Aggregate Queries**: max_count / min_count / count_in_location / **聚合查询**：max_count / min_count / count_in_location
- **Graph-Aware Chunks**: Entity / Context / Region 文档包含图结构元数据 / **图感知文档**：Entity / Context / Region 文档包含图结构元数据

---

## 项目结构 / Project Structure

```
khronos_3dsg_rag/
├── README.md
├── data/
│   └── dsg.json                 # Khronos SLAM 输出 (1858 nodes, 6205 edges)
├── outputs/
│   ├── canonical_graph.json      # v1 规范图（旧版）
│   ├── canonical_graph_v2.json   # v2 规范图（从 raw DSG 直接导出）
│   ├── canonical_graph_v2.manifest.json
│   ├── rag_documents.jsonl       # RAG 文档
│   ├── vector_db/                # ChromaDB 向量数据库
│   └── khronos_office_new/       # Office 数据集输出
│       ├── canonical_graph_v2.json
│       ├── canonical_graph_v2.manifest.json
│       ├── rag_documents.jsonl
│       └── vector_db/
└── src/
    ├── main.py                   # CLI 入口
    ├── parse_3dsg.py             # DSG JSON 解析器（含 position 修复）
    ├── export_canonical_graph.py # 规范图导出器（从 raw DSG 直接导出）
    ├── build_documents.py        # RAG 文档生成器（含图感知增强）
    ├── build_vector_db.py        # ChromaDB 向量索引
    ├── canonical_index.py        # 规范图索引（v1/v2 双格式加载）
    ├── scene_graph_api.py        # 场景图 API（硬约束 + 快速路径）
    ├── entity_resolver.py        # 实体解析器
    ├── graph_ranker.py           # 图感知排序器
    ├── query_rag.py              # RAG 查询接口
    ├── api_schemas.py            # API 数据模型
    └── schemas.py                # 内部数据模型
```

---

## 规范图 v2 / Canonical Graph v2

### 人工细语义补标（可选） / Optional Human-Confirmed Fine-Semantic Overrides

Hospital P1000 unknown object labels can be supplied as a separate, provenance-tracked sidecar. The parser applies only entries marked `human_confirmed` to matching object-layer nodes whose original Khronos `semantic_label` is the unknown sentinel `4294967295`. Raw DSG attributes are not changed. Fine-label IDs remain in the sidecar; the RAG parser uses the fine-label name as the node label because these IDs are not Khronos semantic-label IDs.

Export a separate canonical graph for the structured `SceneGraphRAG` index:

```bash
python src/export_canonical_graph.py /path/to/hospital/full-data/dsg.json \
  --semantic-overrides /path/to/hospital-object-semantic-overrides.json \
  --output-dir outputs/hospital_p1000_manual_semantic_overlay
```

The single-input builder below writes both the canonical graph and optional Chroma vector DB in the same output folder when the sidecar is supplied (do not use `--all` for this dataset-specific file):

```bash
python src/main.py build \
  --input /path/to/hospital/full-data/dsg.json \
  --semantic-overrides /path/to/hospital-object-semantic-overrides.json \
  --output outputs/hospital_p1000_manual_semantic_overlay
```

Non-confirmed entries, missing/non-object nodes, or nodes whose source label is not the unknown sentinel fail closed.

### Reproducible Hospital P1000 build

The reproducible build converts the human review CSV into a hashed semantic
sidecar, exports the canonical graph and documents, and builds a pinned vector
index. It never edits the source DSG or review CSV. The command refuses to
overwrite an existing output directory; choose a new output name for each
build. The manifest records input hashes, software versions, embedding model
revision/device, artifact hashes, and the semantic build ID. A separate stable
logical digest covers every Chroma document, metadata record, and stored vector;
runtime SQLite metadata changes do not invalidate the content check.

```bash
cd /path/to/Masterthesis-wang
python3 llm-rag/RAG/khronos_3dsg_rag/src/main.py \
  --config llm-rag/RAG/khronos_3dsg_rag/config.yaml build \
  --reproducible \
  --dataset hospital-p1000 \
  --input /path/to/hospital/full-data/dsg.json \
  --review-csv /path/to/hospital-object-review-2026-10-12.csv \
  --embedding-model paraphrase-multilingual-MiniLM-L12-v2 \
  --embedding-model-revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
  --embedding-device cpu \
  --output llm-rag/RAG/khronos_3dsg_rag/outputs/hospital_p1000_reproducible_20261024_v5
```

The 27 GB source snapshot and reviewed CSV are local inputs and are intentionally
not stored in Git. Replace their input paths when rebuilding on another machine.
The v5 vector database itself is rebuildable and is intentionally excluded from
the source commit; this output path matches the default Nav2 planner profile.

The pinned embedding model runs on CPU so the GPU remains available to Ollama.
Rebuilding requires the pinned model revision to be available locally or
downloadable. Do not overwrite or relabel an existing run; compare its manifest
and source hashes first.

### 导出方式 / Export Method

直接从 raw DSG 导出，不经过 ChromaDB 或 RAG chunk metadata：

Direct export from raw DSG, without going through ChromaDB or RAG chunk metadata:

```bash
cd llm-rag/RAG/khronos_3dsg_rag
python src/export_canonical_graph.py data/dsg.json -o outputs/
```

### 数据统计 / Data Statistics

| 指标 / Metric | 值 / Value |
|--------------|-----------|
| 总节点 / Total nodes | 1,858 |
| Object 节点 | 191 |
| Agent 节点 | 394 |
| Place 节点 | 1,262 |
| Room 节点 | 10 |
| Building 节点 | 1 |
| 层级边 / Hierarchy edges | 1,867 |
| 邻居边 / Neighbor edges | 4,338 |
| 层级有效率 / Hierarchy validity | 100% |
| Object→Place 覆盖率 | 100% |
| Place→Room 覆盖率 | 98.9% |
| Position 覆盖率 | 100% (1,262/1,262 unique) |

### 规范图 Schema v2 / Canonical Graph Schema v2

```json
{
  "schema_version": 2,
  "source_dsg_hash": "27a21bd8f590bfde",
  "total_nodes": 1858,
  "nodes": {
    "NODE_ID": {
      "node_id": "...",
      "layer": 2,
      "partition": 0,
      "node_type": "object",
      "label": "chairs",
      "position": [x, y, z],
      "parent_place_ids": ["..."],
      "parent_room_ids": ["..."],
      "child_ids": ["..."],
      "neighbor_ids": ["..."],
      "hierarchy_path_ids": ["..."],
      "room_id": "...",
      "place_id": "..."
    }
  },
  "edges": {
    "hierarchy": [{"parent": "...", "child": "..."}],
    "neighbors": [{"source": "...", "target": "..."}]
  }
}
```

---

## Scene Graph API / 场景图 API

### 快速路径 / Fast Paths

```python
from scene_graph_api import SceneGraphRAG

rag = SceneGraphRAG(db_dir)

# 1. Exact ID fast path
result = rag.resolve_entity(EntityReference(text="5692549928996306945"))

# 2. Nearest-by-position (canonical index 直接遍历)
result = rag.resolve_entity(EntityReference(
    text="nearest place",
    node_type="place",
    position=[0, 20, 2],
    quantifier="nearest",
))

# 3. All-by-label (canonical index 直接查询)
result = rag.resolve_entity(EntityReference(
    text="chairs",
    label="chairs",
    node_type="object",
    quantifier="all",
))
```

### 硬约束过滤 / Hard Constraint Filtering

```python
from planner_schemas.intent import EntityReference, LocationConstraint, RelationConstraint

# Location constraint (Place + Room simultaneously)
ref = EntityReference(
    text="chair",
    label="chairs",
    node_type="object",
    locations=[
        LocationConstraint(node_id="PLACE_ID", node_type="place"),
        LocationConstraint(node_id="ROOM_ID", node_type="room"),
    ],
)

# Relation constraint (inside / near / connected_to / contains)
ref = EntityReference(
    text="object",
    label="chairs",
    node_type="object",
    relations=[RelationConstraint(
        relation="inside",
        target_node_id="PLACE_ID",
        target_node_type="place",
    )],
)
```

### 聚合查询 / Aggregate Queries

```python
from planner_schemas.intent import AggregateConstraint, LocationConstraint

ref = EntityReference(
    text="room with most chairs",
    node_type="room",
    aggregate=AggregateConstraint(
        operation="count_in_location",
        target_labels=["chairs"],
        location=LocationConstraint(node_id="ROOM_ID", node_type="room"),
    ),
)
```

---

## 安装 / Installation

```bash
# Run from the repository root with the shared project environment:
.venv/bin/pip install -e llm-rag/planner
```

---

## 使用方法 / Usage

### 导出规范图 / Export Canonical Graph

```bash
cd llm-rag/RAG/khronos_3dsg_rag
python src/export_canonical_graph.py data/dsg.json -o outputs/
```

### 构建向量库 / Build Vector Database

```bash
cd llm-rag/RAG/khronos_3dsg_rag
python src/main.py build --all
```

### 查询 / Query

```bash
python src/main.py query --db outputs/vector_db -q "chair near table" --top-k 5
```

### Python API / Python 接口

```python
from scene_graph_api import SceneGraphRAG
from api_schemas import EntityReference

rag = SceneGraphRAG("outputs/vector_db")

# Simple query
result = rag.resolve_entity(EntityReference(text="chairs", label="chairs", node_type="object"))
print(f"Status: {result.status}, Candidates: {len(result.candidates)}")

# Spatial query
result = rag.resolve_entity(EntityReference(
    text="nearest place", node_type="place", position=[0, 20, 2], quantifier="nearest"
))
print(f"Nearest: {result.selected.node_id}")

# Set query
result = rag.resolve_entity(EntityReference(
    text="chairs", label="chairs", node_type="object", quantifier="all"
))
print(f"All chairs: {len(result.selected_many)}")
```

---

## 数据流 / Data Flow

```
Khronos SLAM
    ↓
dsg.json (SPARK-DSG format, 1858 nodes, 6205 edges)
    ↓ parse_3dsg.py (with position fix)
SceneGraph (nodes + edges + valid positions)
    ↓ export_canonical_graph.py
canonical_graph_v2.json (100% hierarchy validity)
    ↓ build_documents.py
RAG Documents (entity/context/region)
    ↓ build_vector_db.py
ChromaDB Vector Database
    ↓
SceneGraphRAG.resolve_entity()
    ├── Exact ID fast path
    ├── Nearest-by-position fast path (canonical index)
    ├── All-by-label fast path (canonical index)
    └── Standard semantic path (EntityResolver + hard filters)
    ↓
ResolutionResult (status + candidates + selected)
```

---

## 依赖 / Dependencies

- `chromadb` — 向量数据库 / Vector database
- `sentence-transformers` — Embedding 模型 / Embedding model
- `pyyaml` — 配置解析 / Config parsing

---

## 测试 / Tests

```bash
cd llm-rag/RAG/khronos_3dsg_rag
python -m pytest src/ -v
```

---

## 评估结果 / Evaluation Results

### Oracle-Intent Grounding (v3 benchmark, 100 cases)

| 指标 / Metric | 值 / Value |
|--------------|-----------|
| Pipeline status accuracy | 100% |
| Grounding exact success | 100/100 |
| Set P/R/F1 | 1.0 / 1.0 / 1.0 |
| Spatial target-ID accuracy | 10/10 |
| Spatial distance error | 0.0m |
| Case failure count | 0 |
| Avg latency | 271ms |

### 分类别结果 / Category Breakdown

| 类别 / Category | 准确率 / Accuracy | 数量 / Count |
|----------------|------------------|-------------|
| ambiguous | 100% | 5/5 |
| obj_nav | 100% | 10/10 |
| place_constraint | 100% | 15/15 |
| room_constraint | 100% | 10/10 |
| set_query | 100% | 10/10 |
| not_found | 100% | 10/10 |
| spatial | 100% | 10/10 |
| exact_id | 100% | 5/5 |
| complex | 100% | 10/10 |
| relation | 100% | 15/15 |

---

## 版本历史 / Version History

### v18 (2026-07-23) — RAG Grounding 模块完成

- LocationConstraint 支持 Place + Room 同时约束
- RelationConstraint.target_node_id 支持完整节点 ID
- count_in_location 聚合操作
- transitive contains/inside（BFS descendants）
- 统一 constraint pipeline（nearest/all/standard 共用）
- Benchmark v3：byte-identical 可复现性，manifest hash 完整性
- Evaluation：grounding_exact_success, Set P/R/F1, Spatial ID/distance
- target_ref 必填（无静默猜测），step-specific references

### v17 (2026-07-23) — Complex/Relation Schema 修复

- LocationConstraint schema（Place + Room）
- RelationConstraint.target_node_id
- count_in_location aggregate
- Complex cases 使用 locations field
- Relation inside 使用 relations field

### v16 (2026-07-23) — Hard Constraints + Spatial/Set 修复

- node_type / label hard filter
- Nearest query 使用 canonical index 直接遍历
- All query 使用 canonical index 精确集合
- Spatial 0/10 → 10/10, Set P=0.616 → 1.0

### v15 (2026-07-17) — Canonical Graph v2 + Position 修复

- export_canonical_graph.py 直接从 raw DSG 导出
- Hierarchy validity: 26.4% → 100%
- Position parser 修复（Place/Room 不再全部 [0,0,0]）
- Benchmark manifest hash 完整性
- Validator category pass 进入 overall

### v14 (2026-07-16) — 结构化管道优化

- PlanDraft schema（LLM 输出 target_ref）
- 确定性 target_ref → target_id 编译
- Role-specific validator
- 100 条 Grounding benchmark

---

## 论文可用结论 / Paper-Ready Claims

### 可以写 / Can write:

> Given oracle structured intents, the v18 scene-graph Grounding backend achieved 100% pipeline-status accuracy, 100% resolved target-ID accuracy, 100% set exact match, and 100% spatial target-ID accuracy across all 100 development benchmark cases. The canonical graph exporter achieved 100% hierarchy validity with 100% Object-to-Place and 98.9% Place-to-Room coverage.

给定 oracle 结构化意图，v18 场景图 Grounding 后端在全部 100 条开发基准测试中实现了 100% 的流水线状态准确率、100% 的解析目标 ID 准确率、100% 的集合精确匹配和 100% 的空间目标 ID 准确率。规范图导出器实现了 100% 的层级有效率，Object→Place 覆盖率 100%，Place→Room 覆盖率 98.9%。

### 不应写 / Cannot write:

- End-to-end LLM+RAG accuracy = 100%（未用真实 LLM 测试）
- Natural-language understanding accuracy = 100%（仅 oracle intent 模式）
- Cross-dataset generalization is proven（未测试跨数据集）
