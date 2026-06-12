# Khronos

![Khronos](https://github.com/MIT-SPARK/Khronos/assets/36043993/a3f63175-48cc-4ef2-a810-19e4e5cd360c)

Khronos is a unified approach that can reason about short-term dynamics and long-term changes when performing online metric-semantic simultaneous mapping and localization (SLAM) in dynamic environments. A few instances from Khronos' spatio-temporal map, representing the scene state at all times, are shown above. Short-term dynamics (left) are shown in magenta and compared against observed human actions over the corresponding time interval. Both humans and inanimate objects (the cart) are detected. Long-term changes (right) are shown for three time instances of the same scene. The earliest instance is at time 0:20 (top right). While the robot is moving through the hallways, a chair is removed and a red cooler is placed on top of the table; these changes are detected as the robot revisits and closes the loop at time 1:52 (bottom right). Lastly, the cooler is removed again, which is detected by the robot at time 3:35.

Khronos 是一种统一方法，能够在动态环境中执行在线度量-语义同步定位与建图（SLAM）时，同时推理短期动态和长期变化。上图展示了 Khronos 时空地图的几个时刻快照，表示每个时刻的场景状态。短期动态（左图）以品红色显示，并与相应时间间隔内观察到的人类行为进行比较。人类和非生命物体（推车）均被检测到。长期变化（右图）展示了同一场景的三个时间实例。最早实例在 0:20（右上），当机器人在走廊中移动时，一把椅子被移走，一个红色冷藏箱被放到桌子上；当机器人在 1:52 回访并完成回环检测时（右下），这些变化被检测到。最后，冷藏箱再次被移走，在 3:35 时被机器人检测到。

> This project was supported by Amazon, the ARL DCIST program, the ONR RAPID program, and the Swiss National Science Foundation (SNSF) grant No. 214489.
>
> 本项目由 Amazon、ARL DCIST 计划、ONR RAPID 计划和瑞士国家科学基金会（SNSF）资助（资助编号 No. 214489）。

# Table of Contents / 目录
**Credits / 致谢**
* [Paper / 论文](#Paper)
* [Video / 视频](#Video)

**Setup / 环境搭建**
* [Installation / 安装](#setup)
* [Dataset / 数据集](#download-datasets)

**Examples / 示例**
* [Run Khronos / 运行 Khronos](#run-khronos)
* [Run Khronos on real Data / 在真实数据上运行](#run-khronos-on-real-data)
* [Open-Set Segmentation / 开放集语义分割](#open-set-segmentation)
* [Visualize 4D Map / 4D 地图可视化](#visualize-4d-map)
* [Evaluation / 评估](#evaluation)

**Contributing / 贡献指南**
* [Contributing / 贡献指南](#contributing)


# News / 最新动态
* [10/2025] We've released the **ROS2** version of Khronos! / 我们发布了 Khronos 的 **ROS2** 版本！
* [07/2025] We've released several new updates, including **Open-set** datasets and real-time setups! / 我们发布了多项更新，包括**开放集**数据集和实时配置！
* [07/2024] Khronos won the **Outstanding Systems Paper Award** at RSS in Delft! / Khronos 在 RSS 2024（荷兰代尔夫特）获得**优秀系统论文奖**！

# Paper / 论文

If you use this code in your work, please cite the following paper:
如果您的研究使用了本代码，请引用以下论文：

Lukas Schmid, Marcus Abate, Yun Chang, and Luca Carlone, "**Khronos: A Unified Approach for Spatio-Temporal Metric-Semantic SLAM in Dynamic Environments**", in *Robotics: Science and Systems (RSS)*, Delft, the Netherlands, July, 2024. [ [RSS](https://www.roboticsproceedings.org/rss20/p081.html) | [ArXiv](https://arxiv.org/abs/2402.13817) | [Video](https://www.youtube.com/watch?v=YsH6YIL5_kc) ]

```bibtex
@inproceedings{Schmid-RSS24-Khronos,
title     = {Khronos: A Unified Approach for Spatio-Temporal Metric-Semantic SLAM in Dynamic Environments},
author    = {Lukas Schmid and Marcus Abate and Yun Chang and Luca Carlone},
booktitle = {Proc. of Robotics: Science and Systems (RSS)},
year      = {2024},
month     = {July},
address   = {Delft, Netherlands},
doi       = {10.15607/RSS.2024.XX.081}
}
```

# Video / 视频
An overview of Khronos is available on [YouTube](https://www.youtube.com/watch?v=YsH6YIL5_kc):
Khronos 概述视频可在 [YouTube](https://www.youtube.com/watch?v=YsH6YIL5_kc) 观看：

[<img src=https://github.com/MIT-SPARK/Khronos/assets/36043993/279fe8e8-60d9-44ee-af34-1947d4a1aeb0 alt="Khronos Youtube Video">](https://www.youtube.com/watch?v=YsH6YIL5_kc)

# Setup / 环境搭建

> :warning: **Warning / 警告** <br>
> The ROS2 version of Khronos is in active development and is unstable and may not fully be feature-complete.
> Khronos 的 ROS2 版本正在积极开发中，可能不稳定且功能尚未完全实现。

Khronos has been tested on Ubuntu 24.04 & ROS2 Jazzy. It **should** build on newer distributions. It **will not** build on older ROS2 distributions than Iron.
Khronos 已在 Ubuntu 24.04 和 ROS2 Jazzy 上测试通过。**应该**可以在更新的发行版上构建。**不支持** Iron 之前的老版本 ROS2。

## Installation / 安装
Setup a ROS2 workspace:
配置 ROS2 工作空间：
```bash
sudo apt install python3-vcstool python3-tk
mkdir -p ~/ros2_ws/src
cd ~/ros2_ws/src
```

Install system dependencies:
安装系统依赖：
```bash
sudo apt install ros-$ROS_DISTRO-gtsam libgoogle-glog-dev nlohmann-json3-dev
```

Get Khronos and all source dependencies:
获取 Khronos 及所有源码依赖：
```bash
cd ~/ros2_ws/src
git clone git@github.com:MIT-SPARK/Khronos.git khronos
# Use `https.rosinstall` if you do not have ssh key setup with github.com.
# 如果未配置 GitHub SSH 密钥，请使用 `https.rosinstall`
vcs import . < khronos/install/ssh.rosinstall
```

Build / 构建：
```bash
cd ~/ros2_ws
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
```

## Semantic Inference (Optional) / 语义推理（可选）

By default, Khronos will use ground-truth semantic labels for simulated datasets or pre-recorded segmentation topics for real datasets. To run Khronos with `semantic_inference` for closed-set or open-set online segmentation, use [semantic_inference](https://github.com/MIT-SPARK/semantic_inference), which is included in the rosinstall files. Follow the instructions in the [README](https://github.com/MIT-SPARK/semantic_inference/blob/main/README.md) to build the package without TensorRT if necessary. Note that if you do not have TensorRT, you can only use open-set segmentation.
默认情况下，Khronos 在模拟数据集上使用真值语义标签，在真实数据集上使用预录制的分割话题。要使用 `semantic_inference` 进行封闭集或开放集在线语义分割，请参考 [semantic_inference](https://github.com/MIT-SPARK/semantic_inference)（已包含在 rosinstall 文件中）。如需在没有 TensorRT 的环境下构建，请参照其 [README](https://github.com/MIT-SPARK/semantic_inference/blob/main/README.md)。注意：没有 TensorRT 时只能使用开放集分割。

Make sure to source the virtual environment for `semantic_inference` before running Khronos with the open-set segmentation.
运行开放集分割前，请确保已激活 `semantic_inference` 的虚拟环境。

Note that the first time you run `semantic_inference`, it will download the pre-trained model weights. This may take a while.
首次运行 `semantic_inference` 时会下载预训练模型权重，可能需要一些时间。

## Download datasets / 下载数据集

Download the simulated datsets used in our paper [here](https://drive.google.com/drive/folders/1KOcX-jP2bTip_Sm48dvEd9KjxJZjJ6po?usp=drive_link) under `tesse_cd`. For the real dataset, download the mezzanine rosbag (with pre-recorded semantic labels) under `khronos_real`.
点击[此处](https://drive.google.com/drive/folders/1KOcX-jP2bTip_Sm48dvEd9KjxJZjJ6po?usp=drive_link)下载论文中使用的模拟数据集（`tesse_cd` 目录下）。真实数据集请下载 `khronos_real` 目录下的 rosbag（含预录制语义标签）。

The `tesse_cd_office` dataset is the longer of the two simulated datasets and contains the most changes and dynamic objects.
`tesse_cd_office` 数据集是两个模拟数据集中较长的一个，包含最多的变化和动态物体。

**Note:** ROS2 bags are directories, not single files. The downloaded datasets should be extracted to their respective directories.
**注意：** ROS2 bag 是目录，不是单个文件。下载的数据集应解压到各自的目录中。

# Examples / 示例
## Run Khronos / 运行 Khronos

Open [uhumans2_khronos.launch.yaml](khronos_ros/launch/uhumans2_khronos.launch.yaml) and modify the bag path to the `tesse_cd` office rosbag directory.
打开 [uhumans2_khronos.launch.yaml](khronos_ros/launch/uhumans2_khronos.launch.yaml)，将 bag 路径修改为 `tesse_cd` office rosbag 目录。

Run the following in commandline:
在命令行中运行：
```bash
source ~/ros2_ws/install/setup.bash
ros2 launch khronos_ros uhumans2_khronos.launch.yaml
```

RVIZ will launch automatically for real-time visualization. You should see the method build an incremental background mesh, detect and segment static and dynamic objects, and generate object nodes. Output will look like this:
RVIZ 会自动启动进行实时可视化。您将看到方法逐步构建增量背景网格、检测和分割静态/动态物体，并生成物体节点。输出效果如下：

<!-- <img src="https://github.com/MIT-SPARK/Khronos/assets/32229998/01c265ac-c2ab-42e7-8a6a-2f9df84fc85b" alt="Khronos Beginning" width="500"/> -->

<img src="https://raw.githubusercontent.com/MIT-SPARK/Khronos/refs/heads/docs/base/docs/khronos_sim.gif" alt = "Khronos Beginning" width="500">

Use the right side panel of rviz to toggle different visualization options to see each component of the method, including inpus, object/motion detection, factor graph, scene graph, and backend visuals. Most of these are disabled by default.
使用 RVIZ 右侧面板切换不同的可视化选项，查看方法的各个组件，包括输入、物体/运动检测、因子图、场景图和后端可视化。大部分选项默认关闭。

At the end of rosbag playback, Khronos will have a fully completed scene reconstruction with objects, dynamics, and changes:
rosbag 播放结束时，Khronos 将完成包含物体、动态和变化的完整场景重建：

<img src="https://github.com/MIT-SPARK/Khronos/assets/32229998/66aeae1e-bc96-42b7-bcc2-a94f268d4acd" alt="Khronos End" width="500">

After the rosbag has completed playback and the method is finished, call the following service to guarantee that output is saved before termination:
rosbag 播放完成且方法执行结束后，调用以下服务确保输出在终止前被保存：
```bash
ros2 service call /finish_mapping_and_save std_srvs/srv/Empty
```

You can now terminate Khronos.
现在可以终止 Khronos。

To run Khronos with the apartment dataset, change the `dataset` from `tesse_cd_office` to `tesse_cd_apartment` and change the rosbag path accordingly. You can then run the launch file as before.
使用 apartment 数据集时，将 `dataset` 从 `tesse_cd_office` 改为 `tesse_cd_apartment`，并相应修改 rosbag 路径。然后按之前方式运行 launch 文件。

## Run Khronos on Real Data / 在真实数据上运行 Khronos

Download the mezzanine rosbag [here](https://drive.google.com/drive/folders/1Qja6KVHCfT6PaYcj2NcQy4o3LJxXc8Kc?usp=drive_link).
点击[此处](https://drive.google.com/drive/folders/1Qja6KVHCfT6PaYcj2NcQy4o3LJxXc8Kc?usp=drive_link)下载 rosbag。

To run Khronos on real data, use the `jackal_khronos.launch.yaml` file. To use pre-recorded semantic labels, set `use_prerecorded_semantics` to `true`:
在真实数据上运行 Khronos 时使用 `jackal_khronos.launch.yaml`。如需使用预录制语义标签，设置 `use_prerecorded_semantics` 为 `true`：
```bash
ros2 launch khronos_ros jackal_khronos.launch.yaml bag_path:=PATH_TO_BAG_DIR use_prerecorded_semantics:=true
```

**Note:** The ROS2 version uses `ianvs` for bag playback. Kimera-VIO is not yet ported to ROS2, so odometry transforms are published from recorded odometry messages via the `odom_to_tf.py` node.
**注意：** ROS2 版本使用 `ianvs` 进行 bag 回放。Kimera-VIO 尚未移植到 ROS2，因此里程计变换通过 `odom_to_tf.py` 节点从录制的里程计消息发布。

Your ouptut will look something like this:
输出效果如下：

<img src="https://raw.githubusercontent.com/MIT-SPARK/Khronos/refs/heads/docs/base/docs/khronos_real.gif" alt="Khronos on Real Data" width="500">

## Open-Set Segmentation / 开放集语义分割

To run Khronos with semantic_inference, change the `use_gt_semantics` parameter in the launch file from `true` to `false`. Set `use_openset_semantics` to `true` for open-set segmentation.
要使用 semantic_inference 运行 Khronos，将 launch 文件中的 `use_gt_semantics` 参数从 `true` 改为 `false`。设置 `use_openset_semantics` 为 `true` 以启用开放集分割。

For example / 例如：
```bash
ros2 launch khronos_ros uhumans2_khronos.launch.yaml use_gt_semantics:=false use_openset_semantics:=true
```

## Visualize 4D map / 4D 地图可视化

To visualize the spatio-temporal map of the scene after running Khronos, run the following in commandline:
运行 Khronos 后，如需可视化场景的时空地图，请在命令行中运行：
```bash
ros2 launch khronos_ros 4d_visualizer.launch.yaml
```

The visualizer will load the map located (by default) in [output/tmp](output/tmp/). If you save the map elsewhere, be sure to modify the path in [4d_visualizer.launch.yaml](khronos_ros/launch/4d_visualizer.launch.yaml).
可视化器将加载默认位于 [output/tmp](output/tmp/) 的地图。如果将地图保存在其他位置，请修改 [4d_visualizer.launch.yaml](khronos_ros/launch/4d_visualizer.launch.yaml) 中的路径。

<!-- <img src="https://github.com/MIT-SPARK/Khronos/assets/32229998/d8072aab-a874-483f-aada-24607eb620be" alt="Khronos 4D Map" width="700"> -->

<img src="https://raw.githubusercontent.com/MIT-SPARK/Khronos/refs/heads/docs/base/docs/khronos_4d.gif" alt="Khronos 4D Map" width="700">

You can play forward or backward, and see Khronos' estimate of the state of the scene at each timestamp, with current or future knowledge. As an example, fix the query time to 0 and play forward on robot time mode to see how Khronos' understanding of the initial state of the scene evolves over time.
您可以前进或后退播放，查看 Khronos 在每个时间戳的场景状态估计（可使用当前或未来知识）。例如，将查询时间固定为 0，以机器人时间模式前进播放，观察 Khronos 对场景初始状态的理解如何随时间演变。

## Evaluation / 评估

For more information on how to perform evaluation, see [this readme](khronos_eval/README.md).
评估方法详见 [khronos_eval/README.md](khronos_eval/README.md)。

# Contributing / 贡献指南
This is an open-source project and pull requests for features and fixes are very welcome! We follow the [Feature-Branch-Workflow](https://www.atlassian.com/git/tutorials/comparing-workflows/feature-branch-workflow) and the [google C++ style guide](https://google.github.io/styleguide/cppguide.html). To adhere to this, please setup the auto formatter and linter for khronos as follows:
本项目是开源项目，非常欢迎提交功能和修复的 Pull Request！我们遵循 [Feature-Branch-Workflow](https://www.atlassian.com/git/tutorials/comparing-workflows/feature-branch-workflow) 和 [Google C++ 风格指南](https://google.github.io/styleguide/cppguide.html)。请按以下方式配置自动格式化和代码检查工具：
```bash
cd ~/ros2_ws/src/khronos
pip install pre-commit
pre-commit install
```
