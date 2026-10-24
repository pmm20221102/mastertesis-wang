from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare
from launch.substitutions import Command, LaunchConfiguration, PathJoinSubstitution

def generate_launch_description():
    p=LaunchConfiguration("params_file")
    default_params=PathJoinSubstitution([FindPackageShare("khronos_nav2_bridge"),"config","nav2_hospital.yaml"])
    x=PathJoinSubstitution([FindPackageShare("khronos_nav2_bridge"),"urdf","anymal_c_nav2.urdf.xacro"])
    nodes=[("nav2_map_server","map_server"),("nav2_planner","planner_server"),("nav2_controller","controller_server"),("nav2_behaviors","behavior_server"),("nav2_bt_navigator","bt_navigator"),("nav2_waypoint_follower","waypoint_follower")]
    map_file=LaunchConfiguration("map"); start_x=LaunchConfiguration("start_x"); start_y=LaunchConfiguration("start_y"); start_yaw=LaunchConfiguration("start_yaw")
    actions=[Node(package=package,executable=exe,name=exe,output="screen",parameters=[p,{"yaml_filename":map_file}] if exe=="map_server" else [p]) for package,exe in nodes]
    actions += [Node(package="nav2_lifecycle_manager",executable="lifecycle_manager",name="lifecycle_manager_navigation",output="screen",parameters=[p]),Node(package="robot_state_publisher",executable="robot_state_publisher",name="robot_state_publisher",output="screen",parameters=[{"robot_description":ParameterValue(Command(["xacro ",x]),value_type=str),"use_sim_time":False}]),ExecuteProcess(cmd=["/usr/bin/python3",PathJoinSubstitution([FindPackageShare("khronos_nav2_bridge"),"..","..","lib","khronos_nav2_bridge","kinematic_pose_source.py"]),"--ros-args","-r","__node:=kinematic_pose_source","-p",["initial_x:=",start_x],"-p",["initial_y:=",start_y],"-p",["initial_yaw:=",start_yaw]],name="kinematic_pose_source",output="screen",additional_env={"PYTHONPATH":"/opt/ros/jazzy/lib/python3.12/site-packages:/usr/lib/python3/dist-packages","PATH":"/usr/bin:/opt/ros/jazzy/bin:/bin"})]
    default_map=PathJoinSubstitution([FindPackageShare("khronos_nav2_bridge"),"generated","hospital-p1000-khronos-only","floor_0","map.yaml"])
    return LaunchDescription([DeclareLaunchArgument("map", default_value=default_map),DeclareLaunchArgument("params_file",default_value=default_params),DeclareLaunchArgument("start_x",default_value="0.0"),DeclareLaunchArgument("start_y",default_value="0.0"),DeclareLaunchArgument("start_yaw",default_value="0.0")]+actions)
