# Hospital P1000 Nav2 bridge

本模块提供唯一的 Hospital/Nav2 ROS 2 package，包路径为 khronos_nav2_bridge/。它消费 mission JSON 与 floor bundle，使用 Nav2 对有序停靠点规划；集成任务链的自然语言入口见仓库根目录 README.md 和 llm-rag/plan_hospital_nav2.sh。

## Dependencies and build

ROS 2 Jazzy、Nav2、RViz2、colcon 和 rosdep。Nav2/ROS 包依赖由 khronos_nav2_bridge/package.xml 声明，Python 系统包由 ROS/rosdep 提供。完整依赖见 llm-rag/BUILD_ENVIRONMENT.md。

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
rosdep install --from-paths khronos-nav2-bridge/khronos_nav2_bridge --ignore-src -r -y
cd khronos-nav2-bridge
colcon build --packages-select khronos_nav2_bridge --symlink-install
source install/setup.bash
~~~

## Mission interface

底层 runner 的输入是一个 mission JSON、楼层地图 bundle 和输出 JSON 路径；robot-model 必须和启动的 Nav2 profile 一致。默认执行模式会向 Nav2 action server 发送目标。

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
/usr/bin/python3 khronos-nav2-bridge/khronos_nav2_bridge/scripts/run_task_chain_nav2.py \
  --mission experiment/nav2-49/missions/HC-001.mission.json \
  --bundle khronos-nav2-bridge/khronos_nav2_bridge/config/hospital_p1000_floor_transitions.yaml \
  --output /tmp/HC-001-result.json \
  --plan-only \
  --robot-model point
~~~

--plan-only 仅规划，不发送 NavigateToPose；执行模式只能用于隔离模拟 ROS domain，绝不可指向真实机器人 domain。runner 输出包含任务状态、停靠点顺序、地图/路径信息和错误阶段。输入格式及其实际使用方式见：

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
/usr/bin/python3 khronos-nav2-bridge/khronos_nav2_bridge/scripts/run_task_chain_nav2.py --help
~~~

地图 PNG/YAML 与哈希位于 khronos_nav2_bridge/generated/hospital-p1000-khronos-only/。重建源代码在 experiment/map-generation/，原始 Hospital DSG/mesh 不随仓库提供。包内详细 Hospital launch 说明见 khronos_nav2_bridge/README_HOSPITAL.md。
