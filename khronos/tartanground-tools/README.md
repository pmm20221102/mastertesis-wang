# TartanGround → Khronos ROS 2 input adapter

This ROS package is located at `tools/tartanground_khronos_bridge/`. It converts local TartanGround images, depth, segmentation, poses, and sensor parameters into a ROS 2 bag that Khronos can consume. It is not a navigation bridge.

## Dependencies

- ROS 2 Jazzy: `rclpy`, `rosbag2_py`, `sensor_msgs`, `tf2_msgs`, `geometry_msgs`, `builtin_interfaces`, and `cv_bridge`.
- Python: `numpy`, `opencv-python`, `scipy`, and `PyYAML`.
- Local TartanGround dataset, label mapping, pose files, and camera configuration.

ROS dependencies are declared in `package.xml`, and Python packages in `setup.py`. Use rosdep for ROS system packages; do not install `rclpy` or ROS message packages with pip.

~~~bash
source /opt/ros/jazzy/setup.bash
export PATH=/usr/bin:/opt/ros/jazzy/bin:/bin
rosdep install --from-paths khronos/tartanground-tools/tools/tartanground_khronos_bridge --ignore-src -r -y
cd khronos/tartanground-tools
colcon build --base-paths tools/tartanground_khronos_bridge
source install/setup.bash
~~~

## Entry points

~~~bash
ros2 run tartanground_khronos_bridge tartanground_to_ros2bag --help
ros2 run tartanground_khronos_bridge tartanground_audit_bag --help
~~~

Converter input/output paths are specified by command-line arguments and launch/configuration. Outputs are a ROS 2 bag and an audit report; the Khronos mapper then reads the bag and generates a DSG. Without the original dataset, this repository cannot recreate that bag.
