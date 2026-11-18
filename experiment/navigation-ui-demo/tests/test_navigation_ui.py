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
    progress = []
    result = planner.run_instruction(args, progress_callback=progress.append, interactive_selection=True,
        before_nav2_callback=lambda: order.append("start-nav2"))
    if pipeline_status != "valid":
        assert order == ["llm1-rag-llm2"]
    elif mission_status != "ready":
        assert order == ["llm1-rag-llm2", "mission"]
    else:
        assert order == ["llm1-rag-llm2", "mission", "start-nav2", "route"]
        assert result["status"] == "planned"
    assert result["task_chain_backend"] == "llm2"
    stages = [event["stage"] for event in progress]
    if pipeline_status == "valid" and mission_status != "ready":
        assert "mission_ready" not in stages
        assert "mission_not_ready" in stages


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
            Path(command[command.index("--output") + 1]).write_text(json.dumps({"status": "succeeded", "reached_stops": []}))
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


# Output contracts and retained route regressions. All external adapters are mocked.
def test_llm1_displays_recorded_compact_schema_with_required_nulls(ui, controller):
    payload = {"action": "navigate", "stops": [{"order": 1, "destination": "room", "objects": ["bed"], "floor_id": 0}], "clarification": None}
    controller._record_progress({"stage": "intent_schema_ready", "details": {"intent_schema": payload}})
    assert controller.state["stage_outputs"]["intent"] == payload
    controller._record_progress({"stage": "intent_parsed", "details": {"intent": {"action": "navigate", "steps": [{"target": {"text": "internal"}}]}}})
    assert controller.state["stage_outputs"]["intent"] == payload


def test_no_reverse_engineering_compact_intent_from_internal_task(ui, controller):
    controller._record_progress({"stage": "intent_parsed", "details": {"intent": {"action": "navigate", "steps": []}}})
    assert controller.state["stage_outputs"].get("intent") is None


def test_validated_task_chain_excludes_grounding_input_and_preserves_null(ui, controller):
    plan = {"status": "valid", "task_chain": [{"step": 1, "target_id": None, "candidate_node_ids": ["a", "b"], "selection_policy": "deferred_to_nav2"}]}
    controller._record_progress({"stage": "plan_ready", "details": {"status": "valid", "task_plan": plan, "grounded_task": {"secret_input": "not output"}}})
    assert controller.state["stage_outputs"]["task_chain"] == plan
    assert controller.state["stage_status"]["task_chain"]["status"] == "succeeded"


@pytest.mark.parametrize("stage,key", [("intent_parsing", "intent"), ("rag_grounding", "rag"), ("planning", "task_chain"), ("mission_preparation", "mission"), ("nav2_planning", "nav2")])
def test_pipeline_failure_marks_active_stage_and_blocks_downstream(ui, controller, stage, key):
    controller._record_progress({"stage": stage})
    controller._record_progress({"stage": "pipeline_stopped", "details": {"pipeline_result": {"status": "system_error", "error": "service unavailable"}}})
    assert controller.state["stage_status"][key]["status"] == "failed"
    assert controller.state["stage_status"]["execution"]["status"] == "not_run"


def test_mission_failure_keeps_readable_nested_reason(ui, controller):
    controller._record_progress({"stage": "mission_not_ready", "details": {"mission_result": {"status": "floor_unresolved", "unresolved_targets": [{"reason": "Destination floor is unknown"}]}}})
    assert controller.state["stage_status"]["mission"]["status"] == "failed"
    assert "floor" in controller.state["stage_status"]["mission"]["message"].lower()
    summary = ui.NavigationController._summarize_result({"status": "floor_unresolved", "mission_preparation": {"reason": "Destination floor is unknown"}}, "unused.json")
    assert summary["error"] == "Destination floor is unknown"


def test_summary_separates_plan_route_navigation(ui):
    result = {"status": "planned", "execution_performed": False, "pipeline_result": {"status": "valid", "validation_errors": []}, "mission_preparation": {"status": "ready", "mission": {"stops": [{}, {}]}}, "nav2_plan": {"status": "planned"}}
    final = ui.NavigationController._final_summary(result)
    assert final["plan"]["status"] == "succeeded"
    assert final["route"]["status"] == "succeeded"
    assert final["navigation"]["status"] == "not_run"


@pytest.mark.parametrize("code,status,expected", [(0, "succeeded", "succeeded"), (0, "failed", "failed"), (2, "succeeded", "failed"), (0, "running", "failed")])
def test_navigation_terminal_requires_successful_runner_and_result(ui, code, status, expected):
    final = ui.NavigationController._navigation_outcome({"status": status, "reached_stops": [0]}, code, 1)
    assert final["status"] == expected


def test_stop_cancels_active_navigation_but_keeps_route_and_final(ui, controller, monkeypatch):
    controller.demo_process = Mock(pid=123)
    controller.demo_process.poll.return_value = None
    cancel = Mock(return_value={"confirmed": True})
    monkeypatch.setattr(controller, "_cancel_navigation", cancel, raising=False)
    monkeypatch.setattr(controller, "_stop_process", Mock())
    route = {"session_id": "test-session", "segments": [{"floor_id": 0, "path": [{"x": 1, "y": 2}, {"x": 2, "y": 3}]}]}
    controller.state.update(route_snapshot=route, final_summary={"plan": {"status": "succeeded"}, "navigation": {"status": "running"}}, stage_outputs={"intent": {"action": "navigate"}})
    controller.stop()
    cancel.assert_called_once()
    assert controller.state["route_snapshot"] == route
    assert controller.state["final_summary"]["plan"]["status"] == "succeeded"
    assert controller.state["final_summary"]["navigation"]["status"] == "cancelled"
    assert controller.state["stage_outputs"] == {}
    controller._stop_process.assert_any_call("demo_process")


def test_clear_removes_route_and_summary_only_when_idle(ui, controller):
    controller.state.update(route_snapshot={"segments": [1]}, final_summary={"plan": {"status": "succeeded"}})
    controller.clear_display()
    assert controller.state["route_snapshot"] is None
    assert controller.state["final_summary"] == {}
    controller.job_lock.acquire()
    try:
        with pytest.raises(RuntimeError):
            controller.clear_display()
    finally:
        controller.job_lock.release()


def test_route_snapshot_keeps_selected_real_geometry_by_floor(ui):
    result = {"nav2_plan": {"route_selection": {"segment_results": [{"kind": "portal_entry", "floor_id": 0, "path": [{"x": 1, "y": 2}, {"x": 2, "y": 2}]}, {"kind": "floor_transition", "from_floor": 0, "to_floor": 1}, {"kind": "candidate_goal", "floor_id": 1, "path": [{"x": 3, "y": 4}, {"x": 5, "y": 4}]}]}}}
    route = ui.RouteHistory.from_plan(result, "session", "bundle").snapshot()
    assert [s["floor_id"] for s in route["segments"]] == [0, 1]
    assert route["segments"][0]["path"][0] == {"x": 1, "y": 2}
    assert route["execution_segments"] == []


def test_reason_summary_never_dumps_warning_traceback(ui):
    text = ui.NavigationController._friendly_error({"error": "Traceback (most recent call last):\n  File /private/test.py\nRuntimeError: timeout", "reason": "Navigation service is unavailable"})
    assert text == "Navigation service is unavailable"
    assert "Traceback" not in text


def test_unique_llm2_protocol_null_and_empty_candidates_are_retained(ui, controller):
    plan = {"status": "valid", "task_chain": [{"step": 1, "target_id": "room-1", "candidate_node_ids": [], "selection_policy": None, "constraints": {"avoid": [], "prefer": [], "conditions": []}}]}
    controller._record_progress({"stage": "plan_ready", "details": {"task_plan": plan}})
    assert controller.state["stage_outputs"]["task_chain"] == plan


def test_stop_never_claims_navigation_cancelled_without_confirmation(ui, controller, monkeypatch):
    controller.demo_process = Mock(pid=123)
    controller.demo_process.poll.return_value = None
    controller.nav2_process = None
    monkeypatch.setattr(controller, "_cancel_navigation", lambda: {"confirmed": False}, raising=False)
    monkeypatch.setattr(controller, "_stop_process", Mock())
    controller.state["final_summary"] = {"navigation": {"status": "running"}}
    controller.stop()
    assert controller.state["final_summary"]["navigation"]["status"] == "stop_unconfirmed"


def test_retained_overlay_filters_floor_without_joining_separate_segments(ui):
    snapshot = {"segments": [{"floor_id": 0, "path": [{"x": 0, "y": 0}, {"x": 1, "y": 0}]}, {"floor_id": 1, "path": [{"x": 9, "y": 9}]}, {"floor_id": 0, "path": [{"x": 4, "y": 4}, {"x": 5, "y": 4}]}]}
    segments = ui.RouteHistory.segments_on_floor(snapshot, 0)
    assert len(segments) == 2
    assert segments[0]["path"][-1] != segments[1]["path"][0]
    assert ui.RouteHistory.segments_on_floor(snapshot, None) == []


def test_recorded_preview_uses_actual_html_without_importing_live_controller():
    script = DEMO / "preview_recorded.py"
    assert script.exists(), "An isolated fixture preview is required"
    spec = importlib.util.spec_from_file_location("recorded_preview", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    html = module.build_preview_html({"status": "planned", "route_snapshot": {"segments": []}})
    assert "Fixture preview" in html
    assert "stage-intent" in html and "route-floors" in html
    assert "no navigation" in html.lower()


def test_compact_display_strips_undeclared_keys_without_losing_protocol_values(ui, controller):
    raw = {"action": "navigate", "stops": [{"order": 1, "destination": "room", "objects": [], "floor_id": 0, "debug": "hidden"}], "clarification": None, "model_metadata": {"tokens": 10}}
    controller._record_progress({"stage": "intent_schema_ready", "details": {"intent_schema": raw}})
    assert controller.state["stage_outputs"]["intent"] == {"action": "navigate", "stops": [{"order": 1, "destination": "room", "objects": [], "floor_id": 0}], "clarification": None}
    controller._record_progress({"stage": "intent_schema_ready", "details": {"intent_schema": {"stops": "not-a-compact-payload"}}})
    assert controller.state["stage_outputs"]["intent"] is None


@pytest.mark.parametrize("reached", [[0, 99], [1, 0], [0, 0]])
def test_navigation_terminal_matches_runner_zero_based_stop_sequence(ui, reached):
    assert ui.NavigationController._navigation_outcome({"status": "succeeded", "reached_stops": reached}, 0, 2)["status"] == "failed"


def test_stop_confirms_saved_owned_process_even_if_attribute_is_cleared(ui, controller, monkeypatch):
    owned = Mock()
    owned.poll.side_effect = lambda: 0 if getattr(owned, "did_exit", False) is True else None
    controller.nav2_process = owned
    controller.demo_process = Mock()
    controller.demo_process.poll.return_value = None
    controller.state["final_summary"] = {"navigation": {"status": "running"}}
    monkeypatch.setattr(controller, "_cancel_navigation", lambda: {"confirmed": False})
    def stop(name):
        if name == "nav2_process":
            owned.did_exit = True
            controller.nav2_process = None
    monkeypatch.setattr(controller, "_stop_process", stop)
    controller.stop()
    assert controller.state["final_summary"]["navigation"]["status"] == "cancelled"


def test_unicode_english_reason_is_not_discarded(ui):
    text = "The destination’s floor is unknown — choose another room."
    assert ui.NavigationController._friendly_error({"reason": text}) == text


def test_stopped_worker_cannot_launch_navigation(ui, controller, monkeypatch, valid_demo_result):
    controller.stop_requested = True
    controller.last_result = valid_demo_result
    launch = forbid_launch(ui, monkeypatch)
    controller.job_lock.acquire()
    controller._demo_worker()
    launch.assert_not_called()
    assert not controller.job_lock.locked()


def test_unconfirmed_stop_blocks_execution_restart(ui, controller):
    controller.state["status"] = "stop_unconfirmed"
    controller.last_result = {"status": "planned"}
    with pytest.raises(RuntimeError, match="stop"):
        controller.start_demo()


@pytest.mark.parametrize("status,expected", [("target_not_found", "not_run"), ("ambiguous", "needs_input"), ("unsupported", "not_run"), ("system_error", "not_run")])
def test_final_summary_does_not_call_upstream_boundary_a_validation_failure(ui, status, expected):
    result = {"status": status, "pipeline_result": {"status": status, "task_plan": None,
        "grounding": {"references": {"target": {"reason": "No matching destination in the scene graph."}}}},
        "mission_preparation": {"status": "not_attempted"},
        "nav2_plan": {"status": "not_attempted"}}
    final = ui.NavigationController._final_summary(result)
    assert final["plan"]["status"] == expected
    assert "validation" not in final["plan"]["message"].lower()
    assert final["route"]["status"] == "not_run"
    assert final["navigation"]["status"] == "not_run"
    if status == "target_not_found":
        assert "no matching destination" in final["plan"]["message"].lower()


def test_final_summary_reports_real_task_plan_validation_failure(ui):
    result = {"status": "system_error", "pipeline_result": {"status": "system_error",
        "task_plan": {"status": "valid", "task_chain": [{"step": 1}]},
        "validation_errors": ["Candidate set was not preserved."]},
        "mission_preparation": {"status": "not_attempted"}, "nav2_plan": {"status": "not_attempted"}}
    final = ui.NavigationController._final_summary(result)
    assert final["plan"]["status"] == "failed"
    assert "validation" in final["plan"]["message"].lower()
    assert final["route"]["status"] == "not_run"


def test_final_summary_distinguishes_mission_failure_from_unattempted_route(ui):
    result = {"status": "floor_unresolved", "pipeline_result": {"status": "valid",
        "task_plan": {"status": "valid", "task_chain": [{"step": 1}]}},
        "mission_preparation": {"status": "floor_unresolved",
            "unresolved_targets": [{"reason": "Destination floor cannot be resolved."}]},
        "nav2_plan": {"status": "not_attempted"}}
    final = ui.NavigationController._final_summary(result)
    assert final["plan"]["status"] == "succeeded"
    assert final["route"]["status"] == "not_run"
    assert "Destination floor cannot be resolved." in final["route"]["message"]
    assert final["navigation"]["status"] == "not_run"


def test_final_summary_only_calls_attempted_nav2_search_a_route_failure(ui):
    result = {"status": "no_feasible_candidate_route", "pipeline_result": {"status": "valid",
        "task_plan": {"status": "valid", "task_chain": [{"step": 1}]}},
        "mission_preparation": {"status": "ready"},
        "nav2_plan": {"status": "no_feasible_candidate_route", "route_selection": {
            "rejected_sequences": [{"reason": "No connected path to the selected room."}]}}}
    final = ui.NavigationController._final_summary(result)
    assert final["plan"]["status"] == "succeeded"
    assert final["route"]["status"] == "failed"
    assert "No connected path" in final["route"]["message"]
    assert final["navigation"]["status"] == "not_run"


# Independent review: effective mission races, ownership and session isolation.
def test_major_stopped_valid_mission_cannot_start_visualization_or_runner(ui, controller, monkeypatch, valid_demo_result):
    controller.last_result = valid_demo_result
    controller.stop_requested = True
    visual = Mock()
    monkeypatch.setattr(controller, "_start_visualization", visual)
    launch = forbid_launch(ui, monkeypatch)
    controller.job_lock.acquire()
    controller._demo_worker()
    visual.assert_not_called()
    launch.assert_not_called()
    assert not controller.job_lock.locked()


def test_major_stop_during_env_preparation_prevents_runner_popen(ui, controller, monkeypatch, valid_demo_result):
    controller.last_result = valid_demo_result
    monkeypatch.setattr(controller, "_start_visualization", lambda: None)
    def env():
        controller.stop()
        return {}
    monkeypatch.setattr(controller, "_ros_env", env)
    launch = forbid_launch(ui, monkeypatch)
    controller.job_lock.acquire()
    controller._demo_worker()
    launch.assert_not_called()


def test_major_stop_during_slow_nav2_probe_prevents_popen(ui, controller, monkeypatch):
    def probe_then_stop(*args, **kwargs):
        controller.stop()
        return probe("Node not found", 1)
    monkeypatch.setattr(ui.subprocess, "run", probe_then_stop)
    launch = forbid_launch(ui, monkeypatch)
    with pytest.raises(RuntimeError, match="stopped"):
        controller._start_nav2()
    launch.assert_not_called()


def test_major_dead_owned_handle_does_not_confirm_external_stop(ui, controller, monkeypatch):
    old = Mock()
    old.poll.return_value = 0
    controller.nav2_process = old
    controller.demo_process = Mock()
    controller.demo_process.poll.return_value = None
    controller.state["final_summary"] = {"navigation": {"status": "running"}}
    monkeypatch.setattr(controller, "_cancel_navigation", lambda: {"confirmed": False})
    monkeypatch.setattr(controller, "_stop_process", Mock())
    controller.stop()
    assert controller.state["status"] == "stop_unconfirmed"
    assert controller.state["final_summary"]["navigation"]["status"] == "stop_unconfirmed"


def test_major_external_stack_reuse_discards_dead_ownership(ui, controller, monkeypatch):
    dead = Mock()
    dead.poll.return_value = 0
    controller.nav2_process = dead
    map_path = controller.bundle["floors"]["0"]["map_yaml_path"]
    monkeypatch.setattr(ui.subprocess, "run", Mock(side_effect=[probe("active [3]"), probe("String value is: " + map_path)]))
    forbid_launch(ui, monkeypatch)
    controller._start_nav2()
    assert controller.nav2_process is None


def test_major_new_session_clears_old_success_and_route_before_worker(ui, controller, monkeypatch):
    controller.state.update(route_snapshot={"session_id": "old", "segments": [1]},
        stage_status={"execution": {"status": "succeeded"}}, final_summary={"navigation": {"status": "succeeded"}},
        active_stage="execution")
    monkeypatch.setattr(ui.threading, "Thread", Mock())
    controller.start_plan("New navigation request")
    assert controller.state["route_snapshot"] is None
    assert controller.state["stage_status"] == {}
    assert controller.state["final_summary"] == {}
    assert controller.state.get("active_stage") is None
    assert controller.state["session_id"] != "old"
    ui.plan_nav2.run_instruction.side_effect = RuntimeError("Upstream is unavailable")
    controller._plan_worker("New navigation request", None)
    assert controller.state["route_snapshot"] is None
    assert controller.state["final_summary"]["navigation"]["status"] == "not_run"


def test_major_selection_continues_same_session_without_clearing(ui, controller, monkeypatch):
    controller.state.update(route_snapshot={"session_id": "test-session", "segments": []},
        stage_status={"intent": {"status": "succeeded"}})
    monkeypatch.setattr(ui.threading, "Thread", Mock())
    controller.start_plan("same instruction", {"step_0_target": "room"})
    assert controller.state["session_id"] == "test-session"
    assert controller.state["stage_status"]["intent"]["status"] == "succeeded"
    controller.job_lock.release()


def test_major_missing_goal_ownership_never_calls_cancel_transport(ui, controller, monkeypatch):
    controller.demo_process = Mock(pid=123)
    controller.demo_process.poll.return_value = 0
    transport = Mock(side_effect=AssertionError("No UUID ownership; no cancellation request"))
    monkeypatch.setattr(ui.subprocess, "run", transport)
    assert controller._cancel_navigation()["confirmed"] is False
    transport.assert_not_called()


def test_major_spawn_registration_and_stop_are_serialized(ui, controller, monkeypatch):
    from threading import Event, Thread
    entered, release, stopped = Event(), Event(), Event()
    owned = Mock(pid=123)
    alive = [True]
    owned.poll.side_effect = lambda: None if alive[0] else 0
    def popen(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return owned
    monkeypatch.setattr(ui.subprocess, "Popen", popen)
    def stop_process(name):
        if name == "nav2_process" and controller.nav2_process is owned:
            alive[0] = False
    monkeypatch.setattr(controller, "_stop_process", stop_process)
    launch_thread = Thread(target=lambda: controller._spawn_owned("nav2_process", ["fake"]))
    def stop():
        controller.stop()
        stopped.set()
    stop_thread = Thread(target=stop)
    launch_thread.start()
    assert entered.wait(2)
    stop_thread.start()
    assert not stopped.wait(.05)
    release.set()
    launch_thread.join(2); stop_thread.join(2)
    assert stopped.is_set() and not alive[0]
    assert controller.stop_requested


def test_major_new_task_cannot_reset_cancellation_while_stop_is_in_flight(ui, controller, monkeypatch):
    from threading import Event, Thread
    entered, release = Event(), Event()
    controller.demo_process = Mock(pid=123)
    controller.demo_process.poll.return_value = None
    monkeypatch.setattr(controller, "_stop_process", Mock())
    def cancel():
        entered.set()
        assert release.wait(2)
        return {"confirmed": False}
    monkeypatch.setattr(controller, "_cancel_navigation", cancel)
    stopping = Thread(target=controller.stop)
    monkeypatch.setattr(ui.threading, "Thread", Mock())
    stopping.start()
    assert entered.wait(2)
    try:
        with pytest.raises(RuntimeError, match="[Ss]top"):
            controller.start_plan("must wait")
        assert controller.stop_requested
    finally:
        release.set(); stopping.join(2)


def test_major_actual_cancel_transport_uses_verified_current_ledger_only(ui, controller, monkeypatch, tmp_path):
    own = "12345678123456781234567812345678"
    controller.demo_process = Mock(pid=123)
    controller.demo_process.poll.return_value = 0
    controller._demo_execution_id = "current"
    controller._demo_goal_ledger = tmp_path / "goals.json"
    data = {"execution_id": "current", "client_pid": 123, "action_name": "/navigate_to_pose", "goal_ids": [own]}
    controller._demo_goal_ledger.write_text(json.dumps(data))
    transport = Mock(return_value=subprocess.CompletedProcess([], 0, '{"confirmed":true}', ""))
    monkeypatch.setattr(ui.subprocess, "run", transport)
    assert controller._cancel_navigation()["confirmed"] is True
    command = transport.call_args.args[0]
    assert command[-2:] == ["--goal-id", own]
    transport.reset_mock()
    for key, value in [("execution_id", "old"), ("client_pid", 999), ("goal_ids", ["0" * 32])]:
        bad = {**data, key: value}
        controller._demo_goal_ledger.write_text(json.dumps(bad))
        assert controller._cancel_navigation()["confirmed"] is False
    transport.assert_not_called()


@pytest.fixture
def valid_demo_result():
    from candidate_route_planner import CANDIDATE_MISSION_SCHEMA, build_resolved_mission
    candidate = {"target_id": "room-1", "floor_id": 0,
                 "floor_assignment_source": "fixture-floor-assignment",
                 "navigation_target": {"frame_id": "map", "position": [1.0, 1.0, 0.0]}}
    mission = {"schema_version": CANDIDATE_MISSION_SCHEMA, "task_id": "race",
               "map_bundle_id": "fixture-bundle", "initial_floor": 0,
               "initial_pose": {"x": 1.0, "y": 1.0, "yaw": 0.0},
               "stops": [{"stop_index": 0, "candidate_targets": [candidate]}]}
    # Validate this nonempty mission with the real pure mission contract.
    assert build_resolved_mission(mission, [candidate])["stops"][0]["candidate_targets"] == [candidate]
    return {"status": "planned", "mission_preparation": {"status": "ready", "mission": mission}}
