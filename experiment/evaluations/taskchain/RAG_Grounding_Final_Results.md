# RAG Grounding Module — Final Results / RAG Grounding 模块 — 最终成果

## Overview / 概述

This document records the final state of the Khronos 3DSG RAG Grounding module after iterative optimization from v14 through v18.

本文档记录 Khronos 3DSG RAG Grounding 模块经过 v14 至 v18 迭代优化后的最终状态。

---

## 1. System Architecture / 系统架构

### Pipeline / 流水线

```
Natural Language Instruction
    ↓
LLM Intent Parser (structured TaskIntent)
    ↓
Grounding Orchestrator
    ├── EntityResolver (semantic candidate generation)
    ├── SceneGraphRAG.resolve_entity() (hard constraints + fast paths)
    │   ├── Exact ID fast path
    │   ├── Nearest-by-position fast path (canonical index)
    │   ├── All-by-label fast path (canonical index)
    │   └── Standard semantic path (EntityResolver + hard filters)
    └── LocationConstraint / RelationConstraint filtering
    ↓
Grounded Task (resolved node IDs)
    ↓
LLM Planner (PlanDraft → TaskPlan)
    ↓
Validator (role-specific, step-specific)
```

### Key Components / 核心组件

| Component | File | Purpose |
|-----------|------|---------|
| Canonical Graph Exporter | `RAG/khronos_3dsg_rag/src/export_canonical_graph.py` | Direct raw DSG → canonical graph |
| Canonical Index | `RAG/khronos_3dsg_rag/src/canonical_index.py` | v1/v2 dual-format loading |
| Scene Graph API | `RAG/khronos_3dsg_rag/src/scene_graph_api.py` | Entity resolution with hard constraints |
| Entity Resolver | `RAG/khronos_3dsg_rag/src/entity_resolver.py` | Semantic candidate generation |
| Grounding Orchestrator | `llm-rag/planner/grounding/orchestrator.py` | Role dependency + aggregate |
| Intent Schema | `llm-rag/planner/planner_schemas/intent.py` | LocationConstraint, RelationConstraint |
| Evaluator | `llm-rag/planner/eval/run_pipeline_eval.py` | Strict multi-metric evaluation |
| Benchmark Generator | `llm-rag/planner/eval/generate_grounding_benchmark_v2.py` | v3 benchmark with sorted IDs |
| Benchmark Validator | `llm-rag/planner/eval/validate_grounding_benchmark_v2.py` | Hash integrity + ground truth |

---

## 2. Canonical Graph v2 / 规范图 v2

### Data Source / 数据来源

Raw DSG (Khronos output): `RAG/khronos_3dsg_rag/data/dsg.json`
- 1,858 nodes, 6,205 edges
- Scene: khronos_office (2026-06-20)

### Canonical Graph Statistics / 规范图统计

| Metric | Value |
|--------|-------|
| Total nodes | 1,858 |
| Object nodes | 191 |
| Agent nodes | 394 |
| Place nodes | 1,262 |
| Room nodes | 10 |
| Building nodes | 1 |
| Hierarchy edges | 1,867 |
| Neighbor edges | 4,338 |
| Hierarchy validity | 100% |
| Object → Place coverage | 100% |
| Place → Room coverage | 98.9% |
| Position coverage | 100% (1,262/1,262 unique Place positions) |

### Key Fix / 关键修复

v14 used RAG chunk metadata to build canonical graph → hierarchy pollution (26.4% validity).
v15 introduced `export_canonical_graph.py` for direct raw DSG export → 100% validity.

v14 使用 RAG chunk metadata 构建规范图 → 层级污染（26.4% 有效率）。
v15 引入 `export_canonical_graph.py` 直接从 raw DSG 导出 → 100% 有效率。

---

## 3. Benchmark v3 / 基准测试 v3

### Status / 状态

```
benchmark_status: ready
case_count: 100
byte_identical_reproducibility: true
manifest_hash_integrity: true
```

### Category Distribution / 类别分布

| Category | Count | Description |
|----------|-------|-------------|
| ambiguous | 5 | Multiple instances of same label |
| obj_nav | 10 | Navigate to ambiguous label |
| place_constraint | 15 | Object constrained by Place |
| room_constraint | 10 | Object constrained by Room ID |
| set_query | 10 | Find all instances of a label |
| not_found | 10 | Non-existent entities |
| spatial | 10 | Nearest Place to reference position |
| exact_id | 5 | Direct node ID lookup |
| complex | 10 | Multiple constraints (Place+Room, aggregate) |
| relation | 15 | inside/near/connected_to/aggregate |

### Reproducibility / 可复现性

- Fixed seed (42) + sorted candidate IDs → byte-identical output
- Manifest records: benchmark_file_hash, canonical_graph_hash, generator_code_hash
- Same canonical graph + same seed → identical benchmark file

固定 seed (42) + 排序的 candidate IDs → 字节级相同输出
Manifest 记录：benchmark_file_hash, canonical_graph_hash, generator_code_hash
相同 canonical graph + 相同 seed → 相同 benchmark 文件

---

## 4. Evaluation Results / 评估结果

### Oracle-Intent Mode (v3 benchmark) / Oracle 意图模式

| Metric | Value |
|--------|-------|
| Overall pipeline status | 100% |
| Grounding exact success | 100/100 |
| Natural-language accuracy | 100% |
| Role entity-ID accuracy | 100% |
| Set macro P/R/F1 | 1.0 / 1.0 / 1.0 |
| Set exact match | 10/10 |
| Spatial target-ID accuracy | 10/10 |
| Spatial distance error | 0.0m |
| Case failure count | 0 |
| Avg latency | 271ms |

### Category Breakdown / 分类别结果

| Category | Accuracy | Cases |
|----------|----------|-------|
| ambiguous | 100% | 5/5 |
| obj_nav | 100% | 10/10 |
| place_constraint | 100% | 15/15 |
| room_constraint | 100% | 10/10 |
| set_query | 100% | 10/10 |
| not_found | 100% | 10/10 |
| spatial | 100% | 10/10 |
| exact_id | 100% | 5/5 |
| complex | 100% | 10/10 |
| relation | 100% | 15/15 |

---

## 5. Key Technical Improvements / 关键技术改进

### v14 → v15: Canonical Graph Fix / 规范图修复

- Direct raw DSG canonical exporter (`export_canonical_graph.py`)
- Hierarchy validity: 26.4% → 100%
- Object→Place coverage: 0% → 100%
- Place→Room coverage: 21.6% → 98.9%

### v15 → v16: Hard Constraints / 硬约束

- `node_type` hard filter in `resolve_entity()`
- `label` hard filter (exact canonical match)
- Nearest query uses canonical index directly (not semantic Top-K)
- All query uses canonical index directly (exact label/type set)
- Set P/R/F1: 0.616/1.0/0.763 → 1.0/1.0/1.0

### v16 → v17: Complex/Relation Schema / 复杂/关系 Schema

- `LocationConstraint` schema (Place + Room simultaneously)
- `RelationConstraint.target_node_id` (full node ID)
- `count_in_location` aggregate operation
- `aggregate_value` in ResolutionResult
- Orchestrator uses `target_node_id` for relation resolution

### v17 → v18: Evaluation & Benchmark Freeze / 评估与基准冻结

- Strict `grounding_case_success` per-category logic
- Set macro/micro P/R/F1 in summary
- Spatial target-ID accuracy + distance error
- Complete failure file (uses `case_failure`, not just status)
- Byte-identical benchmark reproducibility
- Manifest hash integrity
- `target_ref` required (no silent fallback)
- Step-specific references (`step_N_target`)
- Transitive contains/inside via BFS descendants

---

## 6. Remaining Work / 剩余工作

The RAG Grounding module is now **complete**. The next phase should be:

RAG Grounding 模块现已**完成**。下一阶段应为：

1. **LLM Intent Parser Evaluation** — 100-case evaluation with qwen2.5/gemma4
2. **LLM Task Planning Evaluation** — 50-case oracle-grounding planner benchmark
3. **End-to-End Evaluation** — full pipeline from natural language to task plan
4. **Cross-dataset Generalization** — test on different indoor environments

---

## 7. Paper-Ready Claims / 论文可用结论

### Can write / 可以写：

> Given oracle structured intents, the v18 scene-graph Grounding backend achieved 100% pipeline-status accuracy, 100% resolved target-ID accuracy, 100% set exact match, and 100% spatial target-ID accuracy across all 100 development benchmark cases. The canonical graph exporter achieved 100% hierarchy validity with 100% Object-to-Place and 98.9% Place-to-Room coverage.

给定 oracle 结构化意图，v18 场景图 Grounding 后端在全部 100 条开发基准测试中实现了 100% 的流水线状态准确率、100% 的解析目标 ID 准确率、100% 的集合精确匹配和 100% 的空间目标 ID 准确率。规范图导出器实现了 100% 的层级有效率，Object→Place 覆盖率 100%，Place→Room 覆盖率 98.9%。

### Cannot write / 不应写：

- End-to-end LLM+RAG accuracy = 100% (not tested with real LLM)
- Natural-language understanding accuracy = 100% (oracle intent mode only)
- Cross-dataset generalization is proven (not tested)
