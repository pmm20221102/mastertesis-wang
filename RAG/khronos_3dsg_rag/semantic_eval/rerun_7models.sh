#!/bin/bash
cd /home/ulmmt/Masterthesis-wang/RAG/khronos_3dsg_rag/semantic_eval
PY=/opt/conda/envs/llm-task-planner/bin/python
for m in mistral:latest granite4.1:3b ministral-3:3b-instruct-2512-q4_K_M gemma4:e4b qwen3:4b qwen3.5:4b qwen3.5:2b; do
  echo "##### START $m $(date +%T)"
  "$PY" run_joint_eval_v3.py --model "$m" 2>&1
  echo "##### END $m $(date +%T)"
done
echo "ALL_DONE $(date +%T)"