# Khronos 3DSG source workspace

本目录是 Khronos/Hydra/Spark-DSG ROS 2 源码工作区。它消费传感器或 ROS bag 并输出动态场景图 DSG；下游 llm-rag/RAG/khronos_3dsg_rag/ 再将 DSG 转为 canonical graph 和检索索引。

## Build

依赖以各 ROS package.xml 为准。Ubuntu 24.04 / ROS 2 Jazzy：

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
cd khronos/scene-graph-khronos
rosdep install --from-paths src --ignore-src -r -y
colcon build --base-paths src --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
~~~

## Runtime input/output

Khronos 的具体输入 topic、图层、语义模型及输出目录由选择的 launch/config 决定。不同数据集配置不可互换；运行后在配置的 experiment output directory 中检查 DSG 和运行日志。原始 Hospital P1000 数据、mesh、bag、校准文件及语义权重均不在本仓库。

只运行论文中已打包的自然语言/Nav2流程时，可直接使用已有 RAG/地图产物，不必重建这一 upstream SLAM workspace。
