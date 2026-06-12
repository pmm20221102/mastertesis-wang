# RAG-Enhanced Task Planner / RAG 增强版任务规划器

基于 Khronos 3D 场景图的 RAG 增强版任务规划器，用于机器人室内导航。

RAG-enhanced task planner for robot indoor navigation, built on Khronos 3D scene graphs.

## 与原项目的区别 / Comparison

| | 原项目 Baseline | RAG 版 Enhanced |
|---|---|---|
| 场景图来源 Scene graph source | 预构建的 `scene_graph.json` | RAG 向量库动态检索 |
| 子图提取 Subgraph extraction | 关键词匹配 + 评分 | 向量检索 + 自动补全 |
| 适用场景 Use case | 固定小场景 | 大规模 Khronos 场景图 |

## 流程 / Pipeline

```
用户指令 → RAG 检索相关实体 → 组装上下文 → LLM 任务分解 → TaskPlan
User instruction → RAG retrieves entities → Build context → LLM plans tasks → TaskPlan
```

## 安装 / Setup

```bash
conda activate llm-task-planner
# 依赖已预装，无需额外安装
```

## 使用 / Usage

### 1. 修改配置 / Edit config

编辑 `config_rag.yaml`，修改 `input_command`：

```yaml
input_command: "navigate to the room with chairs"
```

### 2. 运行 / Run

```bash
# 直接运行（使用 config 中的指令）
python main_rag.py

# 或通过命令行指定指令
python main_rag.py -c "go to the building entrance"

# 指定模型
python main_rag.py -c "navigate to room 201" -m gemma4:e4b

# 对比 RAG vs Baseline
python main_rag.py -c "go to the entrance" --compare

# 跳过 LLM 关键词提取（纯向量检索）
python main_rag.py -c "navigate to the room with chairs" --no-llm-keywords
```

### 3. 交互模式 / Interactive mode

```bash
python main_rag.py
# 输入指令后回车，输入 quit 退出
```

## 配置说明 / Configuration

`config_rag.yaml` 主要配置项：

| 字段 Field | 说明 Description |
|---|---|
| `ollama_host` | Ollama 服务地址 |
| `input_command` | 输入指令（CLI 参数优先） |
| `models` | 可用模型列表 |
| `options` | 推理参数（temperature 等） |
| `rag.db_dir` | RAG 向量库路径 |

## 项目结构 / Structure

```
LLM-robot-taskplanning-chaining-rag/
├── main_rag.py          # 主入口 / Main entry
├── rag_retriever.py     # RAG 检索模块 / RAG retrieval
├── schema.py            # 数据模型 / Data models
├── prompt.py            # Prompt 模板 / Prompt template
├── config_rag.yaml      # 配置 / Configuration
└── README.md
```

## 推荐模型 / Recommended model

- **gemma4:e4b** — schema 遵循能力更好
- **mistral** — 通用推理更强，但 schema 遵循不稳定

## 输出 / Output

结果保存到 `task_plan_output_rag.json`，格式：

```json
{
  "status": "valid",
  "task_chain": [
    {
      "step": 1,
      "action": "navigate_to",
      "target_type": "room",
      "target_id": "5908722711110090752",
      "executor": "nav2",
      "constraints": {"avoid": [], "prefer": [], "conditions": []}
    }
  ]
}
```
