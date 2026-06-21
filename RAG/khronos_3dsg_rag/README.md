# Khronos 3D Scene Graph RAG

基于 Khronos 3D 动态场景图的 RAG（检索增强生成）向量库构建工具。

Khronos 3D Dynamic Scene Graph RAG (Retrieval-Augmented Generation) vector database builder.

---

## 概述 / Overview

将 Khronos SLAM 输出的结构化场景图（DSG JSON）转换为可检索的向量数据库，支持自然语言查询场景中的物体、房间和空间关系。

Converts structured scene graphs (DSG JSON) from Khronos SLAM into a searchable vector database, supporting natural language queries for objects, rooms, and spatial relationships.

---

## 功能特性 / Features

- **Graph-Aware Metadata**: 每个 chunk 包含 parent/child/neighbor 关系，支持图遍历检索 / **图感知 Metadata**：每个 chunk 包含 parent/child/neighbor 关系，支持图遍历检索
- **Multi-Type Chunks**: Entity / Region / Relation / Materialized Relation / Alias / **多类型文档**：Entity / Region / Relation / Materialized Relation / Alias
- **Hierarchical Query**: 支持 "room 201 里的物体" 类型的层级查询 / **层级查询**：支持 "room 201 里的物体" 类型的层级查询
- **Multilingual Support**: 使用 paraphrase-multilingual-MiniLM-L12-v2 embedding / **多语言支持**：使用 paraphrase-multilingual-MiniLM-L12-v2 embedding
- **Graph Traversal Expansion**: 检索后自动扩展图邻居 / **图遍历扩展**：检索后自动扩展图邻居

---

## 项目结构 / Project Structure

```
khronos_3dsg_rag/
├── README.md
├── requirements.txt
├── config.yaml                  # 配置文件（数据源、别名、embedding 模型）
├── data/
│   ├── 3DSG.json               # 示例数据
│   └── dsg.json                 # Khronos SLAM 输出
├── outputs/
│   ├── rag_documents.jsonl      # 生成的 RAG 文档
│   └── vector_db/               # ChromaDB 向量数据库
└── src/
    ├── main.py                  # CLI 入口
    ├── parse_3dsg.py            # DSG JSON 解析器
    ├── build_documents.py       # RAG 文档生成器（含图感知增强）
    ├── build_vector_db.py       # ChromaDB 向量索引
    ├── query_rag.py             # RAG 查询接口
    └── schemas.py               # 数据模型
```

---

## 安装 / Installation

```bash
conda activate llm-task-planner
pip install chromadb sentence-transformers pyyaml
```

---

## 使用方法 / Usage

### 构建向量库 / Build Vector Database

```bash
cd RAG/khronos_3dsg_rag

# 构建所有数据源（并行）
python src/main.py build --all

# 构建单个数据源
python src/main.py build --input data/3DSG.json -o outputs/
```

### 查询 / Query

```bash
# 查询单个数据库
python src/main.py query --db outputs/mock_office -q "chair near table" --top-k 5

# 查询所有数据库
python src/main.py query --all -q "navigate to room 201" --top-k 10

# 列出可用数据库
python src/main.py query --list
```

### Python API / Python 接口

```python
from query_rag import query_rag

results = query_rag("chair near table", "outputs/vector_db", top_k=5)
for r in results:
    print(f"[{r['rank']}] score={r['score']:.4f} node={r['node_id']}")
    print(f"  {r['text']}")
```

---

## 文档类型 / Document Types

### 1. Entity Chunk
每个节点一个文档，包含位置、尺寸、观测时间等信息。

One document per node with position, size, observation time, etc.

### 2. Region Chunk
聚合容器节点（room/place/building）的子节点信息。

Aggregated information for container nodes (room/place/building).

### 3. Relation Chunk
每条边一个文档，描述节点间关系。

One document per edge describing relationships between nodes.

### 4. Materialized Relation Chunk
预物化的关系路径，支持快速层级查询。

Pre-materialized relation paths for fast hierarchical queries.

### 5. Alias Chunk
关键词别名扩展，提升查询效果。

Keyword alias expansion for improved query matching.

---

## Graph-Aware Metadata / 图感知元数据

每个 entity chunk 包含以下图结构信息：

Each entity chunk contains the following graph structure information:

| 字段 / Field | 类型 / Type | 说明 / Description |
|-------------|------------|-------------------|
| `parent_ids` | `list[str]` | 父节点 ID / Parent node IDs |
| `child_ids` | `list[str]` | 子节点 ID / Child node IDs |
| `neighbor_ids` | `list[str]` | 邻居节点 ID / Neighbor node IDs |
| `depth` | `int` | 层级深度 (0=building, 3=object) / Hierarchy depth |
| `bounding_box_center` | `list[float]` | 位置坐标 / Position coordinates |
| `bounding_box_dimensions` | `list[float]` | 尺寸 / Dimensions |

---

## 查询示例 / Query Examples

```text
# 物体查询 / Object queries
"chair"                           → 返回所有椅子 / Returns all chairs
"small_office_objects"            → 返回小办公物体 / Returns small office objects

# 层级查询 / Hierarchical queries
"objects in room 201"             → 返回 room 201 的子节点 / Returns children of room 201
"rooms in building"               → 返回所有房间 / Returns all rooms

# 空间查询 / Spatial queries
"chair near table"                → 返回位置相近的椅子和桌子 / Returns chairs near tables
"objects at position x=-15 y=22"  → 返回指定位置的物体 / Returns objects at position

# 关系查询 / Relation queries
"connected rooms"                 → 返回有连接关系的房间 / Returns connected rooms
```

---

## 配置 / Configuration

`config.yaml`:

```yaml
# 数据源 / Data sources
dsg_sources:
  mock_office: "data/3DSG.json"
  khronos_office: "/path/to/dsg.json"

# Embedding 模型 / Embedding model
embed_model: "paraphrase-multilingual-MiniLM-L12-v2"

# 关键词别名 / Keyword aliases
aliases:
  door: ["entrance", "exit", "gate"]
  corridor: ["hallway", "passage"]
  chair: ["seat"]
  table: ["desk"]

# 中文别名 / Chinese aliases
zh_aliases:
  门: ["door", "entrance"]
  走廊: ["corridor", "hallway"]
  椅子: ["chair", "seat"]
```

---

## 数据流 / Data Flow

```
Khronos SLAM
    ↓
dsg.json (SPARK-DSG format)
    ↓ parse_3dsg.py
SceneGraph (nodes + edges)
    ↓ build_documents.py
RAG Documents (entity/region/relation/alias)
    ↓ build_vector_db.py
ChromaDB Vector Database
    ↓ query_rag.py
Retrieved Chunks (with graph expansion)
```

---

## 依赖 / Dependencies

- `chromadb` - 向量数据库 / Vector database
- `sentence-transformers` - Embedding 模型 / Embedding model
- `pyyaml` - 配置解析 / Config parsing
