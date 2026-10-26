# Khronos Source and Input Adapters

This module contains two components:

- `scene-graph-khronos/`: ROS 2 source for Khronos, Hydra, Spark-DSG, and related packages, used to generate dynamic scene graphs from sensors or bags.
- `tartanground-tools/`: input adapter that converts TartanGround data into ROS 2 bags.

The normal thesis Nav2 workflow uses the archived Hospital-DSG-derived RAG index and packaged maps; Khronos perception does not need to be rebuilt for each launch. Build this module only when regenerating a DSG from raw data. The original Hospital data, meshes, calibration files, and models are not included in the repository.

## Khronos workspace

Dependencies are declared in the `package.xml` files under `src/`. From the repository root on Ubuntu 24.04 / ROS 2 Jazzy:

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

Sensor, semantic-model, and dataset settings are defined in each ROS package's `launch/` and `config/` directories. The selected TartanGround launch also requires a local dataset and sensor calibration; compiling the source alone cannot reproduce the thesis DSG.

## TartanGround ROS adapter

The ROS package is at `tartanground-tools/tools/tartanground_khronos_bridge`. Dependencies are declared in `package.xml` and `setup.py`. Inputs are the local TartanGround dataset, poses, label mapping, and camera configuration; output is a ROS 2 bag.

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
rosdep install --from-paths khronos/tartanground-tools/tools/tartanground_khronos_bridge --ignore-src -r -y
cd khronos/tartanground-tools
colcon build --base-paths tools/tartanground_khronos_bridge
source install/setup.bash
ros2 run tartanground_khronos_bridge tartanground_to_ros2bag --help
~~~

This adapter only converts data; it does not perform navigation. The Hospital Nav2 task interface is in `khronos-nav2-bridge/`.
