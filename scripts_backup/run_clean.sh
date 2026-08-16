#!/bin/bash
source /opt/ros/jazzy/setup.bash
source /workspace/scene-graph-khronos/install/setup.bash
export PATH="/usr/bin:$PATH"
source /workspace/semantic_inference_venv/bin/activate

rm -f /tmp/khronos_node.*

# 1. Bag
ros2 bag play /workspace/data/tesse_cd_office -r 0.05 --clock --qos-profile-overrides-path /workspace/scene-graph-khronos/src/khronos/khronos_ros/config/qos_overrides.yaml > /tmp/bag_c.log 2>&1 &
sleep 3

# 2. OpenSet
ros2 run semantic_inference_ros open_set_node --ros-args -p config_path:=/workspace/scene-graph-khronos/src/semantic_inference/semantic_inference_ros/config/openset/fastsam-clip_vit14l.yaml --remap /color/image_raw:=/tesse/left_cam/rgb/image_raw > /tmp/open_c.log 2>&1 &
sleep 20

echo "=== GPU ==="
nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv

echo "=== TOPICS ==="
ros2 topic list 2>/dev/null | grep -E "semantic|rgb|depth|camera" | head -10

echo "=== TF CHECK ==="
timeout 3 ros2 topic echo /tf --once 2>&1 | head -3 || echo "no tf"

echo "=== OPEN SET DIAG ==="
grep "DIAG" /tmp/open_c.log 2>/dev/null | tail -3

pkill -9 -f ros2 2>/dev/null
