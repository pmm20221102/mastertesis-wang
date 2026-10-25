from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_repository_has_only_the_four_project_roots():
    expected = {"llm-rag", "khronos", "khronos-nav2-bridge", "experiment"}
    actual = {path.name for path in ROOT.iterdir() if path.is_dir() and not path.name.startswith(".")}
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
