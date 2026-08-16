# Phase 3R — Robot-Navigable Graph Reconstruction（返工）

> **依据 khronos_anymald_phase0_8_audit §14-18/§31 返工**（2026-08-10）
> 替换旧的 3D 欧氏 FPS sparsifier。核心：**graph-aware（geodesic FPS + per-component + witness path）**。

---

## 1. 旧算法的问题（审计确认）

| 问题 | 旧算法表现 |
|---|---|
| topology shortcut | global 3D 欧氏 FPS + 欧氏最近代表 → stretch mean 4.73, max 31.33（实测跨墙 95m vs 直线 3m） |
| 静默合并分量 | source 6 分量 → output 1 分量；甚至创造跨 source component 连接（R=1.5/2.0 实测 ∞ 距离边） |
| z 语义错误 | PLACES z=1.1~3.9 是 volumetric free-space 高度，route 全在 3.9m 高空 |

## 2. 新算法（Phase 3R）

```
每个 source 连通分量内：
  geodesic FPS（图上最短路距离）选代表     ← 取代欧氏
  place 只能归属同分量代表（geodesic 最近）  ← 禁止跨分量
稀疏边：只来自"原图边两端分属不同代表"
  + witness path（dense 图代表间最短路）
  + stretch = witness geodesic / euclidean
不合并 disconnected components
```

**实现**：`waypoint_graph/sparsifier.py::sparsify_graphaware` + `waypoint_graph/stretch_validator.py`

## 3. 结果（Office DSG，R_geo=3.0）

| 指标 | 旧（欧氏 R=3） | 新（geodesic R=3） |
|---|---|---|
| representatives | 83 | 261 |
| sparse edges | 284 | 624 |
| 连通分量 | 1（静默合并） | **6（保持 source）** |
| stretch mean | 4.73 | **1.62** |
| stretch max | 31.33 | **10.96** |
| stretch >5 比例 | 21% | **2.2%** |
| 跨分量 assignment | 有 | **无（禁止）** |
| 覆盖保证 | 欧氏 | **geodesic ≤ R（已验证）** |

## 4. ground projection（审计 §15 方案 B）

- 所有 waypoint z 投影到 mock ground（`--ground-z 0.0`）
- **`z_source: projected_for_mock`** 写入 report，明确不是真机 waypoint z
- route 不再位于 3.9m 高空

## 5. stretch 处理（审计 §18）

- **不静默删除高 stretch 边**（删除会破坏区域连通——实测过滤 >1.5 后 72 个碎片分量、69 孤立代表）
- 高 stretch 边是"空间近(0.4-1.3m)拓扑远(3-6m)"的隔墙/平行结构桥接
- **route cost 已用 witness geodesic 长度**（沿真实自由空间拓扑，不穿墙）
- stretch 分布 + 高风险边 top 列表写入 `generated/sparsified_report.json`
- 提供 `--max-stretch` 可选过滤，供需要"只保留直走边"时使用

## 6. 验收对照（审计 §31）

- [x] 停止 global 3D 欧氏 FPS + 欧氏最近代表
- [x] clustering/assignment 限制在 source connected component 内
- [x] 用 geodesic 距离优先
- [x] 每条 sparse edge 有 dense witness path
- [x] 新增 geodesic stretch validator
- [x] 不静默合并 disconnected components（6 分量保持）
- [x] ground projection + 标记 z_source=projected_for_mock
- [x] 重新生成 waypoints_sparsified / semantic_to_waypoint / benchmark

## 7. 重新生成结果

```
build_sparsified.py → waypoints_sparsified_R3.0.json (261 nodes / 624 edges / ground-z 0)
build_semantic_to_waypoint.py → semantic_to_waypoint.json (22/22 room)
run_benchmark.py → 见 phase8（exported_components=6=source）
E2E → TASK_SUCCESS（route z=0）
recheck_all.py → 全部 PASS
测试 → 28 passed
```
