# Khronos → ANYmal D 语义导航：后续开发、开源复用与无真机验证计划

> **状态基线（2026-08-09）**  
> LLM + RAG / Intent / Planner / Semantic Grounding 部分视为已基本完成。后续工作不重做 LLM+RAG，而是解决 **Khronos DSG / PLACES → 可执行导航目标与路线 → ANYmal D / 仿真执行器**。
>
> 当前实验场景为 **Office 数据集**。本文示例中的 `Office A`、`Conference Room B`、`R_OFFICE_A` 等只是占位符；真实开发必须使用当前 Office DSG/RAG 中实际存在的实体名称和 ID。

---

# 1. 本轮全网 / GitHub 调研后的关键修正

之前的初步方案默认：

```text
Khronos DSG
↓
PLACES
↓
直接生成 ANYmal waypoints.json
↓
ANYmal
```

经过对公开 GitHub 项目的进一步检索，这个结论需要修正：

> **不要在一开始就假设必须生成完整的 ANYmal `waypoints.json`。**

目前没有找到公开的：

```text
Khronos / Spark-DSG
→ 用户提供的 ANYmal waypoint JSON schema
→ ANYmal D
```

完整开源 bridge。

但是已经找到两条非常接近的公开实现：

```text
MIT-SPARK:
Hydra DSG
→ Grounding / Planning
→ robot-specific plan compilation
→ Spot
```

以及：

```text
ETH / leggedrobotics:
Waypoint / Path / semantic-local planning
→ path follower
→ ANYmal C/D
```

因此当前最合理的研发策略是：

```text
先研究已有实现
↓
确认可复用接口
↓
再决定：
A. 生成 ANYmal native waypoint JSON
或
B. 直接生成 Path / waypoint goal
或
C. 两种 adaptor 都保留
```

---

# 2. 必须本地保存并让 Agent 阅读的开源仓库

Agent 不应依赖实时 GitHub 浏览。正式开发前，应将以下仓库下载到项目附近的本地只读目录，并记录 commit SHA。

推荐目录：

```text
external/navigation_references/
```

## 2.1 第一优先级：必须阅读

### A. MIT-SPARK / Omniplanner

Repository:

```text
https://github.com/MIT-SPARK/Omniplanner.git
```

意义：

```text
DSG-grounded planning
→ robot-specific plan compilation
```

这是当前最重要的架构参考。

其 README 明确把系统拆为：

```text
grounding
problem solving
robot-specific plan post-processing
runtime monitoring
```

重点不是重用它的 LLM 或 Planner，而是研究：

> **它如何把 DSG-grounded planner 的输出变成机器人可执行输入。**

Agent 优先阅读：

```text
README.md

omniplanner/
  src/omniplanner/
    goto_points.py
    compile_plan.py            # 若当前版本存在/相关实现
    omniplanner.py

omniplanner_ros/
  src/omniplanner_ros/
    goto_points_ros.py
    omniplanner_node.py
```

尤其重点看：

```text
goto_points.py
```

中的：

```text
DSG node
→ node.attributes.position
```

以及：

```text
goto_points_ros.py
```

中的：

```text
GotoPointsPlan
→ Follow(...)
```

和：

```text
GotoPointsPlan
→ nav_msgs/Path
```

这一部分与本项目未来的 `ANYmalAdaptor` 高度相关。

---

### B. leggedrobotics / ViPlanner

Repository:

```text
https://github.com/leggedrobotics/viplanner.git
```

意义：

公开 README 明确说明其 ROS Node 面向 ANYmal / legged platform，包含：

```text
planner
visualizer
pathFollower
RViz WaypointTool
```

其中：

```text
pathFollower
→ 将规划 path 转成 twist commands
→ robot execution
```

Agent 必须重点阅读：

```text
ros/README.md
ros/
```

然后定位：

```text
pathFollower
waypoint input
path output
Twist output
frame conventions
goal completion condition
```

本项目要回答的关键问题：

> 如果我们已经能从 Khronos PLACES 产生 global waypoint sequence / Path，是否可以直接接到 ViPlanner / path follower，而完全绕过 ANYmal 私有 `waypoints.json`？

---

### C. MIT-SPARK / Awesome-DCIST-T4

Repository:

```text
https://github.com/MIT-SPARK/Awesome-DCIST-T4.git
```

意义：

这是非常重要的 **真实机器人系统集成参考**。

重点研究：

```text
Hydra / DSG
→ Omniplanner
→ robot executor
→ Spot
```

以及：

```text
spot_prior_dsg
real_spot
prior scene graph
online scene graph planning
```

Agent 不需要把整个 DCIST 系统跑起来。

目标是找出：

```text
DSG 如何进入 planner
planner 输出如何进入 executor
robot frame 如何配置
path / action 如何发送
```

---

## 2.2 第二优先级：用于 ANYmal 局部导航设计

### D. leggedrobotics / ArtPlanner

```text
https://github.com/leggedrobotics/art_planner.git
```

用途：

```text
2.5D terrain / traversability
→ local navigation planning
→ nav_msgs/Path
```

用于理解：

```text
global semantic / topological planner
```

和：

```text
ANYmal terrain-aware local planner
```

之间的职责边界。

不要把 ArtPlanner 当作 Khronos PLACES 替代品。

---

### E. leggedrobotics / Tag Map

```text
https://github.com/leggedrobotics/tagmap.git
```

用途：

主要作为系统设计和实验参考。

其相关工作展示了类似：

```text
semantic map
→ navigable pose graph
→ global path
→ local planner
→ ANYmal
```

并包含 office / lab 类型环境。

Agent 重点看：

```text
semantic target 如何变成导航目标
pose graph / navigation 部分如何组织
```

不是为了用 Tag Map 替换当前已经完成的 RAG。

---

## 2.3 第三优先级：用于无真机物理仿真

### F. leggedrobotics / nav-suite

```text
https://github.com/leggedrobotics/nav-suite.git
```

用途：

ANYmal-D / IsaacLab 导航仿真参考。

目标：

```text
后期把 MockExecutor
替换为
PhysicsSimulationExecutor
```

Agent 首先只研究接口和运行条件，不要在 Phase 0 就安装/训练。

---

### G. ANYbotics / anymal_d_simple_description

```text
https://github.com/ANYbotics/anymal_d_simple_description.git
```

用途：

确认公开 ANYmal D robot description 的边界。

非常重要：

> 公开仓库只提供简化 description；完整 ANYmal D simulation/control software 不是完全公开的。

因此论文中不能把公开仿真替代栈写成“已验证 ANYbotics 私有真机导航栈”。

---

## 2.4 DSG API 参考

### H. MIT-SPARK / Spark-DSG

```text
https://github.com/MIT-SPARK/Spark-DSG.git
```

用于确认：

```text
node attributes
Place attributes
edge attributes
Python/C++ API
serialization
```

如果当前 Khronos workspace 已经包含对应源码，应优先分析项目实际使用的版本；本地 reference clone 仅用于对照。

---

### I. MIT-SPARK / Khronos

```text
https://github.com/MIT-SPARK/Khronos.git
```

如果当前项目已经有 Khronos 源码，不重复使用 reference clone 替换它。

reference copy 仅用于：

```text
upstream comparison
API documentation
commit comparison
```

---

# 3. 外部仓库的使用规则

## Rule A — 必须固定版本

下载完成后记录：

```text
repository
branch
commit SHA
download date
```

禁止 Agent 在开发过程中无提示地：

```text
git pull
```

导致参考接口发生变化。

---

## Rule B — 外部仓库默认只读

建议：

```text
external/navigation_references/
```

全部作为：

```text
READ ONLY
```

本项目真正代码写在自己的：

```text
khronos_anymal_bridge/
```

不要直接魔改：

```text
Omniplanner
ViPlanner
Khronos upstream
```

除非最终明确决定 fork。

---

## Rule C — 不盲目安装全部 dependency

Phase 0/1 只做：

```text
static code reading
interface tracing
schema tracing
```

暂时不要：

```text
构建 Awesome-DCIST 整个 workspace
安装 Spot SDK
编译所有 ViPlanner dependency
训练 policy
```

这些都不是当前阶段必要条件。

---

# 4. 当前 ANYmal waypoint JSON 样本给出的事实

用户已有一个实际 ANYmal waypoint JSON 样本。

它不是单纯参数文件，而是：

```text
Navigation Waypoint Graph
=
Nodes
+
Edges
+
Robot-specific edge policies
```

样本统计：

```text
46 nodes
110 directed edges
header.frame = map
```

主要边策略：

```text
traversability = 1
max_deviation = 3
```

是主流默认组合。

少量 edge 使用：

```text
traversability = 0.5
max_deviation = 1 / 2

stop_and_go
strict_path_follower
perceptive_walk
```

因此可以确认 schema 形状，但当前不能仅凭 JSON 推断这些 ANYmal 私有字段的完整语义。

---

# 5. Khronos PLACES 与 waypoint graph 的天然对应关系

当前核心判断仍然成立：

```text
Khronos PLACES node
≈ navigation waypoint node

Khronos PLACES edge
≈ topological navigation edge
```

可直接研究的字段：

```text
Place ID
position[x,y,z]
orientation（需验证是否有导航意义）
connectivity
edge attributes / weight
```

但 ANYmal 样本中的：

```text
local_planner
locomotion_controller
max_deviation
traversability
nfm_preset
```

不是 Spark-DSG 通用 EdgeAttributes 天然保证具备的字段。

因此：

> **DSG 提供导航图的 geometry + topology。**
>
> **机器人 adaptor 决定如何把它编译为具体执行接口。**

---

# 6. 新的核心架构：不要把系统绑死到 JSON

推荐增加一个本项目自己的中间表示：

```text
NavigationTarget
GlobalRoute
```

例如：

```text
NavigationTarget:
  semantic_ref: R_CONF_B
  place_id: P368
  frame: map
  position: [x, y, z]
  orientation: [...]
```

以及：

```text
GlobalRoute:
  start_place: P120
  goal_place: P368
  place_sequence:
    - P120
    - P137
    - P201
    - P368
  pose_sequence:
    - ...
```

上层只生产：

```text
NavigationTarget / GlobalRoute
```

下层再选择 adaptor。

---

# 7. 三种执行 Adaptor

## Adaptor A — ANYmal Native Waypoint JSON

如果实验室 ANYmal 真机确认：

```text
waypoints.json
```

确实是正式导航 graph 输入，并有 API：

```text
load graph
set waypoint goal
execute
```

则实现：

```text
GlobalRoute / PLACES
↓
AnyMalWaypointJsonAdaptor
↓
waypoints.json
```

第一版特殊 edge policy 采用配置化默认值，不猜私有参数。

---

## Adaptor B — ROS Path / Waypoint

如果 ViPlanner / ArtPlanner / 实验室 ANYmal stack 可以直接消费：

```text
PoseStamped
Path
waypoint goal
```

则实现：

```text
GlobalRoute
↓
RosPathAdaptor
↓
nav_msgs/Path
↓
local planner / pathFollower
```

这种方式很可能比维护 ANYmal 私有 graph schema 更简单。

---

## Adaptor C — Mock / Simulation

用于无真机验证：

```text
GlobalRoute
↓
MockExecutor
```

后期：

```text
GlobalRoute
↓
SimulatorExecutor
↓
ANYmal-D simulation
```

---

# 8. Offline / Online 分层

## 8.1 Offline

```text
Office Dataset / ROS bag
      ↓
   Khronos
      ↓
     DSG
      │
      ├── semantic entities
      │
      └── PLACES graph
             ↓
      navigation preparation
```

可以预生成：

```text
semantic_to_navigation_target.json
```

例如：

```json
{
  "R_CONF_B": {
    "place_id": 368,
    "frame": "map",
    "mapping_method": "room_to_place",
    "source": "khronos_dsg"
  }
}
```

如果最终需要 native graph：

```text
同时生成 waypoints.json
```

---

## 8.2 Online

```text
Natural Language
↓
已经完成的 LLM + RAG
↓
target_ref = R_CONF_B
↓
semantic navigation index
↓
NavigationTarget(P368)
↓
Global Planner / PLACES graph
↓
GlobalRoute
↓
selected robot adaptor
↓
executor
```

这样在线阶段不需要每次完整遍历 DSG。

---

# 9. 阶段化开发流程

---

# Phase 0 — 下载并冻结外部参考代码

## 目标

保证后续 Agent 即使不能访问 GitHub，也能阅读全部关键参考源码。

## 执行

运行本文配套：

```text
download_navigation_references.sh
```

它应：

```text
clone --depth 1
record commit SHA
generate REFERENCE_LOCK.tsv
optionally pack all repos into tar.gz
```

## 必须下载

```text
Omniplanner
Awesome-DCIST-T4
viplanner
art_planner
tagmap
nav-suite
anymal_d_simple_description
Spark-DSG
Khronos
```

## 验收

```text
[ ] 每个 repo 可本地读取
[ ] 每个 repo 有 HEAD commit
[ ] REFERENCE_LOCK.tsv 已生成
[ ] Agent 不需要联网即可 grep / read code
```

如果工作机器不能访问 GitHub：

> 在任意可联网机器运行下载脚本，再把生成的 `navigation_references_<date>.tar.gz` 复制到 Agent 所在机器。

---

# Phase 1 — 已有实现审计：先决定“复用什么”

## 目标

回答一个问题：

> **从 Khronos navigation target 到 ANYmal 执行，哪些代码已经有人写了？**

暂时不要开发 bridge。

---

## 1A. Omniplanner 审计

Agent 必须追踪：

```text
DSG
→ symbol/node grounding
→ node.attributes.position
→ GotoPointsPlan
→ compile_plan
→ Follow / nav_msgs/Path
```

输出：

```text
docs/reference_audit_omniplanner.md
```

必须记录：

```text
关键 class/function
input type
output type
frame handling
可直接复用代码
只能借鉴的代码
与现有 RAG 重复的部分
```

特别注意：

> 本项目 LLM+RAG 已经完成，所以不需要把 Omniplanner 的 grounding/planning 全部搬过来。

我们重点借：

```text
robot-specific plan compiler / adaptor pattern
```

---

## 1B. ViPlanner 审计

追踪：

```text
WaypointTool
→ planner input
→ path
→ pathFollower
→ twist
```

输出：

```text
docs/reference_audit_viplanner.md
```

回答：

```text
是否可以外部发送 waypoint？
是否可以外部发送 path？
path follower 订阅什么？
输出什么 twist topic/message？
ANYmal D 的 frame 约定是什么？
goal reached 如何判断？
```

---

## 1C. DCIST / Spot 审计

输出：

```text
docs/reference_audit_dcist.md
```

重点回答：

```text
Hydra DSG 如何进入 Omniplanner？
prior DSG 如何使用？
robot executor 的边界在哪里？
真实 robot command 如何从 plan 产生？
```

这是架构验证，而不是把 Spot 代码移植给 ANYmal。

---

## 1D. Tag Map / ArtPlanner / nav-suite

做简短对照报告：

```text
docs/reference_audit_legged_navigation.md
```

分别判断：

```text
Tag Map:
可借鉴 semantic→navigation architecture？

ArtPlanner:
可作为 local terrain planner？

nav-suite:
可作为后期 ANYmal-D physics simulator？
```

---

## Phase 1 验收

必须生成一个明确的：

```text
REUSE_DECISION.md
```

格式：

```text
Omniplanner:
  REUSE / ADAPT / REFERENCE ONLY

ViPlanner:
  REUSE / ADAPT / REFERENCE ONLY

ArtPlanner:
  ...

Native waypoints.json:
  REQUIRED / OPTIONAL / UNKNOWN
```

在这一步完成前：

> **禁止正式写 `DSG → waypoints.json` exporter。**

---

# Phase 2 — 审计当前项目真实 Khronos DSG / PLACES

## 目标

不是看 upstream 文档，而是看：

> **当前项目真正跑出的 DSG。**

## Agent 任务

找到当前 Office 数据集跑 Khronos 后的真实：

```text
DSG output
PLACES layer
ROOMS
OBJECTS
edges
```

检查：

```text
Place node count
Place edge count
node ID type
position
orientation
edge representation
edge weight
frame
Room↔Place relation
connected components
isolated nodes
```

生成：

```text
docs/dsg_place_schema_report.md
```

以及一个小 fixture：

```text
tests/fixtures/sample_office_dsg.*
```

## 验收

Agent 必须能用实际代码回答：

```text
for place in places:
    ID?
    position?
    orientation?

for edge in place_edges:
    source?
    target?
```

不能仅依据论文或 README 猜。

---

# Phase 3 — 建立统一 Navigation IR 和 Semantic Navigation Index

## 目标

先完成机器人无关的中间层：

```text
target_ref
→ NavigationTarget
→ GlobalRoute
```

而不是直接绑定 ANYmal JSON。

---

## 3A. Semantic Index

生成：

```text
semantic_to_navigation_target.json
```

例如：

```json
{
  "R_OFFICE_A": {
    "place_id": 122,
    "mapping_method": "room_to_place",
    "frame": "map"
  },
  "R_CONF_B": {
    "place_id": 368,
    "mapping_method": "room_to_place",
    "frame": "map"
  }
}
```

真实实体必须来自 Office DSG。

---

## 3B. Mapping Policy

优先级：

```text
已有 Room↔Place topology
>
doorway / room representative relation
>
nearest valid Place
```

如果使用空间最近邻：

```text
必须设置最大距离
必须记录 distance
必须记录 mapping_method
超阈值 fail closed
```

---

## 3C. GlobalRoute

从 PLACES graph 产生：

```text
Place ID sequence
+
pose sequence
+
cost
```

第一版可以：

```text
Dijkstra / A*
```

cost：

```text
Euclidean edge length
```

如果当前 edge 已有可靠 navigation weight，再评估是否使用。

---

# Phase 4 — Execution Interface Decision Gate

这是当前路线最关键的新阶段。

依据 Phase 1 和可用 ANYmal 软件决定。

---

## Branch A — Native JSON 被确认是必需接口

条件：

```text
实验室 ANYmal stack 确认加载该 JSON
并有明确 goal/execution API
```

才实现：

```text
AnyMalWaypointJsonAdaptor
```

输出：

```text
waypoints.json
```

MVP 先用样本默认 edge policy。

不要猜：

```text
strict_path_follower
perceptive_walk
nfm_preset
```

---

## Branch B — ROS Path / Waypoint 可直接执行

如果存在：

```text
nav_msgs/Path
PoseStamped
Waypoint goal
```

入口，则优先实现：

```text
RosPathAdaptor
```

输入：

```text
GlobalRoute
```

输出：

```text
nav_msgs/Path
```

然后接：

```text
ViPlanner / ArtPlanner / lab local planner
```

---

## Branch C — 暂时没有真实 ANYmal execution API

则先实现：

```text
MockExecutor
```

不要阻塞项目。

---

# Phase 5 — Mock Navigation 闭环

## 目标

无真机情况下验证：

```text
semantic target
→ waypoint/place target
→ global route
→ execution
```

MockExecutor 状态：

```text
IDLE
PLANNING
MOVING
REACHED
NO_PATH
INVALID_GOAL
CANCELLED
```

第一版按 route 节点顺序推进。

---

# Phase 6 — 接入已经完成的 LLM + RAG

## 输入

现有系统输出：

```json
{
  "action": "navigate",
  "target_ref": "R_CONF_B"
}
```

## 新增薄层

```text
NavigationGoalCompiler
```

做：

```text
R_CONF_B
→ semantic_to_navigation_target
→ P368
→ NavigationTarget
```

LLM 禁止：

```text
猜 waypoint
生成 XYZ
生成 route
修改 graph
```

---

# Phase 7 — Office 数据集端到端测试

统一示例：

> **先去办公室 A，再前往会议室 B。**

实际测试必须替换为 Office 数据集中真实存在的 room/area。

流程：

```text
Natural Language
↓
LLM + RAG
↓
R_OFFICE_A
↓
NavigationTarget
↓
GlobalRoute
↓
MockExecutor
↓
REACHED

然后：

R_CONF_B
↓
NavigationTarget
↓
GlobalRoute
↓
MockExecutor
↓
REACHED
↓
TASK_SUCCESS
```

Trace：

```text
[Intent] PASS
[Planner] PASS

[Grounding:R_OFFICE_A] PASS
[NavigationTarget] P...
[Route] PASS
[Reached] PASS

[Grounding:R_CONF_B] PASS
[NavigationTarget] P...
[Route] PASS
[Reached] PASS

[TASK] SUCCESS
```

---

# Phase 8 — 可视化和 Benchmark

## 可视化

至少显示：

```text
全部 Place / waypoint nodes
全部 edges
semantic target
goal Place
planned route
mock robot
```

颜色/样式自行配置，但必须能人工检查：

```text
frame 是否正确
room→place 是否跨墙
路线是否穿越不可通区域
目标是否落在合理位置
```

---

## Benchmark

至少：

```text
Semantic Target Resolution Rate
Navigation Target Resolution Rate
Route Planning Success Rate
End-to-End Navigation Success Rate
Invalid / Hallucinated Navigation ID Rate
```

最后一个必须目标：

```text
0%
```

---

# Phase 9 — ANYmal-D 物理仿真

只有软件闭环稳定后才进入。

优先评估：

```text
nav-suite / IsaacLab
ViPlanner simulation path
实验室现有 simulation stack
```

目标不是复现私有 ANYbotics 全栈。

目标是验证：

```text
GlobalRoute
↓
SimulatorAdaptor
↓
ANYmal-D-like robot
↓
physical navigation
```

上层：

```text
LLM
RAG
semantic mapping
PLACES planning
```

不因 simulator 改变。

---

# Phase 10 — ANYmal D 真机

真机可用后，只补最后一层。

必须实际查询：

```text
ROS2 topic list
service list
action list
ANYmal mission / navigation API
```

确定：

```text
如何加载 navigation graph
如何 set goal
如何获取 status
如何 cancel
如何 replan
```

然后实现：

```text
NativeANYmalExecutor
```

如果真机最后证明需要用户当前的：

```text
waypoints.json
```

则 Branch A 在这里成为正式执行接口。

如果真机可以直接接受：

```text
Path / waypoint goal
```

则 Branch B 成为正式接口。

---

# 10. 推荐项目结构

```text
khronos_anymal_bridge/
│
├── docs/
│   ├── reference_audit_omniplanner.md
│   ├── reference_audit_viplanner.md
│   ├── reference_audit_dcist.md
│   ├── reference_audit_legged_navigation.md
│   ├── dsg_place_schema_report.md
│   └── REUSE_DECISION.md
│
├── navigation_ir/
│   ├── target.*
│   └── route.*
│
├── mapping/
│   ├── semantic_navigation_index.*
│   └── room_place_mapper.*
│
├── planning/
│   ├── place_graph.*
│   └── global_planner.*
│
├── adaptors/
│   ├── base_adaptor.*
│   ├── mock_adaptor.*
│   ├── ros_path_adaptor.*
│   └── anymal_waypoint_json_adaptor.*
│
├── executors/
│   ├── mock_executor.*
│   ├── simulator_executor.*       # later
│   └── native_anymal_executor.*   # later
│
├── validation/
│   ├── graph_validator.*
│   └── mapping_validator.*
│
├── visualization/
│   └── ...
│
└── generated/
    ├── semantic_to_navigation_target.json
    ├── waypoints.json             # only if Branch A
    └── export_report.json
```

---

# 11. Agent 工作原则

## 11.1 不重做 LLM + RAG

现阶段上层接口视为：

```text
target_ref
```

---

## 11.2 不先假设 `waypoints.json` 必须存在

只有：

```text
Phase 1 reference audit
+
Phase 4 interface decision
```

证明它是必要接口以后再投入正式开发。

---

## 11.3 不改 Khronos 核心

优先从：

```text
serialized DSG
Python binding
existing reader
ROS published graph
```

读数据。

---

## 11.4 不猜 ANYmal 私有参数

样本只能证明字段和值存在。

不能仅根据名字声称：

```text
max_deviation 的单位
traversability 的严格物理意义
nfm_preset 的内部逻辑
```

---

## 11.5 外部参考代码只读

先：

```text
grep
rg
read
trace call graph
```

后：

```text
写自己的 adaptor
```

---

## 11.6 Fail Closed

遇到：

```text
frame unknown
semantic mapping ambiguous
goal Place 不存在
graph disconnected
quaternion invalid
```

返回失败。

禁止猜一个“最近看起来差不多”的 goal。

---

# 12. Agent 第一条任务（更新版）

直接给 Agent：

```text
任务：Phase 0 + Phase 1 — 外部导航参考代码审计

背景：
LLM+RAG 已完成。
当前目标是把 Khronos DSG / PLACES 接到 ANYmal D 或 ANYmal-D 仿真。
不要先写 waypoints.json exporter。

1. 确认 external/navigation_references 中以下仓库都存在：
   - Omniplanner
   - Awesome-DCIST-T4
   - viplanner
   - art_planner
   - tagmap
   - nav-suite
   - anymal_d_simple_description
   - Spark-DSG
   - Khronos

2. 全程离线阅读本地 clone，禁止依赖 GitHub WebSearch。

3. 首先审计 Omniplanner：
   追踪 DSG node → position → GotoPointsPlan → compile_plan
   → Follow / nav_msgs/Path。

4. 审计 ViPlanner：
   追踪 waypoint/path input → pathFollower → Twist / robot command。

5. 审计 Awesome-DCIST-T4：
   找出 Hydra/DSG → Omniplanner → Spot executor 的完整边界。

6. 简要审计 Tag Map、ArtPlanner、nav-suite：
   判断哪些组件值得复用，哪些只做架构参考。

7. 不修改上述外部仓库。

8. 输出：
   docs/reference_audit_omniplanner.md
   docs/reference_audit_viplanner.md
   docs/reference_audit_dcist.md
   docs/reference_audit_legged_navigation.md
   docs/REUSE_DECISION.md

9. REUSE_DECISION.md 必须回答：
   - 我们是否真的需要生成完整 ANYmal waypoints.json？
   - 能否直接把 Khronos global route 转为 nav_msgs/Path？
   - ViPlanner/pathFollower 是否可以作为当前 ANYmal execution interface？
   - 哪一层需要我们自己开发 adaptor？
   - 哪些 ANYmal 私有接口仍然未知？

10. 完成报告后停止。
    暂时不要写正式 bridge。
```

---

# 13. Phase 2 的下一条 Agent 任务

只有上一步完成后：

```text
任务：审计当前项目实际 Khronos Office DSG。

1. 不读 upstream reference 来替代当前真实数据。
2. 找到 Office 数据集实际产生的 DSG。
3. 定位 PLACES layer。
4. 写最小只读脚本统计：
   node count
   edge count
   position
   orientation
   frame
   connected components
   edge distances
   Room↔Place relation
5. 输出 docs/dsg_place_schema_report.md。
6. 暂时不生成 ANYmal JSON。
7. 将真实 DSG 字段与 REUSE_DECISION.md 中选择的 robot adaptor 对齐。
```

---

# 14. 当前最推荐的总顺序

```text
Phase 0
下载并冻结外部源码
      ↓
Phase 1
Omniplanner / ViPlanner / DCIST 代码审计
      ↓
Decision:
JSON 还是 Path/Waypoint？
      ↓
Phase 2
真实 Office Khronos DSG / PLACES 审计
      ↓
Phase 3
NavigationTarget + GlobalRoute + semantic index
      ↓
Phase 4
Robot adaptor POC
      ↓
Phase 5
Mock closed loop
      ↓
Phase 6
接现有 LLM/RAG
      ↓
Phase 7
Office E2E
      ↓
Phase 8
Visualization + Benchmark
      ↓
Phase 9
ANYmal-D physics simulation
      ↓
Phase 10
ANYmal D real robot
```

---

# 15. 最终定义

当前后续工作的本质应定义为：

> **复用现有 DSG-grounded robot planning 与 ANYmal navigation 公开实现的接口思想，在 Khronos PLACES 与机器人执行层之间建立一个稳定的 Navigation IR + robot adaptor，而不是预设必须重新实现一整套导航系统。**

最优情况下，需要自己开发的核心很薄：

```text
Existing LLM + RAG
↓
target_ref
↓
semantic navigation index
↓
Khronos Place target
↓
PLACES global route
↓
ANYmal adaptor
↓
existing local navigation / execution
```

当前第一件事：

> **下载并离线审计 Omniplanner、ViPlanner、Awesome-DCIST-T4。**

而不是直接写 `waypoints.json`。
