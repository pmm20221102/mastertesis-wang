import sys
import io
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent.parent / "experiment" / "navigation-ui-demo"))

from navigation_ui import (  # noqa: E402
    DEFAULT_PARAMS,
    NavigationController,
    build_candidate_groups,
    rag_review_badge,
)


def test_candidate_groups_show_graph_evidence_for_each_ambiguous_room():
    pipeline = {
        "intent": {"steps": [{"target": {"text": "room with a bed"}}]},
        "grounding": {"references": {
            "step_0_target": {
                "status": "ambiguous",
                "candidate_node_ids": ["room-a", "room-b"],
            },
        }},
    }
    graph = {"nodes": {
        "room-a": {"node_type": "room", "label": "unknown", "position": [1, 2, 2.2]},
        "room-b": {"node_type": "room", "label": "unknown", "position": [8, 9, 5.7]},
        "bed-a": {"node_type": "object", "label": "bed", "room_id": "room-a"},
        "chair-b": {"node_type": "object", "label": "chair", "room_id": "room-b"},
    }}

    groups = build_candidate_groups(pipeline, graph)

    assert len(groups) == 1
    assert groups[0]["reference_key"] == "step_0_target"
    assert groups[0]["reference_text"] == "room with a bed"
    options = {option["node_id"]: option for option in groups[0]["options"]}
    assert options["room-a"]["floor_id"] == 1
    assert "bed" in options["room-a"]["evidence"]
    assert options["room-b"]["floor_id"] == 2
    assert "chair" in options["room-b"]["evidence"]


def test_candidate_groups_omit_resolved_and_not_found_references():
    pipeline = {"grounding": {"references": {
        "target": {"status": "resolved", "candidate_node_ids": ["r1"]},
        "destination": {"status": "not_found", "candidate_node_ids": []},
    }}}

    assert build_candidate_groups(pipeline, {"nodes": {}}) == []


def test_rag_review_badge_distinguishes_human_reviewed_and_unreviewed_indexes():
    assert rag_review_badge(None) == "No human-reviewed semantic labels applied"
    assert rag_review_badge("a" * 64) == "Human-reviewed semantic labels applied · aaaaaaaaaaaa"


def test_ui_defaults_to_the_half_meter_per_second_rviz_profile():
    assert DEFAULT_PARAMS.name == "nav2_hospital_rviz.yaml"


def test_progress_callback_stores_stage_output_and_progress_message():
    controller = NavigationController.__new__(NavigationController)
    controller.state_lock = threading.RLock()
    controller.state = {"events": [], "stage_outputs": {}}

    controller._record_progress({
        "stage": "intent_parsed", "message": "Intent analysis complete.",
        "details": {"intent": {"action": "navigate", "target": {"text": "chair"}}},
    })

    assert controller.state["stage_outputs"]["intent"]["target"]["text"] == "chair"
    assert controller.state["events"][-1]["message"] == "Intent analysis complete."


def test_terminal_warnings_are_not_added_to_the_user_progress_timeline():
    controller = NavigationController.__new__(NavigationController)
    controller.state_lock = threading.RLock()
    controller.state = {"events": []}
    process = SimpleNamespace(stdout=io.StringIO(
        "[WARN] middleware warning\n[ERROR] child process warning\n"))

    controller._read_logs(process, "nav2")

    assert controller.state["events"] == []
