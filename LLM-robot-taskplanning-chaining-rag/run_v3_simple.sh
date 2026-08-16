#!/bin/bash
cd /home/ulmmt/Masterthesis-wang/LLM-robot-taskplanning-chaining-rag
PY=/opt/conda/envs/llm-task-planner/bin/python
run_one () {
  local m=$1 slug=$2
  echo "##### START $m $(date +%T)"
  "$PY" -m eval.run_planner_simple_eval --model "$m" --repeats 3 --output "eval/results/planner_v3_simple/$slug" 2>&1 | tail -25
  echo "##### END $m $(date +%T)"
}
run_one mistral:latest mistral_latest
run_one granite4.1:3b granite_4_1_3b
echo "ALL_DONE $(date +%T)"