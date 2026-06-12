# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

本文件为 Claude Code (claude.ai/code) 在此代码仓库中工作时提供指引。

## What This Is / 项目简介

A **ROS2 colcon workspace** for **Khronos**, a spatio-temporal metric-semantic SLAM system for dynamic environments (MIT-SPARK Lab, RSS 2024). Each subdirectory under `src/` is an independent git repo from `github.com/MIT-SPARK`. The top-level directory is NOT a single git repository.

**Khronos** 的 ROS2 colcon 工作空间，Khronos 是一个面向动态环境的时空度量-语义 SLAM 系统（MIT-SPARK 实验室，RSS 2024）。`src/` 下每个子目录是来自 `github.com/MIT-SPARK` 的独立 git 仓库，顶层目录本身不是 git 仓库。

## Build Commands / 构建命令

```bash
# Full workspace build (run from workspace root)
# 完整工作空间构建（在工作空间根目录运行）
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release

# Build a single package
# 构建单个包
colcon build --packages-select <package_name> --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release

# Source the workspace before running
# 运行前先 source 工作空间
source install/setup.bash
```

## Running / 运行

```bash
# Khronos on simulated data (edit bag path in launch file first)
# 在模拟数据上运行 Khronos（先修改 launch 文件中的 bag 路径）
ros2 launch khronos_ros uhumans2_khronos.launch.yaml

# Khronos on real Jackal data
# 在真实 Jackal 机器人数据上运行
ros2 launch khronos_ros jackal_khronos.launch.yaml bag_path:=PATH_TO_BAG_DIR use_prerecorded_semantics:=true

# With open-set segmentation (requires semantic_inference + PyTorch)
# 使用开放集语义分割（需要 semantic_inference + PyTorch）
ros2 launch khronos_ros uhumans2_khronos.launch.yaml use_gt_semantics:=false use_openset_semantics:=true

# 4D spatio-temporal map visualizer
# 4D 时空地图可视化器
ros2 launch khronos_ros 4d_visualizer.launch.yaml

# Save output after rosbag finishes
# rosbag 播放完成后保存输出
ros2 service call /finish_mapping_and_save std_srvs/srv/Empty
```

## Testing / 测试

Tests use **CTest** (enabled via `BUILD_TESTING` CMake option). Test source files are in `tests/` subdirectories within each package.

测试使用 **CTest**（通过 `BUILD_TESTING` CMake 选项启用）。测试源文件位于各包的 `tests/` 子目录中。

```bash
# Run all tests for a built package
# 运行已构建包的所有测试
colcon build --packages-select <package_name> --cmake-args -DBUILD_TESTING=ON
cd build/<package_name> && ctest

# Run a single test
# 运行单个测试
cd build/<package_name> && ctest -R <test_name>
```

## Linting and Formatting / 代码检查与格式化

Google C++ style guide is enforced. Pre-commit hooks are configured in `src/khronos/.pre-commit-config.yaml`:

强制执行 Google C++ 风格指南。预提交钩子配置在 `src/khronos/.pre-commit-config.yaml`：

```bash
cd src/khronos
pip install pre-commit
pre-commit install

# Run on all files
# 对所有文件运行
pre-commit run --all-files
```

- **C++**: clang-format v16 + cpplint (Google style)
- **Python**: black v23.7
- **CMake**: cmake-format（在 hydra_ros、ianvs 中）

## Architecture / 架构

### Dependency Graph (key packages) / 依赖关系图（核心包）

```
khronos  (顶层 SLAM 系统)
  ├── hydra  (实时 3D 场景图构建)
  │     ├── spark_dsg  (动态场景图数据结构)
  │     ├── spatial_hash  (体素化 3D 表示)
  │     ├── kimera_pgmo  (位姿图 + 网格优化)
  │     │     └── kimera_rpgo  (鲁棒位姿图优化 → GTSAM)
  │     ├── teaser_plusplus  (点云配准)
  │     └── config_utilities  (基于 YAML 的配置管理)
  ├── pose_graph_tools
  └── GTSAM, OpenCV, Eigen3

ROS2 封装:  khronos_ros, hydra_ros, kimera_pgmo_ros, semantic_inference_ros
工具:       ianvs (rosbag 回放), semantic_inference (TensorRT/PyTorch 语义分割)
```

### Monorepo Packages / 单仓多包

Several `src/` repos are monorepos containing multiple ROS2 packages:
多个 `src/` 仓库是包含多个 ROS2 包的单仓：

- **khronos**: `khronos` (核心库), `khronos_eval`, `khronos_msgs`, `khronos_ros`
- **hydra**: `hydra` (核心库), Python 绑定
- **hydra_ros**: `hydra_ros`, `hydra_msgs`, `hydra_visualizer`
- **kimera_pgmo**: `kimera_pgmo`, `kimera_pgmo_msgs`, `kimera_pgmo_ros`, `kimera_pgmo_rviz`
- **config_utilities**: `config_utilities`, `config_utilities_msgs`, `config_utilities_ros`
- **semantic_inference**: `semantic_inference`, `semantic_inference_msgs`, `semantic_inference_ros`
- **pose_graph_tools**: `pose_graph_tools`, `pose_graph_tools_msgs`, `pose_graph_tools_ros`

### Khronos Core Modules / Khronos 核心模块 (`src/khronos/khronos/src/`)

- **active_window/**: Object tracking, motion detection, object extraction, integration
- **active_window/**: 目标跟踪、运动检测、目标提取、数据融合
- **backend/**: Change detection (ray-based), reconciliation (mesh merging), spatio-temporal map
- **backend/**: 变化检测（基于射线）、协调合并（网格融合）、时空地图
- **utils/**: Geometry, attributes, output file utilities
- **utils/**: 几何工具、属性工具、输出文件工具

### Hydra Core Modules / Hydra 核心模块 (`src/hydra/src/`)

Scene graph pipeline: **input** → **reconstruction** → **frontend** (mesh segmentation, graph building) → **backend** (scene graph updates) → **loop_closure**. Supporting modules: `active_window/`, `places/`, `rooms/`, `odometry/`, `openset/`, `gnn/`.

场景图流水线：**input** → **reconstruction** → **frontend**（网格分割、图构建）→ **backend**（场景图更新）→ **loop_closure**。辅助模块：`active_window/`、`places/`、`rooms/`、`odometry/`、`openset/`、`gnn/`。

### Configuration System / 配置系统

Uses `config_utilities` for declarative YAML-based config. Config files live in:
使用 `config_utilities` 进行声明式 YAML 配置。配置文件位于：

- `khronos_ros/config/` — dataset, mapper, openset, visualization configs / 数据集、建图器、开放集、可视化配置
- `hydra/config/` — datasets, label spaces, LCD configs / 数据集、标签空间、回环检测配置
- `khronos_eval/config/` — evaluation and ground truth configs / 评估与真值配置

### Languages and Standards / 语言与标准

- **C++17** for most packages; **C++20** for spark_dsg; **C++14** for teaser_plusplus
  大部分包使用 C++17；spark_dsg 使用 C++20；teaser_plusplus 使用 C++14
- **Python** for bindings (pybind11/scikit-build-core), scripts, and semantic_inference open-set pipeline
  Python 用于绑定（pybind11/scikit-build-core）、脚本和 semantic_inference 开放集流水线
- ROS2 Jazzy on Ubuntu 24.04

### Datasets / 数据集

ROS2 bags in `data/` — `tesse_cd_office` (~24 GB) and `tesse_cd_apartment` (~10 GB) from the TESSE simulator. Pre-trained FastSAM weights at workspace root: `FastSAM-x.pt`.

`data/` 目录中的 ROS2 bag —— 来自 TESSE 模拟器的 `tesse_cd_office`（约 24 GB）和 `tesse_cd_apartment`（约 10 GB）。预训练 FastSAM 权重位于工作空间根目录：`FastSAM-x.pt`。

### Datasets / 数据集

ROS2 bags in `data/` — `tesse_cd_office` (~24 GB) and `tesse_cd_apartment` (~10 GB) from the TESSE simulator. Pre-trained FastSAM weights at workspace root: `FastSAM-x.pt`.

`data/` 目录中的 ROS2 bag —— 来自 TESSE 模拟器的 `tesse_cd_office`（约 24 GB）和 `tesse_cd_apartment`（约 10 GB）。预训练 FastSAM 权重位于工作空间根目录：`FastSAM-x.pt`。

## Khronos Output / Khronos 输出

After running Khronos and calling `finish_mapping_and_save`, output is saved to `snapshots/` (or configured `output_dir`).
运行 Khronos 并调用 `finish_mapping_and_save` 后，输出保存在 `snapshots/` 目录（或配置的 `output_dir`）。

### Output Directory Structure / 输出目录结构

```
snapshots/00000/                     # ~3.3 GB (tesse_cd_office)
├── dsg.json               239 MB   # Hydra 场景图（完整，JSON 格式）
├── shared_dsg.json        277 MB   # 共享场景图（多机器人场景）
├── mesh.ply                30 MB   # 3D 背景网格（PLY 格式）
├── final.4dmap            618 MB   # 时空地图（自定义二进制格式）
├── deformation_graph.dgrf 292 KB   # 变形图（网格对齐优化用）
├── object_changes.csv       8 KB   # 物体级长期变化记录
├── background_changes.csv   —      # 背景级变化记录
├── timing_stats.csv         4 KB   # 各模块性能统计
├── hydra_config.yaml        4 KB   # Hydra 配置快照
├── maps/                  1.4 GB   # 时间戳快照（12 个 DSG 快照）
│   ├── dsg_00000_*.json  (52 MB × 2)
│   ├── dsg_00001_*.json  (74 MB × 2)
│   ├── ...
│   └── dsg_00005_*.json  (211 MB × 2)  ← 随时间增长
├── frontend/              653 MB   # Hydra 前端输出
│   ├── dsg.json                     # 前端场景图
│   ├── dsg_with_mesh.json           # 带网格的场景图
│   ├── mesh.ply                     # 前端网格
│   └── layer_*_statistics.csv       # 各层级统计
└── backend/               103 MB   # Khronos 后端输出
    ├── dsg_with_mesh.sparkdsg       # 二进制场景图
    └── proposed_merge.csv           # 合并建议
```

### Output File Formats / 输出文件格式

| File / 文件 | Format / 格式 | Content / 内容 |
|---|---|---|
| `dsg.json` | JSON | Hydra scene graph (nodes + edges + attributes) / Hydra 场景图（节点+边+属性） |
| `shared_dsg.json` | JSON | Shared scene graph for multi-robot / 共享场景图 |
| `mesh.ply` | PLY (3D mesh) | Background 3D mesh reconstruction / 背景 3D 网格重建 |
| `final.4dmap` | Custom binary | Spatio-temporal map (per-frame DSG snapshots) / 时空地图 |
| `object_changes.csv` | CSV | Object-level change detection results / 物体级变化检测结果 |
| `deformation_graph.dgrf` | Custom binary | Deformation graph for mesh alignment / 变形图 |

### object_changes.csv Fields / 物体变化记录字段

| Field / 字段 | Description / 说明 |
|---|---|
| `node_id` | Unique object node ID / 物体唯一 ID |
| `first_absent` | First timestamp the object disappeared (ns) / 物体首次消失时间（纳秒） |
| `last_absent` | Last timestamp the object disappeared / 物体末次消失时间 |
| `first_persistent` | First timestamp the object persisted / 物体首次持续出现时间 |
| `last_persistent` | Last timestamp the object persisted / 物体末次持续出现时间 |

### Output Size Notes / 输出大小说明

The `dsg.json` file is very large (~239 MB) and cannot be directly used as LLM task planner input. A preprocessing step is needed to extract a simplified scene graph (like the `scene_graph.json` format used in `LLM-robot-taskplanning-chaining/`).

`dsg.json` 文件非常大（约 239 MB），不能直接作为 LLM 任务规划器的输入。需要预处理步骤提取简化的场景图（类似 `LLM-robot-taskplanning-chaining/` 中使用的 `scene_graph.json` 格式）。

### Key Observations / 关键发现

- Effective layers: Layer 2 (162 active nodes), Layers 3-5 empty / 有效层级：Layer 2（162 个活跃节点），Layer 3-5 为空
- 141 objects tracked with change records / 141 个物体有变化记录
- DSG snapshots grow over time: 52 MB → 211 MB / 场景图快照随时间增长
- Total output: ~3.3 GB for one run / 单次运行总输出约 3.3 GB
