# Khronos → ANYmal D Waypoint：专用适配开发与无真机验证计划

> **项目边界**
>
> 本项目最终机器人已经固定为 **ANYmal D**。  
> 本文档不设计通用机器人接口，不以 Spot、Nav2、ViPlanner 或其他机器人作为最终执行目标。
>
> 当前目标是：
>
> ```text
> Office 数据集
> → Khronos
> → DSG / PLACES
> → ANYmal D waypoint graph
> → ANYmal D waypoint planner / mission interface
> → ANYmal D
> ```
>
> LLM + RAG / Intent / Planner / Semantic Grounding 部分已经基本完成。后续工作只负责把已有语义输出可靠地连接到 ANYmal D 导航。

---

# 1. 最终目标必须固定

本项目后续唯一主线：

```text
Natural Language
↓
已完成的 LLM + RAG
↓
semantic target_ref
↓
semantic → ANYmal waypoint 映射
↓
ANYmal D waypoint graph
↓
ANYmal D 自己的 graph planner
↓
ANYmal D local planner / locomotion
↓
ANYmal D
```

例如 Office 场景：

```text
“先去办公室 A，再去会议室 B”
```

上层已经完成：

```text
Navigate(R_OFFICE_A)
Navigate(R_CONF_B)
```

后续系统负责：

```text
R_OFFICE_A → WP_xxx
R_CONF_B   → WP_yyy
```

然后目标 waypoint 交给 ANYmal D。

---

# 2. 为什么这里不是“通用 Navigation IR”项目

虽然公开项目中可以找到：

```text
DSG → Spot
Waypoint/Path → ANYmal
semantic map → ANYmal
```

等相近工作，但这些只用于回答：

> 别人如何从场景图/语义地图得到机器人可执行导航信息？

它们不能改变本项目的最终接口。

本项目不做：

```text
GenericRobotAdaptor
SpotAdaptor
Nav2Adaptor
多机器人 executor
```

也不把：

```text
nav_msgs/Path
```

作为最终目标格式，除非后续确认 ANYmal D 原生 waypoint 系统内部确实需要它作为中间接口。

最终必须回到：

```text
ANYmal D waypoint / mission navigation
```

---

# 3. 已知 ANYmal D waypoint 样本：当前最重要的接口依据

用户已经提供一份真实 ANYmal waypoint JSON 样本。

它应被视为当前开发中 **最高优先级的实际接口样本**。

结构：

```json
{
  "header": {
    "frame": "map",
    "stamp": 0
  },
  "nodes": [],
  "edges": []
}
```

当前样本统计：

```text
46 nodes
110 directed edges
frame = map
```

它本质是：

```text
3D waypoint nodes
+
topological directed edges
+
ANYmal-specific navigation policy
```

---

# 4. Node schema

典型 node：

```json
{
  "id": 75,
  "pose_stamped": {
    "header": {
      "frame": "map",
      "stamp": 1746712988.9220421
    },
    "orientation": [1, 0, 0, 0],
    "position": [22.0527, -5.5033, 0.03],
    "tolerance": [0, 0]
  }
}
```

需要从 Khronos PLACES 映射：

```text
Khronos Place ID
→ ANYmal waypoint id

Khronos Place position
→ position[x,y,z]

Khronos Place orientation
→ orientation
```

必须特别检查：

```text
frame
quaternion convention
orientation 是否有实际导航意义
```

如果 orientation 缺失/无意义，不允许静默猜测。

必须定义明确 fallback，例如：

```text
identity quaternion
```

或：

```text
根据相邻 edge 方向生成 yaw
```

哪一种最终采用必须通过 ANYmal 接口/实验验证。

---

# 5. Edge schema

典型 edge：

```json
{
  "id": 140,
  "start_node_id": 74,
  "end_node_id": 75,
  "data": {
    "local_planner": {
      "mode_name": "default",
      "planner_name": "default",
      "planner_parameters": ""
    },
    "locomotion_controller": {
      "motion_parameters": "default",
      "motion_state": "default"
    },
    "max_deviation": 3,
    "perception_parameters": {
      "nfm_preset": "autodeduce"
    },
    "traversability": 1,
    "version": 1
  }
}
```

Khronos PLACES 可以天然提供：

```text
source Place
target Place
graph connectivity
```

但是以下字段属于 ANYmal D 导航策略：

```text
local_planner
locomotion_controller
max_deviation
traversability
nfm_preset
version
```

这些不能假设 Khronos 天然包含。

---

# 6. 样本中策略字段的实际分布

当前提供的样本中：

```text
traversability = 1
→ 82 条 edge

traversability = 0.5
→ 28 条 edge
```

```text
max_deviation = 3
→ 82 条

max_deviation = 2
→ 26 条

max_deviation = 1
→ 2 条
```

大多数 edge：

```text
planner_name = default
mode_name = default
motion_state = default
motion_parameters = default
nfm_preset = autodeduce
```

极少数使用：

```text
stop_and_go
strict_path_follower
perceptive_walk
```

因此第一版 MVP 可以使用：

```text
default planner
default motion
traversability = 1
max_deviation = 3
nfm_preset = autodeduce
```

作为统一默认策略。

但是：

> 这只是根据已有 ANYmal 样本做的保守兼容策略，不代表已经掌握这些字段的完整物理语义。

---

# 7. Khronos PLACES 与 ANYmal D waypoint 的核心关系

本项目核心假设：

```text
Khronos PLACES graph
≈
ANYmal waypoint navigation graph
```

对应：

```text
Place node
→ waypoint node

Place edge
→ waypoint edge
```

因此第一阶段不重新发明导航图。

需要做的是：

> **把 Khronos 已经生成的自由空间拓扑图重新序列化为 ANYmal D waypoint graph。**

这就是本项目最核心的 bridge。

---

# 8. 参考项目的正确定位

以下项目必须下载并研究，但只用于“方法参考”，不是最终架构。

---

## 8.1 MIT-SPARK / Omniplanner

```text
https://github.com/MIT-SPARK/Omniplanner.git
```

只研究：

```text
DSG symbol/node
→ position
→ robot planning command
```

重点回答：

```text
它如何从 DSG node attributes 取导航位置？
它如何处理 frame？
它如何把 graph-grounded target 变成机器人目标？
```

不要：

```text
复制它的 LLM
替换现有 RAG
把 Spot-style compiler 作为最终接口
```

---

## 8.2 MIT-SPARK / Awesome-DCIST-T4

```text
https://github.com/MIT-SPARK/Awesome-DCIST-T4.git
```

只研究：

```text
Hydra DSG
→ planner
→ 真四足机器人执行
```

重点理解：

```text
scene graph 与真实 robot runtime 的连接边界
prior DSG 如何加载
goal 如何提交
robot pose / frame 如何对齐
```

Spot 只是案例。

本项目不移植 Spot executor。

---

## 8.3 leggedrobotics / Tag Map

```text
https://github.com/leggedrobotics/tagmap.git
```

只研究：

```text
semantic map
→ navigable graph / pose graph
→ ANYmal navigation
```

特别关注：

```text
office/lab 环境
semantic target 如何落到可导航位置
全局 graph 与 local planner 如何分层
```

不使用 Tag Map 替代 Khronos。

---

## 8.4 leggedrobotics / ViPlanner

```text
https://github.com/leggedrobotics/viplanner.git
```

只研究 ANYmal D 后半段：

```text
waypoint / goal
→ local path
→ path follower
→ ANYmal command
```

重点回答：

```text
ANYmal D 接收什么导航输入？
frame 是什么？
goal reached 怎么判断？
path follower 如何执行？
```

ViPlanner 不是本项目最终 planner。

它的意义是帮助理解：

> ANYmal D 从“高层 waypoint/global target”向低层运动控制过渡时，公开项目用了什么方法。

---

# 9. 可选参考

如果主四个项目不能回答具体问题，再下载：

```text
leggedrobotics/art_planner
leggedrobotics/nav-suite
ANYbotics/anymal_d_simple_description
MIT-SPARK/Spark-DSG
MIT-SPARK/Khronos
```

其中：

```text
Spark-DSG / Khronos
```

用于确认当前 upstream API。

但是当前项目本身已有 Khronos 源码时：

> 必须以实际项目中使用的版本为准。

---

# 10. Agent 为什么必须本地下载这些仓库

Agent 可能无法访问 GitHub。

因此不要把开发流程写成：

```text
“去 GitHub 看 xxx”
```

而必须先：

```text
下载
冻结 commit
本地阅读
```

建议目录：

```text
external/navigation_references/
```

例如：

```text
external/navigation_references/
├── Omniplanner/
├── Awesome-DCIST-T4/
├── tagmap/
├── viplanner/
├── art_planner/                  # optional
├── nav-suite/                    # optional
├── anymal_d_simple_description/  # optional
├── Spark-DSG/                    # optional/upstream
└── Khronos/                      # optional/upstream
```

---

# 11. 外部代码使用规则

## 11.1 全部只读

Agent 可以：

```text
rg
grep
read
trace call graph
查看 message definition
查看 launch/config
```

不要直接修改外部仓库。

---

## 11.2 固定版本

每个 repo 记录：

```text
remote
branch
commit SHA
download date
```

输出：

```text
REFERENCE_LOCK.tsv
```

---

## 11.3 不盲目安装

当前阶段：

```text
只读源码
```

不要为了研究一个接口就：

```text
编译全部 Omniplanner
部署 Spot stack
训练 ViPlanner
安装整个 nav-suite
```

只有后续需要运行某一模块时才安装。

---

# 12. 后续真正应该开发的模块

推荐专用包：

```text
khronos_anymald_bridge/
```

注意名称：

```text
anymald
```

而不是 generic robot navigation。

结构：

```text
khronos_anymald_bridge/
│
├── docs/
│
├── dsg_reader/
│
├── waypoint_exporter/
│
├── semantic_waypoint_mapper/
│
├── waypoint_graph/
│
├── mock_anymald/
│
├── visualization/
│
├── tests/
│
└── generated/
```

---

# 13. Phase 0 — 下载并审计四个核心参考项目

## 目标

回答：

> 是否已有代码直接解决 Khronos/scene graph 到 ANYmal D waypoint 的转换？

如果没有：

> 哪些成熟方法可以直接借鉴到我们的专用 converter？

---

## 13.1 Omniplanner 审计

Agent 输出：

```text
docs/reference_omniplanner.md
```

必须回答：

```text
DSG node 如何 lookup？
position 如何读取？
frame 如何处理？
target 如何转为 robot goal？
有没有直接复用 graph/path 的代码？
```

只提取对：

```text
DSG → navigation target
```

有用的部分。

---

## 13.2 Awesome-DCIST-T4 审计

输出：

```text
docs/reference_dcist.md
```

回答：

```text
Hydra scene graph 如何进入运行时？
prior DSG 如何工作？
Spot graph/path execution 如何接？
哪些逻辑是 robot-independent？
```

目的：

> 学习“DSG 输出如何真正接到四足机器人”，不是做 Spot。

---

## 13.3 Tag Map 审计

输出：

```text
docs/reference_tagmap.md
```

回答：

```text
semantic target 如何变成 navigable pose？
pose graph 如何构建？
如何对接 ANYmal global/local navigation？
office/lab 环境下有哪些可借鉴的规则？
```

---

## 13.4 ViPlanner 审计

输出：

```text
docs/reference_viplanner.md
```

回答：

```text
ANYmal D 的导航输入是什么？
waypoint 如何提交？
Path 如何执行？
Twist / locomotion 命令如何产生？
goal reached 条件是什么？
frame 约定是什么？
```

---

## 13.5 Phase 0 最终报告

输出：

```text
docs/REFERENCE_METHOD_SUMMARY.md
```

必须分三列：

```text
Reference
Useful method
How we use it in Khronos→ANYmal D
```

禁止出现：

```text
“改成通用 adaptor”
“未来支持 Spot”
“同时支持 Nav2”
```

---

# 14. Phase 1 — 审计当前 Office Khronos DSG / PLACES

这是正式开发前最重要的数据审计。

Agent 必须使用：

```text
当前项目真实 Khronos 代码
+
当前 Office 数据集实际输出
```

而不是仅看 upstream。

---

## 14.1 找到真实 DSG

确定：

```text
DSG 文件
PLACES layer
ROOMS layer
OBJECTS layer
```

---

## 14.2 统计 PLACES

输出：

```text
Place node count
Place edge count
connected components
isolated nodes
position range
edge length distribution
```

---

## 14.3 确认 node attributes

必须用实际代码确定：

```text
ID
position
orientation
distance / clearance（如果有）
timestamp
metadata
```

---

## 14.4 确认 edge representation

必须确认：

```text
directed / undirected
weight
attributes
duplicate edges
self loops
```

---

## 14.5 确认 frame

这是硬阻塞项。

必须回答：

```text
Khronos Place XYZ 在什么 frame？
ANYmal waypoint 使用 map frame。
两者是否一致？
```

如果不一致：

```text
必须找到 transform
```

不能直接写：

```text
frame = map
```

---

## 14.6 输出

```text
docs/dsg_place_schema_report.md
```

必须明确列：

```text
可直接写进 ANYmal node 的字段
需要转换的字段
ANYmal 要求但 Khronos 缺失的字段
```

---

# 15. Phase 2 — 实现 Khronos → ANYmal D Waypoint Exporter MVP

这是项目真正的核心开发阶段。

目标：

```text
Khronos PLACES
→ ANYmal-compatible waypoints.json
```

---

## 15.1 Node export

对每一个 Place：

```text
Place ID
→ node.id

Place position
→ pose_stamped.position

frame
→ pose_stamped.header.frame

orientation
→ pose_stamped.orientation
```

建议：

```text
尽可能保留 Khronos Place ID
```

例如：

```text
Place 368
→ waypoint 368
```

避免：

```text
Place 368
→ waypoint 17
```

除非 ANYmal ID 格式要求不能直接保留。

---

## 15.2 Orientation

三种情况必须区分：

### A. Khronos orientation 真实有效

直接转换。

### B. orientation 是 identity / 无导航意义

记录：

```text
orientation_source = fallback
```

MVP 可以先统一 identity。

### C. ANYmal loader 明确要求沿 edge 的 heading

才根据邻接节点计算 yaw。

不要提前复杂化。

---

## 15.3 Edge export

如果 Khronos PLACES edge 是无向：

```text
A -- B
```

ANYmal 样本需要：

```text
A → B
B → A
```

则 exporter 自动展开。

必须确保：

```text
edge id unique
source node exists
target node exists
```

---

## 15.4 Edge policy MVP

第一版全部使用当前 ANYmal 样本中的主流默认配置：

```yaml
local_planner:
  mode_name: default
  planner_name: default
  planner_parameters: ""

locomotion_controller:
  motion_parameters: default
  motion_state: default

max_deviation: 3
traversability: 1

perception_parameters:
  nfm_preset: autodeduce

version: 1
```

这些值必须放：

```text
config/default_anymal_edge_policy.yaml
```

不要散落硬编码。

---

## 15.5 输出

```text
generated/waypoints.json
generated/export_report.json
```

---

# 16. Phase 2 Validator

必须实现严格 validator。

检查：

```text
JSON parse
header.frame
node id uniqueness
edge id uniqueness
edge source exists
edge target exists
finite XYZ
valid quaternion
duplicate directed edges
self-loop
connected components
isolated nodes
```

还要对比：

```text
source Khronos Place graph
vs
exported ANYmal graph
```

保证 topology 没被破坏。

---

# 17. Phase 3 — Scene Graph 可导航性检查

不要立即做 sparsification。

先直接导出全部 PLACES。

然后检查：

```text
ANYmal waypoint graph 是否过密？
是否存在非常短 edge？
是否有大量无意义 Place？
是否出现靠墙/穿墙的 graph edge？
```

只有实际结果证明：

```text
Khronos PLACES 不能直接作为 ANYmal waypoint graph
```

才增加：

```text
graph filtering
graph sparsification
node merging
```

---

# 18. Phase 4 — Semantic Entity → ANYmal Waypoint

LLM + RAG 已经结束于：

```text
target_ref
```

例如：

```text
R_CONF_B
```

现在建立：

```text
semantic_to_waypoint.json
```

---

## 18.1 目标

```text
R_CONF_B
→ waypoint 368
```

在线执行时就不需要每次重新查询完整 DSG。

---

## 18.2 映射优先级

优先使用 DSG 已有关系：

```text
Room ↔ Place
```

如果一个 Room 有多个 Place：

需要确定一个：

```text
navigation anchor waypoint
```

优先级由真实数据决定，例如：

```text
doorway/entrance Place
>
room representative Place
>
nearest valid Place to room centroid
```

---

## 18.3 映射必须可解释

例如：

```json
{
  "R_CONF_B": {
    "waypoint_id": 368,
    "mapping_method": "room_to_nearest_place",
    "distance_m": 0.74,
    "source_place_id": 368
  }
}
```

---

## 18.4 Fail closed

以下情况不允许强行映射：

```text
无 Place
多个候选无法区分
距离过远
跨楼层
frame 不一致
```

---

# 19. Phase 5 — 无真机 ANYmal Waypoint Planner 模拟

当前上不了真机时，不需要伪造腿式控制。

先模拟：

```text
ANYmal waypoint graph planner
```

---

## 19.1 MockANYmalWaypointPlanner

输入：

```text
waypoints.json
current waypoint
goal waypoint
```

输出：

```text
waypoint ID sequence
```

例如：

```text
WP20
→ WP32
→ WP44
→ WP61
→ WP368
```

---

## 19.2 Algorithm

第一版：

```text
Dijkstra
```

或：

```text
A*
```

edge cost：

```text
Euclidean distance
```

如果后续确认 ANYmal 的：

```text
traversability
```

实际参与全局 cost，再模拟对应规则。

现在不要猜。

---

## 19.3 为什么这个模拟有价值

它验证的是：

```text
我们生成的 ANYmal waypoint graph
```

是否：

```text
合法
连通
可规划
goal 可达
```

而不是验证 ANYmal 腿的物理控制。

---

# 20. Phase 6 — 接 LLM + RAG 完整 Office 闭环

示例：

```text
“先去办公室 A，再去会议室 B”
```

流程：

```text
Natural Language
↓
LLM / Planner
↓
Navigate(R_OFFICE_A)
↓
RAG / Grounding
↓
R_OFFICE_A
↓
semantic_to_waypoint
↓
WP_A
↓
MockANYmalWaypointPlanner
↓
route
↓
Mock reached
```

然后：

```text
R_CONF_B
↓
WP_B
↓
route
↓
reached
↓
TASK_SUCCESS
```

---

# 21. Phase 6 Trace

必须产生可用于论文的日志：

```text
[Intent] PASS
[Planner] PASS

[Grounding:R_OFFICE_A] PASS
[Semantic→Waypoint] WP...
[WaypointGraph] VALID
[Route] WP... → ... → WP...
[Reached] PASS

[Grounding:R_CONF_B] PASS
[Semantic→Waypoint] WP...
[Route] PASS
[Reached] PASS

[TASK] SUCCESS
```

---

# 22. Phase 7 — RViz / Graph 可视化

显示：

```text
Khronos Place nodes
Khronos Place edges

exported ANYmal waypoint nodes
exported ANYmal waypoint edges

semantic target
selected target waypoint
planned route
mock robot
```

最重要的验证：

```text
Khronos PLACES
```

和：

```text
ANYmal waypoints
```

应该空间重合。

---

# 23. Phase 8 — 无真机 Benchmark

至少测：

```text
Waypoint Export Validity
Topology Preservation Rate
Semantic-to-Waypoint Resolution Rate
Waypoint Route Planning Success Rate
End-to-End Semantic Navigation Success Rate
Invalid Waypoint ID Rate
```

Invalid Waypoint ID Rate：

```text
目标 = 0%
```

---

# 24. Phase 9 — ANYmal D 物理仿真

这里只作为真机前验证。

参考 ViPlanner / nav-suite 等公开项目：

```text
waypoint route
↓
local path / path follower
↓
simulated ANYmal D
```

但是必须明确：

> 仿真只是验证生成的 waypoint/global route 可以驱动一个 ANYmal-D 仿真模型完成导航，不等于验证 ANYbotics 私有 native waypoint stack。

---

# 25. Phase 10 — ANYmal D 真机

这是最终目标。

上真机后第一件事不是重新设计导航。

而是找到：

```text
ANYmal waypoint graph loader
waypoint goal API
mission API
navigation status API
```

需要实际检查：

```text
ROS topic
ROS service
ROS action
ANYmal mission/navigation command
```

---

# 26. 真机最终闭环

如果真机确认：

```text
waypoints.json
```

就是正式 graph 输入：

```text
generated waypoints.json
↓
ANYmal native graph loader
↓
goal waypoint ID
↓
ANYmal native global planner
↓
ANYmal local planner
↓
locomotion
```

那么项目闭环完成。

---

# 27. 参考代码最终要回答的问题

四个核心参考仓库的审计不是为了改变架构。

它们只回答：

### Omniplanner

```text
Scene Graph target 怎么可靠落到导航位置？
```

### Awesome-DCIST

```text
Scene Graph 如何真正接四足机器人 runtime？
```

### Tag Map

```text
Semantic Map / graph 到 ANYmal 的 global navigation 如何组织？
```

### ViPlanner

```text
ANYmal D 的 waypoint/path 到 locomotion 中间层如何实现？
```

这些方法如果成熟：

```text
借鉴
```

但最终仍要实现：

```text
Khronos
→ ANYmal D waypoint
```

---

# 28. Agent 开发代码结构

推荐：

```text
khronos_anymald_bridge/
│
├── docs/
│   ├── reference_omniplanner.md
│   ├── reference_dcist.md
│   ├── reference_tagmap.md
│   ├── reference_viplanner.md
│   ├── REFERENCE_METHOD_SUMMARY.md
│   └── dsg_place_schema_report.md
│
├── config/
│   └── default_anymal_edge_policy.yaml
│
├── dsg_reader/
│   └── ...
│
├── waypoint_exporter/
│   ├── exporter.*
│   └── schema.*
│
├── semantic_waypoint_mapper/
│   └── ...
│
├── waypoint_graph/
│   ├── loader.*
│   ├── planner.*
│   └── validator.*
│
├── mock_anymald/
│   └── executor.*
│
├── visualization/
│   └── ...
│
├── tests/
│   └── ...
│
└── generated/
    ├── waypoints.json
    ├── semantic_to_waypoint.json
    └── export_report.json
```

---

# 29. Agent 强制规则

## Rule 1

不重做：

```text
LLM
Intent
Planner
RAG
Grounding
```

只消费：

```text
target_ref
```

---

## Rule 2

最终对象固定：

```text
ANYmal D
```

不要扩展：

```text
Spot
Nav2
Unitree
generic robot
```

---

## Rule 3

四个公开项目只读。

```text
参考方法
≠
项目目标
```

---

## Rule 4

不要修改 Khronos 核心算法。

优先：

```text
读取 DSG
转换 PLACES
导出 waypoint
```

---

## Rule 5

不要猜 ANYmal 私有字段。

如果没有源码/文档：

```text
保持样本默认值
+
明确标注 unknown
```

---

## Rule 6

所有默认策略配置化。

---

## Rule 7

所有 semantic → waypoint 映射可追踪。

---

## Rule 8

遇到 frame / mapping ambiguity：

```text
FAIL
```

不要猜。

---

# 30. Agent 第一阶段直接任务

```text
任务：Phase 0 — 四个公开参考项目离线代码审计

项目最终目标固定：
Khronos → ANYmal D native waypoint navigation。

不要设计通用机器人接口。

1. 确认本地存在：
   - Omniplanner
   - Awesome-DCIST-T4
   - tagmap
   - viplanner

2. 如果不存在，先运行下载脚本。

3. 全程读取本地源码，不依赖 Agent 的 GitHub/WebSearch。

4. 审计 Omniplanner：
   找出 DSG node → position → robot navigation target 的代码路径。

5. 审计 Awesome-DCIST-T4：
   找出 Hydra DSG → planner → Spot runtime 的接口边界。
   只提取可借鉴方法，不开发 Spot。

6. 审计 Tag Map：
   找出 semantic map / pose graph → ANYmal navigation 的方法。

7. 审计 ViPlanner：
   找出 waypoint/path → ANYmal D local navigation / path follower 的实现。

8. 输出：
   docs/reference_omniplanner.md
   docs/reference_dcist.md
   docs/reference_tagmap.md
   docs/reference_viplanner.md
   docs/REFERENCE_METHOD_SUMMARY.md

9. REFERENCE_METHOD_SUMMARY.md 必须回答：
   - 是否找到现成的 scene graph → ANYmal D waypoint converter？
   - Omniplanner 中哪些 DSG→goal 方法可以借？
   - Tag Map 中哪些 graph→ANYmal 方法可以借？
   - ViPlanner 中哪些 ANYmal waypoint/path execution 方法可以借？
   - 哪些部分仍必须我们自己实现？

10. 完成后停止。
    不要写通用 adaptor。
    不要改 LLM/RAG。
    不要开始 Spot/Nav2 集成。
```

---

# 31. Agent 第二阶段直接任务

只有 Phase 0 完成后：

```text
任务：Phase 1 — 当前 Office Khronos PLACES 审计

1. 使用当前项目实际 Khronos 版本。
2. 使用当前 Office 数据集真实 DSG 输出。
3. 找到 PLACES layer。
4. 列出 node:
   - ID
   - position
   - orientation
   - frame
   - relevant attributes
5. 列出 edges:
   - source
   - target
   - directed/undirected
   - weight/attributes
6. 统计 graph：
   - nodes
   - edges
   - connected components
   - isolated nodes
   - edge distance
7. 找到 Room↔Place 的真实关系。
8. 对照用户提供的 ANYmal waypoint JSON schema，
   写出字段映射表。
9. 输出 docs/dsg_place_schema_report.md。
10. 暂时不要改 Khronos。
```

---

# 32. Agent 第三阶段直接任务

```text
任务：Phase 2 — Khronos PLACES → ANYmal D waypoints.json MVP

前提：
Phase 1 已完成。

1. 新建独立 khronos_anymald_bridge。
2. 不修改 Khronos 核心。
3. 遍历全部 Place node。
4. 导出 ANYmal node schema。
5. 尽可能保持 Place ID = waypoint ID。
6. 转换 position/frame/orientation。
7. 遍历全部 Place edge。
8. 按 ANYmal 样本需要展开 directed edges。
9. 填 default ANYmal edge policy。
10. 输出 generated/waypoints.json。
11. 实现 validator。
12. 输出 export_report.json。
13. 对比 source graph 与 exported graph topology。
14. 不做 sparsification。
15. 不自动猜 perceptive_walk。
16. 不自动改变 traversability。
17. 添加测试。
```

---

# 33. 推荐执行顺序

```text
Phase 0
离线审计四个参考项目
      ↓
Phase 1
真实 Office Khronos DSG / PLACES
      ↓
Phase 2
Khronos → ANYmal D waypoints.json
      ↓
Phase 3
Graph validity / 必要时再 sparsify
      ↓
Phase 4
semantic target → ANYmal waypoint
      ↓
Phase 5
Mock ANYmal waypoint planner
      ↓
Phase 6
接已经完成的 LLM + RAG
      ↓
Phase 7
Office E2E
      ↓
Phase 8
可视化 + Benchmark
      ↓
Phase 9
ANYmal-D physics simulation
      ↓
Phase 10
ANYmal D native waypoint 真机
```

---

# 34. 最终一句话

本项目后续不是“做一个通用 scene-graph navigation framework”。

而是：

> **针对具体 ANYmal D 机器人，把 Khronos 在 Office 数据集上生成的 DSG / PLACES 拓扑图，转换为符合 ANYmal D waypoint graph 语法与执行要求的导航文件，并最终让现有 LLM+RAG 的语义目标可以直接触发 ANYmal D 原生 waypoint 规划与导航。**

四个公开项目只是帮助我们少走弯路。

真正必须自己跑通的是：

```text
Khronos
↓
ANYmal D waypoint graph
↓
goal waypoint
↓
ANYmal D navigation
```
