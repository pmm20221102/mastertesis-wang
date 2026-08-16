# Semantic Eval — 独立语义向量检索测评（v2）

本目录用于独立评估 RAG 的 ChromaDB 向量语义检索（不调用 LLM / Planner）。

## 文件结构

```
semantic_eval/
├── SEMANTIC_VECTOR_STATUS.md     # 向量库状态确认
├── semantic_dev_cases.jsonl      # Development set（33 条，含 LABEL_DESCRIPTIONS 增强词）
├── semantic_test_cases_v1.jsonl  # Held-out Test set（23 条，未见表达 + hard negatives）
├── validate_semantic_cases.py    # 用例校验（含 test 不重合检查）
├── run_semantic_eval.py          # 测评（dev/test + known/unknown + latency + report）
├── test_semantic_retriever.py    # 检索接口行为测试
├── results/                      # 结果（dev/ + test_v1/ × known/unknown）
└── README.md
```

## 完整复现步骤

依赖：Python（`llm-task-planner` conda env）、`sentence-transformers`、`chromadb`、`pyyaml`。

```bash
cd RAG/khronos_3dsg_rag   # 项目工作区根目录

# 1. 构建向量库（需 GT office DSG）
python src/main.py build --input <GT_office_dsg.json> --output outputs/office_dense
python src/export_canonical_graph.py <GT_office_dsg.json> -o outputs/office_dense

# 2. 校验用例
python semantic_eval/validate_semantic_cases.py semantic_eval/semantic_dev_cases.jsonl
python semantic_eval/validate_semantic_cases.py semantic_eval/semantic_test_cases_v1.jsonl

# 3. 运行测评（dev + test，known/unknown 双模式）
python semantic_eval/run_semantic_eval.py --mode all --node-type-mode both --threshold 0.35

# 4. 接口行为测试
python semantic_eval/test_semantic_retriever.py
```

## 依赖代码（必须随包一起提供）

- `src/semantic_retriever.py` —— 语义检索接口（`search_semantic` 返回 `SemanticSearchResult`）
- `src/build_vector_db.py` —— 加载 ChromaDB（`load_vector_db` / `COLLECTION_NAME`）
- `src/build_documents.py` —— 文档构建（含 LABEL_DESCRIPTIONS）
- `src/parse_3dsg.py`、`schemas.py`、`query_rag.py` —— 解析与查询依赖
- `config.yaml`、`requirements.txt`

## 测评内容

- 自然语言短语 → ChromaDB 向量检索 → 语义最接近的 canonical label / 实体候选
- 指标：Label Hit@1、Label Recall@3/5、Label MRR、Not-found P/R/F1、cold/warm latency
- 数据源：Office GT（`outputs/office_dense/vector_db`，6201 文档）
- Embedding：`paraphrase-multilingual-MiniLM-L12-v2`

## 结果（Held-out Test v1）

| 指标 | known | unknown |
|---|---|---|
| Label Hit@1 | 93.3% | 93.3% |
| Label Recall@5 | 93.3% | 93.3% |
| Label MRR | 0.933 | 0.933 |
| Not-found F1 | 0.94 | 0.94 |
| 非法 node ID 率 | 0 | 0 |

## 数据集说明

- Development：33 条（含增强词，仅用于调试 / 阈值选择）
- Test：23 条（15 matched + 6 hard negatives + 2 not_found；query 不出现于 LABEL_DESCRIPTIONS/alias）
- 真实 canonical label：`small_office_objects`、`large_static_wall_furniture`、`chairs`、`signs`、`large_office_objects`（另有 rooms 的 `unknown` 不参与）
- 限制：Office ontology 类别少，不强行凑 100 条
