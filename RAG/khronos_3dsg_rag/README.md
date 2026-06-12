# Khronos 3DSG RAG

将 Khronos 输出的 3D Dynamic Scene Graph (DSG) JSON 转换为本地 RAG 知识库，支持自然语言检索场景上下文。

## 流程

```
3DSG JSON → 解析节点/边 → 生成语义 chunks → 建立 ChromaDB 向量库 → 自然语言查询
```

## 环境

本项目与 `LLM-robot-taskplanning-chaining` 共用同一个 conda 环境，不单独创建 venv。

```bash
# 激活环境（每次开终端执行一次）
conda activate llm-task-planner
```

依赖已预装，如需手动安装：

```bash
pip install ollama pydantic pyyaml chromadb sentence-transformers
```

首次运行会自动下载 embedding 模型 (~120MB)。

## 使用

### 构建 RAG

编辑 `config.yaml` 配置数据源：

```yaml
dsg_sources:
  khronos_office: "/path/to/office/dsg.json"
  khronos_apartment: "/path/to/apartment/dsg.json"
```

```bash
# 并行构建所有数据源（每个生成独立数据库）
python src/main.py build --all

# 单独构建一个
python src/main.py build --input data/3DSG.json -o outputs/mock_office
```

输出结构：
```
outputs/
├── khronos_office/
│   ├── rag_documents.jsonl
│   └── vector_db/
├── khronos_apartment/
│   ├── rag_documents.jsonl
│   └── vector_db/
└── mock_office/
    ├── rag_documents.jsonl
    └── vector_db/
```

### 查询

```bash
# 查看有哪些可用数据库
python src/main.py query --list

# 查指定数据库（按名称或路径）
python src/main.py query --db khronos_office -q "chair near table"
python src/main.py query --db outputs/mock_office -q "走廊附近有什么"

# 跨所有数据库搜索，按相关度排序
python src/main.py query --all -q "navigate to room 201" --top-k 10
```

### Python API（供 LLM task planner 调用）

```python
from src.query_rag import retrieve_scene_context

# 搜默认数据库
results = retrieve_scene_context("find a chair in room 201", top_k=5)

# 搜指定数据库
from src.query_rag import query_rag
results = query_rag("chair near table", "outputs/khronos_office/vector_db", top_k=5)
```

## 项目结构

```
khronos_3dsg_rag/
├── README.md
├── requirements.txt
├── config.yaml              # 别名配置
├── data/
│   └── 3DSG.json            # mock 测试数据
├── outputs/
│   ├── rag_documents.jsonl  # 生成的文档 chunks
│   └── vector_db/           # ChromaDB 持久化
└── src/
    ├── main.py              # CLI 入口
    ├── parse_3dsg.py        # DSG JSON 解析器
    ├── build_documents.py   # 语义 chunks 生成
    ├── build_vector_db.py   # ChromaDB 向量库构建
    ├── query_rag.py         # 检索接口
    └── schemas.py           # 数据模型
```

## 支持的输入格式

1. **SPARK-DSG 格式**（Khronos / Hydra 原生格式）：`nodes` 列表 + `edges` 列表，节点带 `layer` 和 `attributes`
2. **Flat 格式**（task planner 风格）：`entities` + `relations` 列表

解析器自动检测格式，无需手动配置。

## 配置

`config.yaml` 中可以配置别名映射，用于查询扩展：

```yaml
aliases:
  chair: ["seat"]
  door: ["entrance", "exit"]
zh_aliases:
  椅子: ["chair", "seat"]
  门: ["door", "entrance"]
```

## 与 LLM Task Planner 集成

两个项目共用 miniconda base 环境，RAG 模块可作为 LLM task planner 的场景图检索前端：

```bash
# 1. 用 Khronos 真实输出构建 RAG
cd /home/nenlian/Masterthesis-wang/RAG/khronos_3dsg_rag
python src/main.py build --input /path/to/snapshots/00000/frontend/dsg.json --output outputs/

# 2. 在 LLM 项目中调用 RAG 检索
cd /home/nenlian/Masterthesis-wang/LLM-robot-taskplanning-chaining
python -c "
from query_rag import retrieve_scene_context
ctx = retrieve_scene_context('navigate to room 201')
for r in ctx:
    print(r['node_id'], r['text'])
"
```
