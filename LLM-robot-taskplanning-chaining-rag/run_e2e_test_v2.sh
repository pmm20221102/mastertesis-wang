#!/bin/bash
cd /home/ulmmt/Masterthesis-wang/LLM-robot-taskplanning-chaining-rag
PY=/opt/conda/envs/llm-task-planner/bin/python
for m in granite4.1:3b mistral:latest; do
  slug=granite; [ "$m" = "mistral:latest" ] && slug=mistral
  echo "##### TEST $m $(date +%T)"
  "$PY" -m eval.run_e2e_eval --model "$m" --repeats 3 --cases e2e_test_50_v1.jsonl --output "eval/results/e2e_test_v2/$slug" 2>&1 | grep -v INFO | tail -20
done
echo "ALL_DONE $(date +%T)"