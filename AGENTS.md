# AGENTS.md

Master's thesis workspace: LLM robot task planning system built on Khronos 3D scene graphs.

硕士论文工作区：基于 Khronos 3D 场景图的 LLM 机器人任务规划系统。

## Project Structure / 项目结构

```
Masterthesis-wang/
├── LLM-robot-taskplanning-chaining/     # Baseline LLM task planner / 基础版 LLM 任务规划器
├── LLM-robot-taskplanning-chaining-rag/ # RAG-enhanced task planner / RAG 增强版任务规划器
├── scene-graph-khronos/                 # Khronos ROS2 colcon workspace / Khronos ROS2 工作空间
├── RAG/khronos_3dsg_rag/               # RAG vector DB builder / RAG 向量库构建工具
├── docs/                               # Documentation / 文档
└── log/                                # Logs / 日志
```

## Environment Setup / 环境配置

### Python Projects (LLM planners) / Python 项目（LLM 规划器）

```bash
conda activate llm-task-planner
```

Dependencies / 依赖: `ollama`, `pydantic`, `pyyaml`, `chromadb`, `sentence-transformers`

### ROS2 Project (Khronos) / ROS2 项目（Khronos）

```bash
# Build / 构建
cd scene-graph-khronos
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release

# Must source before running / 运行前必须 source
source install/setup.bash
```

### Ollama (Linux local) / Ollama（Linux 本地）

```bash
# Install / 安装
curl -fsSL https://ollama.com/install.sh | sh

# Pull models / 拉取模型
ollama pull mistral
ollama pull gemma4:e4b
```

- Config `ollama_host: 'http://127.0.0.1:11434'` in config.yaml / 配置文件中设置本地地址

## Run Commands / 运行命令

### LLM Task Planner (baseline) / LLM 任务规划器（基础版）

```bash
conda activate llm-task-planner
cd LLM-robot-taskplanning-chaining
python main.py
```

### LLM Task Planner (RAG) / LLM 任务规划器（RAG 版）

```bash
conda activate llm-task-planner
cd LLM-robot-taskplanning-chaining-rag
python main_rag.py
python main_rag.py -c "go to the entrance" --compare  # Compare mode / 对比模式
```

### RAG Vector DB / RAG 向量库

```bash
cd RAG/khronos_3dsg_rag
python src/main.py build --all
python src/main.py query --all -q "chair near table" --top-k 10
```

### Khronos SLAM

```bash
cd scene-graph-khronos
source install/setup.bash
ros2 launch khronos_ros uhumans2_khronos.launch.yaml
ros2 service call /finish_mapping_and_save std_srvs/srv/Empty  # Save output / 保存输出
```

## Testing / 测试

### Khronos (CTest)

```bash
colcon build --packages-select <package_name> --cmake-args -DBUILD_TESTING=ON
cd build/<package_name> && ctest
cd build/<package_name> && ctest -R <test_name>  # Single test / 单个测试
```

### Linting (Khronos) / 代码检查

```bash
cd scene-graph-khronos/src/khronos
pre-commit run --all-files
```

- C++: clang-format v16 + cpplint (Google style)
- Python: black v23.7

## Architecture Notes / 架构要点

### Data Flow / 数据流

```
Khronos → dsg.json (239MB) → RAG vector DB → LLM task planner → TaskPlan JSON
```

### LLM Planner Pipeline / LLM 规划器流水线

1. Subgraph extraction (keyword/vector matching) / 子图提取（关键词/向量匹配）
2. LLM call (Ollama, Pydantic schema constraint) / LLM 调用（Pydantic schema 约束）
3. JSON cleanup + Schema validation / JSON 清理 + Schema 校验
4. Canonicalization (alias → exact ID) / 规范化（别名 → 精确 ID）
5. Semantic validation (step order, executor, grounding) / 语义校验（步骤顺序、执行器、实体锚定）
6. Repair loop (max 2 attempts) / 修复循环（最多 2 次）
7. Rule-based fallback (deterministic plan) / 规则回退（确定性计划）

### Key Conventions / 关键约定

- `target_id` must be exact scene graph entity ID, never aliases / `target_id` 必须是精确的场景图实体 ID，不能用别名
- Executor is deterministic per action type / 执行器按动作类型确定: `locate_entity` → `scene_graph`, nav → `nav2`, workflow → `external_workflow`
- `task_chain` max 6 steps / `task_chain` 最多 6 步
- Use `logging.getLogger(__name__)`, not `print()` / 日志用 `logging.getLogger(__name__)`，不用 `print()`

### Khronos Package Dependencies / Khronos 包依赖

```
khronos → hydra → spark_dsg, spatial_hash, kimera_pgmo → kimera_rpgo → GTSAM
```

ROS2 wrappers / ROS2 封装: `khronos_ros`, `hydra_ros`, `kimera_pgmo_ros`, `semantic_inference_ros`

## Common Issues / 常见问题

- **Ollama connection failed** / Ollama 连接失败: Check `ollama_host: 'http://127.0.0.1:11434'` in config.yaml
- **Khronos build failed** / Khronos 构建失败: Ensure `source install/setup.bash` was run
- **dsg.json too large** / dsg.json 太大: Must preprocess with RAG module, cannot feed directly to LLM
- **LLM output unstable** / LLM 输出不稳定: Lower `temperature`, keep `seed: 42`
