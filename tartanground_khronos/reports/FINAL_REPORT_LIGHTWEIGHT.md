# Khronos Hospital P1000 — 最终报告

日期：2026-08-18

## 结论

Hospital P1000 完整数据集（3178帧）Khronos 处理 + lightweight save 全链路跑通。

- dsg.json: **5.3 MB**（旧版 5GB，减少 99.9%）
- 所有四层 DSG 完整：objects / places / rooms / buildings
- 无 OOM，无 SIGKILL，无 SIGABRT

## 根因与修复

### 问题1：Place 层缺失

根因：`mapper_voxel010_fixed.yaml` 第 114 行 `type: gvd` 被注释。

```yaml
freespace_places:
    #type: gvd    ← 被注释，GvdPlaceExtractor 未实例化
```

修复：取消注释 `type: gvd`。

### 问题2：Room/Building 层缺失

根因：缺少 `update_functors[rooms]` 和 `update_functors[buildings]`。

对比 apartment config 有三个 update functors：
- `update_functors[khronos_objects]` — object merging
- `update_functors[rooms]` — room detection (UpdateRoomsFunctor)
- `update_functors[buildings]` — building detection (UpdateBuildingsFunctor)

Hospital config 只有第一个。

修复：通过 `--config-utilities-yaml` 注册 rooms 和 buildings functors。

### 问题3：dsg.json 过大（OOM 根因）

根因：`KhronosObjectAttributes::dynamic_object_points` 每个 object 约 179MB，28 个节点共 5GB。
JSON 序列化时内存放大 3-4 倍，触发 OOM。

修复：给 `KhronosObjectAttributes` 添加 `static bool skip_heavy_fields_`，
在 `serialization_info()` 中条件跳过 `dynamic_object_points`。
`Backend::save()` 和 `KhronosPipeline::save()` 在保存前后设置标志。

## 修改文件清单

| 文件 | 修改 |
|------|------|
| `spark_dsg/include/spark_dsg/node_attributes.h` | 添加 `skip_heavy_fields_` 静态标志和访问方法 |
| `spark_dsg/src/node_attributes.cpp` | 定义静态成员 + `serialization_info()` 条件跳过 |
| `khronos/khronos/src/backend/backend.cpp` | `save()` 中启用/关闭 lightweight 模式 |
| `khronos/khronos_ros/src/khronos_pipeline.cpp` | `save()` 中同样处理 |
| `mapper_voxel010_fixed.yaml` | 取消注释 `type: gvd`（需 sudo） |
| `mapper_voxel010_fixed.yaml` | 添加 `update_functors[rooms]` 和 `[buildings]`（需 sudo） |

## 验证结果

### 300 帧 smoke test

| 指标 | 值 |
|------|-----|
| dsg.json | 674 KB |
| Nodes | 396 |
| Edges | 1558 |
| Objects | 9 |
| Places | 334 |
| Rooms | 5 |
| Buildings | 1 |
| dynamic_object_points | 已移除 |

### 1200 帧

| 指标 | 值 |
|------|-----|
| dsg.json | 2 MB |
| Nodes | 1178 |
| Edges | 4846 |
| Objects | 32 |
| Places | 968 |
| Rooms | 7 |
| Buildings | 1 |

### Full P1000（3178 帧）

| 指标 | 值 |
|------|-----|
| dsg.json | **5.3 MB** |
| final.4dmap | 546 MB |
| mesh.ply | 53 MB |
| Nodes | 3116 |
| Edges | 12395 |
| Objects | 93 |
| Places | 2533 |
| Rooms | 19 |
| Buildings | 1 |
| dynamic_object_points | 已移除 |
| OOM/SIGKILL | 无 |

## 输出目录

```
reports/khronos_output/full_p1000_lw_20260818_175447/
├── dsg.json          (5.3 MB, lightweight)
├── shared_dsg.json   (4.9 MB, lightweight)
├── mesh.ply          (53 MB)
├── final.4dmap       (546 MB)
├── deformation_graph.dgrf
├── config.txt
├── experiment_log.txt
├── timing/
├── maps/
└── backend/
```

## 持久化待办（需 sudo）

1. 取消注释 `mapper_voxel010_fixed.yaml` 第 114 行 `type: gvd`
2. 在 `mapper_voxel010_fixed.yaml` 的 backend 段添加：
   ```yaml
   update_functors:
     rooms:
       type: UpdateRoomsFunctor
       places_layer: PLACES
       room_finder:
         min_dilation_m: 0.5
         max_dilation_m: 1.2
         min_component_size: 10
         min_room_size: 10
         dilation_threshold_mode: PLATEAU
         plateau_ratio: 0.15
         clustering_mode: NEIGHBORS
         max_modularity_iters: 5
         modularity_gamma: 1.0
         dilation_diff_threshold_m: -1.0
     buildings:
       type: UpdateBuildingsFunctor
       semantic_label: 22
   ```
