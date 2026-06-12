# semantic_inference

<div align="center">
   <img src="docs/media/demo_segmentation.png"/>
</div>

This repository provides code for running inference on images with pre-trained models to provide both closed and open-set semantics.
本仓库提供使用预训练模型对图像进行推理的代码，支持封闭集和开放集语义分割。

Closed-set and open-set segmentation are implemented as follows:
封闭集和开放集分割的实现方式如下：
  - Inference using dense 2D closed-set semantic segmentation models is implemented in c++ using TensorRT
    封闭集语义分割使用 TensorRT 在 C++ 中实现
  - Inference using open-set segmentation models and language features is implemented in python
    开放集分割和语言特征推理使用 Python 实现

Both kinds of semantic segmentation have a ROS interface associated with them, split between c++ and python as appropriate.
两种语义分割都有对应的 ROS 接口，分别由 C++ 和 Python 实现。
> **Note / 注意** </br>
> We have archived our ROS1 interface [here](https://github.com/MIT-SPARK/semantic_inference/tree/archive/ros_noetic) and do not plan on any additional ROS1 development.
> ROS1 接口已归档[此处](https://github.com/MIT-SPARK/semantic_inference/tree/archive/ros_noetic)，不再维护。

## Table of Contents / 目录

- [Credits / 致谢](#credits)
- [Filing Issues / 提交问题](#filing-issues)
- [Getting started / 快速开始](#getting-started)
  - [Closed-set / 封闭集](docs/closed_set.md#setting-up)
  - [Open-set / 开放集](docs/open_set.md#setting-up)
- [Usage / 使用方法](#usage)

## Credits / 致谢

`semantic_inference` was primarily developed by [Nathan Hughes](https://mit.edu/sparklab/people.html) at the [MIT-SPARK Lab](https://mit.edu/sparklab), assisted by [Yun Chang](https://mit.edu/sparklab/people.html), [Jared Strader](https://mit.edu/sparklab/people.html), [Aaron Ray](https://mit.edu/sparklab/people.html), and [Dominic Maggio](https://mit.edu/sparklab/people.html).
`semantic_inference` 由 [MIT-SPARK 实验室](https://mit.edu/sparklab) 的 [Nathan Hughes](https://mit.edu/sparklab/people.html) 主要开发，[Yun Chang](https://mit.edu/sparklab/people.html)、[Jared Strader](https://mit.edu/sparklab/people.html)、[Aaron Ray](https://mit.edu/sparklab/people.html) 和 [Dominic Maggio](https://mit.edu/sparklab/people.html) 协助完成。
A full list of contributors is maintaned [here](contributors.md).
完整贡献者列表见[此处](contributors.md)。
We welcome additional contributions!
欢迎贡献代码！

## Filing Issues / 提交问题

Please understand that this is research code maintained by busy graduate students, **which comes with some caveats**:
请注意这是由繁忙的研究生维护的研究代码，**有一些注意事项**：
  1. We do our best to maintain and keep the code up-to-date, but things may break or change occasionally
     我们尽力维护代码更新，但可能会偶尔出错或变更
  2. We do not have bandwidth to help adapt the code to new applications
     我们没有精力帮助将代码适配到新的应用场景
  3. The documentation, code-base and installation instructions are geared towards practitioners familiar with ROS
     文档、代码库和安装说明面向熟悉 ROS 的用户

> **:warning: Warning / 警告**<br>
> We don't support other platforms. Issues requesting support on other platforms (e.g., Ubuntu 18.04, Windows) will be summarily closed.
> 不支持其他平台。请求支持其他平台（如 Ubuntu 18.04、Windows）的问题将被直接关闭。

Thank you in advance for your understanding!
提前感谢您的理解！

## Getting started / 快速开始

The recommended use-case for the repository is with ROS.
推荐在 ROS 环境中使用本仓库。
We assume some familiarity with ROS in these instructions.
以下说明假设您对 ROS 有一定了解。
To start, clone this repository into your colcon workspace and run rosdep to get any missing dependencies.
首先，将本仓库克隆到 colcon 工作空间并运行 rosdep 获取缺失的依赖。
This usually looks like the following:
通常步骤如下：
```bash
cd /path/to/colcon_ws/src
git clone git@github.com:MIT-SPARK/semantic_inference.git
vcs import . < semantic_inference/install/packages.yaml
rosdep install --from-paths . --ignore-src -r -y
```

An (optional) quick primer for setting up a minimal workspace is below for those less familiar with ROS.
以下为不熟悉 ROS 的用户提供的（可选）最小工作空间设置指南。

<details>

<summary>Making a workspace / 创建工作空间</summary>

First, make sure rosdep is setup:
首先确保 rosdep 已配置：
```bash
# Initialize necessary tools for working with ROS
# 初始化 ROS 工具
sudo apt install python3-vcstool python3-rosdep
sudo rosdep init
rosdep update
```

Then, make the workspace and initialize it:
然后创建工作空间并初始化：
```bash
# Setup the workspace
# 设置工作空间
mkdir -p path/to/colcon_ws/src
cd colcon_ws
echo "build: {cmake-args: [--no-warn-unused-cli, -DCMAKE_BUILD_TYPE=Release]}" > colcon_defaults.yaml
```

</details>

Once you've added this repository to your workspace, follow one (or both) of the following setup-guides as necessary:
将本仓库添加到工作空间后，根据需要按照以下设置指南操作：
- [Closed-Set / 封闭集](docs/closed_set.md#setting-up)
- [Open-Set / 开放集](docs/open_set.md#setting-up)
- [Instance-Seg / 实例分割](docs/instance_seg.md)

> **Note / 注意** </br>
> Some of our other (larger) packages have or will have more accessible guides to getting `semantic_inference` set up for specific applications, such as [Hydra](https://github.com/MIT-SPARK/Hydra), [Khronos](https://github.com/MIT-SPARK/Khronos) or [Clio](https://github.com/MIT-SPARK/Clio).
> 我们的其他（较大）包已有或即将提供更详细的 `semantic_inference` 设置指南，如 [Hydra](https://github.com/MIT-SPARK/Hydra)、[Khronos](https://github.com/MIT-SPARK/Khronos) 或 [Clio](https://github.com/MIT-SPARK/Clio)。

## Usage / 使用方法

`semantic_inference` is not intended for standalone usage.
`semantic_inference` 不是设计为独立使用的。
Instead, the intention is for the launch files in `semantic_inference` to be used in a larger project.
它的 launch 文件设计为在更大的项目中使用。
More details about including them can be found in the [closed-set](docs/closed_set.md#using-closed-set-segmentation-online) and [open-set](docs/open_set.md#using-open-set-segmentation-online) documentation.
更多集成细节请参阅[封闭集](docs/closed_set.md#using-closed-set-segmentation-online)和[开放集](docs/open_set.md#using-open-set-segmentation-online)文档。

### Standalone Test / 独立测试

```
ros2 launch semantic_inference_ros open_set.launch.yaml
```
and then / 然后在另一个终端播放 rosbag：
```
ros2 bag play path/to/rosbag --remap INPUT_TOPIC:=/color/image_raw
```
The result can be visualized via / 结果可通过以下命令可视化：
```
ros2 run image_view image_view --ros-args -r image:=/semantic_color/image_raw
```

### Integration with Khronos / 与 Khronos 集成

When running with Khronos, use the launch file parameters to enable open-set semantics:
与 Khronos 集成时，通过 launch 文件参数启用开放集语义分割：

```bash
# 激活虚拟环境（重要：先 conda deactivate）
source /home/nenlian/envs/semantic_inference_env/bin/activate
cd ~/Masterthesis-wang/scene-graph-khronos
source install/setup.bash

ros2 launch khronos_ros uhumans2_khronos.launch.yaml \
  use_gt_semantics:=false \
  use_openset_semantics:=true
```

This automatically launches `open_set_node` alongside `khronos_node`.
这会自动同时启动 `open_set_node` 和 `khronos_node`。

### Published Topics / 发布的话题

| Topic / 话题 | Type / 类型 | Description / 说明 |
|---|---|---|
| `/semantic/color/image_raw` | `sensor_msgs/Image` | Color visualization of segmentation / 彩色分割可视化 |
| `/semantic/image_raw` | `FeatureImage` | Embedding + segmentation result (custom msg) / 嵌入+分割结果（自定义消息） |
| `/semantic/feature` | `FeatureVector` | Language embedding vector / 语言嵌入向量 |
| `/semantic_overlay/image_raw` | `sensor_msgs/Image` | Segmentation overlay on RGB / 叠加在 RGB 上的分割结果 |

**Note for Khronos RViz / Khronos RViz 注意事项：**
When using open-set mode, the Semantic Image panel in RViz should subscribe to `/semantic_color/image_raw` (not `/khronos_node/visualization/semantic_image` which is for GT mode).
使用 open-set 模式时，RViz 的 Semantic Image 面板应订阅 `/semantic_color/image_raw`（而非 GT 模式用的 `/khronos_node/visualization/semantic_image`）。

> **Note / 注意** </br>
> This usage (remapping the rosbag output topic) is a little bit backwards from how remappings from ROS are normally specified and is because launch files are unable to take remappings from the command line.
> 这种用法（重映射 rosbag 输出话题）与常规 ROS 重映射方式相反，因为 launch 文件不支持从命令行接收重映射参数。

---

## 本地环境配置 / Local Environment Setup

本项目使用独立的 virtualenv 环境 `semantic_inference_env`，位于 `/home/nenlian/envs/semantic_inference_env/`。
This project uses a standalone virtualenv at `/home/nenlian/envs/semantic_inference_env/`.

### 当前环境状态 / Current Environment Status

| 组件 | 状态 | 版本 |
|------|------|------|
| Python | ✅ 已安装 | 3.12 |
| PyTorch | ✅ 已安装 | 2.10.0+cu128 (CUDA 可用) |
| TensorRT | ❌ 未安装 | — |
| PyYAML | ✅ 已安装 | 6.0.1 |

### 启动流程 / Launch Procedure

**重要：conda 和 virtualenv 会互相干扰，必须先退出 conda 再激活 venv。**
**Important: conda and virtualenv interfere with each other. Always deactivate conda first.**

完整步骤（复制粘贴即可）：
Full steps (copy and paste):

```bash
# 1. 退出 conda（如果在 conda 环境中）
# 1. Exit conda (if in a conda environment)
conda deactivate

# 2. 激活 semantic_inference 虚拟环境
# 2. Activate the semantic_inference virtualenv
source /home/nenlian/envs/semantic_inference_env/bin/activate

# 3. 验证 Python 指向 venv（应显示 venv 路径）
# 3. Verify Python points to venv (should show venv path)
which python3
# 预期输出: /home/nenlian/envs/semantic_inference_env/bin/python3
# Expected: /home/nenlian/envs/semantic_inference_env/bin/python3

# 4. Source ROS2 工作空间
# 4. Source the ROS2 workspace
cd ~/Masterthesis-wang/scene-graph-khronos
source install/setup.bash

# 5. 启动 Khronos + 开放集语义分割
# 5. Launch Khronos + open-set segmentation
ros2 launch khronos_ros uhumans2_khronos.launch.yaml \
  use_gt_semantics:=false \
  use_openset_semantics:=true
```

### 快速验证环境 / Quick Environment Check

```bash
source /home/nenlian/envs/semantic_inference_env/bin/activate
python3 -c "import yaml; import torch; import clip; print('All OK')"
# 预期输出: All OK
```

### 常见问题 / Troubleshooting

**错误 `ModuleNotFoundError: No module named 'yaml'`**
→ 没有激活 venv，或 conda base 覆盖了 Python 路径。运行 `conda deactivate` 后重新激活 venv。

**错误 `ModuleNotFoundError: No module named 'torch'`**
→ 同上，确保 `which python3` 显示 venv 路径。

### 功能限制 / Limitations

- **封闭集分割**（closed-set）：需要 TensorRT，当前**不可用**
- **开放集分割**（open-set）：使用 PyTorch，当前**可用**

### 显存估算 / VRAM Estimate（RTX 3060 6GB）

| 组件 | 显存 |
|------|------|
| FastSAM-x | ~1.0–1.5 GB |
| CLIP ViT-L-14 | ~1.0–1.8 GB |
| CUDA 开销 | ~0.3–0.5 GB |
| **总计** | **~3.0–4.8 GB**（取决于 fp16/fp32） |
