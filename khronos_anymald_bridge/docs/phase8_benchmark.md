# Phase 8R — 无真机 Benchmark（audit 修订版）

> **Phase 8 产出**（2026-08-10，Phase 3R 返工后更新）
> 代码：`run_benchmark.py`
> 产出：`generated/benchmark_report.json`
> 对象：graph-aware sparsified waypoint graph **R_geo=3.0**（261 waypoints / 624 无向边 / ground-z 0）

---

## 1. 结果总表

| # | 指标 | 结果 | 说明 |
|---|---|---|---|
| 1 | **Waypoint Export Validity** | **true** | 通过严格 validator |
| 2 | **Topology Preservation** | **main_component: true, node_coverage: 1.0, source_components=6, exported_components=6** | **不再静默合并分量**（返工核心修复） |
| 3 | **Semantic-to-Waypoint Resolution** | **1.0** (22/22, 22 unique anchors) | |
| 4 | **Route Planning Success** | **1.0** (231/231) | 主分量内 room anchor 对可达 |
| 5 | **Pipeline Execution Consistency** | **1.0** (231/231) | **改名自** "Mock E2E Navigation Success"（见下） |
| 6 | **Invalid Waypoint ID Rate** | **0.0**（目标 0% ✅） | |

## 2. 指标 5 改名说明（审计 §12.1）

原 "Mock End-to-End Navigation Success Rate" 是 **tautological**：`MockExecutor.run()` 先 `shortest_path`，找到 route 后逐 waypoint 自动推进必然 REACHED——无碰撞/避障/motion 约束，不提供独立导航信息。
**改名 `Pipeline Execution Consistency`**：验证 pipeline（semantic→waypoint→route→mock 执行）可一致跑通，**不代表物理导航成功**。

## 3. Topology Preservation（返工修复）

- 旧算法：source 6 分量 → output 1 分量（静默合并，甚至跨分量连边）——**bug**
- 新算法：**exported_components = 6 = source_components**，不跨分量 assignment，无跨分量边
- 主连通分量（2455 places → 255 reps）内结构保持，node_coverage=1.0

## 4. stretch（见 sparsified_report.json）

- stretch median 1.32 / mean 1.62 / max 10.96（旧算法 mean 4.73 / max 31.33）
- route cost 用 witness geodesic 长度（沿真实拓扑，不穿墙）
- 高 stretch 边（>1.5 共 254/624）为隔墙/平行结构桥接，已在报告中标记，不静默删除（删除会破坏区域连通）

## 5. 可靠 vs 需谨慎的指标

**可靠**：Schema/Export Validity、Invalid Waypoint ID Rate、Semantic Mapping Coverage、Topology（分量对齐）
**需谨慎**：Route Planning / Pipeline Consistency 只验证图论可达性与执行一致性，非物理导航
