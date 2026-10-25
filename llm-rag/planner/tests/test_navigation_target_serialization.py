from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main_rag  # noqa: E402


class FakeRag:
    def get_navigation_target(self, node_id):
        return SimpleNamespace(
            navigation_node_id=f"place-{node_id}",
            position=[1.0, 2.0, 2.3],
            frame_id="map",
            goal_type="place",
        )


def candidate(node_id):
    return SimpleNamespace(
        node_id=node_id,
        node_type="object",
        label="bed",
        room_id="room-1",
        place_id=f"place-{node_id}",
        position=[1.0, 2.0, 2.3],
    )


def resolved_set_reference():
    selected = [candidate("object-1"), candidate("object-2")]
    return SimpleNamespace(status="resolved_set", selected=None,
                           selected_many=selected, candidates=selected)


def test_resolved_set_serializes_each_candidate_navigation_anchor():
    target = SimpleNamespace(text="all beds")
    intent = SimpleNamespace(action="navigate", description="visit all beds",
                             actor=None, target=target, source=None,
                             destination=None, steps=[])
    grounding = SimpleNamespace(references={"target": resolved_set_reference()},
                                constraints={}, condition_constraints=[])

    grounded = main_rag._build_grounded_task(intent, grounding, FakeRag())

    records = grounded["references"]["target"]["selected_many"]
    assert [record["node_id"] for record in records] == ["object-1", "object-2"]
    assert [record["navigation_target"]["navigation_node_id"] for record in records] == [
        "place-object-1", "place-object-2"]
    assert all(record["navigation_target"]["frame_id"] == "map" for record in records)


def test_step_specific_resolved_reference_serializes_navigation_frame():
    destination = SimpleNamespace(text="room 1")
    intent_step = SimpleNamespace(action="navigate", description="go to room 1",
                                  actor=None, target=None, source=None,
                                  destination=destination)
    intent = SimpleNamespace(action="navigate", description="visit room 1",
                             actor=None, target=None, source=None,
                             destination=None, steps=[intent_step])
    grounding = SimpleNamespace(
        references={"step_0_destination": SimpleNamespace(
            status="resolved", selected=candidate("room-1"), selected_many=[]
        )},
        constraints={}, condition_constraints=[],
    )

    grounded = main_rag._build_grounded_task(intent, grounding, FakeRag())

    target = grounded["steps"][0]["references"]["destination"]["navigation_target"]
    assert target["frame_id"] == "map"
