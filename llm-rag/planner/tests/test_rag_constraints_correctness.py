"""Hard filters apply to exact-ID, nearest and all paths without live RAG IO."""
import sys
from pathlib import Path
import pytest
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "RAG/khronos_3dsg_rag/src"))
sys.path.insert(0, str(ROOT / "planner"))
from canonical_index import CanonicalGraphIndex
from api_schemas import SceneEntity
from scene_graph_api import SceneGraphRAG
from planner_schemas.intent import EntityReference, RelationConstraint, LocationConstraint

FIRST, SECOND, DESK, ROOM = ("100000000000001", "100000000000002",
                            "100000000000003", "100000000000004")


def rag():
    index = CanonicalGraphIndex()
    for identifier, label, kind, position in [
        (FIRST, "chair", "object", [1, 0, 0]), (SECOND, "chair", "object", [3, 0, 0]),
        (DESK, "desk", "object", [2, 0, 0]), (ROOM, "room", "room", [0, 0, 0])]:
        node = SceneEntity(node_id=identifier, node_type=kind, label=label, position=position,
                           room_id=ROOM if kind == "object" else None)
        index.nodes[identifier] = node
        index.labels[label].append(identifier)
        index.types[kind].append(identifier)
    index.neighbors[DESK] = [SECOND]
    index.parents[SECOND] = [ROOM]
    index.children[ROOM] = [SECOND]
    instance = SceneGraphRAG("/not-used")
    instance._index = index
    instance._resolver = SimpleNamespace(resolve=lambda *args, **kwargs: [{"node_id": FIRST, "score": 1.0}])
    return instance


@pytest.mark.parametrize("field,value", [("label", "desk"), ("node_type", "room")])
def test_exact_id_cannot_override_declared_type_or_label(field, value):
    reference = EntityReference(text=FIRST, **{field: value})
    assert rag().resolve_entity(reference).status == "not_found"


@pytest.mark.parametrize("mode", ["exact", "nearest", "all"])
def test_relation_filter_applied_before_fast_path_selection(mode):
    reference = EntityReference(text=FIRST if mode == "exact" else "chairs", label="chair",
        node_type="object", quantifier={"exact": "one", "nearest": "nearest", "all": "all"}[mode],
        position=[0, 0, 0], relations=[RelationConstraint(relation="connected_to", target_node_id=DESK)])
    result = rag().resolve_entity(reference)
    if mode == "exact":
        assert result.status == "not_found"
    else:
        selected = result.selected_many if mode == "all" else [result.selected]
        assert [candidate.node_id for candidate in selected] == [SECOND]


@pytest.mark.parametrize("mode", ["nearest", "all"])
def test_unresolved_relation_is_not_silently_dropped(mode):
    reference = EntityReference(text="chairs", label="chair", quantifier=mode, position=[0, 0, 0],
        relations=[RelationConstraint(relation="near", target_text="does-not-exist")])
    assert rag().resolve_entity(reference).status == "not_found"


@pytest.mark.parametrize("mode", ["exact", "nearest", "all"])
def test_unresolved_room_is_not_relaxed(mode):
    reference = EntityReference(text=FIRST if mode == "exact" else "chairs", label="chair",
        quantifier={"exact": "one", "nearest": "nearest", "all": "all"}[mode],
        position=[0, 0, 0], room="missing room")
    assert rag().resolve_entity(reference).status == "not_found"


@pytest.mark.parametrize("relation", ["contains", "parent_of"])
def test_contains_and_parent_follow_correct_direction(relation):
    reference = EntityReference(text=ROOM, node_type="room",
        relations=[RelationConstraint(relation=relation, target_node_id=SECOND)])
    result = rag().resolve_entity(reference)
    assert result.status == "resolved" and result.selected.node_id == ROOM


def test_child_of_uses_parent_children():
    reference = EntityReference(text=FIRST,
        relations=[RelationConstraint(relation="child_of", target_node_id=ROOM)])
    assert rag().resolve_entity(reference).status == "not_found"


def test_nearest_missing_reference_position_does_not_become_keyword_match():
    reference = EntityReference(text="chair", label="chair", quantifier="nearest")
    assert rag().resolve_entity(reference).status == "not_found"


@pytest.mark.parametrize("position", [[float("nan"), 0, 0], [float("inf"), 0, 0]])
def test_nonfinite_nearest_position_is_not_accepted(position):
    reference = EntityReference(text="chairs", label="chair", quantifier="nearest", position=position)
    assert rag().resolve_entity(reference).status == "not_found"

@pytest.mark.parametrize("relation", ["inside", "contains"])
def test_api_and_orchestrator_agree_on_transitive_relations(relation):
    from grounding.orchestrator import GroundingOrchestrator
    from planner_schemas.intent import TaskIntent
    api = rag()
    place_id = "100000000000005"
    node = SceneEntity(node_id=place_id, node_type="place", label="place", position=[0, 0, 0])
    api._index.nodes[place_id] = node
    api._index.parents[SECOND] = [place_id]
    api._index.parents[place_id] = [ROOM]
    api._index.children[ROOM] = [place_id]
    api._index.children[place_id] = [SECOND]
    target_id, relation_id = (SECOND, ROOM) if relation == "inside" else (ROOM, SECOND)
    reference = EntityReference(text=target_id,
        relations=[RelationConstraint(relation=relation, target_node_id=relation_id)])
    result = GroundingOrchestrator(api).ground_intent(TaskIntent(action="locate", target=reference))
    assert result.status == "ready" and result.references["target"].selected.node_id == target_id
