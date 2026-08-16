#!/bin/bash
source /opt/ros/jazzy/setup.bash
source /workspace/scene-graph-khronos/install/setup.bash
export PATH="/usr/bin:$PATH"
source /workspace/semantic_inference_venv/bin/activate

pkill -9 -f open_set_node 2>/dev/null
pkill -9 -f play_rosbag 2>/dev/null
pkill -9 -f khronos_node 2>/dev/null
pkill -9 -f hydra_visualizer 2>/dev/null
rm -f /tmp/khronos_node.*
sleep 3

ros2 launch khronos_ros uhumans2_khronos.launch.yaml use_gt_semantics:=false use_openset_semantics:=true dataset:=tesse_cd_office bag_dir:=/workspace/data start_visualizer:=false > /tmp/gpu_launch.log 2>&1 &

sleep 10
echo "=== T+10s ==="
nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv 2>/dev/null

sleep 20
echo "=== T+30s ==="
nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv 2>/dev/null

sleep 30
echo "=== T+60s ==="
nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv 2>/dev/null

sleep 30
echo "=== T+90s ==="
nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv 2>/dev/null

LATEST=$(ls -t /tmp/khronos_node.*INFO* 2>/dev/null | head -1)
echo "=== FRAME COUNT ==="
grep -c OBJ_DIAG $LATEST 2>/dev/null

ros2 service call /finish_mapping_and_save std_srvs/srv/Empty 2>&1 || true
sleep 3
pkill -9 -f ros2 2>/dev/null
