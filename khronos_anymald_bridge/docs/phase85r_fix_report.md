# Phase 8.5R — RViz2 correctness fix（复审修正）

> **依据 khronos_anymald_phase85_reaudit.md §1-3 / §9**（2026-08-11）
> 修复 3 个新问题（A/B/C）+ §9 Fix 1-6。**未进入 ANYmal dynamics / Isaac / real robot**。

---

## 1. 复审发现的问题与修复

### Fix 1（问题 A）：planner 统一使用 witness geodesic cost

- **问题**：文档写 "route cost 已用 witness geodesic"，但 `waypoint_graph/planner.py` 和 `mock_robot_node.py::Planner` 都用欧氏距离，文档与实现不一致；实测 177/231 room pair 的 route 会因 cost 不同而改变。
- **修复**：
  - `waypoint_graph/planner.py`：`WaypointGraph` 支持 witness 边权（`witness_paths.json` 的 `geodesic_m`），无 witness 时 fallback 欧氏。
  - `mock_robot_node.py`：**删除自建 Planner，直接复用 bridge `WaypointGraph + shortest_path`**（避免 planner 逻辑分叉）。
- **验证**：R0→R12 witness cost = **44.12m**（geodesic），route 序列随之改变。

### Fix 2（问题 B）：witness path 反向 traversal reverse

- **问题**：`witness_paths.json` 固定存 min→max，`route_positions()` 对 max→min 方向不 reverse，产生错误的第一段跳跃（旧 max jump 6.84m，多目标拼接 169.9m 不可信）。
- **修复**：新增纯函数 `witness_expand.expand_route()`（route 方向与 stored 相反时 `reversed(path)`），`BridgeData.route_positions()` 复用。
- **验证**（关键，与复审 §2 预测完全一致）：
  - R0→R12：dense pts=59，**max_step=2.81m**（复审预测 ≈2.81m），jump>3m = **0**
  - R0→R12→R4→R9：dense pts=132，len=176.13m，**max_step=2.81m**，jump>3m = **0**
  - headless RViz state 总长 176.1m 与复算 176.13m 一致

### Fix 3：新增测试

`tests/test_phase85r.py`（6 个）：
- `test_planner_uses_witness_cost` / `test_planner_fallback_euclidean_without_witness`
- `test_witness_route_forward` / `test_witness_route_reverse`
- `test_route_positions_no_endpoint_jump`
- `test_launch_config_static`（IfCondition / publish_stretch / package deps）

**测试总数 28 → 34 全过**。

### Fix 4：launch 修复（问题 C1/C2/C3）

- `publish_stretch` 参数（默认 true）正确传给 graph_publisher（`--publish-stretch true/false`）
- `IfCondition(LaunchConfiguration("rviz"))` 替代 Python 直接比较
- `package.xml` 补 `ament_index_python / launch / launch_ros / rviz2` 依赖

### Fix 5：frame 参数化（问题 C4）

- `graph_publisher` / `mock_robot_node` / `data_loader` 的 `frame_id` 从硬编码 `"map"` 改为参数 `--frame`（launch `frame:=map|world`）
- 用户机器需先确认 `ros2 run tf2_ros tf2_echo world map` 是否有 TF；无则用 `frame:=map` 或加静态 TF

### Fix 6：与 Khronos RViz 同屏（用户机器上人工）

- 提供 `config/anymal_mock.rviz` + launch；推荐在现有 `uHumans2.rviz` 手动 Add our topics 后 Save As `uHumans2_anymald_mock.rviz`（audit §4 方法 A）
- 需有显示器环境验证（headless 已验证 publisher）

---

## 2. 复审 §8 完成度评级更新

| 模块 | 复审状态 | 更新后 |
|---|---|---|
| Witness generation | 🟡 需修 reverse | ✅ 已修（max_step 2.81m，无跳跃） |
| Planner cost | ❌ 与文档不一致 | ✅ witness geodesic（44.12m R0→R12） |
| Semantic mapping | 🟡 需视觉验证 | ✅ 软件完成；22 unique anchors；视觉验证待用户 |
| Benchmark | 🟡 planner cost 修后重跑 | ✅ 已重跑（全部通过） |
| RViz launch | 🟡 需修 condition/stretch/deps | ✅ 已修 |
| 测试 | 需 31/32+ | ✅ **34 passed** |

---

## 3. 重跑验证结果

```
pytest: 34 passed
benchmark: Export valid / Topology 6=6 / Semantic 1.0 / Route 1.0 / Pipeline 1.0 / Invalid 0%
E2E: TASK_SUCCESS（witness cost route）
RViz headless: graph_publisher + mock_robot（MOVING→REACHED, route 176.1m, max_step 2.81m）
```

## 4. 边界

- 高 stretch edge（>1.5 共 254）仍不能作为真机 ANYmal native edge（audit §6），RViz mock 可接受（route 沿 dense witness path）
- witness 构造用 global shortest（144/624 穿过第三 cluster），对 mock 可用；native edge 需 cluster-local boundary witness（audit §7，留待真机）
- 未进入 ANYmal D physics / native stack
