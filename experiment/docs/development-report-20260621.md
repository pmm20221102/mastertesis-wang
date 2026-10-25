# Development Report / 开发报告

**Author / 作者**: Yunsong Wang | **Date / 日期**: 2026-06-21

---

## 1. Project Background / 项目背景

Building an LLM-based robot task planning system using Khronos 3D Dynamic Scene Graphs.

基于 Khronos 3D 动态场景图构建 LLM 机器人任务规划系统。

---

## 2. RAG Evaluation Design / RAG 评估设计

### Problem / 问题

Official datasets lack structured groundtruth (no room hierarchy, no semantic labels). Instead, we use Khronos DSG output as groundtruth.

官方数据集缺乏结构化 groundtruth（无 room 层级，无语义标签）。我们改用 Khronos DSG 输出作为 groundtruth。

### Solution / 方案

```
Khronos DSG (1858 nodes, 6205 edges)
    ↓
RAG Index (ChromaDB)
    ↓
6 Query Types → 4 Metrics → Report
```

---

## 3. Query Types / 查询类型

| Type / 类型 | Example / 示例 | Purpose / 目的 |
|------------|----------------|---------------|
| Spatial | "Object at x=-15, y=22" | 位置检索 / Location retrieval |
| Hierarchy | "What is inside room?" | 父→子遍历 / Parent→child traversal |
| Reverse Hierarchy | "Where is chair?" | 子→父遍历 / Child→parent traversal |
| Neighbor | "What is connected to X?" | 邻居发现 / Neighbor discovery |
| Type Filter | "Find all rooms" | 类型过滤 / Type filtering |
| Semantic | "Find all chairs" | 语义匹配 / Semantic matching |

---

## 4. Metrics / 评估指标

| Metric / 指标 | Definition / 定义 | Target / 目标 |
|--------------|------------------|--------------|
| Hit@10 | top-10 中是否有正确答案 / Correct answer in top-10? | > 0.70 |
| Recall@10 | 找到了多少比例的相关项 / Proportion of relevant items found | > 0.60 |
| Precision@10 | 检索结果中正确比例 / Proportion of correct results | > 0.10 |
| Graph Accuracy | 图结构信息是否正确 / Graph structure correctness | > 0.10 |

---

## 5. Current Status / 当前状态

| Metric | Initial | Current | Target | Status |
|--------|---------|---------|--------|--------|
| Hit@10 | 0.10 | 0.55 | 0.70 | ⚠️ |
| Recall@10 | 0.10 | 0.53 | 0.60 | ⚠️ |
| Precision@10 | 0.01 | 0.07 | 0.10 | ⚠️ |
| Graph Accuracy | 0.003 | 0.11 | 0.10 | ✅ |

### By Query Type / 按查询类型

| Type | Hit | Status |
|------|-----|--------|
| Spatial | 97.5% | ✅ |
| Hierarchy | 100% | ✅ |
| Type Filter | 100% | ✅ |
| Semantic | 80% | ✅ |
| Neighbor | 33% | ⚠️ |
| Reverse Hierarchy | 10.5% | ❌ |

### Bottleneck / 瓶颈

Reverse Hierarchy fails because 70 nodes share the same label "small_office_objects", causing ambiguous parent retrieval.

反向层级查询失败，因为 70 个 nodes 有相同的 label，导致 parent 检索歧义。

---

## 6. Nav2 Integration / Nav2 集成

### Architecture / 架构

```
Khronos (TSDF/DSG) → Khronos-Nav2 Bridge → Nav2
```

### Three Bridge Modules / 三个桥接模块

| Module / 模块 | Input / 输入 | Output / 输出 | Status / 状态 |
|--------------|-------------|--------------|--------------|
| Occupancy Bridge | /khronos/occupancy | /khronos/map (OccupancyGrid) | 🔧 Config applied |
| Dynamic Object Bridge | object_changes | /khronos/dynamic_obstacles (PointCloud2) | 📋 Planned |
| Semantic Goal Bridge | DSG query | /semantic_goal_pose (PoseStamped) | 📋 Planned |

### PLY Projection Failed / PLY 投影失败

Attempted: `mesh.ply → 3D→2D projection → PGM/YAML → Nav2`

Result: Map mostly black (occupied), no free space for navigation.

原因: PLY 只有表面顶点，无法区分占用/空闲区域。

Conclusion: PLY projection is negative baseline only.

### Correct Approach / 正确方案

Enable Khronos `TsdfOccupancyPublisher` to publish OccupancyGrid directly from TSDF voxel layer.

启用 Khronos 的 TsdfOccupancyPublisher，直接从 TSDF 体素层发布 OccupancyGrid。

```yaml
# Configuration applied / 已应用配置
sinks:
  - type: TsdfOccupancyPublisher
    extraction:
      min_distance: 0.25
```

### Next Steps / 下一步

1. Run Khronos and verify `/khronos/occupancy` output
2. Implement occupancy_bridge_node
3. Configure Nav2 global_costmap to use `/khronos/map`
4. Implement semantic_goal_server with approach pose search

---

## 7. Conclusions / 结论

| Item | Status |
|------|--------|
| RAG Evaluation Framework | ✅ Implemented |
| Graph-Aware Retrieval | ✅ +45% hit rate |
| Graph Accuracy Target | ✅ Met |
| Hit/Recall/Precision Targets | ⚠️ Not yet met |
| Nav2 Integration | 📋 Planned |

---

## Appendix: Commands / 附录：命令

```bash
# Build RAG / 构建 RAG
cd RAG/khronos_3dsg_rag && python src/main.py build --all

# Run eval / 运行评估
cd rag_eval && python scripts/run_eval.py --config configs/eval_office.yaml

# View report / 查看报告
cat rag_eval/data/reports/eval_summary.md
```
