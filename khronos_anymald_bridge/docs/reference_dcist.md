# Awesome-DCIST-T4 离线审计报告：Hydra/DSG → Omniplanner → robot executor → Spot

审计对象：`MIT-SPARK/Awesome-DCIST-T4` @ `91b01e3759af`（工作站冻结副本，只读）
配套副本：`Omniplanner` @ `db0c8326`（REFERENCE_LOCK.tsv 冻结）、`Spark-DSG` @ `3c40997`
关键 submodule gitlink（内容未 checkout，仅供版本追溯）：`spot_tools=ce06355b`、`omniplanner=732732b6`、`hydra_ros=2f457dfb`、`spark_dsg=fc18f379`、`dsg_updater=d657d6a7`

> 重要前提：本仓库（Awesome-DCIST-T4）是"部署编排 + 配置 + launch"层，实际算法代码在独立仓库：
> **Omniplanner**（`omniplanner`/`omniplanner_ros`/`omniplanner_msgs`）和 **spot_tools**（`spot_tools_ros` + `robot_executor_interface` + `robot_executor_msgs`）均为 submodule，冻结时内容为空。
> 本审计基于：Omniplanner 完整冻结副本（可读全部源码）+ Awesome-DCIST-T4 的 launch/配置（完整）+ Spark-DSG 序列化代码（完整）。
> **spot_tools 的 executor 实现源码不在本地**，第 4 问给出的是从配置、launch remap 与调用方代码反推的接口级证据，无法给出 SDK 调用行号（见下）。

---

## 0. 顶层结论（先说）

这是一套**严格分层的"world model → planner → robot-specific executor"架构**，层间以 **ROS2 topic** 解耦，没有服务调用，没有代码级耦合：

```
[DSG 提供方] → topic: /<robot>/hydra/backend/dsg (spark_dsg DynamicSceneGraph msg)
[Omniplanner] → topic: /<robot>/omniplanner_node/compiled_plan_out (ActionSequenceMsg)
[spot_executor] → bosdyn SDK → Spot
```

**对 Khronos→ANYmal bridge 最有价值的借鉴**：Omniplanner 的 `RobotPlanningAdaptor` 多态 + `compiled_plan_out` 消息边界 + `ActionSequence/Follow` 抽象 action 层。ANYmal 不需要 PDDL/多机那一堆，但"planner 输出抽象 action 序列、robot adaptor 负责翻译成 SDK 调用"这个边界是干净且直接可复制的。

---

## 1. Hydra DSG 如何进入 Omniplanner

**在线模式（真机建图）**：
- Hydra 节点 `hydra_ros_node`（pkg `hydra_ros`）由 `master.launch.yaml` 启动，backend 把 DSG 发布到 topic `/<robot>/hydra/backend/dsg`。
- Omniplanner 订阅同一 topic，代码在 `omniplanner_node.py:12`（`from hydra_ros import DsgSubscriber`）和 `omniplanner_node.py:204`（`DsgSubscriber(self, "~/dsg_in", self.dsg_callback)`），launch 中 remap 到 `hydra/backend/dsg`（`master.launch.yaml:326`）。
- **格式**：DSG 在 ROS 中不是 json，而是 `spark_dsg` 的 `DynamicSceneGraph` 对象（C++ pybind 绑定，`DsgSubscriber` 回调签名 `dsg_callback(header, dsg)`，见 `omniplanner_node.py:261`）。`DsgSubscriber`/`DsgPublisher` 类定义在 Hydra-ROS 仓库（`hydra_ros` submodule，本地不可得）；回调里拿到的就是可调 `find_node/get_layer` 的活 DSG 对象。
- 谁"转"：没人转。Hydra backend C++ 持有 DSG，经 DsgPublisher 序列化为 ROS msg 发出；Omniplanner 经 DsgSubscriber 反序列化回 Python 绑定的同一对象。json 只出现在磁盘持久化环节（见第 2 问）。

**离线/prior 模式**：不启动 Hydra（`launch_config: [main, prior_dsg, planning]`，`experiment_manifest.yaml`），prior DSG 发布器直接把 json 读回发布到**同一个 topic**（见第 2 问），Omniplanner 侧零改动。这是"在线感知图 / 离线先验图可互换"的关键设计。

---

## 2. prior DSG 如何使用

**读取与发布**（`dcist_launch_system/src/dcist_launch_system/prior_dsg_publisher_node.py`）：
- 从 json 文件加载：`self.G = spark_dsg.DynamicSceneGraph.load(self.prior_dsg_fn)`（第 27 行），路径由 `prior_dsg_publisher.yaml` 配置为 `$(env ADT4_PRIOR_MAP)/hydra/backend/dsg.json`。
- 通过 `DsgPublisher(self, "~/dsg_out", True)` 发布（第 44 行，`True` = latching / TRANSIENT_LOCAL），**每 3 秒**重发一次，`frame_id="map"`（第 48-49 行）。launch remap `~/dsg_out → hydra/backend/dsg`（`master.launch.yaml:341`）。
- `run_autoabstr: false`：自动抽象（autoabstr）未启用，prior DSG 直接用。

**json 的出处**：`dsg_saver_node.py` 提供 `SaveDsg` service（`~/save_dsg`），回调里 `self.dsg.save(full_filepath, req.include_mesh)`（第 68 行）——即 DSG 从 topic 收下、存为 spark_dsg 序列化 json（`spark_dsg::io::json::writeGraph`，见 `Spark-DSG/include/spark_dsg/serialization/graph_json_serialization.h:32`）。json 顶层结构：`{"directed","edges","nodes","layer_ids","mesh_edges","multigraph",...}`，节点 id 是 64 位 `NodeSymbol` 编码，节点带 `attributes.position/bounding_box/color` 等（实测 `Spark-DSG/python/tests/resources/apartment_dsg.json`）。**注意**：`save(path, include_mesh)` 里 mesh 默认不带；prior 流程另起 `mesh_publisher_node`（`master.launch.yaml:353`，`~/mesh → hydra/backend/dsg_mesh`）单独发 mesh。

**semantic target 如何变成机器人 goal**（Omniplanner 内）：
- goal 里的 symbol 是 **DSG 节点 id 字符串**（如 `"p123"`、`"O456"`；`p`=MESH_PLACES 层，`O`=OBJECTS 层）。`GotoPointsDomain` 的 grounding 直接查图：
  - `goto_points.py:55-58`：`node = dsg.find_node(str_to_ns_value(symbol))` → `node.attributes.position[:2]` 取 2D 位置。goal 的 `point_names_to_visit: [str]`（`GotoPointsGoalMsg.msg`）就是这样被逐条解析成 `np.ndarray` 位置数组。
  - 机器人起点：`robot_states[goal.robot_id]`（`goto_points.py:47`），由 `RobotPlanningAdaptor.get_pose` 经 tf 提供（见第 6 问）。
- PDDL 路径（`dsg_pddl_grounding.py`）：`PddlGoalMsg.pddl_goal` 是 **PDDL 文本字符串**。`extract_symbols_of_interest`（`dsg_pddl_grounding.py:231-241`）从 goal 里正则提取 `visited-place/at-place/visited-object/at-object` 谓词的 symbol；`add_symbol_positions`（:251）把每个 symbol 用 `spark_dsg.NodeSymbol(char, int)` 构造后 `G.get_node(ns).attributes.position[:2]` 取位置；`generate_*_connectivity` 用 `LayerPlanner`（把 DSG MESH_PLACES 层转 networkx 图，见 `omniplanner/src/omniplanner/tsp.py:20-33`）算连通性和距离，生成 PDDL `connected/distance/object-in-place/place-in-region` 初始事实。
- 语义标签：`DsgContextProvider.__getitem__`（`omniplanner.py`，约 :177-189）在 compile 阶段用 `dsg.get_labelspace(layer, partition).get_node_category(node)` 查语义类别，供 `Pick.object_class` 等使用（`pddl_planner_ros.py:169-176`）。

**prior 的两种变体**（`experiment_manifest.yaml`）：
- `plan_prior = [main, prior_dsg, planning]`：`prior_dsg_publisher` 从 json 发布（tmux `spot_prior_dsg-prior_dsg.yaml`）。
- `plan_prior_heracles = [main, heracles_prior_dsg, planning]`：改用 `heracles_publisher_node`（pkg `heracles_ros`）从 **Neo4j 图数据库**（`heracles_publisher_node.yaml`：heracles_ip/port/neo4j 账号）读 scene graph 发布成 DSG，另配 `heracles_state_updater_node` 与 `dsg_updater/examples/update_robot_holding_object_state.py`（更新机器人 holding 状态）、`chatdsg.py`（LLM 对话入口，base station 上跑）。这是"先验图 + 数据库版本管理"的扩展，对本项目可仅作了解。

---

## 3. robot executor 的边界在哪里

- **负责把 plan 变 robot command 的包**：`spot_tools`（`robustrobotics/spot_tools` @ `ce06355b`）里的 **`spot_tools_ros`**，executable **`spot_executor_node`**（`master.launch.yaml:202`）。同仓库还有 `robot_executor_interface`（Python dataclass 抽象 action 定义）和 `robot_executor_msgs`（ROS msg）。三者本地均无源码。
- **边界就是一条 topic**：`/<robot>/omniplanner_node/compiled_plan_out`。launch remap 证据：`master.launch.yaml:210` — `~/action_sequence_subscriber → omniplanner_node/compiled_plan_out`。也就是说 spot_executor 的唯一 plan 输入是订阅这个 topic；Omniplanner 唯一输出也是发布它（`omniplanner_node.py:99`）。**没有 service、没有 action server、没有代码依赖。**
- executor 其他输入/输出（`master.launch.yaml:198-212` 附近）：
  - 订阅 `~/occupancy_grid → hydra/tsdf/occupancy`（Hydra 的 TSDF occupancy grid，用于局部避障）
  - 订阅 `~/joint_states`（URDF 状态发布）
  - 发布 `~/node_status → ros_system_monitor`（心跳监控）
  - 订阅 `~/cmd_vel`（teleop 键盘 remap：`teleop_twist_keyboard ... -r /cmd_vel:=/$ADT4_ROBOT_NAME/spot_executor_node/cmd_vel`，`spot_sim.yaml`）
  - 接收 `pause` / `resume`（Bool topic，`goal_manager_node.py:14-15` remap 到 `/<robot>/spot_executor_node/pause|resume`）——goal_manager 用它们在做"是否重规划"决策时暂停/恢复执行。

---

## 4. 真实 robot command 如何从 plan 产生（Spot SDK 调用）

**诚实声明**：`spot_executor_node` 的源码在 `spot_tools` 仓库，冻结副本未包含，无法给出 SDK 调用"文件:行号"。以下是从冻结副本中**接口级可验证**的证据：

1. **连接配置**（`config_generation/base_params/spot_executor_node.yaml`，prior_dsg 变体 `config/prior_dsg/spot_executor_node.yaml` 同源）：
   ```yaml
   odom_frame: $(var robot_name)/odom
   body_frame: $(var robot_name)/body        # 真实变体用 /base_link
   spot_ip: $(env ADT4_BOSDYN_IP)
   bosdyn_client_username: $(env ADT4_BOSDYN_USERNAME)
   bosdyn_client_password: $(env ADT4_BOSDYN_PASSWORD)
   ```
   即 executor 用 **bosdyn Python SDK** 直接连 Spot（IP + 账号密码），这是 Spot 标准接口（`bosdyn.client.Robot` 一类）。这与 `spot_sensor_node.yaml` / `spot_twist_node.yaml`（同为 `spot_tools_ros`）一致——三者共用 `robot: {ip, username, password}` 或平铺的 bosdyn 三件套。
2. **真实 vs 仿真开关**（`diff base_params vs config/prior_dsg/spot_executor_node.yaml`）：
   - 真实：`use_fake_spot_interface: false`、`use_fake_spot_pose: false`、`mid_level_planner_type: astar`、`body_frame: base_link`
   - prior_dsg 仿真：`use_fake_spot_interface: true`、`use_fake_spot_pose: true`、`mid_level_planner_type: identity`、`body_frame: body`
   说明 executor 内有一层 "Spot interface" 抽象：真机实现走 SDK，仿真实现是 fake（`use_fake_spot_pose` 用固定 xyz+yaw 假位姿）。
3. **executor 内部行为（由参数推断）**：`mid_level_planner_type` = astar（用 `~/occupancy_grid` 做 2D A* 局部规划）或 identity（直接用 plan 路径）；`occupancy_inflation_radius`（障碍膨胀）、`use_cost_map` + `cost_map_safe_distance`/`cost_map_nearest_obstacle_cost`（cost map 避障）、`lookahead_distance`、`follower_lookahead`（路径跟踪前视）、`goal_tolerance`（到达判定）、`detector_model_path`（YOLO 权重，识别物体做抓取/放置定位）。
4. **抽象 action 与 SDK 的对应**（来自调用方，`robot_executor_interface.action_descriptions` 被 Omniplanner import）：`Follow(frame, path2d)` → 移动命令（SDK 的 SE2 轨迹/速度命令）；`Gaze(frame, robot_point, gaze_point, stow_after, object_id)` → 机身/云台对准；`Pick`/`Place` → 机械臂或 Spot arm 操作（`detector_model_path` 佐证视觉抓取）。消息层：`robot_executor_msgs/msg/ActionSequenceMsg`（`omniplanner_node.py:31` import，:99 发布）。具体 SDK 调用（`RobotCommandBuilder`、`RobotCommandClient` 等）在 spot_tools 内，**本项目如需要可另取 robustrobotics/spot_tools 副本，或按 "Follow→SE2 轨迹" 语义自行映射到 ANYmal 的 go-to/command API**——这正是 bridge 的替代工作。

---

## 5. Omniplanner 输出什么给 executor

**统一输出格式**：`robot_executor_msgs/msg/ActionSequenceMsg`（Spot 路径），发布到 `/{robot}/omniplanner_node/compiled_plan_out`。

**Python 侧结构**（`robot_executor_interface/action_descriptions.py`，Omniplanner 内用法见 `goto_points_ros.py` / `pddl_planner_ros.py`）：
- `ActionSequence(plan_id: str, robot_name: str, actions: List[...])`
- 具体 action（全部带 `frame`，坐标就在该 frame 下）：
  - `Follow(frame, path2d)` — 2D 路径点数组 `(N,2)`。GotoPoints 的每个 `GotoPointPrimitive(start, goal)` 内插 10 个点成一段 `Follow`（`goto_points_ros.py:24-32`）；PDDL 的 `goto-poi` 直接把参数（路径点列）给 `Follow`（`pddl_planner_ros.py:156-157`）；TSP 的每个 step 一段 `Follow`（`tsp_ros.py:19-26`）。
  - `Gaze(frame, robot_point, gaze_point, stow_after, object_id)` — `pddl_planner_ros.py:158-167`（对应 PDDL `inspect`）
  - `Pick(frame, object_class, robot_point, object_point, object_id)` — `pddl_planner_ros.py:169-177`
  - `Place(frame, object_class, robot_point, object_point, object_id)` — `pddl_planner_ros.py:185-190`
- 序列化：`robot_executor_interface_ros.action_descriptions_ros.to_msg(compiled_plan)`（`omniplanner_node.py:336`）→ `ActionSequenceMsg`；另有 `to_viz_msg` → `MarkerArray` 发到 `~/compiled_plan_viz_out` 供 rviz 显示。

**关键点**：plan 在 Omniplanner 侧被"编译"成抽象 action 序列时**已经落到具体 frame（dsg_frame，通常是 map）和具体坐标**，executor 不再做符号/语义理解，只做执行。对 ANYmal 而言，`Follow(frame, path2d)` 一个 action 就够用。

**另一机器人接口（对照）**：`PhoenixPlanningAdaptor`（`omniplanner_node.py:125-131`）发布的是 `nav_msgs/Path`，且 PDDL compile 时只保留 `goto-poi`、丢弃 inspect/pick/place（`pddl_planner_ros.py:219-236`）——同一 planner 输出按 robot_type 分流成不同消息，验证了 adaptor 多态的价值。

---

## 6. robot frame 如何配置

两处独立配置，职责不同：

1. **Omniplanner 侧**（`config_generation/base_params/omniplanner_plugins.yaml`）：
   ```yaml
   robots:
     - robot_name: euclid
       type: robot_executor     # config 类型 → 构造 RobotPlanningAdaptor
       robot_type: spot
       body_frame: euclid/base_link
     - robot_name: samoyed
       type: phoenix            # → 构造 PhoenixPlanningAdaptor
       robot_type: husky
       body_frame: samoyed/base_link
   ```
   由 `omniplanner_node.py:117-122` 的 `@register_config("robot_adaptor", name="robot_executor")` 和 `:135` 的 `name="phoenix"` 分别构造。**`body_frame` 是 Omniplanner 获取机器人位姿的 tf 子帧**：`get_pose(parent_frame)` 做 `tf2.lookup_transform(dsg_frame → body_frame)` 返回 `[x,y,z,yaw]`（`omniplanner_node.py:103-111`、`get_robot_pose` :141-164），用作规划的起点 `robot_states[robot_name]`。多机器人可行是因为每个 robot 的 body_frame 不同。
2. **executor 侧**（`spot_executor_node.yaml`）：`odom_frame: <robot>/odom`、`body_frame: <robot>/body`（或 base_link）。executor 用自己的 odom/body 帧维护位姿，与 Omniplanner 的 dsg_frame 通过 tf 树连通（sim 模式用 `static_transform_publisher` 造 `map→<robot>/map→<robot>/odom` 的伪 tf，见 `master.launch.yaml` 的 `launch_static_map_to_robot_map`/`launch_static_robot_map_to_odom`）。
3. 所有帧统一带 `<robot_name>/` 前缀（launch 顶部 `push_ros_namespace`，`master.launch.yaml:45` 附近），多机隔离。

---

## 7. path / action 如何发送给 Spot（API 类型）

- **主通道是 ROS2 topic（publish/subscribe），不是 service、不是 action**：
  - `ActionSequenceMsg` → topic `/<robot>/omniplanner_node/compiled_plan_out`（发布：`omniplanner_node.py:99`；订阅：`master.launch.yaml:210` 的 executor remap）。
- 辅助 topic：
  - `/<robot>/spot_executor_node/cmd_vel`（Twist，teleop 键盘直接注入）
  - `/<robot>/spot_executor_node/pause`、`/resume`（Bool，goal_manager 发布；`goal_manager_node.py:119-120`）
- service 只用于运维：`/<robot>/dsg_saver/save_dsg`（`dsg_saver_node.py:56`，触发 DSG json 落盘）、`/<robot>/shutdown`。
- 底到 Spot 硬件：executor 内部用 bosdyn SDK 直连（spot_ip + 认证，见第 4 问）。Omniplanner 到 executor 之间**没有任何 bosdyn 概念**——SDK 只存在于 executor 层内部，这是隔离最干净的地方。

---

## 8. ASCII 架构图

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ 在线建图（真机）                离线/prior 规划（无 Hydra）                        │
│                                                                                │
│  [ZED/Livox/相机]   [semantic_inference]                                        │
│        │                     │                                                  │
│  hydra_ros_node  ── frontend(Khronos 3D重建) ── backend(Hydra DSG)              │
│        │                     │                     │                            │
│        └── topic: /<robot>/hydra/backend/dsg_mesh  │                            │
│                                                    │                            │
│        topic: /<robot>/hydra/backend/dsg  ◄────────┘                            │
│            (spark_dsg DynamicSceneGraph, DsgPublisher)                          │
│                     ▲                              ▲                             │
│                     │ DsgSubscriber                │ DsgPublisher (latching, 3s)│
│              ┌──────┴───────┐             ┌────────┴──────────┐                 │
│              │ omniplanner  │             │ prior_dsg_publisher│                │
│              │ _node        │             │ (DynamicSceneGraph │                │
│              │ dsg_in       │             │   .load(json))     │                │
│              └──────┬───────┘             └────────────────────┘                │
│                     │  goal 入口: ~/goto_points_goal / ~/pddl_goal             │
│                     │  (GotoPointsGoalMsg / PddlGoalMsg / LanguagePlanner)      │
│                     ▼                                                           │
│   Omniplanner: ground_problem(dsg) → make_plan → compile_plan(adaptor, frame)   │
│   ── DSG node id "p123" → find_node → position[:2]                              │
│   ── GotoPointsPlan / PddlPlan → Follow/Gaze/Pick/Place → ActionSequence        │
│                     │                                                           │
│                     │  to_msg() → ActionSequenceMsg                             │
│                     ▼                                                           │
│   topic: /<robot>/omniplanner_node/compiled_plan_out                            │
│                     │                                                           │
│                     │ 订阅 (~/action_sequence_subscriber)                      │
│                     ▼                                                           │
│   ┌──────────────────────────────────────────────────────────────────────────┐  │
│   │ spot_executor_node  (spot_tools_ros, pyenv spark_env)                   │  │
│   │  ├─ 输入: ActionSequenceMsg, /occupancy_grid(hydra tsdf), cmd_vel,      │  │
│   │  │        pause/resume(Bool)                                            │  │
│   │  ├─ mid_level_planner: astar(真机) / identity(仿真)                     │  │
│   │  ├─ Spot interface: bosdyn SDK (spot_ip + 认证)  ── 或 ──  fake interface│  │
│   │  └─ 输出: 移动/对准/抓取命令 → Spot 硬件                                 │  │
│   └──────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────┘
   (launch 编排: dcist_launch_system/master.launch.yaml; 实验=experiment_manifest)
```

---

## 9. 对本项目（Khronos→ANYmal D bridge）最有价值 vs 仅架构参考

### 可直接复用 / 借鉴（高价值）

| 内容 | 位置 | 为什么可直接用 |
|---|---|---|
| **RobotPlanningAdaptor 多态**（robot-specific 输出抽象） | `omniplanner_node.py:89-135` | 就是本项目要做的 ANYmal adaptor 的现成范式：一个类负责 `get_pose(tf)` + `publish_plan(msg)`，通过 config 注册。ANYmal 只需注册 `type: robot_executor`（或新 type）并把 `body_frame` 换成 ANYmal 的 body/base |
| **`compiled_plan_out` 消息边界** | `omniplanner_node.py:99` / `master.launch.yaml:210` | planner 与 executor 的唯一契约就是一条 topic + ActionSequenceMsg。ANYmal 侧写一个订阅者即可，不动 planner |
| **`ActionSequence`/`Follow` 抽象 action 层** | `goto_points_ros.py:24-32`, `pddl_planner_ros.py:156-157` | `Follow(frame, path2d)` 是纯 2D 路径点序列，ANYmal 的 go-to 命令几乎同构。`Gaze/Pick/Place` 可先不实现 |
| **prior DSG 发布/保存节点** | `prior_dsg_publisher_node.py` / `dsg_saver_node.py` | Khronos 生成的 DSG 存成 spark_dsg json、再 `DynamicSceneGraph.load` 发布——Khronos 本来就是 Spark-DSG 系，可直接沿用。实现一个 ANYmal 版 DsgPublisher 发布者即可离线复用 Omniplanner 全链路 |
| **topic: `hydra/backend/dsg` 互换性**（在线图/先验图同一通道） | `master.launch.yaml:326,341` | Khronos→ANYmal 若保留 DSG 作为世界模型，在线/离线切换零成本 |
| **goal_manager 的 pause/resume/已满足判定** | `goal_manager_node.py` | executor 暴露 pause/resume 的轻量控制模式，可复用到 ANYmal 的"目标更新时先停后决定" |
| **plugin 化 planner 配置** | `omniplanner_plugins.yaml` / `omniplanner_node.py:300-345` | planner 以 yaml 注册、topic 名动态生成，扩展新 planner 不用改主节点 |
| **实验配置覆盖体系** | `experiment_manifest.yaml` + `config_generation/` | 真实/仿真/prior 用同一套代码、不同 yaml 覆盖（`use_fake_spot_interface` 等），验证模式清晰 |
| **DSG json 格式与序列化** | `Spark-DSG/serialization/graph_json_serialization.h`, `apartment_dsg.json` | 持久化格式现成，含 layer/edge/语义/位置，Khronos 输出即可对接 |

### 仅作架构参考（不要移植）

| 内容 | 原因 |
|---|---|
| PDDL + Fast Downward（`dsg_pddl_*`、`pddl_planning.py`） | 重、慢（README 自述 ~10s/plan）、依赖 fd 二进制；ANYmal 导航用 GotoPoints/TSP 量级足够 |
| MultiRobotWrapper / 多机 PDDL 投影（`multirobot_ros.py`, `compile_plan` 的多机分支） | 单机 ANYmal 用不上 |
| plum 多重分派 + Functor（`omniplanner.py` 的 Wrapper 体系） | 优雅但陡峭，单 planner 场景徒增复杂度 |
| Phoenix 的 `nav_msgs/Path` 输出 | 是"另一机器人接口"的对照样本，不是要复用的 |
| fleet-adt4 TUI / Zenoh / 网络编排（`dcist_launch_system/tui`, `network_diagnostics`） | 多机野战部署工具，与 bridge 无关 |
| heracles / Neo4j / dsg_updater 先验图版本管理 | 图数据库方案，超出导航 bridge 范围 |
| spot_sensor_node / spot_twist_node / 相机 decompress | Spot 专属传感器栈 |

---

## 10. 明确回答：这套分层的哪些设计模式可直接复用到 ANYmal adaptor

**可以（且推荐）直接照搬的 4 个模式**：

1. **"抽象 action 序列 + 消息边界"模式** — planner 只输出 `ActionSequence(plan_id, robot_name, [Follow(frame, path2d), ...])` 到一条 topic，executor 自己决定怎么翻译。ANYmal adaptor = 订阅 `compiled_plan_out`（或等价 topic）→ 把 `Follow.path2d` 转成 ANYmal 的 `navigate_to`/`move_to_pose` 调用。**这是全部借鉴中最核心的一条**：它让 planner 层完全不感知机器人 SDK。
2. **"RobotAdaptor 注册"模式** — 用 config（yaml `robots:` 段）+ `register_config` 构造器注册，每个机器人一个 adaptor 实例（name/type/body_frame）。ANYmal 注册 `{robot_name: anymal, type: robot_executor, robot_type: anymal_d, body_frame: anymal/body}`，Omniplanner 原样可用，甚至不用改代码。
3. **"DSG 先验图可插拔"模式** — 先验图与在线图走同一条 topic（`hydra/backend/dsg`），发布器只负责 `DynamicSceneGraph.load(json)` + latching 发布。Khronos 冻结副本（`navigation_references/Khronos`）同属 Spark-DSG 生态，json 格式互通，ANYmal 直接复用 DsgPublisher 语义。
4. **"实验/仿真开关"模式** — executor 参数里 `use_fake_*` + `mid_level_planner_type`(identity/astar) 让同一 executor 跑仿真/真机。ANYmal adaptor 应同样内置 fake 接口，便于在 Gazebo/无硬件下验证全链路。

**可以借鉴但需裁剪的**：plugin 机制（保留 yaml 注册思路，砍掉动态 topic 的通用性）；goal_manager pause/resume（保留接口，把"已满足判定"从 PDDL 谓词换成 ANYmal 目标语义）。

**不建议带的**：PDDL/Fast Downward、多机 wrapper、plum 多重分派、Phoenix Path 分支。

一句话总结：**本项目复用的正确姿势是——保留 "DSG(topic) → Omniplanner → ActionSequenceMsg(topic) → robot-specific adaptor" 这条消息链，把最末端的一个 adaptor 替换成 ANYmal SDK 实现，其余全部原样继承**。这恰好是 Omniplanner 当初设计 `RobotPlanningAdaptor` 的目的（README："a clean hook for transforming the output of a planner into robot-compatible input"）。

---

### 附录：关键证据清单（文件:行号）

- `Omniplanner/omniplanner_ros/src/omniplanner_ros/omniplanner_node.py`
  - :12 `from hydra_ros import DsgSubscriber`；:204 `DsgSubscriber(self, "~/dsg_in", ...)`
  - :89 `class RobotPlanningAdaptor`；:99 `compiled_plan_out` publisher；:103 `get_pose`；:113 `publish_plan`
  - :117-122 `@register_config("robot_adaptor", name="robot_executor")`；:125-135 `PhoenixPlanningAdaptor` + `name="phoenix"`
  - :261 `dsg_callback(header, dsg)`；:313 `plan_handler`；:336 `publish_plan(to_msg(compiled_plan))`
- `Omniplanner/omniplanner_ros/src/omniplanner_ros/goto_points_ros.py` :24-32 `compile_points_plan`（内插 10 点 → `Follow` → `ActionSequence`）
- `Omniplanner/omniplanner/src/omniplanner/goto_points.py` :46-61 `ground_problem`（:56 `dsg.find_node(str_to_ns_value(symbol))`）
- `Omniplanner/omniplanner_ros/src/omniplanner_ros/pddl_planner_ros.py` :156-190（`goto-poi→Follow`、`inspect→Gaze`、`pick-object→Pick`、`place-object→Place`）
- `Omniplanner/omniplanner_ros/src/omniplanner_ros/goal_manager_node.py` :14-15（pause/resume remap）、:119-120（publish）
- `Awesome-DCIST-T4/dcist_launch_system/launch/master.launch.yaml` :202（spot_executor）、:210（`action_sequence_subscriber ← compiled_plan_out`）、:326（`~/dsg_in → hydra/backend/dsg`）、:341（`~/dsg_out → hydra/backend/dsg`）
- `Awesome-DCIST-T4/dcist_launch_system/src/dcist_launch_system/prior_dsg_publisher_node.py` :27 `DynamicSceneGraph.load`、:44 `DsgPublisher(..., True)`、:48-49 每 3s 发布 frame="map"
- `Awesome-DCIST-T4/dcist_launch_system/src/dcist_launch_system/dsg_saver_node.py` :56 service、:68 `dsg.save(path, include_mesh)`
- `Awesome-DCIST-T4/dcist_launch_system/config_generation/base_params/spot_executor_node.yaml`（bosdyn 三件套、mid_level_planner、use_fake_* 等）
- `Awesome-DCIST-T4/dcist_launch_system/config_generation/base_params/omniplanner_plugins.yaml`（robots/planners 配置）
- `Awesome-DCIST-T4/dcist_launch_system/config_generation/experiment_manifest.yaml`（prior 实验定义）
- `Spark-DSG/include/spark_dsg/serialization/graph_json_serialization.h` :32-38（json 读写接口）；示例 `Spark-DSG/python/tests/resources/apartment_dsg.json`
