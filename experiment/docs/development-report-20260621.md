# Development Report

**Author**: Yunsong Wang | **Date**: 2026-06-21

---

## 1. Project Background

Building an LLM-based robot task planning system using Khronos 3D Dynamic Scene Graphs.

## 2. RAG Evaluation Design

### Problem

Official datasets lack structured groundtruth (no room hierarchy, no semantic labels). Instead, we use Khronos DSG output as groundtruth.

### Solution

```
Khronos DSG (1858 nodes, 6205 edges)
    ↓
RAG Index (ChromaDB)
    ↓
6 Query Types → 4 Metrics → Report
```

---

## 3. Query Types

| Type | Example | Purpose |
|------------|----------------|---------------|
| Spatial | "Object at x=-15, y=22" | Location retrieval |
| Hierarchy | "What is inside room?" | Parent→child traversal |
| Reverse Hierarchy | "Where is chair?" | Child→parent traversal |
| Neighbor | "What is connected to X?" | Neighbor discovery |
| Type Filter | "Find all rooms" | Type filtering |
| Semantic | "Find all chairs" | Semantic matching |

---

## 4. Metrics

| Metric | Definition | Target |
|--------------|------------------|--------------|
| Hit@10 | Correct answer appears in top 10 | > 0.70 |
| Recall@10 | Proportion of relevant items found | > 0.60 |
| Precision@10 | Proportion of retrieved results that are correct | > 0.10 |
| Graph Accuracy | Correctness of graph-structure information | > 0.10 |

---

## 5. Current Status

| Metric | Initial | Current | Target | Status |
|--------|---------|---------|--------|--------|
| Hit@10 | 0.10 | 0.55 | 0.70 | ⚠️ |
| Recall@10 | 0.10 | 0.53 | 0.60 | ⚠️ |
| Precision@10 | 0.01 | 0.07 | 0.10 | ⚠️ |
| Graph Accuracy | 0.003 | 0.11 | 0.10 | ✅ |

### By Query Type

| Type | Hit | Status |
|------|-----|--------|
| Spatial | 97.5% | ✅ |
| Hierarchy | 100% | ✅ |
| Type Filter | 100% | ✅ |
| Semantic | 80% | ✅ |
| Neighbor | 33% | ⚠️ |
| Reverse Hierarchy | 10.5% | ❌ |

### Bottleneck

Reverse Hierarchy fails because 70 nodes share the same label "small_office_objects", causing ambiguous parent retrieval.

## 6. Nav2 Integration

### Architecture

```
Khronos (TSDF/DSG) → Khronos-Nav2 Bridge → Nav2
```

### Three Bridge Modules

| Module | Input | Output | Status |
|--------------|-------------|--------------|--------------|
| Occupancy Bridge | /khronos/occupancy | /khronos/map (OccupancyGrid) | 🔧 Config applied |
| Dynamic Object Bridge | object_changes | /khronos/dynamic_obstacles (PointCloud2) | 📋 Planned |
| Semantic Goal Bridge | DSG query | /semantic_goal_pose (PoseStamped) | 📋 Planned |

### PLY Projection Failed

Attempted: `mesh.ply → 3D→2D projection → PGM/YAML → Nav2`

Result: Map mostly black (occupied), no free space for navigation.

Cause: the PLY contains only surface vertices and cannot distinguish occupied from free space.

Conclusion: PLY projection is a negative baseline only.

### Correct Approach

Enable Khronos `TsdfOccupancyPublisher` to publish OccupancyGrid directly from TSDF voxel layer.

Enable Khronos `TsdfOccupancyPublisher` to publish an `OccupancyGrid` directly from the TSDF voxel layer.

```yaml
# Configuration applied
sinks:
  - type: TsdfOccupancyPublisher
    extraction:
      min_distance: 0.25
```

### Next Steps

1. Run Khronos and verify `/khronos/occupancy` output
2. Implement occupancy_bridge_node
3. Configure Nav2 global_costmap to use `/khronos/map`
4. Implement semantic_goal_server with approach pose search

---

## 7. Conclusions

| Item | Status |
|------|--------|
| RAG Evaluation Framework | ✅ Implemented |
| Graph-Aware Retrieval | ✅ +45% hit rate |
| Graph Accuracy Target | ✅ Met |
| Hit/Recall/Precision Targets | ⚠️ Not yet met |
| Nav2 Integration | 📋 Planned |

---

## Appendix: Commands

```bash
# Build the RAG index
cd RAG/khronos_3dsg_rag && python src/main.py build --all

# Run the evaluation
cd rag_eval && python scripts/run_eval.py --config configs/eval_office.yaml

# View the report
cat rag_eval/data/reports/eval_summary.md
```
