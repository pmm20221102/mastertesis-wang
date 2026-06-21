# RAG Evaluation Subproject

验证 Khronos 场景图 RAG 系统检索正确率的评估框架。

## 快速开始

```bash
# 激活环境
conda activate llm-task-planner

# 生成评估用例
cd rag_eval
python scripts/build_eval_cases.py \
    --dataset office \
    --gt-dir data/groundtruth/office \
    --output data/eval_cases/office_cases.jsonl

# 运行评估
python scripts/run_eval.py --config configs/eval_office.yaml

# 查看结果
cat data/reports/eval_summary.md
```

## 项目结构

```
rag_eval/
├── configs/                      # 评估配置
│   ├── eval_office.yaml
│   ├── eval_apartment.yaml
│   └── eval_all.yaml
├── data/
│   ├── groundtruth/              # Groundtruth 数据
│   │   ├── office/office/        # office 数据集
│   │   └── apartment/apartment/  # apartment 数据集
│   ├── eval_cases/               # 生成的评估用例
│   └── reports/                  # 评估报告
├── rag_eval/                     # 核心模块
│   ├── schemas.py                # 数据类定义
│   ├── groundtruth_loader.py     # Groundtruth 解析
│   ├── query_generator.py        # 评估用例生成
│   ├── retrieval_adapter.py      # RAG 检索适配
│   ├── metrics.py                # 评估指标
│   ├── evaluator.py              # 评估执行器
│   └── report_writer.py          # 报告生成
└── scripts/
    ├── build_eval_cases.py       # 构建评估用例
    ├── run_eval.py               # 运行评估
    └── inspect_index.py          # 检查 RAG 索引
```

## Groundtruth 格式

`gt_dsg_consolidated.json`:
- 只有 layer 2 的 object nodes
- `attributes.name`: 物体符号（如 "O(0)", "O(1)"）
- `attributes.bounding_box`: 位置和尺寸
- `semantic_label`: 未标注（4294967295）

`gt_changes.csv`:
- `ObjectSymbol`: 物体符号
- `AppearedAt`: 出现时间（纳秒）
- `DisappearedAt`: 消失时间（0 = 未消失）

## V1 评估目标

| 类别 | 说明 | 查询类型 |
|------|------|----------|
| A. Index Coverage | groundtruth 物体是否在 RAG index 中 | N/A（统计） |
| B. Object Symbol Retrieval | 按物体符号检索 | "Retrieve O(370)" |
| C. Object Attribute Retrieval | 按属性检索 | "bounding box of O(370)" |
| D. Change Retrieval | 物体变化检索 | "Which objects disappeared?" |

## 评估指标

- **Hit@k**: top-k 中是否包含至少一个正确结果
- **MRR@k**: 第一个正确结果的排名倒数
- **Recall@k**: top-k 中包含多少比例的正确结果
- **Precision@k**: top-k 中正确结果的比例
- **Index Coverage**: groundtruth 物体在 RAG index 中的覆盖率

## 重要限制

### Groundtruth 与 RAG Index 的差异

1. **Groundtruth** (`gt_dsg_consolidated.json`):
   - 物体有 `name` 字段（如 "O(0)", "O(1)"）
   - 位置坐标系可能与 SLAM 输出不同

2. **RAG Index** (Khronos SLAM 输出):
   - 物体 `name` 字段为空
   - 使用 64 位整数作为 node_id
   - 没有 object symbol 信息

### Position-Based Matching

评估框架使用位置匹配（bounding box 中心距离）来关联 groundtruth 物体和 RAG index 中的物体。

默认阈值: 5.0 米（可配置）

### 查询类型适配

V1 生成的查询（如 "Retrieve O(0)"）可能无法直接匹配 RAG index 内容，因为:
- RAG index 没有 object symbol 信息
- 向量搜索基于语义相似度，不是精确匹配

**建议**: 后续版本应使用位置查询或重建包含 object symbol 的 RAG index。

## 配置文件

`configs/eval_office.yaml`:
```yaml
dataset: office
gt_dir: data/groundtruth/office
db_dir: ../RAG/khronos_3dsg_rag/outputs/khronos_office/vector_db
top_k: 10
seed: 42
output_dir: data/reports
```

## 检查 RAG 索引

```bash
python scripts/inspect_index.py --db-dir ../RAG/khronos_3dsg_rag/outputs/khronos_office/vector_db
```

## 输出报告

- `eval_summary.json`: 结构化结果
- `eval_summary.md`: Markdown 报告
- `per_case_results.jsonl`: 每个用例的详细结果
- `failures.jsonl`: 失败用例分析

## 后续改进

1. 重建包含 object symbol 的 RAG index
2. 使用位置查询替代 object symbol 查询
3. 添加更多 groundtruth 标注（room, category）
4. 支持多种检索模式比较（vector_only, hybrid, bm25）
