# Khronos Source and Input Adapters

This module contains two components:

- `scene-graph-khronos/`: ROS 2 source for Khronos, Hydra, Spark-DSG, and related packages, used to generate dynamic scene graphs from sensors or bags.
- `tartanground-tools/`: input adapter that converts TartanGround data into ROS 2 bags.

The normal thesis Nav2 workflow uses the archived Hospital-DSG-derived RAG index and packaged maps; Khronos perception does not need to be rebuilt for each launch. Build this module only when regenerating a DSG from raw data. The original Hospital data, meshes, calibration files, and models are not included in the repository.

## Khronos workspace

Dependencies are declared in the `package.xml` files under `src/`. See [BUILD_ENVIRONMENT.md](BUILD_ENVIRONMENT.md) for the native Ubuntu 24.04/Jazzy setup and optional downloader environment. The bundled navigation demo skips this Khronos workspace entirely. From the repository root:

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
rosdep update
rosdep install --from-paths khronos/scene-graph-khronos/src --ignore-src -r -y
cd khronos/scene-graph-khronos
colcon list --base-paths src
CMAKE_BUILD_PARALLEL_LEVEL=2 MAKEFLAGS=-j2 colcon build \
  --base-paths src --executor sequential \
  --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
  -DSEMANTIC_INFERENCE_USE_TRT=OFF
source install/setup.bash
~~~

The reference closed-set path reads semantic images from the bag, so TensorRT and online segmentation weights are unnecessary. The default Hospital launch and RAG builder share the 109-class fine-semantic mapping plus Unknown (0). Calibration assumptions are defined in the TartanGround adapter source; a separate missing calibration file is not required for this profile. A new run may produce different nodes and maps from the historical thesis run.

After bag playback ends, request `/finish_mapping_and_save` and wait for it to return before stopping the launch. Select the actual timestamped experiment directory and verify nonempty `dsg.json` and `mesh.ply` before building RAG/maps. The complete commands are in [the reproduction guide](../experiment/CODE_REPRODUCTION.md).

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
