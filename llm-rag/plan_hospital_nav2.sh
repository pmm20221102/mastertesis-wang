#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${ROOT}/.local-deployment/activate-python-cpu.sh" ]]; then
  source "${ROOT}/.local-deployment/activate-python-cpu.sh"
elif [[ -x "${ROOT}/.venv/bin/python" ]]; then
  MT_PYTHON="${ROOT}/.venv/bin/python"
fi
PYTHON_BIN="${HOSPITAL_NAV2_PYTHON:-${MT_PYTHON:-python3}}"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-77}"

exec "${PYTHON_BIN}" \
  "${ROOT}/llm-rag/planner/plan_nav2.py" "$@"
