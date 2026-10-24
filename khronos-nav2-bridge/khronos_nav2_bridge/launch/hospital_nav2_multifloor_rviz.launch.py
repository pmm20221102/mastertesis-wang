from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


HC031_F0_DEMO = "experiments/hospital-four-floor-rviz-demo-20261023/missions/HC-031-F0-demo.mission.json"
KHRONOS_DSG = "experiments/hospital-khronos-only-20261019/output/hospital-p1000-khronos-only-generated/dsg_topology_repaired.json"


def generate_launch_description():
    package_share = FindPackageShare("khronos_nav2_bridge")
    map_file = LaunchConfiguration("map")
    params_file = LaunchConfiguration("params_file")
    start_x = LaunchConfiguration("start_x")
    start_y = LaunchConfiguration("start_y")
    start_yaw = LaunchConfiguration("start_yaw")
    bundle_file = LaunchConfiguration("bundle_file")
    mission_file = LaunchConfiguration("mission_file")
    dsg_file = LaunchConfiguration("dsg_file")
    rviz_config = LaunchConfiguration("rviz_config")

    nav2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([package_share, "launch", "hospital_nav2.launch.py"])
        ),
        launch_arguments={
            "map": map_file,
            "params_file": params_file,
            "start_x": start_x,
            "start_y": start_y,
            "start_yaw": start_yaw,
        }.items(),
    )
    mosaic = Node(
        package="khronos_nav2_bridge",
        executable="hospital_floor_mosaic.py",
        name="hospital_floor_mosaic",
        output="screen",
        parameters=[{"bundle_path": bundle_file, "mission_path": mission_file,
                     "dsg_path": dsg_file}],
    )
    rviz = Node(
        package="rviz2",
        executable="rviz2",
        name="hospital_multifloor_rviz",
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
                    [package_share, "config", "nav2_hospital_rviz.yaml"]
                ),
            ),
            DeclareLaunchArgument(
                "bundle_file",
                default_value=PathJoinSubstitution(
                    [package_share, "config", "hospital_p1000_floor_transitions.yaml"]
                ),
            ),
            DeclareLaunchArgument("start_x", default_value="14.85"),
            DeclareLaunchArgument("start_y", default_value="3.65"),
            DeclareLaunchArgument("start_yaw", default_value="0.0"),
            DeclareLaunchArgument("mission_file", default_value=HC031_F0_DEMO),
            DeclareLaunchArgument("dsg_file", default_value=KHRONOS_DSG),
            DeclareLaunchArgument(
                "rviz_config",
                default_value=PathJoinSubstitution(
                    [package_share, "rviz", "hospital_nav2_multifloor.rviz"]
                ),
            ),
            nav2,
            mosaic,
            rviz,
        ]
    )
