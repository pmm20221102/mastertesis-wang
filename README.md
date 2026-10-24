# Hospital P1000: Natural Language to Nav2 Path Planning

This repository contains a reproducible, software-only workflow that turns an
English Hospital P1000 navigation instruction into an ordered, Nav2-checked
path plan using a local LLM, a structured Khronos scene-graph RAG index, and a
four-floor Nav2 map bundle.

## Scope and success criteria

The pipeline is:

`English instruction -> LLM1 intent -> Hospital RAG grounding -> ordered task chain -> Nav2 candidate/path planning -> provenance JSON`

The default task-chain adapter is deterministic after grounding. LLM2 remains
available only for controlled comparisons with `--task-chain-backend llm2`.
The current Nav2 integration is plan-only: a `planned` result means Nav2 found
feasible paths for the ordered stops. It does not send `NavigateToPose`, move a
robot, or validate physical ANYmal dynamics. Multi-floor transitions are
abstract map/pose switches at configured connectors, not simulated stair
climbing.

The default robot profile is the point-robot planning profile with Nav2
costmaps retained. The four Khronos-only maps and their manifest are checked in
under `khronos-nav2-bridge/khronos_nav2_bridge/generated/hospital-p1000-khronos-only/`.

## Requirements and local inputs

- WSL 2, Ubuntu 24.04, ROS 2 Jazzy, `colcon`, `rosdep`, and RViz2.
- Python 3.12, Ollama, and the model `gemma4:e2b-it-qat`.
- The local Hospital P1000 Khronos `dsg.json`, `mesh.ply`, and the reviewed
  `hospital-object-review-2026-10-12.csv` for rebuilding the RAG index.
- The dataset snapshot is approximately 27 GB. Dataset files, the reviewed
  CSV, generated Chroma databases, and build/install/log directories are
  intentionally excluded from Git. Supply the local input paths yourself.

Install ROS 2 Jazzy according to the official ROS instructions before
continuing. The commands below assume the repository root is the current
directory.

If you do not already have the submission branch checked out:

```bash
git clone --branch codex/hospital-nl-rag-nav2-20261024 --single-branch \
  https://gitlab.kit.edu/roberto.corlito/ma_yunsong_wang.git Masterthesis-wang
cd Masterthesis-wang
```

## 1. Create the Python environment

```bash
cd /path/to/Masterthesis-wang
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e 'LLM-robot-taskplanning-chaining-rag[dev]'
```

The RAG builder uses a pinned CPU embedding model so Ollama can use the GPU
independently. The first build may download the pinned model revision.

## 2. Start Ollama

Ensure the Ollama service is running, then pull the configured model:

```bash
ollama pull gemma4:e2b-it-qat
ollama list
```

If Ollama is not managed as a service in the WSL distribution, start `ollama
serve` in a separate terminal and keep that terminal open.

## 3. Build the reproducible Hospital RAG index

The command consumes the Khronos DSG and human-reviewed semantic CSV without
modifying either input. It writes a canonical graph, documents, a pinned
ChromaDB vector index, and provenance manifests. The planner's default database
path is the v5 output shown below.

```bash
cd /path/to/Masterthesis-wang
.venv/bin/python RAG/khronos_3dsg_rag/src/main.py \
  --config RAG/khronos_3dsg_rag/config.yaml build \
  --reproducible \
  --dataset hospital-p1000 \
  --input /path/to/hospital/full-data/dsg.json \
  --review-csv /path/to/hospital-object-review-2026-10-12.csv \
  --embedding-model paraphrase-multilingual-MiniLM-L12-v2 \
  --embedding-model-revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
  --embedding-device cpu \
  --output RAG/khronos_3dsg_rag/outputs/hospital_p1000_reproducible_20261024_v5
```

The builder refuses to overwrite an existing output directory. Keep an existing
build intact; choose a new versioned output path and pass its `vector_db`
directory to the planner with `--db-dir` when rebuilding. Verify the manifest
and logical vector-content digest before using a database.

## 4. Optional: regenerate the Khronos-only maps

The committed maps are ready for the normal workflow; regeneration is not
required. To reproduce the map bundle from local Khronos output:

```bash
cd /path/to/Masterthesis-wang
.venv/bin/python experiments/hospital-khronos-only-20261019/generate_khronos_only.py \
  --dsg /path/to/hospital/khronos-output/dsg.json \
  --mesh /path/to/hospital/khronos-output/mesh.ply \
  --output /tmp/hospital-p1000-khronos-only-generated
```

The checked-in `grid_geometry.json` defines floor extents, resolution, and
origin; the generator uses Place support and mesh projection, not depth-map
occupancy pixels. Regenerated output is diagnostic until its maps, reports,
manifest hashes, and package bundle have all been reviewed together.

## 5. Build the ROS 2/Nav2 package

```bash
cd /path/to/Masterthesis-wang
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
rosdep install --from-paths khronos-nav2-bridge/khronos_nav2_bridge \
  --ignore-src -r -y
cd khronos-nav2-bridge
colcon build --packages-select khronos_nav2_bridge --symlink-install
source install/setup.bash
```

The PATH setting selects the ROS system Python and avoids a user Python taking
precedence over ROS Jazzy packages.

## 6. Start the isolated Hospital Nav2 stack

In terminal 1, start the packaged F0 map and point-robot parameters on a
dedicated local simulation domain:

```bash
cd /path/to/Masterthesis-wang
source /opt/ros/jazzy/setup.bash
source khronos-nav2-bridge/install/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
export ROS_DOMAIN_ID=77
ros2 launch khronos_nav2_bridge hospital_nav2.launch.py \
  map:=khronos-nav2-bridge/khronos_nav2_bridge/generated/hospital-p1000-khronos-only/floor_0/map.yaml \
  params_file:=khronos-nav2-bridge/khronos_nav2_bridge/config/nav2_hospital_point.yaml
```

Wait until Nav2 reports `Managed nodes are active`. Do not use a real robot ROS
domain for this plan-only workflow.

To open RViz2 with this same single-floor Nav2 stack, use
`hospital_nav2_rviz.launch.py` instead of `hospital_nav2.launch.py` and pass the
same `map` and `params_file` arguments. Do not launch both variants in the same
ROS domain.

## 7. Submit an English instruction

In terminal 2, use the same domain and the repository Python environment:

```bash
cd /path/to/Masterthesis-wang
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
export HOSPITAL_NAV2_PYTHON="$PWD/.venv/bin/python"
bash scripts/plan_hospital_nav2.sh \
  --instruction "Please take me to a room with a chair and a door." \
  --ros-domain-id 77 \
  --output /tmp/hospital-nav2-plan.json
```

The wrapper defaults to `gemma4:e2b-it-qat`, the v5 RAG database, the checked-in
four-floor transition bundle, and the deterministic grounded task-chain
adapter. Use `--model`, `--db-dir`, `--bundle`, `--robot-model`, or
`--task-chain-backend` to override a default explicitly. Output files are never
overwritten.

## 8. Interpret the result

- `planned` with `execution_performed: false`: all ordered stops received
  feasible Nav2 paths; no motion command was sent.
- `intent_parse_failed`, `grounding_failed`, `planning_failed`, or another
  non-success status: inspect the stage-specific error and timing fields in the
  JSON before changing prompts, map data, or Nav2 parameters.
- For an ambiguous destination, the planner evaluates grounded candidates
  against floor assignment and feasible route cost; it does not treat a
  candidate list as proof that navigation succeeded.

The JSON records the instruction, model and source digests, RAG build and
vector-content digests, map-bundle hash, selected target candidates, route
geometry, stage timings, and whether execution occurred. A successful plan is
software path-planning evidence only, not robot motion, physical safety, or a
benchmark result.

## 9. Run the tests

From the repository root:

```bash
.venv/bin/python -m pytest -q \
  LLM-robot-taskplanning-chaining-rag/tests \
  RAG/khronos_3dsg_rag/tests \
  experiments/hospital-khronos-only-20261019/test_generate_khronos_only.py

source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
cd khronos-nav2-bridge
colcon test --packages-select khronos_nav2_bridge --event-handlers console_direct+
colcon test-result --verbose
```

The checked-in plan-only record is at
`experiments/hospital-natural-language-nav2-interface-v1-20261024/results/plan-room-chair-door-packaged-maps.json`.
It is a software planning result, not an execution recording.
