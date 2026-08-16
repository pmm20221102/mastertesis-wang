# Phase 4 — Semantic Entity → ANYmal Waypoint 映射报告

> **Phase 4 产出**（2026-08-10，Phase 3R 返工后更新）
> 产出：`generated/semantic_to_waypoint.json`（22 room + 2472 place 映射）
> 代码：`semantic_waypoint_mapper/mapper.py`、`build_semantic_to_waypoint.py`

---

## 1. 输入格式（与现有 LLM+RAG 的实际接口对齐）

现有 LLM+RAG 输出的 target_ref 是 **DSG 节点 id（64 位整数）**（`task_plan_output_rag.json` 实证），不是语义名。
DSG Room 节点无名字（name=''、metadata={}、bbox INVALID），room 身份只有 NodeSymbol index（R0..R21）。

映射表提供两条路径：

| 输入 target_ref | 映射 | 结果 |
|---|---|---|
| DSG Room 节点 id（64 位） | `room_to_waypoint` | room 的 anchor waypoint |
| DSG Place index | `place_to_waypoint` | 该 place 的 sparsified 代表 waypoint |

## 2. Anchor 选择（Phase 3R 返工后）

- 基于 **graph-aware sparsified 图**（R_geo=3.0，geodesic FPS，per-component，261 waypoints）
- Room↔Place 拓扑 → 该 room place 归属的代表集合 → 距 room position 最近者
- 返工改进：place 归属同分量代表（geodesic），**不再跨墙误归属**

## 3. 结果统计（返工后）

| 项 | 值 |
|---|---|
| Room 总数 / 映射成功 | 22 / 22（100%） |
| **unique anchors** | **22 / 22（无共享）** —— 返工前 16 个（多个 room 共享），现在每房间独立 anchor |
| anchor 距离 room position | 0.45 ~ 3.51 m（mean 1.63 m） |
| place_to_waypoint 条目 | 2472（100%） |
| sparsified waypoints | 261 |
| 全部映射值落在 waypoint 集合 | ✓ |

## 4. Fail closed

- room 无 place / 无代表 / 无 position → `waypoint_id: null` + status，不强行映射
- 全部 22 room 当前均成功

## 5. 待验证项

- waypoint id 为原始 Place index（20~28763 稀疏大整数）——ANYmal loader 兼容性需 Phase 10 确认
- Room position 作为 centroid 的准确性；anchor 距离合理但真机可校正

## 6. 使用方式

```bash
docker exec mt-v6 /opt/conda/envs/llm-task-planner/bin/python \
  build_semantic_to_waypoint.py <dsg.json> --R 3.0
```

上层 `target_ref → waypoint_id`：解析 target 为 node id → `room_to_waypoint`（R 层）或 `place_to_waypoint`（P 层）。
