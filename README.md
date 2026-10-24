# LLM Robot Task Planning with Khronos 3D Scene Graphs

# 基于 Khronos 3D 场景图的 LLM 机器人任务规划系统

A Master's thesis project that combines Large Language Models with Khronos 3D Dynamic Scene Graphs (DSG) for semantic robot task planning in indoor environments. The system translates natural-language instructions into executable robot task chains using RAG-enhanced retrieval from scene graph data.

硕士论文项目，将大语言模型与 Khronos 3D 动态场景图（DSG）结合，用于室内环境中的语义机器人任务规划。系统通过 RAG 增强的场景图数据检索，将自然语言指令转换为可执行的机器人任务链。

> **Current Hospital workflow / 当前医院流程**: the maintained local target is
> WSL Ubuntu 24.04 + ROS 2 Jazzy. For the reproducible Hospital P1000 RAG build
> and the natural-language-to-Nav2 plan-only entry point, follow
> [`RAG/khronos_3dsg_rag/README.md`](RAG/khronos_3dsg_rag/README.md) and
> [`khronos-nav2-bridge/khronos_nav2_bridge/README_HOSPITAL.md`](khronos-nav2-bridge/khronos_nav2_bridge/README_HOSPITAL.md).
> The older generic setup and Office examples below are legacy instructions,
> not the Hospital P1000 run procedure.

---

## Table of Contents / 目录

- [Features / 功能特性](#features--功能特性)
- [Project Structure / 项目结构](#project-structure--项目结构)
- [Architecture / 系统架构](#architecture--系统架构)
- [Prerequisites / 环境要求](#prerequisites--环境要求)
- [Installation / 安装步骤](#installation--安装步骤)
- [Usage / 使用方法](#usage--使用方法)
- [Evaluation / 评估](#evaluation--评估)
- [Testing / 测试](#testing--测试)
- [Common Issues / 常见问题](#common-issues--常见问题)
- [Roadmap / 路线图](#roadmap--路线图)
- [License / 许可证](#license--许可证)

---

## Features / 功能特性

- **LLM Task Planning**: Convert natural-language robot instructions into structured task chains with schema validation / **LLM 任务规划**：将自然语言机器人指令转换为带 Schema 校验的结构化任务链
- **RAG-Enhanced Retrieval**: Vector-based scene graph retrieval for relevant entity extraction / **RAG 增强检索**：基于向量的场景图检索，提取相关实体
- **Khronos SLAM Integration**: Real-time 3D scene graph generation from RGB-D sensors / **Khronos SLAM 集成**：从 RGB-D 传感器实时生成 3D 场景图
- **Multi-Model Support**: Ollama-based local LLM inference (Mistral, Gemma) / **多模型支持**：基于 Ollama 的本地 LLM 推理（Mistral, Gemma）
- **Semantic Validation**: Multi-layer validation including schema, canonicalization, and semantic checks / **语义校验**：多层校验包括 Schema、规范化和语义检查
- **Rule-Based Fallback**: Deterministic task planning when LLM fails / **规则回退**：LLM 失败时的确定性任务规划
- **RAG Evaluation Framework**: Groundtruth-based retrieval quality assessment / **RAG 评估框架**：基于 Groundtruth 的检索质量评估

---

## Project Structure / 项目结构

```
Masterthesis-wang/
├── LLM-robot-taskplanning-chaining/     # Baseline LLM task planner (keyword matching) / 基础版 LLM 任务规划器（关键词匹配）
├── LLM-robot-taskplanning-chaining-rag/ # RAG-enhanced task planner / RAG 增强版任务规划器
├── scene-graph-khronos/                 # Khronos ROS2 colcon workspace / Khronos ROS2 工作空间
│   └── src/
│       ├── khronos/                     # Core Khronos library / 核心 Khronos 库
│       ├── hydra/                       # Hydra scene graph / Hydra 场景图
│       ├── spark_dsg/                   # Dynamic Scene Graph / 动态场景图
│       ├── kimera_pgmo/                 # Pose graph optimization / 位姿图优化
│       └── khronos_ros/                 # ROS2 wrappers / ROS2 封装
├── RAG/
│   └── khronos_3dsg_rag/               # RAG vector DB builder / RAG 向量库构建工具
│       ├── src/
│       │   ├── parse_3dsg.py           # DSG JSON parser / DSG JSON 解析器
│       │   ├── build_documents.py      # RAG document generator / RAG 文档生成器
│       │   ├── build_vector_db.py      # ChromaDB vector index / ChromaDB 向量索引
│       │   └── query_rag.py            # RAG query interface / RAG 查询接口
│       └── outputs/                     # Built vector databases / 构建的向量数据库
├── rag_eval/                            # RAG evaluation framework / RAG 评估框架
│   ├── rag_eval/                        # Core evaluation modules / 核心评估模块
│   ├── scripts/                         # Evaluation scripts / 评估脚本
│   ├── configs/                         # Evaluation configs / 评估配置
│   └── data/
│       ├── groundtruth/                 # Office & apartment groundtruth / 办公室和公寓 Groundtruth
│       └── reports/                     # Evaluation reports / 评估报告
├── docs/                               # Documentation / 文档
└── log/                                # Logs / 日志
```

---

## Architecture / 系统架构

### Data Flow / 数据流

```
User Instruction (自然语言指令)
        ↓
┌─────────────────────────────────────┐
│  Subgraph Extraction (子图提取)      │
│  • Keyword matching (关键词匹配)     │
│  • Vector retrieval (向量检索)        │
└─────────────────────────────────────┘
        ↓
┌─────────────────────────────────────┐
│  LLM Task Planning (LLM 任务规划)    │
│  • Ollama inference (本地推理)        │
│  • Pydantic schema constraint        │
└─────────────────────────────────────┘
        ↓
┌─────────────────────────────────────┐
│  Validation Pipeline (校验流水线)     │
│  • JSON cleanup + Schema validation  │
│  • Canonicalization (alias → ID)     │
│  • Semantic validation               │
│  • Repair loop (max 2 attempts)      │
└─────────────────────────────────────┘
        ↓
    TaskPlan JSON (任务计划)
        ↓
┌─────────────────────────────────────┐
│  Execution (执行)                     │
│  • scene_graph → locate_entity       │
│  • nav2 → navigate_to / escort_to    │
│  • external_workflow → trigger       │
└─────────────────────────────────────┘
```

### LLM Planner Pipeline / LLM 规划器流水线

1. **Subgraph Extraction**: Extract relevant entities from scene graph via keyword/vector matching / **子图提取**：通过关键词/向量匹配从场景图中提取相关实体
2. **LLM Call**: Generate task plan using Ollama with Pydantic schema constraint / **LLM 调用**：使用 Ollama 生成任务计划，Pydantic Schema 约束
3. **JSON Cleanup + Schema Validation**: Parse and validate JSON output / **JSON 清理 + Schema 校验**：解析并校验 JSON 输出
4. **Canonicalization**: Map aliases to exact scene graph entity IDs / **规范化**：将别名映射到精确的场景图实体 ID
5. **Semantic Validation**: Check step order, executor assignment, entity grounding / **语义校验**：检查步骤顺序、执行器分配、实体锚定
6. **Repair Loop**: Retry with error feedback (max 2 attempts) / **修复循环**：带错误反馈重试（最多 2 次）
7. **Rule-Based Fallback**: Deterministic plan when LLM fails / **规则回退**：LLM 失败时的确定性计划

### Khronos Package Dependencies / Khronos 包依赖

```
khronos → hydra → spark_dsg, spatial_hash, kimera_pgmo → kimera_rpgo → GTSAM
```

ROS2 wrappers / ROS2 封装: `khronos_ros`, `hydra_ros`, `kimera_pgmo_ros`, `semantic_inference_ros`

---

## Prerequisites / 环境要求

- **OS**: Ubuntu 22.04 (WSL2 supported) / **操作系统**：Ubuntu 22.04（支持 WSL2）
- **ROS**: ROS2 Humble / **ROS**：ROS2 Humble
- **Python**: 3.10+ / **Python**：3.10+
- **Conda**: Miniconda or Anaconda / **Conda**：Miniconda 或 Anaconda
- **Ollama**: Local LLM inference server / **Ollama**：本地 LLM 推理服务器
- **GPU**: Recommended for Khronos SLAM (CUDA) / **GPU**：推荐用于 Khronos SLAM（CUDA）

---

## Installation / 安装步骤

### 1. Clone Repository / 克隆仓库

```bash
git clone https://gitlab.kit.edu/roberto.corlito/ma_yunsong_wang.git
cd ma_yunsong_wang
```

### 2. Create Conda Environment / 创建 Conda 环境

```bash
conda create -n llm-task-planner python=3.10
conda activate llm-task-planner
pip install ollama pydantic pyyaml chromadb sentence-transformers
```

### 3. Install Ollama / 安装 Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh

# Pull models / 拉取模型
ollama pull mistral
ollama pull gemma4:e4b
```

### 4. Build Khronos ROS2 Workspace / 构建 Khronos ROS2 工作空间

```bash
cd scene-graph-khronos
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
```

### 5. Build RAG Vector Database / 构建 RAG 向量库

```bash
cd RAG/khronos_3dsg_rag
python src/main.py build --all
```

---

## Usage / 使用方法

### LLM Task Planner (Baseline) / LLM 任务规划器（基础版）

```bash
conda activate llm-task-planner
cd LLM-robot-taskplanning-chaining
python main.py
```

Example input / 示例输入:
```
Bring the patient from Room 201 to Operating Room 105, avoid the restricted operating corridor if possible, and trigger the first screening workflow after arrival.
```

Example output / 示例输出:
```json
{
  "status": "valid",
  "task_chain": [
    {"step": 1, "action": "locate_entity", "target_type": "person", "target_id": "patient_01"},
    {"step": 2, "action": "navigate_to", "target_type": "room", "target_id": "room_201"},
    {"step": 3, "action": "escort_to", "target_type": "room", "target_id": "room_105"},
    {"step": 4, "action": "trigger_workflow", "target_type": "workflow", "target_id": "first_screening"}
  ]
}
```

### LLM Task Planner (RAG) / LLM 任务规划器（RAG 版）

```bash
conda activate llm-task-planner
cd LLM-robot-taskplanning-chaining-rag

# Single command / 单条指令
python main_rag.py -c "go to the entrance"

# Compare RAG vs baseline / 对比 RAG 与基础版
python main_rag.py -c "navigate to room 201" --compare

# Interactive mode / 交互模式
python main_rag.py
```

### RAG Vector Database / RAG 向量库

```bash
cd RAG/khronos_3dsg_rag

# Build all databases / 构建所有数据库
python src/main.py build --all

# Query / 查询
python src/main.py query --all -q "chair near table" --top-k 10

# List available databases / 列出可用数据库
python src/main.py query --list
```

### Khronos SLAM / Khronos SLAM 建图

```bash
cd scene-graph-khronos
source install/setup.bash

# Launch SLAM / 启动 SLAM
ros2 launch khronos_ros uhumans2_khronos.launch.yaml

# Save output / 保存输出
ros2 service call /finish_mapping_and_save std_srvs/srv/Empty
```

---

## Evaluation / 评估

### RAG Evaluation Framework / RAG 评估框架

The `rag_eval/` subproject provides groundtruth-based evaluation for the RAG retrieval system.

`rag_eval/` 子项目提供基于 Groundtruth 的 RAG 检索系统评估。

```bash
conda activate llm-task-planner
cd rag_eval

# Generate evaluation cases / 生成评估用例
python scripts/build_eval_cases.py \
    --dataset office \
    --gt-dir data/groundtruth/office \
    --output data/eval_cases/office_cases.jsonl

# Run evaluation / 运行评估
python scripts/run_eval.py --config configs/eval_office.yaml

# Inspect RAG index / 检查 RAG 索引
python scripts/inspect_index.py --db-dir ../RAG/khronos_3dsg_rag/outputs/khronos_office/vector_db
```

### Evaluation Metrics / 评估指标

| Metric / 指标 | Description / 说明 |
|--------------|-------------------|
| Hit@k | Whether at least one correct result appears in top-k / top-k 中是否包含至少一个正确结果 |
| MRR@k | Mean Reciprocal Rank of first correct result / 第一个正确结果的平均排名倒数 |
| Recall@k | Proportion of relevant items retrieved in top-k / top-k 中检索到的相关项比例 |
| Precision@k | Proportion of retrieved items that are relevant / 检索结果中相关项的比例 |
| Index Coverage | Proportion of groundtruth entities in RAG index / RAG 索引中 Groundtruth 实体的比例 |

### Evaluation Query Types / 评估查询类型

- **Object Symbol Retrieval**: "Retrieve information about object O(370)" / **物体符号检索**
- **Object Attribute Retrieval**: "What is the bounding box of O(370)?" / **物体属性检索**
- **Change Retrieval**: "Which objects disappeared during the sequence?" / **变化检索**

---

## Testing / 测试

### Khronos CTest / Khronos 单元测试

```bash
cd scene-graph-khronos

# Build with tests / 构建并启用测试
colcon build --packages-select <package_name> --cmake-args -DBUILD_TESTING=ON

# Run all tests / 运行所有测试
cd build/<package_name> && ctest

# Run single test / 运行单个测试
cd build/<package_name> && ctest -R <test_name>
```

### Linting / 代码检查

```bash
cd scene-graph-khronos/src/khronos
pre-commit run --all-files
```

- **C++**: clang-format v16 + cpplint (Google style)
- **Python**: black v23.7

---

## Common Issues / 常见问题

| Issue / 问题 | Solution / 解决方案 |
|-------------|-------------------|
| Ollama connection failed / Ollama 连接失败 | Check `ollama_host: 'http://127.0.0.1:11434'` in config.yaml / 检查配置文件中的 Ollama 地址 |
| Khronos build failed / Khronos 构建失败 | Ensure `source install/setup.bash` was run / 确保已执行 source 命令 |
| dsg.json too large / dsg.json 太大 | Must preprocess with RAG module, cannot feed directly to LLM / 必须通过 RAG 模块预处理，不能直接输入 LLM |
| LLM output unstable / LLM 输出不稳定 | Lower `temperature`, keep `seed: 42` / 降低 temperature，保持 seed: 42 |
| ChromaDB import error / ChromaDB 导入错误 | Run `pip install chromadb` in conda environment / 在 conda 环境中安装 chromadb |

---

## Roadmap / 路线图

- [x] Baseline LLM task planner with schema validation / 基础版 LLM 任务规划器（带 Schema 校验）
- [x] RAG-enhanced task planner / RAG 增强版任务规划器
- [x] Khronos SLAM integration / Khronos SLAM 集成
- [x] RAG evaluation framework / RAG 评估框架
- [ ] Multi-language support (EN/DE/ZH) / 多语言支持
- [ ] Temporal query support / 时序查询支持
- [ ] Real-time scene graph update / 实时场景图更新
- [ ] Physical robot deployment / 实体机器人部署

---

## License / 许可证

This project is for academic use only (Master's thesis).

本项目仅用于学术用途（硕士论文）。

---

## Authors / 作者

- **Yunsong Wang** - Master's student at KIT (Karlsruhe Institute of Technology)
- **Supervisor**: Roberto Corlito
