#!/bin/bash
set -eo pipefail
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
source /opt/ros/jazzy/setup.bash
source /opt/thesis/khronos-nav2-bridge/install/setup.bash
rm -f /tmp/.X0-lock /tmp/.X11-unix/X0
Xvfb :0 -screen 0 1600x1000x24 -nolisten tcp &
sleep 1
openbox >/tmp/openbox.log 2>&1 &
x11vnc -display :0 -localhost -forever -shared -nopw >/tmp/vnc.log 2>&1 &
websockify --web=/usr/share/novnc 6080 localhost:5900 >/tmp/websockify.log 2>&1 &
/opt/venv/bin/python - <<'PY'
from pathlib import Path
import os
import yaml
p = Path("llm-rag/planner/config_rag.yaml")
config = yaml.safe_load(p.read_text())
config["ollama_host"] = os.environ.get("OLLAMA_HOST", "http://ollama:11434")
p.write_text(yaml.safe_dump(config))
PY
export HOSPITAL_RAG_PROFILE_CONFIG=/opt/thesis/experiment/navigation-ui-demo/rag_profiles.json
export HOSPITAL_NAV2_PARAMS=/opt/thesis/khronos-nav2-bridge/khronos_nav2_bridge/config/nav2_hospital_rviz.yaml
exec /opt/venv/bin/python experiment/navigation-ui-demo/navigation_ui.py --host 0.0.0.0 --port 8765
