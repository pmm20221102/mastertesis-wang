# semantic_inference

<div align="center">
   <img src="docs/media/demo_segmentation.png"/>
</div>

This repository provides code for running inference on images with pre-trained models to provide both closed-set and open-set semantics.

Closed-set and open-set segmentation are implemented as follows:
- Inference using dense 2D closed-set semantic-segmentation models is implemented in C++ using TensorRT.
- Inference using open-set segmentation models and language features is implemented in Python.

Both kinds of semantic segmentation have an associated ROS interface, implemented in C++ or Python as appropriate.

> **Note** </br>
> The ROS 1 interface is archived [here](https://github.com/MIT-SPARK/semantic_inference/tree/archive/ros_noetic) and will not receive further development.

## Table of Contents

- [Credits](#credits)
- [Filing Issues](#filing-issues)
- [Getting Started](#getting-started)
  - [Closed-set](docs/closed_set.md#setting-up)
  - [Open-set](docs/open_set.md#setting-up)
- [Usage](#usage)

## Credits

`semantic_inference` was primarily developed by [Nathan Hughes](https://mit.edu/sparklab/people.html) at the [MIT-SPARK Lab](https://mit.edu/sparklab), assisted by [Yun Chang](https://mit.edu/sparklab/people.html), [Jared Strader](https://mit.edu/sparklab/people.html), [Aaron Ray](https://mit.edu/sparklab/people.html), and [Dominic Maggio](https://mit.edu/sparklab/people.html).

A full list of contributors is maintained [here](contributors.md). We welcome additional contributions!

## Filing Issues

Please understand that this is research code maintained by busy graduate students, **which comes with some caveats**:

1. We do our best to maintain and update the code, but things may occasionally break or change.
2. We do not have the capacity to adapt the code to new applications.
3. The documentation, codebase, and installation instructions are intended for practitioners familiar with ROS.

> **:warning: Warning**<br>
> Other platforms are not supported. Issues requesting support for other platforms (e.g. Ubuntu 18.04 or Windows) will be closed.

Thank you in advance for your understanding!

## Getting Started

The recommended use case is to use this repository with ROS. These instructions assume some familiarity with ROS.

To start, clone this repository into your colcon workspace and run rosdep to install missing dependencies. A typical setup is:

```bash
cd /path/to/colcon_ws/src
git clone git@github.com:MIT-SPARK/semantic_inference.git
vcs import . < semantic_inference/install/packages.yaml
rosdep install --from-paths . --ignore-src -r -y
```

An optional quick primer for setting up a minimal workspace is provided below for users less familiar with ROS.

<details>

<summary>Making a Workspace</summary>

First, make sure rosdep is set up:

```bash
# Initialize the tools required for ROS development
sudo apt install python3-vcstool python3-rosdep
sudo rosdep init
rosdep update
```

Then create and initialize the workspace:

```bash
# Set up the workspace
mkdir -p path/to/colcon_ws/src
cd colcon_ws
echo "build: {cmake-args: [--no-warn-unused-cli, -DCMAKE_BUILD_TYPE=Release]}" > colcon_defaults.yaml
```

</details>

Once this repository is in your workspace, follow one or both setup guides as needed:
- [Closed-set](docs/closed_set.md#setting-up)
- [Open-set](docs/open_set.md#setting-up)
- [Instance-Seg](docs/instance_seg.md)

> **Note** </br>
> Other (larger) projects may provide more accessible guides for setting up `semantic_inference` for specific applications, such as [Hydra](https://github.com/MIT-SPARK/Hydra), [Khronos](https://github.com/MIT-SPARK/Khronos), or [Clio](https://github.com/MIT-SPARK/Clio).

## Usage

`semantic_inference` is not intended for standalone use. Its launch files are designed to be used as part of a larger project. For integration details, see the [closed-set](docs/closed_set.md#using-closed-set-segmentation-online) and [open-set](docs/open_set.md#using-open-set-segmentation-online) documentation.

### Standalone Test

```bash
ros2 launch semantic_inference_ros open_set.launch.yaml
```

Then play a rosbag in another terminal:

```bash
ros2 bag play path/to/rosbag --remap INPUT_TOPIC:=/color/image_raw
```

Visualize the result with:

```bash
ros2 run image_view image_view --ros-args -r image:=/semantic_color/image_raw
```

### Integration with Khronos

When running with Khronos, use launch-file parameters to enable open-set semantics:

```bash
# Activate the virtual environment (first run conda deactivate).
source /home/nenlian/envs/semantic_inference_env/bin/activate
cd ~/Masterthesis-wang/scene-graph-khronos
source install/setup.bash

ros2 launch khronos_ros uhumans2_khronos.launch.yaml \
  use_gt_semantics:=false \
  use_openset_semantics:=true
```

This automatically launches `open_set_node` alongside `khronos_node`.

### Published Topics

| Topic | Type | Description |
|---|---|---|
| `/semantic/color/image_raw` | `sensor_msgs/Image` | Color visualization of segmentation |
| `/semantic/image_raw` | `FeatureImage` | Embedding and segmentation result (custom message) |
| `/semantic/feature` | `FeatureVector` | Language embedding vector |
| `/semantic_overlay/image_raw` | `sensor_msgs/Image` | Segmentation overlay on RGB |

**Note for Khronos RViz:** In open-set mode, the RViz Semantic Image panel should subscribe to `/semantic_color/image_raw` rather than `/khronos_node/visualization/semantic_image`, which is used in ground-truth mode.

> **Note** </br>
> Remapping the rosbag output topic is slightly different from typical ROS remapping because launch files cannot accept remapping arguments from the command line.

## Local Environment Setup

This project uses a standalone virtual environment at `/home/nenlian/envs/semantic_inference_env/`.

### Current Environment Status

| Component | Status | Version |
|---|---|---|
| Python | ✅ Installed | 3.12 |
| PyTorch | ✅ Installed | 2.10.0+cu128 (CUDA available) |
| TensorRT | ❌ Not installed | — |
| PyYAML | ✅ Installed | 6.0.1 |

### Launch Procedure

**Important:** Conda and virtualenv interfere with each other. Deactivate Conda before activating the virtualenv.

Full procedure:

```bash
# 1. Exit Conda if currently active
conda deactivate

# 2. Activate the semantic_inference virtualenv
source /home/nenlian/envs/semantic_inference_env/bin/activate

# 3. Confirm Python points to the virtualenv
which python3
# Expected: /home/nenlian/envs/semantic_inference_env/bin/python3

# 4. Source the ROS 2 workspace
cd ~/Masterthesis-wang/scene-graph-khronos
source install/setup.bash

# 5. Launch Khronos with open-set semantic segmentation
ros2 launch khronos_ros uhumans2_khronos.launch.yaml \
  use_gt_semantics:=false \
  use_openset_semantics:=true
```

### Quick Environment Check

```bash
source /home/nenlian/envs/semantic_inference_env/bin/activate
python3 -c "import yaml; import torch; import clip; print('All OK')"
# Expected output: All OK
```

### Troubleshooting

**Error: `ModuleNotFoundError: No module named 'yaml'`**

The virtualenv may not be active, or Conda base may override the Python path. Run `conda deactivate`, then reactivate the virtualenv.

**Error: `ModuleNotFoundError: No module named 'torch'`**

Follow the same steps and confirm that `which python3` points to the virtualenv.

### Limitations

- **Closed-set segmentation** requires TensorRT and is currently **unavailable**.
- **Open-set segmentation** uses PyTorch and is currently **available**.

### VRAM Estimate (RTX 3060 6 GB)

| Component | VRAM |
|---|---|
| FastSAM-x | ~1.0–1.5 GB |
| CLIP ViT-L-14 | ~1.0–1.8 GB |
| CUDA overhead | ~0.3–0.5 GB |
| **Total** | **~3.0–4.8 GB** (depending on fp16/fp32) |
