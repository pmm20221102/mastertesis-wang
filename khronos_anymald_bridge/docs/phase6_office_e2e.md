# Phase 6 — 接 LLM+RAG 完整 Office 闭环报告

> **Phase 6 产出**（2026-08-09）
> 代码：`mock_anymald/executor.py`、`navigation_goal_compiler.py`、`run_office_e2e.py`
> 测试：`tests/test_phase6.py`（5 个，共 26 个全过）
> 产出：`generated/office_e2e_trace.txt`（论文可用 trace）

---

## 1. 接入方式（计划 §29 Rule 1：只消费 target_ref，不重做 LLM/RAG）

- 现有 LLM+RAG 系统输出 **`task_chain` 的 `navigate_to` step + `target_id`**（DSG 节点 id）
- 本阶段通过 `NavigationGoalCompiler` **只消费 target_ref**（Room node id / Place node id / "R12"）
- 现有系统 `config_rag.yaml` 的 vector_db 指向 WSL 旧路径（`khronos_office_new`，工作站不存在），属旧数据依赖；**本阶段用与现有系统一致的接口格式作为输入**，可直接替换为真实 RAG 输出

## 2. 新增模块

| 模块 | 职责 |
|---|---|
| `mock_anymald/executor.py` | `MockExecutor` 状态机（IDLE/PLANNING/MOVING/REACHED/NO_PATH/INVALID_GOAL），沿 route 推进 |
| `navigation_goal_compiler.py` | `target_ref → waypoint id`；歧义/无效 target fail closed（抛 InvalidGoal） |
| `run_office_e2e.py` | E2E 闭环 + 论文 trace；支持 `--targets` 或 `--task-plan`（模拟现有 RAG 输出） |

## 3. E2E 运行结果（`generated/office_e2e_trace.txt`）

输入：起点 R0，目标序列 R12 → R4 → R9（office 真实 room）

```
[Intent] PASS
[Planner] PASS
[Grounding:start R0] PASS (waypoint 3896)
[Grounding:R12] PASS
[Semantic→Waypoint] WP16186
[WaypointGraph] VALID
[Route] 3896 → 25371 → 9445 → 9564 → 18326 → 10856 → 16186 (cost 26.1m)
[Reached] PASS (n_waypoints=7)
[Grounding:R4] PASS
[Semantic→Waypoint] WP26905
[Route] 16186 → ... → 26905 (cost 28.8m)
[Reached] PASS (n_waypoints=8)
[Grounding:R9] PASS
[Semantic→Waypoint] WP9445
[Route] 26905 → ... → 9445 (cost 13.8m)
[Reached] PASS (n_waypoints=5)
[TASK] SUCCESS
```

**闭环完整跑通**：语义目标 → waypoint → 规划 → mock 执行 → REACHED → TASK_SUCCESS，格式满足计划 §21 trace 要求。

## 4. 说明

- "先去办公室 A，再去会议室 B" 用当前 Office DSG 的真实 room（R12/R4/R9）替代占位符（计划 §6：真实实体必须来自 Office DSG）
- LLM+RAG 部分以其接口格式接入（未实际联调现有 RAG，因其旧数据依赖）；现有系统可用时只需把其 `task_plan` 输出喂给 `--task-plan`
