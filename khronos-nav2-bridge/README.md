# Hospital P1000 Nav2 bridge

This module contains the repository's Hospital/Nav2 ROS 2 package at `khronos_nav2_bridge/`. It consumes a mission JSON and floor bundle, then uses Nav2 to plan an ordered set of stops. For the integrated natural-language task-chain entry point, see the repository-root `README.md` and `llm-rag/plan_hospital_nav2.sh`.

## Dependencies and build

Dependencies: ROS 2 Jazzy, Nav2, RViz2, colcon, and rosdep. Nav2/ROS package dependencies are declared in `khronos_nav2_bridge/package.xml`; Python system packages are provided through ROS/rosdep. See `llm-rag/BUILD_ENVIRONMENT.md` for the full environment setup.

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
rosdep install --from-paths khronos-nav2-bridge/khronos_nav2_bridge --ignore-src -r -y
cd khronos-nav2-bridge
colcon build --packages-select khronos_nav2_bridge --symlink-install
source install/setup.bash
~~~

## Mission interface

The low-level runner takes a mission JSON, a floor-map bundle, and an output JSON path. `robot-model` must match the Nav2 profile that was launched. By default, execution mode sends goals to the Nav2 action server.

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
/usr/bin/python3 khronos-nav2-bridge/khronos_nav2_bridge/scripts/run_task_chain_nav2.py \
  --mission experiment/nav2-49/missions/HC-001.mission.json \
  --bundle khronos-nav2-bridge/khronos_nav2_bridge/config/hospital_p1000_floor_transitions.yaml \
  --output /tmp/HC-001-result.json \
  --plan-only \
  --robot-model point
~~~

`--plan-only` computes a route without sending `NavigateToPose` goals. Execution mode is restricted to an isolated simulation ROS domain and must never target a real robot domain. Runner output includes task status, stop order, map/path information, and the failure stage. Inspect the interface with:

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
/usr/bin/python3 khronos-nav2-bridge/khronos_nav2_bridge/scripts/run_task_chain_nav2.py --help
~~~

Map PNG/YAML files and their hashes are under `khronos_nav2_bridge/generated/hospital-p1000-khronos-only/`. Map-generation source is in `experiment/map-generation/`; the original Hospital DSG/mesh is not included. For detailed Hospital launch instructions, see `khronos_nav2_bridge/README_HOSPITAL.md`.
