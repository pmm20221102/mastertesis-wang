# LLM + Semantic RAG + Graph Grounding 最终联合测评报告（v3，mistral）

- 日期：2026-08-06
- 模型：mistral:latest
- 数据：joint_cases_v3.jsonl（30 条，GT 由 canonical 自动生成，含 Room 约束）
- 流程：NL -> LLM Intent -> Semantic RAG Top-1 -> 确定性 Graph Grounding（predicted label + Room）
- 修复：无 Ground Truth 泄漏、禁止 full-instruction fallback、strict intent gate、GT node IDs 比较

## 验收对照（任务书第 25 节）

| 条件 | 值 | 达标 |
|---|---|---|
| Benchmark validator | PASS | ✅ |
| Intent Gate ≥80% | 96.67% | ✅ |
| Semantic Label Hit@1 ≥85% | 95.83% | ✅ |
| Semantic Recall@5 ≥90% | 100.00% | ✅ |
| Graph Grounding Exact ≥90% | 93.33% | ✅ |
| Full Joint Exact ≥80% | 93.33% | ✅ |
| Room-constrained Grounding ≥80% | 100.00% | ✅ |
| Ambiguous Candidate F1 ≥0.90 | 1.000 | ✅ |
| Set Exact Match ≥0.90 | 100.00% | ✅ |
| Invalid node ID Rate = 0 | 0（canonical 确定性） | ✅ |
| Failure Stage Coverage = 100% | ✅ | ✅ |

## 失败案例（2/30）

- `joint_v3_029`: `Find something employees can sit on duri` stage=`semantic_retrieval`
- `joint_v3_030`: `Find items people use for writing at wor` stage=`intent_quantifier`

## 失败 by stage

- {"semantic_retrieval": 1, "intent_quantifier": 1}

## 说明

- Graph Grounding 采用 canonical 确定性 label+room 解析（predicted label 驱动，无 GT 泄漏）
- resolve_entity 的 single 路径依赖 text 语义，与 Semantic RAG 分层后不适用，故用确定性 label 路径
- 本轮未修改 Semantic embedding / Retriever / threshold / Planner