# Hospital P1000: natural language to Nav2 path planning

This package is the ROS 2/Nav2 endpoint for the Hospital P1000 planner. The
default software flow is:

`English instruction -> local LLM1 intent -> verified Hospital RAG grounding -> ordered candidate mission -> Nav2 path-only route selection`

The task-chain adapter is deterministic by default. The LLM2 chain generator
is retained for controlled comparison (`--task-chain-backend llm2`). A
successful `planned` result means Nav2 found feasible paths; it does not mean
the robot moved. The plan-only path does not send `NavigateToPose`, but it does
use `/map_server/load_map` and `/initialpose` while testing floor candidates
and restoring the configured start. Use a dedicated local simulation ROS
domain, never a real robot domain.

## Fresh WSL setup

Prerequisites are WSL Ubuntu 24.04, ROS 2 Jazzy, Ollama, and the local Hospital
P1000 DSG plus the 78-row reviewed semantic CSV. The 27 GB dataset and reviewed
CSV are local inputs and are intentionally not committed.

Install Python dependencies and build the ROS package:

```bash
cd /path/to/Masterthesis-wang
python3 -m venv .venv
.venv/bin/pip install -e 'llm-rag/planner[dev]'
source /opt/ros/jazzy/setup.bash
rosdep install --from-paths khronos-nav2-bridge/khronos_nav2_bridge --ignore-src -r -y
cd khronos-nav2-bridge
colcon build --packages-select khronos_nav2_bridge --symlink-install
source install/setup.bash
ollama pull gemma4:e2b-it-qat
```

For the complete English-only workflow, including the exact RAG rebuild
command, follow the repository-root [`README.md`](../../README.md). The Chroma
index is rebuildable and is not committed.

## Start Nav2 and request a path

In terminal 1, source the workspace build, use an isolated domain, then start
the packaged Khronos-only F0 map:

```bash
cd /path/to/Masterthesis-wang
source /opt/ros/jazzy/setup.bash
source khronos-nav2-bridge/install/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
export ROS_DOMAIN_ID=77
ros2 launch khronos_nav2_bridge hospital_nav2.launch.py \
  map:=khronos-nav2-bridge/khronos_nav2_bridge/generated/hospital-p1000-khronos-only/floor_0/map.yaml \
  params_file:=khronos-nav2-bridge/khronos_nav2_bridge/config/nav2_hospital_point.yaml
```

After Nav2 reports `Managed nodes are active`, run in terminal 2:

```bash
cd /path/to/Masterthesis-wang
export HOSPITAL_NAV2_PYTHON="$PWD/.venv/bin/python"
bash llm-rag/plan_hospital_nav2.sh \
  --instruction "Please take me to a room with a chair and a door." \
  --ros-domain-id 77 \
  --output /tmp/hospital-nav2-plan.json
```

The JSON records the model digest, instruction and code hashes, RAG build and
vector-content digests, map bundle hash, selected target candidates, path
geometry, and stage timings. It refuses to overwrite an existing output.
`planned` plus `execution_performed: false` is the expected plan-only success
condition. No waypoint execution or physical ANYmal dynamics are claimed.

## Map provenance

The four installed maps are the Khronos-only Place-support plus mesh-projection
maps. The map bundle and `generated/hospital-p1000-khronos-only/map_manifest.json`
pin the grid and file hashes. The geometry-only contract is
`experiment/map-generation/grid_geometry.json`; it records
floor extents, resolution and origin, not depth-map occupancy. The generator
also repairs only the two approved Place topology links. Rebuilding requires
the local P1000 DSG and mesh inputs; its ignored output directory is documented
in the map asset README.

Run the LLM/RAG and map-generator tests from the repository root, then run the
ROS package's checked-in Hospital contract suite:

```bash
.venv/bin/python -m pytest -q llm-rag/planner/tests \
  llm-rag/RAG/khronos_3dsg_rag/tests \
  experiment/map-generation/test_generate_khronos_only.py

source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
cd khronos-nav2-bridge
colcon test --packages-select khronos_nav2_bridge --event-handlers console_direct+
colcon test-result --verbose
```
