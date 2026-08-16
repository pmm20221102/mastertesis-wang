# Omniplanner 离线审计报告（Khronos→ANYmal D 导航 bridge 项目）

- 仓库：MIT-SPARK/Omniplanner（参考副本，只读）
- commit：`db0c8326d952d803248cb82dba6bd0878af98e12`（main，与任务指定一致）
- 审计日期：2026-08-09
- 审计目标链路：`DSG → symbol/node grounding → node.attributes.position → GotoPointsPlan → compile_plan → Follow / nav_msgs/Path`

---

## 0. 仓库结构速览

两个 ROS2 Python 包 + 若干附属：

- `omniplanner/` —— 非 ROS 核心：`omniplanner.py`（管线 + wrapper 框架）、`goto_points.py`、`tsp.py`、`language_planner.py`、`compile_plan.py`、`functor.py`、`utils.py`
- `omniplanner/src/dsg_pddl/` —— PDDL 专用 grounding/planning（Fast Downward 求解）+ `domains/*.pddl`
- `omniplanner_ros/` —— ROS2 节点 `omniplanner_node.py` + 各 planner 插件（`goto_points_ros.py`、`pddl_planner_ros.py`、`language_planner_ros.py`、`multirobot_ros.py`、`goal_manager_node.py`、`omniplanner_repair_node.py`）
- `text_to_speech/` —— 计划语音播报（与本次审计无关）
- 外部依赖（不在本仓库）：`spark_dsg`（DSG 本体）、`plum`（multiple dispatch）、`spark_config`（配置）、`hydra_ros`（DsgSubscriber）、`robot_executor_interface(_ros)`（定义 `ActionSequence`/`Follow` 等）、`omniplanner_msgs`（自定义 msg）、`nlu_interface(_dcist)`（LLM grounding）

---

## 1. 架构总览：四层职责

README 明确把系统拆成四层。注意：**四层不是四个独立的 ROS 节点，而是同一 ROS 回调链内、通过 `plum` 动态多重分派（multiple dispatch）串联的函数层**。ROS 侧只有一个核心节点 `OmniPlannerRos`，插件（planner plugin）注册订阅 topic 后，回调里依次走完 grounding → planning → compile。

| 层 | 载体 | 职责 | 关键函数 |
| --- | --- | --- | --- |
| **问题 grounding** | 每个 plugin 实现的 `ground_problem(...)` 分派 | 把 (domain, map_context, robot_states, goal) 变成求解器可吃的**已落地问题**。map_context 可以是 `DynamicSceneGraph`、`np.ndarray`（点集）等，按类型分派 | `ground_problem(domain, dsg, robot_states, goal, feedback)` |
| **问题求解（problem solving）** | `make_plan(...)` 分派 | 对已落地问题产出**抽象计划**（符号动作序列或点序列） | `make_plan(grounded_problem, map_context)` |
| **robot-specific plan post-processing** | `compile_plan(adaptors, plan_frame, plan)` 分派 | 把抽象计划**编译成具体机器人的可执行消息**（`ActionSequence[Follow]` 或 `nav_msgs/Path`），按 plan 类型 + adaptor 类型双重分派 | `compile_plan(...)` + 各 plugin 的 `compile_points_plan` / `compile_pddl_plan` 等 |
| **runtime monitoring** | `OmniPlannerRos` 节点内 | DSG 新鲜度心跳（`NodeInfoMsg`，DSG 超过 15s 未更新报 WARNING）、规划状态、`PluginFeedbackCollector` 把 LLM 响应发布给 RViz、`ros_logging.py` 把 python logging 转发到 ROS | `hb_callback`（omniplanner_node.py:269）、`plugin.get_plugin_feedback(node)` |

**管线总入口** `full_planning_pipeline`（omniplanner.py:268）顺序：

```
ground_problem(domain, map_context, robot_states, goal, feedback)
  → GroundedProblem 包进 SymbolicContext(DsgContextProvider(map_context), grounded_problem)  # 携带符号上下文
  → make_plan(contextualized_problem, map_context)
```

之后由节点调用 `compile_plan(self.robot_adaptors, self.dsg_frame, plans)` → `collect_plans` → 按 robot_name 发布。

**扩展机制**：新 plugin = 一组 `ground_problem`/`make_plan`/`compile_plan` 分派实现 + 一个 ROS 接口类（`get_plan_callback()` 返回 `(msg_type, topic, callback)`）+ `spark_config` 注册。README 明说"集成新 planner 几乎不用碰 Omniplanner ROS 代码"。

---

## 2. 关键 class / function 清单（文件:行号 · 输入 → 输出）

### 2.1 核心框架（omniplanner/src/omniplanner/omniplanner.py）

| 符号 | 位置 | 输入 → 输出 |
| --- | --- | --- |
| `PlanningDomain` / `PlanningGoal` / `ExecutionInterface` | :29 / :33 / :37 | 空标记类（planning domain 的基类） |
| `PlanRequest` | :42 | `(domain, goal, robot_states: dict)` → 请求封装 |
| `GroundedProblem` / `Plan` | :49 / :56 | 空标记类（已落地问题 / 计划） |
| `Wrapper(FunctorTrait)` | :60 | wrapper 基类（可被 fmap） |
| `fmap(fn, wrapper: Wrapper)` | :66 | 对 wrapper 内容应用 fn |
| `push(x: Wrapper)` | :71 | 交换两层 wrapper 顺序（`W[A]→A[W]` 反向包装） |
| `RobotWrapper[T]` | :80 | `(name: str, value: T)` —— 带机器人名的容器，`fmap`/`extract`/`with_new_value` 在 :87/:93/:99 |
| `MultiRobotWrapper[T]` | :104 | `(names, value, 两级名字映射)`，多机器人专用 |
| `string_as_nodesymbol(str)` | :151 | `"p0"`/`"O(3)"` 字符串 → `spark_dsg.NodeSymbol`（None 表示解析失败） |
| `SymbolicContext[T]` | :160 | `(context: dict, value: T)` —— 计划携带符号上下文（如 DSG 节点语义标签） |
| `DsgNodeContext(UserDict)` | :180 | DSG 节点上下文 dict（含 `position`、`semantic_label`），不可被外部覆盖 |
| `DsgContextProvider(dict)` | :194 | 惰性从 DSG 查节点上下文；`__getitem__(symbol)` 返回 DsgNodeContext |
| `DispatchException` | :236 | 无匹配分派时报错 |
| `ground_problem(domain, map_context, robot_states, goal, feedback)`（基类 fallback） | :245 | 抛 `DispatchException` —— 实际实现靠各 plugin 分派注册 |
| `make_plan(GroundedProblem, map_context)`（基类） | :257 | 抛 `DispatchException` |
| `make_plan(grounded_problem: Functor, map_context)` | :262 | `Functor → fmap(make_plan, _)` —— 使 wrapper 可透明穿过管线 |
| `full_planning_pipeline(PlanRequest, map_context, feedback)` | :268 | `PlanRequest → SymbolicContext[grounded 结果] → plan`（**管线主入口**） |
| `full_planning_pipeline(list[PlanRequest], ...)` | :289 | 多请求并行版（逐 request 调用上面那个） |

### 2.2 GotoPoints plugin（omniplanner/src/omniplanner/goto_points.py）

| 符号 | 位置 | 输入 → 输出 |
| --- | --- | --- |
| `GotoPointsDomain(PlanningDomain)` | :13 | 空 domain 标记 |
| `GroundedGotoPointsProblem` | :17 | `(start_point, goal_points)` —— 已落地为数值点 |
| `GotoPointsPlan` | :24 | `plan: list` —— 抽象计划（**注意：`append` 方法有个 bug，`self.plan.append(gpplan.append)` 拼错了，见 §8 备注，实际没人用**） |
| `GotoPointPrimitive` | :33 | `(start: np.ndarray, goal: np.ndarray)` —— 单段移动原语 |
| `GotoPointsGoal` | :39 | `(goal_points: List[str], robot_id: str)` —— goal 是 **symbol 字符串列表**（如 `["O(0)", "O(1)"]`） |
| `ground_problem(GotoPointsDomain, dsg: DynamicSceneGraph, robot_states, goal: GotoPointsGoal, feedback)` | :46 | `(domain, DSG, robot poses, symbol-goal) → RobotWrapper[GroundedGotoPointsProblem]` |
| `ground_problem(GotoPointsDomain, map_context: np.ndarray, start, goal: list, feedback)` | :75 | `(domain, 点集, start, 索引) → GroundedGotoPointsProblem` |
| `make_plan(GroundedGotoPointsProblem, map_context)` | :87 | `(start, points) → GotoPointsPlan`（串联 start→p0→p1→…） |

### 2.3 计划编译层（omniplanner/src/omniplanner/compile_plan.py）

| 符号 | 位置 | 输入 → 输出 |
| --- | --- | --- |
| `compile_plan(adaptors, plan_frame, p: SymbolicContext[List[Any]])` | :22 | 处理 plan 是 list 子类的边缘情况 → `List[RobotWrapper]` |
| `compile_plan(adaptors, plan_frame, p: SymbolicContext[RobotWrapper[Any]])` | :31 | **单机器人主路径**：`push` 反包 → 取 adaptor → fmap 编译 |
| `compile_plan(adaptors, plan_frame, p: list)` | :42 | 裸 list 逐元素编译 |
| `compile_plan(adaptors, plan_frame, p: RobotWrapper[Any])` | :49 | 剥 RobotWrapper，按 name 取 adaptor |
| `compile_plan(adaptors, plan_frame, p: SymbolicContext[MultiRobotWrapper[Any]])` / `MultiRobotWrapper[SymbolicContext]` | :55 / :65 | 多机器人路径（名字重映射后分编译） |
| `compile_plan(adaptor, plan_frame, p: Wrapper)` | :76 | **"跳过"多余 wrapper**：`extract(p)` 后递归（GotoPointsPlan 走到这里） |
| `collect_plans(List[RobotWrapper] / RobotWrapper)` | :94 / :100 | `{robot_name: compiled_plan}` |

### 2.4 ROS 侧（omniplanner_ros/src/omniplanner_ros/）

| 符号 | 位置 | 输入 → 输出 |
| --- | --- | --- |
| `compile_points_plan(plan, plan_id, robot_name, frame_id)` | goto_points_ros.py:24 | `GotoPointsPlan → ActionSequence[Follow]`（Spot/RobotExecutor 用） |
| `compile_plan(adaptor, plan_frame, p: GotoPointsPlan)` | goto_points_ros.py:38 | 通用 adaptor 版 → `ActionSequence` |
| `compile_plan(adaptor: PhoenixPlanningAdaptor, plan_frame, p: GotoPointsPlan)` | goto_points_ros.py:43 | **Phoenix 版 → `nav_msgs/Path`** |
| `compile_points_path(plan, robot_name, frame_id)` | goto_points_ros.py:80 | `GotoPointsPlan → nav_msgs/Path`（每段 10 点线性插值，z=0，orientation.w=1） |
| `path_to_marker(path, marker_ns)` | goto_points_ros.py:53 | Path → MarkerArray 可视化 |
| `GotoPointsRos` + `get_plan_callback()` / `goto_points_callback(msg, robot_poses)` | :104 / :108 / :114 | `GotoPointsGoalMsg → PlanRequest` |
| `GotoPointsConfig` | :131 | spark_config 注册（plugin 名 `"GotoPoints"`） |
| `RobotPlanningAdaptor` | omniplanner_node.py:89 | `(config, node, tf_buffer)` —— 持 `name/robot_type/child_frame`，`get_pose(parent_frame)` :103，`publish_plan` :113 发 `ActionSequenceMsg` |
| `PhoenixPlanningAdaptor(RobotPlanningAdaptor)` | omniplanner_node.py:127 | 覆写 `plan_pub` 为 `Path` topic `/ {name}/omniplanner_node/compiled_plan_out` |
| `get_robot_pose(tf_buffer, parent_frame, child_frame)` | omniplanner_node.py:151 | `tf → np.array([x,y,z,yaw])`（**start pose 的唯一来源**） |
| `OmniPlannerRos(Node)` | omniplanner_node.py:181 | 核心节点：DSG 订阅 :261、心跳 :269、`get_robot_poses` :300、`register_plugin` :306（内含 `plan_handler` :313，主线调用点） |
| `PhoenixPlanningAdaptor` 对应 `compile_pddl_plan` 版 | pddl_planner_ros.py:212 `compile_phoenix_pddl_plan` | PDDL 计划 → Path（仅 goto-poi，其余动作打日志跳过） |

### 2.5 PDDL 侧（dsg_pddl/，与 TSP 一起看更全）

| 符号 | 位置 | 输入 → 输出 |
| --- | --- | --- |
| `PddlSymbol(symbol, layer, unary_predicates, position)` | pddl_grounding.py:15 | symbol + 类别 + 可选 position（np.ndarray） |
| `PddlProblem` / `.to_string()` | :29 / :43 | PDDL problem 对象 → Fast Downward 可读字符串 |
| `ConstraintFact(predicate, symbols)` | :86 | 运行时约束（forbidden-poi / forbidden-edge） |
| `PddlGoal(pddl_goal, robot_id, constraints)` | :98 | PDDL 目标字符串 + 机器人 |
| `PddlDomain`（解析 PDDL AST） | :131 | domain 字符串 → 结构化 AST |
| `GroundedPddlProblem(domain, problem_str, symbols: Dict[str,PddlSymbol])` | :193 | 已落地 PDDL 问题 |
| `solve_pddl(problem)` | pddl_planning.py:19 | `GroundedPddlProblem → list[tuple]`（调 fast-downward 子进程；env 可调搜索/限时） |
| `ground_problem(PddlDomain, dsg, robot_states, PddlGoal)` | dsg_pddl_grounding.py:477 | DSG → `RobotWrapper[GroundedPddlProblem]`；按 domain_name 分派到 `generate_inspection/rearrangement/region_pddl` |
| `add_symbol_positions(G, symbols)` | dsg_pddl_grounding.py:314 | 用 `G.get_node(NodeSymbol(...)).attributes.position[:2]` 回填符号位置 |
| `generate_object_containment` / `generate_place_containment` | :221 / :248 | DSG 拓扑 → `object-in-place` / `place-in-region` 事实 |
| `explicit_edges_from_layer` / `implicit_edges_from_layers` | :65 / :85 | 层内/跨层边 → 连通性事实（带距离，含 LayerPlanner 导航距离） |
| `make_plan(GroundedPddlProblem, map_context)` | dsg_pddl_planning.py:76 | FD 计划 → `PddlPlan`，再把符号动作 `parameterize` 成路径（`parameterize_goto_poi` :28 用 `LayerPlanner.get_external_path`） |
| `PddlPlan(domain, symbolic_actions, parameterized_actions, symbols)` | dsg_pddl_planning.py:17 | 抽象计划 = 符号动作 + 几何参数（路径点） |
| `LayerPlanner(dsg, layer)` | tsp.py:17 | DSG MESH_PLACES 层 → networkx；`get_shortest_distance` / `get_shortest_path` / `get_closest_node_id` / `get_external_distance` :68 / `get_external_path` :82 |
| `ground_problem(LanguageDomain, dsg, robot_states, LanguageGoal)` | language_planner.py:26 | 自然语言 → （`goto_points`：直接分词成 symbol 列表 或 `Pddl`：`llm_interface.request_plan_specification(command, dsg)` → `ast.literal_eval` 成 `{robot: pddl_goal}`）→ 递归进 GotoPoints/PDDL grounding |
| `ground_problem(TspDomain, dsg, ...)` | tsp.py:152 | symbol → 位置 → 全对导航距离矩阵（LayerPlanner）→ `GroundedTspProblem` |

---

## 3. GotoPointsPlan 完整生成链（逐跳追踪）

以 `GotoPointsRos` 插件、单机器人、goal = `["O(0)","O(1)"]` 为例：

1. **ROS 入口**：用户在 `/ {robot}/omniplanner_node/goto_points_goal`（私有 topic，前缀 plugin 名）发布 `GotoPointsGoalMsg(point_names_to_visit, robot_id)`。`register_plugin` 注册的 `plan_handler`（omniplanner_node.py:313）收到。
2. **预处理**：`plan_handler` 检查 `dsg_last` 非空；`robot_poses = get_robot_poses(self.dsg_frame)`（即每个 adaptor 用 tf 查 `dsg_frame → body_frame` 的 `[x,y,z,yaw]`）；调 plugin 的 `goto_points_callback(msg, robot_poses)` → `GotoPointsGoal(goal_points=["O(0)","O(1)"], robot_id)` → `PlanRequest(domain=GotoPointsDomain(), goal, robot_states)`。
3. **管线**：`full_planning_pipeline(req, dsg_last, feedback)`（omniplanner.py:268）：
   - `ground_problem(GotoPointsDomain, dsg, robot_states, GotoPointsGoal)`（goto_points.py:46）：
     - `start = robot_states[goal.robot_id]`（**起点来自 tf，见 §4**）
     - 闭包 `get_loc(symbol)`：`dsg.find_node(str_to_ns_value(symbol))` → `node.attributes.position[:2]` —— **这就是 DSG node → node.attributes.position 的落地点**；查不到直接 `raise Exception`
     - `referenced_points = np.array([...])` 取全部目标点 2D 坐标
     - 递归 `ground_problem(domain, referenced_points, start, [0..n-1], feedback)`（goto_points.py:75）→ `GroundedGotoPointsProblem(start_point, point_sequence)`
     - 包 `RobotWrapper(robot_id, grounded)` 返回
   - 包 `SymbolicContext(DsgContextProvider(dsg), RobotWrapper(...))`（omniplanner.py:279-281）
   - `make_plan(...)`：命中 `make_plan(Functor, map_context)`（omniplanner.py:262），fmap 逐层剥到 `GroundedGotoPointsProblem` → `make_plan`（goto_points.py:87）：`time.sleep(3)`（模拟规划耗时）→ 构建 `GotoPointPrimitive(start→p0)` 和 `p_i→p_{i+1}` → `GotoPointsPlan(plan)`
   - 最终 plan 类型：`SymbolicContext[RobotWrapper[GotoPointsPlan]]`
4. **编译**：`compile_plan(self.robot_adaptors, self.dsg_frame, plans)`（omniplanner.py:313 之后节点调用）：
   - 命中 `compile_plan(adaptors, plan_frame, SymbolicContext[RobotWrapper[Any]])`（compile_plan.py:31）→ `push(p)` 成 `RobotWrapper[name, SymbolicContext[GotoPointsPlan]]` → `adaptor = adaptors[name]` → `fmap(compile_plan(adaptor, plan_frame), _)` → 剥到 `SymbolicContext[GotoPointsPlan]`
   - 无 `SymbolicContext[GotoPointsPlan]` 专用分派 → 命中"跳过 wrapper" `compile_plan(adaptor, plan_frame, Wrapper)`（compile_plan.py:76）→ `extract` 出 `GotoPointsPlan`
   - **Spot adaptor**（`RobotPlanningAdaptor`）→ `compile_plan(adaptor, plan_frame, GotoPointsPlan)`（goto_points_ros.py:38）→ `compile_points_plan`：每段 `Follow(frame=plan_frame, path2d=10点线性插值)` → `ActionSequence(plan_id=uuid, robot_name, actions)` → `to_msg` → `ActionSequenceMsg` → 发布到 `/ {name}/omniplanner_node/compiled_plan_out`
   - **Phoenix adaptor**（`PhoenixPlanningAdaptor`）→ `compile_plan(adaptor: Phoenix, ...)`（goto_points_ros.py:43）→ `compile_points_path` → **`nav_msgs/Path`**（header.frame_id = plan_frame，poses 含 10 点/段，z=0，orientation 恒等）→ 发布到同 topic（Path 类型）
5. **collect_plans** 按 name 汇总 → `publish_plan(to_msg(compiled_plan))` + `to_viz_msg` 发布 MarkerArray。

**关键结论**：本链路中"DSG symbol → 位置"只发生在 `goto_points.py:46-61` 的 `get_loc` 闭包（`dsg.find_node(str_to_ns_value(symbol)).attributes.position[:2]`）和 `dsg_pddl_grounding.py:314 add_symbol_positions`（PDDL 路径同样用法）。位置取的是**2D xy**。

---

## 4. Frame handling（坐标系处理）

- **统一假设**：DSG 发布在固定帧（config 里的 `fixed_frame: map`）。DSG 节点的 `attributes.position` 被直接当作"地图系坐标"使用，**plan 输出不做任何 frame 变换** —— `compile_points_path` / `compile_phoenix_pddl_plan` 把 `plan_frame`（= DSG header.frame_id）原样写进 `Path.header.frame_id` 和 `Follow(frame=...)`。
- **唯一 tf 使用点**：机器人起点。`get_robot_pose(tf_buffer, parent_frame=dsg_frame, child_frame=body_frame)`（omniplanner_node.py:151）查 `dsg_frame → body_frame` 变换，返回 `[x,y,z,yaw]`，作为 start（goto_points.py:49）或 PDDL 的 `pstart` 位置。README 明说多机器人前提是"robot<->map 变换必须存在"。
- **姿态**：整个管线不产生朝向。`Path.poses[*].pose.orientation.w = 1.0`（恒等四元数）；`get_robot_pose` 只返回 yaw 供 start 用；`Follow` 只有 `path2d`，无朝向。**没有坐标变换逻辑可抄 —— 这是本项目要补的最大空白之一**（见 §8）。
- 依赖关系：位置是 2D（`position[:2]`），z 永远 0.0。对 ANYmal（需要 3D 轨迹/朝向）不适用。

---

## 5. 可直接复用的代码（接口/数据结构）

按依赖代价从低到高：

1. **`omniplanner/utils.py:8 str_to_ns_value(s)`** —— `"O(0)"` → `NodeSymbol.value`（依赖 `parse` + `spark_dsg`）。Khronos 也是 spark_dsg，直接可用。
2. **`omniplanner.py:151 string_as_nodesymbol`** + **`pddl_utils.py:52 pddl_char_to_dsg_char`** —— PDDL 小写字符（o/p/r）与 DSG 大写字符（O/P/R）互转 + NodeSymbol 构造。直接可用。
3. **`tsp.py:17 LayerPlanner`** —— 把 DSG `MESH_PLACES` 层变成 networkx 图，提供 `get_closest_node_id/point`、`get_shortest_distance`、`get_external_distance`、`get_external_path`（外部点 → 最近 place → 图上最短路 → 目标）。**这是我们给 ANYmal 生成 place-level 路径（或用 place 图做 RAG 检索）的核心工具**，可直接 import。
4. **`goto_points_ros.py:80 compile_points_path`** —— `GotoPointsPlan → nav_msgs/Path`。几乎就是我们想要的 ANYmal 输出，只差 3D/朝向。
5. **数据结构**：`GotoPointsGoal`、`GroundedGotoPointsProblem`、`GotoPointPrimitive`、`GotoPointsPlan`、`PlanRequest` —— 结构极简，可以直接抄进本项目，或只抄形式。
6. **`compile_plan.py` 的分派契约**：`compile_plan(adaptors: dict, plan_frame: str, plan)` + `collect_plans` —— "按 robot_name 取 adaptor、按计划类型编译"的入口模式。
7. **`pddl_grounding.py` 的 `PddlSymbol` / `GroundedPddlProblem` / `PddlProblem.to_string`** —— 如果本项目还要输出 PDDL（用于任意 solver），这些可直接用。
8. **DSG 拓扑提取函数**：`dsg_pddl_grounding.py` 的 `explicit_edges_from_layer` / `implicit_edges_from_layers` / `generate_object_containment` / `generate_place_containment` —— DSG 结构 → 事实/图，与规划器无关，可直接借用做 place/object 关系图。
9. **`omniplanner_node.py:151 get_robot_pose`** —— tf 查 robot pose 的模板（含 1s 等待 + 异常处理）。

## 6. 只能借鉴的代码（模式）

1. **`plum` 动态多重分派框架本身**（`@dispatch` / `@overload` / `@dispatchable_parametric`）—— 机制精巧（README 也承认难以静态分析调用序列），但引入 `plum` + 泛型 dataclass 推断（`generic_inference`，functor.py:15）整套机制，对本项目（单一机器人、单一计划类型）过重。**借鉴其"按 (plan 类型 × robot adaptor 类型) 选编译函数"的思想即可，用普通函数表 / `match` 语句实现更简单、可静态分析**。
2. **Wrapper 栈（`RobotWrapper` / `SymbolicContext` / `MultiRobotWrapper` / `push` / `fmap`）** —— 用于在不改中间函数的前提下从管线头向尾携带元数据（robot 名、DSG 符号上下文）。单机器人且不需要语义上下文时，可退化为"计划类自带 robot_id + 上下文 dict"，不必上 Functor。
3. **`compile_plan` 对 wrapper 的"透明跳过"分派（compile_plan.py:76）** —— 干净地处理"计划外面套了几层元数据"。若我们保留 wrapper，照抄；否则可省。
4. **多机器人（`MultiRobotWrapper` + 名字重映射 + `compile_multirobot_pddl_plan`）** —— 本 bridge 若只指挥 1 台 ANYmal，不需要；多台才值得抄。
5. **`compile_plan` 的 list 子类边缘情况（compile_plan.py:22 + README 备注）** —— 若我们的计划类继承 list，必须额外实现 `compile_plan(adaptor, frame, SymbolicContext[List[Any]])`；**规避办法：不要让计划类继承 list**。
6. **运行时约束 + 增量目标管理**：`MultiRobotPddlConstrainedPlannerRos.on_plan_compiled`（multirobot_ros.py）+ `goal_manager_node.py`（缓存已访问 POI，PDDL goal 三值判定"已满足则跳过重规划"）+ `omniplanner_repair_node.py`（持久约束 topic）。**模式值得借鉴**（避免重复规划、支持 forbidden 约束），但实现强绑定 PDDL goal 语法，需重写。
7. **`on_plan_compiled` 插件钩子**（omniplanner_node.py:347-353）—— 计划发布后的派生信息回吐点（发布 visited POIs）。比在节点里硬编码更干净，值得保留。

## 7. 与本项目已完成的 LLM+RAG 重叠/不需要的部分

- **重叠**：自然语言 → symbol grounding。Omniplanner 用 `LanguageDomain` + `LanguagePddlInterface.request_plan_specification(command, dsg)` 让 LLM 输出 `{robot: pddl_goal}`（language_planner.py:26-45）。本项目的 LLM+RAG 已做了这一步（方式不同），**不需要重复**。可参考的点：feedback 把 LLM 原始响应发布给 RViz（`PluginFeedbackCollector`）。
- **不需要（或已覆盖）**：
  - PDDL + Fast Downward 求解栈（`solve_pddl` / `dsg_pddl_planning.py` / `domains/*.pddl`）—— 除非你们还想要符号规划器。
  - `nlu_interface(_dcist)`、OpenAI 配置、prompt 渲染 —— 本项目 RAG 自有一套。
  - `LanguageGoal` / `LanguageDomain` 类本身。
  - TSP 求解（`two_opt` / `solve_tsp_2opt`）—— 若订单排序不需要则省。
- **本项目必须复用/对齐的（不在重叠内）**：DSG 订阅（`hydra_ros.DsgSubscriber` 或自己写）、`spark_dsg` node symbol 查找、`node.attributes.position` 读取 —— 这是 Omniplanner 和本项目共同的地基。

## 8. 明确回答：robot-specific plan compiler 模式能否直接指导 ANYmal adaptor？

**能，直接指导，且是最好的参考；但"指导"不等于"照抄"，差距主要在几何维度与执行语义。**

### 可以原样搬的骨架
1. **"plan-agnostic 入口 + 按计划类型分派 + 按 robot 名选 adaptor"三段式**：`compile_plan(adaptors, plan_frame, plan)` → 剥 wrapper → 命中我们自己的 `compile_plan(adaptor, plan_frame, WaypointPlan)` → 产出 `nav_msgs/Path`。`PhoenixPlanningAdaptor`（omniplanner_node.py:127）+ `compile_points_path`（goto_points_ros.py:80）就是现成的 Path 版模板：adaptor 持 `robot_name` + `create_publisher(Path, ...)`，编译函数只写 `frame_id` + poses。
2. **adaptor 职责划分**：`RobotPlanningAdaptor` 的 `get_pose(parent_frame)` + `publish_plan(plan)`（omniplanner_node.py:103/113）可以直接对应 ANYmal adaptor 的"查起点位姿 + 发 Path"。
3. **机器人起点统一走 tf**（`get_robot_pose`），与计划位置同帧假设一致。
4. **抽象计划层（`GotoPointPrimitive`/`GotoPointsPlan`）与具体消息层（Path）解耦**：上层只管 `(start, goal)` 点序列，下层决定插值密度和消息类型 —— 这个分层正是 ANYmal adaptor 该有的形态。

### 差距（需要在 adaptor 里补）
1. **2D → SE(2)/3D**（最大差距）。全链只产出 xy（`position[:2]`），Path 的 z=0、orientation 恒等。ANYmal 导航要么要带 z 的 3D 轨迹（viplanner / nav-suite 输入），要么至少要航向角。需要在编译层把 `GotoPointPrimitive` 升级为带 z/yaw（或从 place 图/tangent 推算），插值从 2D 变 3D/SE(2)。`np.interp` 的写法可留，维度要换。
2. **frame 转换缺失**。Omniplanner 赌 DSG 已在 `map`。本项目必须先验证 Khronos DSG 帧与 ANYmal 执行端期望帧是否一致；不一致要在 adaptor 里加 tf（或静态变换）。
3. **执行语义缺位**。Omniplanner 只发布消息，跟踪交给外部 `RobotExecutor`。ANYmal 侧要明确消费端：纯 `nav_msgs/Path` 给谁（viplanner？navigation 栈？）、要不要 speed/stop 控制、要不要 action server 确认。`Follow(frame, path2d)` 只是占位语义。
4. **计划类型要自建**。`GotoPointsPlan` 是"点序"语义；ANYmal 可能要"waypoint + 动作标签"（如 stop-inspect）。定义自己的 dataclass 并写对应 `compile_plan` 分派即可 —— 机制通用，内容自定。
5. **坑：`GotoPointsPlan.append` 有个 bug**（goto_points.py:27 写的是 `self.plan.append(gpplan.append)`，把方法对象追加进去了）。好在整条链路没人调用 append，但照抄数据结构时不要照抄这个方法。

### 结论
**直接复用价值高**：`compile_plan`/`collect_plans` 入口、`RobotPlanningAdaptor`/`PhoenixPlanningAdaptor`/`compile_points_path` 是 ANYmal adaptor 的直接模板；`LayerPlanner`、`str_to_ns_value`、DSG 拓扑提取函数可直接 import。**需要新写**：3D/SE(2) 插值与朝向、frame 校验/变换、执行消费端对接。**建议不搬**：`plum` 多重分派、Wrapper/Functor 栈、MultiRobot、PDDL/FD 栈、LLM grounding（与本项目 RAG 重复）。**主风险**：Omniplanner 是无速度、无朝向、2D、feed-forward（README 自述 ~10s/plan 的低频规划）的体系，ANYmal 的鲁棒导航还需要把计划落到带速度/朝向/停止的路径消息上，这一步 Omniplanner 给不了。
