# LLM + 3DSG RAG 稳定收敛与简化修改任务书

## 0. 任务背景

当前系统已经具备较完整的：

```text
Natural Language
→ Intent Parser
→ Scene Graph Grounding
→ Task Planner
→ Validator
```

同时也已经开发了：

```text
Intent Benchmark
Paraphrase Benchmark
Planner Benchmark
Status Gate
Multi-step
role_refs / role_ids
Escort / Guide
Prefer / Avoid
Aggregate
Relation
Coordinate
```

但是该模块只是毕业论文的一部分。

当前目标不是继续开发通用 Benchmark 平台，也不是删除已有功能重新实现，而是：

> 保留现有代码和高级功能，将论文主流程收敛到一套规模适中、稳定、可复现、容易解释的状态。

---

# 1. 最终目标状态

系统主线保留：

```text
1. Oracle-Intent Grounding
2. LLM Intent Parsing
3. Oracle-Grounding Task Planning
4. 小规模 End-to-End Evaluation
```

正式量化范围限制为：

```text
Grounding：100 cases
Intent：60 cases
Planner：30 cases
End-to-End：20 cases
Paraphrase：20–30 cases，可选
模型：2 个
```

高级功能不删除，但暂时不进入主论文量化结果：

```text
Escort / Guide 多角色规划
Prefer constraint
Coordinate parsing
完整 480-case paraphrase
复杂 step-specific role binding
大规模多模型比较
```

这些功能统一标记为：

```text
experimental
```

---

# 2. 修改原则

Agent 必须遵循以下原则。

## 2.1 不大规模回滚代码

禁止：

```text
删除整个 v3/v4 Benchmark 系统
删除 role_refs / role_ids
删除 Escort / Guide
删除 Paraphrase generator
删除现有结果文件
恢复到不兼容当前 schema 的旧代码
```

允许：

```text
将高级功能移入 experimental 范围
从主 Benchmark 中排除不稳定 case
增加小型 final benchmark
修复影响最终指标的关键 Bug
```

---

## 2.2 不继续扩大系统

禁止新增：

```text
新的大型 Benchmark
新的复杂 schema
新的 Planner action 类型
新的模型适配层
新的公开 Benchmark 全量接入
新的自动 paraphrase 生成流水线
新的复杂指标系统
```

---

## 2.3 保留历史结果

现有文件不得覆盖。

旧 Benchmark 和结果移动或保留在：

```text
eval/archive/
eval/experimental/
```

最终主结果写入：

```text
eval/final/
```

---

# 3. 建议的稳定基线

## 3.1 Grounding 基线

保留当前已经通过的：

```text
grounding_benchmark_100_v3
```

以及当前 100/100 的 Oracle Intent Grounding 结果。

Grounding 模块保持冻结。

除非发现会导致现有 100 条回归失败的明确 Bug，否则不得修改：

```text
SceneGraphRAG retrieval
GroundingOrchestrator
candidate filtering
relation filtering
aggregate implementation
top_k
canonical graph
```

---

## 3.2 Intent 主线范围

正式支持并评价：

```text
locate
navigate
Room constraint
Place constraint
Relation
all
nearest
aggregate
source → destination
avoid
简单两步 locate → navigate
unsupported
```

暂不进入主量化结果：

```text
coordinate
escort
guide
复杂多角色 step
prefer
三步及以上复杂规划
```

功能代码保留，但标记：

```text
experimental
```

---

## 3.3 Planner 主线范围

正式支持并评价：

```text
locate_entity
navigate_to
source → destination
avoid constraint
两步 locate → navigate
```

暂不进入主量化结果：

```text
escort_to
guide_to
prefer
multi-step escort
多角色 role_refs 精确指标
```

---

# 4. 必须修复的关键问题

只修复以下会直接影响最终论文结果的问题。

---

## 4.1 修复 3 条 Intent action 冲突

当前类似：

```text
Find the chairs in room 0756.
```

Oracle 却是：

```text
navigate
```

处理方式：

```text
Find / Locate / Where → locate
Navigate / Go / Move → navigate
```

修正所有存在明显冲突的 case。

不需要重新生成全部 160 条。

---

## 4.2 Coordinate case 不进入最终测试

当前 coordinate instruction 只提供二维四舍五入坐标，但 Oracle 保存完整三维坐标。

最简单处理：

```text
保留原始 coordinate 功能和 case；
从 final_intent_60 中排除 coordinate cases；
不再为坐标功能继续扩展 evaluator。
```

原始 coordinate case 移入：

```text
eval/experimental/coordinate/
```

---

## 4.3 修复 multi-step entity comparison

当前 multi-step evaluator 不能可靠区分：

```text
place_1111
place_2222
```

修改 step comparison：

```python
每个 step 的 actor / target / source / destination
统一调用现有 _compare_entity_ref()
```

不要仅比较：

```text
label
role presence
```

必须至少检查：

```text
node_type
label
visible identifier / text
```

---

## 4.4 修复 final strict success 定义

最终论文使用的 Intent strict 指标只要求：

```text
schema valid
action correct
role set correct
每个 role 的 semantic identity 正确
relation / location 正确
quantifier / aggregate 正确
constraints 正确
multi-step action 和 role 正确
```

不要求：

```text
自然语言 surface text 完全逐字一致
```

保留单独的：

```text
surface_exact
```

但不进入主 strict success。

---

## 4.5 修复结果文件的版本标记

旧结果必须明确标记：

```text
benchmark_version
evaluator_version
model
temperature
case_count
completed / partial
```

不能将：

```text
v2 结果
```

作为：

```text
v4 final 结果
```

---

# 5. 保留但移出主评测的功能

以下功能不删除。

---

## 5.1 Escort / Guide

保留：

```text
schema
parser support
planner support
role_refs / role_ids
tests
experimental benchmark
```

但正式论文主表不报告：

```text
Escort accuracy
Guide accuracy
multi-role exact match
```

论文中写为：

> Multi-role escort and guide planning were implemented as exploratory extensions but were excluded from the main quantitative evaluation.

---

## 5.2 Prefer Constraint

保留：

```text
prefer shortest route
symbolic preference
相关 schema
```

但不进入 final_planner_30。

主评测只保留：

```text
avoid constraint
```

---

## 5.3 完整 480 条 Paraphrase

保留文件：

```text
intent_paraphrase_480_v3
```

移动或标记为：

```text
development / experimental
```

不再继续修复全部 480 条。

从中人工选择：

```text
20–30 条语义明确的 paraphrase
```

形成：

```text
final_paraphrase_24.jsonl
```

建议每类 4 条：

```text
locate / navigate
Room / Place
Relation
Aggregate
source → destination
multi-step
```

排除：

```text
coordinate
丢失步骤
丢失 constraint
语义不自然
```

---

## 5.4 Status Gate

保留为：

```text
Pipeline Status Propagation Contract Test
```

不作为 LLM Planner 主结果。

可以只在附录报告：

```text
ambiguous propagation
not_found propagation
unsupported propagation
```

---

# 6. 创建最终精简 Benchmark

不要重新随机生成。

从现有经过检查的 case 中筛选。

---

## 6.1 Final Intent 60

创建：

```text
eval/final/cases/final_intent_60.jsonl
eval/final/cases/final_intent_60.manifest.json
```

建议分布：

| 类别 | 数量 |
|---|---:|
| Locate / Navigate | 15 |
| Room / Place Constraint | 10 |
| Relation | 10 |
| Quantifier / Aggregate | 10 |
| Source → Destination | 5 |
| Avoid / Simple Multi-step | 5 |
| Unsupported | 5 |
| 总计 | 60 |

筛选要求：

```text
0 duplicate instruction
0 empty field
0 action conflict
0 coordinate case
0 escort / guide
0 prefer
Oracle 可以由 instruction 恢复
人工检查通过
```

---

## 6.2 Final Planner 30

创建：

```text
eval/final/cases/final_planner_30.jsonl
eval/final/cases/final_planner_30.manifest.json
```

建议分布：

| 类别 | 数量 |
|---|---:|
| Single-step Locate / Navigate | 15 |
| Source → Destination | 5 |
| Avoid Constraint | 5 |
| Locate → Navigate Multi-step | 5 |
| 总计 | 30 |

排除：

```text
escort
guide
prefer
multi-role
coordinate
三步以上任务
```

---

## 6.3 Final End-to-End 20

创建：

```text
eval/final/cases/final_e2e_20.jsonl
eval/final/cases/final_e2e_20.manifest.json
```

建议：

```text
简单任务                   10
Room / Place / Relation     5
Avoid / Multi-step          5
```

所有 case 必须：

```text
Grounding 事实存在于 canonical graph
Oracle Intent 正确
Expected Plan 正确
人工检查通过
```

---

## 6.4 Optional Paraphrase 24

创建：

```text
eval/final/cases/final_paraphrase_24.jsonl
```

仅用于小型鲁棒性实验。

不得作为主要 Benchmark。

---

# 7. 最终指标精简

---

## 7.1 Grounding

只保留：

```text
Pipeline status accuracy
Resolved target-ID accuracy
Set exact match
Relation exact
Aggregate accuracy
Failure count
```

---

## 7.2 Intent

只保留：

```text
Schema validity
Action accuracy
Role accuracy
Strict Intent accuracy
```

可附加：

```text
Error breakdown
```

不需要在论文主表中报告几十个细粒度字段。

---

## 7.3 Planner

只保留：

```text
Valid plan rate
Exact action sequence
Reference binding accuracy
Avoid constraint accuracy
Overall plan success
```

---

## 7.4 End-to-End

只保留：

```text
End-to-End success
Intent failure count
Grounding failure count
Planner failure count
```

---

# 8. 模型实验范围

只测试两个模型：

```text
主模型
小模型对照
```

建议：

```text
Gemma 4B
Qwen 0.5B / 1.5B
```

配置：

```text
temperature = 0
每个 case 跑 1 次
```

稳定性检查：

```text
只选 10–20 条代表 case
运行 3 repeats
```

不要对全部 case 做大量重复。

---

# 9. 最终目录建议

```text
eval/
├── final/
│   ├── cases/
│   │   ├── final_intent_60.jsonl
│   │   ├── final_planner_30.jsonl
│   │   ├── final_e2e_20.jsonl
│   │   └── final_paraphrase_24.jsonl
│   ├── manifests/
│   ├── results/
│   └── FINAL_EVALUATION_REPORT.md
│
├── experimental/
│   ├── coordinate/
│   ├── escort_guide/
│   ├── prefer/
│   ├── paraphrase_480/
│   └── status_gate/
│
└── archive/
    ├── v1/
    ├── v2/
    └── old_results/
```

只移动文件，不删除历史内容。

---

# 10. 测试要求

不需要建立庞大的新测试体系。

只保证以下测试通过：

```text
1. 现有 Grounding regression tests
2. Intent serialization tests
3. Wrong destination causes multi-step strict failure
4. final_intent_60 has exactly 60 valid unique cases
5. final_planner_30 has exactly 30 valid unique cases
6. final_e2e_20 references valid graph entities
7. runner can complete a mock evaluation
```

最终要求：

```text
pytest = 100% pass
```

---

# 11. Agent 禁止事项

本轮禁止：

```text
重新设计整个 Intent schema
重新设计整个 Planner
删除 role_refs / role_ids
继续修复全部 480 paraphrases
继续增加 Benchmark case
增加新的模型
接入完整公开 Benchmark
修改已冻结 Grounding 核心
增加新的复杂 metrics
```

---

# 12. Agent 最终交付

必须提交：

```text
1. 修改文件列表
2. final_intent_60.jsonl
3. final_planner_30.jsonl
4. final_e2e_20.jsonl
5. 可选 final_paraphrase_24.jsonl
6. 对应 manifest
7. 全部测试结果
8. 两个模型的完整结果
9. End-to-End 失败阶段统计
10. FINAL_EVALUATION_REPORT.md
```

---

# 13. 完成条件

满足以下条件后结束 LLM 测评开发：

```text
Grounding 100 条结果保留
Intent 60 条全部经过人工检查
Planner 30 条全部经过人工检查
End-to-End 20 条全部经过人工检查
两个模型运行完成
所有结果目录完整
pytest 全部通过
不存在已知会改变主结果的 evaluator Bug
```

不要求：

```text
Escort / Guide 完美
Coordinate 完美
Prefer 完美
480 条 Paraphrase 完美
公开 Benchmark 全量接入
所有模型完成
```

---

# 14. 论文定位

论文中将这一部分定位为：

> A modular evaluation of language understanding, scene-graph grounding, and task planning within the proposed semantic navigation system.

不要定位为：

```text
A new general-purpose embodied-agent benchmark
```

最终重点是说明：

```text
LLM 能否理解任务
Scene Graph 能否正确 Grounding
Planner 能否生成简单可执行任务
错误主要出现在哪个阶段
```

---

# 15. 最终执行指令

请基于当前 v4 代码进行稳定收敛修改。

不要大规模回滚，不要删除高级功能。

将：

```text
Grounding
基础 Intent
基础 Planner
简单 multi-step
avoid constraint
```

作为论文主线。

将：

```text
coordinate
escort / guide
prefer
完整 paraphrase
复杂 role binding
```

保留为 experimental。

完成精简后的：

```text
60 Intent
30 Planner
20 End-to-End
```

并运行两个模型。

完成后停止继续扩展本模块。
