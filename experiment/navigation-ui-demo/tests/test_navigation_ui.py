"""Offline regressions: no ROS, model calls, or database access."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import Mock

import pytest

DEMO = Path(__file__).resolve().parents[1]


@pytest.fixture
def ui(monkeypatch):
    # The planner is an external service boundary for controller unit tests.
    planner = types.ModuleType("plan_nav2")
    planner.run_instruction = Mock()
    monkeypatch.setitem(sys.modules, "plan_nav2", planner)
    spec = importlib.util.spec_from_file_location("demo_navigation_ui", DEMO / "navigation_ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def controller(ui, monkeypatch, tmp_path):
    monkeypatch.setattr(ui.NavigationController, "_load_inputs", lambda self: None)
    monkeypatch.setenv("HOSPITAL_UI_OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("HOSPITAL_TASK_CHAIN_BACKEND", raising=False)
    c = ui.NavigationController()
    c.defaults = {"model": "test-model", "initial_state": {"pose": {"x": 1}}}
    c.bundle = {"floors": {"0": {"map_yaml_path": str(tmp_path / "floor0.yaml")}}}
    c.rag_db = tmp_path / "vector_db"
    c.defaults_path = tmp_path / "defaults.yaml"
    c.state["session_id"] = "test-session"
    return c


def probe(stdout="", code=0, stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def forbid_launch(ui, monkeypatch):
    launch = Mock(side_effect=AssertionError("must not launch a competing stack"))
    monkeypatch.setattr(ui.subprocess, "Popen", launch)
    return launch


@pytest.mark.parametrize("code,stdout,stderr", [(1, "", "Node not found"), (0, "Node not found", "")])
def test_cold_start_launches_and_waits_for_exact_active(ui, controller, monkeypatch, code, stdout, stderr):
    run = Mock(side_effect=[probe(stdout, code, stderr), probe("inactive [2]"), probe("active [3]")])
    process = Mock(pid=123, stdout=[])
    process.poll.return_value = None
    launch = Mock(return_value=process)
    monkeypatch.setattr(ui.subprocess, "run", run)
    monkeypatch.setattr(ui.subprocess, "Popen", launch)
    monkeypatch.setattr(ui.time, "sleep", lambda _: None)
    controller._start_nav2()
    assert run.call_count == 3  # inactive must not finish the wait
    assert launch.call_count == 1
    assert "map:=" + controller.bundle["floors"]["0"]["map_yaml_path"] in launch.call_args.args[0]
    assert controller.nav2_process is process


@pytest.mark.parametrize("state", ["inactive [2]", "unconfigured [1]", "finalized [4]"])
def test_existing_nonactive_stack_is_left_untouched(ui, controller, monkeypatch, state):
    run = Mock(return_value=probe(state))
    monkeypatch.setattr(ui.subprocess, "run", run)
    launch = forbid_launch(ui, monkeypatch)
    with pytest.raises(RuntimeError, match="not active"):
        controller._start_nav2()
    assert run.call_count == 1  # no map probe for inactive
    launch.assert_not_called()


@pytest.mark.parametrize("same_map", [True, False])
def test_active_map_reuse_or_refusal(ui, controller, monkeypatch, same_map):
    map_path = controller.bundle["floors"]["0"]["map_yaml_path"] if same_map else "/other/map.yaml"
    monkeypatch.setattr(ui.subprocess, "run", Mock(side_effect=[probe("active [3]"), probe("String value is: " + map_path)]))
    launch = forbid_launch(ui, monkeypatch)
    if same_map:
        controller._start_nav2()
        assert controller.nav2_process is None  # external process is never owned
        kill = Mock()
        monkeypatch.setattr(ui.os, "killpg", kill)
        controller.stop()
        kill.assert_not_called()
    else:
        with pytest.raises(RuntimeError, match="different map"):
            controller._start_nav2()
    launch.assert_not_called()


@pytest.mark.parametrize("response", [probe("active [3]", 1, "transport failed"), probe("unknown [99]"), probe("", 2, "daemon not found"), probe("", 1, "Node not found: transport failed"), subprocess.TimeoutExpired("ros2", 20), FileNotFoundError("ros2")])
def test_query_failures_do_not_launch(ui, controller, monkeypatch, response):
    run = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
    monkeypatch.setattr(ui.subprocess, "run", run)
    launch = forbid_launch(ui, monkeypatch)
    with pytest.raises(RuntimeError):
        controller._start_nav2()
    launch.assert_not_called()


@pytest.mark.parametrize("response", [probe("", 1, "Node not found"), probe("wrong output"), subprocess.TimeoutExpired("ros2", 20)])
def test_active_map_query_errors_leave_external_stack_untouched(ui, controller, monkeypatch, response):
    monkeypatch.setattr(ui.subprocess, "run", Mock(side_effect=[probe("active [3]"), response]))
    launch = forbid_launch(ui, monkeypatch)
    with pytest.raises(RuntimeError, match="map"):
        controller._start_nav2()
    assert controller.nav2_process is None
    launch.assert_not_called()


def test_default_backend_is_llm2_and_visible(ui, controller):
    assert controller._runtime_args("go").task_chain_backend == "llm2"
    controller.refresh_rag_profiles = lambda: 0
    assert controller.snapshot()["task_chain_backend"] == "llm2"
    assert "task_chain_backend" in ui.HTML


@pytest.mark.parametrize("backend", ["llm2", "grounded_deterministic"])
def test_backend_override_and_worker_callback(ui, controller, monkeypatch, backend):
    monkeypatch.setenv("HOSPITAL_TASK_CHAIN_BACKEND", backend)
    monkeypatch.setattr(ui.NavigationController, "_load_inputs", lambda self: None)
    c = ui.NavigationController()
    c.defaults = controller.defaults
    c.state["session_id"] = "worker"
    c.job_lock.acquire()
    def run(args, **kwargs):
        assert args.task_chain_backend == backend
        # ROS must only be started by the planner's post-mission hook.
        start.assert_not_called()
        kwargs["before_nav2_callback"]()
        return {"status": "planned", "pipeline_result": {}, "task_chain_backend": backend}
    start = Mock()
    c._start_nav2 = start
    ui.plan_nav2.run_instruction.side_effect = run
    c._plan_worker("go", None)
    start.assert_called_once()
    assert c.state["status"] == "planned"
    assert c.state["result"]["task_chain_backend"] == backend
    assert not c.job_lock.locked()


def test_invalid_backend_fails_before_inputs(ui, monkeypatch, tmp_path):
    monkeypatch.setenv("HOSPITAL_TASK_CHAIN_BACKEND", "typo")
    load = Mock()
    monkeypatch.setattr(ui.NavigationController, "_load_inputs", load)
    with pytest.raises(ValueError, match="HOSPITAL_TASK_CHAIN_BACKEND"):
        ui.NavigationController()
    load.assert_not_called()


@pytest.mark.parametrize("status", ["ambiguous", "target_not_found", "unsupported", "system_error"])
def test_early_pipeline_exit_never_starts_ros(ui, controller, monkeypatch, status):
    launch = forbid_launch(ui, monkeypatch)
    ui.plan_nav2.run_instruction.return_value = {"status": status, "pipeline_result": {}}
    controller.job_lock.acquire()
    controller._plan_worker("go", None)
    launch.assert_not_called()
    assert controller.state["status"] == status
    assert not controller.job_lock.locked()


@pytest.mark.parametrize("state", ["active [2]", "inactive [3]", "not active [3]", "active [3] extra"])
def test_malformed_lifecycle_state_never_authorizes_launch(ui, controller, monkeypatch, state):
    monkeypatch.setattr(ui.subprocess, "run", Mock(return_value=probe(state)))
    launch = forbid_launch(ui, monkeypatch)
    with pytest.raises(RuntimeError):
        controller._start_nav2()
    launch.assert_not_called()


def test_stop_only_signals_owned_process_groups(ui, controller, monkeypatch):
    owned = Mock(pid=123)
    owned.poll.return_value = None
    controller.nav2_process = owned
    kill = Mock()
    monkeypatch.setattr(ui.os, "killpg", kill)
    controller.stop()
    kill.assert_called_once_with(123, ui.signal.SIGINT)
    assert controller.state["status"] == "stopped"


@pytest.mark.parametrize("pipeline_status,mission_status", [("valid", "ready"), ("ambiguous", "ready"), ("valid", "invalid")])
def test_real_planner_orchestration_defers_ros_until_valid_mission(monkeypatch, tmp_path, pipeline_status, mission_status):
    # Exercise the real run_instruction orchestration. Replace only external
    # model/RAG/ROS adapters, so this test cannot load a DB or contact a service.
    root = DEMO.parents[1]
    for name, members in {
        "main_rag": {"load_config": Mock(return_value={}), "run_structured_task_planner": Mock()},
        "main": {"_validate_build_manifest": Mock()},
        "prepare_instruction_mission": {"build_candidate_mission": Mock()},
    }.items():
        module = types.ModuleType(name)
        for key, value in members.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location("demo_plan_contract", root / "llm-rag/planner/plan_nav2.py")
    planner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(planner)
    (tmp_path / "bundle.yaml").write_text("map_bundle_id: test")
    (tmp_path / "defaults.yaml").write_text("unused")
    (tmp_path / "canonical_graph_v2.json").write_text("{}")
    order = []
    info = dict.fromkeys(["build_id", "manifest_sha256", "source_dsg_sha256", "semantic_review_csv_sha256", "canonical_graph_sha256"], "test")
    monkeypatch.setattr(planner, "verify_rag_build", Mock(return_value=info))
    monkeypatch.setattr(planner, "ollama_model_digest", Mock(return_value="digest"))
    monkeypatch.setattr(planner, "_load_defaults", Mock(return_value={"initial_state": {}, "floor_z_tolerance_m": .25}))
    monkeypatch.setattr(planner, "_prompt_source_hashes", Mock(return_value={}))
    def pipeline(*args, **kwargs):
        assert kwargs["task_chain_backend"] == "llm2"
        order.append("llm1-rag-llm2")
        return {"status": pipeline_status}
    planner.run_structured_task_planner.side_effect = pipeline
    def mission(*args, **kwargs):
        order.append("mission")
        return {"status": mission_status, "mission": {}}
    planner.build_candidate_mission.side_effect = mission
    def ros(*args):
        order.append("route")
        return 0, {"status": "planned", "execution_performed": False}, ""
    monkeypatch.setattr(planner, "_run_ros_plan", Mock(side_effect=ros))
    args = types.SimpleNamespace(bundle=str(tmp_path / "bundle.yaml"), defaults=str(tmp_path / "defaults.yaml"),
        db_dir=str(tmp_path / "vector_db"), model="test", floor_tolerance_m=None, instruction="go",
        intent_prompt_profile="hospital_taskchain_surface_v1", num_ctx=4096, num_predict=512,
        request_timeout_s=360, task_chain_backend="llm2", robot_model="point", ros_domain_id=79)
    result = planner.run_instruction(args, interactive_selection=True,
        before_nav2_callback=lambda: order.append("start-nav2"))
    if pipeline_status != "valid":
        assert order == ["llm1-rag-llm2"]
    elif mission_status != "ready":
        assert order == ["llm1-rag-llm2", "mission"]
    else:
        assert order == ["llm1-rag-llm2", "mission", "start-nav2", "route"]
        assert result["status"] == "planned"
    assert result["task_chain_backend"] == "llm2"


@pytest.mark.parametrize("status", [None, "ambiguous", "nav2_plan_failed"])
def test_rviz_requires_successful_route(ui, controller, monkeypatch, status):
    controller.last_result = {"status": status} if status else None
    launch = forbid_launch(ui, monkeypatch)
    with pytest.raises(RuntimeError, match="only after a route"):
        controller.start_demo()
    launch.assert_not_called()


def test_successful_route_can_start_rviz_then_mission_runner(ui, controller, monkeypatch):
    controller.last_result = {"status": "planned", "mission_preparation": {"mission": {"stops": []}}}
    commands = []
    def launch(command, **kwargs):
        commands.append(command)
        process = Mock(stdout=[])
        process.poll.return_value = 0
        process.wait.return_value = 0
        if command[0] == "/usr/bin/python3" and "--output" in command:
            Path(command[command.index("--output") + 1]).write_text(json.dumps({"status": "completed"}))
        return process
    monkeypatch.setattr(ui.subprocess, "Popen", launch)
    controller.job_lock.acquire()
    controller._demo_worker()
    assert [command[0] for command in commands] == ["/usr/bin/python3", "rviz2", "/usr/bin/python3"]
    assert commands[0][1].endswith("map_labels.py")
    assert "--plan-only" not in commands[1]
    assert controller.state["status"] == "demo_complete"
    assert not controller.job_lock.locked()


def test_child_diagnostics_are_saved_without_polluting_progress(ui, controller):
    import io
    process = types.SimpleNamespace(stdout=io.StringIO("WARN: diagnostic detail\n"))
    before = list(controller.state.get("events", []))
    controller._read_logs(process, "nav2")
    log = controller.output_dir / "test-session-nav2.log"
    assert "WARN: diagnostic detail" in log.read_text()
    assert controller.state.get("events", []) == before


def test_nav2_failure_is_exposed_as_a_clean_result_error(ui):
    result = {"status": "nav2_plan_failed", "nav2_plan": {"error": "No feasible route"}}
    assert ui.NavigationController._summarize_result(result, "result.json")["error"] == "No feasible route"


def test_clarification_without_candidates_does_not_claim_multiple_destinations(ui):
    result = {"status": "ambiguous", "pipeline_result": {
        "intent": {"action": "ask_clarification", "description": "where is healthmonitor"},
        "error": "where is healthmonitor"}}
    message = ui.NavigationController._result_message(result)
    assert "LLM1 requested clarification" in message
    assert "where is healthmonitor" in message
    assert "Plan Route" in message
    assert "Multiple destinations" not in message


def test_unresolved_destination_without_candidates_requests_more_detail(ui):
    message = ui.NavigationController._result_message({"status": "ambiguous"})
    assert "more specific description" in message
    assert "selection is required" not in message
