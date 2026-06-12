请帮我实现一个轻量级 Python 项目，负责把 Khronos 输出的 `3DSG.json` / `dsg.json` 转换成一个本地 RAG 知识库。

当前只需要完成第一步：**从 3DSG JSON 生成 RAG，并提供基本检索接口**。

不需要实现完整 LLM task planner，不需要接 ROS2，也不需要接导航模块。这个项目只负责：

```text
3DSG JSON
    ↓
解析节点、边和空间关系
    ↓
生成语义化文本 chunks
    ↓
建立本地向量数据库
    ↓
支持关键词或自然语言查询
```

## 1. 输入

输入文件路径示例：

```text
data/3DSG.json
```

或者：

```text
data/dsg.json
```

JSON 来自 Khronos / Hydra / Dynamic Scene Graph。真实结构可能比较复杂，所以代码需要尽量鲁棒，不要假设字段完全固定。

请尽量自动解析以下信息：

* node id
* node type / layer，例如 room, place, object, agent, building, floor, region
* semantic label / class name
* name / attributes
* position / centroid / bounding box
* parent-child relation
* spatial relation，例如 inside, near, connected_to, adjacent_to, contains
* timestamp / last seen time，如果存在
* edge relation，如果存在

如果某些字段不存在，不要报错，使用默认值或者跳过。

## 2. 输出

生成两个主要输出：

### A. RAG 文本文档

输出路径：

```text
outputs/rag_documents.jsonl
```

每一行是一个可检索文档，例如：

```json
{
  "doc_id": "object_123",
  "text": "Object object_123 is a chair. It is located in room_204. It is near table_05. Position: x=1.2, y=3.4, z=0.0.",
  "metadata": {
    "node_id": "object_123",
    "node_type": "object",
    "label": "chair",
    "room": "room_204"
  }
}
```

### B. 本地向量数据库

输出路径：

```text
outputs/vector_db/
```

优先使用：

```text
ChromaDB + sentence-transformers
```

embedding model 建议使用：

```text
paraphrase-multilingual-MiniLM-L12-v2
```

这样可以初步支持中英文关键词查询。

## 3. 检索功能

RAG 应该支持类似下面的查询：

```text
chair
table
door
corridor
room 204
object near table
objects in room
navigation target near entrance
where is object_123
```

返回结果使用 JSON 格式，方便后续接 LLM task planner。

示例输出：

```json
[
  {
    "rank": 1,
    "score": 0.82,
    "node_id": "object_123",
    "node_type": "object",
    "label": "chair",
    "text": "Object object_123 is a chair. It is located in room_204 and near table_05.",
    "metadata": {
      "room": "room_204",
      "related_nodes": ["table_05"]
    }
  }
]
```

## 4. 项目结构

请按照下面的结构实现：

```text
khronos_3dsg_rag/
├── README.md
├── requirements.txt
├── config.yaml
├── data/
│   └── 3DSG.json
├── outputs/
│   ├── rag_documents.jsonl
│   └── vector_db/
└── src/
    ├── main.py
    ├── parse_3dsg.py
    ├── build_documents.py
    ├── build_vector_db.py
    ├── query_rag.py
    └── schemas.py
```

## 5. 命令行功能

### 构建 RAG

实现命令：

```bash
python src/main.py build --input data/3DSG.json --output outputs/
```

这个命令需要完成：

1. 读取 3DSG JSON；
2. 解析 nodes；
3. 解析 edges / relations；
4. 将结构化 3DSG 信息转换成自然语言 document chunks；
5. 保存 `outputs/rag_documents.jsonl`；
6. 建立本地向量数据库；
7. 保存到 `outputs/vector_db/`。

### 查询 RAG

实现命令：

```bash
python src/main.py query --db outputs/vector_db --query "chair near table" --top-k 5
```

输出 top-k 相关结果，格式为 JSON。

## 6. 文档 chunk 生成规则

不要把整个 3DSG 原封不动塞进向量数据库，而是生成语义清晰的小 chunks。

至少生成以下几类 chunk：

### Entity chunk

每个 node 生成一个文档，例如：

```text
Object object_123 is a chair. Position: x=1.2, y=3.4, z=0.0.
```

### Region / room / place chunk

如果存在 room / place / region 类型节点，则生成聚合文档，例如：

```text
Room room_204 contains object_123, object_124 and place_08.
```

### Relation chunk

重要关系单独生成文档，例如：

```text
object_123 is near object_124.
object_123 is inside room_204.
room_204 is connected to corridor_02.
```

### Alias / keyword chunk

建立一个简单的 alias 字典，用于提升查询效果。先写成静态配置即可，不需要复杂 NLP。

示例：

```yaml
aliases:
  door: ["entrance", "exit", "gate"]
  corridor: ["hallway", "passage"]
  room: ["area", "space"]
  table: ["desk"]
  chair: ["seat"]
  person: ["human", "agent"]
```

可以额外支持一些中文关键词：

```yaml
zh_aliases:
  门: ["door", "entrance", "exit"]
  走廊: ["corridor", "hallway"]
  房间: ["room"]
  桌子: ["table", "desk"]
  椅子: ["chair", "seat"]
  人: ["person", "human", "agent"]
```

## 7. 技术要求

* 使用 Python。
* 使用 ChromaDB 作为默认向量数据库。
* 使用 sentence-transformers 作为 embedding。
* 代码保持简单、清晰、模块化。
* 不要过度工程化。
* 对异常 JSON 结构要有容错。
* 如果输入 JSON 缺少某些字段，不要崩溃。
* README 中写清楚安装、build 和 query 的用法。
* 提供一个最小 mock 3DSG JSON，方便没有真实数据时测试。

## 8. 后续接口预留

请预留一个函数，方便后续 LLM task planner 调用：

```python
def retrieve_scene_context(query: str, top_k: int = 5) -> list[dict]:
    """
    Retrieve relevant scene context from the 3DSG-based RAG database.
    This function only returns retrieved context.
    It does not generate a task plan.
    """
```

这个函数只负责返回检索到的 scene context，不要生成任务步骤。

## 9. 当前阶段不要做的内容

请不要实现：

* ROS2 接口
* Nav2 接口
* 机器人真实导航
* 动态避障
* 完整任务规划器
* LLM agent 框架
* 多轮对话系统
* 复杂图数据库
* Web UI

当前只需要实现一个最小可运行版本：

```text
read 3DSG JSON → build semantic chunks → build vector DB → query relevant scene context
```

请先完成这个最小闭环，保证代码可以直接运行。
