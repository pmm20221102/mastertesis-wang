# ViPlanner (leggedrobotics/viplanner) ROS 栈离线审计报告

- 仓库：`/home/ulmmt/Masterthesis-wang/khronos_anymal_bridge/external/navigation_references/viplanner`
- commit：`6fcf3c60f6fa3b28b3a11af054d6033825923789`（2025-02-12）
- 审计范围：`ros/` 子目录（planner / pathFollower / waypoint_rviz_plugin / visualizer）
- 链路：`WaypointTool → /mp_waypoint → viplanner_node → /viplanner/path → pathFollower → TwistStamped`

---

## 0. 一句话结论

ViPlanner 的 ROS 栈是**"单点 waypoint 输入 + 本地 5m 视野 planner + 相对式路径跟随"**架构：planner 只吃**一个** `PointStamped` 目标，输出**base 系**的短路径（50 点 @ 0.1m = 5m）；pathFollower 订阅该路径并转成 **TwistStamped**。**我们可以用自己生成的 global path 直接喂给 pathFollower（绕过 planner 和 waypoints.json），前提是：路径必须转换到 robot/base 系并以一定频率持续重发。** 详细论证见 Q10。

---

## 1. 是否可以外部发送 waypoint？用什么接口/topic/message？

**可以。** 接口就是标准 ROS topic，无私有依赖。

- **Topic**：`/mp_waypoint`（planner 的 `goal_topic` 参数，`viplanner_node.py` 默认值即 `/mp_waypoint`；`anymal_mount.yaml` 也配置为 `/mp_waypoint`）
- **Message**：`geometry_msgs/PointStamped`（只读 `point.x/y/z`，`header.frame_id` 指明坐标 frame）
- **处理逻辑**：`viplanner_node.py` 的 `goalCallback`（`ros/planner/src/viplanner_node.py:634-645`）收到后设 `is_goal_init=True, is_goal_processed=False`，随后在 `depthCallback`（L783-857）里用 tf 把 goal 变换到 robot frame 和 world frame。**支持任意输入 frame**——只要 TF tree 里能查到 `base ← goal.frame_id` 和 `odom ← goal.frame_id`。
- **注意**：一次只能给一个 waypoint，**不支持路径/序列**。要发一串 waypoint，必须逐个发送，并靠 `is_goal_processed` / `/viplanner/status` 判断上一个是否到达（见 Q6）。

参考发送端（RViz WaypointTool，`ros/waypoint_rviz_plugin/src/waypoint_tool.cpp`）：订阅 `/state_estimator/pose_in_odom`（`geometry_msgs/PoseWithCovarianceStamped`，用于取 robot z 高度），发布 `PointStamped` 到 `/mp_waypoint`。它在 `onPoseSet`（L65-100）里把点在 **odom** 系构造，再尝试 `tf.lookupTransform("map", "base")` 转成 **map** 系发布；tf 失败则退回 odom 系。也就是说 ANYmal 的 waypoint 输入惯例是 **map 系**。

---

## 2. 是否可以外部发送 path？path 怎么进 planner？

- **planner 本身不接受 path。** `viplanner_node` 只订阅 depth / rgb / camera_info / goal / joy 五类输入；path 是它的**输出**不是输入。外部无法把 path 直接"灌进 planner"。
- **但是 path 可以绕过 planner 直接进 pathFollower**（见 Q3/Q10）。`pathFollower` 订阅的 `/viplanner/path` 就是标准 `nav_msgs/Path`，任何节点都能发。
- 若**必须**走 planner 来对 path 做避障重规划，则只能"逐点喂 waypoint"：把 global path 上的点按顺序变成 `PointStamped` 发到 `/mp_waypoint`，等 `/viplanner/status` 置 1（到达）后再发下一个。

**path 是怎么产生的（内部）**：`viplanner_node.spin()`（L182-321）每 `main_freq`（anymal 配置 10 Hz）：
1. 攒齐 depth + rgb/sem 图像 + goal；
2. `goalProjection()`（L325-377）把 goal 投影到深度相机系（robotics 约定）；
3. `VIPlannerInference.plan()/plan_depth()`（`vip_inference.py:87-133`）跑网络得到 keypoints，再用 `TrajGeneratorFromPFreeRot(preds, step=0.1)`（`viplanner/traj_cost_opt/traj_opt.py:47-92`）插值出 **50 点、间距 0.1m、总长 5m** 的轨迹；
4. `pubPath()`（L508-583）发布。

---

## 3. path follower 订阅什么 topic / message？订阅的 path 格式？

**Topic：`/viplanner/path`——硬编码，不是 ROS 参数**（`ros/pathFollower/src/pathFollower.cpp:251`）：
```cpp
ros::Subscriber subPath = nh.subscribe<nav_msgs::Path> ("/viplanner/path", 5, pathHandler);
```
若想换 topic，只能改源码或用 ROS remap（`/viplanner/path:=/my_path`）。

**Message：`nav_msgs/Path`**，`pathHandler`（L148-164）**只拷贝 `pose.position.x/y/z`**（忽略 orientation、header.frame_id）。因此：

- 格式要求：`nav_msgs/Path`，至少 2 个 pose，每个 pose 只需 position 有效；
- **frame 约定（关键）**：follower 把收到的 path 点当作"**相对当前机器人位姿的坐标**"处理。它在收到 path 那一刻记录当前 odom pose（`vehicleXRec/YRec/YawRec`，L153-157），之后每帧计算机器人相对位移 `vehicleXRel/YRel`（L277-278，把 odom 增量旋进记录的 yaw 系），再用 `path.poses[i] - (vehicleXRel, vehicleYRel)` 求距离。**所以 path 点必须表达在"发布时刻的机器人坐标系"（即 base 系），不能是 odom/map 绝对坐标**。ViPlanner 发布在 `robot_id`（默认 `base`，anymal 配置 `base_inverted`）系，正好满足；如果自己发 map 系绝对坐标，follower 算出的距离是错的。
- **注意**：`pathHandler` 收到新 path 会重置 `pathPointID=0` 并重新记录基准位姿（L161-163），即**每条新 path 重新开始跟踪**。

---

## 4. 输出什么 twist topic/message？

- **Message：`geometry_msgs/TwistStamped`**（不是 `Twist`！`pathFollower.cpp:15,259,261`）。
- **Topic**：由 `commandTopic` 参数控制。
  - C++ 默认值：`/cmd_vel`（`pathFollower.cpp:100`）；
  - ANYmal launch 实际配置：`/local_guidance_path_follower/twist`（`ros/pathFollower/launch/path_follower.launch:4`）。
- 填充内容：`cmd_vel.header.frame_id = baseFrame`（默认 `base`）；`twist.linear.x`（经 `maxSpeed`/`autonomySpeed` 缩放的速度，`baseInverted` 时取反）；`twist.angular.z`（yaw rate，rad/s，封顶 `maxYawRate`）。只在 `pubSkipCount` 计数时发布（`pubSkipNum`，默认 1，即每帧都发）。速度由 `maxAccel` 做梯形平滑（L362-368）。
- 辅助输入：`/joy`（`sensor_msgs/Joy`，切 autonomyMode 与手动速度）、`/speed`（`std_msgs/Float32`，autonomy 下覆盖目标速度）、`/stop`（`std_msgs/Int8`，`>=1` 刹停、`>=2` 再锁 yaw）。

---

## 5. frame 约定：map/odom/base 怎么配？路径输入期望什么 frame？

配置点：

| 项 | 值 | 位置 |
|---|---|---|
| planner `robot_id` | `base`（默认）/ `base_inverted`（anymal_mount.yaml） | `viplanner_node.py:992` |
| planner `world_id` | `odom`（默认） | `viplanner_node.py:993` |
| planner `mount_cam_frame` | `wide_angle_camera_rear_camera_parent`（anymal，相机不在 TF 树内） | `anymal_mount.yaml` |
| pathFollower `baseFrame` | `base` | `path_follower.launch:5` |
| pathFollower `odomTopic` | `/state_estimator/pose_in_odom`（`PoseWithCovarianceStamped`） | `path_follower.launch:3` |
| pathFollower `baseInverted` | `false`（anymal 配置 `robot_id=base_inverted` 时此参数与之配合，`viplanner.launch` 注释说明"也需调整 path_follower.launch"） | `path_follower.launch:14` |

- **planner 的路径输出**：`pubPath()`（L508-583）同时发三份——
  - `/viplanner/path`（`base_path`）：`header.frame_id = robot_id`（base 系），**follower 用的就是这份**；
  - `/viplanner/path_viz`（`odom_path`）：`header.frame_id = world_id`（odom 系），仅可视化；
  - `/viplanner/path_fear`：fear 触发时只发 1 个 pose（`base_path.poses = poses[:1]`，L571-575），迫使 follower 停车。
- **frame 层级**：依赖 ANYmal 状态估计提供的 TF 树 `map → odom → base → 相机系`。WaypointTool 里 `tf.lookupTransform("map", "base")` 表明 `map` 帧存在且可用。planner 的 goal 变换、相机位姿都走 `tf_buffer`。
- **pathFollower 期望的 path frame**：**robot/base 系、相对于"发布时刻机器人位姿"**（见 Q3）。它不读 header，不查 tf。

---

## 6. goal reached 如何判断？完成条件逻辑在哪（文件:行号）？

有两层"完成"判断，含义不同：

1. **planner 层（goal reached）**——`viplanner_node.py:271-282`：
   - 条件：goal 投影到深度相机系后的平面距离 `sqrt(goal_cam_frame[0][0]^2 + goal_cam_frame[0][1]^2) < conv_dist`（默认 0.5，anymal 配置 `conv_dist: 0.5`）**且** `is_goal_processed`（已收到过 depth 帧）**且**非 smart-joystick 模式；
   - 动作：`planner_status.data = 1`，发布到 `/viplanner/status`（`std_msgs/Int16`），log "Goal Arrived"（L282）。`-1` 表示 fear 失败路径（L292-294）。
   - 这是**上游用来推进 waypoint 序列的信号**：外部发 waypoint 序列时，应订阅 `/viplanner/status`，读到 1 再发下一个；读 0 表示规划中/等待新 goal。

2. **pathFollower 层（停车逻辑）**——`pathFollower.cpp`：
   - `pathSize <= 1` → 速度清零（L352-353 `if (pathSize <= 1) joySpeed2 = 0;`），yaw 在 `noRotAtGoal=true` 时也清零（L348）；
   - 距离终点 `endDis < slowDwnDisThre`（launch 配置 0.3）时按比例减速（L354-356）；
   - `dis < stopDisThre`（launch 配置 0.4）且 `noRotAtGoal` → 停止旋转（L348）；
   - 没有显式发布"到达"消息——follower 到达后只是保持静止、持续发 `linear.x≈0` 的 TwistStamped。
   - 额外停机制：`/stop`≥1 立即刹停（L369）、倾角超限 `useInclToStop` 停 `stopTime`（L366-368）。

**结论**：外部驱动方（bridge）应把 `/viplanner/status == 1` 当作"到达当前 waypoint、可发下一个"；绕开 planner 直接发 path 时，到达判定需自己算（例如 `endDis`/`pathSize` 语义或看 `linear.x≈0`）。

---

## 7. ROS 节点清单（ASCII 图）

```
                    ┌────────────────────────────────────────────────────────┐
                    │                  ANYmal 私有状态估计栈                    │
                    │  TF: map→odom→base   /state_estimator/pose_in_odom      │
                    │            /depth_cam_mounted_front/{depth,color}/...    │
                    └──────────────┬──────────────┬──────────────┬─────────────┘
                                   │              │              │
        WaypointTool (RViz 插件)    │              │              │
   pub: /mp_waypoint (PointStamped) │              │              │
        /joy (sensor_msgs/Joy, 假  )│              │              │
   sub: /state_estimator/pose_in_odom              │              │
        └──────────────┬───────────┘               │              │
                       ▼                            │              │
        ┌───────────────────────────┐               │              │
        │ viplanner_node (Python)   │               │              │
        │ sub: /mp_waypoint        │               │              │
        │      /joy                │               │              │
        │      depth Image (10Hz)  │◄──────────────┘              │
        │      rgb CompressedImage │◄─────────────────────────────┘
        │      {depth,rgb}_camera_info                            │
        │ pub: /viplanner/path     (nav_msgs/Path, base 系) ────┐ │
        │      /viplanner/path_viz (Path, odom 系)              │ │
        │      /viplanner/path_fear(Path)                       │ │
        │      /viplanner/status   (Int16, 0/1/-1)              │ │
        │      /viplanner/timer, /viplanner/m2f_timer (Float32) │ │
        │      /viplanner/sem_image/compressed                  │ │
        │      viplanner/visualization/{crop_goal,odom_circle,  │ │
        │                             goal_line}                │ │
        └───────────────────────────┘                           │ │
                                                               ▼ │
        ┌─────────────────────────────────────────────┐         │
        │ pathFollower (C++, anymal 用 path_follower)  │         │
        │ sub: /viplanner/path (硬编码, nav_msgs/Path)◄┴─────────┘
        │      odomTopic=/state_estimator/pose_in_odom │
        │           (PoseWithCovarianceStamped)        │
        │      /joy (sensor_msgs/Joy)                  │
        │      /speed (Float32), /stop (Int8)          │
        │ pub: commandTopic=/local_guidance_path_follower/twist
        │           (geometry_msgs/TwistStamped)       │
        └───────────────┬─────────────────────────────┘
                        ▼
              ANYmal 执行器 / 状态估计
```

可选节点（launch 开关）：
- **visualizer**（`viplannerViz.cpp`）：每相机一个节点，sub 图像/CameraInfo/`/viplanner/path`/`/mp_waypoint`，pub `/viplanner/viz_path_*`。仅可视化。
- **joystick_drivers**（ps3joy / joy）：`enable_smart_joystick=true` 时启动，给 `/joy` 提供真实手柄输入。

**launch 结构**（`ros/planner/launch/viplanner.launch`）：一个 launch 里 `path_follower.launch`（`use_path_follow=true` 默认）＋`viplanner_node`＋可选 viz/joy。任何节点都不读任何 JSON 文件。

---

## 8. planner 和 pathFollower 的职责边界

| | viplanner_node（planner） | pathFollower |
|---|---|---|
| 输入 | goal（PointStamped）+ depth/rgb 图像 + 内参 + tf | `/viplanner/path` + odom + 可选 joy/speed/stop |
| 核心任务 | 语义分割(M2F) + ViPlanner 网络 + 轨迹插值，**生成 5m 本地避障路径** | **纯几何路径跟踪**：look-ahead 选点、yaw 跟踪、速度/减速度曲线、双向驱动、安全停车 |
| 输出 | `/viplanner/path`（base 系 Path）+ 状态 | TwistStamped 速度指令 |
| 视野 | 50 点 / 5m，10 Hz 刷新 | 无感知，只跟点 |
| 不做什么 | 不输出速度、不做动力学平滑 | 不感知、不规划、不知道 goal 全局语义 |

即：**planner 是"感知→轨迹生成"，pathFollower 是"轨迹→速度指令"**。两者通过 `/viplanner/path` 解耦，这是整个 bridge 可以插入/绕过的关键接缝。

---

## 9. 关键 class / function（文件:行号、输入输出）

| 组件 | 位置 | 输入 | 输出 |
|---|---|---|---|
| `VIPlannerNode.__init__` | `ros/planner/src/viplanner_node.py:56-180` | ROS 参数 cfg | 建立全部 pub/sub |
| `VIPlannerNode.spin` | `viplanner_node.py:182-321` | — | 主循环，10Hz 调 plan + pubPath |
| `VIPlannerNode.goalCallback` | `viplanner_node.py:634-645` | `PointStamped` | 置 `is_goal_init=True`，重置状态 |
| `VIPlannerNode.depthCallback` | `viplanner_node.py:761-862` | depth `Image` | goal 变换（robot/world frame）、算 `cam_offset/cam_rot`、置 ready |
| `VIPlannerNode.goalProjection` | `viplanner_node.py:325-377` | goal robot/world frame + depth pose | goal 投影到相机系（torch.Tensor [1,3]）；超 `max_goal_distance` 时裁剪并发 marker |
| `VIPlannerNode.pubPath` | `viplanner_node.py:508-583` | waypoints np.ndarray (N,3)（base 系）| 三份 `nav_msgs/Path`：base/odom/fear；base 系 poses 直接拷入，odom 系用 tf 变换；滤掉身后点 |
| goal reached 判定 | `viplanner_node.py:271-282` | goal_cam_frame + flags | `/viplanner/status` = 1 |
| `VIPlannerInference.plan / plan_depth` | `ros/planner/src/vip_inference.py:87-133` | depth np.ndarray, (rgb/sem), goal (torch [1,3]) | `(traj np.ndarray (50,3), fear)` |
| `TrajGeneratorFromPFreeRot` | `viplanner/traj_cost_opt/traj_opt.py:47-92` | keypoints (N,k,3), step=0.1 | 50 点轨迹（out_shape=[50,3]） |
| `ROSArgparse` | `ros/planner/utils/rosutil.py:13-30` | rosparam 前缀 | 把 yaml 参数绑定成 attributes |
| `WaypointTool::onPoseSet` | `ros/waypoint_rviz_plugin/src/waypoint_tool.cpp:65-100` | RViz 鼠标点击 (x,y,theta) | 发 `/mp_waypoint`(PointStamped, map 系) + 假 `/joy`(axes[2]=-1 → autonomyMode) |
| `pathHandler` | `ros/pathFollower/src/pathFollower.cpp:148-164` | `nav_msgs/Path` | 拷 position、记基准位姿、`pathPointID=0` |
| `odomHandler`（两个重载）| `pathFollower.cpp:105-146` | `PoseWithCovarianceStamped` / `Odometry` | 更新 vehicle 位姿/速度 |
| 主控制循环 | `pathFollower.cpp:265-388` | path + odom + joy/speed/stop | `TwistStamped` 发布（`/cmd_vel` 或 `/local_guidance_path_follower/twist`）|
| `VIPlannerViz` | `ros/visualizer/src/viplannerViz.cpp:85-109` | img, cam_info, `/viplanner/path`, `/mp_waypoint` | `/viplanner/viz_path_*` 投影可视化 |

---

## 10. 明确回答：能否直接接 path、绕过 ANYmal 私有 waypoints.json？

**结论：可以——路径层面完全能绕过 ViPlanner planner 和 waypoints.json，直接接 pathFollower；waypoint 层面也能逐个绕过。但有两个现实约束：base 系 + 持续刷新，和一个开放性依赖：TwistStamped 是否直接是 ANYmal 执行器的最终 cmd。**

### 10.1 我们已有 Khronos PLACES 产生的 global waypoint sequence / Path，接法

**方案 A（推荐，最薄）：global Path → `/viplanner/path`，绕过 planner**
- pathFollower 订阅的就是标准 `nav_msgs/Path`（topic 硬编码 `/viplanner/path`，可 ROS remap），且它**只跟点、不感知、不查 tf**。发布任意来源的 path 它都会执行。
- **必须满足**：
  1. **frame**：路径点必须是"base/robot 系、相对发布时刻机器人位姿"的坐标（见 Q3/Q5）。map 系 global path 需要先用 `map→odom→base` tf 变换成 base 系再发。follower 会把每条新 path 重置跟踪起点（L161-163）。
  2. **刷新率**：follower 是相对式跟踪——机器人一移动，base 系就变。一条 path 只发一次会因坐标系漂移越跟越偏。ViPlanner 本身 10Hz 重发。**桥接节点应每控制周期（≥10Hz）把 global path 重新变换到当前 base 系重发**，且只发送机器人前方的部分（可仿照 `pubPath` L551-558 的"滤掉已过点"）。
  3. **终点语义**：follower 到点只停车、不发完成消息；`pathSize<=1` 会强制停车。若需要"到达反馈"，要么自己算（监听输出的 TwistStamped 判断 `linear.x≈0`），要么保留 planner 用 `/viplanner/status`。
- **这条路径完全不依赖 waypoints.json，也不依赖 ANYmal 的 global planner。**

**方案 B：global waypoint 逐个喂 `/mp_waypoint`（保留 ViPlanner 避障）**
- 把 sequence 里每个 waypoint 依次发 `PointStamped`（map 系即可，planner 自动变换），订阅 `/viplanner/status`，值=1 时发下一个。
- 代价：要跑 M2F + ViPlanner 网络（依赖深度/RGB 图像与内参、GPU），且 planner 的 5m 本地视野意味着高频取点。
- 同样不碰 waypoints.json。

### 10.2 差距与依赖清单

| # | 差距 / 依赖 | 说明 |
|---|---|---|
| 1 | **frame 变换** | 必须有 ANYmal 的 TF 树 `map→odom→base`（+ 相机系）。状态估计由 ANYmal 私有栈提供；bridge 侧需要有 `map→odom`（我们自己的 SLAM/全局定位提供）和 `odom→base`（ANYmal 提供）。这是**唯一绕不开的运行时依赖**。 |
| 2 | **path 需 base 系 + 持续重发** | 不能直接把 map 系绝对 Path 一次性丢给 follower；需要一个小桥接节点做坐标变换与重发布（无现成节点，需自写）。 |
| 3 | **到达判定缺失** | 绕过 planner 后没有 `/viplanner/status`；follower 不发完成消息，需要桥接节点自己判断终点。 |
| 4 | **速度语义** | follower 输出 `geometry_msgs/TwistStamped`，且用 `autonomySpeed`（launch 默认 1.5 m/s）作为目标速度、`/speed` 可覆盖。需要确认 ANYmal 侧消费 `/local_guidance_path_follower/twist` 的接口就是这份 TwistStamped（桥接落地前的验证点）。 |
| 5 | **waypoints.json 无关性** | 审计确认整个 ViPlanner ROS 链路（viplanner_node / pathFollower / WaypointTool）**不读任何文件**；waypoints.json 只是 ANYmal 私有 global planner 的产物，与 ViPlanner 无耦合。绕开 ANYmal global planner 即可完全绕开它。 |

### 10.3 底线

**"能否直接接" = 能。** 唯一结构性要求是：**桥接节点负责把 Khronos 的 global path（map 系）变换为 base 系、按 ≥10Hz 持续重发到 `/viplanner/path`，并自行判定终点**。pathFollower 其余输入（odom `/state_estimator/pose_in_odom`、`/speed`、`/stop`）都可从 ANYmal 栈/桥接侧补齐。若想保留 ViPlanner 的本地避障，则走方案 B（逐 waypoint + `/viplanner/status` 推进），同样不需要 waypoints.json。
