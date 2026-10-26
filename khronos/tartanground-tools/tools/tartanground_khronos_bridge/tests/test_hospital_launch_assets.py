from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LAUNCH_FILE = PACKAGE_ROOT / "launch" / "tartanground_hospital_khronos.launch.yaml"
SETUP_FILE = PACKAGE_ROOT / "setup.py"


def test_hospital_launch_does_not_depend_on_the_original_home_directory():
    launch_text = LAUNCH_FILE.read_text(encoding="utf-8")

    assert "/home/ulmmt/Masterthesis-wang" not in launch_text
    assert "$(find-pkg-share tartanground_khronos_bridge)" in launch_text
    assert "$(find-pkg-share khronos_ros)" in launch_text


def test_package_installs_every_config_referenced_by_hospital_launch():
    setup_text = SETUP_FILE.read_text(encoding="utf-8")

    for config_name in (
        "tartanground_input.yaml",
        "tartanground_hospital_label_space.yaml",
        "tartanground_hospital_remap.yaml",
        "mapper_voxel010_fixed.yaml",
    ):
        assert config_name in setup_text
