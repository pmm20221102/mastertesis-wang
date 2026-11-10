# Hospital Navigation GUI Demo

This is an optional, self-contained demonstration module for the thesis repository. It connects the natural-language planner to a selected Hospital P1000 RAG build, resolves destinations against the scene graph, compiles a navigation mission, and can display point-robot Nav2 execution in RViz2.

> **Quick start:** For this packaged demo, you do not need to rebuild the RAG index, regenerate maps, or package a Nav2 run profile. The two RAG indexes and matching profiles are bundled, and the demo reuses the four-floor maps already packaged in the repository. You still need the Python dependencies, a built ROS 2/Nav2 bridge, and the local Ollama model listed below.

For a remote GPU workstation, follow the [Docker and SSH guide](../../docker/README.md).
It exposes both this GUI and the RViz2 desktop through an SSH tunnel.

## Included RAG Profiles

| GUI option | Build ID | Semantic review |
| --- | --- | --- |
| Human-reviewed RAG (v5) | `32ac2c76a36d682943f024730db6b73fb85e4cede4c018be130975eddfcad7c7` | 78 manually confirmed object overrides; sidecar SHA-256 `b774341bc1c0338f8241c29f000360dc380310ef9bc8066e3eed3f6ca188b7cb` |
| Fresh RAG (no manual labels) | `d10694264b2ee4222ce32a75a5dd10eff63208340dfaacd49add0843466757ca` | No manual override file |

Both profiles use the same Hospital DSG SHA-256 `a051980dbb1a3d19caa8deeb3027eb534cb7f87760a0b0d906e26ca4a661f14d`, Hospital109 mapping SHA-256 `271eb9e71e5be5362f515fd9c65980f78ec56ae7b22708477aba1c1379f93f2b`, `paraphrase-multilingual-MiniLM-L12-v2` embedding revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, and floor-map bundle. **Human-reviewed RAG** adds 78 manually checked labels for objects whose original DSG labels were unknown. **Fresh RAG** uses the DSG labels without that sidecar, so those objects remain unlabeled and some instructions may return `target_not_found`. The difference is already encoded in each database; the GUI loads the chosen profile and does not apply annotations at query time. Select the reviewed option to use the human semantic corrections, or the fresh option to see behavior from automatic labels alone. Each result records its selected profile/build ID. The reviewed profile is selected by default. The legacy profile field `review_csv_sha256` stores the hash of the bundled JSON override sidecar for this build.

Both profiles retain all 42 native Hospital109 object labels. The reviewed profile additionally resolves 78 objects that were unknown in the source DSG. A phrase matching one of those reviewed labels can ground in the reviewed profile and return no match in the fresh profile. The selector loads the complete chosen index and matching defaults; it does not add labels after retrieval.

## Automatic RAG Discovery

The GUI scans for newly generated RAG builds every 15 seconds and has a Refresh RAG list button. By default it searches:

- The repository experiment directory.
- Run-like folders in the WSL home directory.
- The Hospital RAG outputs directory under ~/Masterthesis-wang.
- Additional roots listed in HOSPITAL_RAG_DISCOVERY_ROOTS.

Set extra WSL paths as a colon-separated list before launch, for example:

    export HOSPITAL_RAG_DISCOVERY_ROOTS="$HOME/hospital-runs:/mnt/d/hospital-runs"

A generated build is offered only when it has a vector_db directory, build manifest, canonical graph, and a neighboring nav2-profile directory containing hospital_nav2_plan_defaults.yaml, hospital_floor_transitions.yaml, and run_profile_manifest.json. The run manifest must match the RAG build, DSG, map bundle, and defaults hashes. Incomplete or mismatched runs are skipped. Full Chroma content verification is performed before planning.

## Requirements

- WSL2 with Ubuntu 24.04.
- ROS 2 Jazzy.
- The khronos-nav2-bridge package built in this checkout.
- Ollama with gemma4:e2b-it-qat available locally.
- A Python environment with the dependencies in llm-rag/requirements.txt.

The launcher sources ROS Jazzy and the bridge workspace. It selects a Python environment with the required imports from the repository .venv, the standard Miniconda location, or PATH. Set HOSPITAL_UI_PYTHON to use a specific interpreter.

## Launch

From the repository root in WSL:

    bash experiment/navigation-ui-demo/run_demo.sh

The GUI opens at http://127.0.0.1:8765. The default task-chain backend is LLM2; the page banner and saved plan identify the backend. Enter an English navigation request, choose a RAG profile, and press Plan Route. If grounding returns multiple candidates, select a destination and confirm. The left column displays LLM1 intent, RAG grounding, task chain, mission, Nav2 route, and RViz execution output. The progress column displays application-level status messages.

Changing the RAG clears the current plan and stage outputs. The Stop Simulation button stops the GUI-owned Nav2/RViz processes and clears the displayed plan outputs.

## Task-chain backend interface

The demo defaults to the thesis path **LLM1 intent → RAG grounding → LLM2 task chain → validated mission → Nav2 route**. The planner CLI elsewhere in the repository retains its own default; this setting applies only to this demo.

```bash
# Full LLM1 + RAG + LLM2 path (also the default when unset).
HOSPITAL_TASK_CHAIN_BACKEND=llm2 bash experiment/navigation-ui-demo/run_demo.sh

# Deterministic task-chain alternative: still uses LLM1 and RAG, bypasses LLM2.
HOSPITAL_TASK_CHAIN_BACKEND=grounded_deterministic bash experiment/navigation-ui-demo/run_demo.sh
```

Only `llm2` and `grounded_deterministic` are accepted. The backend is fixed for the server process; restart it to change modes. `GET /api/state` exposes `task_chain_backend`, the page displays it, and each saved plan and its UI summary records it. Existing request APIs are unchanged: `POST /api/plan` with `{"instruction":"..."}`, `POST /api/select` with `{"selections":{"reference_key":"node_id"}}`, then `POST /api/demo` to execute a successfully planned route.

Planning does not require a prestarted Nav2 stack. Intent parsing, grounding, destination selection, task-chain validation, and mission compilation happen first. Only a ready mission invokes the Nav2 startup hook; RViz starts when you press **Run Navigation in RViz2** after route planning succeeds. `planned` is plan-only (`execution_performed: false`); simulation execution is a separate stage.

## Short workflow: packaged human-reviewed RAG and maps

No Khronos build, sensor replay, new DSG, map generation, or RAG rebuild is needed. The name `khronos_nav2_bridge` refers to the ROS navigation package, which is still required; it does not require running Khronos perception.

1. Prepare the Python/Ollama and ROS Jazzy dependencies described in [BUILD_ENVIRONMENT](../../llm-rag/BUILD_ENVIRONMENT.md). The embedding model at the pinned revision must be cached locally or downloadable, even when the vector database is already packaged.
2. Build only the navigation bridge if it is not built in this checkout:

   ```bash
   source /opt/ros/jazzy/setup.bash
   export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
   (cd khronos-nav2-bridge && colcon build --packages-select khronos_nav2_bridge --symlink-install)
   ```

3. Start the local Ollama service if needed and ensure `gemma4:e2b-it-qat` is installed. Launch from the repository root:

   ```bash
   export HOSPITAL_UI_PYTHON="$PWD/.venv/bin/python"  # must have llm-rag/requirements.txt installed
   HOSPITAL_TASK_CHAIN_BACKEND=llm2 bash experiment/navigation-ui-demo/run_demo.sh
   ```

4. Open the local UI and keep **Human-reviewed RAG (v5)** selected. Enter an instruction such as `Take me to a room with a bed`, press **Plan Route**, and confirm any ambiguous destination. Check the intent, grounding, task chain, mission, and Nav2 stage outputs. Then press **Run Navigation in RViz2** to view and execute the point-robot simulation. Use **Stop Simulation** to stop GUI-owned processes.

## Full workflow: a new Khronos output to visualized navigation

For perception from raw sensors or bags, first follow [Khronos setup](../../khronos/README.md) and the input/mapper steps in the [reproduction guide](../CODE_REPRODUCTION.md). These require the external dataset and produce a new `dsg.json` and `mesh.ply`; the GUI does not perform perception. If you already have that pair, begin here. Run from the repository root with a dependency-complete `.venv`:

```bash
export HOSPITAL_KHRONOS_OUTPUT=/absolute/path/to/new-khronos-output
export HOSPITAL_DSG="$HOSPITAL_KHRONOS_OUTPUT/dsg.json"
export HOSPITAL_MESH="$HOSPITAL_KHRONOS_OUTPUT/mesh.ply"
export HOSPITAL_RUN_OUTPUT="$(mktemp -d "$HOME/hospital-khronos-rebuild.XXXXXX")"
export HOSPITAL_RAG_OUTPUT="$HOSPITAL_RUN_OUTPUT/rag"
export HOSPITAL_MAP_OUTPUT="$HOSPITAL_RUN_OUTPUT/maps"
test -s "$HOSPITAL_DSG" && test -s "$HOSPITAL_MESH"

.venv/bin/python llm-rag/RAG/khronos_3dsg_rag/src/main.py \
  --config llm-rag/RAG/khronos_3dsg_rag/config.yaml build \
  --reproducible --dataset hospital-p1000 --label-space hospital109 --input "$HOSPITAL_DSG" \
  --embedding-model paraphrase-multilingual-MiniLM-L12-v2 \
  --embedding-model-revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
  --embedding-device cpu --output "$HOSPITAL_RAG_OUTPUT"

.venv/bin/python experiment/map-generation/generate_khronos_only.py \
  --dsg "$HOSPITAL_DSG" --mesh "$HOSPITAL_MESH" \
  --output "$HOSPITAL_MAP_OUTPUT" --no-manual-topology

.venv/bin/python experiment/map-generation/package_nav2_run.py \
  --maps-manifest "$HOSPITAL_MAP_OUTPUT/manifest.json" \
  --rag-build "$HOSPITAL_RAG_OUTPUT" --output "$HOSPITAL_RUN_OUTPUT/nav2-profile"

export HOSPITAL_RAG_DISCOVERY_ROOTS="$HOSPITAL_RUN_OUTPUT"
export HOSPITAL_UI_PYTHON="$PWD/.venv/bin/python"
HOSPITAL_TASK_CHAIN_BACKEND=llm2 bash experiment/navigation-ui-demo/run_demo.sh
```

These commands build a no-review RAG. To create a reviewed index, pass `--review-csv /absolute/path/to/review.csv` for a CSV tied to this exact DSG, or `--semantic-overrides /absolute/path/to/semantic_overrides.json` for a confirmed sidecar. The options are mutually exclusive. The bundled 78-label sidecar belongs only to its recorded DSG and must not be reused with another graph without matching its object IDs. A successful build does not guarantee that a phrase such as “bed” exists in the new graph; choose an observed label or add checked annotations for that DSG.

Press **Refresh RAG list**, choose the newly generated run by its label/build ID (the bundled human-reviewed profile remains the default), then follow the same Plan → optional selection → RViz steps. Discovery requires the RAG, maps, defaults, bundle, and run manifest to agree; do not change hashes or copy an unrelated profile to force acceptance. Map/profile packaging checks free cells and provenance; Nav2 must still confirm route connectivity. The Hospital geometry and abstract floor-transition assumptions still apply.

## Offline regression tests

From the repository root, using a Python with `pytest` and `PyYAML`:

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m pytest -q -p no:cacheprovider experiment/navigation-ui-demo/tests
bash -n experiment/navigation-ui-demo/run_demo.sh
```

Tests replace model, database, subprocess, and ROS adapters. They cover cold startup (`Node not found` with exit 1), exact lifecycle states including inactive during startup, active same/different map handling, failed/timeout/malformed probes, owned-process shutdown, backend defaults/override/provenance, planner callback order, early pipeline exits, and RViz execution gating. They do not establish that live ROS, LLM inference, or the packaged artifacts work on a target machine.

## Maps and Simulation

The demo uses the four-floor Khronos-only maps in khronos-nav2-bridge/khronos_nav2_bridge/generated/hospital-p1000-khronos-only and a matching transition bundle. Map paths are workspace-relative and each map YAML/PGM is checked against its recorded SHA-256.

RViz2 runs an isolated point-robot kinematic simulation on ROS_DOMAIN_ID 79. The Nav2 profile sets a 0.5 m/s desired linear speed; the controller may reduce actual speed around turns and goals. This is software demonstration only. It does not control a physical robot, validate ANYmal hardware, or demonstrate physical stair/elevator traversal.

The GUI writes per-session planning and demo JSON files under ~/.cache/hospital-nav2-ui.


The lifecycle probe must report exactly `active [3]` before reuse or startup readiness. An exact `Node not found` response permits a cold launch even with exit code 1. An inactive stack, unknown response, transport error, or failed map check blocks launching a competing stack. An active external stack is reused only after its map matches. The GUI keeps no process handle for a reused external stack and never stops it; select another isolated domain or ask its owner to stop/recover it when incompatible. Use `HOSPITAL_NAV2_DOMAIN_ID` to choose the simulation domain (default 79).

## Example object descriptions

The bundled Human-reviewed RAG (v5) canonical graph contains 120 Object nodes
with 24 distinct labels. The table includes both native and manually reviewed
labels. Counts refer to graph nodes. Label spelling is preserved as stored.

| Object label | Instances |
|---|---:|
| door | 26 |
| stairs | 11 |
| table | 11 |
| wall | 11 |
| sofa | 10 |
| cabinet | 9 |
| chair | 6 |
| desk | 4 |
| doorway | 4 |
| floor | 3 |
| railing | 3 |
| sign | 3 |
| staircase | 3 |
| elevator | 2 |
| exitsign | 2 |
| healthmonitor | 2 |
| nightstand | 2 |
| window | 2 |
| acousticceiling | 1 |
| bed | 1 |
| Entrance | 1 |
| medicalscreen | 1 |
| metalmedicaltable | 1 |
| screen | 1 |

Try these instructions:

- Take me to a room with a health monitor.
- Please take me into the room with the bed.
- Take me to a room that has a desk and a sign.

A recorded label can have multiple instances. Confirm the destination if the
GUI asks. Navigation also requires a usable anchor and route. If you select a
different RAG build, use descriptions grounded in that build's own labels.
The [60 benchmark instructions](../task-chain-60/public/tasks_en.jsonl) also
include cases that expect clarification or a no-match response.
