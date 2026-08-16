#!/bin/bash
cd /home/ulmmt/Masterthesis-wang/LLM-robot-taskplanning-chaining-rag
PY=/opt/conda/envs/llm-task-planner/bin/python
for bench in "e2e_test_50_v1.jsonl:e2e_test_v2" "e2e_supported_scope_36_v1.jsonl:e2e_supported_scope"; do
  cases=${bench%%:*}; out=${bench##*:}
  for m in granite4.1:3b mistral:latest; do
    slug=granite; [ "$m" = "mistral:latest" ] && slug=mistral
    echo "##### $bench $m $(date +%T)"
    "$PY" -m eval.run_e2e_eval --model "$m" --repeats 3 --cases "$cases" --output "eval/results/$out/$slug" 2>&1 | grep -v INFO | tail -22
  done
done
echo "ALL_DONE $(date +%T)"