# REFERENCE_METHOD_SUMMARY — 参考项目方法总结（Khronos → ANYmal D Waypoint）

> **Phase 0 最终报告**（2026-08-09）
> 依据离线审计：`reference_omniplanner.md` / `reference_dcist.md` / `reference_tagmap.md` / `reference_viplanner.md` / `reference_legged_artplanner_navsuite.md`
> 参考仓库冻结于 `external/navigation_references/`（REFERENCE_LOCK.tsv，只读）
>
> **最终对象固定：ANYmal D。** 不设计通用接口，不输出 nav_msgs/Path 作为目标格式。所有借鉴都服务于一件事：**把 Khronos DSG/PLACES 可靠地转换为 ANYmal D waypoint graph，并让语义目标能触发 ANYmal D 原生 waypoint 规划。**

---

## 1. 是否找到现成的 scene graph → ANYmal D waypoint converter？

**没有找到。**

- 没有公开项目直接实现 `Khronos/Spark-DSG → ANYmal D waypoints.json`。
- 最接近的是 Omniplanner 的 `DSG → GotoPointsPlan → nav_msgs/Path`（Spot/Phoenix 专用，输出不是 ANYmal waypoint graph）和 ViPlanner 的 `waypoint → pathFollower → Twist`（ANYmal 后半段，但**不产生也不消费 waypoints.json**）。
- 结论：**Khronos PLACES → ANYmal D waypoint graph 的 exporter 必须自己实现**，这正是本项目的核心开发。

---

## 2. 方法总结表（三列）

| Reference | Useful method | How we use it in Khronos → ANYmal D |
|---|---|---|
| **Omniplanner** | DSG node lookup：`str_to_ns_value(symbol)` → `dsg.find_node().attributes.position`（`goto_points.py:46-61`） | 读 Khronos PLACES 节点位置的标准姿势；dsg_reader 用它取每个 Place 的 position |
| **Omniplanner** | `LayerPlanner`（`tsp.py:17`）：把 DSG MESH_PLACES 层转 networkx 图，`get_shortest_path/get_closest_node_id` | 验证导出的 waypoint graph 连通性；Phase 5 Mock planner 的 Dijkstra 可直接复用其图构建思路 |
| **Omniplanner** | 拓扑提取：`explicit_edges_from_layer` / `generate_place_containment`（`dsg_pddl_grounding.py`） | 从真实 DSG 提取 Place 连通边与 Room↔Place 从属关系，喂给 waypoint exporter |
| **Omniplanner** | `node.attributes.position` 直接当 map 系使用、frame 假设 | **反面教材**：它赌 DSG 已在 map。我们必须在 Phase 1 实测 Office DSG 的 frame，不静默假设 |
| **Omniplanner** | `compile_points_path`（GotoPointsPlan→Path 插值模板） | 仅作"点序列 → 导航坐标序列"的插值参考；本项目输出目标是 ANYmal waypoint graph，不是 Path |
| **Awesome-DCIST-T4** | prior DSG：`DynamicSceneGraph.load(json)` + `DsgPublisher` latching 发布（`prior_dsg_publisher_node.py:27,44`） | 用官方序列化加载 Khronos 的 `dsg.json`；确认 DSG 在 spark_dsg 生态中的 json 格式互通 |
| **Awesome-DCIST-T4** | 语义 goal = DSG node id 字符串，`find_node` 解坐标 | 本项目 `semantic_to_waypoint.json` 的 `target_ref → waypoint_id` 直接采用"节点 id 字符串"约定 |
| **Awesome-DCIST-T4** | `use_fake_*` 仿真开关 + `mid_level_planner_type: identity`（`spot_executor_node.yaml`） | 无真机验证方式：mock_anymald 用同一"真机/仿真开关"模式，验证 waypoint graph 可被消费 |
| **Tag Map** | `assign_label_box_lattice_graph_nodes`：语义 box → 最近可导航节点（碰撞检查 + 距离阈值） | semantic_waypoint_mapper：Room → Place anchor waypoint 的映射规则（doorway > 代表 Place > 最近有效 Place，超阈值 fail closed） |
| **Tag Map** | `PoseGraph.shortest_path`（networkx 包装） | waypoint_graph 连通性/规划的轻量图操作参考 |
| **ViPlanner** | ANYmal 导航后半段：waypoint goal → local path → pathFollower → `TwistStamped` | **理解消费端**：ANYmal D 在 waypoint graph 之后如何走到运动控制；frame 约定（map→odom→base）与 goal reached 语义供 waypoint 语义设计参考 |
| **ViPlanner** | 关键事实：其 ROS 链路**不读任何文件**，`waypoints.json` 是 ANYmal 私有 global planner 产物 | 佐证 waypoints.json 与 ViPlanner 无耦合；waypoint graph 的正确消费端是 ANYmal D 自己的 navigation/mission 接口（Phase 10 真机确认） |
| **art_planner / nav-suite / anymal_d_simple_description** | art_planner：`PoseStamped goal → nav_msgs/Path` 接口、learned motion cost；nav-suite：IsaacLab ANYmal-D 仿真接口；anymal_d_simple_description：URDF 描述 | 仅 Phase 9 物理仿真与 ANYmal D 模型可视化时参考；不是当前 waypoint 主线的依赖 |

---

## 3. 必须由我们实现的（核心）

1. **dsg_reader** — 读真实 Khronos Office DSG，提取 PLACES 层（node ID / position / orientation / frame / connectivity / Room↔Place）。
2. **waypoint_exporter** — Khronos PLACES → ANYmal D waypoint graph（nodes + edges + 默认 edge policy），保持 Place ID = waypoint ID。
3. **waypoint_graph validator** — 严格校验 JSON 结构、frame、id 唯一、边端点存在、拓扑一致性、连通性、无自环/重复边。
4. **semantic_waypoint_mapper** — `target_ref → waypoint_id`，映射可解释、fail closed。
5. **mock_anymald** — 无真机验证 waypoint graph 可加载、可规划、goal 可达（Dijkstra/A*，edge cost = Euclidean）。
6. **接已有 LLM+RAG** — 只消费 `target_ref`，不重做上层。

---

## 4. 底线

- 参考项目只回答"别人怎么从场景图/语义地图得到可执行导航信息"；
- 它们**不改变本项目最终接口 = ANYmal D waypoint graph**；
- 四个参考仓库全程只读，禁止 `git pull`，commit 已锁定在 REFERENCE_LOCK.tsv。
