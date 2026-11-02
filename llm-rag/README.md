# LLM-RAG: Scene-Graph Indexing and Task-Chain Planning

This module turns a Khronos DSG into a provenance-bearing graph/RAG build and connects natural-language instructions to ordered navigation missions.

## Components and contracts

| Component | Input | Output |
|---|---|---|
| `RAG/khronos_3dsg_rag/` | Khronos `dsg.json`, Hospital109 label space, optional labels tied to the same Object IDs | `canonical_graph_v2.json`, `rag_documents.jsonl`, Chroma `vector_db/`, `build_config.json`, and `build_manifest.json` |
| `planner/` LLM1 | User instruction | Structured actions, ordered stops, target phrases, and constraints |
| Graph/RAG grounding | LLM1 target phrases and the selected canonical/vector indexes | Entity IDs or complete candidate sets with graph evidence |
| Planner LLM2 | Intent contract and grounded references | Ordered task-chain draft using unique IDs or retained candidate sets |
| Binding and validation | LLM2 draft and grounding inventory | Validated mission plan or a structured failure |
| Nav2 adapter | Validated mission, run defaults, and floor-map bundle | Plan-only route result or a separate simulation-execution result |

The run profile ties RAG and maps to the same source DSG and records hashes. A plan-only `planned` status means Nav2 found routes; it does not mean a robot executed the route. The GUI demo can run Nav2 separately after planning and candidate confirmation.

## Build a new RAG index

Start with a completed Khronos `dsg.json`. For this repository's Hospital pipeline, identify the dataset label space explicitly; the full workflow uses `hospital109`. The clean build writes to a new directory and does not overwrite it:

```bash
.venv/bin/python llm-rag/RAG/khronos_3dsg_rag/src/main.py \
  --config llm-rag/RAG/khronos_3dsg_rag/config.yaml build \
  --reproducible --dataset hospital-p1000 --label-space hospital109 \
  --input /path/to/khronos-run/dsg.json \
  --embedding-model paraphrase-multilingual-MiniLM-L12-v2 \
  --embedding-model-revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
  --embedding-device cpu \
  --output /path/to/new-run/rag
```

Manual labels are optional. To use them, provide a review CSV or `semantic_overrides.json` whose Object IDs belong to this exact DSG. Do not reuse the bundled demo's historical sidecar on a different graph unless those IDs and annotations have been checked. The two label-input options are mutually exclusive.

The demo selector compares two already-built indexes. The human-reviewed profile contains 78 confirmed labels for objects that were unknown in its source DSG; the fresh profile does not. Both keep the same 42 native Hospital109 object labels, source DSG, embedding revision, and map bundle. A phrase depending on one of the reviewed labels may ground in the first profile and return `target_not_found` in the second. The GUI loads the entire selected index and matching run defaults; it does not apply annotations after retrieval.

Inspect all build inputs and outputs in `build_manifest.json`. A new index can contain different nodes and documents; downstream maps and run profile must use the same source DSG.

## Plan one natural-language request

With Ollama available, a matching RAG build and a running isolated Nav2 stack, use the repository wrapper:

```bash
export HOSPITAL_NAV2_PYTHON="$PWD/.venv/bin/python"
bash llm-rag/plan_hospital_nav2.sh \
  --instruction "Please take me to a room with a chair." \
  --task-chain-backend llm2 \
  --db-dir /path/to/new-run/rag/vector_db \
  --bundle /path/to/new-run/nav2-profile/hospital_floor_transitions.yaml \
  --defaults /path/to/new-run/nav2-profile/hospital_nav2_plan_defaults.yaml \
  --ros-domain-id 78 \
  --output /tmp/hospital-plan.json
```

The wrapper uses plan-only mode. Set `--task-chain-backend grounded_deterministic` for the deterministic LLM2 replacement. The output JSON records model/source provenance, pipeline stages, mission preparation, Nav2 route planning, and `execution_performed: false`.

## Run the interactive demo

For the shortest launch using bundled RAG and maps, see [navigation-ui-demo](../experiment/navigation-ui-demo/README.md). It includes an English request UI, selectable RAG profiles, multi-candidate interaction, automatic Nav2 startup after a mission is ready, and an RViz2 button after route planning succeeds.

## Tests and environment

Install the root Python environment from [`BUILD_ENVIRONMENT.md`](BUILD_ENVIRONMENT.md). Focused module tests:

```bash
.venv/bin/python -m pytest -q \
  llm-rag/planner/tests \
  llm-rag/RAG/khronos_3dsg_rag/tests
```

See [CODE_REPRODUCTION.md](../experiment/CODE_REPRODUCTION.md) for the raw-data → Khronos → RAG → maps → LLM task chain → Nav2/RViz workflow and its inputs. Historical evaluation gaps appear in [REPRODUCIBILITY_GAPS.md](../experiment/REPRODUCIBILITY_GAPS.md).
