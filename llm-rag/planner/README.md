# LLM-RAG planner

This is the repository's only natural-language task planner. It depends on the Khronos 3DSG RAG builder/retriever in `../RAG/khronos_3dsg_rag/` and connects to the repository's Hospital Nav2 bridge through a mission JSON and Nav2 bundle.

## Main Entry Point

The main natural-language-to-Nav2 entry point is `plan_nav2.py`. From the repository root, use the wrapper:

~~~bash
bash llm-rag/plan_hospital_nav2.sh \
  --instruction "Please take me to a room with a chair and a door." \
  --ros-domain-id 77 \
  --output /tmp/hospital-nav2-plan.json
~~~

Before running, build the RAG v5 index, start the isolated Hospital Nav2 stack, and source the ROS 2 Jazzy environment. See the repository-root `README.md` for the complete sequence.

## Parameters and Inputs/Outputs

`plan_nav2.py` inputs:

- Required: natural-language `--instruction` and a new output path via `--output`.
- Defaults: `gemma4:e2b-it-qat`, the Hospital v5 `vector_db`, Hospital floor bundle, and plan-only Nav2 adapter.
- Overridable: `--model`, `--db-dir`, `--bundle`, `--defaults`, `--intent-prompt-profile`, `--task-chain-backend`, `--num-ctx`, `--num-predict`, `--request-timeout-s`, `--floor-tolerance-m`, `--robot-model`, and `--ros-domain-id`.

The output JSON records the intent, entity candidates, task chain/mission, RAG and map provenance, route, stage statuses, and timings. A successful plan does not mean navigation was executed; in plan-only mode, `execution_performed=false`.

~~~bash
.venv/bin/python llm-rag/planner/plan_nav2.py --help
.venv/bin/python llm-rag/planner/main_rag.py --help
~~~

`main_rag.py` exposes lower-level intent, grounding, and task-chain interfaces. Thesis runs should use `plan_nav2.py` to keep the RAG, mission, and ROS-domain contracts in one entry point.

## Configuration and Dependencies

- Python runtime and test dependencies are listed in the repository-level `llm-rag/requirements.txt`; `pyproject.toml` retains package metadata and minimum versions. The project currently runs from the source tree and does not use an editable install.
- Ollama endpoint, default instruction, model list, and RAG index path are in `config_rag.yaml`.
- The default database path should match the v5 build output in the repository-root README. For a custom build output, pass `--db-dir` explicitly.
- Optional baseline comparison uses `experiment/evaluations/baseline/scene_graph.json`; this is a data fixture, not a second planner implementation.

For environment dependencies, see `../BUILD_ENVIRONMENT.md`.

## Tests

~~~bash
.venv/bin/python -m pytest -q llm-rag/planner/tests
~~~
