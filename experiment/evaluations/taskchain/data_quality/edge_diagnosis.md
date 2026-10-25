# Edge Diagnosis Report

## 数据来源

| 数据源 | 节点数 | 边数 |
|--------|--------|------|
| 原始 DSG (dsg.json) | 402 | 288 |
| RAG Documents (rag_documents.jsonl) | 1332 | 4675 |
| Canonical Graph (canonical_graph.json) | 1332 | 0 |

## 诊断结论

### 1. 原始 DSG 是否有 edges

**是**。原始 DSG 有 288 条 edges，全部在 layer 2 (objects) 之间。

### 2. 边在哪里丢失

**Canonical graph 从 ChromaDB metadata 重建时丢失。**

原因：
- `build_documents.py` 截断 `parent_ids[:5]`, `child_ids[:10]`, `neighbor_ids[:10]`
- `canonical_index.py` 从 ChromaDB metadata 重建 canonical graph
- ChromaDB metadata 中的 edges 被截断或丢失

### 3. RAG Documents 的 4675 条 relations

这些是 places 之间的 `connected_to` 关系，不是原始 DSG 的 edges。

原始 DSG 的 288 条 edges 是 objects 之间的关系。

### 4. 修复方案

**从原始 DSG 直接导出 canonical graph**，不经过 ChromaDB metadata。

```text
parse raw DSG (dsg.json)
    ├── canonical_graph.json (完整图，不截断)
    ├── rag_documents.jsonl (用于 embedding，可截断)
    └── vector_db
```

### 5. 原始 DSG edge 结构

```json
{
  "source": 6989586621679009792,
  "target": 6989586621679009793,
  "info": {
    "type": "EdgeAttributes",
    "weight": 1.0
  }
}
```

所有 edges 都在 layer 2 (objects) 之间，没有 hierarchy edges。

### 6. 数据限制

原始 DSG 只有 layer 2 (objects)，没有：
- Room nodes
- Place nodes  
- Building nodes
- Hierarchy edges (Room → Place → Object)

这意味着 **Room/Place/Object hierarchy 在当前数据集中不存在**。

因此：
- Room-constrained queries 无法测试
- Place topology 无法测试
- 只能测试 object-level queries

### 7. 建议

生成受限 benchmark，只测试当前数据真实支持的能力：
- Object label ambiguity
- Set query
- Exact ID sanity
- Not-found
- Object navigation

**不得在数据不支持时测试 Room-constrained Grounding。**
