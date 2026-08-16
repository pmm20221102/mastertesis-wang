# DSG PLACES Schema Report — 当前 Office Khronos DSG / PLACES 审计

> **Phase 1 产出**（2026-08-09）
> 依据：真实 Office 数据集 Khronos 输出 + 实际代码统计（`dsg_reader/analyze_places.py`）
> 目的：为 Khronos PLACES → ANYmal D waypoint graph exporter 提供事实依据，回答"哪些字段可直接写入 ANYmal node，哪些需转换，哪些缺失"。
> 本文档**只读分析，未修改任何 Khronos 数据**。

---

## 0. 数据来源

| 项 | 值 |
|---|---|
| DSG 文件 | `scene-graph-khronos/snapshots/office_launch_dense/2026_08_03-16_46_38/dsg.json`（517MB） |
| 对照 | `scene-graph-khronos/snapshots/office_nogt/2026_08_05-14_38_56/dsg.json`（257MB） |
| 格式 | SPARK-DSG json 序列化（`SPARK_DSG_header`，version 1.1.3） |
| frame 声明 | `hydra_config.yaml`: `map_frame: map`, `odom_frame: odom`, `robot_frame: base_link_gt` |
| 环境 | Office 数据集（label_names 含 office 类别；`tesse_cd_office.yaml` 标签重映射） |
| RAG 主线 | `outputs/office_dense/`（canonical_graph_v2 + vector_db 完整流程，对应本 DSG） |

> 注：RAG `config.yaml` 引用的 `2026_06_20-18_30_13/dsg.json` 已不存在（过时配置）。当前实际 Office DSG 即上表 `office_launch_dense`。

### 0.1 对照实验（office_nogt，保证结论不依赖单一 DSG）

| 项 | office_dense（主线） | office_nogt（对照） |
|---|---|---|
| PLACES 节点 | 2472 | 2483 |
| PLACES 层内边 | 8833 | 8822 |
| Room 数 / 有 Place 的 Room | 22 / 22 | 15 / 15 |
| MESH_PLACES | 0 | 0 |
| orientation | 全 identity | 全 identity |
| z 范围 | 1.1~3.9 | 1.1~3.9 |
| 连通分量 | 6（主 2455） | 10（主 2439） |
| self-loop / duplicate | 0 / 0 | 0 / 0 |
| weight mean | 0.977 | 0.980 |

**结论：两个 Office DSG 结构高度一致，本文档所有结论（字段映射、无向→有向、z 高度、orientation identity）对两者均成立。**

---

## 1. 概览（层统计）

| Layer | partition | 节点数 | 说明 |
|---|---|---|---|
| PLACES | 3 / 0 | **2472** | GVD 拓扑自由空间节点（本项目 waypoint 来源） |
| MESH_PLACES | 3 / 1 | **0** | surface places —— **该数据集未生成**，本项目不使用 |
| ROOMS | 4 / 0 | 22 | 房间层 |
| BUILDINGS | 5 / 0 | 1 | 建筑层 |
| OBJECTS | 2 / 0 | 209 | 物体层 |
| AGENTS | 2 / 97 | 392 | agent 轨迹层（非导航图节点） |
| **合计** | | **3096** | 边总数 12335（`directed=false`，无向） |

---

## 2. PLACES 节点审计（layer 3, partition 0）

### 2.1 ID
- 编码：NodeSymbol = 高 8 位层字符（`P`）+ 低 56 位 index（`node_symbol.h:50-103`）
- 示例：id `8070450532247928852` → `P20`；`...926990` → `P368`
- **index 分布**：min=20, max=28765，**稀疏不连续**（2472 个节点占 index 区间 28746）。实验过程中累计创建过 ~28765 个 place，最终保留 2472。
- 意义：**可以直接用 Place index 作为 ANYmal waypoint id**（P368 → waypoint 368），符合计划文档 §15.1"尽可能保留 Place ID"。

### 2.2 position（可直接使用）
- 结构：`attributes.position = [x, y, z]`（float32 数组）
- 有效性：**2472/2472 有效**（有限值），100% 覆盖率
- 范围：x ∈ [-19.7, 21.7]，y ∈ [3.3, 37.9]，z ∈ **[1.1, 3.9]**
- 注意：**z 非地面高度**。ANYmal waypoint 样本 z=0.03。Khronos office 的 z 是环境绝对高度（GVD 节点位于自由空间）。→ 见 §5 frame 审计。

### 2.3 orientation（存在但全为 identity）
- 结构：`attributes.orientation = {w, x, y, z}`（四元数字典）
- **2472/2472 全部 = {w:1, x:0, y:0, z:0}**（identity）
- 结论：符合计划文档 §15.2 **情况 B**——Khronos Place orientation 无导航意义。MVP 直接导出 identity，标注 `orientation_source: fallback_identity`。

### 2.4 其他 attributes（对 waypoint 无直接影响，记录备查）
| 字段 | 值 | 意义 |
|---|---|---|
| `distance` | 0.2~4.3 | GVD 到最近障碍的距离（可作 traversability 参考，**Phase 2 不自动用它改 traversability**） |
| `bounding_box` | INVALID（dimensions 全 0） | place 无实际 bbox |
| `semantic_label` | 4294967295（0xFFFFFFFF） | **无效/无标签**——Place 本身无语义，语义来自 Room↔Place 与 OBJECT 层 |
| `name` | ""（空） | 无名称 |
| `metadata` | {} | 空 |
| `real_place` | true | 有效 place 标记 |

---

## 3. 边审计

| 项 | 值 |
|---|---|
| 总边 | 12335 |
| **directed** | **false（无向）** |
| PLACES 层内边（3,0→3,0） | **8833** |
| Place↔Room 边（跨层） | 2455 |
| 其他（含 OBJECT/AGENT 相关） | 1047 |
| self-loop | 0 |
| duplicate directed | 0 |
| weight 范围 | [0.2, 4.294]，mean 0.977 |
| weight 直方图 | 1.0-3.0: 7181，0.1-0.5: 2762，0.5-1.0: 2289，3.0-10: 103 |

- 边结构：`{source: <64bit NodeId>, target: <64bit NodeId>, info: {type: "EdgeAttributes", weight: float, weighted: true, metadata: {}}}`
- **weight 是欧氏距离**（与 position 校验一致，mean≈0.98m 符合 GVD 节点间距）
- **关键**：`directed=false` → ANYmal waypoint 样本需要 **directed edges**（A→B 与 B→A 都需存在）。exporter 必须把每条无向 PLACES 边**展开为两条有向边**（8833 → 17666 条）。

### 3.1 PLACES 连通性
| 项 | 值 |
|---|---|
| 连通分量数 | **6** |
| 最大分量 | 2455（覆盖 99.3% 的 place） |
| 其余 5 个分量 | 5, 3, 3, 3, 3（共 17 节点，孤立碎片区） |
| 完全孤立节点 | 0 |

- 主分量连通性良好；5 个小分量是分离的碎片区域，exporter **保留全部**（不丢弃），validator 会标记这些分离区域（ANYmal planner 无法在分量间导航）。

---

## 4. Room ↔ Place 关系（semantic 锚点）

- 22 个房间**全部**有 Place（place_room 边 2455 条，覆盖 100%）
- 每个房间的 Place 数（前 8）：

| Room | Place 数 | | Room | Place 数 |
|---|---|---|---|---|
| R12 | 620 | | R4 | 112 |
| R14 | 387 | | R17 | 68 |
| R6 | 215 | | R16 | 32 |
| R9 | 215 | | R7 | 29 |
| R0 | 207 | | R15 | 170 |

- 一个房间有大量 place（如 R12=620）→ **Phase 4 semantic_waypoint_mapper 必须从房间选一个 navigation anchor waypoint**，不能直接给 620 个候选。
- 无 Place 的房间：0（全部有）

---

## 5. Frame 审计（Phase 1 硬阻塞项）

**结论：frame 声明一致（map），但 z 轴高度语义需要验证。**

| 检查项 | Khronos | ANYmal waypoint 样本 | 一致？ |
|---|---|---|---|
| frame 声明 | `map_frame: map`（hydra_config） | `header.frame: map` | ✅ 一致 |
| x/y 坐标 | 环境绝对坐标 | 环境绝对坐标 | ✅ 同类 |
| **z 坐标** | **1.1 ~ 3.9**（自由空间高度） | **0.03**（近地面） | ⚠️ **不一致** |

- **frame=map 一致**：Khronos 声明且（运行时）发布在 `map` 帧；ANYmal waypoint 也要求 map 帧。
- **z 差异**：Khronos PLACES z 是自由空间高度（GVD 节点在可通行空间内，非地面投影）；ANYmal 样本 z≈0。直接导出会导致 waypoint 全部悬空 1-4m。
- **处理选项（Phase 2 决策，不静默假设）**：
  1. z 保持原值导出，标注 `unknown`，交由 ANYmal 侧判断是否忽略 z（many legged nav 栈只看 x/y）；
  2. z 归一化为固定地面高度（如 0）——**但需 ANYmal 接口验证**；
  3. 若 ANYmal loader 明确忽略 z 则无影响。
- **真实验证**：frame 的最终一致性要在仿真/真机时用同一 map 坐标验证（Phase 9/10）。Phase 1 只能确认声明值。

---

## 6. Khronos PLACES → ANYmal waypoint node/edge 字段映射表

### 6.1 Node 映射

| ANYmal waypoint node 字段 | 来源 | 直接/转换 | 说明 |
|---|---|---|---|
| `id` | Khronos Place index（`P368`→`368`） | ✅ 直接 | 保留 Place ID，符合 §15.1 |
| `pose_stamped.header.frame` | `map`（常量） | ✅ 直接 | 与 Khronos map_frame 一致 |
| `pose_stamped.header.stamp` | `0`（默认） | ⚠️ 需决定 | 样本 stamp 有值；离线导出可置 0，标注 |
| `pose_stamped.position` | `attributes.position [x,y,z]` | ✅ 直接（z 见 §5） | 2472/2472 有效 |
| `pose_stamped.orientation` | `attributes.orientation` | ✅ 直接 | 全 identity，标注 fallback |
| `pose_stamped.tolerance` | —（ANYmal 私有） | ❌ 缺失 | 默认 `[0,0]`，配置化 |

### 6.2 Edge 映射

| ANYmal waypoint edge 字段 | 来源 | 直接/转换 | 说明 |
|---|---|---|---|
| `id` | 新分配 | ⚠️ 生成 | 唯一即可 |
| `start_node_id` | Place index（source 解码） | ✅ 直接 | |
| `end_node_id` | Place index（target 解码） | ✅ 直接 | |
| `data.local_planner` | —（ANYmal 私有） | ❌ 缺失 | 默认 `mode_name=default, planner_name=default, planner_parameters=""` |
| `data.locomotion_controller` | — | ❌ 缺失 | 默认 `motion_parameters=default, motion_state=default` |
| `data.max_deviation` | — | ❌ 缺失 | 默认 `3` |
| `data.traversability` | — | ❌ 缺失 | 默认 `1` |
| `data.perception_parameters.nfm_preset` | — | ❌ 缺失 | 默认 `"autodeduce"` |
| `data.version` | — | ❌ 缺失 | 默认 `1` |

> 所有 ANYmal 私有缺失字段 → 集中在 `config/default_anymal_edge_policy.yaml`（计划文档 §15.4），不散落硬编码。

### 6.3 无向→有向展开
- Khronos `directed=false`，8833 条 PLACES 内边
- ANYmal 样本为 directed edges → exporter **展开为 17666 条**（A→B 和 B→A）
- 需保证：edge id 唯一、start/end node 存在、无 self-loop（已确认 0）、无重复

---

## 7. 字段分类汇总

| 类别 | 字段 | 处理 |
|---|---|---|
| **可直接写进 ANYmal node** | `id`（Place index）、`frame`（map）、`position`（x/y/z）、`orientation`（identity） | 直接导出 |
| **需要转换** | 无向边 → 双向有向边；edge.id 新生成；header.stamp 决定 | exporter 逻辑 |
| **ANYmal 要求但 Khronos 缺失** | `tolerance`、`local_planner`、`locomotion_controller`、`max_deviation`、`traversability`、`nfm_preset`、`version` | 配置化默认值（default_anymal_edge_policy.yaml） |
| **待验证** | z 轴高度语义（1.1~3.9 vs 0.03） | Phase 9/10 真机/仿真验证；MVP 标注 unknown |
| **不用于自动推断** | `distance`（GVD 障碍距离） | 不自动改 traversability；不自动猜 perceptive_walk |

---

## 8. 对 Phase 2 exporter 的直接输入

1. **节点**：遍历 2472 个 PLACES，`id=index`，`position=attributes.position`，`orientation=attributes.orientation`（identity），`frame=map`。
2. **边**：遍历 8833 条 PLACES 内边，解码 source/target index，展开双向，填默认 edge policy。
3. **校验**：node id 唯一（index 唯一已确认）、edge id 唯一、端点存在、无自环（0）、无重复、连通分量标记（主分量 2455 + 5 个碎片）。
4. **不做**：sparsification（§17）、不自动改 traversability、不猜 perceptive_walk、不丢弃小分量（只标记）。
