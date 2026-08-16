#!/bin/bash
cd /home/ulmmt/Masterthesis-wang/LLM-robot-taskplanning-chaining-rag
PY=/opt/conda/envs/llm-task-planner/bin/python
echo "##### DEV mistral $(date +%T)"
"$PY" -m eval.run_e2e_eval --model mistral:latest --repeats 1 --cases e2e_dev_v1.jsonl --output eval/results/e2e_dev_v2/mistral 2>&1 | grep -v INFO | tail -18
echo "##### TEST granite $(date +%T)"
"$PY" -m eval.run_e2e_eval --model granite4.1:3b --repeats 3 --cases e2e_test_50_v1.jsonl --output eval/results/e2e_test_v2/granite 2>&1 | grep -v INFO | tail -20
echo "##### TEST mistral $(date +%T)"
"$PY" -m eval.run_e2e_eval --model mistral:latest --repeats 3 --cases e2e_test_50_v1.jsonl --output eval/results/e2e_test_v2/mistral 2>&1 | grep -v INFO | tail -20
echo "ALL_DONE $(date +%T)"