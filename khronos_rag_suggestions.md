# Khronos DSG 作为 RAG 数据源接入 Task Planner 的处理建议

## 目标

将 Khronos SLAM 跑出的结构化场景图（DSG）作为 RAG 数据源，接入 LLM-robot-taskplanning-chaining 系统，实现：

1. 用户输入自然语言导航指令
2. LLM 从 Khronos 生成的场景图中检索相关 entities
3. 基于检索到的 subgraph 生成 task_chain

---

## 数据现状分析

### Khronos DSG 输出（snapshots/00000）

| 文件 | 说明 |
|------|------|
| `frontend/dsg.json` | 前端场景图 JSON，502 nodes + 339 edges |
| `frontend/dsg_with_mesh.json` | 包含 mesh 顶点数据的完整场景图 |
| `maps/dsg_*.json` | 12 个时序快照（不同时间点的场景状态） |
| `object_changes.csv` | 物体出现/消失时间线 |
| `hydra_config.yaml` | 语义标签映射表（19 类） |

**节点分布：**
- layer=2, partition=0: 162 个 OBJECTS（语义物体，带 bounding_box、semantic_label、mesh）
- layer=2, partition=97: 340 个 AGENTS（人类轨迹，带 position、trajectory）

### Task Planner 输入格式（scene_graph.json）

```json
{
  "scene_id": "hospital_demo_scene_graph_001",
  "scene_type": "hospital_like_indoor_environment",
  "description": "A three-floor hospital environment...",
  "entities": [
    {
      "id": "room_105",
      "type": "operating_room",
      "name": "OR 105",
      "function": "operating_room",
      "description": "Operating Room 105 located on the first floor..."
    }
  ],
  "relations": [
    {"from": "floor_1", "to": "room_105", "relation": "contains"}
  ]
}
```

### 格式差异对比

| 维度 | Khronos DSG | Task Planner 需要 |
|------|-------------|-------------------|
| ID | 64 位整数 (`5692549928996306944`) | 可读字符串 (`room_101`) |
| 层级 | 分层编号 (layer 2/3/4/5) | 扁平列表，用 `type` 区分 |
| 语义 | `semantic_label: 11` (数字) | `type: "operating_room"` |
| 关系 | 数字 ID 边，无语义 | `{"relation": "contains"}` |
| 描述 | 无（只有几何信息） | 自然语言 description |
| 状态 | `is_active`, `first/last_observed_ns` | `state: "empty"/"occupied"` |

**核心缺失**：Khronos 输出的是几何+数字标签的原始图，task planner 需要可读的、有自然语言描述的场景图。中间缺一层**语义翻译**。

---

## 处理流程（4 步）

### 第 1 步：DSG → 结构化中间格式

从 DSG JSON 中提取每个 node 的可读信息：

```
输入: 502 个 nodes + 339 条 edges
输出: 每个 node 的结构化信息
  - id: 字符串化（原 64 位整数 → "obj_5692549928996306944"）
  - layer → entity type 映射:
      layer 2, partition 0 → object
      layer 2, partition 97 → agent (human)
      layer 3 → place
      layer 4 → room
      layer 5 → building
  - semantic_label → 查 hydra_config.yaml 的 label_names:
      0=Unknown, 1=Ventilation/Piping, 2=Small office objects,
      3=Large static wall furniture, 4=Large office objects,
      5=Floor, 6=Ceiling, 7=Jail cell, 8=Bathroom,
      9=Bedroom, 10=Shelving, 11=Chairs, 12=Walls,
      13=Doors, 14=Elevator, 15=Signs, 16=Lighting,
      17=Humans, 18=Railing
  - bounding_box.center → position (x, y, z)
  - is_active, first/last_observed → 状态
```

**关键点**：Khronos 输出中没有自然语言 name 和 description，需要在后续步骤补全。

### 第 2 步：语义标注补全（最关键）

Khronos 的 `semantic_label` 只有 19 类（Floor, Walls, Chairs, Humans...），粒度太粗。Task planner 需要区分 `operating_room` vs `patient_room` vs `storage_room`。

**补全信息的来源：**

1. **数据集 ground truth**：如果是 tesse_cd_office 数据集，场景原本有语义标注
2. **LLM 推断**：把 bounding box + semantic_label + 位置关系喂给 LLM，让它推断：
   - "这个物体大概是什么"
   - "这个区域的功能是什么"
   - "这个空间的自然语言描述"
3. **人工标注**：对关键区域手动命名（最可靠但成本高）

**推荐方案**：用 LLM 批量推断 + 人工抽检验证。

### 第 3 步：层级关系构建

Khronos DSG 的 edges 主要是 agent 轨迹（partition 97 节点之间），**没有明确的 contains/adjacent_to 关系**。

需要从几何数据推导：

```
规则推导（可自动化）：
- contains: A.bounding_box 包含 B.center → A contains B
- adjacent_to: 两个 room 的 bounding_box 距离 < 阈值 → adjacent_to
- located_in: object.center 在某个 room 的 bounding_box 内 → located_in
- on: object.center.z 在某个 surface 的 z 范围内 → on
```

这些关系可以写规则自动推导，不完全依赖 LLM。

### 第 4 步：组装成 scene_graph.json

最终输出格式：

```json
{
  "scene_id": "khronos_office_00000",
  "scene_type": "office_environment",
  "description": "An office space mapped by Khronos SLAM, containing desks, chairs, and corridor areas.",
  "entities": [
    {
      "id": "obj_5692549928996306944",
      "type": "chair",
      "name": "Chair near entrance",
      "function": "seating",
      "description": "A chair detected by Khronos, located at (-15.36, 22.27, 1.37), observed from 7.69s to 9.34s."
    },
    {
      "id": "area_entrance",
      "type": "corridor",
      "name": "Entrance Area",
      "function": "passage",
      "description": "The entrance corridor connecting the main office area to the outside."
    }
  ],
  "relations": [
    {"from": "area_entrance", "to": "obj_5692549928996306944", "relation": "contains"}
  ]
}
```

---

## RAG 检索架构

转换后的场景图可以两种方式接入：

### 方式 A：直接注入 prompt（简单）

将整个 scene_graph.json 作为 subgraph 喂给 LLM，走现有 `extract_relevant_subgraph()` 流程。

优点：无需额外基础设施
缺点：场景大时 token 超限

### 方式 B：向量检索（推荐）

将场景图拆成检索条目，存入向量数据库：

```json
{
  "text": "Chair at position (-15.36, 22.27, 1.37), type=chairs, in entrance area",
  "metadata": {
    "entity_id": "obj_5692549928996306944",
    "layer": 2,
    "semantic_label": 11,
    "position": [-15.36, 22.27, 1.37]
  }
}
```

用户说 "go to the chair near the entrance" 时：
1. 向量检索匹配到相关 entities
2. 组装成 subgraph
3. LLM 生成 task_chain

**推荐工具**：Chroma（轻量）或 FAISS（高性能）

### 混合方案（最佳）

- 几何关系（contains, adjacent_to）用规则推导
- 语义描述（name, description）用 LLM 生成
- 检索用向量数据库 + 关键词过滤

---

## 时序数据的价值

`maps/` 下的 12 个时序快照 + `object_changes.csv` 可以提供：

| 数据 | 用途 |
|------|------|
| 物体出现/消失时间线 | `constraints.conditions`（如 "chair must be present"） |
| Agent 轨迹 | 动态避障（"avoid human_path at time T"） |
| 场景变化 | 检测环境变化，更新场景图 |

**但时序数据在初期可以不接入**，先用静态场景图验证 task planning 效果。

---

## 优先级

| 优先级 | 任务 | 工作量 | 说明 |
|--------|------|--------|------|
| P0 | DSG → scene_graph.json 格式转换脚本 | 2-3 天 | 解决格式兼容问题 |
| P0 | LLM 补全 name/description | 1-2 天 | 解决语义缺失问题 |
| P1 | 几何关系推导（contains/adjacent_to） | 1 天 | 解决层级关系问题 |
| P1 | 接入 task planner 验证效果 | 1-2 天 | 端到端测试 |
| P2 | 向量化存入 Chroma/FAISS | 2-3 天 | 大场景下的检索优化 |
| P2 | 时序数据接入 | 3-5 天 | 动态场景支持 |

---

## 注意事项

1. **ID 映射需要持久化**：Khronos 的 64 位 ID 和转换后的可读 ID 需要维护映射表
2. **场景图需要定期更新**：Khronos 是在线 SLAM，场景图会持续变化，需要增量更新机制
3. **语义标签的局限性**：Khronos 的 19 类标签可能不够细，考虑扩展或组合使用
4. **边界情况**：Khronos 可能检测到未识别物体（label=0），需要 fallback 处理
