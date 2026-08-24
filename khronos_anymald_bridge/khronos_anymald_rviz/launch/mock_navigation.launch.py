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
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _robot_state_publisher(context):
    if LaunchConfiguration("enable_anymal").perform(context).lower() not in (
            "true", "1", "yes"):
        return []
    urdf_path = Path(LaunchConfiguration("anymal_urdf").perform(context))
    description_root = urdf_path.parent.parent
    description = urdf_path.read_text(encoding="utf-8").replace(
        "package://anymal_d_simple_description/", f"file://{description_root}/")
    return [Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        name="anymal_d_state_publisher",
        output="screen",
        parameters=[{"robot_description": description,
                     "publish_frequency": 30.0}],
    )]


def generate_launch_description():
    default_base = os.path.join(os.path.expanduser("~"),
                                "Masterthesis-wang", "khronos_anymald_bridge")
    pkg_share = get_package_share_directory("khronos_anymald_rviz")
    rviz_config = os.path.join(pkg_share, "config", "anymal_mock.rviz")
    default_mesh = os.path.join(default_base, "generated", "mesh_downsampled.ply")
    default_urdf = os.path.join(
        default_base, "external", "navigation_references",
        "anymal_d_simple_description", "urdf", "anymal.urdf")

    return LaunchDescription([
        DeclareLaunchArgument("base_dir", default_value=default_base,
                              description="khronos_anymald_bridge 目录（含 generated/）"),
        DeclareLaunchArgument("data_dir", default_value="",
                              description="waypoint JSON 目录；空值使用 base_dir/generated"),
        DeclareLaunchArgument("canonical_graph", default_value="",
                              description="Hospital canonical_graph_v2.json"),
        DeclareLaunchArgument("vertical_transitions", default_value="",
                              description="Hospital vertical_transitions_v2.json"),
        DeclareLaunchArgument("floor_model", default_value="",
                              description="Hospital floor_model_v2.json"),
        DeclareLaunchArgument("floor_spacing", default_value="0.0",
                              description="沿 X 展开各层的间距；0 保留原布局"),
        DeclareLaunchArgument("floor_columns", default_value="1",
                              description="楼层展开布局列数"),
        DeclareLaunchArgument("z_offset", default_value="0.0",
                              description="waypoint/route 相对 mesh 的显示高度"),
        DeclareLaunchArgument("reachable_from", default_value="-1",
                              description="仅显示该 waypoint 可达的导航图"),
        DeclareLaunchArgument("focus_floors", default_value="false",
                              description="仅显示当前楼层并随机器人切层"),
        DeclareLaunchArgument("initial_floor", default_value="0",
                              description="聚焦模式启动楼层"),
        DeclareLaunchArgument("native_z", default_value="false",
                              description="保留 JSON 原生三维 z（Hospital 必须 true）"),
        DeclareLaunchArgument("r", default_value="3.0"),
        DeclareLaunchArgument("ground_z", default_value="0.0"),
        DeclareLaunchArgument("frame", default_value="map",
                              description="frame_id（Khronos RViz 用 world 时设 world）"),
        DeclareLaunchArgument("start_room", default_value="0"),
        DeclareLaunchArgument("start_waypoint", default_value="-1",
                              description="显式起点 waypoint；-1 使用 start_room"),
        DeclareLaunchArgument("task_plan", default_value="",
                              description="LLM+RAG task plan json（最终 demo 必须用这个）"),
        DeclareLaunchArgument("goal_rooms", default_value="",
                              description="逗号分隔目标（诊断 fallback；最终用 task_plan）"),
        DeclareLaunchArgument("rate", default_value="10.0"),
        DeclareLaunchArgument("mock_speed", default_value="3.0"),
        DeclareLaunchArgument("demo_duration", default_value="0.0",
                              description="移动总时长；>0 覆盖 mock_speed"),
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
        DeclareLaunchArgument("max_mesh_edges", default_value="120000",
                              description="大 mesh 线框边数上限"),
        DeclareLaunchArgument("enable_anymal", default_value="true",
                              description="显示真实 ANYmal-D URDF/DAE RobotModel"),
        DeclareLaunchArgument("anymal_urdf", default_value=default_urdf),
        DeclareLaunchArgument("robot_frame", default_value="base"),
        DeclareLaunchArgument("robot_z_offset", default_value="0.0",
                              description="仅用于可视化的 ANYmal base 高度补偿"),
        DeclareLaunchArgument("show_marker_robot", default_value="false",
                              description="在 ANYmal 下显示宽视角定位光环"),
        DeclareLaunchArgument("camera_follow_yaw", default_value="true",
                              description="false 使用地图相对总览镜头"),
        DeclareLaunchArgument("rviz_config", default_value=rviz_config),
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
                "--data-dir", LaunchConfiguration("data_dir"),
                "--canonical-graph", LaunchConfiguration("canonical_graph"),
                "--vertical-transitions", LaunchConfiguration("vertical_transitions"),
                "--floor-model", LaunchConfiguration("floor_model"),
                "--floor-spacing", LaunchConfiguration("floor_spacing"),
                "--floor-columns", LaunchConfiguration("floor_columns"),
                "--z-offset", LaunchConfiguration("z_offset"),
                "--reachable-from", LaunchConfiguration("reachable_from"),
                "--focus-floors", LaunchConfiguration("focus_floors"),
                "--native-z", LaunchConfiguration("native_z"),
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
                "--max-edges", LaunchConfiguration("max_mesh_edges"),
                "--floor-model", LaunchConfiguration("floor_model"),
                "--floor-spacing", LaunchConfiguration("floor_spacing"),
                "--floor-columns", LaunchConfiguration("floor_columns"),
                "--focus-floors", LaunchConfiguration("focus_floors"),
                "--initial-floor", LaunchConfiguration("initial_floor"),
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
                "--data-dir", LaunchConfiguration("data_dir"),
                "--canonical-graph", LaunchConfiguration("canonical_graph"),
                "--vertical-transitions", LaunchConfiguration("vertical_transitions"),
                "--floor-model", LaunchConfiguration("floor_model"),
                "--floor-spacing", LaunchConfiguration("floor_spacing"),
                "--floor-columns", LaunchConfiguration("floor_columns"),
                "--z-offset", LaunchConfiguration("z_offset"),
                "--native-z", LaunchConfiguration("native_z"),
                "--r", LaunchConfiguration("r"),
                "--ground-z", LaunchConfiguration("ground_z"),
                "--frame", LaunchConfiguration("frame"),
                "--start-room", LaunchConfiguration("start_room"),
                "--start-waypoint", LaunchConfiguration("start_waypoint"),
                "--task-plan", LaunchConfiguration("task_plan"),
                "--targets", LaunchConfiguration("goal_rooms"),
                "--rate", LaunchConfiguration("rate"),
                "--mock-speed", LaunchConfiguration("mock_speed"),
                "--demo-duration", LaunchConfiguration("demo_duration"),
                "--checkpoint-pause", LaunchConfiguration("checkpoint_pause"),
                "--start-delay", LaunchConfiguration("start_delay"),
                "--anymal-urdf", LaunchConfiguration("anymal_urdf"),
                "--robot-frame", LaunchConfiguration("robot_frame"),
                "--robot-z-offset", LaunchConfiguration("robot_z_offset"),
                "--show-marker-robot", LaunchConfiguration("show_marker_robot"),
                "--camera-follow-yaw", LaunchConfiguration("camera_follow_yaw"),
                "--focus-floors", LaunchConfiguration("focus_floors"),
            ],
        ),
        OpaqueFunction(function=_robot_state_publisher),
        Node(
            package="rviz2",
            executable="rviz2",
            name="rviz2",
            arguments=["-d", LaunchConfiguration("rviz_config")],
            condition=IfCondition(LaunchConfiguration("rviz")),
        ),
    ])
