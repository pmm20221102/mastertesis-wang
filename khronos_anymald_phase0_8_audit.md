# Khronos → ANYmal D Phase 0–8 全面审查报告

## 结论摘要

本次审查对象为用户提交的 `khronos_anymald_bridge` Phase 0–8 全部产物。

独立执行结果：

```text
pytest tests -q
26 passed
```

因此可以确认：

- 代码结构完整；
- schema/exporter/mapper/planner/mock executor 的单元测试均能运行；
- 全量 Khronos PLACES → ANYmal schema 的序列化链路基本正确；
- semantic target → waypoint → Dijkstra → MockExecutor 的软件接口链路已建立。

但是，当前不能把 Phase 3–8 的结果解释为“ANYmal D 导航图已经验证正确”。

最主要原因有两个：

1. 当前稀疏化算法使用 **3D 欧氏距离 FPS + 欧氏最近代表 assignment**，会破坏 Khronos 原始图的可通行拓扑，生成大量 graph shortcut。
2. Khronos PLACES 是 **3D volumetric GVD/free-space skeleton**，z = 1.1–3.9 m，而不是地面 waypoint。当前 mock 路线大量位于 3.9 m 高度，不能代表真实 ANYmal D 地面导航。

因此当前最准确的状态是：

> **Phase 0–2 基本成立；Phase 3 必须修正；Phase 4–8 的软件代码可复用，但其“导航正确性”结论需要在修正 Phase 3 后重新运行。**

当前用户只要求做到：

> **RViz2 中完成可视化的语义→waypoint→规划→mock 运动闭环。**

这个目标 **不需要完整 ANYmal D 动力学/控制参数**，完全可以继续完成。

---

# 1. 独立功能检测

## 1.1 测试

实际运行：

```text
python3 -m pytest tests -q
```

结果：

```text
26 passed in 0.55s
```

说明以下模块的基本软件逻辑可工作：

```text
DSG loader
waypoint exporter
schema compatibility
validator
sparsifier basic unit tests
semantic mapper
Dijkstra planner
NavigationGoalCompiler
MockExecutor
```

注意：

> 单元测试通过只能证明代码符合当前测试假设，不能证明稀疏 graph 在真实环境中物理可通行。

---

# 2. Phase 0 审查 — 参考项目

状态：

```text
完成
```

已形成：

```text
reference_omniplanner.md
reference_dcist.md
reference_tagmap.md
reference_viplanner.md
reference_legged_artplanner_navsuite.md
REFERENCE_METHOD_SUMMARY.md
```

判断：

- 没有重复开发 LLM/RAG；
- 最终对象保持 ANYmal D；
- 正确识别没有公开的 Khronos → ANYmal native waypoint converter；
- 参考项目被用于方法借鉴，而非改变最终目标。

建议：

```text
Phase 0 无需继续修改。
```

---

# 3. Phase 1 审查 — Office DSG / PLACES

状态：

```text
基本完成
```

报告中的真实 Office DSG：

```text
total nodes = 3096
PLACES = 2472
Rooms = 22
Objects = 209
PLACES edges = 8833 undirected
PLACES components = 6
largest component = 2455
```

Room→Place：

```text
22/22 rooms 有 Place
```

这一部分非常有价值。

## 已正确识别的核心问题

报告已经发现：

```text
Khronos PLACES z = 1.1 ~ 3.9 m
ANYmal sample z ≈ ground level
```

这是一个真正的硬问题。

但后续阶段虽然记录了这个 warning，却继续直接使用 3D PLACES 做稀疏化和 route planning，因此问题实际上没有被解决。

---

# 4. Phase 2 审查 — Dense DSG → ANYmal schema exporter

状态：

```text
软件实现基本完成
```

实际输出：

```text
2472 nodes
8833 source undirected edges
→ 17666 directed edges
```

validator：

```text
valid = true
source components = [2455,5,3,3,3,3]
exported components = [2455,5,3,3,3,3]
topology_preserved = true
```

这是当前整个项目里最可靠的一段。

## 评价

对于：

```text
Khronos PLACES
→ 按用户 ANYmal JSON schema 序列化
```

这一工作已经成立。

包括：

```text
Place ID → waypoint ID
position
orientation
frame
undirected → two directed edges
ANYmal default policy
```

schema test 也与用户真实 waypoint sample 对齐。

## 未解决问题

仍然不能证明：

```text
ANYmal native loader 一定接受大整数非连续 ID
stamp=0 一定合法
tolerance=[0,0] 的具体语义
default policy 对所有 edge 都正确
```

这些属于最终真机接口问题，不影响当前 RViz2 mock。

---

# 5. Phase 3 审查 — 当前最严重问题

状态：

```text
代码完成，但算法需要返工
```

当前算法：

```text
3D Euclidean FPS
↓
每个 place 分配给最近 representative
↓
原始 edge 跨 cluster
↓
representative 之间直接建 edge
```

问题是：

```text
Euclidean close
!=
graph/topological close
!=
robot directly traversable
```

---

## 5.1 独立发现：大量 sparse edge 是 topology shortcut

我使用：

```text
generated/waypoints.json
generated/waypoints_sparsified_R3.0.json
```

独立计算：

对于每条 R=3.0 sparse edge：

```text
straight sparse edge distance
vs
两个 representative 在原始 dense Khronos graph 中的 shortest-path distance
```

R=3.0：

```text
83 sparse nodes
284 undirected sparse edges
```

结果：

```text
median geodesic/straight ratio = 1.57
mean ratio = 4.73
max ratio = 31.33
```

284 条 edge 中：

```text
ratio > 1.2 : 227
ratio > 1.5 : 153
ratio > 2.0 : 108
ratio > 5.0 : 59
```

最严重例子：

```text
sparse edge:
4816 ↔ 26905

Euclidean distance:
3.03 m

但原始 Khronos graph 中：
shortest path ≈ 95.02 m

ratio ≈ 31.33
```

这意味着：

> 两个点空间上只有 3 m 左右，但在真实自由空间 topology 上需要绕约 95 m 才能到达。

典型原因就是：

```text
平行走廊
隔墙房间
不同拓扑区域
```

因此当前 sparse edge 很可能直接跨墙/跨区域。

---

## 5.2 当前“0 穿墙边”检测不能证明安全

现有 `_wall_check()` 判断：

```text
edge 两端的 GVD distance < 0.3
AND edge length > 1.5
```

才判 suspicious。

这个判据只能看：

```text
edge endpoints
```

不能检查：

```text
两点之间的直线穿过什么
```

因此：

```text
0 suspicious edges
```

不能等价于：

```text
0 through-wall edges
```

当前报告中的：

> “无靠墙/穿墙问题，edge 可信”

需要撤回或改写。

---

# 6. Phase 3 第二个核心问题 — z 不是地面导航层

当前 dense PLACES：

```text
z = 1.1 ~ 3.9 m
median ≈ 2.5 m
```

并且 z 值分布在多个高度层：

```text
1.1
1.3
1.5
...
3.9
```

这明显更像：

```text
3D free-space / GVD skeleton
```

而不是机器人底盘/足端所在的 ground surface。

R=3.0 sparse graph：

```text
z = 1.1 ~ 3.9 m
```

284 条 undirected sparse edges 的 vertical difference：

```text
median |Δz| = 1.4 m
mean |Δz| ≈ 1.39 m
max |Δz| = 2.8 m
```

其中：

```text
|Δz| > 1.0 m : 164 / 284
|Δz| > 2.0 m : 107 / 284
```

这显然不是普通 office 单层地面导航图。

---

## 6.1 甚至示例 route 本身就在高空

当前：

```text
R0 → R12
```

route：

```text
3896
25371
9445
9564
18326
10856
16186
```

这 7 个节点：

```text
全部 z = 3.9 m
```

因此当前绿色 mock route：

> XY 形状看起来合理，但实际上在 3D 数据里是 z=3.9 m 的路线。

所以现在的图可以验证：

```text
XY projection consistency
```

但不能验证：

```text
ANYmal ground navigation
```

---

# 7. Phase 3 连通分量问题

原始 PLACES：

```text
6 components
```

R=3.0 sparse：

```text
1 component
```

文档解释为：

```text
小碎片被 representative 覆盖吸收
```

但这不应被称为：

```text
topology preserved
```

因为：

```text
source disconnected
→ output connected
```

本身就是 topology 变化。

更重要的是：

当前 assignment 使用全局欧氏最近点，不区分 connected component。

因此一个小 component 的节点可以被 assignment 到另一个 component 的 representative。

在 R=1.5 / R=2.0 候选中，我甚至检测到：

```text
某些 sparse edge 的两个 representative
在原始 graph 中根本不可达
```

即：

```text
original graph distance = ∞
```

说明当前 quotient construction 确实能创造跨 source component 的连接。

这是必须修复的 bug，不只是报告措辞问题。

---

# 8. Phase 4 — Semantic → Waypoint

状态：

```text
软件实现完成，但输出依赖错误的 Phase 3 graph
```

当前：

```text
22 / 22 room 映射成功
```

但：

```text
22 rooms
→ 只有 16 个 unique anchors
```

共享 waypoint：

```text
WP3896: R0, R3, R19
WP9428: R2, R10
WP5495: R5, R20, R21
WP5381: R6, R7
```

这不一定绝对错误。

如果 waypoint 位于：

```text
共享门口 / 公共走廊
```

可以合理。

但当前没有验证：

```text
anchor 是否真的属于该 room
anchor 是否位于 room entrance
anchor 是否跨墙
```

更严重的是：

当前 room place 也可能被欧氏 assignment 到：

```text
另一个房间/隔墙区域的 representative
```

因此 100% resolution 目前只表示：

```text
每个 room 都得到一个整数 waypoint ID
```

不能表示：

```text
每个 room 都得到正确导航 anchor
```

---

# 9. Phase 5 — Mock Planner

状态：

```text
Dijkstra 实现正确
但只验证 graph-theoretic reachability
```

231/231：

```text
room anchor pair 可达
```

这个结果依赖：

```text
sparse graph 被错误合并成一个 component
```

所以当前的：

```text
100% route planning success
```

不能作为物理导航性能指标。

正确表述应该是：

> 当前生成 graph 在图论上单连通，Dijkstra API 工作正常。

---

# 10. Phase 6 — LLM+RAG E2E

状态：

```text
接口链路完成
真实在线联调未完成
```

文档自己明确说明：

```text
现有 RAG vector_db 路径在工作站不可用
```

因此当前 E2E 使用：

```text
与 RAG 输出相同的 target_ref/task_plan 格式
```

但不是实际运行：

```text
LLM → RAG → bridge
```

这一点并非严重问题，因为 LLM+RAG 已经在另一个阶段独立完成。

但论文中要准确写：

```text
interface-level E2E / replay-based E2E
```

而不是：

```text
full online integrated E2E
```

除非之后实际接一次现有 RAG 输出。

---

# 11. Phase 7 — Visualization

状态：

```text
静态可视化完成
RViz2 当前目标尚未完成
```

已有：

```text
places_vs_waypoints.png
route_viz.png
```

优点：

```text
83 sparse waypoint 位置
100% 是原始 Place node 的位置
```

因此：

```text
coordinate copy / serialization 没有发生偏移
```

这一结论成立。

但：

```text
100% spatial coincidence
```

是由于 waypoint 本身就是 source Place 的子集。

它不能证明：

```text
edge 合法
route 可走
z 正确
```

当前图还是：

```text
2D x/y projection
```

恰好隐藏了 z=1.1~3.9 的问题。

---

# 12. Phase 8 — Benchmark

状态：

```text
benchmark 代码完整
但部分指标定义需要重写
```

目前：

```text
Export Validity = true
Semantic resolution = 100%
Route success = 100%
E2E success = 100%
Invalid waypoint IDs = 0%
```

其中可靠指标：

```text
Schema/Export Validity
Invalid Waypoint ID Rate
代码层 Semantic Mapping Coverage
```

不可靠/需重定义：

```text
Topology Preservation
Route Planning Success
E2E Navigation Success
```

---

## 12.1 E2E 100% 实际上是一个近乎必然结果

`MockExecutor.run()`：

```text
先 shortest_path()
↓
如果找到 route
↓
逐 waypoint 自动推进
↓
必然 REACHED
```

它没有：

```text
collision
local planner failure
edge invalidity
motion constraints
dynamic obstacle
```

因此：

```text
Route success = 100%
```

时：

```text
Mock E2E success ≈ 必然 100%
```

这两个指标没有提供独立的信息。

建议将其改名：

```text
Pipeline Execution Consistency
```

不要称为：

```text
Navigation Success Rate
```

---

# 13. 当前真实完成度

## Phase 状态表

| Phase | 实现完成度 | 功能可信度 | 结论 |
|---|---|---|---|
| 0 Reference audit | 完成 | 高 | 保留 |
| 1 DSG audit | 完成 | 高 | 保留 |
| 2 Dense exporter | 完成 | 高（schema层） | 保留 |
| 3 Sparsification | 已实现 | **低** | **必须返工** |
| 4 Semantic mapping | 已实现 | 中低 | Phase 3 修复后重跑 |
| 5 Mock planner | 完成 | 中 | planner代码保留，graph重建后重跑 |
| 6 E2E interface | 完成 | 中 | 真实RAG联调可最后补一次 |
| 7 Static viz | 完成 | 中 | **缺 RViz2** |
| 8 Benchmark | 已实现 | 中低 | 指标需修订 |

---

# 14. 当前最重要的修改：不要继续沿现在的 Phase 3

应该新增：

```text
Phase 3R — Robot-Navigable Graph Reconstruction
```

---

# 15. Phase 3R Step 1 — 先解决“3D free-space graph ≠ ground graph”

必须明确：

```text
Khronos PLACES
是 3D volumetric free-space skeleton
```

不能直接把其 z 作为 ANYmal waypoint z。

对于当前单层 Office 场景，至少需要产生：

```text
2D/2.5D ground navigation graph
```

推荐顺序：

### 方案 A（最好）

利用：

```text
Khronos mesh / TSDF / floor geometry
```

给每个 navigation node 找：

```text
ground height z_ground(x,y)
```

最终 waypoint：

```text
[x, y, z_ground]
```

### 方案 B（当前 RViz2 mock 足够）

如果 Office 已确认单层且 floor frame 已知：

```text
PLACES 用于 x/y topology
z 统一投影到 visualization/navigation floor
```

但必须明确标记：

```text
z_source = projected_for_mock
```

不要把这个输出称为真机最终 waypoint z。

---

# 16. Phase 3R Step 2 — Sparsification 必须 graph-aware

禁止继续：

```text
global Euclidean nearest representative
```

至少改成：

```text
per connected component
+
graph/geodesic-aware clustering
```

即：

```text
原始 PLACES component
↓
在该 component 内选 reps
↓
place 只能 assignment 到同 component rep
```

这样至少不会发生：

```text
component A
→ 被 component B 的 representative 吸收
```

---

# 17. Phase 3R Step 3 — 每条 sparse edge 必须保留 witness path

对于 sparse：

```text
A ↔ B
```

必须保存/验证：

```text
A
→ 原始 dense PLACES path
→ B
```

建议输出 debug metadata：

```text
source_dense_path
source_path_length
euclidean_distance
geodesic_stretch
```

至少在开发/validation report 中保存，不一定写进 ANYmal JSON。

---

# 18. Phase 3R Step 4 — 新增 Geodesic Stretch 验证

定义：

```text
stretch =
dense_graph_shortest_path(A,B)
/
euclidean_distance(A,B)
```

如果 ANYmal edge 语义是：

```text
两个 waypoint 之间允许 local planner 直接走
```

则 stretch 太大意味着：

```text
潜在跨墙 / 跨走廊 shortcut
```

建议第一轮人工门槛：

```text
stretch <= 1.2 ~ 1.5
```

阈值最终需结合真实 ANYmal local planner 能力调整。

当前 R=3：

```text
153 / 284 edges > 1.5
108 / 284 > 2
59 / 284 > 5
```

所以现在 graph 不通过这一验收。

---

# 19. Phase 3R Step 5 — 不要用“匹配另一个地图的节点密度”决定 R

当前：

```text
R=3.0
```

主要依据是：

```text
节点密度和 edge mean 接近用户 ANYmal sample
```

但 ANYmal sample 对应另一个具体地图。

不同环境：

```text
走廊宽度
房间尺寸
拓扑复杂度
```

不同。

因此：

```text
sample density
```

只能作为参考，不能作为主要 selection criterion。

真正应该优化：

```text
topology safety
semantic room coverage
route stretch
edge local traversability
节点数
```

---

# 20. Phase 4R — Room Anchor 重新验证

Phase 3R 后重新产生 semantic map。

对每个 room：

必须检查：

```text
anchor waypoint 是否来自该 room 的 Place
或
明确属于 room entrance / doorway
```

增加：

```text
room_anchor_unique
anchor_inside_room_or_entrance
anchor_distance
```

并人工可视化 22 个 room anchors。

共享 anchor 不一定禁止，但必须解释。

---

# 21. 当前用户真正需要的下一阶段：RViz2 Mock Simulation

用户当前目标：

> 不需要物理 ANYmal D 仿真，只需要在 RViz2 中把链路可视化模拟跑起来。

这是完全可以做到的。

不需要：

```text
ANYmal mass
joint inertia
actuator model
controller gains
foot contact model
```

---

# 22. RViz2 最小实现

新增 ROS2 package，例如：

```text
khronos_anymald_rviz/
```

发布：

```text
/anymald_waypoints/nodes
  visualization_msgs/MarkerArray

/anymald_waypoints/edges
  visualization_msgs/Marker

/anymald_room_anchors
  visualization_msgs/MarkerArray

/anymald_route
  nav_msgs/Path

/anymald_route_marker
  visualization_msgs/Marker

/anymald_mock_robot
  visualization_msgs/Marker
```

所有：

```text
frame_id = map
```

并与当前 Khronos RViz2 的：

```text
Fixed Frame
```

保持一致。

---

# 23. RViz2 中应该同时显示

```text
Khronos Background Mesh
Khronos original PLACES（可选）
ANYmal sparsified waypoint nodes
ANYmal waypoint edges
Room anchors
Current goal
Current planned route
Mock robot
```

这样才能真正做：

```text
Khronos output
vs
generated ANYmal graph
```

3D 同屏验证。

---

# 24. Mock Robot 不需要 ANYmal D 模型

第一版：

```text
CUBE / CYLINDER / ARROW Marker
```

即可。

它只代表：

```text
ANYmal D base pose
```

例如：

```text
green box = mock ANYmal D
arrow = heading
```

沿 route 定时插值移动。

这已经足够验证：

```text
RAG target
↓
waypoint
↓
route
↓
mock robot motion in RViz2
```

---

# 25. 如果希望 RViz2 里看起来像 ANYmal D

可以后续使用公开的：

```text
ANYbotics anymal_d_simple_description
```

作为 visualization-only URDF。

但这不是当前必要条件。

即使完全没有 ANYmal model：

```text
Marker mock robot
```

也能完成当前 thesis-level software closed-loop visualization。

---

# 26. RViz2 Mock Executor 推荐行为

不要继续“一个 step 瞬移一个 waypoint”。

改成：

```text
timer 20–50 Hz
```

在每个 route edge 之间：

```text
position linear interpolation
yaw = atan2(dy,dx)
```

模拟速度例如配置：

```text
mock_speed_mps
```

发布：

```text
Pose / TF / Marker
```

到节点附近：

```text
distance < mock_goal_tolerance
```

切下一段。

最终：

```text
REACHED
```

---

# 27. 当前阶段建议的停止点

在没有 ANYmal D 私有 navigation/mission stack 的情况下，完成以下内容即可停止：

```text
1. 修复 ground/topology graph
2. semantic → waypoint
3. Dijkstra/mock global route
4. RViz2 overlay
5. mock robot animation
6. current room → target room E2E
7. benchmark + screenshots / rosbag
```

这已经能证明：

> **Khronos DSG/PLACES 可以被确定性转换为 ANYmal-D-compatible waypoint representation，并能从现有语义导航目标驱动一个 RViz2 中的 waypoint-planning mock execution。**

但不能声称：

> **已经验证 ANYmal D 物理可行性或原生导航栈兼容性。**

---

# 28. 真机/完整仿真才需要解决的剩余问题

以后有 ANYmal D / Research stack 后验证：

```text
native waypoint loader 是否接受当前 JSON
waypoint ID 是否允许稀疏大整数
z 的真正定义
orientation 的真正用途
tolerance 含义
traversability 的真实含义
max_deviation 单位与含义
strict_path_follower
perceptive_walk
nfm_preset
mission/goal API
local planner failure behavior
```

这些现在不用阻塞 RViz2。

---

# 29. 对当前“缺 ANYmal D 模型参数无法继续”的判断

准确说法应是：

```text
缺 ANYmal D 完整模型/控制参数
→ 阻塞高保真物理仿真和原生控制验证
```

但：

```text
不阻塞 RViz2 visualization mock
不阻塞 waypoint graph validation
不阻塞 semantic→waypoint E2E
不阻塞 route animation
```

因此当前项目还能继续一个完整阶段。

---

# 30. 最推荐的下一步执行顺序

```text
Step 1
修复 Phase 3 sparsification
（最高优先级）
↓
Step 2
解决 ground z / visualization z
↓
Step 3
重新生成 semantic mapping
↓
Step 4
重新跑 planner + benchmark
↓
Step 5
新增 RViz2 Marker/Path publisher
↓
Step 6
新增 Mock ANYmal base animation
↓
Step 7
与 Khronos mesh 同屏 overlay
↓
Step 8
跑 Office semantic E2E
↓
Step 9
保存 screenshots / rosbag / quantitative report
↓
STOP
等待 ANYmal D native stack / 真机
```

---

# 31. 给 Agent 的直接修改任务

```text
当前 Phase 0–8 已审计。

不要继续 Phase 9 物理仿真。

首先返工 Phase 3：

1. 当前 sparsifier 的 global 3D Euclidean FPS + nearest representative assignment
   会产生 topology shortcut，必须停止使用。

2. 所有 clustering/assignment 必须限制在 source connected component 内。

3. 优先使用 graph/geodesic distance，而不是纯 Euclidean distance。

4. 每条 sparse edge 必须有 source dense graph witness path。

5. 新增 geodesic stretch validator：
   dense shortest-path distance / sparse euclidean distance。

6. 不允许 source graph disconnected components 被静默合并。

7. 当前 PLACES z=1.1~3.9 是 volumetric free-space 高度，
   不可直接视为 ANYmal ground waypoint。
   为当前 RViz2 mock 实现明确的 ground projection / visualization z，
   并在 metadata/report 标记为 mock projection。

8. 修复后重新生成：
   waypoints_sparsified
   semantic_to_waypoint
   benchmark

9. Benchmark 删除/改名当前 tautological 的
   “Mock End-to-End Navigation Success Rate”。
   改为 Pipeline Execution Consistency。

10. 新增 RViz2 ROS2 package：
    - MarkerArray waypoint nodes
    - LINE_LIST edges
    - room anchors
    - nav_msgs/Path route
    - mock robot Marker
    - timer interpolation motion
    - frame_id=map

11. 在 Khronos 当前 RViz2 中同屏显示：
    Background Mesh + waypoint graph + route + mock robot。

12. 当前阶段完成后停止。
    不需要 ANYmal D dynamics/controller。
```

---

# 32. 最终评价

当前工作不是失败。

相反：

```text
DSG parsing
schema conversion
semantic mapping API
planner API
mock execution framework
testing framework
```

都已经搭好了。

真正需要返工的是：

```text
“如何从 3D Khronos PLACES 得到物理合理的稀疏 ANYmal waypoint graph”
```

这恰好也是整个 Khronos→ANYmal D 适配里最有研究价值的一步。

修复它之后，当前不需要完整 ANYmal D 参数，也可以把：

```text
Natural Language
→ RAG target
→ ANYmal waypoint
→ route
→ RViz2 mock robot
```

完整演示出来。
