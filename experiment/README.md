# Experiments: Datasets, Evaluations, and Results

This module stores datasets, frozen inputs, evaluation scripts, and run outputs. Experiment records are an evidence archive; code refactors do not rerun or rewrite them automatically.

For clean-environment setup, tests, a provenance-bearing RAG build, and a single Nav2 plan-only request, follow [CODE_REPRODUCTION.md](CODE_REPRODUCTION.md). It also lists historical batches that cannot currently be replayed from this repository and the missing inputs.

## Main Thesis Experiments

### 60-Task Task-Chain Ablation

- Input: `task-chain-60/public/tasks_en.jsonl` contains 60 instructions.
- Main run: `task-chain-60/runs/surface-v1-llm2-60x4-20261020/`.
- Conditions: Full, No LLM1, Raw DSG, and No LLM2; each task-condition pair ran once, for 240 outputs.
- Provenance files: `artifact_manifest.json`, `draft/system_lock.json`, and `run_manifest.json`.
- `predictions/` stores condition outputs. `runtime_inputs/` stores the canonical graph, RAG documents, and index snapshot used by that run.

Datasets such as `final_intent_60` and `planning_generation_60` serve different evaluation purposes and are stored under `evaluations/taskchain/`; they are not the dataset used for this main ablation.

### 49 Nav2 Missions

- Input: 49 mission files in `nav2-49/missions/`; the preparation report is `missions/preparation_report.json`.
- First-pass results: 49 result JSON files in `nav2-49/tasks/`; `logs/` contains first-pass logs.
- Diagnostic reruns: logs and results for six failed missions are stored separately under `retry-20261023/`; they do not overwrite first-pass results.
- Thesis denominator: 43/49 on the first pass. Successes in the six reruns are supplementary diagnostics and do not replace the first-pass score.

These missions are an executable subset selected from an earlier Full prediction run. They do not represent a 49/60 task-chain success rate.

### Other Artifacts

- `four-floor-demo/`: standalone HC-031 four-floor RViz2 demonstration.
- `map-generation/`: Khronos-only map generator, geometry manifest, and tests. Generated `output/` can be rebuilt and is not committed.
- `interface-smoke/`: natural-language-to-Nav2 interface integration records; these are not benchmark results.
- `evaluations/taskchain/`, `evaluations/semantic-rag/`, and `evaluations/retrieval/`: other evaluation cases, scripts, and historical results.
- `evaluations/baseline/scene_graph.json`: optional fixture for baseline comparison in the RAG planner. The old baseline planner implementation is not included.

## Requirements for Full Replays

Archived missions, predictions, logs, and results can be used to verify thesis denominators; they are not evidence that a run has been reproduced. Rebuilding the full RAG index requires the local Hospital DSG, mesh, 78-row review CSV, and embedding model. Replaying Nav2 requires Ollama, a running isolated Nav2 stack, the matching map bundle, and a ROS domain. Absolute paths in historical manifests are preserved as run provenance and must not be treated as paths in the current checkout.

## Quick Archive-Count Checks

~~~bash
wc -l experiment/task-chain-60/public/tasks_en.jsonl
find experiment/nav2-49/missions -maxdepth 1 -name 'HC-*.mission.json' | wc -l
find experiment/nav2-49/tasks -maxdepth 1 -name 'HC-*.result.json' | wc -l
~~~

Expected counts are 60, 49, and 49, respectively. Matching these counts does not reproduce an experiment.
