# Degraded Output Provenance Report

## Phase 1 Findings

### Identification of the 574-Node DSG File

| Field | Value |
|---|---|
| Absolute path | `reports/khronos_output/full_p1000_2026_08_18-11_30_30/dsg.json` |
| File size | 614,404 bytes (614 KB) |
| Modification time | 2026-08-18 13:35 |
| SHA-256 | `50fb7015dfeef8a6e2ca5e2fa7b1f3ecf70266f27ec1cfeda8973afb195a6789` |
| Run timestamp | 2026-08-18 11:30:31–11:35:59 (5.5 minutes; stopped early) |
| Output directory | `reports/khronos_output/full_p1000_2026_08_18-11_30_30/` |
| `experiment_log` | Four lines only; run stopped prematurely |
| Executable | `/workspace/install/khronos_ros/lib/khronos_ros/khronos_node` |
| Launch mode | `ros2 launch` (via `tartanground_hospital_khronos.launch.yaml`) |
| Mapper configuration | `mapper_voxel010_fixed.yaml` |

### DSG Layer Analysis

| Layer / partition | Name | Nodes |
|---|---|---:|
| 2, partition 0 | OBJECTS/AGENTS | 93 |
| 2, partition 97 | AGENTS | 481 |
| 3, partition 0 | PLACES | **0** |
| 4, partition 0 | ROOMS | **0** |
| 5, partition 0 | BUILDINGS | **0** |
| **Total** | | **574** |

### Root Cause

**The `type: gvd` entry for `freespace_places` was commented out in `mapper_voxel010_fixed.yaml`.**

```yaml
freespace_places:
    #type: gvd    # Commented out
```

Without the `type` field, Khronos does not instantiate `GvdPlaceExtractor`; no Place nodes are generated, and consequently there are no Places, Rooms, or Buildings.

Compared with the known-good apartment configuration (`apartment_v6e/config.txt`):

```text
freespace_places [Virtual Config: GvdPlaceExtractor::Config]:
   filter_places:          true (default)
   places_layer:           PLACES (default)
   room_finder [RoomFinderConfig]:
```

The Hospital configuration instead shows:

```text
freespace_places [Uninitialized Virtual Config]
```

### Conclusion

1. **Not caused by `dynamic_object_points.clear()`** — `backend.cpp` and `.bak` are identical, and no strip code is present.
2. **Not caused by OOM/SIGKILL** — `voxel010_full.log` is a separate SIGABRT caused by a configuration-path error.
3. **The root cause is configuration** — `type: gvd` was commented out, so the Place generator was inactive.

### Fix

Uncomment `type: gvd` in `mapper_voxel010_fixed.yaml`, then rerun the validation.
