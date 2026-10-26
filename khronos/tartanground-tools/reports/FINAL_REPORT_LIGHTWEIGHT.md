# Khronos Hospital P1000 — Final Report

Date: 2026-08-18

## Conclusion

The complete Hospital P1000 dataset (3,178 frames) was processed by Khronos, and the lightweight-save pipeline completed successfully.

- `dsg.json`: **5.3 MB** (down from 5 GB; a 99.9% reduction)
- All four DSG layers are present: objects / places / rooms / buildings
- No OOM, SIGKILL, or SIGABRT

## Root Causes and Fixes

### Issue 1: Missing Place Layer

Root cause: `type: gvd` on line 114 of `mapper_voxel010_fixed.yaml` was commented out.

```yaml
freespace_places:
    #type: gvd    # Commented out; GvdPlaceExtractor was not instantiated
```

Fix: uncomment `type: gvd`.

### Issue 2: Missing Room/Building Layers

Root cause: `update_functors[rooms]` and `update_functors[buildings]` were missing.

The apartment configuration has three update functors:
- `update_functors[khronos_objects]` — object merging
- `update_functors[rooms]` — room detection (`UpdateRoomsFunctor`)
- `update_functors[buildings]` — building detection (`UpdateBuildingsFunctor`)

The Hospital configuration had only the first.

Fix: register the rooms and buildings functors using `--config-utilities-yaml`.

### Issue 3: Oversized `dsg.json` (OOM Root Cause)

Root cause: `KhronosObjectAttributes::dynamic_object_points` used about 179 MB per object; 28 nodes totaled about 5 GB. JSON serialization multiplied memory use by 3–4× and triggered OOM.

Fix: add `static bool skip_heavy_fields_` to `KhronosObjectAttributes` and conditionally skip `dynamic_object_points` in `serialization_info()`. `Backend::save()` and `KhronosPipeline::save()` set and clear the flag around serialization.

## Modified Files

| File | Change |
|---|---|
| `spark_dsg/include/spark_dsg/node_attributes.h` | Add the `skip_heavy_fields_` static flag and accessors |
| `spark_dsg/src/node_attributes.cpp` | Define the static member and conditionally skip it in `serialization_info()` |
| `khronos/khronos/src/backend/backend.cpp` | Enable/disable lightweight mode in `save()` |
| `khronos/khronos_ros/src/khronos_pipeline.cpp` | Apply the same handling in `save()` |
| `mapper_voxel010_fixed.yaml` | Uncomment `type: gvd` (requires sudo) |
| `mapper_voxel010_fixed.yaml` | Add `update_functors[rooms]` and `[buildings]` (requires sudo) |

## Verification Results

### 300-Frame Smoke Test

| Metric | Value |
|---|---|
| `dsg.json` | 674 KB |
| Nodes | 396 |
| Edges | 1,558 |
| Objects | 9 |
| Places | 334 |
| Rooms | 5 |
| Buildings | 1 |
| `dynamic_object_points` | Removed |

### 1,200 Frames

| Metric | Value |
|---|---|
| `dsg.json` | 2 MB |
| Nodes | 1,178 |
| Edges | 4,846 |
| Objects | 32 |
| Places | 968 |
| Rooms | 7 |
| Buildings | 1 |

### Full P1000 (3,178 Frames)

| Metric | Value |
|---|---|
| `dsg.json` | **5.3 MB** |
| `final.4dmap` | 546 MB |
| `mesh.ply` | 53 MB |
| Nodes | 3,116 |
| Edges | 12,395 |
| Objects | 93 |
| Places | 2,533 |
| Rooms | 19 |
| Buildings | 1 |
| `dynamic_object_points` | Removed |
| OOM/SIGKILL | None |

## Output Directory

```text
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

## Persistent Changes Required (sudo)

1. Uncomment `type: gvd` on line 114 of `mapper_voxel010_fixed.yaml`.
2. Add the following to the backend section of `mapper_voxel010_fixed.yaml`:

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
