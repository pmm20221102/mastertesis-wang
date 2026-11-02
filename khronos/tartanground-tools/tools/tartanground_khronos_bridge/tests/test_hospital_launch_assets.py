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


def test_default_launch_and_rag_share_the_hospital_109_label_space():
    import yaml

    launch_text = LAUNCH_FILE.read_text(encoding="utf-8")
    assert "/config/tartanground_hospital_p1000_fine_label_space.yaml" in launch_text
    assert "/config/tartanground_hospital_p1000_fine_remap.yaml" in launch_text
    space_path = PACKAGE_ROOT / "config/tartanground_hospital_p1000_fine_label_space.yaml"
    space = yaml.safe_load(space_path.read_text(encoding="utf-8"))
    assert space["total_semantic_labels"] == 110
    labels = {row["label"]: row["name"].lower() for row in space["label_names"]}
    assert labels[1] == "door"
    assert labels[5] == "cabinet"
    assert labels[72] == "chair"
    rag_space = PACKAGE_ROOT.parents[3] / "llm-rag/RAG/khronos_3dsg_rag/config" / space_path.name
    assert space_path.read_bytes() == rag_space.read_bytes()
    assert "semantic_label_remap_filepath" not in space
    remap = yaml.safe_load((PACKAGE_ROOT / "config/tartanground_hospital_p1000_fine_remap.yaml").read_text())
    assert all(row["super_id"] in labels for row in remap)
    setup = SETUP_FILE.read_text(encoding="utf-8")
    assert space_path.name in setup
    assert "tartanground_hospital_p1000_fine_remap.yaml" in setup
