# LLM + Semantic RAG + Graph Grounding 联合测试报告

- 模型：gemma4:e4b（不修改 LLM / Intent Parser / Graph Grounding）
- 用例数：30

## 分层成功率

| 层 | 成功率 | 验收线 | 达标 |
|---|---|---|---|
| Intent | 100.00% | - | - |
| Semantic Retrieval | 96.67% | ≥85% Hit@1 | - |
| Graph Grounding | 96.67% | - | - |
| Final node ID 正确 | 96.67% | - | - |
| **联合成功** | **96.67%** | ≥80-85% | ✅ |

## Failure by Stage

- {"semantic_retrieval": 1}（failure stage coverage 100%）

## 失败案例

- `joint_011`: `Find storage attached to the structure.` stage=`semantic_retrieval` exp=large_static_wall_furniture

## 数据限制

- canonical room 节点无编号（label=unknown），room 约束（如 room 0752）无法在 grounding 层精确匹配，作为数据限制记录
- Office ontology 仅 5 个有意义的 object label，类别少