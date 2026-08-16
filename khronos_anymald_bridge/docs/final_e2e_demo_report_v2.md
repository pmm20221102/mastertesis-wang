# 最终 E2E Demo 交付报告 v2（room-number constrained semantic navigation）

> 对照 `final_demo_llm_rag_authenticity_fix.md`（2026-08-11）完成。
> **数据现实**：Office DSG 缺乏 room-level 语义 label/attributes，无编号纯语义无法构造
> 5 个不同 room（见 `docs/office_semantic_diversity_analysis.md`，aggregate "most X" 全收敛 R19）。
> **用户裁决**：采用 room-number constrained semantic navigation——room number 为显式空间约束，
> 语义 object label 仍由冻结 LLM+RAG 解析与 grounding。

---

## 1. 最终自然语言指令（一条完整英文）

```text
Starting from your current position, first navigate to the chair in room 0756, then navigate
to the wall-mounted storage unit in room 0759, then navigate to the small office item in room
0758, then navigate to the large office machine in room 0764, and finally navigate to the
wall-mounted storage unit in room 0767. Stop briefly at each destination before continuing
to the next one.
```

- 整条指令**一次**进入冻结 LLM Planner，不预拆句、不 TSP 重排。
- 无 R* / DSG ID / waypoint ID；room number 为显式空间约束。
- 5 个目标房间：R4 / R7 / R6 / R12 / R15（互异，均 ≠ start R0）。

## 2. 真实链路（全部为冻结组件）

```text
final_instruction
→ 冻结 LLM Intent/Planner（parse_intent_with_llm）→ 5 ordered navigate steps
→ 每步冻结 Semantic RAG（search_semantic → predicted label）
→ 每步冻结 Graph Grounding（canonical room + label 一致性，predicted 驱动、无 GT 泄漏）
→ target_ref（R…）→ semantic_to_waypoint → waypoint
→ witness-aware planner → route → RViz2 mock ANYmal D
```

`demo/build_final_multistep_taskplan.py` 实现；5/5 action=navigate 必须由 LLM 真实输出，
禁止 locate→navigate 改写；grounding 失败 / 步数≠5 / 顺序不一致均 fail（不静默修复）。

## 3. 五目标表（LLM action / Semantic RAG / Grounding / waypoint / route）

| # | Human semantic phrase | LLM action | Semantic RAG top1 | Grounding target_ref | Waypoint | Route cost |
|---:|---|---|---|---|---:|---:|
| 1 | the chair in room 0756 | navigate | chairs | R4 | 4647 | 17.3m |
| 2 | the wall-mounted storage unit in room 0759 | navigate | large_static_wall_furniture | R7 | 6817 | 5.7m |
| 3 | the small office item in room 0758 | navigate | small_office_objects | R6 | 8483 | 8.7m |
| 4 | the large office machine in room 0764 | navigate | large_office_objects | R12 | 12136 | 38.3m |
| 5 | the wall-mounted storage unit in room 0767 | navigate | large_static_wall_furniture | R15 | 22793 | 40.8m |

总路线 **110.8m**（witness projected_xy cost），5 段全非零、全可达、目标房间互异且 ≠ start。

## 4. 上游验证（md §11：非硬编码）

`final_task_plan.json` 携带 `upstream_validation`，由生成脚本真实写入，`run_office_e2e.py`
实际检查后才输出 PASS：

```text
[Intent] PASS (5/5 action=navigate=True; 冻结 LLM 真实输出，无 locate→navigate 改写)
[Planner] PASS (steps=5/5, order_preserved=True, no_tsp_reorder=True)
```

## 5. Bridge E2E 结果

```text
[Grounding:start R0] PASS (waypoint 4275)
[Grounding:5908722711110090756] PASS → WP4647  → cost 17.3m → Reached PASS
[Grounding:5908722711110090759] PASS → WP6817  → cost 5.7m  → Reached PASS
[Grounding:5908722711110090758] PASS → WP8483  → cost 8.7m  → Reached PASS
[Grounding:5908722711110090764] PASS → WP12136 → cost 38.3m → Reached PASS
[Grounding:5908722711110090767] PASS → WP22793 → cost 40.8m → Reached PASS
[TASK] SUCCESS
```

trace：`demo/final_bridge_trace.txt`。

## 6. 模型复现（md §24）

`demo/final_demo_manifest.json`：

| 项 | 值 |
|---|---|
| model | `mistral:latest` |
| digest | `6577803aa9a036369e481d648a2baebb381ebc6e897f2bb9a766a2aa7bfbc1cf` |
| size | 4,372,824,384 bytes（Q4_K_M, 7.2B） |
| ollama | 0.32.5 |
| inference options | temperature=0.1, top_p=0.9, top_k=40, num_ctx=8192, repeat_penalty=1.1, seed=42 |
| timestamp | 2026-08-11T18:31:06 |

## 6.5 Mesh 集成与显示（RViz）

- **launch 正式集成**：`mock_navigation.launch.py` 新增 `enable_mesh` / `mesh_path` / `wireframe` / `mesh_delay` / `start_delay` 参数，Khronos mesh 由同一 launch 启动（不再依赖手工第二条命令）。启动顺序：RViz → graph/mesh（mesh 延迟 1~2s one-shot，检测订阅者后发布）→ mock robot（`start_delay` 静态停留，肉眼确认 mesh + waypoint + edges 后再动画）。
- **运行前自动验证**（录制脚本内置，任一失败中止）：1) `ros2 node list` 含 `khronos_mesh_publisher`；2) `ros2 topic info /anymald/khronos_mesh -v` Publisher count=1；3) `ros2 topic echo /anymald/khronos_mesh --once` 收到 mesh marker；4) mesh 文件可读。
- **mesh 显示模式（wireframe）**：Khronos mesh 以 LINE_LIST 线框显示（0.1m 亮白线）——OGRE 对实心大 mesh（~100k 顶点）渲染失败（assimp 三角化/顶点缓冲问题），线框无背面剔除、建筑结构清晰；黑背景（参考效果：黑底 + 亮 mesh + waypoint 点线）。
- **坐标显示说明**：waypoint/edges/路径导航层统一悬浮 `DISPLAY_Z=4.6`（display-only XY overlay，**preserve x-y and lift above mesh**）；mesh 为原始 DSG 坐标（z 1.0~4.25）。**不声称 3D 坐标完全重合**，仅在 XY 平面与 mesh 对齐（waypoint 点线投影位置与 mesh 结构对应）。

## 7. 测试与一致性

```text
pytest: 43 passed（34 原有 + 9 新增 test_final_demo_multistep.py）
新增集成测试验证：纯英文无内部 ID / exactly 5 navigate / 顺序保留（无 TSP）/
5/5 RAG+Grounding resolved / 5 target_ref 可转 waypoint / 5 route reachable /
目标房间互异且≠start / manifest digest 非空。
```

## 8. 限制与论文表述

- **本 Demo 验证**：room-number constrained semantic navigation 的端到端链路——
  一条完整自然语言指令由冻结 LLM 分解、每步由冻结 Semantic RAG + Graph Grounding 解析语义
  object label 并 grounding 到指定 room，经 semantic→waypoint 与 witness planner 执行。
- **不声称**：本 Demo 验证了无编号 semantic room discovery（数据集语义多样性限制见
  `docs/office_semantic_diversity_analysis.md`）。
- **论文可用描述**：

> An end-to-end room-number-constrained semantic navigation pipeline was demonstrated in
> RViz2. A single natural-language instruction containing five ordered semantic destinations
> (object + room constraint) was decomposed by the frozen LLM planner into five navigation
> steps. Each step's semantic object label was independently resolved by the frozen Semantic
> RAG and graph-grounding pipeline against the specified DSG room, compiled into
> Khronos-derived ANYmal-D waypoint anchors, planned over the graph-aware waypoint
> representation with witness costs, and executed sequentially by the RViz2 mock ANYmal-D.
> This validates the semantic-to-waypoint software pipeline and spatial consistency in
> RViz2; it does not constitute validation of the proprietary/native ANYmal-D locomotion
> or mission-control stack, nor of unconstrained semantic room discovery.
