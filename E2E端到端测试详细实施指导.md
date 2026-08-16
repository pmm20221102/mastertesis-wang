# LLM + Semantic RAG + Graph Grounding + Simple Planner
# End-to-End（E2E）测试实施指导

---

# 0. 本阶段目标

当前已经完成：

```text
Intent + Semantic RAG + Graph Grounding 联合测试
Simple Planner v3 独立测试
Deterministic Compiler
Plan Validator
```

当前 Planner v3 结果：

```text
granite4.1:3b
Strict Symbolic Plan Success = 100%
Final Validation Pass        = 100%

mistral:latest
Strict Symbolic Plan Success ≈ 98.3%
Final Validation Pass        ≈ 98.3%
```

因此可以进入 End-to-End 测试。

本阶段只回答一个问题：

> 用户给出一条自然语言任务后，整个系统能否在不使用任何 Oracle 中间答案的情况下，生成正确、安全、可执行的最终计划？

完整流程：

```text
Natural Language
→ LLM Intent Parser
→ Semantic Vector RAG
→ Graph Grounding
→ Simple Planner
→ Deterministic Compiler
→ Plan Validator
→ Final TaskPlan
```

---

# 1. 本阶段必须保持简单

本轮 E2E 不增加新功能，不扩大系统边界。

不要加入：

```text
走廊尽头推理
房间功能推断
动态物体重定位
复杂 Place topology
三个以上目标的长任务链
真实 Nav2 执行
跨数据集实验
```

本轮只测试当前已经实现并单独验证过的能力：

```text
单目标导航
Room 约束导航
明确 source → destination
escort
avoid / prefer
ambiguous
not_found
unsupported
简单两步任务
```

---

# 2. 开始前必须冻结的组件

在 E2E Test 运行前，记录以下文件的 Hash：

```text
Intent Parser Prompt
Intent schema
Semantic Retriever 配置
Embedding model
Vector DB
Canonical Graph
Graph Grounding API
Simple Planner Prompt
SimplePlan schema
Deterministic Compiler
Plan Validator
E2E Benchmark
E2E Evaluator
```

E2E 正式 Test 开始后：

```text
不得根据 Test 失败修改 Prompt
不得修改 Semantic threshold
不得修改 Ground Truth
不得删除困难 case
不得修改 Planner schema
```

只允许在 Dev 集上修复接口 Bug 和评分器 Bug。

---

# 3. 模型选择

第一轮只测试两个模型：

```text
主模型：
granite4.1:3b

对照模型：
mistral:latest
```

原因：

```text
Granite Planner v3 = 100%
Granite 延迟更低

Mistral Joint Grounding 更稳定
Mistral Planner v3 ≈ 98.3%
```

不要重新运行全部七个模型。

统一配置：

```text
temperature = 0
repeats = 3
```

如果模型有特殊 thinking 模式：

```text
必须关闭 thinking
确保 content 中返回结构化结果
```

---

# 4. E2E Benchmark 分为 Dev 和 Test

## 4.1 Dev Set

建议：

```text
15–20 条
```

用途：

```text
调试模块连接
修复字段传递
修复评分器
验证 failure_stage
```

Dev 可以重复运行和修改。

---

## 4.2 Frozen Test Set

建议：

```text
50 条
```

用途：

```text
正式论文结果
模型对比
最终失败分析
```

Test 冻结后不得调参。

Dev 与 Test：

```text
任务类别可以相同
自然语言表达不得重复
Ground Truth case 不得重复
```

---

# 5. 推荐的 50 条 E2E Test 分布

|类别|数量|
|---|---:|
|single_navigate|12|
|room_constrained_navigate|8|
|ordered_source_destination|8|
|escort|6|
|navigate_with_avoid|4|
|navigate_with_prefer|4|
|ambiguous_gate|3|
|not_found_gate|3|
|unsupported_gate|2|
|总计|50|

暂时不加入当前已删除的复杂类别：

```text
single_locate
locate_then_navigate
不明确的 multi_step
step-specific references
复杂 relation / aggregate planning
```

---

# 6. 各类别设计

## 6.1 single_navigate：12 条

用户指令：

```text
Go to something people can sit on.
Navigate to the wall-mounted storage unit.
Go to the large office machine.
```

完整链：

```text
Intent:
action = navigate

Semantic RAG:
surface target → canonical label

Graph Grounding:
如果多个实例 → ambiguous
如果唯一实例 → resolved

Planner:
navigate_to target

Compiler:
ref → real node ID / navigation Place

Validator:
PASS
```

为了生成可执行计划，single_navigate 应选择：

```text
通过 Room 约束或其他已实现约束后唯一的目标
```

无约束且多个候选时应放入 ambiguous 类别，而不是 single_navigate。

---

## 6.2 room_constrained_navigate：8 条

例如：

```text
Go to the chair in room 0756.
Navigate to the sign in room 0756.
Go to the large office machine in room 0764.
```

要求：

```text
LLM 正确提取 Room
Semantic RAG 正确映射 label
Graph Grounding 找到 Room 内唯一实体
Planner 输出 navigate_to target
Compiler 绑定正确 ID
```

---

## 6.3 ordered_source_destination：8 条

Instruction 必须明确：

```text
First go to room 0752, then go to room 0756.
```

不要写模糊的：

```text
Navigate from 0752 to 0756.
```

Expected symbolic plan：

```json
{
  "status": "valid",
  "steps": [
    {"action": "navigate_to", "ref": "source"},
    {"action": "navigate_to", "ref": "destination"}
  ]
}
```

---

## 6.4 escort：6 条

推荐使用高层宏动作。

Instruction：

```text
Escort the person from room 0752 to room 0756.
```

Expected symbolic plan：

```json
{
  "status": "valid",
  "steps": [
    {"action": "escort_to", "ref": "destination"}
  ]
}
```

Compiler 自动绑定：

```text
actor
source
destination
executor
```

不要让 LLM 输出 `role_refs` 或内部 ID。

---

## 6.5 navigate_with_avoid：4 条

Instruction：

```text
Navigate to the target while avoiding room 0752.
```

LLM 只需输出：

```json
{
  "steps": [
    {"action": "navigate_to", "ref": "target"}
  ]
}
```

Intent / Grounding 保存 avoid Room。

Compiler 自动附加真实 avoid Room ID。

不要让 Planner 输出 constraint reference。

---

## 6.6 navigate_with_prefer：4 条

例如：

```text
Navigate to the target and prefer the shortest route.
```

`shortest_route` 是：

```text
routing policy
```

不是 graph node ID。

Compiler 自动附加：

```json
{
  "routing_policy": "shortest_path"
}
```

---

## 6.7 ambiguous_gate：3 条

例如：

```text
Go to a chair.
```

场景中存在多个 chair。

Expected：

```text
Grounding status = ambiguous
Final pipeline status = blocked / needs_clarification
No executable plan
```

Planner 不应被调用，或只返回：

```text
ambiguous
```

不能任意选择 ID。

---

## 6.8 not_found_gate：3 条

查询当前 ontology 不支持的明确概念。

Expected：

```text
Semantic / Grounding = not_found
No executable plan
No node ID
```

---

## 6.9 unsupported_gate：2 条

使用机器人当前没有 executor 的任务。

Expected：

```text
unsupported
No executable plan
```

---

# 7. Ground Truth 设计

每条 E2E case 必须包含：

```text
自然语言 instruction
Oracle Intent
Expected Semantic Label
Expected Grounding
Expected Symbolic Plan
Expected Compiled Plan
Expected Final Status
```

但 Oracle 字段只能用于评分。

推荐 schema：

```json
{
  "case_id": "e2e_001",
  "category": "room_constrained_navigate",
  "instruction": "Go to the chair in room 0756.",

  "oracle_intent": {
    "action": "navigate",
    "target": {
      "text": "chair",
      "node_type": "object",
      "quantifier": "one",
      "locations": [
        {
          "node_type": "room",
          "text": "0756",
          "node_id": "FULL_ROOM_ID"
        }
      ]
    }
  },

  "expected_semantic": {
    "status": "matched",
    "canonical_label": "chairs"
  },

  "expected_grounding": {
    "status": "resolved",
    "node_ids": ["OBJECT_ID"],
    "candidate_node_ids": ["OBJECT_ID"]
  },

  "expected_symbolic_plan": {
    "status": "valid",
    "steps": [
      {
        "action": "navigate_to",
        "ref": "target"
      }
    ]
  },

  "expected_compiled_plan": {
    "status": "valid",
    "steps": [
      {
        "action": "navigate_to",
        "target_id": "OBJECT_ID",
        "executor": "nav2"
      }
    ]
  },

  "expected_final_status": "ready"
}
```

---

# 8. Ground Truth 生成规则

必须先从 canonical graph 确定真实节点，再写自然语言。

顺序：

```text
选择真实 Room / Object / Agent
→ 生成完整 Grounding candidate set
→ 确定 status
→ 定义 symbolic plan
→ 由 Compiler 生成 expected compiled plan
→ 最后写 instruction
```

禁止：

```text
先编一句话
→ 再猜 Ground Truth
```

Expected compiled plan 应尽可能由同一个 deterministic Compiler 从 Oracle symbolic plan 生成，避免手写不一致。

---

# 9. E2E Benchmark Validator

Validator 必须检查：

```text
Case ID 唯一
所有 expected ID 存在
Room / Object 层级正确
Expected semantic label 存在
Expected candidate set 完整
Expected status 与 candidate count 一致
Expected symbolic refs 存在
Expected actions 合法
Expected compiled IDs 与 refs 一致
Ambiguous/not_found/unsupported 没有 executable plan
Benchmark hash 正确
```

Validator 必须从 canonical graph 重新计算 Grounding。

---

# 10. 禁止 Oracle 泄漏

预测函数只能接收：

```python
run_e2e(
    instruction,
    model,
    runtime_config,
)
```

预测路径禁止接收：

```text
oracle_intent
expected_label
expected_grounding
expected_symbolic_plan
expected_compiled_plan
```

推荐结构：

```python
prediction = run_e2e(instruction, model, runtime)
score = evaluate_e2e(prediction, case_ground_truth)
```

新增测试：

```text
test_prediction_path_has_no_oracle_fields
```

---

# 11. 真实 E2E 执行流程

每条 case 必须真实调用生产模块：

```text
1. Intent Parser
2. Semantic Vector Retriever
3. SceneGraphRAG / GroundingOrchestrator
4. Simple Planner v3
5. compile_simple_plan
6. Plan Validator
```

不要使用：

```text
测试专用 ground_label()
手工构造 predicted Grounding
Oracle symbolic plan
```

---

# 12. Planner 调用门控

只有：

```text
Grounding status = ready / resolved
```

时才调用 Planner。

以下状态：

```text
ambiguous
not_found
unsupported
```

必须：

```text
不调用 Planner
不生成 executable plan
```

记录：

```text
planner_called = false
```

---

# 13. 每条结果必须保存的内容

```json
{
  "case_id": "e2e_001",
  "instruction": "...",

  "intent_result": {
    "raw_output": "...",
    "parsed_intent": {},
    "schema_valid": true,
    "latency_ms": 0
  },

  "semantic_result": {
    "query": "...",
    "status": "matched",
    "top1_label": "...",
    "label_ranking": [],
    "latency_ms": 0
  },

  "grounding_result": {
    "status": "resolved",
    "node_ids": [],
    "candidate_node_ids": [],
    "latency_ms": 0
  },

  "planner_result": {
    "called": true,
    "first_pass_raw": "...",
    "first_pass_symbolic_plan": {},
    "repair_used": false,
    "final_symbolic_plan": {},
    "latency_ms": 0
  },

  "compiled_plan": {},

  "validation_result": {
    "passed": true,
    "errors": []
  },

  "stage_scores": {
    "intent_success": true,
    "semantic_success": true,
    "grounding_success": true,
    "planner_success": true,
    "compiler_success": true,
    "validator_success": true
  },

  "e2e_success": true,
  "failure_stage": null,
  "all_errors": [],
  "total_latency_ms": 0
}
```

---

# 14. Intent 评分

至少检查：

```text
Schema Validity
Action Accuracy
Quantifier Accuracy
Room / Location Accuracy
Spurious Location Rate
Wrong-provided Node Type Rate
Intent Gate Success
```

规则：

```text
node_type 缺失
→ 不直接失败，记录 provided rate

node_type 填错
→ Intent 失败

Ground Truth 无 Location
但 LLM 多生成 Location
→ Intent 失败
```

Target text 的语义正确性由 Semantic Retrieval 评价，不做字符串 exact match。

---

# 15. Semantic Retrieval 评分

对于 expected matched：

```python
semantic_success = (
    top1_label == expected_label
    and status in {"matched", "ambiguous"}
)
```

如果：

```text
Top-1 label 正确
但 status = not_found
```

仍然是 Semantic Stage 失败。

对于 expected not-found：

```python
semantic_success = status == "not_found"
```

同时报告：

```text
Label Hit@1
Recall@5
MRR
Not-found F1
```

---

# 16. Grounding 评分

## Resolved

```text
status 正确
AND target ID 正确
```

## Ambiguous

```text
status = ambiguous
AND candidate-set F1 = 1.0
```

## Resolved Set

若 E2E Test 中保留集合 Grounding：

```text
status = resolved_set
AND exact set match
```

## Not-found

```text
status = not_found
AND node_ids = []
```

---

# 17. Planner 评分

使用 Planner v3 simple 指标：

```text
Symbolic Schema Validity
Status Accuracy
Exact High-level Action Sequence
Role Ref Accuracy
Strict Symbolic Plan Success
Repair Rate
```

不评价 LLM 输出：

```text
executor
target_type
constraint refs
step number
node IDs
```

因为这些由 Compiler 负责。

---

# 18. Compiler 评分

Compiler 是确定性模块，目标：

```text
Executor Mapping Accuracy       = 100%
Target-ID Binding Accuracy      = 100%
Constraint Attachment Accuracy  = 100%
Escort Actor Binding Accuracy   = 100%
Step Numbering Accuracy         = 100%
```

若 Compiler 失败，应记录为：

```text
compiler
```

而不是 Planner 失败。

---

# 19. Validator 评分

至少检查：

```text
Allowed actions
Action order
Role refs
Real node IDs
Correct-role IDs
Executor
Constraints
Final plan schema
```

安全指标：

```text
Invalid Node ID Rate
Ungrounded ID Rate
Wrong-role ID Rate
Missing Required ID Rate
```

目标全部为：

```text
0
```

---

# 20. E2E Strict Success 定义

每条 case：

```python
e2e_success = (
    intent_success
    and semantic_success
    and grounding_success
    and planner_success
    and compiler_success
    and validator_success
)
```

对于：

```text
ambiguous
not_found
unsupported
```

成功定义为：

```text
正确阻塞
Planner 未被调用
没有 executable plan
没有 node ID 泄漏
```

---

# 21. Failure-by-stage

首个阻塞阶段：

```text
intent_schema
intent_action
intent_quantifier
intent_location
semantic_retrieval
grounding_status
grounding_id
grounding_candidate_set
planner_schema
planner_action
planner_ref
compiler
validator
```

每条失败保存：

```json
{
  "failure_stage": "intent_location",
  "all_errors": [
    "room_missing",
    "final_plan_not_generated"
  ]
}
```

`failures.jsonl` 行数必须等于 summary 中的 failure count。

---

# 22. E2E 主指标

## 总体

```text
E2E Strict Success
Final Validation Pass Rate
Final Action Sequence Accuracy
Final Target-ID Accuracy
Total Latency
```

## 分阶段

```text
Intent Gate Success
Semantic Stage Success
Grounding Exact Success
Planner Strict Success
Compiler Success
Validator Success
```

## 安全

```text
Invalid Node ID Rate
Ungrounded ID Rate
Wrong-role ID Rate
Missing Required ID Rate
```

## 失败归因

```text
Failure by Stage
```

---

# 23. 重复运行与统计

每个模型：

```text
50 cases × 3 repeats = 150 runs
```

报告：

```text
Mean Success
Standard Deviation
Per-case Consistency
First-pass Planner Success
Repair Rate
```

对于 status-gate cases，应要求：

```text
3/3 均正确阻塞
```

---

# 24. 延迟指标

保存：

```text
Intent latency
Semantic retrieval latency
Grounding latency
Planner latency
Compiler latency
Validator latency
Total latency
```

报告：

```text
Mean
Median
P95
```

不要把模型首次加载时间混入普通 warm-query 延迟。

冷启动单独报告。

---

# 25. 推荐文件结构

```text
eval/e2e/
├── cases/
│   ├── e2e_dev_v1.jsonl
│   ├── e2e_test_50_v1.jsonl
│   └── e2e_test_50_v1.manifest.json
├── generate_e2e_benchmark.py
├── validate_e2e_benchmark.py
├── run_e2e_eval.py
├── metrics.py
├── README.md
└── tests/
    └── test_e2e_eval.py
```

结果：

```text
eval/results/e2e_v1/
├── granite/
│   ├── summary.json
│   ├── per_case_results.jsonl
│   ├── failures.jsonl
│   └── run_manifest.json
└── mistral/
    ├── summary.json
    ├── per_case_results.jsonl
    ├── failures.jsonl
    └── run_manifest.json
```

---

# 26. Run Manifest

必须保存：

```text
Git commit
E2E benchmark hash
Evaluator hash
Intent prompt hash
Semantic config hash
Vector DB fingerprint
Canonical graph hash
Planner prompt hash
SimplePlan schema hash
Compiler hash
Validator hash
Model
Temperature
Context length
Repeat count
Command
Environment
```

---

# 27. 必须新增的测试

## 防泄漏

```text
test_e2e_prediction_receives_only_instruction
test_no_expected_label_in_prediction_path
test_no_expected_grounding_in_prediction_path
test_no_expected_plan_in_planner_prompt
```

## Status gate

```text
test_ambiguous_does_not_call_planner
test_not_found_does_not_call_planner
test_unsupported_does_not_call_planner
test_blocked_case_has_no_executable_plan
```

## 模块连接

```text
test_intent_target_text_enters_semantic_retrieval
test_semantic_top1_enters_grounding
test_grounded_refs_enter_simple_planner
test_symbolic_plan_enters_compiler
test_compiled_plan_enters_validator
```

## 安全

```text
test_final_ids_exist
test_final_ids_come_from_grounded_refs
test_wrong_role_id_rejected
test_invalid_id_rate_zero
```

## 评分

```text
test_spurious_location_causes_intent_failure
test_positive_semantic_not_found_is_failure
test_e2e_requires_all_stages
test_failure_count_matches_failures_file
```

---

# 28. E2E 验收条件

至少一个模型达到：

```text
E2E Strict Success             ≥ 80%
Final Validation Pass          ≥ 85%
Intent Gate Success            ≥ 85%
Semantic Stage Success         ≥ 90%
Grounding Exact Success        ≥ 90%
Planner Strict Success         ≥ 90%（条件于 Planner 被调用）
Compiler Success               = 100%
Status Gate Accuracy           = 100%
Final Target-ID Accuracy       ≥ 95%
Invalid Node ID Rate           = 0
Ungrounded ID Rate             = 0
Wrong-role ID Rate             = 0
Failure Stage Coverage         = 100%
```

E2E 分数低于独立模块是正常的。

不要以：

```text
必须接近 100%
```

作为成功标准。

---

# 29. 修复策略限制

正式 Test 前允许在 Dev 上：

```text
修接口 Bug
修字段传递
修 evaluator
修 validator
```

不允许：

```text
根据 Test case 调 Prompt
给 Test query 加 alias
修改 Semantic threshold 迎合 Test
修改 expected plan
删除失败 case
增加规则 fallback 掩盖 LLM 错误
```

最多允许一次针对 Dev 的小型 Prompt 修复。

---

# 30. E2E 结果分析

报告至少包含：

```text
Granite vs Mistral 总体结果
每阶段成功率
失败阶段分布
各类别成功率
延迟
Repair 使用率
安全指标
3 次重复的一致性
```

重点分析：

```text
失败是否仍集中在 Intent Room / Quantifier
Planner 是否保持高准确率
模块串联是否产生新的接口错误
```

---

# 31. 当前不需要做 Nav2 执行

本轮 E2E 的最终输出是：

```text
Validated TaskPlan
```

不是：

```text
机器人真实到达目标
```

Nav2 / 仿真执行属于下一层：

```text
Execution Evaluation
```

不要把导航成功率混入本轮。

---

# 32. 论文中 E2E 的正确含义

本论文中的 E2E：

```text
自然语言
→ 最终经过验证的可执行 TaskPlan
```

它不等于：

```text
自然语言
→ 机器人在现实中完成任务
```

论文中应明确区分：

```text
Planning End-to-End
和
Physical Execution End-to-End
```

---

# 33. 最终交付物

```text
[ ] e2e_dev_v1.jsonl
[ ] e2e_test_50_v1.jsonl
[ ] e2e_test_50_v1.manifest.json
[ ] E2E benchmark generator
[ ] E2E benchmark validator
[ ] E2E evaluator
[ ] E2E unit/integration tests
[ ] Granite results
[ ] Mistral results
[ ] E2E comparison report
[ ] Complete failures
[ ] Run manifests
[ ] E2E stage conclusion
```

---

# 34. 推荐执行顺序

```text
Phase 0
冻结 Joint Grounding 和 Planner v3

Phase 1
建立 15–20 条 E2E Dev

Phase 2
实现真实生产模块串联

Phase 3
修复 Dev 中的接口与 evaluator Bug

Phase 4
生成并验证 Frozen E2E Test 50

Phase 5
运行 Granite + Mistral，repeats=3

Phase 6
分析 Failure-by-stage

Phase 7
冻结结果并写论文
```

---

# 35. 最终原则

E2E 阶段不要再让 LLM承担确定性工作。

系统分工保持：

```text
LLM Intent Parser：
理解自然语言

Semantic RAG：
映射 canonical label

Graph Grounding：
确定真实实体和 ID

Simple Planner：
输出高层 action sequence + role ref

Compiler：
补 executor、ID、type、constraints、step number

Validator：
拒绝非法计划
```

最终目标：

> 验证这些已经独立通过的模块连接后，能否从自然语言稳定生成安全、正确、可执行的最终任务计划。
