# Khronos 3DSG source workspace

This directory is the Khronos/Hydra/Spark-DSG ROS 2 source workspace. It consumes sensor streams or ROS bags and produces dynamic scene graphs (DSGs). The downstream `llm-rag/RAG/khronos_3dsg_rag/` module converts a DSG into a canonical graph and retrieval index.

## Build

Dependencies are defined by each ROS package's `package.xml`. On Ubuntu 24.04 / ROS 2 Jazzy:

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
cd khronos/scene-graph-khronos
rosdep install --from-paths src --ignore-src -r -y
colcon build --base-paths src --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
~~~

## Runtime input/output

Khronos input topics, graph layers, semantic models, and output directories depend on the selected launch/configuration. Dataset configurations are not interchangeable. After a run, inspect the configured experiment output directory for the DSG and logs. Original Hospital P1000 data, meshes, bags, calibration files, and semantic weights are not included in this repository.

To run only the packaged natural-language/Nav2 workflow described in the thesis, use the existing RAG/map artifacts; rebuilding this upstream SLAM workspace is unnecessary.
