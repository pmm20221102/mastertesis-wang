# Full Reproduction Gaps: Khronos to Nav2 Planning

This note describes what is still needed to reproduce the Hospital P1000 software pipeline **starting with the raw TartanGround data, running Khronos, and ending with a Nav2 plan**. It complements [`CODE_REPRODUCTION.md`](CODE_REPRODUCTION.md), whose runnable path starts from an already available Hospital DSG and review CSV. This is a software planning workflow: a successful plan does not demonstrate physical robot motion or physical safety.

## Target artifact flow

```text
Hospital P1000 TartanGround source data + calibration/semantic inputs
  -> ROS 2 bag + bag audit
  -> Khronos run
  -> Hospital DSG (dsg.json) + mesh (mesh.ply) + provenance/logs
       |                               |
       |                               +-> Khronos-only map generation
       +-> reviewed semantic CSV          -> floor maps + map manifest
             -> reproducible RAG index
                  -> Ollama-backed task planning
                       + RAG index + map bundle + isolated Nav2 stack
                       -> plan-only result JSON
```

The map and RAG branches consume artifacts from the same Khronos run. The current checkout documents these branches separately and does not connect them into one executable procedure.

## Current state and missing reproduction instructions

| Stage | What is present | What is missing to reproduce this stage from its input |
|---|---|---|
| Source data | Documentation identifies the original Hospital P1000/TartanGround data as external and gives an approximate total size. | Dataset acquisition/access instructions, exact dataset release or snapshot, directory-layout contract, checksums, calibration files, semantic inputs/weights, and the precise P1000 sequence/frame range used by the thesis run. |
| TartanGround to bag | The adapter package and its dependencies are documented; the README points to `--help`. | A copy-pasteable conversion command with the selected dataset root, environment/version/trajectory/camera, frame range, rate, output path, and the expected audit report (topics, message counts, timestamps, and pass criteria). The package README does not select the thesis run's actual CLI values. |
| Khronos build and run | The Khronos workspace build and `rosdep` commands are documented. | The Hospital P1000 launch/configuration, exact input topics or bag playback command, semantic model/weights and versions, runtime parameters, output directory, completion condition, and expected run logs are not specified as one reproducible invocation. The source README explicitly leaves output paths dependent on the selected launch/configuration. |
| Khronos DSG/mesh output | The map generator expects `dsg.json` and `mesh.ply` under `datasets/Hospital-workstation-backup-20261006/tartanground_khronos/reports/khronos_output/fine_full_p1000/`. | That source snapshot is not included. The clone has no default Khronos `dsg.json` or `mesh.ply`; it also has no per-run manifest tying these outputs to the dataset, bag, configuration, model weights, and source revision. Required output checks and hashes need to be recorded. |
| DSG review and RAG build | [`CODE_REPRODUCTION.md`](CODE_REPRODUCTION.md) gives a RAG build command and pins the expected DSG and review-CSV SHA-256 values plus the embedding model revision. | The required 78-row semantic-review CSV is absent. The repository does not explain how to produce or obtain the reviewed labels, how to audit its rows, or how the Khronos output above becomes the exact DSG passed to the RAG builder. The archived `vector_db` is not a substitute for a newly built index with `build_manifest.json`. |
| Khronos-only map generation | Four floor maps and their manifest are committed under `khronos-nav2-bridge/khronos_nav2_bridge/generated/hospital-p1000-khronos-only/`. The map README documents a regeneration command. | Regeneration requires the missing Khronos DSG and mesh in the ignored `datasets/` snapshot path. The run guide needs to state whether the final Nav2 test uses committed maps or maps regenerated from the current Khronos run, and retain the corresponding map manifest and hashes. |
| RAG + Nav2 plan | The root README and reproduction guide show how to start the isolated Nav2 stack and submit one plan-only instruction. | This step requires a provenance-bearing RAG index, the Ollama service/model, and a matching map bundle. The model name is recorded, but the Ollama version/model digest and service setup are not pinned. A successful plan must be recorded as `status: planned` and `execution_performed: false`. |
| Historical evaluations | Task inputs, predictions, missions, logs, and results are archived. | The exact historical 60-task × 4-condition runner is absent; archived outputs are not a rerun. The single plan-only smoke test must not be presented as a replay of the historical batch or its score. See the replay limits in [`CODE_REPRODUCTION.md`](CODE_REPRODUCTION.md). |

The expected default Khronos path above is referenced by [`generate_khronos_only.py`](map-generation/generate_khronos_only.py). It is not present in this checkout. The committed floor maps are downstream artifacts; they do not provide the missing DSG, mesh, source bag, or RAG index.

## Missing inputs and services in the inspected checkout

Checked on **2026-10-26** in the `thesis-final` checkout used for this review:

| Required item | Expected location or identity | Current availability |
|---|---|---|
| Hospital P1000/TartanGround source snapshot, calibration, and semantic inputs | The local source snapshot referenced by the Khronos and map-generation instructions | Not present in the clone; the raw source data is external to Git. |
| Khronos output DSG | `datasets/Hospital-workstation-backup-20261006/tartanground_khronos/reports/khronos_output/fine_full_p1000/dsg.json` | Missing. |
| Khronos output mesh | `datasets/Hospital-workstation-backup-20261006/tartanground_khronos/reports/khronos_output/fine_full_p1000/mesh.ply` | Missing. |
| Reviewed semantic CSV | Expected SHA-256 `29baf76d7bd2cda59b5e86e3a07fceb9411716cd59ba1e780b3b9808fd2ca6d5`; the experiment notes describe 78 reviewed rows | No matching CSV is included in the clone. Do not substitute a different file and call it the exact reproduction. |
| Provenance-bearing Hospital RAG index | `llm-rag/RAG/khronos_3dsg_rag/outputs/hospital_p1000_reproducible_20261024_v5/`, including `vector_db/` and `build_manifest.json` | Missing. The archived runtime index does not contain the required adjacent build manifest. |
| Ollama service and planner model | Model name in `hospital_nav2_plan_defaults.yaml`: `gemma4:e2b-it-qat` | At inspection time, the host had no `ollama` executable and `http://127.0.0.1:11434/api/tags` was unreachable. The model therefore could not be queried for the single-plan run. |
| Live Nav2 stack for the planning request | Hospital F0 launch, matching map and params, isolated `ROS_DOMAIN_ID=77` | Must be launched for each run; an installed ROS environment alone is not a running Nav2 stack. |

The expected DSG SHA-256 is `a051980dbb1a3d19caa8deeb3027eb534cb7f87760a0b0d906e26ca4a661f14d`, as pinned in [`hospital_nav2_plan_defaults.yaml`](../khronos-nav2-bridge/khronos_nav2_bridge/config/hospital_nav2_plan_defaults.yaml). A single-plan reproduction is ready only after the expected DSG and review CSV are available and verified, the RAG build completes with a valid manifest, Ollama can serve the configured model, and the matching Nav2 stack is active in the isolated domain.

## Environment setup gap

The documented clean-host sequence calls `rosdep update`, but does not include first-time `rosdep init`. On a fresh ROS host with no `/etc/ros/rosdep/sources.list.d/20-default.list`, `rosdep update` fails until an administrator runs `rosdep init` once. The Python requirements pin direct dependencies but do not lock all transitive dependencies; ROS packages are resolved from the current apt/rosdep indexes. The README does not provide a single versioned environment/container definition covering the Python, ROS, Ollama, and Khronos runtime dependencies.

## Minimum evidence for a complete run guide

To make the requested end-to-end flow reviewable and repeatable, the run guide needs to provide these stage records:

1. **Dataset manifest:** source URL/access route, release or snapshot identifier, expected directory tree, and SHA-256 values for the selected sequence, calibration, labels, and model weights.
2. **Bag manifest and audit:** the exact adapter command, selected frames/rate, ROS topic/type/count summary, timestamp range, and a saved audit report.
3. **Khronos run manifest:** source revision, launch/config files and hashes, model versions, bag hash, output directory, run command, exit status, and hashes for `dsg.json` and `mesh.ply`.
4. **RAG provenance:** the reviewed CSV source/procedure and 78-row check, the exact Khronos DSG hash, embedding model revision/device, generated canonical graph/documents/vector DB, and validated `build_manifest.json`.
5. **Map provenance:** whether committed or regenerated maps are used, the source DSG/mesh hashes, map-generation command, and map manifest/hash check.
6. **Nav2 plan record:** ROS domain, launch/map/parameter files, Ollama version and model digest, exact natural-language request, output JSON, and the `planned` / `execution_performed: false` acceptance check.

Until the missing source data and review CSV are available and the Khronos invocation is documented, the full raw-data-to-Nav2 chain cannot be reproduced from this checkout. The currently documented tests, bridge build, packaged maps, and archived experiment outputs establish narrower checkpoints only.
