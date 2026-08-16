# Khronos → ANYmal D：最终 RViz2 演示前代码审查、修改指导与端到端 5-Checkpoint 测试流程

> **依据**：最新提交的 `khronos_anymald_bridge` 代码、生成产物、Phase 3R/8.5/8.5R 报告、mesh integration 报告与测试结果。  
> **本阶段最终目标**：不进入 ANYmal D 物理动力学/原生控制栈；完成并录制一段可以清楚证明  
>
> ```text
> Natural Language
> → LLM + RAG
> → DSG semantic target
> → ANYmal D waypoint
> → waypoint route
> → Khronos map + waypoint graph spatial overlap
> → RViz2 mock ANYmal D movement
> ```
>
> 的完整演示视频。
>
> **重要修正**：最终 Demo 的用户输入必须使用人类自然语言语义，而不是 `R0/R4/...` 这类 DSG 内部 ID。  
> `R*` 只作为 RAG/grounding 之后的内部结果。  
> 由于最终自然语言名称必须以当前真实 Office RAG 数据库为准，本文不预先虚构“会议室/打印区”等名称；Agent 需要先从现有 RAG/canonical index 中自动筛选 5 个真实、可稳定 grounding 的人类可读实体，再生成最终五目标指令。

---

# 1. 最新代码总体结论

实际重新运行：

```bash
python3 -m pytest tests -q
```

结果：

```text
34 passed
```

可以确认以下模块已经形成并基本可用：

```text
DSG PLACES loader
Dense ANYmal-schema exporter
Per-component geodesic FPS sparsification
Connected-component preservation
Witness path generation
Ground projection for mock
Semantic Room → Waypoint mapping
NavigationGoalCompiler
Dijkstra planner
Witness reverse expansion
MockExecutor
ROS2 graph publisher
ROS2 mock robot animation
RViz2 config / launch
Khronos mesh publisher
Static mesh ↔ waypoint alignment visualization
```

旧审计中的三个严重问题已经完成核心修复：

| 问题 | 当前状态 |
|---|---|
| 3D 欧氏 FPS / topology shortcut | ✅ 改为 per-component geodesic FPS |
| source 6 component → output 1 | ✅ 保持 6 component |
| route 位于 z=3.9 m | ✅ RViz mock 投影到 z=0，标记 `projected_for_mock` |

当前生成结果：

```text
Dense PLACES:           2472
Dense PLACES edges:     8833 undirected
Sparse waypoints:       261
Sparse edges:           624 undirected / 1248 directed
Source components:      6
Sparse components:      6
Rooms:                  22
Room anchors:           22
Invalid waypoint IDs:   0%
```

---

# 2. 当前完成状态应如何准确表述

目前已经可以说：

> **Khronos DSG/PLACES → graph-aware ANYmal-D-compatible waypoint representation → semantic waypoint resolution → global waypoint planning → witness-expanded RViz2 mock execution 的软件链路已经建立。**

但当前还不能说：

```text
ANYmal D native navigation stack 已验证
ANYmal D 物理可行性已验证
ANYmal D locomotion controller 已验证
```

因为这些仍需要：

```text
ANYmal D native waypoint loader
mission API
local planner
locomotion/control stack
real robot or complete simulator
```

本阶段只需要完成：

```text
RViz2 waypoint-level mock navigation validation
```

---

# 3. 最新代码审查后，录最终演示前还必须修改的 6 个问题

---

## Fix A — `run_office_e2e.py` 仍未加载 witness cost

### 当前代码

`run_office_e2e.py`：

```python
graph = WaypointGraph.load(args.graph)
```

没有加载：

```text
generated/witness_paths.json
```

因此它实际仍 fallback 到：

```text
sparse waypoint Euclidean distance
```

而不是当前 Phase 8.5R 所声明的 witness cost。

这可以从当前 `generated/office_e2e_trace.txt` 看出来：

```text
R0 → R12 cost 33.8m
```

而 Phase 8.5R 报告中的 witness planner：

```text
R0 → R12 cost 44.12
```

二者明显不一致。

### 修改

给 `run_office_e2e.py` 增加：

```text
--witness generated/witness_paths.json
```

默认：

```python
generated/witness_paths.json
```

并改为：

```python
graph = WaypointGraph.load(args.graph, args.witness)
```

### 验收

同一个 start/goal：

```text
CLI E2E planner
RViz mock planner
unit-test planner
```

必须产生同一条 sparse route。

---

# 4. Fix B — `run_benchmark.py / verify_room_routes.py / recheck_all.py` 也必须统一 planner cost

当前以下代码仍：

```python
WaypointGraph.load(spars_path)
```

而没有 witness：

```text
run_benchmark.py
verify_room_routes.py
recheck_all.py
```

这导致：

> 单元测试和 RViz mock 已经使用 witness cost，但 benchmark / E2E / recheck 仍可能在使用另一套 Euclidean cost。

虽然“是否可达”通常不受影响，但：

```text
route sequence
route cost
benchmark trace
demo trace
```

会产生逻辑分叉。

### 修改原则

所有正式运行入口必须只允许一个 planner source：

```text
waypoints_sparsified_R3.0.json
+
witness_paths.json
```

建议统一封装：

```python
def load_demo_graph(base_dir, R=3.0):
    return WaypointGraph.load(
        ...waypoints_sparsified...,
        ...witness_paths.json
    )
```

禁止每个脚本各自决定是否加载 witness。

---

# 5. Fix C — 当前 `geodesic_m` 不能继续直接解释为“米”

这是本轮最重要的新数据一致性问题。

当前代码：

```text
sparsifier.py
```

使用 Khronos DSG edge：

```python
info.weight
```

作为 Dijkstra edge cost，然后把累积结果命名：

```text
geodesic_m
```

但对当前 `witness_paths.json` 独立复算：

```text
witness path 的真实 3D 几何长度 / geodesic_m

median ≈ 1.51
mean   ≈ 1.67
```

624 条 witness edge 中：

```text
614 / 624
```

的真实几何长度与 `geodesic_m` 相差超过 5 cm。

这说明当前实际数据中：

```text
DSG edge info.weight
```

不能未经验证就写成：

```text
physical distance in meters
```

而且当前代码 `dsg_reader/loader.py` 的注释：

```text
weight = 欧氏距离
```

必须重新验证。

---

## 5.1 Agent 必须先做 edge-weight audit

在真实：

```text
office_launch_dense/.../dsg.json
```

上统计每一条 PLACES edge：

```text
stored_weight = edge.info.weight

metric_3d =
||position(source) - position(target)||

ratio =
stored_weight / metric_3d
```

输出：

```text
mean
median
p90
max
MAE
|weight-metric| < 1cm 的比例
```

### 判定

如果：

```text
weight ≈ metric distance
```

再允许使用：

```text
*_m
```

命名。

否则必须改名。

---

## 5.2 推荐把一个 edge 的三个量彻底分开

`witness_paths.json` 改为：

```json
{
  "(A,B)": {
    "path": [...],

    "khronos_graph_cost": 4.272,

    "metric_3d_length_m": 20.078,

    "projected_xy_length_m": 18.345,

    "endpoint_euclidean_3d_m": 4.621,

    "topology_stretch": ...
  }
}
```

其中：

### `khronos_graph_cost`

```text
Σ DSG info.weight
```

保留 Khronos 原图内部 cost。

### `metric_3d_length_m`

```text
Σ ||dense_position[i+1] - dense_position[i]||3D
```

### `projected_xy_length_m`

当前 RViz mock：

```text
z = 0
```

因此机器人动画真正移动的长度：

```text
Σ ||(x_i,y_i) - (x_i+1,y_i+1)||
```

---

## 5.3 当前 RViz mock planner 推荐用哪个 cost？

为了演示“地面机器人沿真实 witness corridor 走最短物理路线”，本阶段推荐：

```text
Dijkstra cost = projected_xy_length_m
```

原因：

```text
1. route topology 仍来自 Khronos dense witness
2. 不穿墙
3. cost 有明确物理单位 m
4. 与 RViz robot 实际动画长度完全一致
```

`khronos_graph_cost` 可以保留用于分析，但不要再称为“米”。

---

# 6. Fix D — 当前 RViz 动态 publisher 仍然过度刷新

这是最终录制“mesh + animation”视频前最重要的性能修改。

当前：

```text
mock_robot_node
```

在每一次 timer tick（默认 25 Hz）中都会重新发布：

```text
CUBE robot
ARROW robot
goal marker
完整 nav_msgs/Path
完整 route LINE_STRIP
不断增长的 robot trail
state
```

实际上：

```text
Path
RouteMarker
GoalMarker
StartMarker
```

全部是静态或低频对象。

它们不应该 25 Hz 全量重发。

---

## 6.1 正确拆分

### 启动时只发一次 / Transient Local

```text
start marker
all checkpoint markers
goal list
route nav_msgs/Path
route LINE_STRIP
```

### 动态只发

```text
mock robot pose
robot arrow
state
```

频率：

```text
10 Hz
```

对视频已经足够平滑。

### Trail

可选：

```text
5 Hz
```

并做：

```text
距离 > 0.1~0.2m 才加入一个点
```

避免 trail 点数无限增长。

---

# 7. Fix E — 静态 waypoint graph 和 mesh 不应周期重发

---

## 7.1 `graph_publisher.py`

当前使用：

```text
TRANSIENT_LOCAL
```

但同时：

```python
create_timer(1.0, publish_all)
```

注释说：

> 避免 late subscriber 丢失。

这个理由不成立。

`TRANSIENT_LOCAL` 本身就是为了：

```text
late subscriber
```

能够收到最近一次持久化消息。

### 修改

默认：

```text
启动后 publish once
```

可保留一个诊断参数：

```text
--republish-period 0
```

其中：

```text
0 = 不周期重发
```

只有特殊 DDS 环境才设置 >0。

---

## 7.2 `khronos_mesh_publisher.py`

当前：

```python
self.timer = self.create_timer(1.0, self.publish)
```

这意味着：

```text
同一个 MESH_RESOURCE
每秒重新发布一次
```

RViz/assimp/OGRE 可能重复触发 resource refresh/rebuild。

这对：

```text
mesh + dynamic marker
```

没有必要，而且可能加剧软件 GL 崩溃。

### 修改

mesh publisher：

```text
QoS = TRANSIENT_LOCAL
publish once
不设 timer
```

RViz late join 仍可获得 mesh marker。

---

# 8. Fix F — 最终视频不要优先使用自制 MESH_RESOURCE；优先使用 Khronos 原生 Background Mesh display

当前 mesh integration 已经证明：

```text
mesh + waypoint graph static
```

空间可以叠加。

但最终视频的最佳路径不是：

```text
自制 file://mesh.ply Marker
+
mock robot
```

而是：

> **直接使用你之前已经稳定显示 Khronos 场景的 `uHumans2.rviz` / Background Mesh display，再把 ANYmal mock topics 加进去。**

原因：

```text
Khronos 原生 display 已经稳定
不需要 assimp 重复加载单独 PLY
直接证明“原 Khronos 输出”和“ANYmal waypoint 输出”重合
更符合论文展示目的
```

---

## 8.1 推荐最终 RViz config

从：

```text
uHumans2.rviz
```

开始。

手工 Add：

```text
/anymald/waypoints/nodes
/anymald/waypoints/edges
/anymald/room_anchors
/anymald/route
/anymald/route_marker
/anymald/checkpoints
/anymald/current_goal
/anymald/mock_robot
/anymald/robot_trail
```

然后保存：

```text
uHumans2_anymald_demo.rviz
```

最终演示视频应该使用这份 config。

---

# 9. 软件渲染崩溃的当前判断

mesh integration 报告已经完成多轮隔离：

```text
30万 / 10万 / 5万 mesh
25 Hz → 1 Hz
minimal
static-final
```

都尝试过。

headless：

```text
Xvfb + llvmpipe
```

环境下：

```text
mesh + mock entity marker
```

仍然可能使 RViz2 退出。

由于：

```text
free memory 约 47 GB
```

且没有 OOM 证据，目前不能归因于 RAM。

最终视频应在有显示器 + GPU 的机器运行，并先检查：

```bash
glxinfo -B
```

必须优先看到：

```text
OpenGL renderer = NVIDIA / hardware GPU
```

而不是：

```text
llvmpipe
Accelerated: no
```

---

# 10. 最终 Demo 必须从“真实 LLM+RAG 输出”启动，而不是硬编码 `--goal-rooms`

当前：

```text
mock_robot_node
```

只支持：

```bash
--goal-rooms 12,4,9
```

这对于 RViz animation 已经够，但不够证明：

```text
LLM+RAG → RViz
```

是真正同一条运行链。

---

## 10.1 必须新增 task-plan 输入

推荐：

```text
mock_robot_node --task-plan <json>
```

或者新增：

```text
task_plan_demo_node
```

但不要复制 planner。

推荐直接修改 `mock_robot_node`：

```text
--task-plan
--start-room
```

读取现有 LLM+RAG：

```json
{
  "task_chain": [
    {
      "action": "navigate_to",
      "target_id": ...
    }
  ]
}
```

流程：

```text
task_plan
↓
extract navigate_to target_id
↓
NavigationGoalCompiler
↓
waypoint IDs
↓
same WaypointGraph planner
↓
same RViz animation
```

这样最终视频才能严格叫：

```text
LLM+RAG → ANYmal D RViz visualization
```

---

# 11. 5-Checkpoint Demo 还需要一个“检查点状态机”

当前 mock node 会：

```text
把多个 goal route 全部拼接成一条长 route
```

但 RViz 中只把：

```text
最终 waypoint
```

作为 goal。

对于五目标自然语言指令，视频中应该明确看到：

```text
1/5
2/5
3/5
4/5
5/5
```

---

## 11.1 新增 checkpoint markers

Topic：

```text
/anymald/checkpoints
```

显示 5 个 checkpoint：

```text
1: R4
2: R7
3: R8
4: R12
5: R14
```

样式建议：

```text
未到达：黄色
当前目标：红色
已完成：绿色
```

---

## 11.2 当前 goal marker 要随 checkpoint 更新

当前代码：

```text
_goal_wp = 最后一个 goal
```

应改成：

```text
current_checkpoint_index
current_goal_wp
```

机器人到达 checkpoint 时：

```text
CHECKPOINT_REACHED 1/5
↓ pause
current goal = checkpoint 2
```

---

## 11.3 checkpoint pause

增加：

```text
--checkpoint-pause 1.0
```

视频演示：

```text
到达每个房间停 1 秒
```

让观看者可以明显看到五个目标依次完成。

---

# 12. 最终 Demo 必须使用真正的人类自然语言语义目标

这里必须明确区分三层：

```text
用户说的话
↓
RAG / Grounding 得到的语义实体
↓
DSG 内部 ID / Room ID / target_ref
↓
ANYmal waypoint
```

因此：

> **用户不应该说 `R4、R7、R8、R12、R14`。**

这些 `R*` ID 只应该作为 Grounding 之后的内部结果出现在 trace、debug 或论文系统图中。

本项目已经完成 LLM + RAG 的语义判断、实体检索与 grounding，所以最终 Demo 必须利用这一能力。

---

## 12.1 正确的 Demo 目标选择方法

不要在文档里人工虚构：

```text
“会议室”
“打印区”
“茶水间”
```

除非这些语义确实存在于当前 Office RAG / canonical graph / DSG 数据中。

Agent 必须先从当前 **真实 Office RAG 数据库** 中选择 5 个满足以下条件的实体：

```text
1. 有人类可读语义名称/描述；
2. RAG 能稳定 grounding；
3. Grounding 后有唯一 target_ref / DSG entity；
4. entity 能确定性映射到一个有效 ANYmal waypoint；
5. 5 个目标在空间上尽量分布在不同区域，使视频路线具有展示价值；
6. 五目标之间全部存在有效 waypoint route。
```

---

## 12.2 增加 Demo Target Selection 脚本

建议新增：

```text
demo/select_demo_targets.py
```

读取：

```text
RAG canonical index / entity index
semantic_to_waypoint.json
waypoint graph
```

输出候选表：

```text
human_name
semantic_type
target_ref
DSG/entity ID
waypoint_id
connected_component
RAG resolvable?
route reachable?
```

例如：

```text
Candidate 1:
human_name = "<真实 Office 语义名称>"
target_ref = ...
waypoint = ...

Candidate 2:
...
```

然后从中选 5 个。

必须把最终选择保存：

```text
demo/office_5checkpoint_targets.json
```

---

## 12.3 最终自然语言指令应该是什么样

最终自然语言必须是类似：

> **机器人从当前位置出发，先前往【语义目标1】，然后依次前往【语义目标2】、【语义目标3】、【语义目标4】和【语义目标5】。请严格按照这个顺序导航，每到一个目标后短暂停留，再继续前往下一个地点。**

其中：

```text
【语义目标1...5】
```

必须替换成当前 Office RAG 数据库里真实存在、且经过验证可以 grounding 的人类可读名称。

例如只有在数据库中确实存在这些实体时，才可以写成：

```text
“先去会议室，再去打印区，然后去休息区……”
```

否则禁止使用。

---

## 12.4 五个 waypoint 的含义

用户要求的“五个 waypoint”在 Demo 里应理解为：

```text
5 个高层语义导航 checkpoint
```

即：

```text
Natural language semantic target 1
→ semantic waypoint anchor 1

Natural language semantic target 2
→ semantic waypoint anchor 2

...

Natural language semantic target 5
→ semantic waypoint anchor 5
```

ANYmal 的底层 graph planner 在两个 checkpoint 之间仍然可以经过多个内部 waypoint。

因此：

```text
5 个用户语义目标
≠
整条底层 route 只有 5 个 graph node
```

---

# 13. 五个目标的内部 Grounding / Waypoint 结果必须由真实 RAG 自动产生

原先用于开发验证的：

```text
R4  → WP4647
R7  → WP6817
R8  → WP7388
R12 → WP12136
R14 → WP22068
```

可以继续保留为：

```text
bridge / waypoint 层的已知测试 fixture
```

但不能直接作为最终自然语言 Demo 输入。

最终 Demo 必须产生如下真实 trace：

```text
Human semantic phrase 1
↓ RAG
target_ref / DSG entity A
↓ deterministic semantic→waypoint
WP_A

Human semantic phrase 2
↓ RAG
target_ref / DSG entity B
↓
WP_B

...

Human semantic phrase 5
↓
WP_E
```

最终确定的 5 个目标必须记录成表：

| 顺序 | 用户自然语言目标 | RAG target_ref | DSG/Entity ID | ANYmal waypoint |
|---:|---|---|---|---:|
| 1 | `<真实名称>` | `<target_ref>` | `<id>` | `<WP>` |
| 2 | `<真实名称>` | `<target_ref>` | `<id>` | `<WP>` |
| 3 | `<真实名称>` | `<target_ref>` | `<id>` | `<WP>` |
| 4 | `<真实名称>` | `<target_ref>` | `<id>` | `<WP>` |
| 5 | `<真实名称>` | `<target_ref>` | `<id>` | `<WP>` |

这张表应该直接成为最终 Demo 的 ground-truth trace。

---

# 14. 当前数据下的预期路径规模

如果按推荐的：

```text
projected_xy_length_m
```

作为 mock planner cost，当前产物估算：

```text
R0 → R4   ≈ 17.3 m
R4 → R7   ≈ 5.7 m
R7 → R8   ≈ 13.0 m
R8 → R12  ≈ 43.1 m
R12 → R14 ≈ 34.9 m
```

总 projected route：

```text
≈ 114.1 m
```

约：

```text
95 个 dense witness poses
```

如果 demo speed：

```text
3.0 m/s
```

移动时间约：

```text
38 s
```

加：

```text
5 次 checkpoint pause
初始地图展示
最终停止
```

最终视频：

```text
约 45~55 s
```

非常适合论文/答辩展示。

> 以上长度基于当前生成产物离线复算；在 Fix C 完成并重新规划后，以最终 trace 为唯一正式数据源。

---

# 15. 从头开始的最终一次完整测试

下面这条流程必须作为最终验收流程固定下来。

---

# Stage 0 — 数据一致性冻结

最终 demo 前，必须保证：

```text
RAG
Khronos DSG
semantic_to_waypoint
waypoints
mesh
```

全部来自同一个 Office snapshot。

当前主 DSG：

```text
scene-graph-khronos/snapshots/
office_launch_dense/2026_08_03-16_46_38/dsg.json
```

RAG 主线报告中对应：

```text
outputs/office_dense/
```

但当前文档还记录：

```text
config_rag.yaml
```

指向旧：

```text
khronos_office_new
```

这是最终真实 LLM+RAG 联调前必须修复的配置问题。

---

## 15.1 新增一个 demo manifest

建议生成：

```text
generated/demo_manifest.json
```

至少包含：

```json
{
  "dsg_snapshot": ".../office_launch_dense/2026_08_03-16_46_38/dsg.json",
  "dsg_sha256": "...",
  "rag_output_dir": ".../outputs/office_dense",
  "waypoint_graph": "generated/waypoints_sparsified_R3.0.json",
  "semantic_index": "generated/semantic_to_waypoint.json",
  "witness": "generated/witness_paths.json",
  "R_geo": 3.0,
  "ground_projection": 0.0
}
```

Demo 启动时先验证 manifest。

---

# Stage 1 — 重新生成 Khronos → ANYmal D 导航产物

在 bridge 根目录：

```bash
source <ROS/Jazzy 环境>
```

定义：

```bash
DSG=/home/ulmmt/Masterthesis-wang/scene-graph-khronos/snapshots/office_launch_dense/2026_08_03-16_46_38/dsg.json
```

重新生成：

```bash
python3 export_waypoints.py "$DSG"

python3 build_sparsified.py "$DSG" \
  --R 3.0 \
  --ground-z 0.0

python3 build_semantic_to_waypoint.py "$DSG" \
  --R 3.0
```

然后：

```bash
python3 -m pytest tests -q
```

验收：

```text
34+ tests PASS
```

Fix A–F 完成后，应增加测试，目标建议：

```text
40+ tests
```

---

# Stage 2 — 生成/检查 metric witness 数据

执行新 edge-weight audit。

要求输出：

```text
generated/edge_weight_audit.json
```

然后重建：

```text
witness_paths.json
```

必须同时有：

```text
khronos_graph_cost
metric_3d_length_m
projected_xy_length_m
```

正式 mock planner 使用：

```text
projected_xy_length_m
```

---

# Stage 3 — 运行所有 bridge 一致性检查

```bash
python3 recheck_all.py
python3 verify_room_routes.py
python3 run_benchmark.py "$DSG" --R 3.0
```

这三个脚本必须使用：

```text
同一个 witness-aware graph loader
```

禁止 fallback 到另一套 cost。

---

# Stage 4 — 修复并启动真实 LLM+RAG

这一部分最新 bridge 压缩包没有包含 LLM/RAG 源码，因此不要在 bridge 中重新实现。

使用现有：

```text
LLM-robot-taskplanning-chaining-rag/
```

系统。

首先：

```bash
cd <LLM_RAG_ROOT>
python main_rag.py --help
```

Agent 必须根据**当前实际 CLI**运行 structured/RAG 模式。

不要猜 CLI 参数。

---

## 4.1 必须先修 config

确保：

```text
canonical graph
vector DB
DSG manifest
```

全部指向：

```text
office_dense
```

并与：

```text
office_launch_dense/2026_08_03-16_46_38/dsg.json
```

一致。

---

## 4.2 输入最终 Demo 指令

首先运行 `demo/select_demo_targets.py`，从当前真实 Office RAG 数据中选出 5 个可稳定 grounding 的人类可读目标。

然后构造：

```text
机器人从当前位置出发，先前往【目标1】，然后依次前往【目标2】、
【目标3】、【目标4】和【目标5】。请严格按照这个顺序导航；
每到一个目标后短暂停留，再继续前往下一个地点。
```

其中：

```text
【目标1...5】
```

必须全部来自 RAG 数据库真实实体，不得使用 `R4/R7/...` 作为用户输入。

---

# Stage 5 — 验证 LLM+RAG 的真实输出

保存真实输出：

```text
demo/office_5checkpoint_task_plan.json
```

必须检查：

```text
navigate_to step 数 = 5
```

并且 5 个输出必须与：

```text
demo/office_5checkpoint_targets.json
```

中选定的人类语义目标一一对应、顺序一致。

最终 Grounding 结果应记录：

```text
natural-language phrase
→ target_ref
→ DSG/entity ID
→ waypoint ID
```

一个最小可接受 task plan 结构：

```json
{
  "task_chain": [
    {
      "action": "navigate_to",
      "target_id": "<由真实RAG产生的entity_1>"
    },
    {
      "action": "navigate_to",
      "target_id": "<由真实RAG产生的entity_2>"
    },
    {
      "action": "navigate_to",
      "target_id": "<由真实RAG产生的entity_3>"
    },
    {
      "action": "navigate_to",
      "target_id": "<由真实RAG产生的entity_4>"
    },
    {
      "action": "navigate_to",
      "target_id": "<由真实RAG产生的entity_5>"
    }
  ]
}
```

实际 LLM/RAG 可以有更多 schema 字段，但以下三点必须满足：

```text
action = navigate_to
5 个目标全部来自真实 semantic grounding
顺序与用户自然语言要求一致
```

---

# Stage 6 — 真正用 RAG task plan 跑 bridge CLI E2E

不要：

```text
--targets R4,R7,...
```

最终验收必须使用：

```text
--task-plan
```

例如：

```bash
python3 run_office_e2e.py \
  --graph generated/waypoints_sparsified_R3.0.json \
  --witness generated/witness_paths.json \
  --semantic generated/semantic_to_waypoint.json \
  --start R0 \
  --task-plan demo/office_5checkpoint_task_plan.json \
  --out demo/office_5checkpoint_bridge_trace.txt
```

必须输出：

```text
[Intent] PASS
[Planner] PASS

[Grounding:R4] PASS
[Semantic→Waypoint] WP4647
[Route] ...
[Reached] PASS

...

[Grounding:R14] PASS
[Semantic→Waypoint] WP22068
[Reached] PASS

[TASK] SUCCESS
```

---

# Stage 7 — 让 RViz node 直接读取同一份 task plan

最终视频禁止：

```text
LLM/RAG 输出一份 JSON
↓
然后人工重新输入 --goal-rooms 4,7,8,12,14
```

因为这会断掉真正的端到端证据链。

最终命令应设计成：

```bash
ros2 launch khronos_anymald_rviz mock_navigation.launch.py \
  base_dir:=<BRIDGE_ROOT> \
  start_room:=0 \
  task_plan:=<BRIDGE_ROOT>/demo/office_5checkpoint_task_plan.json \
  frame:=world \
  mock_speed:=3.0 \
  rate:=10.0 \
  checkpoint_pause:=1.0 \
  publish_stretch:=false
```

`task_plan` 中的五个 target 必须直接进入：

```text
NavigationGoalCompiler
```

再转换：

```text
R4  → WP4647
R7  → WP6817
R8  → WP7388
R12 → WP12136
R14 → WP22068
```

---

# Stage 8 — 最终地图重合视频：推荐运行方式

## 8.1 不要 headless 录最终视频

最终使用：

```text
有显示器
+
GPU hardware OpenGL
```

先：

```bash
glxinfo -B
```

确认不是：

```text
llvmpipe
```

---

## 8.2 优先启动 Khronos 原生 RViz 场景

使用现有：

```text
uHumans2.rviz
```

显示：

```text
Background Mesh
Scene Graph
Rooms
```

然后加载：

```text
uHumans2_anymald_demo.rviz
```

或在现有 config 中 Add mock topics。

---

# 16. 最终 RViz 中应该显示什么

为了视频清楚，不要把所有 debug layer 全打开。

---

## 推荐显示

### Khronos

```text
Background Mesh          ON
Room nodes               ON
Object layer             可关闭
dynamic trajectories     OFF
大量 Object edge         OFF
```

### ANYmal bridge

```text
WaypointNodes            ON
WaypointEdges            ON，半透明
RoomAnchors              ON
Checkpoints              ON
Route                    ON
MockRobot                ON
RobotTrail               ON
GoalMarker               ON
StretchEdges             OFF（正式 demo）
```

StretchEdges 只用于 debug screenshot。

---

# 17. 视频应该证明的“地图重合”是什么

最终视频不是为了证明：

```text
两个截图长得差不多
```

而是要直接看到：

```text
Khronos reconstructed Office mesh
+
Khronos Room nodes
+
ANYmal waypoint graph
```

处于同一个 RViz coordinate frame。

具体人工检查：

```text
waypoint nodes 落在走廊/房间 free space
route 沿走廊
Room anchor 靠近对应 Room
robot 沿 route 移动
没有整体旋转/镜像/平移
```

---

# 18. 五个 demo anchor 必须逐一人工 spot-check

正式录制前单独检查：

```text
R4  → WP4647
R7  → WP6817
R8  → WP7388
R12 → WP12136
R14 → WP22068
```

要求：

```text
anchor 在目标 room 内或合理 entrance/corridor 位置
没有跨墙映射
没有错误 component
```

注意：

```text
22 unique anchor
```

本身不是 correctness proof。

最终这 5 个必须视觉确认。

---

# 19. 推荐增加 5-Checkpoint 可视化状态

正式 demo 中：

```text
Checkpoint 1/5: R4
Checkpoint 2/5: R7
...
```

每个 checkpoint：

```text
未访问    yellow
当前目标  red
已访问    green
```

机器人达到后：

```text
pause 1 s
```

再变换下一目标。

这比只画一条长 route 更能证明：

> LLM 的五个高级语义导航目标正在按顺序执行。

---

# 20. 推荐增加 Demo Status Topic

发布：

```text
/anymald/demo_state
```

内容：

```text
instruction_id
current_checkpoint
target_ref
waypoint_id
state
progress
```

例如：

```text
checkpoint=3/5
target=R8
waypoint=7388
state=MOVING
```

终端可以同步：

```bash
ros2 topic echo /anymald/demo_state
```

作为视频旁证。

---

# 21. 最终视频推荐时间线

整个视频建议约：

```text
45~60 秒
```

### 0–4 秒

静态展示：

```text
Khronos mesh
+
ANYmal waypoint graph
+
5 checkpoint markers
```

目的：

> 先让观看者看到两张地图确实处于同一坐标系。

### 4–7 秒

终端/字幕展示自然语言：

```text
机器人当前位于 R0……
R4 → R7 → R8 → R12 → R14
```

### 7–45 秒

Mock ANYmal：

```text
R0
→ R4
→ R7
→ R8
→ R12
→ R14
```

每到目标：

```text
停 1 秒
marker 由 red → green
```

### 最后 3 秒

显示：

```text
TASK_SUCCESS
5/5 checkpoints reached
```

---

# 22. Mock 参数建议

正式视频不要使用：

```text
25 Hz + 1 m/s
```

推荐：

```text
rate = 10 Hz
mock_speed = 3.0 m/s
checkpoint_pause = 1.0 s
```

理由：

```text
10Hz 已足够视频视觉连续
减少 RViz 动态 marker 压力
约 114m route 可在 ~38s 完成
```

---

# 23. 可视化 z-offset

当前导航逻辑：

```text
ground_z = 0
```

为了避免 marker 与 floor mesh z-fighting，可以只在 visualization 层加入：

```text
viz_z_offset = +0.03 ~ +0.08m
```

注意：

```text
navigation coordinate z 仍 = 0
```

只改变：

```text
marker display height
```

不要污染 waypoint JSON。

---

# 24. 最终视频录制建议

Linux 有显示器机器可直接用 OBS。

或者：

```bash
ffmpeg -video_size 1920x1080 \
  -framerate 30 \
  -f x11grab \
  -i :0.0 \
  -c:v libx264 \
  -preset veryfast \
  -crf 20 \
  anymald_khronos_e2e_demo.mp4
```

具体 DISPLAY / resolution 以本机为准。

---

# 25. 最终验收 Checklist

---

## A. 数据一致性

```text
[ ] RAG office_dense 与当前 Office DSG 同一 snapshot
[ ] demo_manifest 校验通过
[ ] semantic_to_waypoint 来源一致
[ ] waypoint graph 来源一致
[ ] mesh 来源一致
```

---

## B. 代码

```text
[ ] run_office_e2e 加载 witness
[ ] benchmark 加载 witness
[ ] verify_room_routes 加载 witness
[ ] recheck_all 加载 witness
[ ] 不再存在 Euclidean/witness planner 分叉
[ ] DSG edge weight 单位审计完成
[ ] graph_cost 与 metric length 分离
```

---

## C. RViz 性能

```text
[ ] graph 静态 markers 只发布一次
[ ] mesh marker 若使用 custom publisher，只发布一次
[ ] route 只发布一次/任务变化时发布
[ ] goal 只在 checkpoint 变化时发布
[ ] robot pose 10Hz
[ ] trail 低频/抽稀
[ ] 硬件 OpenGL，不是 llvmpipe
```

---

## D. 5-Checkpoint LLM+RAG

```text
[ ] LLM 接收一条自然语言指令
[ ] Planner 输出 exactly 5 navigate_to
[ ] RAG grounding 顺序 = R4,R7,R8,R12,R14
[ ] target_id 全部为当前 DSG 真 ID
[ ] task plan 保存为文件
```

---

## E. Semantic → Waypoint

```text
[ ] 五个人类自然语言目标均由真实 RAG grounding
[ ] 每个 target_ref 均能确定性映射到 waypoint
[ ] 五个 waypoint ID 全部存在于当前 graph
[ ] 五个 anchor RViz 视觉检查合理
[ ] 用户输入中不直接出现 R4/R7 等内部 Room ID
```

---

## F. Planner / Route

```text
[ ] 每一 sparse edge 有 witness
[ ] witness 展开方向正确
[ ] 每一 consecutive dense pose 对应真实 dense topology
[ ] route 不穿墙
[ ] projected route length 与 RViz state length 一致
```

---

## G. RViz 视频

```text
[ ] Khronos Background Mesh 可见
[ ] Khronos Room nodes 可见
[ ] ANYmal waypoint graph 可见
[ ] 五个 checkpoint 可见
[ ] route 可见
[ ] mock robot 连续移动
[ ] 每个 checkpoint 有到达状态
[ ] 5/5 后 TASK_SUCCESS
[ ] RViz 无 TF error
[ ] 视频无明显卡顿/崩溃
```

---

# 26. 建议 Agent 的实际修改顺序

不要再扩展新研究功能。

严格按：

```text
1. Fix witness-aware planner consistency
2. Audit DSG edge weight semantics
3. Split graph_cost / metric length
4. Planner mock 改用 projected_xy_length_m
5. 优化 RViz static/dynamic publish architecture
6. mock_robot_node 支持 --task-plan
7. 增加 5-checkpoint state machine
8. 增加 checkpoint markers
9. 合并 Khronos uHumans2 RViz config
10. 修复 RAG office_dense 配置
11. 跑真实 LLM+RAG 5-target task
12. 用同一 task-plan 驱动 RViz
13. 录最终视频
14. STOP
```

---

# 27. Agent 直接任务模板

```text
任务：完成 Khronos→ANYmal D 最终 RViz2 E2E Demo。

注意：
- 不进入 ANYmal D physics / Isaac / native control。
- 不重做 LLM+RAG。
- 最终目标是一条真实 LLM+RAG 输出驱动的五语义目标 RViz2 导航视频。

Part 1 — planner/data consistency
1. 修改 run_office_e2e.py，默认加载 generated/witness_paths.json。
2. 修改 run_benchmark.py / verify_room_routes.py / recheck_all.py，
   全部复用同一个 witness-aware graph loader。
3. 审计真实 Office DSG 的 PLACES edge.info.weight：
   与 node position 真实 3D Euclidean length 做统计对比。
4. 如果 weight 不是物理米制：
   - `geodesic_m` 改名 `khronos_graph_cost`
   - witness 增加 `metric_3d_length_m`
   - witness 增加 `projected_xy_length_m`
5. RViz mock planner 使用 `projected_xy_length_m`，
   确保 planner cost 与实际 mock animation distance 一致。
6. 更新所有 report / trace，禁止把非米制 graph cost 写成 m。

Part 2 — RViz rendering optimization
7. graph_publisher 的静态 graph/anchors 使用 TRANSIENT_LOCAL，只发布一次。
8. khronos_mesh_publisher 若保留：
   TRANSIENT_LOCAL + publish once，去掉 1Hz timer。
9. mock_robot_node：
   - route/path/start/checkpoints 静态发布
   - current goal 只在 checkpoint 切换时发布
   - robot pose/state 10Hz
   - trail 低频并抽稀
10. 最终 demo 优先使用 Khronos 原生 Background Mesh display，
    不优先使用自制 MESH_RESOURCE。
11. frame_id 保持参数化；最终确认 world/map TF。

Part 3 — actual LLM+RAG integration
12. 修现有 LLM+RAG config，使其指向当前 `outputs/office_dense`
    且与 `office_launch_dense/2026_08_03-16_46_38/dsg.json` 一致。
13. 增加 demo_manifest（包含 DSG snapshot/hash、RAG output、waypoint/witness 版本）。
14. 新增/运行 `demo/select_demo_targets.py`：
    从当前真实 Office RAG/canonical index 中选择 5 个
    人类可读、可稳定 grounding、可映射到 waypoint、路线可达的目标。
15. 保存选择结果：
    `demo/office_5checkpoint_targets.json`。
16. 根据这 5 个真实语义名称生成一条人类自然语言指令：
    “机器人从当前位置出发，先前往【目标1】，然后依次前往
     【目标2】、【目标3】、【目标4】和【目标5】。
     严格按照该顺序导航，每到一个目标后短暂停留。”
17. 保存真实 LLM+RAG 输出：
    `demo/office_5checkpoint_task_plan.json`。
18. 验证 exactly 5 navigate_to；
    每个 target 必须与 targets.json 中的人类语义目标一一对应。

Part 4 — task plan directly drives RViz
17. mock_robot_node/launch 新增 `--task-plan` / `task_plan:=`。
19. task plan target_id 必须经 NavigationGoalCompiler 转 waypoint，
    禁止人工再次写 `--goal-rooms` 作为最终验收。
20. 增加 checkpoint state machine：
    - 5 checkpoint markers
    - current target 红色
    - reached checkpoint 绿色
    - 1s pause
    - state 显示 `checkpoint i/5`
21. 推荐参数：
    rate=10Hz
    mock_speed=3.0m/s
    checkpoint_pause=1s
    publish_stretch=false

Part 5 — final visual validation
22. 基于现有 Khronos `uHumans2.rviz` 加入 ANYmal topics，
    保存 `uHumans2_anymald_demo.rviz`。
23. 根据 `office_5checkpoint_targets.json` 确认五个真实语义目标的
    semantic entity → waypoint anchor 在 RViz 中位置合理。
24. 最终视频必须同时看到：
    Khronos mesh + Room nodes + waypoint graph +
    5 checkpoints + witness route + moving mock robot。
25. 最终状态必须：
    5/5 checkpoint reached
    TASK_SUCCESS。
26. 完成后停止，不进入 ANYmal dynamics。
```

---

# 28. 最终论文中可使用的准确描述

如果以上最终 Demo 通过，可以写：

> **An end-to-end semantic waypoint navigation pipeline was demonstrated in RViz2. A natural-language office inspection instruction containing five ordered semantic destinations was parsed and grounded by the existing LLM+RAG pipeline. The resulting DSG room identifiers were deterministically compiled to Khronos-derived ANYmal-D waypoint anchors. Global routes were planned over the graph-aware sparsified waypoint graph and expanded along dense Khronos witness paths. The Khronos reconstructed environment, semantic rooms, waypoint graph, planned route, and a mock ANYmal-D base were visualized in a shared RViz2 coordinate frame, where the mock robot sequentially reached all five requested destinations.**

必须同时注明：

> **This validates the semantic-to-waypoint software pipeline and spatial consistency in RViz2; it does not constitute validation of the proprietary/native ANYmal-D locomotion or mission-control stack.**

---

# 29. 当前阶段最终判断

最新代码已经非常接近最终可展示状态。

现在不应该继续扩大研究范围。

真正还差的是：

```text
真实 RAG config 对齐
+
统一 planner cost
+
RViz publisher 性能收紧
+
task-plan 直接驱动 RViz
+
5 checkpoint 演示状态
+
Khronos 原生 mesh 同屏录像
```

完成这六件事以后：

> **当前无真机条件下的 Khronos → ANYmal D 软件链路验证即可正式封存。**
