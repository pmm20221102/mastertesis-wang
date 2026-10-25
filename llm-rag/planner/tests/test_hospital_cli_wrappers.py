import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]


def test_plan_cli_wrapper_accepts_an_explicit_python_interpreter(tmp_path):
    capture = tmp_path / "python-args.json"
    fake_python = tmp_path / "python-fake"
    fake_python.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "json.dump(sys.argv[1:], open(os.environ['CAPTURE_ARGS'], 'w'))\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    environment = os.environ.copy()
    environment["HOSPITAL_NAV2_PYTHON"] = str(fake_python)
    environment["CAPTURE_ARGS"] = str(capture)

    completed = subprocess.run(
        ["bash", str(ROOT / "llm-rag/plan_hospital_nav2.sh"), "--help"],
        env=environment, capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 0, completed.stderr
    arguments = json.loads(capture.read_text(encoding="utf-8"))
    assert arguments[0].endswith("llm-rag/planner/plan_nav2.py")
    assert arguments[1] == "--help"


def test_plan_cli_wrapper_uses_repository_venv_when_local_deployment_is_absent(tmp_path):
    workspace = tmp_path / "repo"
    scripts = workspace / "llm-rag"
    scripts.mkdir(parents=True)
    wrapper = scripts / "plan_hospital_nav2.sh"
    shutil.copyfile(ROOT / "llm-rag/plan_hospital_nav2.sh", wrapper)
    venv_bin = workspace / ".venv/bin"
    venv_bin.mkdir(parents=True)
    capture = tmp_path / "venv-args.json"
    fake_python = venv_bin / "python"
    fake_python.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "json.dump(sys.argv[1:], open(os.environ['CAPTURE_ARGS'], 'w'))\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    environment = os.environ.copy()
    environment.pop("HOSPITAL_NAV2_PYTHON", None)
    environment.pop("MT_PYTHON", None)
    environment["CAPTURE_ARGS"] = str(capture)

    completed = subprocess.run(
        ["bash", str(wrapper), "--help"], env=environment,
        capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 0, completed.stderr
    arguments = json.loads(capture.read_text(encoding="utf-8"))
    assert arguments[0].endswith("llm-rag/planner/plan_nav2.py")
    assert arguments[1] == "--help"


def test_plan_only_wrapper_runs_without_private_local_deployment(tmp_path):
    workspace = tmp_path / "repo"
    script_dir = workspace / "khronos-nav2-bridge/khronos_nav2_bridge/scripts"
    script_dir.mkdir(parents=True)
    source = (ROOT / "khronos-nav2-bridge/khronos_nav2_bridge/scripts/run_plan_only.sh")
    shutil.copyfile(source, script_dir / "run_plan_only.sh")
    capture = tmp_path / "runner-args.json"
    child = script_dir / "run_task_chain_nav2.py"
    child.write_text(
        "import json, os, sys\n"
        "json.dump(sys.argv[1:], open(os.environ['CAPTURE_ARGS'], 'w'))\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["CAPTURE_ARGS"] = str(capture)

    completed = subprocess.run(
        ["bash", str(script_dir / "run_plan_only.sh"),
         "mission.json", "bundle.yaml", "result.json", "point"],
        env=environment, capture_output=True, text=True, check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(capture.read_text(encoding="utf-8")) == [
        "--mission", "mission.json", "--bundle", "bundle.yaml", "--output",
        "result.json", "--robot-model", "point", "--plan-only",
    ]
