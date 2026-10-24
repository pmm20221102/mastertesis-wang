#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: run_plan_only.sh MISSION BUNDLE OUTPUT ROBOT_MODEL" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
if [[ -f "${ROOT}/.local-deployment/activate-ros-cpu.sh" ]]; then
  source "${ROOT}/.local-deployment/activate-ros-cpu.sh"
else
  unset PYTHONHOME PYTHONPATH
  if [[ $- == *u* ]]; then
    set +u
    RESTORE_NOUNSET=1
  else
    RESTORE_NOUNSET=0
  fi
  source /opt/ros/jazzy/setup.bash
  if [[ "${RESTORE_NOUNSET}" == 1 ]]; then set -u; fi
  unset RESTORE_NOUNSET
  export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
  export PYTHONNOUSERSITE=1
fi

exec /usr/bin/python3 "${SCRIPT_DIR}/run_task_chain_nav2.py" \
  --mission "$1" \
  --bundle "$2" \
  --output "$3" \
  --robot-model "$4" \
  --plan-only
