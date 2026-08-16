#!/bin/bash
cd /home/ulmmt/Masterthesis-wang/LLM-robot-taskplanning-chaining-rag
PY=/opt/conda/envs/llm-task-planner/bin/python
DB=/home/ulmmt/Masterthesis-wang/RAG/khronos_3dsg_rag/outputs/office_dense/vector_db
CASES=eval/cases/planner_benchmark_50_v2.jsonl
run_one () {
  local m=$1 slug=$2
  echo "##### START $m $(date +%T)"
  "$PY" -m eval.run_pipeline_eval --db "$DB" --planning-cases "$CASES" --mode oracle_grounding --model "$m" --repeats 3 --output "eval/results/planner/$slug" --allow-blocked 2>&1 | tail -25
  echo "##### END $m $(date +%T)"
}
run_one mistral:latest mistral_latest
run_one granite4.1:3b granite_4_1_3b
echo "ALL_DONE $(date +%T)"