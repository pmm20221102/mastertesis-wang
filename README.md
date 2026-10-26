# Hospital P1000: Natural Language to Nav2 Planning

This repository contains the thesis implementation and evaluation archive. The shortest software workflow is:

Natural-language instruction → LLM intent parsing → Khronos-RAG semantic grounding → ordered mission → ROS 2/Nav2 path planning → provenance JSON.

The default natural-language entry point runs in software plan-only mode: `planned` means Nav2 found feasible paths to the stops in the task chain, and `execution_performed` is `false`. It does not demonstrate physical ANYmal motion, stair/elevator traversal, or physical safety. Cross-floor routes use preconfigured abstract transitions, map loading, and pose resets. The low-level Nav2 mission runner is a separate interface and may execute goals only in an isolated simulation ROS domain.

## Modules and Documentation

| Module | Responsibility | Documentation |
|---|---|---|
| `llm-rag/` | The single LLM-RAG planner and Khronos DSG index builder | [`llm-rag/README.md`](llm-rag/README.md), [`llm-rag/BUILD_ENVIRONMENT.md`](llm-rag/BUILD_ENVIRONMENT.md) |
| `khronos/` | Khronos source and TartanGround input adapter | [`khronos/README.md`](khronos/README.md) |
| `khronos-nav2-bridge/` | Hospital P1000 ROS 2/Nav2 package | [`khronos-nav2-bridge/README.md`](khronos-nav2-bridge/README.md) |
| `experiment/` | Thesis data, evaluation results, maps, and demonstrations | [`experiment/README.md`](experiment/README.md) |

The repository root contains this README as its only Markdown file. Each module documents its own build, dependencies, and interfaces.

For clean-environment setup, tests, a RAG index build, and a Nav2 plan-only request, follow the step-by-step [code reproduction guide](experiment/CODE_REPRODUCTION.md). It distinguishes runnable workflows from historical experiment archives.

## Environment Requirements

- Recommended platform: WSL 2 / Ubuntu 24.04, Python 3.12, ROS 2 Jazzy, colcon, and rosdep.
- Python direct dependencies are pinned in [`llm-rag/requirements.txt`](llm-rag/requirements.txt). Nav2 ROS dependencies are declared in `khronos-nav2-bridge/khronos_nav2_bridge/package.xml` and installed with rosdep.
- The local model service is Ollama; the default model is `gemma4:e2b-it-qat`.
- The original Hospital P1000 DSG, mesh, and manually reviewed CSV (about 27 GB of data) are local inputs and are not stored in Git. Supply their paths when building the RAG index.
- See `llm-rag/BUILD_ENVIRONMENT.md` for dependency sources, version constraints, installation order, and verification commands.

The following commands assume the repository root is the current directory. Install ROS 2 Jazzy first, then create the Python environment:

~~~bash
python3 -m venv .venv
.venv/bin/pip install -r llm-rag/requirements.txt
~~~

Install Ollama from the [official Linux instructions](https://docs.ollama.com/linux):

~~~bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
ollama pull gemma4:e2b-it-qat
curl -fsS http://127.0.0.1:11434/api/tags
~~~

If systemd is unavailable, run `ollama serve` in a dedicated terminal and keep it open, then run the model-pull and API-check commands in another terminal. See `llm-rag/BUILD_ENVIRONMENT.md` for version recording and service checks.

Use rosdep to install the dependencies declared by the bridge and build the Hospital package:

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then sudo rosdep init; fi
rosdep update
rosdep install --from-paths khronos-nav2-bridge/khronos_nav2_bridge --ignore-src -r -y
cd khronos-nav2-bridge
colcon build --packages-select khronos_nav2_bridge --symlink-install
source install/setup.bash
cd ..
~~~

The system-first `PATH` ensures ROS Jazzy uses Ubuntu's system Python rather than a Conda Python.

## Build the Hospital RAG Index

The builder reads the DSG and review CSV without modifying either input. The output directory must not already exist. The versioned `v5` path below matches the default `--db-dir` used by the natural-language planner.

~~~bash
.venv/bin/python llm-rag/RAG/khronos_3dsg_rag/src/main.py \
  --config llm-rag/RAG/khronos_3dsg_rag/config.yaml build \
  --reproducible \
  --dataset hospital-p1000 \
  --input /path/to/hospital/full-data/dsg.json \
  --review-csv /path/to/hospital-object-review-2026-10-12.csv \
  --embedding-model paraphrase-multilingual-MiniLM-L12-v2 \
  --embedding-model-revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
  --embedding-device cpu \
  --output llm-rag/RAG/khronos_3dsg_rag/outputs/hospital_p1000_reproducible_20261024_v5
~~~

On success, the output directory contains the canonical graph, RAG documents, `vector_db`, and build manifest. If you choose a custom output path, pass the `vector_db` directory under that path to the planner with `--db-dir`. Existing output directories are never overwritten; use a new versioned path for each rebuild.

## Run the Natural-Language-to-Nav2 Workflow

Terminal 1: start the isolated Hospital F0 Nav2 stack. Wait until the log reports `Managed nodes are active`. Do not use a real robot's ROS domain.

~~~bash
source /opt/ros/jazzy/setup.bash
source khronos-nav2-bridge/install/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
export ROS_DOMAIN_ID=77
ros2 launch khronos_nav2_bridge hospital_nav2.launch.py \
  map:=khronos-nav2-bridge/khronos_nav2_bridge/generated/hospital-p1000-khronos-only/floor_0/map.yaml \
  params_file:=khronos-nav2-bridge/khronos_nav2_bridge/config/nav2_hospital_point.yaml
~~~

Terminal 2: submit a natural-language instruction. The script reads the default `v5` RAG index and refuses to overwrite an existing result file.

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
export ROS_DOMAIN_ID=77
export HOSPITAL_NAV2_PYTHON="$PWD/.venv/bin/python"
bash llm-rag/plan_hospital_nav2.sh \
  --instruction "Please take me into the room with the bed." \
  --ros-domain-id 77 \
  --output /tmp/hospital-nav2-plan.json
~~~

Main options:

- `--model`: override the Ollama model.
- `--db-dir`: override the RAG `vector_db`.
- `--bundle`: override the floor-map and abstract-transition configuration.
- `--task-chain-backend grounded_deterministic` or `llm2`: deterministic compilation is the default; `llm2` is retained for controlled comparison.
- `--num-ctx`, `--num-predict`, and `--request-timeout-s`: set per-request LLM limits and timeout.
- `--robot-model point` or `footprint`: select the Nav2 robot profile.

See all options with:

~~~bash
.venv/bin/python llm-rag/planner/plan_nav2.py --help
~~~

The output JSON records the instruction, model and code summaries, RAG/map provenance, target candidates, route geometry, stage timings, and `execution_performed`. A target candidate does not mean it was reached; a successful plan-only result does not demonstrate physical robot motion.

## Nav2 Mission Interface

The low-level runner consumes a frozen mission JSON, a map bundle, and an output path. Its default mode may send `NavigateToPose` goals; add `--plan-only` to validate route planning without sending navigation goals.

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
export ROS_DOMAIN_ID=77
/usr/bin/python3 khronos-nav2-bridge/khronos_nav2_bridge/scripts/run_task_chain_nav2.py \
  --mission experiment/nav2-49/missions/HC-001.mission.json \
  --bundle khronos-nav2-bridge/khronos_nav2_bridge/config/hospital_p1000_floor_transitions.yaml \
  --output /tmp/HC-001-result.json \
  --plan-only \
  --robot-model point
~~~

This command uses ROS Jazzy's system Python so it can import `rclpy`. Inputs are ordered stops and candidate targets; output is a per-task JSON with status, path, and stage information. Actual navigation must only connect to an isolated simulation domain.

## Data and Evaluation Denominators

- `experiment/task-chain-60/public/tasks_en.jsonl` contains 60 inputs. The thesis-selected `surface-v1-llm2-60x4-20261020` run contains 240 task-condition outputs across Full, No LLM1, Raw DSG, and No LLM2.
- `experiment/nav2-49/missions` contains 49 executable missions selected from an earlier Full prediction run. `preparation_report.json` also records excluded invalid outputs. `tasks/` contains 49 first-pass results; `retry-20261023/` contains separate diagnostic reruns for six failures.
- The reported 43/49 is the original first-pass result. The six successful reruns do not replace that score. Task-chain successes, the Nav2 task denominator, and the HC-031 demonstration are separate evidence sets and must not be combined into one end-to-end success rate.
- Other task-chain, semantic-RAG, and retrieval evaluations are stored under `experiment/evaluations/`. Historical manifests retain their original absolute paths as provenance; those paths are not current-checkout paths.

The original task-chain, Nav2 mission, and result directories were copied from the local thesis experiment archive. Rebuilding the complete RAG index and rerunning historical experiments still require the corresponding external data, models, and ROS environment.

## Tests and Troubleshooting

Python, RAG, map-generation, and interface-contract tests:

~~~bash
.venv/bin/python -m pytest -q \
  llm-rag/planner/tests \
  llm-rag/RAG/khronos_3dsg_rag/tests \
  experiment/map-generation/test_generate_khronos_only.py \
  experiment/tests
~~~

ROS package tests:

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
cd khronos-nav2-bridge
colcon test --packages-select khronos_nav2_bridge --event-handlers console_direct+
colcon test-result --verbose
~~~

- If the RAG `vector_db` is missing, run the build command above. Use `--db-dir` when using a custom output directory.
- If Ollama is unreachable, confirm that its service is running and check `http://127.0.0.1:11434`.
- If a Nav2 action/service is unavailable, wait for `Managed nodes are active` and confirm that both terminals use the same isolated `ROS_DOMAIN_ID`.
- If the raw DSG, mesh, or review CSV is missing, prepare these external inputs locally and provide their paths explicitly.
