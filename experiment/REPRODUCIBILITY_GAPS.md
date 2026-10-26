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
| Source data | The official [TartanGround documentation](https://tartanair.org/tartanground.html) gives the `Hospital` / `diff` / `P1000` download route. On the current host the extracted front-camera sequence is now under the ignored `datasets/` snapshot; `P1000_metadata.json` records 3,178 poses at 0.1 s and its hash matches the archived reference. | The data is not included in Git. The newly downloaded dataset and downloader revision must be preserved with a per-file manifest to repeat this step on a clean machine. The repo's raw-data instructions still omit installation/version pinning for the downloader. |
| TartanGround to bag | The current host adapter build succeeds with the package marker and `setup.cfg` added in this branch. Its data audit passes at 3,178 image/depth/segmentation/pose entries; the generated 10 Hz bag audit and `ros2 bag info` both pass. | The generated bag is a new local artifact, not the historical Khronos input; the historical playback rate was not recorded. The adapter tests use machine-specific defaults and need `TARTANGROUND_ROOT`, `TARTANGROUND_CONFIG`, and `TARTANGROUND_BAG` set on other machines. |
| Khronos build and run | The archived `config.txt` and `experiment_log.txt` provide some effective settings: `ClosedSetImageReceiver`, `lcam_front`, 0.1–5.0 m sensor range, 24 main integration threads, and an 8 min 1 s run. ROS dependencies now pass `rosdep check`. | The current clean build fails at `khronos/CMakeLists.txt:29`: it references `src/active_window/data/frame_data_buffer.cpp`, which is absent from the checkout and its Git history. The historical Khronos commit `63faadde6ed92220e78fb2f6ca86dcc54bb5cf9` is unavailable and its recorded tree was dirty. Separately, the checked-in Hospital launch hard-codes `/home/ulmmt/Masterthesis-wang/tartanground_khronos/` paths and does not expose all config paths as arguments. |
| Khronos DSG/mesh output | The original files are described at `datasets/Hospital-workstation-backup-20261006/tartanground_khronos/reports/khronos_output/fine_full_p1000/`; their historical hashes match the RAG defaults and map manifest. | No DSG or mesh is present at that path on the current host. The downloaded raw data and generated bag do not yield Khronos outputs until the missing C++ source is restored and the launch paths are made portable. |
| DSG review and RAG build | [`CODE_REPRODUCTION.md`](CODE_REPRODUCTION.md) gives a RAG build command and pins the expected DSG and review-CSV SHA-256 values plus the embedding model revision. | The required 78-row semantic-review CSV is still absent. The archived `vector_db` is not a substitute for a newly built index with `build_manifest.json`; no RAG build was run. |
| Khronos-only map generation | Four floor maps and `map_manifest.json` are committed. The manifest pins generator, geometry, DSG, mesh, and per-floor map hashes. The plan-only smoke test uses these committed maps. | Regenerating maps from the new bag is blocked because this run did not produce Khronos DSG/mesh outputs. |
| RAG + Nav2 plan | A direct HC-001 Nav2 mission plan-only smoke test succeeded against the committed maps and isolated domain 77. The 60×4 manifest records model digest `07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c` for `gemma4:e2b-it-qat`. | This was not the integrated natural-language/RAG path. The 78-row CSV and manifest-bearing RAG index are missing; Ollama is not installed or running. The Nav2 smoke test verifies mission-to-planner routing only. |
| Historical evaluations | Task inputs, predictions, missions, logs, and results are archived. | The exact historical 60-task × 4-condition runner is absent; archived outputs are not a rerun. The single plan-only smoke test must not be presented as a replay of the historical batch or its score. See the replay limits in [`CODE_REPRODUCTION.md`](CODE_REPRODUCTION.md). |

The expected default Khronos path above is referenced by [`generate_khronos_only.py`](map-generation/generate_khronos_only.py). It is not present in this checkout. The committed floor maps are downstream artifacts; they do not provide the source bag, review CSV, or provenance-bearing RAG index.

## Missing inputs and services in the inspected checkout

Checked on **2026-10-26** in the `thesis-final` checkout and host used for this review:

| Required item | Expected location or identity | Current availability |
|---|---|---|
| Hospital P1000/TartanGround source snapshot | Ignored local path `datasets/Hospital-workstation-backup-20261006/tartanground_khronos/dataset/` | Downloaded on this host from `tartanairpy` revision `158a6844d782942110967325ca3082f50ab2bfc7` in a separate Python 3.12 environment. The metadata and label-map hashes match the recorded reference; the data is not committed to Git. |
| TartanGround ROS bag | `datasets/Hospital-workstation-backup-20261006/tartanground_khronos/bags/hospital-p1000-lcam-front-10hz/` | Generated locally from frames 0–3177 at 10 Hz. Bag database is 9.8 GiB, SHA-256 `a554410dc368e7f0341b4a18e7ea434e48b361346bf28c3122560174f78a9b5a`; audit JSON and `ros2-bag-info.txt` are beside it, all ignored by Git. |
| Khronos output DSG | `datasets/Hospital-workstation-backup-20261006/tartanground_khronos/reports/khronos_output/fine_full_p1000/dsg.json`; expected SHA-256 `a051980dbb1a3d19caa8deeb3027eb534cb7f87760a0b0d906e26ca4a661f14d` | Not committed. The matching file is present only in the originating workstation's external archive. |
| Khronos output mesh | `datasets/Hospital-workstation-backup-20261006/tartanground_khronos/reports/khronos_output/fine_full_p1000/mesh.ply`; expected SHA-256 `813bb3f534c9385edccbcfc71d3405ec81b5852c23061c68542e15debe40f268` | Not committed. The matching file is present only in the originating workstation's external archive. |
| Reviewed semantic CSV | Expected SHA-256 `29baf76d7bd2cda59b5e86e3a07fceb9411716cd59ba1e780b3b9808fd2ca6d5`; the experiment notes describe 78 reviewed rows | No matching CSV is included in the clone. Do not substitute a different file and call it the exact reproduction. |
| Provenance-bearing Hospital RAG index | `llm-rag/RAG/khronos_3dsg_rag/outputs/hospital_p1000_reproducible_20261024_v5/`, including `vector_db/` and `build_manifest.json` | Missing. The archived runtime index does not contain the required adjacent build manifest. |
| Ollama service and planner model | `gemma4:e2b-it-qat`; selected task-chain manifest digest `07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c` | The host has no `ollama` executable and the local API endpoint is unreachable. The integrated RAG/LLM plan was not run. |
| Live Nav2 stack for the planning request | Hospital F0 launch, matching map/params, isolated `ROS_DOMAIN_ID=77` | Started successfully after installing `ros-jazzy-nav2-navfn-planner`; HC-001 plan-only returned `status: planned`, `execution_performed: false`, one feasible sequence, 51.8247 m path, and 2 abstract floor transitions. The stack was shut down after the test. |

The expected DSG SHA-256 is `a051980dbb1a3d19caa8deeb3027eb534cb7f87760a0b0d906e26ca4a661f14d`, as pinned in [`hospital_nav2_plan_defaults.yaml`](../khronos-nav2-bridge/khronos_nav2_bridge/config/hospital_nav2_plan_defaults.yaml). The direct Nav2 smoke test is verified, but the integrated single-plan flow still requires the missing Khronos DSG/mesh and review CSV, a manifest-bearing RAG build, and Ollama serving the configured model.

## Reproduction Evidence on the Current Host

The `tartanairpy` downloader was cloned at revision `158a6844d782942110967325ca3082f50ab2bfc7` and installed as package version 1.4.0 in a separate Python 3.12 environment. Its unpinned dependencies resolved to PyTorch 2.14.1 and a CUDA 13 runtime even though the downloader itself uses CPU. The [official TartanGround installation instructions](https://tartanair.org/tartanground.html) document a Python 3.9-tested environment; the repository should pin and document a minimal downloader environment rather than relying on an unpinned editable install.

The adapter source package initially failed to build because `setup.py` referenced a missing `resource/tartanground_khronos_bridge` marker; after adding the marker, `ros2 run` still could not find entry points because `setup.cfg` did not direct scripts into `lib/<package>`. This branch now adds both packaging files. With `TARTANGROUND_ROOT`, `TARTANGROUND_CONFIG`, and `TARTANGROUND_BAG` set to host-local paths, its tests report 18 passed and 2 skipped. The Nav2 package also lacked a declared dependency for the configured `nav2_navfn_planner` plugin; this branch now declares `nav2_navfn_planner` in its `package.xml`.

All declared ROS dependencies pass `rosdep check` after installation. Building the Khronos workspace still fails at CMake generation because `src/active_window/data/frame_data_buffer.cpp` is absent. The log reports 22 packages finished, `khronos` failed, `hydra_ros` aborted, and two packages were not processed. The exact historical source revision recorded in the archive is unavailable, so the Khronos output could not be regenerated on this host.

The separate Nav2 smoke test uses the committed maps and archived mission `HC-001`; it is not an LLM/RAG-to-Nav2 run. Its successful plan-only JSON is `/tmp/HC-001-plan-only.json`.

The original source snapshot was recorded at `/home/nenlian/Masterthesis-wang/datasets/Hospital-workstation-backup-20261006/tartanground_khronos/` on its source WSL workstation. That path was not mounted on this host; the selected TartanGround inputs were downloaded separately from the official source. Their P1000 metadata/label map hashes match the archived values. The read-only adapter audit passed with `image=3178 depth=3178 seg=3178 pose=3178`.

The external historical Khronos output `fine_full_p1000/` was reported to contain `dsg.json` and `mesh.ply` with the expected SHA-256 values above. Those files were not available on this host; the checked-in map manifest pins those source hashes, confirming the committed maps' historical provenance but not a Khronos rerun here.

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
