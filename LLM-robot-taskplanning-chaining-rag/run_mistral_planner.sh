#!/bin/bash
cd /home/ulmmt/Masterthesis-wang/LLM-robot-taskplanning-chaining-rag
PY=/opt/conda/envs/llm-task-planner/bin/python
DB=/home/ulmmt/Masterthesis-wang/RAG/khronos_3dsg_rag/outputs/office_dense/vector_db
CASES=eval/cases/planner_benchmark_50_v2.jsonl
echo "##### START mistral:latest $(date +%T)"
"$PY" -m eval.run_pipeline_eval --db "$DB" --planning-cases "$CASES" --mode oracle_grounding --model mistral:latest --repeats 3 --output eval/results/planner/mistral_latest --allow-blocked 2>&1 | tail -20
echo "##### END mistral:latest $(date +%T)"
echo "ALL_DONE $(date +%T)"