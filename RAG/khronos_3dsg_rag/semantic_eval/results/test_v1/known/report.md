# 语义向量检索测评报告（Test v1，held-out）

- 日期：2026-08-05
- 数据源：Office GT（`office_launch_dense`，3096 节点 / 12335 边）
- 向量库：`outputs/office_dense/vector_db`（6201 文档）
- Embedding model：`paraphrase-multilingual-MiniLM-L12-v2`
- node-type 模式：`known`
- not_found 阈值：`0.35`（在 dev 上确定）

## Label-level 指标

| 指标 | 值 | 验收线 | 达标 |
|---|---|---|---|
| 测试样例数 | 23（matched 15 + not_found 8）| - | - |
| Label Hit@1 | 93.33% | ≥80% | ✅ |
| Label Recall@3 | 93.33% | - | - |
| Label Recall@5 | 93.33% | ≥90% | ✅ |
| Label MRR | 0.933 | - | - |
| Not-found Precision / Recall / F1 | 0.89 / 1.00 / 0.94 | - | - |
| 不存在 node ID 率 | 0.0 | =0 | ✅ |

## 延迟

- Cold-start：`7630 ms`（首次加载 embedding 模型）
- Warm mean / median / p95：`31` / `20` / `56` ms

## 数据集说明

- Development set：`semantic_dev_cases.jsonl`（33 条，含 LABEL_DESCRIPTIONS 增强词，仅用于调试 / 阈值选择）
- Test set：`semantic_test_cases_v1.jsonl`（23 条，held-out）
- Test query 不出现于 LABEL_DESCRIPTIONS 与 alias 表（见 `validate_semantic_cases.py`）
- 真实 canonical label：`chairs, large_office_objects, large_static_wall_furniture, signs, small_office_objects`
- 每个 label 的测试数量：`{"chairs": 3, "signs": 3, "small_office_objects": 3, "large_static_wall_furniture": 3, "large_office_objects": 3}`
- 含 hard negatives（语义接近但数据库不存在的类别）
- 约束：不编造 label、不修改 expected 迎合结果、不使用 18 位 node ID

## 失败案例

- `semantic_t_012`: `built-in storage in the office` (expected=large_static_wall_furniture)

## 当前 Office 数据集限制

- 语义类别较少：5 个有意义的 object canonical label（`small_office_objects`、`large_static_wall_furniture`、`chairs`、`signs`、`large_office_objects`），另有 rooms 的 `unknown` 不参与语义匹配
- `large_office_objects` / `small_office_objects` 本体粒度粗，个别表达（如 `table`/`desk`）存在歧义，已在 dev 中标记；test 使用更明确的未见表达
- 因此不强行凑 100 条；保持 20-40 条有效测试