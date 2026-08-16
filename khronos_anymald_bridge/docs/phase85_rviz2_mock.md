# Phase 8.5 — RViz2 Mock Navigation

> **Phase 8.5 产出**（2026-08-10，基于 ROS2 **jazzy**）
> 新增 ROS2 package：`khronos_anymald_rviz/`（工作站，已 colcon build）
> 目标：Khronos map + waypoint graph + semantic target + route + moving mock ANYmal 同屏

---

## 1. Package 结构

```
khronos_anymald_rviz/
├── package.xml / setup.py / setup.cfg / resource/
├── config/anymal_mock.rviz           RViz2 配置（Fixed Frame=map + 全部 display）
├── launch/mock_navigation.launch.py  启动 graph_publisher + mock_robot_node（可选 rviz）
└── khronos_anymald_rviz/
    ├── data_loader.py       加载 generated/ + marker 辅助（z 投影 ground_z）
    ├── graph_publisher.py   静态 waypoint 图发布
    └── mock_robot_node.py   规划 + witness 展开 + 20-30Hz 插值移动
```

## 2. 节点与 Topics

| 节点 | Topic | 内容 |
|---|---|---|
| graph_publisher | `/anymald/waypoints/nodes` | MarkerArray，261 waypoint nodes（SPHERE 绿） |
| | `/anymald/waypoints/edges` | Marker LINE_LIST，624 稀疏边（蓝） |
| | `/anymald/room_anchors` | MarkerArray，22 room anchors（红 + R 标签） |
| | `/anymald/waypoints/stretch_edges` | MarkerArray，**stretch 分级**（默认开关 `--publish-stretch`） |
| mock_robot_node | `/anymald/route` | **nav_msgs/Path，witness 展开**（106 poses） |
| | `/anymald/route_marker` | Marker LINE_STRIP route |
| | `/anymald/goal_marker` | 当前 goal（红 SPHERE） |
| | `/anymald/mock_robot` | CUBE（机身）+ ARROW（朝向），25Hz 插值 |
| | `/anymald/state` | String：IDLE/MOVING/REACHED/NO_PATH + pos/yaw/进度 |

所有 frame_id = map；graph_publisher 用 TRANSIENT_LOCAL（latch）。

## 3. stretch 分级可视化（audit §31 要求）

`--publish-stretch` 启用，颜色区分（`graph_publisher.py`）：
- 绿 `<=1.5` 可直走
- 黄 `(1.5, 2]`
- 橙 `(2, 5]`
- 红 `>5` 高风险（跨墙/跨走廊）

实测 624 边：绿 370 / 黄 108 / 橙 132 / 红 14（>5）。

## 4. Headless 验证（容器内，无显示器）

```
colcon build --packages-select khronos_anymald_rviz   # 干净通过
graph_publisher: topics 全部发布, stretch edges 1248
mock_robot_node:  start=R0(wp4275) goals=['R12','R4','R9'] state=MOVING
                  → state=REACHED pos=(-1.50,24.50,0.00) yaw=101 s=169.9/169.9m
                  → z=0.00（ground projection）
route Path poses=106（witness 展开：12 waypoints → 106 dense 点）
mock_robot 插值 25Hz（timer 1/rate，cube+arrow 各 25Hz）
```

状态机验证：MOVING → REACHED（speed 50 m/s 快速走完 169.9m 验证）。

## 5. 使用说明

**Headless（验证 publisher）**：
```bash
docker exec mt-v6 bash -c "
cd /home/ulmmt/Masterthesis-wang/khronos_anymald_bridge
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 run khronos_anymald_rviz graph_publisher --base-dir \$PWD --publish-stretch &
ros2 run khronos_anymald_rviz mock_robot_node --base-dir \$PWD --start-room 0 --goal-rooms 12,4,9
"
```

**有显示器（RViz2 可视化 + 与 Khronos mesh 同屏）**：
```bash
# 在有显示器的机器（或 X11 转发）：
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 launch khronos_anymald_rviz mock_navigation.launch.py \
    base_dir:=/home/ulmmt/Masterthesis-wang/khronos_anymald_bridge
# RViz2 加载 config/anymal_mock.rviz（Fixed Frame=map）
# 与 Khronos 现有 RViz2（mesh/DSG）用同一 map frame 同屏
```

## 6. 需求对照（Phase 8.5 要求 1-17）

| # | 要求 | 状态 |
|---|---|---|
| 1 | 新建 ROS2 package | ✅ `khronos_anymald_rviz`（jazzy） |
| 2 | MarkerArray waypoint nodes | ✅ |
| 3 | LINE_LIST sparse edges | ✅ |
| 4 | Room anchor MarkerArray | ✅ |
| 5 | nav_msgs/Path route | ✅ |
| 6 | route 展开 witness path | ✅（106 poses） |
| 7 | mock robot Marker | ✅ CUBE + ARROW |
| 8 | 20-30Hz 插值 | ✅ 25Hz |
| 9 | yaw 按运动方向 | ✅ atan2 |
| 10 | mock z = ground z=0 标记 | ✅ z=0.00 |
| 11 | goal marker + state | ✅ |
| 12 | IDLE/MOVING/REACHED/NO_PATH | ✅ 验证 |
| 13 | launch + RViz config | ✅ |
| 14 | headless topic echo/hz 验证 | ✅ |
| 15 | 有显示器人工验证与 mesh 重合 | ⏳ 需用户本地 RViz2（提供 launch/config） |
| 16 | stretch 高风险开关（>1.5/2/5） | ✅ `--publish-stretch` |
| 17 | demo screenshot/trace 后停止 | ✅ trace 已记录，screenshot 需 RViz2 环境 |

## 7. 边界

- 未进入 ANYmal D dynamics / Isaac / real robot（按要求）
- mock z = ground projection（`z_source: projected_for_mock`），非真机 z
- route cost 用 witness geodesic（沿真实拓扑，不穿墙）
