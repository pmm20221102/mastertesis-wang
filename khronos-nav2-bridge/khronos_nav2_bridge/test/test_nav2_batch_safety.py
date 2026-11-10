"""Offline provenance and goal-lifecycle regressions; no ROS/model execution."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
BATCH_DIR = ROOT / "experiment/task-chain-60"
sys.path.insert(0, str(BATCH_DIR))
sys.path.insert(0, str(ROOT / "khronos-nav2-bridge/khronos_nav2_bridge/scripts"))
import run_nav2_batch as batch
import run_task_chain_nav2 as runner


@pytest.fixture
def source():
    return dict(rag_build_id="build", rag_manifest_sha256="m",
                canonical_graph_sha256="g", source_dsg_sha256="d",
                semantic_review_csv_sha256=None, rag_vector_content_sha256="v",
                map_bundle_id="map", map_bundle_sha256="b", start_profile_sha256="s")


def test_matching_current_prediction(source):
    batch.validate_prediction_sources([{"task_id": "one", "provenance": source}], source, "sqlite")


@pytest.mark.parametrize("key", [
    "rag_build_id", "rag_manifest_sha256", "canonical_graph_sha256",
    "source_dsg_sha256", "semantic_review_csv_sha256", "rag_vector_content_sha256",
    "map_bundle_id", "map_bundle_sha256", "start_profile_sha256",
])
def test_current_prediction_mismatch_rejected(source, key):
    prediction = {**source, key: "other"}
    with pytest.raises(ValueError, match=key):
        batch.validate_prediction_sources([{"task_id": "one", "provenance": prediction}], source, "sqlite")


@pytest.mark.parametrize("provenance", [None, {}, {"graph_sha256": "g"},
    {"graph_sha256": "g", "database_sha256": "wrong", "raw_dsg_sha256": "d"}])
def test_unknown_or_incomplete_legacy_prediction_rejected(source, provenance):
    with pytest.raises(ValueError):
        batch.validate_prediction_sources([{"task_id": "one", "provenance": provenance}], source, "sqlite")


def test_legacy_prediction_requires_exact_graph_database_and_dsg(source):
    provenance = {"graph_sha256": "g", "database_sha256": "sqlite", "raw_dsg_sha256": "d"}
    batch.validate_prediction_sources([{"task_id": "old", "provenance": provenance}], source, "sqlite")
    with pytest.raises(ValueError, match="graph_sha256"):
        batch.validate_prediction_sources([{"task_id": "old", "provenance": {**provenance, "graph_sha256": "x"}}], source, "sqlite")


def test_partial_current_provenance_cannot_fall_back_to_legacy(source):
    provenance = {"graph_sha256": "g", "database_sha256": "sqlite", "raw_dsg_sha256": "d",
                  "rag_build_id": "wrong"}
    with pytest.raises(ValueError):
        batch.validate_prediction_sources([{"task_id": "old", "provenance": provenance}], source, "sqlite")


class Handle:
    def __init__(self):
        self.calls = []
        self.result_future = object()
        self.cancel_future = object()

    def get_result_async(self):
        self.calls.append("result")
        return self.result_future

    def cancel_goal_async(self):
        self.calls.append("cancel")
        return self.cancel_future


@pytest.mark.parametrize("terminal", [4, 5, 6])
def test_timeout_cancels_and_waits_for_terminal_result(terminal):
    handle = Handle()
    calls = []
    def wait(future, timeout, label):
        calls.append(label)
        if len(calls) == 1:
            raise TimeoutError("navigation timed out")
        if future is handle.cancel_future:
            return SimpleNamespace(return_code=0, goals_canceling=[object()])
        assert handle.calls == ["result", "cancel"]
        return SimpleNamespace(status=terminal)
    result = runner.wait_for_navigation_result(handle, wait, 0.1)
    assert result["status"] == "failed"
    assert result["batch_safe"] is True
    assert result["action_status"] == terminal
    assert len(calls) == 3


@pytest.mark.parametrize("failure", ["cancel_timeout", "cancel_rejected", "result_timeout", "nonterminal", "cancel_exception"])
def test_unconfirmed_cancellation_is_not_batch_safe(failure):
    handle = Handle()
    count = 0
    def wait(future, timeout, label):
        nonlocal count
        count += 1
        if count == 1:
            raise TimeoutError("navigation timed out")
        if future is handle.cancel_future:
            if failure == "cancel_exception":
                raise RuntimeError("cancel service failed")
            if failure == "cancel_timeout":
                raise TimeoutError("cancel timed out")
            return SimpleNamespace(return_code=1 if failure == "cancel_rejected" else 0)
        if failure != "nonterminal":
            raise TimeoutError("terminal result unavailable")
        return SimpleNamespace(status=3)
    result = runner.wait_for_navigation_result(handle, wait, 0.1)
    assert result["status"] == "failed"
    assert result["batch_safe"] is False
    assert "cancel" in handle.calls


def test_success_does_not_cancel():
    handle = Handle()
    result = runner.wait_for_navigation_result(handle, lambda *args: SimpleNamespace(status=4), 1)
    assert result["status"] == "succeeded"
    assert result["batch_safe"] is True
    assert handle.calls == ["result"]


@pytest.mark.parametrize("result", [None, {}, {"status": "running"},
    {"status": "failed", "batch_safe": False}, {"status": "failed", "batch_safe": "true"}])
def test_batch_refuses_missing_or_unsafe_runner_confirmation(result):
    assert batch.runner_allows_next_task(result) is False


def test_batch_can_continue_after_confirmed_terminal_failure():
    assert batch.runner_allows_next_task({"status": "failed", "batch_safe": True})


@pytest.mark.parametrize("payload", [None, "broken", {"status": "failed", "batch_safe": False},
                                     {"status": "failed", "batch_safe": True}])
def test_batch_stops_before_next_submission_unless_goal_confirmed_terminal(tmp_path, monkeypatch, source, payload):
    tasks = tmp_path / "tasks.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    tasks.write_text('{"task_id":"one"}\n{"task_id":"two"}\n')
    predictions.write_text("".join(json.dumps({"task_id": task_id, "repeat": 1,
        "result": {}, "provenance": source}) + "\n" for task_id in ("one", "two")))
    output = tmp_path / "batch"
    monkeypatch.setenv("ROS_DOMAIN_ID", "77")
    monkeypatch.setattr(sys, "argv", ["batch", str(predictions), "--tasks", str(tasks),
                                    "--output-dir", str(output)])
    monkeypatch.setattr(batch, "load_batch_sources", lambda *args: (
        {"map_bundle_id": "map"}, {"initial_state": {}, "floor_z_tolerance_m": 0.25,
                                  "robot_model": "point"}, {}, source, "sqlite"))
    monkeypatch.setattr(batch, "build_candidate_mission",
                        lambda *args, **kwargs: {"status": "ready", "mission": {}})
    submitted = []
    def run(command, **kwargs):
        submitted.append(command)
        result_path = Path(command[command.index("--output") + 1])
        if isinstance(payload, dict):
            result_path.write_text(json.dumps(payload))
        elif payload is not None:
            result_path.write_text(payload)
        return SimpleNamespace(returncode=2)
    monkeypatch.setattr(batch.subprocess, "run", run)
    safe = isinstance(payload, dict) and payload.get("batch_safe") is True
    assert batch.main() == (0 if safe else 2)
    summary = json.loads((output / "summary.json").read_text())
    assert len(submitted) == (2 if safe else 1)
    assert summary["batch_stopped"] is not safe
    assert summary["unprocessed_task_ids"] == ([] if safe else ["two"])
    assert summary["provenance"] == source


def test_batch_rejects_wrong_provenance_before_creating_output(tmp_path, monkeypatch, source):
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text('{"task_id":"one"}\n')
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text(json.dumps({"task_id": "one", "repeat": 1, "provenance": {}}) + "\n")
    output = tmp_path / "batch"
    monkeypatch.setattr(sys, "argv", ["batch", str(predictions), "--tasks", str(tasks),
                                    "--output-dir", str(output), "--prepare-only"])
    monkeypatch.setattr(batch, "load_batch_sources", lambda *args: ({}, {}, {}, source, "sqlite"))
    with pytest.raises(SystemExit) as error:
        batch.main()
    assert error.value.code == 2
    assert not output.exists()


def test_load_sources_reuses_plan_nav2_checks_and_rejects_graph_mismatch(tmp_path, monkeypatch):
    import types
    module = types.ModuleType("plan_nav2")
    calls = []
    bundle_path = tmp_path / "bundle.yaml"
    bundle_path.write_text("map_bundle_id: map")
    graph = tmp_path / "graph.json"
    graph.write_text("{}")
    db = tmp_path / "vector_db"
    db.mkdir()
    defaults = {"rag_source": {"dsg_sha256": "d"}}
    module._load_defaults = lambda path, bundle: (calls.append(("defaults", path, bundle)) or defaults)
    module._sha256_file = lambda path: "actual"
    def verify(database, *, expected_source):
        calls.append(("rag", database, expected_source))
        return {"canonical_graph_sha256": "wrong"}
    module.verify_rag_build = verify
    monkeypatch.setitem(sys.modules, "plan_nav2", module)
    import floor_transition_core
    monkeypatch.setattr(floor_transition_core, "validate_transition_bundle",
                        lambda bundle, **kw: bundle)
    with pytest.raises(ValueError, match="graph"):
        batch.load_batch_sources(db, bundle_path, tmp_path / "defaults", graph)
    assert calls[0][0] == "defaults"
    assert calls[1] == ("rag", db, defaults["rag_source"])


@pytest.mark.parametrize("key", [
    "rag_build_id", "rag_manifest_sha256", "canonical_graph_sha256",
    "source_dsg_sha256", "semantic_review_csv_sha256", "rag_vector_content_sha256",
    "map_bundle_id", "map_bundle_sha256", "start_profile_sha256",
])
def test_missing_current_field_is_rejected_even_when_expected_null(source, key):
    provenance = {name: value for name, value in source.items() if name != key}
    with pytest.raises(ValueError, match=key):
        batch.validate_prediction_sources([{"task_id": "one", "provenance": provenance}], source, "sqlite")


@pytest.mark.parametrize("key", ["graph_sha256", "database_sha256", "raw_dsg_sha256"])
def test_current_record_rejects_contradictory_legacy_alias(source, key):
    with pytest.raises(ValueError, match=key):
        batch.validate_prediction_sources([{"task_id": "one",
            "provenance": {**source, key: "wrong"}}], source, "sqlite")


def test_cancel_rejection_racing_with_terminal_result_is_safe():
    handle = Handle()
    calls = 0
    def wait(future, timeout, label):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("result timeout")
        if future is handle.cancel_future:
            return SimpleNamespace(return_code=1)
        return SimpleNamespace(status=4)
    result = runner.wait_for_navigation_result(handle, wait, 1)
    assert result["status"] == "failed"
    assert result["batch_safe"] is True
    assert result["cancel_return_code"] == 1


@pytest.mark.parametrize("mode", ["terminal", "unconfirmed", "acceptance_timeout", "rejected"])
def test_real_navigation_method_tracks_safety_without_ros(mode):
    # Compile the actual nested method with injected ROS message/client doubles.
    import ast
    tree = ast.parse(Path(runner.__file__).read_text())
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "navigate_to_pose")
    namespace = dict(wait_for_navigation_result=runner.wait_for_navigation_result,
                     normalize_nav2_pose=runner.normalize_nav2_pose,
                     bundle={"frame_id": "map"}, action_timeout=1,
                     NavigateToPose=SimpleNamespace(Goal=SimpleNamespace))
    exec(compile(ast.Module(body=[method], type_ignores=[]), runner.__file__, "exec"), namespace)
    handle = Handle()
    handle.accepted = mode != "rejected"
    acceptance = object()
    calls = []
    def wait(future, timeout, label):
        calls.append(label)
        if future is acceptance:
            if mode == "acceptance_timeout":
                raise TimeoutError("acceptance timed out")
            return handle
        if future is handle.cancel_future:
            return SimpleNamespace(return_code=0)
        if calls.count("NavigateToPose result"):
            if "terminal result" in label and mode == "terminal":
                return SimpleNamespace(status=5)
            raise TimeoutError("result timed out")
        raise AssertionError(label)
    node = SimpleNamespace(
        nav_client=SimpleNamespace(wait_for_server=lambda **kwargs: True,
                                   send_goal_async=lambda goal: acceptance),
        _pose=lambda value: (value, object()), _wait=wait, batch_safe=True,
        _current_pose=lambda: {"x": 0, "y": 0, "yaw": 0})
    if mode == "acceptance_timeout":
        with pytest.raises(TimeoutError):
            namespace["navigate_to_pose"](node, {})
    else:
        result = namespace["navigate_to_pose"](node, {})
        assert result["status"] == "failed"
    assert node.batch_safe is (mode in ("terminal", "rejected"))
    if mode in ("terminal", "unconfirmed"):
        assert handle.calls == ["result", "cancel"]


def test_readme_fresh_predictions_provenance_flows_into_batch(tmp_path, monkeypatch, source):
    """Execute the documented Python snippet; replace only inference and inputs."""
    readme = (ROOT / "README.md").read_text()
    start = readme.index("    .venv/bin/python - <<'PY'")
    end = readme.index("    PY", start)
    code = "\n".join(line[4:] if line.startswith("    ") else line
                     for line in readme[start:end].splitlines()[1:])
    tasks = tmp_path / "experiment/task-chain-60/public/tasks_en.jsonl"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("".join(json.dumps({"task_id": task_id, "instruction": "Go to a room."})
                             + "\n" for task_id in ("one", "two")))
    output = tmp_path / "fresh"
    output.mkdir()
    db = tmp_path / "rag/vector_db"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_DIR", str(output))
    monkeypatch.setenv("RAG_DB", str(db))
    loads = []
    def load(db_dir, bundle_path, defaults_path, graph_path=None):
        loads.append((db_dir, bundle_path, defaults_path))
        assert db_dir == db
        return ({"map_bundle_id": "map"},
                {"initial_state": {}, "floor_z_tolerance_m": 0.25, "robot_model": "point"},
                {}, source, "sqlite")
    monkeypatch.setattr(batch, "load_batch_sources", load)
    calls = []
    def inference(command, **kwargs):
        calls.append(command)
        assert command[command.index("--db-dir") + 1] == str(db)
        assert kwargs["check"] is True
        Path(command[command.index("--output-json") + 1]).write_text('{"status":"valid"}')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(batch.subprocess, "run", inference)
    original_path = sys.path[:]
    try:
        exec(compile(code, str(ROOT / "README.md"), "exec"), {})
    finally:
        sys.path[:] = original_path
    predictions = output / "predictions.jsonl"
    rows = [json.loads(line) for line in predictions.read_text().splitlines()]
    assert len(rows) == len(calls) == 2
    assert all(row["provenance"] == source and row["repeat"] == 1 for row in rows)
    assert all(row["result"] == {"status": "valid"} for row in rows)
    batch.validate_prediction_sources(rows, source, "sqlite")
    monkeypatch.setattr(batch, "build_candidate_mission",
                        lambda *args, **kwargs: {"status": "ready", "mission": {}})
    profile = tmp_path / "experiment/navigation-ui-demo/nav2-profile"
    monkeypatch.setattr(sys, "argv", ["batch", str(predictions), "--db-dir", str(db),
        "--tasks", str(tasks), "--bundle", str(profile / "hospital_floor_transitions.yaml"),
        "--defaults", str(profile / "hospital_nav2_plan_defaults.yaml"),
        "--output-dir", str(output / "prepared"), "--prepare-only"])
    assert batch.main() == 0
    summary = json.loads((output / "prepared/summary.json").read_text())
    assert summary["provenance"] == source
    assert summary["preparation_counts"] == {"ready": 2}
    assert summary["submitted"] == 0
    assert len(loads) == 2
