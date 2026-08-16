# Tag Map 参考审计 — 对本项目（Khronos → ANYmal D Waypoint）的可用方法

- 仓库：`leggedrobotics/tagmap` @ `ded3eb728f64`（冻结副本，只读）
- 审计范围：semantic map → navigable pose graph → ANYmal navigation 的方法

> 本项目最终对象是 ANYmal D，目标是 Khronos PLACES → ANYmal D waypoint graph。Tag Map 只用于回答"语义目标如何可靠落到可导航位置"，不替换 Khronos，也不作为最终架构。

---

## 1. 一句话结论

Tag Map（CoRL 2024，ETH）展示了一条"**文本标签驱动的语义地图 → 语义目标定位 → 可导航图节点 → 最短路径**"的干净链路。它没有 ANYmal 执行代码、没有 ROS 层、不跑真机导航——因此本项目**只借鉴"semantic target → navigable graph node"的衔接逻辑**，其余（RAM 图像打标、Matterport 离线评估、lattice graph）与 Khronos 系不兼容，不采用。

---

## 2. 关键文件与接口

| 文件:行号 | 内容 | 对本项目 |
|---|---|---|
| `tag_mapping/tag_mapping/tag_map.py:23` | `TagMap`：`add_entry`(:29) / `add_tag`(:40) / `query(tag)`(:79) → 该 tag 关联的带 pose 的 entries | 数据结构思路（tag → 带位姿的观测）可参考，但本项目语义来源是 Khronos DSG，不是图像 tag |
| `tag_mapping/tag_mapping/pose_graph.py:6` | `PoseGraph`：networkx 包装，`closest_node_idx`(:36) / `shortest_path(start,end)`(:57)，节点=地图点、边带长度 | **可直接照抄接口思路**做我们的 waypoint graph 连通性检查 |
| `tag_mapping/tag_mapping/localization/pipeline.py` | `localization_pipeline`：多视点 voxel voting → DBSCAN/HDBSCAN 聚类 → 语义目标 bounding box | 定位方法，本项目语义位置由 Khronos Room/Place 关系给出，不需要 |
| `tag_mapping/tag_mapping/evaluation/lattice_navigation_graph.py` | `LatticeNavigationGraph` / `create_lattice_navigation_graph`：场景 mesh 上建 3D lattice grid → 滤穿墙边 → networkx 最短路 | 仅离线评估用；本项目图源是 Khronos PLACES，不采用 |
| `tag_mapping/tag_mapping/evaluation/lattice_graph_utils.py:8` | `assign_label_box_lattice_graph_nodes`：**语义 bounding box 膨胀 ±1m → 找 box 内/邻近且碰撞自由的导航图节点** | **关键可借鉴逻辑**：语义目标 → 最近可导航节点，与我们的"Room → Place anchor waypoint"同构 |

---

## 3. semantic target 如何变成 navigable pose（他们的做法）

```
扫描帧 + RAM tagger → TagMap(tag → pose)
  → localization 多视点投票 + 聚类 → 语义目标 bounding box
  → assign_label_box_lattice_graph_nodes（box 膨胀 + 碰撞检查）→ 可导航 lattice 图节点
  → PoseGraph/networkx shortest_path → 全局路径
```

核心转换点只有一个：`assign_label_box_lattice_graph_nodes` —— 把"语义目标的空间区域"落到"最近的可导航图节点"，中间带**距离阈值 + 碰撞自由**两个约束。这与本项目 `Room ↔ Place → navigation anchor waypoint` 的映射优先级（doorway/代表 Place → 最近有效 Place）是同构的。

**对本项目可借鉴的规则**：
1. 语义目标必须先落到一个**可导航图节点**（Place），而不是直接取语义中心坐标；
2. 落点必须有**距离阈值**，超阈值 fail closed（Tag Map 用 ±1m 膨胀，我们用 Room↔Place 已有拓扑优先，空间近邻时设最大距离）；
3. 落点要**可解释**（记录 source、distance、mapping_method）。

---

## 4. pose graph 如何构建 / 如何对接 ANYmal navigation

- Tag Map 的 PoseGraph 是"地图点 + 长度加权边"的 networkx 图，只做最短路径，**到 ANYmal 之间的执行层不在仓库里**（论文的实机部分无对应代码）。
- 对接 ANYmal 的分层思路（global graph 与 local planner 分离）是参考价值所在：**global 层只做"语义 → 可达节点 → 全局节点序列"，local 执行交给机器人自己的导航**。这与本项目完全一致：Khronos PLACES 是 global 图，ANYmal D 自己的 graph planner / local planner 是执行层。
- Tag Map 无 ROS、无 office 场景可复用的具体配置。

---

## 5. 判断

| 内容 | 结论 |
|---|---|
| `assign_label_box_lattice_graph_nodes` 的"语义 box → 最近可导航节点" | **借鉴** → 本项目 semantic_waypoint_mapper 的 Room↔Place 映射逻辑 |
| `PoseGraph` 接口（closest_node_idx / shortest_path） | **借鉴** → waypoint_graph 连通性检查 / Mock 规划 |
| RAM 图像打标 + Matterport 离线评估管线 | 不采用（语义来源是 Khronos DSG，不是图像 tag） |
| lattice navigation graph 生成 | 不采用（图源是 Khronos PLACES） |
| ANYmal 执行 | 仓库中不存在，需自研（= 本项目 waypoints.json exporter + ANYmal D 消费） |
