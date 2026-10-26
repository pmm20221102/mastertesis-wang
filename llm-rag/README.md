# LLM-RAG Module

This module contains the RAG task planner and its Khronos 3DSG index builder:

- `planner/`: LLM1 intent parsing, entity referencing, task-chain validation, and the natural-language-to-Nav2 mission entry point.
- `RAG/khronos_3dsg_rag/`: parses DSGs, exports canonical graphs, and builds retrieval documents and a Chroma vector database.

See `BUILD_ENVIRONMENT.md` for the build environment and dependency sources. The repository-root `README.md` describes the shortest end-to-end startup procedure.

## Main Interfaces

### Build a RAG Index

Run from the repository root:

~~~bash
.venv/bin/python llm-rag/RAG/khronos_3dsg_rag/src/main.py \
  --config llm-rag/RAG/khronos_3dsg_rag/config.yaml build \
  --reproducible --dataset hospital-p1000 \
  --input /path/to/hospital/full-data/dsg.json \
  --review-csv /path/to/hospital-object-review-2026-10-12.csv \
  --embedding-model paraphrase-multilingual-MiniLM-L12-v2 \
  --embedding-model-revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
  --embedding-device cpu \
  --output llm-rag/RAG/khronos_3dsg_rag/outputs/hospital_p1000_reproducible_20261024_v5
~~~

Inputs are the raw DSG and review CSV. The output directory contains the canonical graph, documents, `vector_db`, and build manifest. The build neither modifies inputs nor overwrites an existing output.

### Run the Planner

The supported natural-language-to-Nav2 entry point is `planner/plan_nav2.py`; the repository-root `plan_hospital_nav2.sh` is a convenience wrapper. Inputs are an instruction, RAG index, map bundle, and model options. The output is a new provenance JSON. The default execution scope is plan-only.

~~~bash
bash llm-rag/plan_hospital_nav2.sh \
  --instruction "Please take me to a room with a chair and a door." \
  --ros-domain-id 77 \
  --output /tmp/hospital-nav2-plan.json
~~~

For other parameters and status definitions, see the repository-root README and `planner/README.md`.

### Interactive navigation demo

The GUI demonstration is maintained as a separate module in experiment/navigation-ui-demo. It offers a selector for the human-reviewed RAG and the fresh no-review RAG, and discovers future generated run profiles. See that module's README for launch instructions, discovery roots, and packaged assets.

## Tests

~~~bash
.venv/bin/python -m pytest -q llm-rag/planner/tests llm-rag/RAG/khronos_3dsg_rag/tests
~~~
