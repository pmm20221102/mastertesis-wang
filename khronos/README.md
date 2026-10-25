# Khronos 源码与输入适配

本模块包含两部分：

- scene-graph-khronos/：Khronos、Hydra、Spark-DSG 等 ROS 2 源码，用于从传感器或 bag 生成动态场景图。
- tartanground-tools/：TartanGround 到 ROS 2 bag 的输入适配器。

正常论文 Nav2 运行使用已归档的 Hospital DSG 派生 RAG index 和打包地图，不必在每次启动时重建 Khronos 感知。只有从原始数据重新生成 DSG 时才需要编译本模块。Hospital 原始数据、mesh、校准和模型未随仓库提交。

## Khronos workspace

依赖由 src 下各 package.xml 声明。Ubuntu 24.04 / ROS 2 Jazzy 环境中从仓库根目录执行：

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
rosdep update
rosdep install --from-paths khronos/scene-graph-khronos/src --ignore-src -r -y
cd khronos/scene-graph-khronos
colcon list --base-paths src
colcon build --base-paths src --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
~~~

具体传感器、语义模型和数据集配置位于对应 ROS package 的 launch/config 目录。选定的 TartanGround launch 还需要本地数据集和传感器校准，不能只靠源码编译得到论文 DSG。

## TartanGround ROS adapter

ROS package 位于 tartanground-tools/tools/tartanground_khronos_bridge。依赖由 package.xml/setup.py 声明；输入为本地 TartanGround 数据集、pose、标签映射和相机配置，输出为 ROS 2 bag。

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
rosdep install --from-paths khronos/tartanground-tools/tools/tartanground_khronos_bridge --ignore-src -r -y
cd khronos/tartanground-tools
colcon build --base-paths tools/tartanground_khronos_bridge
source install/setup.bash
ros2 run tartanground_khronos_bridge tartanground_to_ros2bag --help
~~~

该 adapter 只负责数据转换，不执行导航；Hospital Nav2 任务接口位于 khronos-nav2-bridge/。
