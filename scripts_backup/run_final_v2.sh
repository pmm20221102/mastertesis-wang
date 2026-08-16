#!/bin/bash
source /opt/ros/jazzy/setup.bash
source /workspace/scene-graph-khronos/install/setup.bash
export PATH="/usr/bin:$PATH"
source /workspace/semantic_inference_venv/bin/activate

pkill -9 -f open_set_node 2>/dev/null
pkill -9 -f khronos_node 2>/dev/null
pkill -9 -f play_rosbag 2>/dev/null
sleep 2
rm -f /tmp/khronos_node.*

nohup ros2 bag play /workspace/data/tesse_cd_office -r 0.05 --clock --qos-profile-overrides-path /workspace/scene-graph-khronos/src/khronos/khronos_ros/config/qos_overrides.yaml > /tmp/bag_v2.log 2>&1 &
sleep 3

nohup ros2 run semantic_inference_ros open_set_node --ros-args -p config_path:=/workspace/scene-graph-khronos/src/semantic_inference/semantic_inference_ros/config/openset/fastsam-clip_vit14l.yaml --remap /color/image_raw:=/tesse/left_cam/rgb/image_raw > /tmp/open_v2.log 2>&1 &
sleep 15

nohup ros2 run khronos_ros khronos_node --ros-args   --config-utilities-file /workspace/scene-graph-khronos/install/khronos_ros/share/khronos_ros/config/datasets/uHumans2_openset.yaml@input   --config-utilities-file /workspace/scene-graph-khronos/install/khronos_ros/share/khronos_ros/config/mapper/uHumans2_openset.yaml   --config-utilities-file /workspace/scene-graph-khronos/install/hydra/share/hydra/config/label_spaces/ade20k_indoor_label_space.yaml   --config-utilities-yaml {robot_id: 0, odom_frame: odom, robot_frame: base_link_gt, map_frame: map}   --config-utilities-yaml {glog_level: 0, glog_verbosity: 0}   --config-utilities-yaml {experiment: {output_dir: /home/nenlian/Masterthesis-wang/scene-graph-khronos/snapshots}}   --config-utilities-yaml {semantic_colormap_file: ""}   --config-utilities-yaml {backend: {fix_input_poses: true}}   --config-utilities-yaml {input: {inputs: {left_cam: {sensor: {min_range: 0.1, max_range: 100.0, extrinsics: {sensor_frame: left_cam}}}}}}   --config-utilities-yaml {sensor_frame: left_cam}   --remap ~/input/left_cam/depth_registered/image_rect:=/tesse/depth_cam/mono/image_raw   --remap ~/input/left_cam/rgb/image_raw:=/tesse/left_cam/rgb/image_raw   --remap ~/input/left_cam/rgb/camera_info:=/tesse/left_cam/camera_info   --remap ~/input/left_cam/semantic/image_raw:=/semantic/image_raw   > /tmp/khronos_v2.log 2>&1 &

sleep 90

LATEST=$(ls -t /tmp/khronos_node.*INFO* 2>/dev/null | head -1)
echo "FRAME_COUNT:$(grep -c OBJ_DIAG $LATEST 2>/dev/null)"
echo "FIRST:$(grep OBJ_DIAG $LATEST 2>/dev/null | head -1)"
echo "LAST:$(grep OBJ_DIAG $LATEST 2>/dev/null | tail -1)"

ros2 service call /finish_mapping_and_save std_srvs/srv/Empty 2>&1 || true
sleep 3
pkill -9 -f open_set_node 2>/dev/null
pkill -9 -f khronos_node 2>/dev/null
pkill -9 -f play_rosbag 2>/dev/null
