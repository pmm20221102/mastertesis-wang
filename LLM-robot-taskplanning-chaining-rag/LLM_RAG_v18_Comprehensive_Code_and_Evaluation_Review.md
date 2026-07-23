# LLM Task Planning + Khronos 3DSG RAG v18  
# 代码、Benchmark 与测评结果全面审查报告

## 0. 审查对象与方法

本次审查对象为：

```text
LLM_RAG_v18_LLM_Eval_Phase1_2.tar.gz
```

主要包含：

```text
llm/intent_parser.py
main_rag.py
schema.py
planner_schemas/intent.py
grounding/orchestrator.py

eval/run_intent_eval.py
eval/run_pipeline_eval.py
eval/generate_intent_benchmark.py
eval/generate_paraphrase_benchmark.py
eval/generate_planning_benchmark.py

eval/cases/
├── grounding_benchmark_100_v3.jsonl
├── intent_benchmark_100_v1.jsonl
├── intent_paraphrase_300_v1.jsonl
└── planning_benchmark_50_v1.jsonl

eval/results/
├── grounding_baseline_v17/
├── intent_qwen_05b_v1_temp0/
├── intent_gemma4_v1_temp0/
├── intent_gemma4_v1_temp07_r1/
├── intent_gemma4_v1_temp0_r3/
├── intent_gemma4_paraphrase_v1_temp0/
├── intent_qwen35_4b_v1_temp0/
├── planning_gemma4_v1_final2/
└── planning_qwen_05b_v1/

tests/
├── test_intent_serialization.py
└── test_intent_evaluator.py
```

本次实际执行：

1. 安全解包并检查文件结构；
2. 执行 Python compile；
3. 执行压缩包中全部 pytest；
4. 检查四个 benchmark 的文件数量、重复项和 manifest hash；
5. 使用 generator 重新生成 Intent、Paraphrase 和 Planning benchmark；
6. 检查 byte-level reproducibility；
7. 静态审查 Intent Parser、Intent Evaluator、Planner、Planner Evaluator；
8. 重新统计现有 Intent 结果中的条件指标；
9. 逐条检查 Planner 失败 case；
10. 对比交付报告与实际结果文件的一致性；
11. 检查当前压缩包能否独立 import 和复现实验。

---

# 1. 总体结论

当前 v18 已经完成了三个有价值的工作：

```text
1. Grounding benchmark v3 的文件 hash 与 manifest 一致；
2. Intent/Paraphrase/Planning benchmark 可以固定 seed 字节级复现；
3. 已经建立 Intent 与 Planner 的独立 runner 和初步结果目录。
```

但是，当前 **LLM Intent 的正式测评结论还不可信**，Planner 测评也只能视为初步 smoke test。

最重要的原因不是模型本身，而是：

```text
Benchmark oracle、Prompt schema、Parser canonicalization 和 Evaluator
对同一个字段使用了不一致的定义。
```

尤其是：

```text
Prompt 要求 node_type = objects / places / rooms
Benchmark oracle 保存 node_type = object / place / room
```

因此，模型即使严格遵循 Prompt，也会被 Evaluator 判错。

更严重的是，当前所谓：

```text
Strict exact match
```

并没有把：

```text
label
node_type
room
position
EntityReference 的完整字段
```

纳入最终 strict 判定。

审查现有结果发现：

```text
Gemma4 报告的 44 个 strict success 中，
44 个都存在 EntityReference 字段错误。

Qwen2.5 0.5B 报告的 4 个 strict success 中，
4 个也都存在 EntityReference 字段错误。
```

如果将当前 evaluator 已经计算出的 `role_field_metrics` 纳入严格判定：

```text
Gemma4 role-inclusive exact success = 0/100
Qwen2.5 0.5B role-inclusive exact success = 0/100
```

这不代表模型真实能力为 0%，而是说明：

> 当前 Oracle、Prompt 和 Evaluator 的 schema contract 不一致，现有 strict 指标不能用于正式模型结论。

Planner 部分也存在类似问题：

```text
Planning benchmark 文件名写 50，但实际只有 45 条；
其中只有 25 条真正调用 Planner；
25 条全部是单目标、单步骤；
没有 actor/source/destination；
没有 constraint；
没有 multi-step；
没有 step-specific reference；
没有 escort/guide/avoid/wait 等任务。
```

因此目前 Gemma4 的：

```text
planner actions exact sequence = 92%
```

只能解释为：

> 在 25 条单目标、单步骤、只有 locate_entity 或 navigate_to 的简单 Oracle-Grounding case 上，Gemma4 有 23 条生成了期望 action。

它不能证明完整 Planner 已经完成。

---

# 2. 正面结果

## 2.1 Python compile 通过

执行：

```bash
python -m compileall . -q
```

结果：

```text
PASS
```

说明当前打包文件不存在基本语法错误。

---

## 2.2 压缩包内测试通过

执行：

```bash
pytest -q
```

结果：

```text
20 passed
```

其中：

```text
test_intent_serialization.py    7 tests
test_intent_evaluator.py       13 tests
```

但交付报告中声称：

```text
66 passed
```

当前压缩包只包含 20 个测试，另外 46 个旧测试没有随包提供。

因此当前只能确认：

```text
打包的 20 个测试通过
```

不能根据当前交付物独立确认：

```text
完整仓库 66 tests 全部通过
```

---

## 2.3 Benchmark manifest hash 全部一致

实际重新计算 SHA256 前 16 位：

| Benchmark | Manifest | 实际文件 | 结果 |
|---|---:|---:|---|
| grounding_benchmark_100_v3 | `25407e21069e4d71` | `25407e21069e4d71` | PASS |
| intent_benchmark_100_v1 | `934b51f191b92f43` | `934b51f191b92f43` | PASS |
| intent_paraphrase_300_v1 | `3bb49d5336e1b07b` | `3bb49d5336e1b07b` | PASS |
| planning_benchmark_50_v1 | `1f1a8566b12eea8f` | `1f1a8566b12eea8f` | PASS |

Intent、Paraphrase 和 Planning manifest 中记录的 source benchmark hash 也与实际文件一致。

这是相较 v17 的实质性进步。

---

## 2.4 Benchmark 可以字节级复现

使用当前 generator、相同输入和 seed=42 重新生成后：

```text
intent benchmark       byte-identical
paraphrase benchmark   byte-identical
planning benchmark     byte-identical
```

Planning benchmark 在不同：

```text
PYTHONHASHSEED=1
PYTHONHASHSEED=999
```

下也生成相同文件。

因此当前三个 generator 的字节级复现性是合格的。

---

## 2.5 Intent 完整序列化已经修复

`intent_to_dict()` 已改为：

```python
return intent.model_dump(mode="json")
```

能够保留：

```text
locations
relations.target_node_id
aggregate.location
steps
constraints
quantifier
```

这一修改是正确的。

---

## 2.6 Ollama import 已从 Intent Parser 顶层移除

`parse_intent_with_llm()` 内部才执行：

```python
from ollama import Client
```

因此单独 import：

```text
llm.intent_parser
eval.run_intent_eval
```

不再要求预先安装 Ollama。

这一方向正确。

---

# 3. P0：Prompt 与 Oracle 的 node_type contract 完全不一致

## 3.1 Prompt 使用复数 node_type

`llm/intent_parser.py` 的 Prompt 明确要求：

```text
objects
places
rooms
agents
```

例如：

```json
{
  "label": "chairs",
  "node_type": "objects"
}
```

---

## 3.2 Benchmark Oracle 使用单数 node_type

100 条 Intent benchmark 的 target node_type 分布：

```text
object = 74
place  = 17
room   = 9
```

即：

```text
所有 100 条 target 都使用单数形式。
```

---

## 3.3 当前 schema 没有约束 node_type

`EntityReference.node_type` 当前是：

```python
node_type: str | None = None
```

因此以下值都能通过 Pydantic：

```text
object
objects
chair
thing
unknown_type
```

模型输出复数可以通过 schema，但随后被 benchmark 判错。

---

## 3.4 对现有结果的实际影响

重新统计：

```text
Gemma4 node_type exact match = 0/100
Qwen2.5 0.5B node_type exact match = 0/100
```

这不是简单的“模型总是输出 object 而不是 objects”。

交付报告中的表述方向反了。

实际情况是：

```text
Prompt/Few-shot 引导模型输出复数；
Oracle benchmark 要求单数。
```

所以模型遵循 Prompt 时会被判错。

---

## 3.5 必须修改

建立唯一 canonical node type contract。

建议统一为单数：

```python
NodeType = Literal["object", "place", "room", "agent"]
```

理由：

```text
canonical graph 和 grounding backend 当前主要使用单数；
schema.py 的 TargetType 也使用单数；
减少 Planner、Grounding、Intent 三层转换。
```

然后同步修改：

```text
planner_schemas/intent.py
llm/intent_parser.py Prompt
Few-shot examples
intent benchmark generator
paraphrase benchmark generator
evaluator normalization
tests
```

不建议仅在 evaluator 中偷偷加入：

```text
objects → object
places  → place
```

如果需要兼容旧结果，应同时报告：

```text
raw node_type accuracy
canonicalized node_type accuracy
```

不能只报告 canonicalized 结果。

---

# 4. P0：当前 Strict Exact Match 并不严格

## 4.1 当前 strict 判定实际检查内容

`eval/run_intent_eval.py` 中 strict 只检查：

```text
action
role presence
location exact（仅 oracle 有 location 时）
relation exact（仅 oracle 有 relation 时）
quantifier
aggregate
multi-step
```

没有检查：

```text
target label
target node_type
target room
target text
source/actor/destination 字段内容
position
attributes
name
aggregate location 的完整字段
```

虽然 `_compare_entity_ref()` 计算了这些字段，但其结果：

```text
role_field_metrics
```

没有进入 `strict_exact_match`。

---

## 4.2 实际审查结果

现有结果中：

| 结果集 | 报告 strict success | 其中含 role field 错误 | role-inclusive strict |
|---|---:|---:|---:|
| Gemma4 controlled | 44/100 | 44 | 0/100 |
| Gemma4 temp=0.7 | 44/100 | 44 | 0/100 |
| Gemma4 3 repeats | 132/300 | 132 | 0/300 |
| Qwen2.5 0.5B | 4/100 | 4 | 0/100 |
| Gemma4 paraphrase partial | 132/289 | 132 | 0/289 |
| Qwen3.5 partial | 31/83 | 31 | 0/83 |

这说明：

> 当前 44% 不是完整 Intent strict accuracy，而只是部分结构字段通过率。

---

## 4.3 当前 44% 可以解释为什么指标

最多只能改名为：

```text
structural-control-field match
```

或者：

```text
partial intent contract match
```

不能称为：

```text
Strict exact match
End-to-end intent accuracy
Complete structured intent accuracy
```

---

## 4.4 必须修改 strict success

建议定义：

```text
strict_semantic_success =
    schema_valid
AND action_correct
AND role_set_exact
AND all_expected_role_fields_correct
AND no_spurious_role_fields
AND location_set_exact
AND relation_set_exact
AND quantifier_correct
AND aggregate_exact
AND position_correct
AND multi_step_exact
```

需要单独决定 `text` 是否进入 strict。

推荐：

```text
text surface form 不进入 semantic strict；
label / node_type / room / locations / relations / position 必须进入。
```

同时保留：

```text
surface_text_exact
```

作为单独指标。

---

# 5. P0：所谓 raw output 实际不是模型原始输出

## 5.1 当前 evaluator 的流程

`evaluate_intent_case()` 调用：

```python
predicted_intent = parse_intent_with_llm(...)
predicted = intent_to_dict(predicted_intent)
raw_output = json.dumps(predicted)
```

但是 `parse_intent_with_llm()` 内部已经完成：

```text
Ollama structured output
→ Pydantic parse
→ validate_and_canonicalize_intent()
→ 返回修改后的 TaskIntent
```

因此保存的：

```text
raw_output
```

实际上是：

```text
post-Pydantic + post-canonicalization 的结果
```

不是模型原始 response。

---

## 5.2 造成的影响

当前无法区分：

```text
模型原始输出正确
但规则修改错了

模型原始输出错误
被规则修好了

模型漏掉字段
被 deterministic inference 补上了

模型 JSON 不完整
thinking fallback 提取了部分 JSON
```

因此目前无法真正分析：

```text
LLM 本身的能力
规则修复贡献
repair 对 accuracy 的提升
```

---

## 5.3 必须修改返回结构

建议 `parse_intent_with_llm()` 返回：

```python
class IntentParseTrace(BaseModel):
    raw_message_content: str | None
    raw_thinking: str | None
    extracted_json: str | None
    parsed_intent: dict | None
    canonicalized_intent: dict | None
    rules_applied: list[str]
    schema_error: str | None
    model_metadata: dict
```

Evaluator 分别计算：

```text
raw schema validity
parsed intent metrics
canonicalized intent metrics
```

最终至少报告：

```text
raw strict
post-canonicalization strict
canonicalization gain
canonicalization damage
```

---

# 6. P0：Action accuracy 不是纯 LLM 指标

## 6.1 Parser 会覆盖模型 action

`validate_and_canonicalize_intent()` 会调用：

```python
rule_action = _detect_action_from_text(user_command)
```

只要 rule_action 不是默认 navigate，就会直接覆盖模型 action。

所以当前 action accuracy 实际是：

```text
LLM + keyword rule hybrid accuracy
```

不是：

```text
LLM action classification accuracy
```

---

## 6.2 当前 keyword rule 存在严重 substring bug

规则使用：

```python
if any(w in text_lower for w in ["pick up", "grasp", "carry", "place", "lift"]):
    return "unsupported"
```

其中：

```text
"place"
```

会命中所有包含空间 Place 的导航查询。

例如：

```text
Find a place near place 931848.
```

会被判为：

```text
unsupported
```

同样，以下指令也会受到影响：

```text
Find the small_office_object inside place 928841.
Navigate to the chair in place 928832.
```

---

## 6.3 `guide` 也被错误解释为 escort

规则：

```python
if "guide" in text:
    return "escort"
```

因此：

```text
Guide me to a large_office_object.
```

被判定为 escort。

但这里 “me” 不是需要解析和 Grounding 的 actor，语义上更接近 navigate。

---

## 6.4 默认 navigate 也不安全

无法识别的句子全部默认：

```text
navigate
```

例如：

```text
Which room has the most chairs?
How many chairs are in room 0753?
List every chair.
```

如果模型本身输出错误，default navigate 可能保留错误。

---

## 6.5 当前 benchmark 与 rule action 冲突 43/100

使用当前 `_detect_action_from_text()` 规则重新计算：

```text
43/100 instructions 的 rule action 与 oracle action 不一致。
```

因此 action 结果同时受到：

```text
benchmark action 定义不一致
keyword rule bug
LLM 输出
```

三种因素影响。

---

## 6.6 必须修改

### 测评模式

Intent LLM Evaluation 中：

```text
禁止覆盖 LLM action。
```

只记录：

```text
raw model action
canonicalized action
```

Production 可以保留规则修复，但测评必须分开。

### 规则实现

若仍需规则 fast path：

```text
使用 token/regex word boundary；
禁止简单 substring；
区分 “place as verb” 和 “Place as spatial entity”；
区分 “guide me to X” 与 “guide person X to Y”。
```

例如：

```python
re.search(r"\b(pick up|grasp|carry|lift)\b", text)
```

不要将裸词：

```text
place
```

直接作为 manipulation 证据。

---

# 7. P0：Intent Benchmark 不是完整 Intent Benchmark

## 7.1 100 条 case 全部只有 target role

Manifest：

```text
required_roles_distribution:
target = 100
```

实际检查也确认：

```text
actor       0
source      0
destination 0
target    100
```

因此当前：

```text
role presence F1 = 100%
```

对于 Gemma4 并不能证明复杂角色解析能力。

它只证明：

```text
模型多数时候输出了 target，且没有额外顶层角色。
```

---

## 7.2 没有 multi-step Intent

实际：

```text
steps cases = 0
```

因此：

```text
multi_step_exact_rate = null
```

当前 Intent Parser Prompt 虽然加入 multi-step few-shot，但 benchmark 没有验证。

---

## 7.3 没有 escort/source→destination/avoid/unsupported

Oracle action 分布：

```text
locate   = 71
navigate = 29
```

缺少：

```text
escort
guide
avoid
wait
ask_clarification
unsupported
```

也缺少：

```text
actor
source
destination
task constraints
```

---

## 7.4 exact_id case 不是真正 LLM case

5 条 exact_id case 会进入 deterministic fast path：

```text
不调用 LLM。
```

将其放入 LLM Intent 总准确率会混合：

```text
deterministic parser
LLM parser
```

应单独报告。

---

## 7.5 必须重建 Intent Benchmark v2

建议拆成：

```text
A. deterministic_exact_id_20_v1
B. llm_intent_controlled_150_v2
C. llm_intent_paraphrase_300_v2
```

Controlled benchmark 至少覆盖：

| 类型 | 建议数量 |
|---|---:|
| navigate target | 15 |
| locate target | 15 |
| room/place constraints | 20 |
| relation | 20 |
| spatial position | 15 |
| set/quantifier | 10 |
| aggregate | 10 |
| source→destination | 10 |
| escort/guide | 10 |
| avoid/prefer constraints | 10 |
| unsupported | 5 |
| ambiguous/clarification | 5 |
| multi-step | 15 |

---

# 8. P0：Oracle Intent 仍包含内部 node IDs

Intent Parser Prompt 明确规定：

```text
LLM 不应生成 Scene Graph node ID。
```

但 Oracle Intent 中仍存在：

```text
locations[].node_id
relations[].target_node_id
aggregate.location.node_id
```

例如 complex location case：

```json
{
  "locations": [
    {"node_id": "...", "node_type": "place"},
    {"node_id": "...", "node_type": "room"}
  ]
}
```

却缺少：

```text
text
```

这与 LLM 输出 contract 相反。

---

## 8.1 对 location metric 的影响

Evaluator 比较：

```text
(location.text, location.node_type)
```

Oracle location.text 为空，模型通常输出：

```text
place 928841
room 0752
```

结果：

```text
模型语义上可能正确，但 exact match 一定失败。
```

现有结果：

```text
Gemma4 location exact = 0/4
Qwen2.5 location exact = 1/4
```

其中很大一部分是 Oracle 表达错误，不应直接归因给模型。

---

## 8.2 必须修改

LLM Intent Oracle 只保存用户可见语义：

```json
{
  "text": "place 928841",
  "node_type": "place"
}
```

或：

```json
{
  "surface_id": "928841",
  "node_type": "place"
}
```

内部完整 node ID 应保存到独立字段：

```text
grounding_evidence
```

不得作为 LLM Intent expected output。

---

# 9. P0：Spatial Intent Evaluation 忽略 position

Spatial Oracle 中包含：

```python
position: [x, y, z]
```

但当前 evaluator 不比较 position。

因此模型可以完全丢失坐标，只要：

```text
action
target role
quantifier=nearest
```

部分字段满足，就可能被算作成功。

这会严重高估 spatial parsing。

---

## 必须增加

```text
position_presence_accuracy
position_dimension_accuracy
position_absolute_error
position_within_tolerance_rate
```

建议：

```text
2D 指令允许 z 缺失；
x/y tolerance = 0.1 或按 benchmark 精度确定；
3D 指令必须比较 x/y/z。
```

Strict spatial success：

```text
action
+ target node_type
+ nearest quantifier
+ position within tolerance
```

---

# 10. P0：Aggregate accuracy 被默认负样本严重抬高

当前 summary 的 aggregate accuracy：

```python
agg_correct / total_cases
```

其中 91 条没有 aggregate。

所以报告：

```text
Gemma4 aggregate accuracy = 87%
Qwen2.5 aggregate accuracy = 83%
```

看起来很高。

但只看 9 条真正 aggregate case：

```text
Gemma4 aggregate correct = 3/9 = 33.3%
Qwen2.5 aggregate correct = 1/9 = 11.1%
```

同时：

```text
Gemma4 在 91 个非 aggregate case 中生成了 7 个 spurious aggregate
Qwen2.5 在 91 个非 aggregate case 中生成了 9 个 spurious aggregate
```

---

## 必须拆分指标

```text
aggregate_presence_precision
aggregate_presence_recall
aggregate_presence_f1
aggregate_operation_accuracy_on_positive_cases
aggregate_target_labels_accuracy
aggregate_location_accuracy
aggregate_value_accuracy（若适用）
spurious_aggregate_rate
```

不能继续只报告：

```text
overall aggregate accuracy
```

---

# 11. P0：Quantifier accuracy 也被默认 one 主导

当前：

```text
20 条非默认 quantifier
80 条默认 one
```

现有结果：

### Gemma4

```text
non-default quantifier accuracy = 19/20 = 95%
default-one accuracy            = 43/80 = 53.75%
overall                          = 62%
```

### Qwen2.5 0.5B

```text
non-default quantifier accuracy = 19/20 = 95%
default-one accuracy            = 47/80 = 58.75%
overall                          = 66%
```

这说明模型能识别显式：

```text
all
nearest
```

但经常在普通 case 中产生错误 quantifier。

应报告：

```text
non-default quantifier recall
default-one specificity
spurious quantifier rate
per-class confusion matrix
```

---

# 12. P0：Relation 与 Location 的 0 分不能直接解释为模型失败

现有结果：

```text
Gemma4 relation F1 = 0.0，n=12
Gemma4 location F1 = 0.0，n=4
```

但存在三个混合原因：

```text
1. Oracle location 缺 text；
2. parser canonicalizer 会删除 target_text 未原样出现在 instruction 中的 relation；
3. paraphrase/模型可能输出语义同义词，evaluator 只做字符串 exact。
```

例如：

```text
close to
next to
near
```

可能表达同一 relation，但当前只允许 exact relation enum + exact target_text。

建议：

```text
relation type 必须 exact；
target entity 使用 canonical label + visible identifier 比较；
surface text 仅作为诊断；
同义词映射必须预先固定，不得动态 LLM judging。
```

---

# 13. P0：Paraphrase Benchmark 存在大量语义损坏

## 13.1 300 条中有 23 个重复 instruction

当前 paraphrase benchmark：

```text
case count              = 300
duplicate instructions  = 23
```

重复并不一定全部无效，但会降低有效样本独立性。

---

## 13.2 Complex location 被生成空文本

由于 Oracle location 只有 node_id、没有 text，generator 会生成：

```text
The chairs situated in
The small office objects situated in
```

或者：

```text
in  and
```

这些不是有效自然语言任务。

---

## 13.3 Spatial paraphrase 丢失坐标

Generator 在看到：

```text
quantifier = nearest
```

后直接生成：

```text
Where's the closest place?
Find the nearest place
```

但没有保留原指令中的：

```text
(x, y, z)
```

因此 paraphrase 与 Oracle spatial intent 不再语义等价。

---

## 13.4 connected_to 没有实现

Colloquial/Rephrased generator 只处理：

```text
near
inside
```

没有处理：

```text
connected_to
```

虽然文件顶部定义了 `CONNECTED_PATTERNS`，实际函数没有使用。

因此 connected_to case 会落入默认模板，丢失关系条件。

---

## 13.5 Aggregate paraphrase 使用错误 target text

部分 Oracle target.text 是：

```text
room with most large_static_wall_furniture
```

Generator 又套一层：

```text
Which room has the most {label_display}?
```

形成：

```text
Which room has the most room with most large static wall furniture?
```

明显语义和语法错误。

---

## 13.6 Rephrased 样本大量是名词短语

例如：

```text
Destination: large office objects
Movement toward the small office objects
The location of a chair
Every chair in the scene
```

这些可以作为鲁棒性测试，但不应全部当作自然用户指令。

应标记：

```text
fragment
elliptical_query
imperative
question
```

并分别报告，而不是都称为 paraphrase。

---

## 13.7 当前 Paraphrase Evaluation 不完整

目录：

```text
intent_gemma4_paraphrase_v1_temp0/
```

只有：

```text
per_case_results.jsonl
```

实际：

```text
289/300 cases
```

缺少：

```text
11 cases
summary.json
run_manifest.json
raw_outputs.jsonl
predictions.jsonl
failures.jsonl
report.md
```

所以交付报告中的：

```text
Paraphrase strict exact = 45.7%
```

是基于未完成运行和有缺陷 strict metric 得到的临时结果。

不能写入正式论文。

---

## 13.8 必须重建 Paraphrase Benchmark v2

每条 paraphrase 生成后必须执行 slot-preservation validator：

```text
action preserved
target label preserved
room/place visible identifier preserved
relation preserved
quantifier preserved
aggregate operation preserved
coordinates preserved
role structure preserved
```

任何关键 slot 丢失：

```text
reject and regenerate
```

同时增加：

```text
empty text check
duplicate instruction check
grammar sanity check
manual review status
```

正式发布前建议人工审查全部 300 条，数量可控。

---

# 14. P0：现有 Result 目录不完整且报告与文件不一致

## 14.1 Qwen3.5 只完成 83/100

当前只有：

```text
per_case_results.jsonl
83 cases
49 schema valid
34 schema errors
```

缺少完整 summary 和 run manifest。

因此交付报告中的 qwen3.5 指标只能标记为：

```text
partial run
```

不能与完整 100-case 模型直接横向比较。

---

## 14.2 Gemma4 paraphrase 只完成 289/300

同样属于：

```text
partial run
```

---

## 14.3 Qwen2.5 只有 1 repeat

交付报告标题写：

```text
Controlled, temp=0, 3 repeats
```

但：

```text
intent_qwen_05b_v1_temp0/run_manifest.json
repeats = 1
```

只有 Gemma4 有：

```text
temp0_r3
```

因此当前不能说所有模型都做了 3 repeats。

---

## 14.4 temp=0.7 只有 1 repeat

交付报告写：

```text
Deterministic ~96%
```

但 temp=0.7 目录只有：

```text
1 repeat
```

一个 repeat 无法计算跨 repeat 一致性。

---

## 14.5 交付报告已经过期

报告写：

```text
Planning benchmark 当前只有 7 cases
Gemma4 3-repeat 正在后台运行
```

但包内已经有：

```text
Planning benchmark 45 cases
Gemma4 temp0 r3 300 evaluations
Planner result directories
```

说明 `PHASE1_DELIVERY_REPORT.md` 没有在最终打包前更新。

---

# 15. P0：Repeat Stability 实际没有实现

当前 r3 summary 只是把：

```text
100 cases × 3 repeats
```

合并为：

```text
total_cases = 300
```

没有生成：

```text
per-case consistency
field agreement
majority vote
all-repeat exact rate
pairwise agreement
```

报告中的“deterministic 100%”并没有独立结果文件支持。

---

## 必须增加

```text
unique_case_count
total_evaluations
all_repeats_identical_rate
action_agreement_rate
role_agreement_rate
field-level agreement
strict_success_all_repeats
strict_success_any_repeat
majority_vote_accuracy
```

输出：

```text
repeat_stability.json
```

---

# 16. P0：Runner 没有实现计划书承诺的关键功能

当前 `run_intent_eval.py` 实际 CLI 支持：

```text
benchmark
model
repeats
temperature
output
category
```

没有实现：

```text
单 case 过滤
resume
失败重跑
retry
mock model CLI
timeout 配置
seed 配置
模型 digest
token usage
prompt snapshot
config snapshot
断点安全恢复
```

虽然每个 case 会重写 `per_case_results.jsonl`，但这不是 resume。

---

## 16.1 增量保存方式效率较低

当前每完成一个 case，就：

```text
重新打开文件
重写全部 all_results
```

复杂度约为：

```text
O(N²)
```

100 条影响不大，300×多 repeats 时会增加无效 I/O。

推荐：

```text
append-only JSONL
每条 fsync 或定期 flush
summary 单独原子替换
```

---

## 16.2 没有 atomic write

任务中断时可能留下半写文件。

建议：

```text
write temp file
→ fsync
→ os.replace
```

---

# 17. P0：现有测试覆盖不足，部分测试实际上无效

## 17.1 `test_run_manifest_complete` 是恒真测试

当前：

```python
for field in required_fields:
    assert field in required_fields
```

这个测试永远通过。

它没有读取任何 manifest，也没有验证 runner 输出。

---

## 17.2 `test_raw_and_repaired_metrics_separated` 没有测试分离

该测试只检查：

```text
evaluate_intent_case 函数签名中存在 case 和 model 参数
```

并没有验证：

```text
raw output
parsed output
canonicalized output
repair flags
```

实际上当前代码也没有实现真正 raw/repaired 分离。

---

## 17.3 没有测试 strict exact 是否检查 role fields

当前最严重的 strict metric bug 没有测试覆盖。

必须新增：

```text
test_strict_fails_on_wrong_label
test_strict_fails_on_wrong_node_type
test_strict_fails_on_wrong_room
test_strict_fails_on_missing_position
test_strict_fails_on_spurious_location
test_strict_fails_on_spurious_relation
test_strict_fails_on_wrong_aggregate_location
```

---

## 17.4 没有 runner integration test

当前 20 个测试没有实际执行：

```text
mock LLM → runner → output directory → summary/manifest
```

需要加入完全离线的 mock integration test。

---

# 18. P0：当前压缩包无法独立运行核心代码

实际 import：

```text
eval.run_intent_eval  OK
llm.intent_parser     OK

main_rag              FAIL
eval.run_pipeline_eval FAIL
grounding.orchestrator FAIL
```

失败原因：

```text
ModuleNotFoundError: No module named 'api_schemas'
```

压缩包没有包含：

```text
api_schemas.py
scene_graph_api.py
canonical_index.py
entity_resolver.py
完整 config
vector DB
canonical graph
```

因此当前压缩包只能静态审查和读取结果，不能独立重新运行完整 pipeline。

---

## 必须交付两种形式之一

### 方案 A：完整可运行包

包含：

```text
所有 Python 模块
requirements/lock file
config snapshot
canonical manifest
benchmark
测试
```

大型 vector DB 可单独提供。

### 方案 B：最小审查包 + reproduction manifest

至少包含：

```text
git commit
完整仓库路径
requirements hash
config hash
vector DB hash
RAG documents hash
canonical graph hash
一键构建命令
一键运行命令
```

当前包处于两者之间，不足以独立复现。

---

# 19. Grounding Baseline 的评价

## 19.1 当前可以确认的内容

现有结果文件报告：

```text
pipeline status accuracy      = 100/100
grounding exact success       = 100/100
set exact match               = 10/10
spatial target ID accuracy    = 10/10
failure count                 = 0
```

Benchmark v3 文件 hash 也与 manifest 一致。

Grounding 的候选 ID 和 aggregate_value 序列化代码已经存在。

---

## 19.2 当前包内无法重新验证的内容

缺少：

```text
canonical graph
vector DB
RAG documents
grounding generator
validator
完整依赖模块
```

因此无法在当前压缩包内重新运行 100 条 Grounding。

---

## 19.3 summary 与 run manifest 的 model 字段不一致

`summary.json`：

```text
model = mistral
```

`run_manifest.json`：

```text
model = N/A (oracle intent)
```

Oracle-intent Grounding 不应强调 LLM model。

建议统一为：

```text
model = null
intent_source = oracle
```

---

## 19.4 结论

Grounding 核心算法暂时不需要继续扩展。

但正式归档时需要补全：

```text
完整 reproduction manifest
validator report
canonical/vector DB hashes
可运行依赖
```

---

# 20. Planning Benchmark 的主要问题

## 20.1 文件名与实际 case count 不一致

文件名：

```text
planning_benchmark_50_v1.jsonl
```

实际：

```text
45 cases
```

Manifest 同样记录：

```text
case_count = 45
benchmark_status = ready
```

Generator 因跳过无 resolved reference case，最终没有补齐到 50。

---

## 20.2 只有 25 条真正调用 Planner

分布：

```text
total                 = 45
planner invoked       = 25
pre-set ambiguous     = 13
pre-set not_found     = 7
```

20 条 ambiguous/not_found 直接由 Oracle status 返回，不调用 LLM Planner。

这些 case 可以测试 pipeline gate，但不能计入 Planner 生成能力。

---

## 20.3 真正 Planner case 全部是单步骤

Expected step count：

```text
1 step = 25
0 step = 20
```

没有：

```text
2-step
3-step
multi-step
```

---

## 20.4 全部只有 target role

Oracle grounding role 分布：

```text
target only = 25
no role     = 20
```

缺少：

```text
actor
source
destination
constraint references
step-specific references
```

---

## 20.5 Action 仅有两种

真正 Planner case 的 expected action：

```text
locate_entity = 13
navigate_to   = 12
```

没有：

```text
escort_to
guide_to
avoid_area
wait
ask_clarification
handover_to_staff
trigger_workflow
perform_screening
return_to_base
```

---

## 20.6 因此当前 Planner benchmark 不能验证

```text
multi-step decomposition
step-specific target_ref
actor/destination binding
source/destination binding
constraint propagation
avoid/prefer
role-ID swap prevention
executor variety
workflow actions
clarification generation
```

---

# 21. Planner 结果的正确解读

## 21.1 Gemma4

现有结果：

```text
planner invoked                = 25
status correct                 = 25/25
action exact sequence          = 23/25
target ID set correct          = 25/25
validation pass               = 25/25
```

两条 action 失败：

```text
What is the closest location to coordinates...
expected navigate_to
predicted locate_entity

Locate node 569...
expected navigate_to
predicted locate_entity
```

这两条都存在 instruction 与 Oracle action 的语义冲突。

所以 92% 中既有模型因素，也有 benchmark label 因素。

---

## 21.2 Qwen2.5 0.5B

现有结果：

```text
planner invoked                = 25
status correct                 = 22/25
action exact sequence          = 9/25
target ID set correct          = 22/25
```

模型多数将：

```text
locate
```

规划为：

```text
navigate_to
```

3 条 case 返回 system_error。

---

## 21.3 Target ID accuracy 不是 LLM 生成 ID 能力

Planner Prompt 禁止模型输出 node ID。

程序通过：

```text
target_ref → deterministic resolver → target_id
```

自动填 ID。

所以：

```text
target_ids_accuracy = 100%
```

主要证明：

```text
系统编译器可以把 target_ref 绑定到 Oracle ID。
```

它不证明：

```text
LLM 能正确生成 node ID。
```

这本身是正确架构，但指标必须命名准确。

---

# 22. P0：Planner Evaluator 会丢失 system_error

`evaluate_planning_case()` 不论内部 PipelineResult 是否包含 error，正常返回路径都写：

```python
"error": None
```

因此 `_run_planner_with_oracle_grounding()` 返回：

```text
status = system_error
error = 具体异常
```

后，Evaluator 会丢弃异常文本。

现有 Qwen 结果中：

```text
3 个 system_error
error 字段仍为 null
```

---

## 22.1 造成的错误指标

Summary 的 error rate 使用：

```python
errors = sum(1 for r in planning if r.get("error"))
```

因为 error 被强制清空，报告得到：

```text
error_rate = 0%
```

但实际存在：

```text
3/45 system_error
3/25 planner-invoked system_error
```

---

## 必须修改

```python
"error": result.error
```

并单独报告：

```text
system_error_rate_all
system_error_rate_planner_invoked
timeout_rate
parse_error_rate
validation_error_rate
```

---

# 23. P0：Planner validation pass rate 被错误高估

当前：

```python
pi_validation_ok = count(not validation_errors)
```

即使：

```text
status = system_error
task_chain = []
error != null
```

只要 `validation_errors=[]`，也会被视为：

```text
validation pass
```

因此 Qwen 虽然有 3 个 system_error，仍报告：

```text
planner_validation_pass_rate = 100%
```

这是不正确的。

---

## 正确条件

```text
validation_pass =
    planner_invoked
AND predicted_status != system_error
AND error is None
AND validation_errors is empty
AND task plan schema valid
```

还应区分：

```text
structural validation pass
semantic benchmark success
```

Qwen 的情况说明：

```text
结构合法不等于 action 语义正确。
```

---

# 24. P0：Planner 的 target_ref 仍会被自动猜测

`PlanDraftStep` 允许 executable action：

```text
target_ref = ""
```

随后 `_resolve_target_refs()` 会自动推断：

```text
navigate_to → destination 或 target
locate_entity → target
escort_to → actor
```

这会掩盖模型漏掉 target_ref 的错误。

当前 Planner 结果只保存编译后的 TaskPlan，没有保存原始 PlanDraft。

所以无法知道：

```text
target_ref 是模型正确输出的
还是系统自动补上的。
```

---

## 必须分开评价

```text
raw target_ref accuracy
compiled target_id accuracy
repair-applied rate
inference-applied rate
```

在正式 Planner benchmark 中：

```text
raw target_ref 缺失必须判为 Planner 结构错误；
Production 可以自动修复，但需单独报告 repaired success。
```

---

# 25. P0：Step-specific reference 校验仍未闭环

当前 validator 的 role→ID map 只构建：

```text
actor
target
source
destination
```

没有构建：

```text
step_0_target
step_1_destination
...
```

对于任何以：

```text
step_
```

开头的 target_ref，validator 只检查格式和 ID 是否属于允许集合。

它不会检查：

```text
step_1_destination
是否真正绑定到了 step 1 destination ID。
```

因此不同 step 的 ID 仍可能交换。

---

## 25.1 `_resolve_target_refs()` 还有 index bug

当前使用：

```python
step_idx = grounded_task.get("steps", []).index(step_data)
```

如果两个 step_data 内容相同：

```text
.index() 永远返回第一个相同元素的位置
```

会生成错误 step index。

必须改为：

```python
for step_idx, step_data in enumerate(...):
```

---

# 26. P0：当前 Plan Schema 不足以表达 escort 等双参数动作

`TaskStep` 只有：

```text
一个 target_ref
一个 target_id
```

但：

```text
escort_to
guide_to
```

至少需要：

```text
actor
destination
```

`navigate from A to B` 也可能需要：

```text
source
destination
```

当前 `ACTION_TARGET_REFS` 虽允许 actor 或 destination，但单个 step 不能同时绑定两者。

---

## 建议重构

将：

```python
target_ref: str
target_id: str
```

改为：

```python
role_refs: dict[str, str]
role_ids: dict[str, str]
```

例如：

```json
{
  "action": "escort_to",
  "role_refs": {
    "actor": "actor",
    "destination": "destination"
  }
}
```

编译后：

```json
{
  "role_ids": {
    "actor": "...",
    "destination": "..."
  }
}
```

这比继续为每个 action 猜一个主 target_ref 更可靠。

---

# 27. P1：Benchmark status gate 没有正确识别 v3

`_check_benchmark_status()` 只查找：

```text
grounding_benchmark_100_v2.manifest.json
grounding_benchmark_100_v1.manifest.json
```

没有通用查找当前 benchmark 同名 manifest，也没有明确处理 v3/planning manifest。

因此运行 v3 时可能得到：

```text
No manifest found, proceeding
```

即使 manifest status 被改成 blocked，也可能绕过 gate。

---

## 必须修改

统一：

```python
manifest_path = cases_path.with_suffix(".manifest.json")
```

并验证：

```text
benchmark_file_hash
case_count
benchmark_status
source_benchmark_hash
schema_version
```

任何不一致：

```text
拒绝运行
```

---

# 28. P1：Run provenance 仍严重不足

Intent run manifest 只有：

```text
model
repeats
temperature
benchmark path/hash
total_cases
total_evaluations
output_dir
```

缺少：

```text
model digest
Ollama version
code commit
prompt hash
Pydantic schema hash
config snapshot/hash
seed
num_ctx
num_predict
timeout
retry policy
host
start/end time
completion status
Python/package versions
```

Planner 结果目录甚至没有：

```text
run_manifest.json
```

---

## 必须新增

每次实验：

```text
run_manifest.json
config_snapshot.json
prompt_snapshot.txt
environment.json
benchmark_manifest_snapshot.json
code_diff_or_commit.txt
completion.json
```

---

# 29. P1：Thinking JSON 提取实现不够稳健

`_extract_json_from_thinking()` 使用简单 brace depth。

它没有正确处理 JSON string 中的：

```text
"{"
"}"
escaped quote
```

可能在 description 含大括号时提前终止。

更根本的问题是：

```text
不应依赖 reasoning/thinking 文本作为正式 structured output。
```

建议优先：

```text
关闭或限制 thinking
提高 output token budget
使用支持 structured output 的非-thinking模式
```

Thinking fallback 只能作为诊断，不应与正常结果混合。

---

# 30. P1：num_predict 最低 2048 对所有模型生效

当前：

```python
if num_predict < 2048:
    num_predict = 2048
```

注释说是为了 thinking model，但实际对所有模型生效。

这会：

```text
增加小模型延迟
增加无意义输出预算
使不同模型配置不可比
```

应按模型 profile 配置：

```text
thinking model
non-thinking model
small model
large model
```

并在 manifest 中记录真实 options。

---

# 31. P1：Parser 中存在不可达残留代码

`_extract_json_from_thinking()` 的：

```python
return None
```

后面还残留一段解析 EntityReference 的代码。

这段代码永远无法执行，并且没有函数定义头。

虽然不会导致 compile 失败，但说明重构未清理完整。

应删除。

---

# 32. P1：Planner Benchmark Generator 的预期计划过于模板化

当前 expected plan 主要由：

```text
oracle action → 固定一个 planner action
```

生成。

例如：

```text
locate → locate_entity
navigate → navigate_to
```

没有测试：

```text
一个 Intent 是否需要多 action
locate + navigate
find actor + escort
navigate + avoid constraint
clarify + retry
```

因此所谓“task decomposition”实际上尚未测到 decomposition。

当前测到的主要是：

```text
单 action 分类
```

---

# 33. P1：Aggregate Query 的 Planner 语义仍不清楚

例如：

```text
Which room has the most chairs?
How many objects are in room 0753?
```

Grounding 已经得到答案后，Planner expected action 被设为：

```text
locate_entity
```

但真实系统行为可能是：

```text
只返回回答
navigate_to room
report count
```

目前 action taxonomy 没有：

```text
report_result
answer_query
```

所以 aggregate query 被硬塞进 locate_entity。

这会导致模型输出 navigate_to 或 locate_entity 时很难判断谁真正符合系统设计。

需要先明确系统需求：

```text
这些 query 是用户信息查询，
还是机器人要导航到结果位置？
```

然后再设计 expected plan。

---

# 34. 当前测评结果应该如何重新解释

## 34.1 Intent Gemma4

现有可确认：

```text
100/100 能经过 structured output + Pydantic + canonicalization
action partial metric = 69%
target role presence = 100%
relation exact = 0/12
location exact = 0/4
aggregate positive-case accuracy = 3/9
non-default quantifier accuracy = 19/20
```

不能确认：

```text
完整 strict accuracy = 44%
纯 LLM action accuracy = 69%
node_type accuracy低是模型问题
```

---

## 34.2 Intent Qwen2.5 0.5B

现有可确认：

```text
100/100 能经过 structured output + Pydantic + canonicalization
action partial metric = 67%
relation exact = 0/12
location exact = 1/4
aggregate positive-case accuracy = 1/9
non-default quantifier accuracy = 19/20
```

不能确认：

```text
完整 strict accuracy = 4%
```

---

## 34.3 Qwen3.5 4B

当前只完成：

```text
83/100
schema valid 49
schema error 34
```

只能作为：

```text
partial diagnostic
```

不能进入正式模型排名。

---

## 34.4 Planner Gemma4

当前可以写：

> On 25 planner-invoked, single-step oracle-grounding cases containing only `locate_entity` or `navigate_to`, Gemma4 produced the expected one-action sequence in 23 cases.

不能写：

```text
Planner overall accuracy = 92%
Multi-step planning complete
Role binding complete
Planner generalization proven
```

---

## 34.5 Planner Qwen2.5 0.5B

当前可以写：

> Qwen2.5 0.5B produced the expected one-action sequence in 9 of 25 planner-invoked cases and returned system errors in 3 cases.

当前报告中的：

```text
validation pass = 100%
error rate = 0%
```

不可信，应修 evaluator 后重新计算。

---

# 35. 建议的修复顺序

## Phase A：先修 Intent Contract，不再跑新模型

### A1. 统一 schema

```text
[ ] NodeType 统一为 object/place/room/agent
[ ] Prompt 与 Oracle 同步
[ ] EntityReference.node_type 改为 Literal
[ ] Intent Oracle 移除内部 node ID
[ ] location/aggregate location 补 visible text
```

### A2. 修 benchmark

```text
[ ] 发布 intent_benchmark_v2
[ ] exact-ID 与 LLM cases 分离
[ ] action label 人工复核
[ ] 增加 actor/source/destination
[ ] 增加 unsupported/avoid/escort
[ ] 增加至少 15 条 multi-step
```

### A3. 修 paraphrase

```text
[ ] 保留 coordinates
[ ] 保留 room/place identifier
[ ] connected_to 模板
[ ] 删除空文本
[ ] 删除或标记 fragments
[ ] 去重
[ ] 全量人工审查
[ ] 发布 paraphrase_v2
```

---

## Phase B：重写 Intent Evaluator v2

```text
[ ] 保存真实 raw model response
[ ] 保存 parsed intent
[ ] 保存 canonicalized intent
[ ] 保存 rules_applied
[ ] strict 纳入完整 semantic fields
[ ] position metric
[ ] aggregate conditional metrics
[ ] quantifier confusion matrix
[ ] spurious location/relation metrics
[ ] unique cases 与 evaluations 分开
[ ] repeat stability
[ ] per-style metrics
[ ] per-field metrics
```

完成后再跑：

```text
Gemma4 temp0 × 3
Qwen2.5 0.5B temp0 × 3
第三个模型 temp0 × 3
Gemma4 temp0.7 × 3
```

---

## Phase C：修 Parser，但必须保留 raw baseline

```text
[ ] action rule 不再覆盖测评输出
[ ] substring 规则改 regex/token
[ ] 删除 place manipulation bug
[ ] 区分 guide me 与 escort actor
[ ] 删除默认 navigate 或单独标记 fallback
[ ] client dependency injection
[ ] configurable timeout/seed/options
[ ] 删除不可达代码
```

实验顺序：

```text
raw prompt baseline
→ prompt v2
→ canonicalizer v2
```

每次保留独立 experiment ID。

---

## Phase D：重建 Planner Benchmark v2

不要继续修补当前 `planning_benchmark_50_v1`。

建议建立：

```text
planner_generation_60_v2
planner_status_gate_20_v2
```

### Planner generation 60

全部真正调用 Planner：

```text
single-step target       15
source→destination       10
escort/guide             10
avoid/prefer             10
multi-step               15
```

### Status gate 20

```text
ambiguous      10
not_found       5
unsupported     5
```

分开报告，不混入 Planner generation accuracy。

---

## Phase E：重构 Planner role binding

```text
[ ] PlanStep 支持 role_refs/role_ids
[ ] executable action 不允许空 target_ref
[ ] evaluation 模式禁止自动 inference
[ ] raw PlanDraft 单独保存
[ ] repair 前后分开评分
[ ] step-specific map 用 enumerate
[ ] validator 验证 step_ref → exact role ID
[ ] system_error 正确保留
[ ] validation pass 排除 system_error
```

---

## Phase F：最后才做 End-to-End

只有满足：

```text
Intent benchmark v2 ready
Intent evaluator v2 verified
Planner benchmark v2 ready
Planner evaluator v2 verified
```

后再运行：

```text
Natural language
→ LLM Intent
→ Grounding
→ LLM Planner
→ Validator
```

并报告：

```text
raw Intent success
canonicalized Intent success
Grounding success given predicted Intent
Planner success given predicted Grounding
End-to-End strict success
first failure stage
```

---

# 36. 必须新增的测试

## Intent contract

```text
test_node_type_contract_single_form
test_prompt_node_types_match_schema
test_oracle_contains_no_internal_ids
test_location_oracle_has_visible_text
test_spatial_oracle_has_position
```

## Strict evaluator

```text
test_strict_fails_wrong_label
test_strict_fails_wrong_node_type
test_strict_fails_wrong_room
test_strict_fails_wrong_position
test_strict_fails_spurious_location
test_strict_fails_spurious_relation
test_strict_fails_wrong_aggregate_location
```

## Raw/canonicalized trace

```text
test_raw_response_preserved
test_canonicalized_output_separate
test_rules_applied_recorded
test_canonicalization_gain_and_damage
```

## Metrics

```text
test_aggregate_positive_denominator
test_spurious_aggregate_rate
test_quantifier_confusion_matrix
test_unique_cases_vs_evaluations
test_repeat_stability
```

## Paraphrase

```text
test_paraphrase_preserves_coordinates
test_paraphrase_preserves_relation
test_paraphrase_preserves_location_ids
test_no_empty_location_text
test_no_duplicate_instruction
test_connected_to_paraphrase
```

## Planner

```text
test_planner_error_preserved
test_system_error_not_validation_pass
test_empty_target_ref_fails_raw_plan
test_step_ref_exact_role_binding
test_duplicate_step_data_uses_enumerate
test_multi_role_escort_binding
test_source_destination_binding
test_planner_strict_success
```

## Artifact integrity

```text
test_all_result_dirs_have_manifest
test_result_count_matches_manifest
test_partial_run_marked_incomplete
test_prompt_hash_recorded
test_config_hash_recorded
test_model_digest_recorded
```

---

# 37. 下一轮 Agent 强制执行任务书

## Phase 0：冻结当前结果，仅作为 diagnostic

```text
[ ] 将现有 Intent 结果标记 benchmark_version=v1_development
[ ] 将现有 strict_exact 重命名 partial_structural_match
[ ] 将 Qwen3.5 标记 partial 83/100
[ ] 将 Gemma paraphrase 标记 partial 289/300
[ ] 不覆盖现有结果目录
```

---

## Phase 1：建立 Intent schema contract v2

```text
[ ] NodeType Literal 单数化
[ ] Prompt/Few-shot 同步
[ ] Oracle 移除内部 node IDs
[ ] visible location text 完整
[ ] action label 人工 review
```

交付：

```text
intent_schema_contract_v2.md
intent_benchmark_150_v2.jsonl
intent_benchmark_150_v2.manifest.json
intent_benchmark_validation_report.json
```

---

## Phase 2：建立 Intent evaluator v2

```text
[ ] raw/parsed/canonicalized 三层输出
[ ] 完整 strict semantic success
[ ] position/aggregate/relation/location 指标
[ ] repeat stability
[ ] per-style/per-category/per-field
```

交付：

```text
run_intent_eval_v2.py
intent_metrics_v2.py
tests/test_intent_evaluator_v2.py
```

---

## Phase 3：重建 paraphrase v2

```text
[ ] slot-preserving generator
[ ] validator
[ ] 0 duplicate
[ ] 0 empty text
[ ] 0 missing coordinates
[ ] 0 missing relation
[ ] 人工审查记录
```

---

## Phase 4：重新运行模型

每个完整运行必须：

```text
cases complete
repeats complete
run_manifest complete
raw outputs complete
no background unfinished state
```

---

## Phase 5：再修 Planner

```text
[ ] Planner benchmark 60 个真实 invoked cases
[ ] multi-step ≥ 15
[ ] actor/source/destination/constraints 全覆盖
[ ] role_refs schema
[ ] raw vs compiled vs repaired 分开评价
```

---

# 38. 当前阶段结束判断

## Grounding

```text
核心算法：可以冻结
实验归档：仍需补全可复现交付物
```

## Intent

```text
框架原型：已建立
正式 benchmark：不可冻结
正式 evaluator：不可冻结
当前模型分数：只能作为 diagnostic
```

## Planner

```text
单步 smoke test：已完成
完整 Planner evaluation：未完成
multi-step：未验证
role binding：未验证
constraints：未验证
```

## End-to-End

```text
现在不应开始正式 End-to-End 实验。
```

可以先做少量开发调试，但不能生成论文最终指标。

---

# 39. 当前可以写进论文的内容

可以谨慎写：

> A preliminary isolated intent-parsing evaluation framework and an oracle-grounding planner evaluation framework were implemented. The current development benchmarks are reproducible at the file level, and all included benchmark hashes match their manifests. However, the first evaluation iteration revealed schema-contract inconsistencies between prompt conventions and oracle annotations, particularly for node types and location representations. Consequently, the current intent scores are treated as diagnostic results rather than final natural-language accuracy.

Planner 可以写：

> In a preliminary oracle-grounding smoke test consisting of 25 planner-invoked single-step cases, Gemma4 generated the expected action in 23 cases. The benchmark currently covers only single-target `locate_entity` and `navigate_to` actions and therefore does not yet evaluate multi-step decomposition, multi-role binding, or constraint propagation.

---

# 40. 当前不应写进论文的内容

不应写：

```text
Gemma4 Intent strict accuracy = 44%
Qwen2.5 Intent strict accuracy = 4%
Gemma4 Planner overall accuracy = 92%
Planner validation pass = 100%
LLM+RAG End-to-End accuracy 已完成
Multi-step planning 已验证
跨模型稳定性已完成
Paraphrase benchmark 已完成 300 cases
```

---

# 41. 最终评价

v18 的主要价值不是已经得到了最终模型排名，而是：

```text
已经搭建出 Intent 和 Planner 分层测评的基本骨架，
并暴露出了下一阶段必须修复的 schema 与 evaluator 问题。
```

当前最危险的做法是：

```text
继续换模型、继续跑更多 repeats，
但不先修 Benchmark 和 Evaluator。
```

因为这样只会产生更多不可比较的结果。

正确路线是：

```text
冻结 Grounding
→ 统一 Intent schema contract
→ 重建 Intent benchmark v2
→ 修复 strict evaluator
→ 重新跑完整模型结果
→ 重建真正的 multi-step Planner benchmark
→ 修复 Planner raw/compiled/repair 评价
→ 最后运行 End-to-End
```

当前优先级最高的五项是：

```text
1. 统一 object/place/room node_type contract；
2. 修复 strict exact，使完整 EntityReference 进入判定；
3. 保存真实 raw LLM output，分离 canonicalization；
4. 重建语义等价的 paraphrase benchmark；
5. 将 Planner benchmark 改为真实 multi-step、多角色、全部调用 Planner 的 case。
```

完成这五项后，下一轮模型结果才适合作为正式实验结论。
