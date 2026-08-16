# LLM Intent Parser 语义测试报告

- 模型：gemma4:e4b（不修改 Intent Parser）
- 用例数：30

## 指标

| 指标 | 值 | 验收线 | 达标 |
|---|---|---|---|
| Schema validity | 100.00% | ≥98% | ✅ |
| Action accuracy | 96.67% | ≥90% | ✅ |
| Target role F1 | 0.939 | ≥85% | ✅ |
| Spurious-role rate | 0.00% | ≤5% | ✅ |
| node_type 提供率 | 0.00% | - | - |
| Location(room) 提取 | 66.67% | - | - |

## 说明

- 当前模型（gemma4:e4b）多数情况未在 `target.node_type` 输出 node_type，而是输出 surface text + label/aggregate 推测；联合测试语义层使用 unknown 模式（不依赖 node_type）
- room 约束出现在 `aggregate.location` 等位置，评估已覆盖