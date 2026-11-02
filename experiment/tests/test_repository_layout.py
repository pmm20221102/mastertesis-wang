from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_repository_has_only_the_four_project_roots():
    expected = {"llm-rag", "khronos", "khronos-nav2-bridge", "experiment"}
    visible_dirs = {
        path.name for path in ROOT.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
    # Colcon creates an ignored root-level log/ directory even when the build
    # itself ran inside a package. That build artifact is not a project module.
    generated_colcon_dirs = {
        path.name for path in ROOT.iterdir()
        if path.is_dir() and (path / "COLCON_IGNORE").is_file()
    }
    actual = visible_dirs - generated_colcon_dirs
    assert actual == expected


def test_root_has_only_the_primary_readme_markdown():
    markdown = {path.name for path in ROOT.glob("*.md")}
    assert markdown == {"README.md"}


def test_paper_experiment_artifacts_are_present():
    taskchain = ROOT / "experiment" / "task-chain-60"
    nav2 = ROOT / "experiment" / "nav2-49"
    assert (taskchain / "public" / "tasks_en.jsonl").is_file()
    assert (taskchain / "runs" / "surface-v1-llm2-60x4-20261020" / "run_manifest.json").is_file()
    assert len(list((nav2 / "missions").glob("HC-*.mission.json"))) == 49
