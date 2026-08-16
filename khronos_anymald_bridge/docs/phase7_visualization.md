# Phase 7 — Graph 可视化报告

> **Phase 7 产出**（2026-08-10）
> 代码：`visualization/visualize.py`
> 产出：`generated/visualization/places_vs_waypoints.png`、`generated/visualization/route_viz.png`

---

## 1. 可视化内容（计划 §22）

| 图 | 内容 |
|---|---|
| `places_vs_waypoints.png` | Khronos PLACES 全部节点（灰点）+ sparsified waypoints R=3.0（蓝点/蓝边）+ room anchors（红星 + R 标签） |
| `route_viz.png` | 同上背景 + R0→R12 绿色 route 高亮 + start/goal 标记 |

工作站在 headless 环境（无显示器/无 RViz），用 matplotlib Agg 后端生成静态 2D top-down 图（x/y，z 忽略）。

## 2. 最重要的验证：空间重合（计划 §22 关键项）

```
空间重合率: 100.0%  (83 sparsified waypoints 全部落在 Khronos PLACES 位置)
```

- **Khronos PLACES 与 ANYmal waypoints 完全空间重合**——waypoints 是 PLACES 的稀疏子集（位置逐点一致），不存在坐标偏移或 frame 错位。
- 这从视觉+数值双重确认了导出链路（loader → exporter → sparsifier）没有破坏坐标。

## 3. 实现说明

- 用系统 python3（`/usr/bin/python3`，有 matplotlib 3.11.1）
- 从 `generated/waypoints.json`（全量 PLACES 位置）、`waypoints_sparsified_R3.0.json`（83 waypoints + 边）、`semantic_to_waypoint.json`（room anchors）加载
- 运行：`python3 visualization/visualize.py`
- 图在工作站 `generated/visualization/`，可下载查看

## 4. 后续

- 如需 RViz 原生可视化（3D、实时），需在 ANYmal 真机/仿真环境（ROS）下运行，属 Phase 9/10
- 本阶段静态图已满足"人工检查 frame 正确、room→place 不跨墙、route 不穿障碍、目标合理"的验证目的（配合 Phase 3 的 0 穿墙边 + 重合率 100%）
