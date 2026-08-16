# Khronos → ANYmal D 导航 Bridge：参考仓库离线对照审计

- 审计日期：2026-08-09
- 访问方式：SSH 只读工作站 `/home/ulmmt/Masterthesis-wang/khronos_anymal_bridge/external/navigation_references/`（冻结副本，commit 见各节）
- 审计范围：`tagmap`、`art_planner`、`nav-suite`、`anymal_d_simple_description` 四个仓库
- 项目背景：Khronos semantic map → navigable pose graph → global path → MockExecutor/PhysicsSimulationExecutor → ANYmal D 仿真

---

## 1. tagmap（commit ded3eb728f64）

### 仓库目标
ETH Zurich 的 **Tag Map**（CoRL 2024）：用 RAM/RAM++ 大规模图像识别模型把场景变成**文本标签驱动的语义地图**，供 LLM 做空间推理与导航任务。论文有真实机器人导航实验，但**仓库代码本身只覆盖"建图 + 语义目标定位 + 离线评估"**，不包含导航执行。

### 关键文件与接口
| 文件:行号 | 内容 |
|---|---|
| `tag_mapping/tag_mapping/tag_map.py:23` | `TagMap`：`add_entry`（:29）、`add_tag(tag, entry_uuid)`（:40）、`query(tag)`（:79）→ 返回该 tag 关联的带 pose 的 entries。核心数据结构：tag → 观测帧 pose |
| `tag_mapping/tag_mapping/pose_graph.py:6` | `PoseGraph`：networkx 包装，`closest_node_idx`（:36）、`shortest_path(start_point, end_point)`（:57），节点 = 地图点，边带长度 |
| `tag_mapping/tag_mapping/localization/pipeline.py` | `localization_pipeline`：多视点 voxel voting → DBSCAN/HDBSCAN 聚类 → 得语义目标 bounding box（用于定位目标物/区域） |
| `tag_mapping/tag_mapping/evaluation/lattice_navigation_graph.py` | `LatticeNavigationGraph` + `create_lattice_navigation_graph`：在场景 mesh 上建 3D lattice grid → 滤掉穿墙边 → networkx 最短路径；**只用于评估，不用于实机导航** |
| `tag_mapping/tag_mapping/evaluation/lattice_graph_utils.py:8` | `assign_label_box_lattice_graph_nodes`：**"语义目标 → 可导航图节点"的关键转换**——把语义 bounding box 膨胀 ±1m（Habitat 的 OGN 判据），找 box 内 + 邻近且碰撞自由的 lattice 节点 |
| `evaluation/README.md` | 用 Matterport3D 离线评估 P2E/E2P 定位指标；**零 ROS 依赖**（grep `nav_msgs/ros/rospy` 无命中） |

### 读出的架构
```
扫描帧 + RAM tagger → TagMap(tag→pose) → 语义目标定位(bounding box)
   → assign_label_box_lattice_graph_nodes → 可导航 lattice 图节点
   → PoseGraph/networkx shortest_path → 全局路径（到此为止，之后交给外部导航）
```
**semantic target 变成导航目标**的机制在代码里就是这个：tag 查询 → 定位出 OBB → 映射到导航图最近可达节点。但**整个 repo 没有 local planner、没有 nav_msgs/Path 发布、没有 ANYmal 集成**；"语义→图节点→路径→执行"的闭环只在论文的实机部分，仓库里没有对应代码。

### 判断：仅参考（架构模式）＋ 少量可复用
- **复用**：`TagMap` / `PoseGraph` 这两个数据结构的思路很轻（约百行），语义 tag → pose → 图节点 → 最短路径的链条可以直接照搬接口；`assign_label_box_lattice_graph_nodes` 的"语义 box → 邻近可导航节点（碰撞检查 + 距离阈值）"是值得抄的转换逻辑。
- **不复用**：整条 RAM 图像打标 + Matterport 管线与本项目（Khronos 语义 SLAM 作为地图源）不兼容；lattice graph 生成 + 全对最短路径矩阵离线评估与实机导航无关；无任何 ROS/执行层。
- 结论：**只做架构参考**——它是"text-based semantic map → navigable graph → global path"的标准样例，正好对应该项目的上游链路，但需要自研连接层。

---

## 2. art_planner（commit 2f3fde68b8ac）

### 仓库目标
ETH Zurich 的 **ArtPlanner**（Field Robotics 2023 / IROS 2021）：基于 2.5D height map + 学习得到的 motion cost，为 ANYmal 做采样式路径规划。真正可用于 ANYmal 的 local terrain planner。

### 关键文件与接口
| 文件:行号 | 内容 |
|---|---|
| `art_planner_msgs/action/PlanToGoal.action` | Action 接口：goal = `geometry_msgs/PoseStamped`；feedback 状态枚举 `INVALID_START/INVALID_GOAL/PLANNING/FOUND_SOLUTION/NO_SOLUTION/NO_GOAL_TF/...`（与本项目 MockExecutor 的状态机几乎一一对应） |
| `art_planner_ros/src/planner_ros.cpp:256` | 订阅 `elevation_map`（`grid_map_msgs/GridMap`，含 elevation + traversability 层） |
| `planner_ros.cpp:258,265` | Action server `plan_to_goal`；发布 `path`（`nav_msgs/Path`，latch）、`map`、`planning_time` |
| `planner_ros.cpp` (构造函数后段) | 单次规划 service `plan`（`nav_msgs/GetPlan`）；机器人位姿从 TF（map←base，加 feet center 偏移 `robot.feet.offset.z`）；连续重规划线程按 `replan_freq` 跑 |
| `art_planner/include/art_planner/planner.h` | 核心库：OMPL `SE3StateSpace` + `SimpleSetup`，`Map`（grid_map）、`StateValidityChecker`（躯干 box + 四足 reachability box 在地形图上做碰撞） |
| `art_planner_ros/config/params.yaml:7` | `replan_freq: 1.0 Hz`、`plan_time: 0.01s`、`elevation_layer/traversability_layer`、`traversability_thres: 0.15`、机器人躯干尺寸、`snap_goal_to_map: true` |
| `art_planner_motion_cost/srv/costQuery.srv` | Motion cost 服务：`query_poses` [B×6]（每行 target x,y,yaw, start x,y,yaw）→ `cost_power/cost_time/cost_risk`；模型权重 `rsc/model/grid-*.pt` 随 LFS 提供，PyTorch |
| `art_planner_ros/scripts/path_follower.py` | 官方自嘲 "hacky and unsupported" 的路径跟随器：订阅 `/art_planner/path` → 发 `/path_planning_and_following/twist`（`TwistStamped`），PID 控制 |

### 读出的架构
```
grid_map (elevation+traversability) ──┐
PoseStamped goal ──▶ OMPL 采样式规划（lazy_prm_star / prm_motion_cost）
  ├─ 碰撞检查：2.5D 高度图上躯干盒 + 足端可达盒 + 边缘侵蚀（foothold_margin）
  └─ 优化目标：learned motion cost（energy/time/risk，网络服务返回）
──▶ nav_msgs/Path ──▶ 外部 path follower ──▶ TwistStamped
```
依赖：**ROS noetic / catkin**、OMPL 1.4.2、ANYbotics `grid_map`、OpenCV，并自带一份改过 height-field 碰撞检查的 **vendored ODE**（与系统 ODE 冲突的警告写在 README 里）。

### 判断：借鉴为主，核心库有条件复用
- **借鉴**：接口设计是本项目 local planner 层的教科书——`PoseStamped goal → nav_msgs/Path`、feedback 状态枚举、TF 取机器人位姿、1Hz 重规划、goal 自动吸附地形。**`prm_motion_cost` 的 learned motion cost 是 ANYmal 专用、开箱即用**，价值最高。
- **复用障碍**：① 整栈是 **ROS 1 noetic catkin**，本项目（Khronos 系）是 **ROS 2**（khronos_ros 用 `ament_cmake/rclcpp`），ROS 接口层要重写；② 依赖 OMPL + 自改 ODE + grid_map，构建链重；③ 输入必须是 ANYbotics grid_map 格式，需从 Khronos 地图转出 elevation/traversability 层。
- 结论：**核心规划库（art_planner 包）本身不依赖 ROS**（只依赖 grid_map_core + OMPL），可作为算法与配置参考，或评估移植成本；**不推荐直接 catkin 整体复用**。

---

## 3. nav-suite（commit 648ba3b28dac）

### 仓库目标
leggedrobotics 的 **Isaac Navigation Suite**（Apache-2.0）：在 IsaacSim/IsaacLab 里统一"环境导入 + 数据采集 + 地形可通行性分析 + RL 导航训练"的框架，带 ANYmal-D 导航任务示例。这是本项目 simulation 层的**直接参考**。

### 关键文件与接口
| 文件:行号 | 内容 |
|---|---|
| `README.md` | 运行条件：**IsaacSim 4.5.0 + IsaacLab v2.1.1**（last checked commit 19b24c7，需含 IsaacLab-Internal **PR77**）、Ubuntu 24.04、Python 3.10、git-lfs 拉取 policies/terrains |
| `exts/nav_tasks/nav_tasks/configs/env_cfg_base.py:53` | `NavTasksDepthNavSceneCfg`：terrain = `TerrainImporterCfg(ROUGH_TERRAINS_CFG)`；**robot = `ANYMAL_D_CFG`（来自 `isaaclab_assets` 扩展，不在本 repo）**（:73）；传感器 = `height_scanner`（RayCaster，locomotion 策略观测）+ `front_zed_camera`（ZED X Mini raycaster 相机，15°，下采样）+ `contact_forces` |
| `env_cfg_base.py:128-133` | **动作接口 `NavigationSE2ActionCfg`**：动作 = SE2 速度命令（vx, vy, yaw_rate），经 `low_level_action = JointPositionActionCfg`（全部关节）喂给冻结的 **locomotion 策略 `/Policies/ANYmal-C/HeightScan/policy.pt`（来自 IsaacLab Nucleus，不在本 repo）** |
| `env_cfg_base.py:426-432` | 频率分层：`sim.dt=0.005`，nav 策略 `decimation=20 → 10Hz`，low-level `low_level_decimation=4 → 50Hz` |
| `exts/nav_tasks/nav_tasks/mdp/actions/navigation_actions.py` | `NavigationSE2Action.process_actions`（10Hz 速度命令）/ `apply_actions`（50Hz 用 `torch.jit` 加载的 low-level 策略输出关节位置） |
| `exts/nav_tasks/nav_tasks/mdp/commands/goal_command_base.py` | Goal 命令：`pos_command_w`（世界系 x,y,z），`GoalCommandCfg` 从 terrain analysis 图采样可行 start-goal 对（`path_length_range [2,10] m`） |
| `exts/nav_suite/nav_suite/terrain_analysis/terrain_analysis.py:43,270` | `TerrainAnalysis`（singleton）：raycast 建密集 height map → 采样可行点 → `nx.Graph`（node 带 `pos`/`terrain_idx`，edge 带 `distance`）→ `shortest_path_lengths`（:152）。**"可通行性 → 导航图"就是它** |
| `exts/nav_suite/nav_suite/terrain_analysis/terrain_analysis_cfg.py:92-121` | 图过滤同时支持 **semantic 域**：`semantic_point_filter/semantic_edge_filter/semantic_cost_mapping`（yaml：class→cost，`semantic_cost_threshold=0.5`）；示例 `data/matterport/semantic_costs.yaml`（floor=0 可通行，其余=1 障碍） |
| `scripts/nav_suite/collector/*`、`scripts/nav_suite/terrains/*` | 独立脚本：Matterport/Carla/Warehouse 导入、轨迹采样、多模态传感器渲染 |

### 读出的架构（ANYmal-D 导航仿真）
```
TerrainAnalysis(height map + semantic cost) → nx 可通行图
   → GoalCommand 采样 start-goal
   → Nav 策略(10Hz) 输出 SE2 速度命令
   → ANYmal-C HeightScan locomotion 策略(50Hz) 输出关节位置
   → ManagerBasedRLEnv 物理仿真，状态/接触/相机观测闭环
```

### 判断：借鉴（simulation 层首选参考），不直接复用
- 它是 **RL 训练/数据收集框架**，不是"给一条 GlobalRoute 去执行的导航执行器"——本项目要的 `PhysicsSimulationExecutor` 更接近"IsaacLab 里跑 ANYmal-D、接受速度命令、回报状态"的一个瘦 wrapper，nav-suite 给出了这个 wrapper 必须对接的全部接口与运行条件。
- **运行条件苛刻**：IsaacSim 4.5.0 + IsaacLab v2.1.1（PR77）+ Nucleus 上的 ANYmal-C locomotion policy + NVIDIA GPU；robot 资产与策略都**不在仓库里**，属于外部依赖。只研究接口合理，安装/训练成本高，不应在 Phase 0 做。
- 结论：**接口/运行条件参考**；其中 terrain analysis（semantic cost → 可通行图）与"semantic map → 导航图"的对接方式对 global 层也有参考价值。

---

## 4. anymal_d_simple_description（commit d5bf4dc3a81a）

### 仓库目标
ANYbotics 官方公开的 ANYmal D **简化机器人描述（URDF）**，BSD-3-Clause。README 明确：完整 ANYmal D description/simulation/control 仅对 **ANYmal Research 社区**开放。

### 关键文件与边界
| 文件:行号 | 内容 |
|---|---|
| `urdf/anymal.urdf` | **单个 1531 行 URDF（不是 xacro）**；14 个 revolute joint：12 个腿部关节（LF/RF/LH/RH × HAA/HFE/KFE）+ 2 个 inspection payload（pan/tilt）；`base` link（:7），`base_inertia` 质量 `8.27629 kg`（:53） |
| `urdf/anymal.urdf` | **只有** kinematics + link（`<visual>`/`<collision>`/`<inertial>`）+ `meshes/*.dae` 与 `*.jpg` 纹理 |
| `urdf/anymal.urdf` | **没有** `<sensor>`、`<gazebo>`、`<transmission>`、`ros_control`/`hardware_interface` 任何标签（grep 零命中） |
| `launch/load.launch` | 仅把 URDF 以 `textfile` 塞进 parameter server（注释写 "xacro" 但实际是 raw URDF） |
| `launch/standalone.launch` | `joint_state_publisher_gui` + `robot_state_publisher` + rviz 可视化调试 |

### 判断：复用（仅作描述/可视化），仿真与控制边界必须划清
- **能复用**：URDF + DAE mesh 可直接用来做机器人可视化、TF 树、collision 外形（IK/占位），BSD-3 许可宽松。
- **不能复用为仿真**：无任何执行器/控制器/传感器插件，无法直接驱动；真正开环/闭环仿真要么自己搭（难），要么走 ANYmal Research。
- **论文声称边界**：
  - 可以声称：使用公开的 ANYmal D 运动学/几何描述（`anymal_d_simple_description`，BSD-3）做机器人建模与可视化；导航框架本身（语义图、规划、仿真接口）是自己的贡献。
  - **不能声称**：使用了 ANYbotics 官方的完整 ANYmal 仿真/控制栈；不能暗示具备与官方一致的动力学保真度或传感器模型；若提及 ANYmal 本体性能，须注明使用的是简化 URDF。

---

## 5. 综合判断

| 组件 | 仓库 | 结论 | 理由 |
|---|---|---|---|
| 语义→导航目标转换 | tagmap | **借鉴接口** | `query(tag)→localization box→assign_label_box_lattice_graph_nodes→图节点` 是标准链路，逻辑可抄；其余是 Matterport/RAM 离线管线，不相关 |
| PoseGraph / 最短路径 | tagmap | **借鉴** | networkx 包装 ~100 行，接口干净；但本项目如用 Khronos pose graph，需自建连接层 |
| Local terrain planner | art_planner | **核心库有条件复用，接口必借鉴** | 唯一真正的 ANYmal 地形规划器；但 ROS1 noetic + OMPL + 自改 ODE，移植成本高；learned motion cost 模型价值最高 |
| 可通行性→导航图 | nav-suite | **借鉴** | `TerrainAnalysis`（height map + semantic cost → nx.Graph）对"语义图→可导航图"有直接参考价值 |
| ANYmal-D 仿真执行 | nav-suite | **仅接口/运行条件参考** | IsaacLab v2.1.1 + IsaacSim 4.5.0 + PR77 + Nucleus policy + GPU，环境重，Phase 0 只研究 |
| 机器人描述 | anymal_d_simple_description | **复用** | URDF+mesh 直接可用作可视化/外形；无仿真/控制能力 |

## 6. 对本项目的建议

**Local planner 层：优先看 art_planner。**
- 接口照抄：`PoseStamped goal → nav_msgs/Path`、连续重规划（1Hz）、goal 吸附地形、`PlanToGoal.action` 的 feedback 枚举（与本项目 MockExecutor 状态机一一对应，可做协议对齐）。
- 若 Phase 0 之后要真实地形规划，评估两条路：① 仅移植 art_planner 核心库（`art_planner` 包本身无 ROS 依赖，需把 `grid_map` 输入换成 ROS2 消息或自建 2.5D 结构）；② 用其配置/成本模型思路自研。learned motion cost 权重可考虑直接用。
- tagmap 的 PoseGraph/`assign_label_box_lattice_graph_nodes` 作为"semantic target → 导航图节点"的下游衔接参考。

**Simulation 层：优先看 nav-suite（只研究接口，不装不训）。**
- `PhysicsSimulationExecutor` 的对接面就是 `env_cfg_base.py` 那套：输入 = goal position(x,y,z)+yaw，输出 = 10Hz SE2 速度命令，50Hz 由 ANYmal-C locomotion 策略转关节位置，状态/接触/相机读回。
- 运行时前置条件：IsaacSim 4.5.0 + IsaacLab v2.1.1（含 PR77）+ Nucleus 的 `ANYmal-C/HeightScan/policy.pt` + NVIDIA GPU；robot 资产来自 `isaaclab_assets`。这些决定了"能不能跑、在哪跑"。
- **MockExecutor → PhysicsSimulationExecutor 的接口要求**（据 nav-suite + 项目 dev plan）：保持 MockExecutor 的状态机契约（`IDLE/PLANNING/MOVING/REACHED/NO_PATH/INVALID_GOAL/CANCELLED`）不变；把"按 route 节点推进"替换为：route 节点 → `pos_command_w`（世界系 x,y,z + yaw）→ 周期下发 SE2 速度命令 → 轮询 base pose/速度判断 `REACHED`/超时 `NO_PATH`。两边的抽象（goal 2D pose in、状态反馈 out）天然对齐，替换点在 `MOVING` 的内部实现。

**机器人本体：用 anymal_d_simple_description 做可视化/外形层即可**，论文表述按第 4 节的声称边界执行。
