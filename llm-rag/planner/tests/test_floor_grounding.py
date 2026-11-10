"""Offline C01 regression: explicit floors constrain room navigation."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "planner"))
sys.path.insert(0, str(ROOT / "RAG/khronos_3dsg_rag/src"))
from api_schemas import EntityReference as APIReference, SceneEntity
from canonical_index import CanonicalGraphIndex
from grounding.orchestrator import GroundingOrchestrator
from llm.intent_parser import get_intent_prompt_artifact, simple_taskchain_to_task_intent
from planner_schemas.intent import EntityReference, TaskIntent
from planner_schemas.taskchain_intent import SimpleTaskChainIntent
from scene_graph_api import SceneGraphRAG

HEIGHTS = [-1.2, 2.3, 5.8, 9.3]


def make_api():
    index = CanonicalGraphIndex()
    for floor, z in enumerate(HEIGHTS):
        room = str(100000000000000 + floor)
        bed = str(200000000000000 + floor)
        for node in [SceneEntity(room, "room", "room", position=[floor, 0, z]),
                     SceneEntity(bed, "object", "bed", position=[floor, 0, z + .8], room_id=room)]:
            index.nodes[node.node_id] = node
            index.labels[node.label].append(node.node_id)
            index.types[node.node_type].append(node.node_id)
        index.parents[bed] = [room]
        index.children[room] = [bed]
    rag = SceneGraphRAG("/unused-offline")
    rag._index = index
    return rag


def payload(floor):
    return SimpleTaskChainIntent.model_validate({"action": "navigate", "stops": [
        {"order": 1, "destination": "room", "objects": ["bed"], "floor_id": floor}]})


@pytest.mark.parametrize("floor", [0, 1, 2, 3, "0", "1", "2", "3", "F0", "F1", "F2", "F3"])
def test_stop_floor_survives_adaptation_and_filters_rooms(floor):
    intent = simple_taskchain_to_task_intent(payload(floor), preserve_surface_objects=True)
    expected = int(str(floor).removeprefix("F"))
    assert intent.steps[0].target.floor_id == expected
    assert intent.steps[0].target.text == "room"
    result = GroundingOrchestrator(make_api()).ground_intent(intent)
    ref = result.references["step_0_target"]
    assert ref.status == "resolved"
    assert [c.node_id for c in ref.candidates] == [str(100000000000000 + expected)]
    assert ref.selected.node_type == "room"


@pytest.mark.parametrize("mode", ["one", "any", "all", "nearest", "farthest", "exact"])
def test_all_resolution_paths_apply_floor_before_selection(mode):
    rag = make_api()
    ref = EntityReference(text=str(100000000000000) if mode == "exact" else "room",
                          node_type="room", label="room", floor_id=2,
                          quantifier="one" if mode == "exact" else mode,
                          position=[0, 0, HEIGHTS[0]])
    result = rag.resolve_entity(ref)
    if mode == "exact":
        assert result.status == "not_found"
        assert not result.candidates and result.selected is None
    else:
        assert result.selected.node_id == str(100000000000002)
        assert {c.node_id for c in result.candidates} == {str(100000000000002)}


def test_missing_floor_match_returns_no_candidates():
    rag = make_api()
    del rag._index.nodes[str(100000000000002)]
    result = GroundingOrchestrator(rag).ground_intent(simple_taskchain_to_task_intent(payload(2)))
    assert result.status == "not_found"
    assert not result.references["step_0_target"].candidates


def test_unknown_room_height_cannot_satisfy_explicit_floor():
    rag = make_api()
    for node in rag._index.nodes.values():
        if node.node_type == "room":
            node.position = [0, 0, float("nan")]
    result = rag.resolve_entity(EntityReference(text="room", label="room", node_type="room", floor_id=0))
    assert result.status == "not_found"


def test_api_dto_and_object_parent_room_floor():
    result = make_api().resolve_entity(APIReference("r", "bed", node_type="object", label="bed",
                                                  floor_id=0, quantifier="all"))
    assert [c.node_id for c in result.selected_many] == [str(200000000000000)]


@pytest.mark.parametrize("floor", [-1, 4, "F4", True, 1.2, "first", ""])
def test_invalid_floors_are_rejected(floor):
    with pytest.raises(ValueError):
        payload(floor)
    with pytest.raises(ValueError):
        EntityReference(text="room", floor_id=floor)


def test_floor_scope_is_per_stop_and_absence_is_unrestricted():
    compact = SimpleTaskChainIntent.model_validate({"action": "navigate", "stops": [
        {"order": 1, "destination": "room", "objects": ["bed"], "floor_id": "F0"},
        {"order": 2, "destination": "room", "objects": ["bed"], "floor_id": "F3"},
        {"order": 3, "destination": "room", "objects": ["bed"]}]})
    result = GroundingOrchestrator(make_api()).ground_intent(simple_taskchain_to_task_intent(compact))
    assert result.references["step_0_target"].selected.node_id == str(100000000000000)
    assert result.references["step_1_target"].selected.node_id == str(100000000000003)
    assert len(result.references["step_2_target"].candidates) == 4


def test_surface_prompt_requires_explicit_structured_floor_and_documents_numbering():
    prompt, schema = get_intent_prompt_artifact("hospital_taskchain_surface_v1")
    assert "floor_id" in schema["$defs"]["SimpleTaskStop"]["properties"]
    assert "floor_id" in prompt and "F0" in prompt and "F3" in prompt
    assert "ground floor" in prompt and "first floor" in prompt
    assert "Do not infer" in prompt


@pytest.mark.parametrize("relation", ["near", "nearest_to", "farthest_from"])
def test_relative_ranking_uses_only_requested_floor_rooms(relation):
    from planner_schemas.intent import RelationConstraint
    result = make_api().resolve_entity(EntityReference(
        text="room", node_type="room", label="room", floor_id=2,
        relations=[RelationConstraint(relation=relation, target_node_id=str(200000000000002),
                                      target_text="bed", target_label="bed", target_node_type="object")]))
    assert result.selected.node_id == str(100000000000002)
    assert [c.node_id for c in result.candidates] == [str(100000000000002)]


def test_missing_and_equidistant_geometry_do_not_match():
    rag = make_api()
    for position in ([], [0, 0], [0, 0, (HEIGHTS[0] + HEIGHTS[1]) / 2]):
        for node in rag._index.nodes.values():
            node.position = position
        result = rag.resolve_entity(EntityReference(text="room", label="room", node_type="room", floor_id=0))
        assert result.status == "not_found"


def test_surface_parser_preserves_structured_floor_through_canonicalization(monkeypatch):
    import ollama
    from types import SimpleNamespace
    from llm.intent_parser import parse_intent_with_llm, intent_to_dict

    compact = {"action": "navigate", "stops": [
        {"order": 1, "destination": "room", "objects": ["bed"], "floor_id": "F0"}]}
    requests = []
    def chat(self, **kwargs):
        requests.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(compact), thinking=None))
    monkeypatch.setattr(ollama.Client, "chat", chat)
    intent = parse_intent_with_llm("Go to a room with a bed on F0.", model="synthetic",
        config={"intent_prompt_profile": "hospital_taskchain_surface_v1"})
    assert intent_to_dict(intent)["steps"][0]["target"]["floor_id"] == 0
    assert len(requests) == 1
    assert "floor_id" in requests[0]["format"]["$defs"]["SimpleTaskStop"]["properties"]
    assert GroundingOrchestrator(make_api()).ground_intent(intent).references[
        "step_0_target"].selected.node_id == str(100000000000000)


def test_saved_60_no_floor_payloads_keep_original_adapted_intents():
    repo = ROOT.parent
    saved = repo / "experiment/task-chain-60/runs/surface-v1-llm2-60x4-20261020/predictions/full.jsonl"
    rows = [json.loads(line) for line in saved.read_text().splitlines()]
    assert len(rows) == 60

    def without_floor(value):
        if isinstance(value, dict):
            return {key: without_floor(item) for key, item in value.items() if key != "floor_id"}
        if isinstance(value, list):
            return [without_floor(item) for item in value]
        return value

    for row in rows:
        record = row["result"]
        raw = record["intent_parse_attempts"][-1]["raw_lmm_output"]
        compact = SimpleTaskChainIntent.model_validate(raw)
        assert all(stop.floor_id is None for stop in compact.stops), row["task_id"]
        intent = simple_taskchain_to_task_intent(compact, preserve_surface_objects=True)
        assert without_floor(intent.model_dump(mode="json")) == record["intent"], row["task_id"]



def test_multiple_rooms_on_requested_floor_remain_candidates_for_ui():
    rag = make_api()
    extra = str(100000000000010)
    rag._index.nodes[extra] = SceneEntity(extra, "room", "room", position=[9, 0, HEIGHTS[1]])
    result = rag.resolve_entity(EntityReference(text="room", node_type="room", label="room", floor_id=1))
    assert result.status == "ambiguous" and result.selected is None
    assert {c.node_id for c in result.candidates} == {str(100000000000001), extra}


def test_null_floor_and_round_trip_preserve_unrestricted_requests():
    intent = simple_taskchain_to_task_intent(payload(None))
    restored = TaskIntent.model_validate_json(intent.model_dump_json())
    assert restored.steps[0].target.floor_id is None
    result = GroundingOrchestrator(make_api()).ground_intent(restored)
    assert len(result.references["step_0_target"].candidates) == 4
