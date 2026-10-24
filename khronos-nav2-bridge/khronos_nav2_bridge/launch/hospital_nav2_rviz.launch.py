from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    package_share = FindPackageShare("khronos_nav2_bridge")
    map_file = LaunchConfiguration("map")
    params_file = LaunchConfiguration("params_file")
    start_x = LaunchConfiguration("start_x")
    start_y = LaunchConfiguration("start_y")
    start_yaw = LaunchConfiguration("start_yaw")
    rviz_config = LaunchConfiguration("rviz_config")

    hospital_nav2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution(
                [package_share, "launch", "hospital_nav2.launch.py"]
            )
        ),
        launch_arguments={
            "map": map_file,
            "params_file": params_file,
            "start_x": start_x,
            "start_y": start_y,
            "start_yaw": start_yaw,
        }.items(),
    )

    rviz = Node(
        package="rviz2",
        executable="rviz2",
        name="hospital_manual_rviz",
        output="screen",
        arguments=["-d", rviz_config],
        parameters=[{"base_frame": "base_link"}],
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "map",
                default_value=PathJoinSubstitution(
                    [package_share, "generated", "hospital-p1000-khronos-only",
                     "floor_0", "map.yaml"]
                ),
            ),
            DeclareLaunchArgument(
                "params_file",
                default_value=PathJoinSubstitution(
                    [package_share, "config", "nav2_hospital_point.yaml"]
                ),
            ),
            DeclareLaunchArgument("start_x", default_value="14.85"),
            DeclareLaunchArgument("start_y", default_value="3.65"),
            DeclareLaunchArgument("start_yaw", default_value="0.0"),
            DeclareLaunchArgument(
                "rviz_config",
                default_value=PathJoinSubstitution(
                    [package_share, "rviz", "hospital_nav2_manual.rviz"]
                ),
            ),
            hospital_nav2,
            rviz,
        ]
    )
