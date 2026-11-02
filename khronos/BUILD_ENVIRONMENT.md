# Khronos build environment

## Scope

This workspace contains the ROS 2 port of Khronos and its vendored source dependencies. The supported reference path here is Ubuntu 24.04 / ROS 2 Jazzy with dataset-provided closed-set semantic images. CUDA, TensorRT, online segmentation weights, Docker, and the original workstation's absolute paths are not required for that path.

The bundled GUI path skips this entire workspace. It only builds the separate Nav2 bridge described in the root README.

## System setup

Install [ROS 2 Jazzy Desktop and ros-dev-tools](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html), then run from the repository root:

```bash
sudo apt install build-essential cmake git python3-venv
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then sudo rosdep init; fi
rosdep update
rosdep install --from-paths khronos/scene-graph-khronos/src --ignore-src -r -y
rosdep check --from-paths khronos/scene-graph-khronos/src --ignore-src
```

`package.xml` files declare the ROS/system dependencies. Vendored dependencies such as Hydra, Spark-DSG, config_utilities, Kimera-PGMO/RPGO, and TEASER++ are built from this checkout; do not replace them with unrecorded source directories from another workspace.

## Build Khronos

A sequential package build with two compiler workers limits memory pressure. Run inside the workspace so colcon output stays there:

```bash
cd khronos/scene-graph-khronos
CMAKE_BUILD_PARALLEL_LEVEL=2 MAKEFLAGS=-j2 colcon build \
  --base-paths src --executor sequential \
  --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
  -DSEMANTIC_INFERENCE_USE_TRT=OFF
source install/setup.bash
ros2 pkg executables khronos_ros
cd ../..
```

The executables include `khronos_node`. TensorRT is explicitly disabled because labels arrive in the bag; this does not disable closed-set graph construction. A different online semantic frontend needs its own tested dependencies and configuration.

## Build the sensor/bag adapter

```bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
rosdep install --from-paths khronos/tartanground-tools/tools/tartanground_khronos_bridge --ignore-src -r -y
cd khronos/tartanground-tools
colcon build --base-paths tools/tartanground_khronos_bridge --symlink-install
source install/setup.bash
ros2 run tartanground_khronos_bridge tartanground_to_ros2bag --help
cd ../..
```

Source both overlays when launching the mapper: Jazzy, then the Khronos workspace, then the TartanGround adapter workspace. The launch uses package-share paths for every runtime configuration.

## Label-space contract

The default `tartanground_hospital_khronos.launch.yaml` uses `tartanground_hospital_p1000_fine_label_space.yaml` and the corresponding fine remap. The map contains Unknown (0) plus 109 categories; examples are 1=door, 5=cabinet, 72=chair. The RAG builder uses the same portable mapping and records its SHA-256. The older 14-class files remain as legacy assets and are not the default full workflow.

Run the adapter regression checks together with the Python tests. The cross-module test checks that mapper and RAG mappings agree, not merely that both have a file with the same name.

## Input data and downloader

The raw sequence is external. Existing extracted data can be used directly after the adapter audit. For a fresh download, initialize the pinned SDK and its nested submodules:

```bash
git submodule update --init --recursive khronos/tartanground-tools/tools/tartanairpy
```

The SDK is pinned by the gitlink to `158a6844d782942110967325ca3082f50ab2bfc7`. Its own `pyproject.toml` declares its dependencies. Use a dedicated downloader environment because the upstream SDK includes visualization/CUDA dependencies that are not part of the planner or closed-set mapper:

```bash
python3 -m venv khronos/tartanground-tools/.download-venv
khronos/tartanground-tools/.download-venv/bin/python -m pip install --upgrade pip
khronos/tartanground-tools/.download-venv/bin/python -m pip install -e khronos/tartanground-tools/tools/tartanairpy
```

The data download and bag-conversion commands are in [CODE_REPRODUCTION.md](../experiment/CODE_REPRODUCTION.md). Neither SDK installation nor Khronos compilation downloads the Hospital scene itself.
