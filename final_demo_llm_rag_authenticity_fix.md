# 最终 Demo 上层语义真实性修正说明
## Frozen LLM+RAG → Khronos → ANYmal D RViz2

> **适用版本**：2026-08-11 最新 `khronos_anymald_bridge`  
> **依据**：对最新代码、`demo/build_real_taskplan.py`、`demo/select_demo_targets.py`、`run_office_e2e.py`、最终 RAG trace、task plan、最终报告及 34 个测试的复审。
>
> **目的**：修正最终 Demo 的上层语义链路，使演示真正证明：
>
> ```text
> 一条人类自然语言任务
> → 已冻结的 LLM / Intent / Planner
> → 已冻结的 Semantic RAG / Graph Grounding
> → DSG semantic target_ref
> → 已完成的 semantic→waypoint bridge
> → ANYmal D waypoint planning
> → RViz2 Mock ANYmal D
> ```
>
> 而不是通过 Python 预先知道答案后构造 task plan。
>
> **重要边界**：
>
> - LLM+RAG 已经过独立 benchmark 验证并冻结，**不要重新设计、训练或替换其算法**。
> - Khronos→waypoint→RViz2 已经基本完成，**本轮不要继续修改其导航算法**。
> - 本轮只修正“最终 Demo 如何真实调用被冻结的 LLM+RAG，并把其真实输出接到下游”。

---

# 1. 当前版本中已经确认正确的部分

最新代码执行：

```bash
python3 -m pytest tests -q
```

结果：

```text
34 passed
```

以下链路已经完成，当前应视为冻结：

```text
Khronos DSG / PLACES
→ graph-aware sparsification
→ component preservation
→ witness paths
→ ground projection for RViz mock
→ semantic room → waypoint
→ witness-aware route planning
→ task_plan → MockExecutor / RViz2
→ checkpoint 1/5 ... 5/5
```

因此当前问题**不是**：

```text
waypoint graph 错了
RViz route 错了
Mock robot 不会移动
```

而是：

> **最终 Demo 的 task plan 并不是由“一条完整的人类自然语言任务”通过冻结的 LLM+RAG 主链真实产生。**

---

# 2. 为什么冻结之前的 LLM+RAG 测试仍然非常重要

冻结的正确含义是：

```text
模块已经通过独立 benchmark
↓
后续不再修改内部算法
↓
把它作为稳定组件直接接到 ANYmal D bridge
```

不是：

```text
Demo 阶段绕过它
```

最终架构必须保持：

```text
┌───────────────────────────────────────┐
│ FROZEN：LLM + RAG                     │
│                                       │
│ Human Natural Language                │
│ ↓                                     │
│ Intent / Task Planner                 │
│ ↓                                     │
│ Semantic RAG                          │
│ ↓                                     │
│ Graph Grounding                       │
│ ↓                                     │
│ target_ref / DSG entity               │
└──────────────────┬────────────────────┘
                   │ stable interface
                   ▼
┌───────────────────────────────────────┐
│ FROZEN：Khronos → ANYmal D Bridge     │
│                                       │
│ target_ref                            │
│ ↓                                     │
│ semantic_to_waypoint                  │
│ ↓                                     │
│ waypoint graph                        │
│ ↓                                     │
│ route                                 │
│ ↓                                     │
│ RViz2 Mock ANYmal D                   │
└───────────────────────────────────────┘
```

因此最终 Demo 的意义恰恰是：

> **第一次把两个已经分别验证的模块真正串起来。**

---

# 3. 当前 Demo 的核心问题一：最终“五目标指令”没有整体进入 LLM

## 3.1 当前代码实际行为

`demo/build_real_taskplan.py` 当前定义：

```python
TARGETS = [
    {"instruction": "navigate to a chair in room 0756"},
    {"instruction": "navigate to a wall-mounted storage unit in room 0759"},
    ...
]
```

然后：

```python
for t in TARGETS:
    pred = run_pipeline(t["instruction"], ...)
```

即实际执行：

```text
单目标句子 1 → LLM+RAG
单目标句子 2 → LLM+RAG
单目标句子 3 → LLM+RAG
单目标句子 4 → LLM+RAG
单目标句子 5 → LLM+RAG
```

之后 Python 才生成：

```text
office_5checkpoint_instruction.txt
```

因此当前方向实际上是：

```text
已预定义的 5 个目标
↓
分别跑 5 次单目标 pipeline
↓
Python 合并 task_chain
↓
Python 再生成一条看起来像“五目标自然语言”的句子
```

不是：

```text
一条五目标自然语言
↓
LLM Planner
↓
自动分解出 5 个 ordered navigation steps
↓
每个 step 进入 RAG/Grounding
```

---

## 3.2 为什么这是实质性问题

最终报告当前写：

> A natural-language office inspection instruction containing five ordered semantic destinations was processed by the existing LLM+RAG pipeline.

这一表述对当前实现来说过强。

当前真正验证的是：

> 5 个单独的自然语言目标分别通过 LLM+RAG，然后由 Python 组合成一个五目标 task plan。

如果论文要展示：

```text
“一个复杂自然语言任务”
→ Task Decomposition
→ 5 个 navigation steps
```

那么必须真正把整条指令送入已冻结的 Planner。

---

## 3.3 必须修改

Agent 必须先检查**当前冻结的 LLM+RAG 项目中正式的 multi-step Planner API**。

最终调用逻辑必须是：

```text
full_instruction
↓
Intent / Planner
↓
step 1
step 2
step 3
step 4
step 5
↓
对每个 step 调用现有 RAG/Grounding
```

禁止：

```text
TARGETS = [...]
for target in TARGETS:
    run_pipeline(...)
```

作为最终 Demo 入口。

如果 `run_pipeline()` 本身只是单目标 benchmark helper：

> 不要强行用它承担 multi-step decomposition。

应调用现有项目正式的：

```text
Intent Parser
Planner
Grounding pipeline
```

按真实系统架构串起来。

---

# 4. 核心问题二：最终用户指令并不是真正的人类语义导航

当前指令：

```text
机器人从当前位置出发，
先前往【the chair in room 0756】...
```

存在两个明显问题：

```text
1. 中英文混合；
2. 直接提供 room 0756 / 0759 / 0764 / ...
```

这实际上把非常关键的空间约束直接告诉了系统。

例如：

```text
the chair in room 0756
```

系统主要做：

```text
chair
+
room number 0756
→ constrained lookup
```

而不是展示：

```text
人类语义描述
→ RAG / aggregate / relation grounding
→ 自己判断是哪一个 Room
```

---

## 4.1 最终输入要求

最终用户输入必须：

```text
100% English
不出现：
- 0756 / 0759 ...
- R0 / R4 ...
- DSG ID
- waypoint ID
```

必须是人类会自然提出的 Office 语义任务。

---

# 5. 核心问题三：当前应该使用 Room-level semantic query，而不是 object + room number

当前 bridge 已经实现的稳定接口是：

```text
semantic Room
→ room anchor waypoint
```

当前并没有正式实现：

```text
Object entity
→ nearest navigable Place
→ object-specific waypoint
```

但当前自然语言却说：

```text
navigate to a chair
navigate to a wall-mounted storage unit
navigate to a large office machine
```

RAG 找到 object 后，代码实际做的是：

```python
room_id = grounding["room_id"]
```

然后：

```text
Object
↓
所在 Room
↓
Room waypoint anchor
```

也就是说：

> 视频中的机器人实际导航到“这个物体所在房间的 anchor”，不是导航到具体物体。

---

## 5.1 最终 Demo 应与真实能力一致

当前不应该声称：

```text
go to the chair
```

而应该使用：

```text
go to the room ...
```

例如 aggregate semantic room query：

```text
the room with the most chairs
the room with the most wall-mounted furniture
the room with the most large office objects
the room with the most small office objects
the room with the most signs
```

这类查询与当前冻结系统的意义高度一致：

```text
Natural language room semantics
↓
LLM intent
↓
Semantic RAG label
↓
Graph aggregate grounding
↓
Room
↓
waypoint
```

---

# 6. 为什么 aggregate semantic room query 不是“临时换方案”

这一点必须明确：

> **Aggregate semantic room query 本来就是冻结的 Semantic RAG + Graph Grounding 能力之一。**

它不是为了 Demo 临时新增的逻辑。

最终 Demo 应该刻意调用前面 benchmark 已经验证过的能力，例如：

```text
aggregate
room_constraint
relation
spatial
```

而不是为了提高成功率退化成：

```text
room number lookup
```

---

# 7. `select_demo_targets.py` 的正确用途

当前：

```text
demo/select_demo_targets.py
```

从 canonical graph 中确定性统计：

```text
某 label 在哪个 Room 数量最多
```

这段代码**可以保留**。

但是它只能用于：

> **离线选择适合录 Demo 的测试题。**

正确：

```text
select_demo_targets.py
↓
发现：
“the room with the most chairs”
在当前 Office 数据上：
- 有唯一答案
- waypoint 可达
- 适合演示
↓
把这一 phrase 放入 Demo instruction
```

正式执行时：

```text
“the room with the most chairs”
↓
被冻结的 LLM+RAG
↓
重新独立得到 Room
```

禁止：

```text
select_demo_targets.py
↓
已经知道 R19
↓
直接把 R19 填进 task plan
```

---

# 8. 核心问题四：TSP 改变了用户明确指定的任务顺序

当前 `build_real_taskplan.py`：

```python
# TSP 最近邻排序
best = min(
    remaining,
    key=lambda s: shortest_path(...)
)
```

而生成的自然语言却说：

```text
Please strictly follow this order.
```

当前原始输入顺序：

```text
0756
→ 0759
→ 0764
→ 0767
→ 0758
```

实际 task plan 经 TSP 后：

```text
0756
→ 0759
→ 0758
→ 0764
→ 0767
```

因此：

```text
用户要求顺序
≠
机器人执行顺序
```

这是确定性逻辑错误。

---

## 8.1 必须修改

最终 Demo：

```text
删除 TSP reorder
```

Planner 输出什么顺序，下游就严格执行什么顺序。

如果未来想测试 route optimization，则用户指令必须明确说：

```text
Visit these destinations in the most efficient order.
```

但这不是当前 Demo 的目的。

---

# 9. 核心问题五：`all ... items` 是 set-query，但最后只执行一次 Room waypoint

当前目标：

```text
all the small office items in room 0758
```

真实 Grounding：

```text
resolved_set(14)
```

这表示：

```text
14 个 object entity
```

但当前 bridge：

```text
resolved_set(14)
↓
room R6
↓
WP8483
↓
只访问一次
```

因此语义上：

```text
“visit all 14 items”
```

被偷偷降级为：

```text
“visit their Room once”
```

---

## 9.1 必须修改

最终五目标 Demo 不使用：

```text
all
every
each object
set navigation target
```

除非真正实现：

```text
set of objects
→ multiple object-specific navigation goals
```

本阶段没有必要实现这一扩展。

---

# 10. 核心问题六：LLM 输出 `locate`，Python 却强制写成 `navigate_to`

当前真实 trace：

```text
chair                 → action=navigate
wall-mounted storage  → action=locate
small office items    → action=locate
large office machine  → action=navigate
small office item     → action=locate
```

但 `build_real_taskplan.py` 无论 Intent 是什么，都写：

```python
"action": "navigate_to"
```

因此：

```text
LLM 真实输出
≠
最终 task plan action
```

这会让报告中：

```text
LLM Planner → navigate steps
```

变得不准确。

---

## 10.1 必须修改

最终 Demo 选择的自然语言表达必须让：

```text
5/5 step action = navigate
```

真实由 LLM 输出。

不能通过：

```text
locate → Python 强制改 navigate_to
```

解决。

如果某个 phrase 解析为 `locate`：

```text
换一个更清晰的自然语言表达
```

例如：

```text
Go to the room ...
Navigate to the room ...
Proceed to the room ...
```

但仍然不得增加 room number / internal ID。

---

# 11. 核心问题七：`run_office_e2e.py` 的 Intent / Planner PASS 是硬编码

当前：

```python
trace_lines.append("[Intent] PASS")
trace_lines.append("[Planner] PASS")
```

无论真实 LLM：

```text
action 是否正确
step 数是否正确
顺序是否正确
```

都会输出：

```text
[Intent] PASS
[Planner] PASS
```

这不是有效验证。

---

## 11.1 必须修改

最终 task plan 应附带真实上游 validation，例如：

```text
expected navigation steps = 5
actual navigation steps   = 5

expected semantic phrases = [...]
planner semantic phrases  = [...]

all action=navigate?
order preserved?
grounding status valid?
```

只有实际满足后：

```text
[Intent] PASS
[Planner] PASS
```

否则：

```text
FAIL
```

---

# 12. 核心问题八：Demo 目录中存在两套互相矛盾的 source of truth

当前：

```text
demo/office_5checkpoint_targets.json
```

是旧 aggregate candidate：

```text
R0 / R4 / R6 / R14 / R19
```

而：

```text
demo/office_5checkpoint_task_plan.json
```

是另一套：

```text
R4 / R7 / R6 / R12 / R15
```

同时：

```text
build_real_taskplan.py
```

并没有读取：

```text
office_5checkpoint_targets.json
```

而是把 TARGETS 硬编码在代码里。

这会造成：

```text
report
task plan
candidate selection
instruction
```

之间出现版本混乱。

---

## 12.1 必须修改

最终目录建议：

```text
demo/
├── office_demo_candidates.json
│   # select_demo_targets.py 的离线候选，只用于选题
│
├── final_instruction.txt
│   # 最终唯一的一条用户输入
│
├── final_llm_plan.json
│   # 整条 instruction 经真实 Planner 后的输出
│
├── final_grounding_trace.json
│   # 5 steps 分别经过 RAG/Grounding 的输出
│
├── final_task_plan.json
│   # Grounded target_ref / target_id
│
├── final_bridge_trace.txt
│
└── final_demo_manifest.json
```

旧 demo 产物：

```text
移入 legacy/
```

或者删除。

最终只能存在：

> **一套 source of truth。**

---

# 13. 核心问题九：`mistral:latest` 不能作为论文最终可复现配置

当前：

```python
--model mistral:latest
```

`latest` 未来可能变化。

最终实验至少必须记录：

```text
model name
exact tag
Ollama digest/hash
runtime version
temperature
其他 inference options
date/time
```

最终 `demo_manifest.json` 应保存这些信息。

这不是当前功能 bug，但属于最终论文实验可重复性要求。

---

# 14. 最终正确的 Demo 流程

必须变成：

```text
STEP 0
select_demo_targets.py
只用于挑选候选语义 phrase
不产生最终答案
              ↓
STEP 1
构造一条纯英文、无 ID 的五目标人类自然语言
              ↓
STEP 2
整条 instruction → Frozen LLM Planner
              ↓
得到 5 个 ordered navigate steps
              ↓
STEP 3
每一个 Planner step
→ Frozen Semantic RAG
→ Frozen Graph Grounding
              ↓
得到 5 个 DSG semantic target_ref
              ↓
STEP 4
验证：
5/5 action=navigate
5/5 grounding success
顺序不变
目标唯一
              ↓
STEP 5
target_ref
→ Frozen semantic_to_waypoint
              ↓
5 个 ANYmal waypoint
              ↓
STEP 6
Frozen waypoint graph planner
→ 5 段 route
              ↓
STEP 7
同一份真实 task plan
直接驱动 RViz2 Mock ANYmal D
              ↓
5/5 CHECKPOINT REACHED
              ↓
TASK_SUCCESS
```

---

# 15. 最终自然语言应具备的形式

最终 phrase 必须通过真实 pipeline 自动筛选。

目标形式例如：

```text
the room with the most chairs
the room with the most wall-mounted furniture
the room with the most large office objects
the room with the most small office objects
the room with the most signs
```

但是：

> **不能仅因为 canonical graph 显示这些查询有答案，就宣布它们可以用于最终 Demo。**

每一个候选必须真实运行：

```text
LLM Intent
+
Semantic RAG
+
Graph Grounding
```

确认：

```text
action = navigate
grounding = resolved
unique room
valid waypoint
route reachable
```

然后才进入最终 instruction。

---

# 16. 最终五目标指令的格式示例

最终真实 phrase 确定后，格式可以类似：

```text
Starting from your current position, first go to the room with the most chairs.
Then go to the room with the most wall-mounted furniture, followed by the room
with the most large office objects, the room with the most small office objects,
and finally the room with the most signs. Stop briefly at each destination
before continuing to the next one.
```

注意：

```text
这只是格式示例。
```

Agent 必须通过真实冻结 pipeline 验证每个 phrase 后，才能确定最终五目标。

---

# 17. Multi-step Planner 的验收是这次新增的唯一真正新测试

冻结的 RAG benchmark 已经验证过很多单目标语义能力。

最终 Demo 的新增价值是：

```text
一条 natural-language instruction
→ 5 ordered sub-tasks
```

所以应新增一个集成测试：

```text
test_final_demo_multistep.py
```

至少验证：

```text
full instruction → exactly 5 steps
all 5 actions == navigate
semantic target phrases 顺序正确
无 room number / R* / waypoint ID 出现在 input
5 steps 全部 grounding success
5 target_ref 全部可转 waypoint
5 routes 全部 reachable
```

这是 integration test。

不要改已有冻结 benchmark 定义。

---

# 18. 最终 trace 必须同时包含上层和下层证据

最终视频旁边/报告中应保存：

```text
[INPUT]
Full natural-language instruction

[LLM PLANNER]
Step 1: ...
Step 2: ...
Step 3: ...
Step 4: ...
Step 5: ...

[RAG/GROUNDING]
phrase 1 → R...
phrase 2 → R...
...

[SEMANTIC→WAYPOINT]
R... → WP...
...

[ROUTE]
segment 1 cost ...
...
segment 5 cost ...

[RVIZ]
Checkpoint 1/5 REACHED
...
Checkpoint 5/5 REACHED

[TASK]
SUCCESS
```

这样才能证明整条链路。

---

# 19. 明确禁止的修复方式

Agent 不得通过以下方式“让 Demo 通过”：

```text
❌ 给自然语言加入 Room number
❌ 给自然语言加入 R*
❌ 给自然语言加入 waypoint ID
❌ 预先把 5 个句子分好再声称 Planner 分解
❌ Python aggregate 直接生成最终 target_ref
❌ TSP 偷偷重排用户顺序
❌ locate 强制改为 navigate
❌ resolved_set 多 object 偷偷降成一个 Room visit
❌ run_office_e2e 硬编码 Intent/Planner PASS
❌ 为了 Demo 修改冻结 benchmark ground truth
```

---

# 20. 本轮明确不要修改的部分

以下模块已经完成，除非为了接入真实 task plan 有非常薄的接口修改，否则不要动：

```text
Khronos DSG export
Dense waypoint exporter
Geodesic/component-aware sparsifier
Witness path construction
Ground projection
semantic_to_waypoint
WaypointGraph planner
MockExecutor
RViz graph publisher
RViz checkpoint state machine
RViz robot animation
```

不要重新做：

```text
Khronos → ANYmal graph algorithm
```

---

# 21. Agent 修改任务

## Part A — 清理当前错误 Demo

1. 将当前：

```text
office_5checkpoint_instruction.txt
office_5checkpoint_task_plan.json
office_5checkpoint_rag_trace.json
```

标记为：

```text
legacy_realrag_v1
```

不要再作为最终论文 Demo。

2. `office_5checkpoint_targets.json` 改名：

```text
office_demo_candidates.json
```

明确：

```text
只用于离线挑选 candidate phrases
```

---

## Part B — 构造真正的五目标 natural-language test

3. 从冻结系统已经验证的 semantic/aggregate query 类型中选候选。

4. 每个 candidate 单独做预检：

```text
LLM action=navigate
RAG label stable
Grounding unique Room
waypoint valid
route reachable
```

预检只负责：

```text
选题
```

不进入最终 task plan。

5. 选出 5 个稳定且不同的 Room，且都不同于 start。

6. 用这 5 个 phrase 构造：

```text
一条完整纯英文指令
```

输入中不允许内部 ID。

---

## Part C — 调用冻结的正式 Planner

7. 找到现有 LLM+RAG 项目的正式 multi-step Planner 调用链。

8. 将完整 instruction 一次性输入 Planner。

9. Planner 必须真实输出：

```text
5 ordered navigation steps
```

10. 不允许 Python 预先拆句代替 Planner。

11. 不允许 TSP 重排。

---

## Part D — 调用冻结 RAG/Grounding

12. 对 Planner 的 5 个 step 分别调用现有 Semantic RAG + Grounding。

13. 保存真实：

```text
intent
semantic retrieval
grounding
target_ref
target_id
```

14. 只有：

```text
5/5 action=navigate
5/5 grounding success
```

才允许进入 bridge。

---

## Part E — 下游保持原逻辑

15. 使用当前已完成的：

```text
NavigationGoalCompiler
```

转换：

```text
target_ref → waypoint
```

16. 使用当前已完成的 witness-aware planner。

17. 同一份 final task plan 直接驱动 RViz。

18. 不允许人工再次写：

```text
--goal-rooms
```

---

# 22. 最终验收标准

必须全部满足：

```text
[ ] 输入是一条完整英文自然语言
[ ] 输入无 Room number
[ ] 输入无 R*
[ ] 输入无 waypoint ID
[ ] Planner 一次性接收完整 instruction
[ ] Planner 输出 exactly 5 navigation steps
[ ] 5/5 LLM action=navigate
[ ] 用户顺序 == Planner 顺序 == RViz 执行顺序
[ ] 无 TSP reorder
[ ] 5/5 Semantic RAG real retrieval
[ ] 5/5 Graph Grounding success
[ ] 5 target_ref 均为真实 DSG entity
[ ] 5/5 semantic→waypoint success
[ ] 5/5 route reachable
[ ] 五目标均不是 start
[ ] 不使用 set/all query 伪装一个 waypoint
[ ] Intent/Planner PASS 非硬编码
[ ] RViz 使用同一份真实 final_task_plan
[ ] CHECKPOINT 1/5 → 5/5
[ ] TASK_SUCCESS
```

---

# 23. 最终产物

Agent 最终提交：

```text
demo/final_instruction.txt
demo/final_llm_plan.json
demo/final_grounding_trace.json
demo/final_task_plan.json
demo/final_bridge_trace.txt
demo/final_demo_manifest.json
docs/final_e2e_demo_report_v2.md
```

报告必须给出一张表：

| # | Human semantic phrase | LLM action | Semantic RAG | Grounding target_ref | Waypoint | Route cost |
|---:|---|---|---|---|---:|---:|
| 1 | ... | navigate | ... | R... | WP... | ... |
| 2 | ... | navigate | ... | R... | WP... | ... |
| 3 | ... | navigate | ... | R... | WP... | ... |
| 4 | ... | navigate | ... | R... | WP... | ... |
| 5 | ... | navigate | ... | R... | WP... | ... |

---

# 24. 模型复现要求

最终 Demo 禁止仅记录：

```text
mistral:latest
```

至少记录：

```text
model tag
model digest/hash
Ollama version
inference options
temperature
timestamp
```

如果最终仍使用 `latest` 作为启动 alias：

> manifest 中也必须记录本次运行时实际解析到的 model digest。

---

# 25. 对最终论文表述的修正

修复完成后才可以写：

> **A single natural-language office navigation instruction containing five ordered semantic destinations was decomposed by the frozen LLM planner. Each navigation step was independently resolved by the frozen Semantic RAG and graph-grounding pipeline into a DSG semantic room entity. The grounded room references were deterministically compiled into Khronos-derived ANYmal-D waypoint anchors, planned over the graph-aware waypoint representation, and executed sequentially by the RViz2 mock ANYmal-D visualization.**

当前版本在修复前，只能准确描述为：

> **Five separately specified natural-language targets were independently grounded by the LLM+RAG pipeline and subsequently combined by deterministic Python logic into a waypoint execution task.**

二者不能混淆。

---

# 26. 最终原则

这次不要为了 Demo 成功率降低系统难度。

最终 Demo 的意义应该是：

```text
前面冻结的 LLM+RAG
        +
后面完成的 Khronos→ANYmal bridge
```

第一次真正连成一个系统。

正确执行链必须始终保持：

```text
Human Natural Language
↓
FROZEN LLM Planner
↓
FROZEN Semantic RAG
↓
FROZEN Graph Grounding
↓
target_ref
↓
FROZEN Khronos→ANYmal bridge
↓
ANYmal waypoint
↓
RViz2
```

`select_demo_targets.py` 可以帮助**选题**。

它绝不能替代：

```text
LLM + RAG + Grounding
```

参与正式执行。
