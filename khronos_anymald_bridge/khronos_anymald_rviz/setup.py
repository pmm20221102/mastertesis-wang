from setuptools import setup
import os
from glob import glob

package_name = "khronos_anymald_rviz"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
        (os.path.join("share", package_name, "config"), glob("config/*.rviz")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="nenlian",
    maintainer_email="wang@kit.edu",
    description=(
        "Khronos -> ANYmal D waypoint graph RViz2 visualization and mock navigation "
        "(jazzy, headless-testable)"
    ),
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "graph_publisher = khronos_anymald_rviz.graph_publisher:main",
            "mock_robot_node = khronos_anymald_rviz.mock_robot_node:main",
            "khronos_mesh_publisher = khronos_anymald_rviz.khronos_mesh_publisher:main",
        ],
    },
)
