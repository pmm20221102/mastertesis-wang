#!/usr/bin/env bash
set -euo pipefail

DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DEMO_DIR/../.." && pwd)"

export HOSPITAL_RAG_PROFILE_CONFIG="${HOSPITAL_RAG_PROFILE_CONFIG:-$DEMO_DIR/rag_profiles.json}"
export HOSPITAL_NAV2_BUNDLE="${HOSPITAL_NAV2_BUNDLE:-$DEMO_DIR/nav2-profile/hospital_floor_transitions.yaml}"
export HOSPITAL_NAV2_PARAMS="${HOSPITAL_NAV2_PARAMS:-$ROOT/khronos-nav2-bridge/khronos_nav2_bridge/config/nav2_hospital_rviz.yaml}"
export HOSPITAL_NAV2_RVIZ_CONFIG="${HOSPITAL_NAV2_RVIZ_CONFIG:-$ROOT/khronos-nav2-bridge/khronos_nav2_bridge/rviz/hospital_nav2_manual.rviz}"
export HOSPITAL_NAV2_DOMAIN_ID="${HOSPITAL_NAV2_DOMAIN_ID:-79}"

for required_file in "$HOSPITAL_RAG_PROFILE_CONFIG" "$HOSPITAL_NAV2_BUNDLE" "$HOSPITAL_NAV2_PARAMS" "$HOSPITAL_NAV2_RVIZ_CONFIG"; do
  if [[ ! -f "$required_file" ]]; then
    echo "Required demo file is missing: $required_file" >&2
    exit 2
  fi
done

if [[ ! -f "$ROOT/khronos-nav2-bridge/install/setup.bash" ]]; then
  echo "Nav2 bridge is not built. Follow khronos-nav2-bridge/README.md first." >&2
  exit 2
fi

set +u
source /opt/ros/jazzy/setup.bash
source "$ROOT/khronos-nav2-bridge/install/setup.bash"
set -u
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
export PYTHONNOUSERSITE=1
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_STATIC_PEERS=""

PYTHON_CANDIDATES=()
if [[ -n "${HOSPITAL_UI_PYTHON:-}" ]]; then
  PYTHON_CANDIDATES+=("$HOSPITAL_UI_PYTHON")
else
  PYTHON_CANDIDATES+=("$ROOT/.venv/bin/python")
  if [[ -n "${HOME:-}" ]]; then
    PYTHON_CANDIDATES+=("$HOME/miniconda3/bin/python")
  fi
  SYSTEM_PYTHON="$(command -v python3 || true)"
  if [[ -n "$SYSTEM_PYTHON" ]]; then
    PYTHON_CANDIDATES+=("$SYSTEM_PYTHON")
  fi
fi

PYTHON_BIN=""
for candidate in "${PYTHON_CANDIDATES[@]}"; do
  if [[ -x "$candidate" ]] && "$candidate" -c 'import yaml, chromadb, ollama, sentence_transformers' >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  echo "No Python interpreter with the demo dependencies was found." >&2
  echo "Install dependencies from llm-rag/BUILD_ENVIRONMENT.md or set HOSPITAL_UI_PYTHON." >&2
  exit 2
fi

cd "$ROOT"
exec "$PYTHON_BIN" "$DEMO_DIR/navigation_ui.py" \
  --host 127.0.0.1 --port "${HOSPITAL_UI_PORT:-8765}" --open-browser
