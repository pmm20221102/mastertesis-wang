from setuptools import find_packages, setup

package_name = "tartanground_khronos_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config",
         ["config/tartanground_input.yaml",
          "config/tartanground_hospital_label_space.yaml",
          "config/tartanground_hospital_remap.yaml",
          "config/mapper_voxel010_fixed.yaml"]),
        ("share/" + package_name + "/launch",
         ["launch/tartanground_hospital_khronos.launch.yaml"]),
    ],
    install_requires=[
        "numpy",
        "opencv-python",
        "scipy",
        "PyYAML",
        "rclpy",
        "rosbag2_py",
        "sensor_msgs",
        "tf2_msgs",
        "geometry_msgs",
        "builtin_interfaces",
        "cv_bridge",
    ],
    entry_points={
        "console_scripts": [
            "tartanground_to_ros2bag = tartanground_khronos_bridge.convert_to_bag:main",
            "tartanground_audit_bag = tartanground_khronos_bridge.audit_bag:main",
        ],
    },
    zip_safe=True,
)
