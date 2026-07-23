# LLM 测试开发阶段执行计划

## 0. 总目标

当前 deterministic Grounding backend 视为基本稳定依赖。

下一阶段需要建立三个彼此隔离的测试层：

```text
Stage A：Natural Language → LLM Intent Parser
Stage B：Oracle Grounded Task → LLM Planner
Stage C：Natural Language → Intent → Grounding → Planner
```

必须先完成 Stage A，再开始 Stage B，最后运行 Stage C。

禁止一开始同时修改 Intent、Grounding 和 Planner，否则无法判断错误来自哪个阶段。

---

# Phase 0：建立冻结基线

## 0.1 保存当前 Grounding 基线

运行并保存：

```text
现有 pytest
现有 100-case oracle_intent evaluation
当前代码 commit/hash
canonical graph hash
benchmark hash
运行配置
```

生成：

```text
eval/results/grounding_baseline_v17/
├── run_manifest.json
├── summary.json
├── failures.jsonl
├── command.txt
└── environment.json
```

后续开发不得降低当前 Grounding 指标。

---

## 0.2 修复测试环境依赖

将：

```python
from ollama import Client
```

从 `main_rag.py` 顶层移除，改为：

```text
lazy import
或 dependency injection
```

要求：

```text
普通 unit tests 不安装 Ollama 也能运行
真实模型测试单独标记 integration / requires_ollama
```

验收：

```text
所有非 LLM integration tests 全部通过
不存在因为 ollama package 缺失导致的测试失败
```

---

## 0.3 完整序列化 TaskIntent

删除手工维护的不完整 `intent_to_dict()` 字段列表。

优先使用：

```python
intent.model_dump(mode="json")
```

必须保留：

```text
action
target
destination
source
constraints
locations
relations
relation.target_node_id
aggregate
aggregate.location
quantifier
steps
step-specific references
```

新增测试：

```text
test_intent_serialization_preserves_locations
test_intent_serialization_preserves_relations
test_intent_serialization_preserves_aggregate
test_intent_serialization_preserves_steps
```

---

## 0.4 本阶段禁止事项

Phase 0–1 中禁止：

```text
继续扩大 Grounding top_k
修改向量检索算法
修改 canonical graph 内容
修改 expected result 适配模型输出
放宽 evaluator 判定条件
将 LLM 输出错误归因给 Grounding
```

只有发现明确接口 Bug 时，才允许修改 Grounding，并必须增加回归测试。

---

# Phase 1：LLM Intent Parser Evaluation

## 1.1 测试边界

本阶段只测试：

```text
Natural-language instruction
        ↓
LLM Intent Parser
        ↓
Predicted TaskIntent
        ↓
与 Oracle TaskIntent 对比
```

本阶段不得运行：

```text
SceneGraphRAG
GroundingOrchestrator
LLM Planner
Task Compiler
```

Intent Parser 输出错误必须直接记录，不允许通过 Grounding 结果反向判断 Intent 是否正确。

---

## 1.2 建立独立 Intent Benchmark

不要直接把现有 Grounding benchmark 当作完整自然语言 benchmark。

建立两个测试层级。

### Tier A：Controlled Intent Benchmark

从 Grounding benchmark 中提取：

```text
instruction
oracle TaskIntent
case category
expected roles
```

生成：

```text
eval/cases/intent_benchmark_100_v1.jsonl
eval/cases/intent_benchmark_100_v1.manifest.json
```

Tier A 用于验证：

```text
schema
action
roles
locations
relations
quantifier
aggregate
multi-step structure
```

---

### Tier B：Natural-Language Paraphrase Benchmark

为每个 Oracle Intent 生成至少 3 种自然语言表达：

```text
标准表达
口语表达
省略或换序表达
```

建议总量：

```text
100 intents × 3 paraphrases = 300 instructions
```

内部 Scene Graph 完整 ID 不应直接泄漏到普通自然语言中。

例如不要主要依赖：

```text
Place 1c4a95...
Room 0753 full node ID
```

自然语言优先使用：

```text
room number
room label
object description
relative location
user-visible alias
```

每条 paraphrase 必须人工或规则验证，确保没有改变原始语义。

生成：

```text
eval/cases/intent_paraphrase_300_v1.jsonl
eval/cases/intent_paraphrase_300_v1.manifest.json
```

---

## 1.3 Benchmark 数据结构

每条 case 至少包含：

```json
{
  "case_id": "intent_v1_001",
  "source_case_id": "v3_obj_nav_001",
  "instruction": "...",
  "language_style": "controlled | standard | colloquial",
  "category": "object_navigation",
  "oracle_intent": {},
  "required_roles": ["target"],
  "tags": []
}
```

必须保存 Oracle Intent 的完整结构，不得只保存 status 或 target ID。

所有列表必须稳定排序。

同一个：

```text
source benchmark
generator version
seed
```

必须生成 byte-identical 文件。

---

## 1.4 修改 Intent Parser Prompt

Prompt 必须完整说明当前 schema，特别是：

```text
locations[]
LocationConstraint.node_type
LocationConstraint.text
relations[]
RelationConstraint.relation
RelationConstraint.target_text
aggregate.operation
aggregate.location
quantifier
steps[]
```

普通自然语言解析时：

```text
LLM 不应猜测内部 Scene Graph node ID
```

因此默认输出：

```text
text
label
node_type
relation type
aggregate operation
```

内部 ID 应由 Grounding resolver 后续生成。

只有输入文本明确包含可见 ID 时，才允许保存 ID。

---

## 1.5 必须加入 Few-shot 示例

至少覆盖：

```text
单目标导航
Room location constraint
Place location constraint
Place + Room intersection
near relation
connected_to relation
all / nearest quantifier
count_in_location
max_count / min_count
source → destination
multi-step instruction
unsupported instruction
```

Few-shot 不能直接复制 benchmark 测试样本。

---

## 1.6 Intent 指标

实现独立 evaluator，不使用 LLM 评分。

必须报告：

```text
Schema validity
Action accuracy
Role presence Precision
Role presence Recall
Role presence F1
Spurious-role rate
Target label accuracy
Destination label accuracy
Source label accuracy
node_type accuracy
Location extraction Precision / Recall / F1
Location node_type accuracy
Relation type accuracy
Relation target-text accuracy
Relation extraction F1
Quantifier accuracy
Aggregate operation accuracy
Aggregate location accuracy
Step count accuracy
Ordered step-action accuracy
Multi-step exact match
Strict Intent exact match
```

同时输出：

```text
per-category metrics
per-language-style metrics
per-model metrics
per-repeat stability
```

---

## 1.7 Normalization 规则

Evaluator 可以执行有限且明确的标准化：

```text
lowercase
首尾空格
连续空格
连字符与下划线统一
已声明 alias mapping
```

禁止：

```text
使用 LLM 判断两个 Intent 是否等价
根据模型输出动态修改 Oracle
把错误 label 自动替换成最接近 canonical label
忽略额外生成的 role
```

任何自动 repair 必须同时保存：

```text
raw_output
parsed_output
repair_applied
repair_reason
```

并分别报告：

```text
raw schema validity
post-repair schema validity
```

---

## 1.8 模型运行配置

至少测试：

```text
qwen2.5:0.5b
qwen2.5:1.5b 或同等级模型
另一个不同模型系列的小模型
```

每个模型至少运行：

```text
controlled benchmark：3 repeats
paraphrase benchmark：3 repeats
```

必须记录：

```text
model
model digest/version
temperature
seed
system prompt hash
few-shot hash
timeout
retry count
raw response
latency
token usage（若可获得）
```

建议分别运行：

```text
temperature = 0：确定性基线
temperature > 0：稳定性测试
```

不得把不同 temperature 的结果混成一个总指标。

---

## 1.9 Intent 失败分类

每个失败必须归入一个主类别：

```text
invalid_json
schema_error
wrong_action
missing_role
spurious_role
wrong_label
wrong_node_type
location_error
relation_error
quantifier_error
aggregate_error
step_decomposition_error
timeout
model_runtime_error
```

生成：

```text
intent_failures.jsonl
intent_error_breakdown.json
intent_confusion_matrix.json
```

每个 failure 保存：

```text
instruction
oracle_intent
raw_model_output
parsed_intent
field-level differences
failure category
```

---

## 1.10 Intent Evaluation CLI

实现统一命令入口，例如：

```bash
python -m eval.run_intent_eval \
  --benchmark eval/cases/intent_benchmark_100_v1.jsonl \
  --model qwen2.5:1.5b \
  --repeats 3 \
  --temperature 0 \
  --output eval/results/intent_qwen_1_5b_v1
```

Paraphrase benchmark 使用相同 runner。

Runner 必须支持：

```text
单 case 调试
按 category 过滤
断点继续
失败重跑
mock model
真实 Ollama model
```

---

## 1.11 Intent Evaluation 输出

结果目录必须包含：

```text
intent_results/
├── run_manifest.json
├── config.json
├── summary.json
├── per_category_metrics.json
├── per_field_metrics.json
├── repeat_stability.json
├── predictions.jsonl
├── raw_outputs.jsonl
├── failures.jsonl
├── error_breakdown.json
├── prompt_snapshot.txt
├── benchmark_manifest_snapshot.json
└── report.md
```

---

## 1.12 Phase 1 测试要求

新增测试：

```text
test_intent_benchmark_schema
test_intent_benchmark_reproducible_bytes
test_intent_evaluator_exact_match
test_intent_evaluator_missing_role
test_intent_evaluator_spurious_role
test_intent_evaluator_location_f1
test_intent_evaluator_relation_f1
test_intent_evaluator_quantifier
test_intent_evaluator_aggregate
test_intent_evaluator_multi_step
test_raw_and_repaired_metrics_separated
test_run_manifest_complete
test_mock_intent_eval
```

所有 evaluator 测试必须使用人工构造的小样本，确保：

```text
正确预测得到 1.0
错误预测得到预期扣分
额外 role 会被处罚
字段缺失不会被忽略
```

---

# Phase 1 完成条件

只有满足以下条件后，才允许进入 Planner 测试：

```text
[ ] Intent benchmark schema 已冻结
[ ] Controlled benchmark 100 cases 可运行
[ ] Natural-language paraphrase benchmark 可运行
[ ] Evaluator 有完整 field-level metrics
[ ] 非 Ollama 环境 unit tests 全部通过
[ ] 至少两个模型完成 baseline
[ ] repeats 与稳定性结果已生成
[ ] 所有失败都有明确分类
[ ] 结果目录包含完整 provenance
```

模型性能暂不通过修改 benchmark 来提高。

第一轮 baseline 完成后，根据 error breakdown 再决定是否修改：

```text
Prompt
Few-shot
Schema description
Parser repair
Model size
```

每次修改必须使用新 experiment ID，并保留修改前结果。

---

# Phase 2：Oracle-Grounding Planner Evaluation

Phase 1 完成后再执行本阶段。

输入必须是：

```text
Oracle Grounded Task
```

不能使用 LLM Intent Parser 输出，否则会混入上游误差。

建立：

```text
planning_benchmark_50_v1.jsonl
```

覆盖：

```text
single-step navigation
locate + navigate
source → destination
escort
avoid constraint
ambiguous
not_found
unsupported
multi-step
```

重点修复和测试：

```text
step_0_target
step_1_destination
step_ref_to_id
Planner prompt available refs
Repair prompt available refs
role-to-ID validation
multi-step required references
```

主要指标：

```text
Plan schema validity
Exact action sequence
Ordered action coverage
target_ref accuracy
step-specific target_ref accuracy
executor accuracy
constraint accuracy
role-ID correctness
validator pass rate
strict plan exact match
```

---

# Phase 3：End-to-End Evaluation

只有 Intent 和 Planner 独立测试完成后，才运行：

```text
Natural Language
→ LLM Intent
→ Grounding
→ LLM Planner
→ Validator
```

至少建立：

```text
50 cases
```

每条结果必须记录：

```text
intent_success
grounding_success
planning_success
validation_success
first_failure_stage
```

失败阶段只能是：

```text
intent
grounding
planning
validation
runtime
```

最终必须同时报告：

```text
Intent-only accuracy
Oracle-intent Grounding accuracy
Oracle-grounding Planner accuracy
End-to-end strict accuracy
```

禁止只报告一个总成功率。

---

# 本轮 Agent 实际执行范围

本轮只完成：

```text
Phase 0
Phase 1
```

暂不实现：

```text
Phase 2 Planner benchmark
Phase 3 End-to-End benchmark
跨数据集测试
新的 Grounding 功能
```

完成后提交：

```text
1. 修改文件列表
2. 架构说明
3. Benchmark manifest
4. 全部测试结果
5. 至少两个模型的 Intent baseline
6. Field-level metrics
7. Error taxonomy
8. 尚未解决的问题
9. 下一轮 Planner 开发建议
```

不要只给总结报告，必须提交实际代码、benchmark、测试和可复现运行命令。
