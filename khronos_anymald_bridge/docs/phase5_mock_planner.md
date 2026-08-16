# Phase 5 — Mock ANYmal Waypoint Planner 报告

> **Phase 5 产出**（2026-08-09）
> 代码：`waypoint_graph/planner.py`（Dijkstra）、`plan_waypoints.py`（CLI）、`verify_room_routes.py`（可达性验证）
> 测试：`tests/test_planner.py`（5 个，共 21 个全过）

---

## 1. 实现

- **`WaypointGraph`**：从 `waypoints.json`（ANYmal schema）构建有向图（节点 + 双向边）
- **`shortest_path`**：Dijkstra，edge cost = **欧氏距离**（计划 §19.2；traversability 是否参与 ANYmal 全局 cost 未确认，不猜）
- **CLI** `plan_waypoints.py`：`--from-room R --to-room R`（经 semantic_to_waypoint 解析 anchor）或直接 `--start/--goal waypoint id`

## 2. 验证结果（sparsified 图 R=3.0，83 waypoints）

### 两两可达性（22 个 room anchor）
```
room 数: 22   配对: 231
可达: 231/231 (100.0%)
```

### 示例 route
```
R0→R1: 2 waypoints, 5.6m
R0→R12: 7 waypoints, 26.1m
R0→R6: 3 waypoints, 10.3m
```

## 3. 结论

1. **waypoint graph 合法、连通、可规划**：所有 room anchor 两两可达（100%），主连通分量覆盖全部 22 房间。
2. **goal 可达**：任意 room 的语义目标都能解析为可达 waypoint 并规划出 route。
3. **R0→R3 共享同一 anchor（waypoint 3896，0.0m）**：相邻/重叠房间可能映射到同一导航点，对导航可接受（同一点可达两房间）。

## 4. 注意

- 演示 route 用的 edge cost 是欧氏距离；若 ANYmal 真机确认 traversability/max_deviation 参与全局 cost，再更新 cost 规则（计划 §19.2）。
- 验证的是"waypoint graph 可规划"，不是 ANYmal 腿部物理控制（计划 §19.3）。
