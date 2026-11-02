# Hospital P1000: Khronos, LLM-RAG Task Chains, and Nav2

This project connects Khronos scene reconstruction to semantic retrieval, local-language-model task chaining, and a Nav2/RViz2 software demonstration.

## Choose a reproduction path

| Path | Stages | External scene data |
|---|---|---|
| **Bundled demo (short path)** | Bundled RAG + floor maps → LLM1 → RAG grounding → LLM2 → candidate selection → Nav2 → RViz2 | None. Both RAG databases, maps, and matching profiles are committed. |
| **Full reconstruction path** | TartanGround images/depth/semantic images/poses → ROS bag → Khronos → new DSG + mesh → new RAG + maps → run profile → LLM task chain → Nav2/RViz2 | A local Hospital P1000 sequence, obtained from TartanGround. |

The short path does not compile or run Khronos, regenerate a RAG index, or require the original DSG, mesh, or review CSV. It still needs the Python dependencies, ROS/Nav2 bridge, and Ollama model. The full path accepts newly generated Khronos outputs; it does not require identical node counts or map pixels to the historical thesis run.

## Modules

| Module | Input → output | Build and interface documentation |
|---|---|---|
| `khronos/` | Sensor/data bag → DSG and mesh | [README](khronos/README.md), [build environment](khronos/BUILD_ENVIRONMENT.md) |
| `llm-rag/` | DSG → canonical graph/documents/vector DB; instruction + RAG → task chain | [README](llm-rag/README.md), [Python environment](llm-rag/BUILD_ENVIRONMENT.md) |
| `khronos-nav2-bridge/` | Mission + map bundle → route/action results and point-robot execution | [README](khronos-nav2-bridge/README.md) |
| `experiment/` | Tasks, maps, run profiles, archived results, and interactive demo | [README](experiment/README.md), [complete reproduction guide](experiment/CODE_REPRODUCTION.md) |

The root README is the only root Markdown document. Module documentation describes its own inputs, outputs, and dependencies.

## 1. Clone and install the common runtime

Recommended platform: WSL2 or native Ubuntu 24.04, Python 3.12, ROS 2 Jazzy Desktop. Install ROS using the [official Jazzy Ubuntu instructions](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html), including `ros-dev-tools`.

```bash
git clone --branch thesis-final --recurse-submodules https://github.com/pmm20221102/mastertesis-wang.git
cd mastertesis-wang
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r llm-rag/requirements.txt
.venv/bin/python -m pip check
```

Install [Ollama](https://docs.ollama.com/linux), start its service (`ollama serve` in another terminal if systemd is unavailable), and obtain the model:

```bash
ollama pull gemma4:e2b-it-qat
curl -fsS http://127.0.0.1:11434/api/tags
```

Build the small ROS bridge required by **both** paths. All commands start at the repository root:

```bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then sudo rosdep init; fi
rosdep update
rosdep install --from-paths khronos-nav2-bridge/khronos_nav2_bridge --ignore-src -r -y
cd khronos-nav2-bridge
colcon build --packages-select khronos_nav2_bridge --symlink-install
source install/setup.bash
cd ..
```

ROS uses Ubuntu's system Python. The planner and Chroma use `.venv`; the launcher handles this interpreter boundary. See [Python environment details](llm-rag/BUILD_ENVIRONMENT.md).

## 2A. Short path: open the bundled navigation demo

After common setup, run from the repository root:

```bash
bash experiment/navigation-ui-demo/run_demo.sh
```

The launcher opens `http://127.0.0.1:8765`. Choose **Human-reviewed RAG (v5)** (default) or **Fresh RAG (no manual labels)**, enter an English navigation request, and click **Plan Route**. If several destinations match, choose one and confirm. The UI shows intent, RAG, task-chain, mission, and route outputs. Once a mission is ready, it starts an isolated Nav2 stack automatically. Click the RViz2 demo button after route planning succeeds.

The two GUI profiles share the same Hospital P1000 DSG, native Hospital109 labels, embedding model/revision, and floor-map bundle. **Human-reviewed RAG (v5)** also applies 78 manually checked object labels to objects whose DSG labels are unknown. **Fresh RAG (no manual labels)** builds from the DSG alone and leaves those 78 objects without the manual semantic additions. The reviewed index can therefore ground phrases that depend on those labels; the fresh index can return no match for them. Choose the profile whose semantic inputs you want to demonstrate. The UI displays the selected profile and records its build ID in the result.

The default backend is the complete `llm2` path. `HOSPITAL_TASK_CHAIN_BACKEND=grounded_deterministic` selects the documented deterministic replacement for comparison. The demo uses an 8,192-token context and a 2,048-token output budget. These are demo settings, not a reconstruction of historical request defaults.

No-match and unsupported requests return application-level feedback. Raw Nav2/RViz diagnostics are retained in local session log files and are not streamed into the progress panel. See the [demo README](experiment/navigation-ui-demo/README.md) for RAG discovery, custom profiles, and environment overrides.

## 2B. Full path: regenerate Khronos outputs, RAG, and maps

Follow [the full reproduction guide](experiment/CODE_REPRODUCTION.md) in order:

1. Download or supply Hospital P1000 sensor data; audit and convert it to a ROS 2 bag.
2. [Build Khronos and the TartanGround adapter](khronos/BUILD_ENVIRONMENT.md). The recommended closed-set path uses the same **Hospital109** label space in the mapper and RAG builder. It does not require TensorRT or an online segmentation model.
3. Run Khronos and replay the bag in one isolated ROS domain. Finish mapping and wait for nonempty `dsg.json` and `mesh.ply` in the actual timestamped run directory.
4. Build RAG from this `dsg.json`, using `--reproducible --dataset hospital-p1000`. Human labels are optional and must belong to this graph's Object IDs.
5. Generate new floor maps from the **same** DSG and mesh, then package a matching Nav2 bundle/defaults/profile using `experiment/map-generation/package_nav2_run.py`.
6. Add the run directory to `HOSPITAL_RAG_DISCOVERY_ROOTS`, refresh the GUI, choose the new RAG, and repeat the language → task-chain → Nav2/RViz2 interaction.

Full-scene mapping has a substantial memory footprint. A partial 3178-frame run in this environment reached about 16 GiB RSS before it was stopped at the user's request; its output is incomplete and is not used as a build input. The minimum RAM for a complete run has not been established. Use the bundled path on a machine that cannot sustain full mapping.

For this Hospital workflow, floor elevations, extents, initial poses, and portal templates are explicit scene configuration. Other scenes require checked configuration; free portal endpoints alone do not prove route connectivity.

## Interfaces and provenance

- RAG build: `canonical_graph_v2.json`, `rag_documents.jsonl`, `vector_db/`, `build_config.json`, and `build_manifest.json`. Label-space identity and mapping hash are recorded with the encoder revision.
- Map build: per-floor YAML/PGM/report files and `manifest.json`, with source DSG/mesh hashes.
- Run profile: `hospital_floor_transitions.yaml`, `hospital_nav2_plan_defaults.yaml`, and `run_profile_manifest.json`, tying the RAG and maps to the same DSG.
- Planner result: structured intent/grounding/task chain, mission, route, source/model summaries, timings, and `execution_performed`.
- GUI execution: local per-session JSON and diagnostic logs under `~/.cache/hospital-nav2-ui`.

A plan-only `planned` result means Nav2 found a route; execution results are recorded separately. RViz2 uses a point-robot kinematic simulation with configured map/pose transitions and a 0.5 m/s desired speed. Use an isolated simulation ROS domain.

## Tests and validation

Run the Python checks from the repository root after installing the bridge and ROS dependencies above. `PYTEST_DISABLE_PLUGIN_AUTOLOAD` keeps unrelated ROS/colcon pytest entry points from changing the test environment:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q \
  llm-rag/planner/tests \
  llm-rag/RAG/khronos_3dsg_rag/tests \
  experiment/map-generation/test_generate_khronos_only.py \
  experiment/map-generation/test_package_nav2_run.py \
  experiment/navigation-ui-demo/tests experiment/tests
```

Build ROS packages inside their module directory so generated `build/install/log` folders stay out of the repository root. The [reproduction guide](experiment/CODE_REPRODUCTION.md) also covers ROS tests and live validation.

## Historical thesis experiments

The main 60×4 task-chain outputs, 49 prepared missions, first-pass results, and the independent four-floor demonstration are archived under `experiment/`. The 49 missions come from the same primary Full prediction batch, with different scoring/preparation criteria; 43/49 is the original first-pass completion count. Diagnostic reruns remain separate.

Running the software on a new graph is distinct from recalculating the historical Gold metrics. Exact historical batch-replay materials that are not yet delivered are listed in [reproducibility gaps](experiment/REPRODUCIBILITY_GAPS.md). Archived outputs are not presented as a new run.
