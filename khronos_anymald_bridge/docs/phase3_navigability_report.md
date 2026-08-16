# Phase 3 — Scene Graph 可导航性检查报告

> **Phase 3 产出**（2026-08-09）
> 数据源：`office_launch_dense/2026_08_03/dsg.json` → `generated/waypoints.json`（全量导出）
> 对比基准：用户提供的 `inputs/waypoints_sample.json`（46 nodes / 110 directed edges）
> 分析脚本：`dsg_reader/analyze_waypoint_graph.py`；`waypoint_graph/sparsifier.py`

---

## 1. 结论（先说）

> **Khronos PLACES 直接导出不能作为 ANYmal waypoint graph——密度约为样本的 50 倍，需要 sparsification / node merging。**
> 但图本身**无靠墙/穿墙问题**（0 条可疑边），GVD 拓扑质量良好，问题纯粹是**节点冗余密度**。
> 满足计划文档 §17"实际结果证明需要过滤"的条件，已实现 sparsifier（参数待 ANYmal 接口确认）。

---

## 2. 全量导出 vs ANYmal 样本

| 指标 | ANYmal 样本 | Khronos 全量导出 | 差异 |
|---|---|---|---|
| nodes | 46 | 2472 | — |
| edges | 110 (directed) | 17666 | — |
| 覆盖面积 (x/y) | 1327.7 m² | 1432.4 m² | 同量级 |
| **节点密度** | **0.0346 /m²** | **1.7257 /m²** | **≈ 50×** |
| edge 长度 mean | 3.75 m | 1.42 m | 过短 |
| edge < 1.0m 比例 | 0% | 24.4% | 大量短边 |
| edge < 2.0m 比例 | 9.1% | 84.8% | 绝大部分过短 |
| 最近邻 < 0.5m 的节点 | 2 (4%) | 943 (38%) | 严重冗余 |
| 最近邻 < 1.0m 的节点 | 2 (4%) | **2396 (97%)** | 全部过密 |
| z 范围 | -0.05 ~ 2.03 | 1.1 ~ 3.9 | 见 §5 |

**判断**：覆盖面积相近（约 1400 m²），但 Khronos GVD 产生了 50 倍于 ANYmal 期望的节点密度。97% 的节点与最近邻 < 1m，这样的图对 ANYmal waypoint planner 没有意义（密集冗余节点不会增加可达性信息）。

---

## 3. 重合节点（node merging 需求）

- 2472 节点中仅 **2455 个唯一位置**：**17 对节点完全重合**（position 相同）
- 最小节点间距 = **0.0 m**
- 这些重合节点应被合并（sparsifier 的 FPS 覆盖天然处理：重合节点属于同一代表）

---

## 4. 靠墙 / 穿墙检查（利用 Khronos GVD distance）

| 项 | 值 |
|---|---|
| place 到最近障碍 distance < 0.3m | 0 |
| place 到最近障碍 distance < 0.1m | 0 |
| 可疑边（两端 distance<0.3 且边>1.5m） | **0** |

**结论**：Khronos GVD PLACES 边沿自由空间自由中轴走，无靠墙/穿墙边。导出后 edge weight 即欧氏距离，语义为"waypoint 间直线可达"，可信。

---

## 5. z 轴（继承 Phase 1 硬阻塞项）

全量导出 z ∈ [1.1, 3.9]，ANYmal 样本 z ∈ [-0.05, 2.03]。两者都是环境绝对坐标（非地面投影），z 高度语义一致性待 ANYmal 接口/真机验证（Phase 9/10）。sparsification 不影响 z（保留代表节点原 z）。

---

## 6. Sparsification 方案（已实现，参数待确认）

**方法**（`waypoint_graph/sparsifier.py`）：
1. **最远点采样（FPS）**：选代表节点，保证每个 place 到最近代表 ≤ R（覆盖半径）
2. **quotient 图**：原图边若连接不同代表，则在代表间建边 → **保持连通分量**
3. 代表节点保留原 Place ID / position / orientation

**候选分析**（全量 2472 → 各 R）：

| R (m) | nodes | edges | 密度 /m² | 密度 vs 样本 | edge mean | valid | 连通分量 |
|---|---|---|---|---|---|---|---|
| 1.5 | 352 | 2978 | 0.246 | 7.1× | 2.56 m | ✓ | 1 |
| 2.0 | 197 | 1564 | 0.138 | 4.0× | 3.19 m | ✓ | 1 |
| **3.0** | **83** | **568** | **0.058** | **1.7×** | **4.33 m** | ✓ | **1** |
| 4.0 | 41 | 192 | 0.029 | 0.8× | 5.54 m | ✓ | 1 |

- 全部候选 valid（validator 通过）、连通分量为 1（原 5 个碎片分量被代表覆盖吸收，导航上避免不可达碎片，且不产生穿墙边）
- **推荐 R=3.0**：密度 1.7× 样本、edge mean 4.33m 最接近样本 3.75m 量级
- 候选文件：`generated/waypoints_sparsified_R{1.5,2.0,3.0,4.0}.json`

> **重要**：R（目标节点间距/密度）是 ANYmal 接口相关参数，**此处为候选分析，不拍脑袋定死**。最终 R 需 ANYmal D 真机/仿真接口确认后选定。计划文档 §19 也要求 edge cost 语义（traversability 是否参与全局 cost）待确认，当前不改动。

---

## 7. 对后续阶段的输入

1. **Phase 4（semantic_to_waypoint）**：用 sparsified waypoint 图（推荐 R=3.0 或待确认参数）生成 `semantic_to_waypoint.json`；Room→waypoint 映射应落到 sparsified 后的 waypoint（代表节点），而非原始 2472 place。
2. **Phase 5（Mock planner）**：Dijkstra 在 sparsified 图上跑，edge cost = 欧氏距离。
3. 全量导出仍保留（`generated/waypoints.json`）作为未过滤基准；sparsified 作为可导航候选。

---

## 8. 验收对照（计划文档 §17）

- [x] 直接导出全部 PLACES（未预先 sparsify）
- [x] 检查是否过密 → **是（50×），已量化**
- [x] 检查非常短边 → **是（84.8% 边 < 2m）**
- [x] 检查无意义 Place → **17 对重合节点；碎片分量被吸收**
- [x] 检查靠墙/穿墙边 → **无（0 可疑边）**
- [x] 证明需要后实现 filtering/sparsification → **FPS + quotient 已实现并验证**
