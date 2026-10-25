# TartanGround → Khronos ROS 2 input adapter

此 ROS package 位于 tools/tartanground_khronos_bridge/。它把本地 TartanGround 图片、深度、分割、位姿和传感器参数转成 Khronos 可消费的 ROS 2 bag；它不是导航 bridge。

## Dependencies

- ROS 2 Jazzy：rclpy、rosbag2_py、sensor_msgs、tf2_msgs、geometry_msgs、builtin_interfaces、cv_bridge。
- Python：numpy、opencv-python、scipy、PyYAML。
- 机器本地的 TartanGround 数据集、标签映射、pose 文件和相机配置。

ROS 依赖在 package.xml 中声明，Python 包在 setup.py 中声明；用 rosdep 安装 ROS 系统包，不要用 pip 安装 rclpy/ROS 消息包。

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

转换器输入输出路径由命令行参数和 launch/config 指定。输出是 ROS 2 bag 与审计报告；随后由 Khronos mapper 读取并生成 DSG。未提供原始 dataset 时，本仓库不能重建该 bag。
