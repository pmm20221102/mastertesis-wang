# Degraded Output Provenance Report

## Phase 1 结论

### 574-node DSG 文件定位

| 字段 | 值 |
|------|-----|
| absolute path | `reports/khronos_output/full_p1000_2026_08_18-11_30_30/dsg.json` |
| file size | 614,404 bytes (614 KB) |
| mtime | 2026-08-18 13:35 |
| sha256 | `50fb7015dfeef8a6e2ca5e2fa7b1f3ecf70266f27ec1cfeda8973afb195a6789` |
| run timestamp | 2026-08-18 11:30:31 → 11:35:59 (5.5 min, stopped early) |
| output directory | `reports/khronos_output/full_p1000_2026_08_18-11_30_30/` |
| experiment_log | 4 lines only, run stopped prematurely |
| executable | `/workspace/install/khronos_ros/lib/khronos_ros/khronos_node` |
| launch mode | `ros2 launch` (via `tartanground_hospital_khronos.launch.yaml`) |
| mapper config | `mapper_voxel010_fixed.yaml` |

### DSG Layer 分析

| Layer | Name | Nodes |
|-------|------|-------|
| 2, partition 0 | OBJECTS/AGENTS | 93 |
| 2, partition 97 | AGENTS | 481 |
| 3, partition 0 | PLACES | **0** |
| 4, partition 0 | ROOMS | **0** |
| 5, partition 0 | BUILDINGS | **0** |
| **Total** | | **574** |

### 根因

**`mapper_voxel010_fixed.yaml` 中 `freespace_places` 的 `type: gvd` 被注释掉了。**

```yaml
freespace_places:
    #type: gvd    ← 被注释！
```

没有 `type` 字段 → Khronos 不实例化 `GvdPlaceExtractor` → 不生成 Place 节点 → 没有 Place → 没有 Room → 没有 Building。

对比已知成功的 apartment config (`apartment_v6e/config.txt`):
```
freespace_places [Virtual Config: GvdPlaceExtractor::Config]:
   filter_places:          true (default)
   places_layer:           PLACES (default)
   room_finder [RoomFinderConfig]:
```

而 hospital config:
```
freespace_places [Uninitialized Virtual Config]
```

### 结论

1. **不是 `dynamic_object_points.clear()` 导致的** — backend.cpp 和 .bak 完全相同，无 strip 代码
2. **不是 OOM/SIGKILL** — `voxel010_full.log` 是另一次独立的配置路径错误导致的 SIGABRT
3. **根因是配置问题** — `type: gvd` 被注释，place 生成器未激活

### 修复

取消注释 `mapper_voxel010_fixed.yaml` 中的 `type: gvd`，重新运行验证。
