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
| Source data | The official [TartanGround documentation](https://tartanair.org/tartanground.html) gives the `Hospital` / `diff` / `P1000` download route. A local WSL archive has the extracted front-camera sequence (4.4 GB); `P1000_metadata.json` records 3,178 poses at 0.1 s. The adapter's fixed 640×640 intrinsics, camera transform, and NED-to-ENU/FLU conversion are described in `CODE_REPRODUCTION.md`. | The dataset is not included in Git. A reproducer must obtain the same TartanGround release/snapshot, preserve the exact frames and label-map files, and produce a checksummed data manifest. The available local archive is machine-local, not a Git artifact. |
| TartanGround to bag | `CODE_REPRODUCTION.md` now provides the adapter build, 10 Hz / 3,178-frame conversion profile, pre-conversion sync audit, bag topic audit, and `ros2 bag info` check. The read-only local audit passed with 3,178 image/depth/segmentation/pose entries. | The original bag, its hash, and the historical playback rate were not recorded. The documented 10 Hz profile is a new reference conversion, not proof that the historical Khronos run used the same timing. Preserve the new bag and audit outputs for each rerun. |
| Khronos build and run | The archived `config.txt` and `experiment_log.txt` provide some effective settings: `ClosedSetImageReceiver`, `lcam_front`, 0.1–5.0 m sensor range, 24 main integration threads, and an 8 min 1 s run. | There is no portable copy-paste launch command. The checked-in Hospital launch file hard-codes `/home/ulmmt/Masterthesis-wang/tartanground_khronos/` paths and does not expose all input/config paths as arguments. The historical bag playback rate is unknown, and the recorded source tree was dirty. |
| Khronos DSG/mesh output | The original files exist outside the clone on the originating WSL workstation at `datasets/Hospital-workstation-backup-20261006/tartanground_khronos/reports/khronos_output/fine_full_p1000/`. Their DSG/mesh hashes match the values pinned by the RAG defaults and map manifest. | The files are not in Git and must be transferred or regenerated on another machine. The run records Khronos revision `63faadde6ed92220e78fb2f6ca86dcc54bb5cf9e`, but that commit is unavailable in the current Khronos Git history. The run's `git_status.txt` records uncommitted edits to `backend.cpp`, `khronos_pipeline.cpp`, and `khronos_ros/CMakeLists.txt`, plus untracked packages/configuration; the exact historical source snapshot cannot be reconstructed from the commit ID alone. |
| DSG review and RAG build | [`CODE_REPRODUCTION.md`](CODE_REPRODUCTION.md) gives a RAG build command and pins the expected DSG and review-CSV SHA-256 values plus the embedding model revision. | The required 78-row semantic-review CSV is absent. The repository does not explain how to produce or obtain the reviewed labels, how to audit its rows, or how the Khronos output above becomes the exact DSG passed to the RAG builder. The archived `vector_db` is not a substitute for a newly built index with `build_manifest.json`. |
| Khronos-only map generation | Four floor maps and `map_manifest.json` are committed. The manifest pins generator, geometry, DSG, mesh, and per-floor map hashes. `CODE_REPRODUCTION.md` uses these committed maps for plan-only validation. | Regenerating maps requires the external DSG/mesh snapshot. The existing guide clearly distinguishes using committed maps from regenerating a new map bundle. |
| RAG + Nav2 plan | The guide gives the RAG build, isolated Nav2 launch, and plan-only request. The selected 60×4 manifest records model digest `07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c` for `gemma4:e2b-it-qat`. | The 78-row semantic-review CSV is absent, so the exact pinned index cannot be rebuilt. The Ollama executable/service and model are external; the run record does not pin the Ollama server version. A successful plan must be recorded as `status: planned` and `execution_performed: false`. |
| Historical evaluations | Task inputs, predictions, missions, logs, and results are archived. | The exact historical 60-task × 4-condition runner is absent; archived outputs are not a rerun. The single plan-only smoke test must not be presented as a replay of the historical batch or its score. See the replay limits in [`CODE_REPRODUCTION.md`](CODE_REPRODUCTION.md). |

The expected default Khronos path above is referenced by [`generate_khronos_only.py`](map-generation/generate_khronos_only.py). It is not present in this checkout. The committed floor maps are downstream artifacts; they do not provide the source bag, review CSV, or provenance-bearing RAG index.

## Missing inputs and services in the inspected checkout

Checked on **2026-10-26** in the `thesis-final` checkout used for this review:

| Required item | Expected location or identity | Current availability |
|---|---|---|
| Hospital P1000/TartanGround source snapshot, calibration, and semantic inputs | The official TartanGround `Hospital` / `diff` / `P1000` selection; see the command in `CODE_REPRODUCTION.md`. | The originating WSL workstation has a 4.4 GB extracted snapshot with 3,178 synchronized lcam_front frames. It is outside the clone and must be transferred/downloaded on another machine. |
| Khronos output DSG | `datasets/Hospital-workstation-backup-20261006/tartanground_khronos/reports/khronos_output/fine_full_p1000/dsg.json`; expected SHA-256 `a051980dbb1a3d19caa8deeb3027eb534cb7f87760a0b0d906e26ca4a661f14d` | Not committed. The matching file is present only in the originating workstation's external archive. |
| Khronos output mesh | `datasets/Hospital-workstation-backup-20261006/tartanground_khronos/reports/khronos_output/fine_full_p1000/mesh.ply`; expected SHA-256 `813bb3f534c9385edccbcfc71d3405ec81b5852c23061c68542e15debe40f268` | Not committed. The matching file is present only in the originating workstation's external archive. |
| Reviewed semantic CSV | Expected SHA-256 `29baf76d7bd2cda59b5e86e3a07fceb9411716cd59ba1e780b3b9808fd2ca6d5`; the experiment notes describe 78 reviewed rows | No matching CSV is included in the clone. Do not substitute a different file and call it the exact reproduction. |
| Provenance-bearing Hospital RAG index | `llm-rag/RAG/khronos_3dsg_rag/outputs/hospital_p1000_reproducible_20261024_v5/`, including `vector_db/` and `build_manifest.json` | Missing. The archived runtime index does not contain the required adjacent build manifest. |
| Ollama service and planner model | `gemma4:e2b-it-qat`; the selected task-chain manifest records model digest `07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c` | At the inspection on the reproduction computer, the host had no `ollama` executable and `http://127.0.0.1:11434/api/tags` was unreachable. Install/start Ollama and verify the model digest before running. The historical Ollama server version is not recorded. |
| Live Nav2 stack for the planning request | Hospital F0 launch, matching map and params, isolated `ROS_DOMAIN_ID=77` | Must be launched for each run; an installed ROS environment alone is not a running Nav2 stack. |

The expected DSG SHA-256 is `a051980dbb1a3d19caa8deeb3027eb534cb7f87760a0b0d906e26ca4a661f14d`, as pinned in [`hospital_nav2_plan_defaults.yaml`](../khronos-nav2-bridge/khronos_nav2_bridge/config/hospital_nav2_plan_defaults.yaml). A single-plan reproduction is ready only after the expected DSG and review CSV are available and verified, the RAG build completes with a valid manifest, Ollama can serve the configured model, and the matching Nav2 stack is active in the isolated domain.

## Additional Verification on the Originating WSL Workstation

The original source snapshot is outside this repository at `/home/nenlian/Masterthesis-wang/datasets/Hospital-workstation-backup-20261006/tartanground_khronos/`. The extracted dataset directory is 4.4 GB. Its `P1000_metadata.json` records 3,178 poses and a 0.1-second timestep. Running the adapter's `--audit-only` mode against the flat dataset root returned `image=3178 depth=3178 seg=3178 pose=3178` and did not write a bag.

The external Khronos output `fine_full_p1000/` contains `dsg.json` and `mesh.ply` with the expected SHA-256 values above. The checked-in `map_manifest.json` pins those same source hashes, confirming that the four committed floor maps were generated from those artifacts. This verifies local source/map provenance, but it does not make the raw data or historical run source available in a clean Git clone.

The Khronos run's `experiment_log.txt` records a start at 2026-08-19 19:35:31 and a stop at 19:43:32. Its `git_hash.txt` records `63faadde6ed92220e78fb2f6ca86dcc54bb5cf9e`, which is not available as a commit object in the current Khronos checkout. The recorded `git_status.txt` also lists uncommitted edits and untracked files. Thus the artifact hashes are verified, but the exact source tree that generated them is not recoverable from the recorded Git SHA alone.

The adapter source uses fixed 640×640 pinhole intrinsics (`fx=fy=320`, `cx=cy=320`), a fixed base-to-camera transform, and NED-to-ROS ENU/FLU pose conversion; these values are code-defined rather than loaded from a separate calibration file. The recorded Khronos run uses `ClosedSetImageReceiver` with dataset semantic labels, so it does not require a separate online segmentation model. The adapter, label-map/remap, and mapper configuration files must still be versioned with any new run.

## Environment setup gap

The root README and code-reproduction guide now include a conditional first-time `sudo rosdep init`. The Python requirements pin direct dependencies but do not lock all transitive dependencies; ROS packages are resolved from the current apt/rosdep indexes. No single versioned environment/container definition yet covers Python, ROS, Ollama, and the Khronos runtime together. The raw-data guide also identifies that the checked-in Khronos launch file hard-codes paths from the original `/home/ulmmt/...` environment.

## Minimum evidence for a complete run guide

To make the requested end-to-end flow reviewable and repeatable, the run guide needs to provide these stage records:

1. **Dataset manifest:** the official TartanGround download route, selected `Hospital` / `diff` / `P1000` / `lcam_front` data, expected directory layout, and checksums for the downloaded data, calibration, label map, and model weights. The sequence count and timestep are now documented; a per-file source manifest is still needed.
2. **Bag manifest and audit:** the guide now gives an explicit 3,178-frame, 10 Hz adapter profile, input sync check, bag topic audit, and `ros2 bag info` command. The historical bag and its playback rate were not retained, so this profile is a new run definition rather than an exact historical replay.
3. **Khronos run manifest:** the guide records the archived 8 min 1 s duration, effective sensor/thread settings, and source-output hashes. Recover the exact source snapshot/dirty diff, make the hard-coded launch paths portable, and record the bag hash, final command, and new run outputs to reproduce Khronos itself.
4. **RAG provenance:** the expected DSG hash and embedding model revision/device are pinned. The exact 78-row reviewed CSV and its provenance are still missing; without them the current RAG builder cannot create the required manifest-bearing index.
5. **Map provenance:** committed maps and `map_manifest.json` are available, and their source DSG/mesh hashes match the locally verified artifacts. For a new map-generation run, retain the input hashes and generated manifest.
6. **Nav2 plan record:** the guide covers the isolated ROS domain, launch/map/parameter files, exact request, output JSON, and `planned` / `execution_performed: false` check. The expected model digest is now recorded; the Ollama server version and a full plan run on a clean host remain to be verified.

Until the missing source data and review CSV are available and the Khronos invocation is documented, the full raw-data-to-Nav2 chain cannot be reproduced from this checkout. The currently documented tests, bridge build, packaged maps, and archived experiment outputs establish narrower checkpoints only.
