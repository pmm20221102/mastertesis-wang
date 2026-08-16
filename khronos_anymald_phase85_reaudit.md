# Khronos → ANYmal D Phase 3R / 8.5 复审报告

## 0. 总结

本次复审对象：用户提交的最新 `khronos_anymald_bridge` 产物。

实际执行：

```text
python3 -m pytest tests -q
28 passed
```

并完成 Python 静态编译检查：

```text
PYCOMPILE_OK
```

因此可以确认：

- 旧审计指出的 3 个核心 bug 已基本处理：
  - global Euclidean sparsification → per-component geodesic FPS；
  - source 6 components → output 6 components；
  - z=1.1~3.9 volumetric layer → mock ground z=0；
- RViz2 ROS2 package 已经形成；
- Marker/Path/mock robot publisher 代码已经存在；
- 现阶段确实可以继续做到 RViz2 mock 闭环，而不需要 ANYmal D dynamics/controller。

但是复审又发现 3 个需要优先修复的问题。

---

# 1. 新问题 A：Planner 实际仍使用 Euclidean edge cost

文档多处写：

```text
route cost 已用 witness geodesic
```

例如：

```text
docs/phase3r_reconstruction.md
docs/phase8_benchmark.md
docs/phase85_rviz2_mock.md
generated/sparsified_report.json
```

但实际代码：

```text
waypoint_graph/planner.py
khronos_anymald_rviz/mock_robot_node.py::Planner
```

均使用：

```python
distance = Euclidean waypoint distance
```

并没有使用：

```text
witness_paths.json -> geodesic_m
```

因此文档与实现不一致。

## 独立复算

R0 → R12：

当前 Euclidean sparse planner：

```text
4275
→4262
→4326
→5423
→8335
→8670
→9633
→9646
→9534
→11436
→11228
→12136
```

Euclidean sparse cost：

```text
33.81
```

如果按 witness `geodesic_m` 作为 edge cost：

```text
4275
→4262
→4326
→5423
→8335
→8670
→9633
→10301
→9825
→11436
→11446
→12136
```

route 本身就发生改变。

22 个 room 的 231 个 pair 中：

```text
177 / 231
```

的 shortest sparse route 会因为：

```text
Euclidean cost
vs
witness geodesic cost
```

而不同。

这说明它不是报告措辞小问题，而是实际 planner 行为问题。

## 修改

`WaypointGraph` 应加载一个 edge-cost sidecar：

```text
witness_paths.json
```

或把 mock 专用 `geodesic_m` 放入内部 graph metadata。

然后：

```text
Dijkstra cost = witness geodesic_m
```

`mock_robot_node.Planner` 不应自己再实现另一套 Euclidean planner。

最好直接复用：

```text
waypoint_graph/planner.py
```

避免两个 planner 逻辑分叉。

---

# 2. 新问题 B：witness path 在反向 traversal 时没有 reverse

`witness_paths.json` 的 key 使用：

```text
(min_id, max_id)
```

witness path 固定保存：

```text
min_id → max_id
```

但：

```python
BridgeData.route_positions()
```

无论实际 route 是：

```text
min → max
```

还是：

```text
max → min
```

都直接：

```python
for pid in witness["path"][1:]:
```

没有在：

```text
a > b
```

时 reverse path。

这是实际动画 bug。

## 独立复算

624 个 sparse undirected edge 中，仅考虑 reverse traversal：

```text
约 276 条
```

会产生明显错误的第一段跳跃。

R0 → R12 当前 sparse route 含 3 条 reverse witness edge：

修正 witness direction 后：

```text
expanded XY length ≈ 58.15 m
max dense step ≈ 2.81 m
```

当前代码：

```text
expanded XY length ≈ 72.29 m
max jump ≈ 6.84 m
```

多目标：

```text
R0 → R12 → R4 → R9
```

当前文档记录：

```text
169.9 m
```

这个数正好来自错误方向的 witness 拼接。

将 witness direction 修正后，同一 Euclidean sparse route：

```text
约 129.92 m
```

因此当前：

```text
phase85_rviz2_mock.md 中 169.9m
```

不是可信 route length。

## 修改

在：

```python
BridgeData.route_positions(route)
```

中：

```python
key = (min(a,b), max(a,b))
path = witness[key]["path"]

if a != path[0]:
    path = list(reversed(path))
```

并添加测试：

```text
test_witness_route_forward
test_witness_route_reverse
test_route_positions_no_endpoint_jump
```

---

# 3. 新问题 C：RViz launch / frame 还有实际集成问题

## C1. `--publish-stretch` 没有从 launch 传给 graph_publisher

`graph_publisher.py` 只有传：

```text
--publish-stretch
```

才发布 stretch 分类 edge。

但：

```text
mock_navigation.launch.py
```

启动 graph_publisher 时没有该参数。

同时：

```text
anymal_mock.rviz
```

又默认打开：

```text
/anymald/waypoints/stretch_edges
```

结果：

> 用 launch 正常启动时，StretchEdges display 很可能是空的。

修改：

```text
DeclareLaunchArgument("publish_stretch", default_value="true")
```

并正确传给节点。

---

## C2. RViz launch condition 写法应改

当前：

```python
condition=LaunchConfiguration("rviz") == "true"
```

不建议这样写。

ROS2 launch 标准写法：

```python
from launch.conditions import IfCondition

condition=IfCondition(LaunchConfiguration("rviz"))
```

避免条件在 launch-description 构造阶段被 Python 直接比较。

---

## C3. package.xml 缺 launch runtime dependencies

`mock_navigation.launch.py` 使用：

```text
ament_index_python
launch
launch_ros
rviz2
```

但 `package.xml` 没有显式声明这些运行依赖。

建议补：

```xml
<exec_depend>ament_index_python</exec_depend>
<exec_depend>launch</exec_depend>
<exec_depend>launch_ros</exec_depend>
<exec_depend>rviz2</exec_depend>
```

---

## C4. Frame 需要与用户当前 Khronos RViz 真正对齐

当前 package 全部硬编码：

```text
frame_id = map
Fixed Frame = map
```

而用户之前当前 Khronos RViz 截图中：

```text
Global Options -> Fixed Frame = world
```

这不一定是错误：

如果系统存在：

```text
world ↔ map TF
```

则可以正常显示。

但在真正同屏前必须检查：

```bash
ros2 run tf2_ros tf2_echo world map
```

如果不存在 transform：

> mock graph 会无法与 Khronos mesh 同屏。

最稳妥的修改是：

```text
frame_id
```

做成 ROS 参数/launch 参数，而不是在：

```text
graph_publisher.py
mock_robot_node.py
data_loader.py
```

多处硬编码 `"map"`。

---

# 4. RViz2 目前还不是“真正同屏完成”

当前：

```text
config/anymal_mock.rviz
```

只包含：

```text
Grid
WaypointNodes
WaypointEdges
RoomAnchors
StretchEdges
Route
Goal
MockRobot
```

并没有包含用户当前 Khronos RViz 中的：

```text
Scene Graph
Background Mesh
Object trajectories
Khronos plugin displays
```

因此现阶段真实状态应表述：

```text
RViz2 mock package / publisher 已完成
```

但：

```text
Khronos mesh + mock graph 同屏
```

仍需要用户在有显示器机器上做最后一次人工集成。

推荐：

### 方法 A（最简单）

打开现有 Khronos：

```text
uHumans2.rviz
```

然后手动 Add：

```text
MarkerArray /anymald/waypoints/nodes
Marker      /anymald/waypoints/edges
MarkerArray /anymald/room_anchors
Path        /anymald/route
Marker      /anymald/mock_robot
Marker      /anymald/goal_marker
```

确认无误后：

```text
Save Config As...
```

保存成：

```text
uHumans2_anymald_mock.rviz
```

比单独维护一个没有 Khronos displays 的 `anymal_mock.rviz` 更适合当前项目。

---

# 5. Semantic room anchor 仍有一个“结论过度”问题

当前：

```text
22 rooms
22 unique anchors
```

是事实。

但：

```text
22 unique
```

本身不是 correctness 指标。

相邻 room 合理地共享一个 corridor/doorway waypoint，并不一定错误。

更重要的是当前 mapping：

```text
Room places
→ 每个 place 的 geodesic representative
→ 从这些 reps 中选距离 room position 最近的 rep
```

代表节点：

```text
可能不是该 Room 原始 Place 集合中的节点
```

因为 clustering 只保证：

```text
same connected component
```

不保证：

```text
same room
```

所以文档里的：

```text
“不再有跨墙 anchor”
```

目前没有严格证据。

## 建议新增 validator

对每个 Room anchor 统计：

```text
anchor_is_original_room_place
anchor_xy_to_room_centroid
anchor_room_membership
anchor witness path from room place
```

至少在 RViz2 同屏里人工检查 22 个 anchor。

当前阶段不需要强制 anchor 必须 unique。

---

# 6. 高 stretch edge 仍然不能作为真机 ANYmal native edge

当前修复后：

```text
stretch median = 1.32
mean = 1.62
max = 10.96
>5 = 14 edges
```

这比旧版显著改善。

对 RViz2 mock：

```text
可以接受
```

前提：

```text
planner 使用 witness cost
route visualization 使用正确方向的 witness path
```

因为这样 mock 实际运动沿 dense free-space path。

但是对最终：

```text
ANYmal native waypoints.json
```

不能直接宣布这 624 条 sparse edge 都是合法 native edge。

原因：

ANYmal loader 很可能把：

```text
A → B
```

解释为 local planner 可以直接处理 A/B 间这一段。

而高 stretch edge：

```text
A/B 距离很近
但真实 free-space path 要绕几米
```

可能不满足这个语义。

这属于未来真机阶段。

当前 RViz2 mock 不需要解决。

---

# 7. witness 构造还有一个后续优化点

当前 sparse edge 因：

```text
某条 dense boundary edge
两端被 assignment 到不同 reps A/B
```

而产生。

但 witness 使用：

```text
A → B 的全局 shortest dense path
```

而不是：

```text
A → boundary_u
→ boundary edge
→ boundary_v
→ B
```

且不限制 witness 只能处于 cluster A/B。

独立统计：

```text
144 / 624 witness path
```

内部经过了 assignment 属于第三 representative cluster 的 dense node。

这不影响：

```text
它仍然是一条真实 Khronos free-space path
```

因此对 RViz mock 可用。

但对“sparse edge 的局部语义”不够纯。

未来如果需要 native ANYmal edge，应考虑构造：

```text
cluster-local boundary witness
```

而不是 global shortest witness。

---

# 8. 当前完成度重新评级

| 模块 | 当前状态 |
|---|---|
| Phase 0 reference audit | ✅ 完成 |
| Phase 1 DSG audit | ✅ 完成 |
| Phase 2 dense schema export | ✅ 完成 |
| Phase 3R component/geodesic sparsification | ✅ 核心修复完成 |
| Ground projection for mock | ✅ 完成 |
| Witness generation | 🟡 可用于 mock，但需修 reverse |
| Planner cost | ❌ 与文档不一致，仍 Euclidean |
| Semantic mapping | 🟡 软件完成，room anchor 需视觉验证 |
| Benchmark | 🟡 框架完成，planner cost 修后重跑 |
| RViz publisher | ✅ 基本完成 |
| RViz launch | 🟡 需修 condition / stretch / deps |
| Khronos 同屏人工验证 | ⏳ 未完成 |
| ANYmal D physics/native stack | ⏳ 当前不需要 |

---

# 9. 当前最推荐的最后修改

不要继续扩展 Phase 9。

只做一个：

```text
Phase 8.5R — RViz2 correctness fix
```

## 必须修

### Fix 1

统一 planner：

```text
Dijkstra edge cost = witness geodesic_m
```

不要再用 sparse Euclidean length。

### Fix 2

witness route：

```text
如果 route 方向与 stored witness 相反
→ reverse witness path
```

### Fix 3

新增 reverse witness tests。

### Fix 4

launch：

```text
IfCondition
publish_stretch parameter
package dependencies
```

### Fix 5

frame 参数化：

```text
frame_id:=map/world
```

并在用户机器验证：

```text
world↔map TF
```

### Fix 6

将当前 Khronos RViz config 和 ANYmal mock displays 合并后保存。

---

# 10. 修复后重新跑的验证

必须重新输出：

```text
28+ tests PASS
```

建议测试变成：

```text
31/32+ tests
```

至少增加：

```text
test_reverse_witness_expansion
test_planner_uses_witness_cost
test_launch_config_static
```

然后重新跑：

```text
run_benchmark.py
run_office_e2e.py
RViz headless topics
```

文档中 route cost 的描述才可以继续写：

```text
witness geodesic
```

---

# 11. 用户机器上最后一次人工验收

在有显示器机器上：

```text
Khronos RViz2
+
ANYmal mock topics
```

确认：

```text
[ ] waypoint XY 与 Khronos Scene Graph 对齐
[ ] room anchor 靠近对应 Room
[ ] route 沿走廊/自由空间，不跳墙
[ ] reverse route 也正常
[ ] mock robot 连续移动，无 6~10m 瞬移
[ ] goal 到达后 state=REACHED
[ ] frame 无 TF error
```

保存：

```text
截图
视频
RViz config
终端 trace
```

---

# 12. 是否需要 ANYmal D 模型参数才能继续？

结论：

```text
不需要。
```

对于当前目标：

```text
RViz2 mock semantic navigation
```

已有：

```text
CUBE + ARROW mock robot
```

就足够。

完整 ANYmal D 参数只在后续：

```text
真实动力学
joint control
locomotion
native mission stack
real waypoint loader
```

时需要。

因此：

> 修掉本报告中 planner cost + witness reverse + RViz launch/frame 这几项，再做一次有显示器同屏验收，就可以合理地把当前软件验证阶段封存。
