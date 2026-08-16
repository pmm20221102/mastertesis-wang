# Audit Response — 返工完成确认报告

> 对照 `khronos_anymald_phase0_8_audit.md` 逐条回应（2026-08-10）
> 状态：Phase 0-8 全部按审计意见返工/确认；新增 Phase 8.5 RViz2 Mock

---

## 0. 总结论

审计的三个核心判断全部接受并已处理：

1. **Phase 3 必须返工** → 已完成 **Phase 3R**（graph-aware sparsification），旧算法弃用。
2. **Phase 4-8 的"导航正确性"结论需在修复后重跑** → 已全部重跑（semantic / planner / benchmark / E2E / recheck）。
3. **当前用户目标 = RViz2 mock 闭环，不需要 ANYmal 动力学** → 已实现 Phase 8.5 RViz2 mock，未进入 dynamics / Isaac / real robot。

**当前准确表述**：Khronos DSG/PLACES → graph-aware waypoint graph（stretch-aware, 6 分量保持）→ semantic→waypoint → Dijkstra→witness route → RViz2 mock 动画，全链路软件闭环成立；**不声称 ANYmal 物理/原生栈兼容已验证**。

---

## 1. 逐阶段回应

### Phase 0 — 参考项目审计（audit §2：无需修改）
- ✅ 保留。未重复开发 LLM/RAG，最终对象保持 ANYmal D，参考项目仅作方法借鉴。
- 证据：`docs/REFERENCE_METHOD_SUMMARY.md` + 5 份 reference 文档。

### Phase 1 — Office DSG / PLACES 审计（audit §3：基本完成，z 问题被记录但未解决）
- ✅ 数据审计保留（2472 PLACES / 22 ROOMS / 8833 无向边 / 6 分量）。
- ✅ **z 问题已由 Phase 3R 的 ground projection 解决**（见下），不再"记录 warning 后继续使用 3D"。

### Phase 2 — Dense exporter（audit §4：最可靠段，保留）
- ✅ 保留。schema 层可靠：2472 nodes / 17666 directed edges / validator valid / topology_preserved=true。
- 未解决问题（大整数 ID / stamp=0 / tolerance 语义 / default policy 全边正确）→ 属真机接口问题，不影响 RViz2 mock，留待 Phase 10。

### Phase 3 — Sparsification（audit §5/6/7：必须返工）→ **Phase 3R 完成**
三个核心 bug 全部修复：

| audit 问题 | 修复前 | 修复后 |
|---|---|---|
| **拓扑 shortcut**（§5） | 3D 欧氏 FPS + 欧氏最近代表，stretch mean 4.73 / max 31.33 | **geodesic FPS per-component**，mean 1.62 / max 10.96 |
| **z 高空路线**（§6） | route 全在 z=3.9m | **ground projection** z=0，`z_source: projected_for_mock` |
| **静默合并分量**（§7） | 6→1，且跨分量连边（∞ 距离边） | **6 分量保持**，禁止跨分量 assignment |

- "0 穿墙边检测"（§5.2）已撤回 → 由 **witness path + geodesic stretch validator** 替代（route cost 沿真实拓扑，不穿墙）。
- 详细：`docs/phase3r_reconstruction.md`

### Phase 4 — Semantic→Waypoint（audit §8：依赖错误 graph，重跑）
- ✅ 在修复后的 graph-aware 图（R_geo=3.0, 261 waypoints）上重跑。
- **22/22 room → 22 unique anchors**（audit 指出旧版 16 个共享），不再有 room 共享/跨墙 anchor。
- anchor 距离 0.45~3.51m。详细：`docs/phase4_semantic_to_waypoint.md`

### Phase 5 — Mock Planner（audit §9：planner 代码保留，graph 重建后重跑）
- ✅ planner 代码保留，在修复后图上重跑：231/231 room 对可达。
- 表述已修正：只验证**图论可达性**（主分量内），不代表物理导航性能。

### Phase 6 — LLM+RAG E2E（audit §10：interface-level）
- ✅ 保持 interface-level / replay-based E2E（现有 RAG config 指向旧数据源）。
- 论文应写 "interface-level E2E"，非 "full online integrated E2E"（除非之后实际接一次现有 RAG）。

### Phase 7 — Visualization（audit §11：静态完成，缺 RViz2）
- ✅ 静态图保留（重合率 100% 仅说明坐标拷贝无偏移，非 edge 合法）。
- ✅ **RViz2 已由 Phase 8.5 补上**（缺项消除）。

### Phase 8 — Benchmark（audit §12：指标需重写）→ **Phase 8R**
- ✅ 指标 5 改名 **Pipeline Execution Consistency**（原 Mock E2E 是 tautological，audit §12.1）。
- ✅ Topology 指标修正：`exported_components = 6 = source_components`（不再静默合并）。
- ✅ Invalid Waypoint ID Rate = 0%（目标达成）。
- 可靠指标（Schema/Export Validity、Invalid ID、Semantic Coverage、Topology）与需谨慎指标（Route/Pipeline 仅图论+执行一致性）已明确区分。
- 详细：`docs/phase8_benchmark.md`

---

## 2. 审计 §31 直接修改任务 12 条对照表

| # | 任务 | 状态 | 证据 |
|---|---|---|---|
| 1 | 停止 global 3D 欧氏 FPS + 欧氏最近代表 | ✅ | `sparsifier.py::sparsify_graphaware` 替代，旧函数移除 |
| 2 | clustering/assignment 限制在 source connected component 内 | ✅ | 测试 `test_no_cross_component_edges` / `test_components_not_merged` |
| 3 | 优先 graph/geodesic 距离 | ✅ | FPS 用 dense 图 Dijkstra 距离 |
| 4 | 每条 sparse edge 有 witness path | ✅ | `witness_paths.json`（624 边全部有 dense path） |
| 5 | 新增 geodesic stretch validator | ✅ | `stretch_validator.py` + `sparsified_report.json` |
| 6 | 不允许 disconnected components 静默合并 | ✅ | 6 分量保持；benchmark exported_components=6 |
| 7 | ground projection + 标记 z_source=projected_for_mock | ✅ | `--ground-z` + report 标记 |
| 8 | 重新生成 waypoints_sparsified / semantic_to_waypoint / benchmark | ✅ | `generated/` 全部重建 |
| 9 | benchmark 改名 tautological 指标 → Pipeline Execution Consistency | ✅ | `run_benchmark.py` 指标 5 |
| 10 | 新增 RViz2 ROS2 package | ✅ | `khronos_anymald_rviz/`（jazzy） |
| 11 | 与 Khronos RViz2 同屏显示 | ⏳ 需有显示器环境 | 提供 `config/anymal_mock.rviz` + launch；headless 已验证 publisher |
| 12 | 完成后停止，不需要 ANYmal dynamics | ✅ | 未进入 dynamics/Isaac/real robot |

---

## 3. 修复后关键数据（R_geo=3.0）

| 项 | 值 |
|---|---|
| waypoint reps / edges | 261 / 624 |
| 连通分量 | **6（= source）** |
| stretch median / mean / max | 1.32 / 1.62 / 10.96 |
| stretch 分级（绿/黄/橙/红） | 370 / 108 / 132 / 14（>5） |
| ground projection z | 0.0（projected_for_mock） |
| room anchors | 22/22 unique |
| route witness 展开 | 12 waypoints → 106 dense poses |
| benchmark | Export valid / Pipeline 1.0 / Invalid ID 0% |
| 测试 | **28 passed** |
| RViz2 headless | graph publisher + mock robot 25Hz 插值 + MOVING→REACHED 验证通过 |

---

## 4. 已解决 vs 待验证

**已解决（本阶段内）**：Phase 3 算法返工、z 投影、分量保持、stretch validator、semantic anchor、benchmark 指标、RViz2 package。

**待你验证 / 待后续**：
1. **有显示器 RViz2**：与 Khronos mesh/DSG 同屏人工验证（audit §22-26）——命令已备好（`phase85_rviz2_mock.md` §5）。
2. **真机接口**（audit §28，不阻塞当前阶段）：native waypoint loader、ID 稀疏大整数、z 真机定义、orientation/tolerance/traversability/max_deviation 语义、strict_path_follower/perceptive_walk/nfm_preset、mission API。
3. **R_geo / stretch 阈值**：当前 R_geo=3.0、stretch 阈值 1.5/2/5 为开发默认，最终需结合 ANYmal local planner 能力调整。

---

## 5. 结论（对应 audit §32）

审计的判断成立：DSG parsing / schema conversion / semantic mapping API / planner API / mock execution framework / testing framework 均已搭好；真正需要返工的"从 3D Khronos PLACES 得到物理合理的稀疏 ANYmal waypoint graph"已由 **Phase 3R** 完成。当前可完整演示：

```
Natural Language → RAG target → ANYmal waypoint → route → RViz2 mock robot
```
