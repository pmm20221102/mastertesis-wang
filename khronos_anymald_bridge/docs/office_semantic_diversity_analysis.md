# Office 数据集语义多样性限制：aggregate "the room with the most X" 收敛 R19

> 2026-08-11 · 依据真实 Office DSG（`canonical_graph_v2.json`，22 rooms / 209 objects / 6 种 object label）
> 背景：`final_demo_llm_rag_authenticity_fix.md` §5 建议用 aggregate semantic room query
> （"the room with the most chairs" 等）作为最终 Demo 目标，以展示系统从自然语言语义
> 自行定位房间。本分析记录真实数据下的筛选结果：**该目标形式在本数据集不可行**。

---

## 1. 方法

对 canonical graph 做确定性统计：每个 object label 在各 room 的对象数（`object.room_id` 归属），
取最大者即 "the room with the most X" 的确定性答案。

## 2. 结果：5 个 label 的 max room 全部是 R19

| semantic label | R19 对象数 | 第二多 room（对象数） | 唯一答案 |
|---|---|---|---|
| `chairs` | 8 | R14 (7) | ✅ |
| `small_office_objects` | 20 | R6 (14) | ✅ |
| `large_static_wall_furniture` | 22 | R6 (12) | ✅ |
| `large_office_objects` | 6 | R14 (3) | ✅ |
| `signs` | 6 | R0/R14 (3) | ✅ |

即 "the room with the most chairs"、"the room with the most small office items"、
"the room with the most wall-mounted storage"、"the room with the most large office
machines"、"the room with the most signs" 五个查询**全部收敛到同一房间 R19**，
无法产生 5 个不同的导航目标。

## 3. label × room 完整矩阵（object count）

| label | R0 | R4 | R6 | R7 | R8 | R9 | R12 | R14 | R15 | R19 |
|---|---|---|---|---|---|---|---|---|---|---|
| chairs | 4 | 1 | 3 | 2 | 4 | . | 6 | 7 | . | **8** |
| large_office_objects | . | 1 | . | . | . | . | 1 | 3 | 1 | **6** |
| large_static_wall_furniture | . | 5 | 12 | 1 | 8 | 3 | 3 | 11 | 1 | **22** |
| signs | 3 | 1 | . | . | . | . | . | 3 | . | **6** |
| small_office_objects | 5 | 3 | 14 | 5 | 4 | 4 | 13 | 12 | 1 | **20** |

每房间对象总数：R0=12, R4=11, R6=29, R7=8, R8=16, R9=7, R12=23, R14=36, R15=3,
**R19=62（占全部 209 的 30%）**。

## 4. 原因分析

1. **R19 是最大的开放空间**：62 个对象（30%），5 类 label 均在此聚集最多；
   其余房间无任何 label 能在数量上超过它。
2. **room 节点无语义**：22 个 room 的 `label`/`name` 全部为 `'unknown'`（无房间类型/名称）。
3. **agents / places 无语义**：392 agents、2472 places 的 label 均为空。
4. **object 无细粒度语义**：209 objects 无 `name`/`attributes`/`relations`，且全部 `is_active=False`。
5. **冻结系统唯一稳定验证的 room 级语义是 "object + room number"**（benchmark
   room_resolved 案例，如 "Find a chair in room 0756" → R4），而非无编号语义。

## 5. 结论

无编号、纯语义的 room 描述在本数据集中**无法唯一区分除 R19 外的房间**，
aggregate "the room with the most X" 因此无法构造 5 个不同 room 目标。
这是 **Office 数据集的语义多样性限制**，不是系统能力缺陷。

用户裁决（2026-08-11）：最终 Demo 采用 **room-number constrained semantic navigation**——
room number 为显式空间约束，语义 object label 仍由冻结 LLM+RAG 解析与 grounding
（见 `docs/final_e2e_demo_report_v2.md`）。本分析结果保存为数据集语义多样性限制，
不作为最终 Demo 的能力展示。

## 6. 论文建议表述

> The Office dataset exhibits limited semantic diversity at the room level: rooms carry
> no semantic labels, and all object categories concentrate most instances in a single
> large open space (R19). Consequently, purely semantic room queries ("the room with the
> most X") converge to a single room and cannot define five distinct navigation targets.
> The final demonstration therefore uses room-number-constrained semantic navigation, in
> which room numbers are explicit spatial constraints while object semantics are still
> resolved by the frozen LLM+RAG pipeline.
