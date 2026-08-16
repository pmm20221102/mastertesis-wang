"""Launch khronos_anymald_rviz: graph_publisher + khronos_mesh_publisher + mock_robot_node + rviz2。

Khronos mesh 正式集成（不再依赖手工第二条命令）：
  - enable_mesh:=true  启动 khronos_mesh_publisher（延迟 one-shot，见节点内实现）
  - mesh_path:=<abs mesh.ply>   默认 <base_dir>/generated/mesh_downsampled.ply
  - mesh_delay:=2.0    mesh 延迟发布秒数（等 RViz 订阅）
  - start_delay:=N     mock robot 启动后延迟秒数（先静态确认 mesh/waypoint/edges 再动画）

用法:
  ros2 launch khronos_anymald_rviz mock_navigation.launch.py \
      base_dir:=<BRIDGE_ROOT> \
      task_plan:=<BRIDGE_ROOT>/demo/final_task_plan.json \
      enable_mesh:=true mesh_path:=<BRIDGE_ROOT>/generated/mesh_downsampled.ply \
      start_delay:=20.0 frame:=map
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    default_base = os.path.join(os.path.expanduser("~"),
                                "Masterthesis-wang", "khronos_anymald_bridge")
    pkg_share = get_package_share_directory("khronos_anymald_rviz")
    rviz_config = os.path.join(pkg_share, "config", "anymal_mock.rviz")
    default_mesh = os.path.join(default_base, "generated", "mesh_downsampled.ply")

    return LaunchDescription([
        DeclareLaunchArgument("base_dir", default_value=default_base,
                              description="khronos_anymald_bridge 目录（含 generated/）"),
        DeclareLaunchArgument("r", default_value="3.0"),
        DeclareLaunchArgument("ground_z", default_value="0.0"),
        DeclareLaunchArgument("frame", default_value="map",
                              description="frame_id（Khronos RViz 用 world 时设 world）"),
        DeclareLaunchArgument("start_room", default_value="0"),
        DeclareLaunchArgument("task_plan", default_value="",
                              description="LLM+RAG task plan json（最终 demo 必须用这个）"),
        DeclareLaunchArgument("goal_rooms", default_value="",
                              description="逗号分隔目标（诊断 fallback；最终用 task_plan）"),
        DeclareLaunchArgument("rate", default_value="10.0"),
        DeclareLaunchArgument("mock_speed", default_value="3.0"),
        DeclareLaunchArgument("checkpoint_pause", default_value="1.0",
                              description="到达 checkpoint 停留秒数"),
        DeclareLaunchArgument("publish_stretch", default_value="false",
                              description="发布 stretch 分级边（正式 demo 关闭）"),
        DeclareLaunchArgument("republish_period", default_value="0.0",
                              description="graph 静态图周期重发秒数（0=不重发；RViz late "
                                          "subscriber 收不到时设 3.0）"),
        DeclareLaunchArgument("rviz", default_value="true",
                              description="是否启动 RViz2（headless 也 true，配 Xvfb）"),
        # Khronos mesh 正式集成
        DeclareLaunchArgument("enable_mesh", default_value="true",
                              description="是否启动 khronos_mesh_publisher"),
        DeclareLaunchArgument("mesh_path", default_value=default_mesh,
                              description="mesh.ply 绝对路径（默认 base_dir/generated/mesh_downsampled.ply）"),
        DeclareLaunchArgument("mesh_delay", default_value="2.0",
                              description="mesh 延迟 one-shot 发布秒数（等 RViz 订阅）"),
        DeclareLaunchArgument("wireframe", default_value="true",
                              description="mesh 用 LINE_LIST 线框（推荐，OGRE 实心大 mesh 渲染失败）"),
        # mock robot 静态停留
        DeclareLaunchArgument("start_delay", default_value="0.0",
                              description="mock robot 启动后延迟秒数（先肉眼确认 mesh/waypoint/edges 再动画）"),

        Node(
            package="khronos_anymald_rviz",
            executable="graph_publisher",
            name="anymald_graph_publisher",
            output="screen",
            arguments=[
                "--base-dir", LaunchConfiguration("base_dir"),
                "--r", LaunchConfiguration("r"),
                "--ground-z", LaunchConfiguration("ground_z"),
                "--frame", LaunchConfiguration("frame"),
                "--publish-stretch", LaunchConfiguration("publish_stretch"),
                "--republish-period", LaunchConfiguration("republish_period"),
            ],
        ),
        Node(
            package="khronos_anymald_rviz",
            executable="khronos_mesh_publisher",
            name="khronos_mesh_publisher",
            output="screen",
            arguments=[
                "--mesh", LaunchConfiguration("mesh_path"),
                "--frame", LaunchConfiguration("frame"),
                "--delay", LaunchConfiguration("mesh_delay"),
                "--wireframe", LaunchConfiguration("wireframe"),
            ],
            condition=IfCondition(LaunchConfiguration("enable_mesh")),
        ),
        Node(
            package="khronos_anymald_rviz",
            executable="mock_robot_node",
            name="anymald_mock_robot",
            output="screen",
            arguments=[
                "--base-dir", LaunchConfiguration("base_dir"),
                "--r", LaunchConfiguration("r"),
                "--ground-z", LaunchConfiguration("ground_z"),
                "--frame", LaunchConfiguration("frame"),
                "--start-room", LaunchConfiguration("start_room"),
                "--task-plan", LaunchConfiguration("task_plan"),
                "--targets", LaunchConfiguration("goal_rooms"),
                "--rate", LaunchConfiguration("rate"),
                "--mock-speed", LaunchConfiguration("mock_speed"),
                "--checkpoint-pause", LaunchConfiguration("checkpoint_pause"),
                "--start-delay", LaunchConfiguration("start_delay"),
            ],
        ),
        Node(
            package="rviz2",
            executable="rviz2",
            name="rviz2",
            arguments=["-d", rviz_config],
            condition=IfCondition(LaunchConfiguration("rviz")),
        ),
    ])
