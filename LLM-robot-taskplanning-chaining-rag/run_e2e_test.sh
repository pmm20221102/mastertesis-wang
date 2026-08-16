#!/bin/bash
cd /home/ulmmt/Masterthesis-wang/LLM-robot-taskplanning-chaining-rag
PY=/opt/conda/envs/llm-task-planner/bin/python
run_one () {
  local m=$1 slug=$2
  echo "##### START $m $(date +%T)"
  "$PY" -m eval.run_e2e_eval --model "$m" --repeats 3 --cases e2e_test_50_v1.jsonl --output "eval/results/e2e_v1/$slug" 2>&1 | grep -v INFO | tail -20
  echo "##### END $m $(date +%T)"
}
run_one granite4.1:3b granite
run_one mistral:latest mistral
echo "ALL_DONE $(date +%T)"