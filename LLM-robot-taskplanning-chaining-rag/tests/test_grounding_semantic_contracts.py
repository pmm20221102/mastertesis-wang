"""CPU contracts against the real canonical index and candidate resolver."""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "RAG/khronos_3dsg_rag/src"))
sys.path.insert(0, str(ROOT / "LLM-robot-taskplanning-chaining-rag"))

from api_schemas import EntityReference as APIReference, SceneEntity
from canonical_index import CanonicalGraphIndex
from entity_resolver import EntityResolver
from planner_schemas.intent import EntityReference, LocationConstraint, RelationConstraint, TaskIntent
from scene_graph_api import SceneGraphRAG
from semantic_retriever import LabelScore, SemanticCandidate, SemanticSearchResult

FIRST, SECOND, ROOM, OTHER_ROOM, PLACE = [str(100000000000001 + i) for i in range(5)]


def make_api(labels=("bed", "healthmonitor")):
    index = CanonicalGraphIndex()
    for identifier, label, kind, position, room in [
        (FIRST, labels[0], "object", [1, 0, 0], ROOM),
        (SECOND, labels[1], "object", [2, 0, 0], OTHER_ROOM),
        (ROOM, "ward", "room", [0, 0, 0], ""),
        (OTHER_ROOM, "office", "room", [3, 0, 0], ""),
        (PLACE, "corridor", "place", [0, 0, 0], ROOM),
    ]:
        node = SceneEntity(node_id=identifier, label=label, node_type=kind,
                           position=position, room_id=room, place_id=PLACE if kind == "object" else "")
        index.nodes[identifier] = node
        index.labels[label.lower()].append(identifier)
        index.types[kind].append(identifier)
    index.neighbors[FIRST] = [SECOND]
    index.children[ROOM] = [FIRST]
    index.parents[FIRST] = [ROOM]
    api = SceneGraphRAG("/unused-offline")
    api._index = index
    # Supply in-memory metadata to the real resolver; no behavior is mocked.
    api._resolver = EntityResolver("/unused-offline")
    api._resolver._index_cache = {
        nid: {"label": n.label, "node_type": n.node_type}
        for nid, n in index.nodes.items()
    }
    return api


def reference(label, mode, identifier=FIRST, **kwargs):
    return EntityReference(text=identifier if mode == "exact" else label,
                           label=label, quantifier="one" if mode == "exact" else mode,
                           position=[0, 0, 0], **kwargs)


@pytest.mark.parametrize("mode", ["exact", "one", "any", "all", "nearest"])
@pytest.mark.parametrize("label,identifier", [
    ("bed", FIRST), ("beds", FIRST), ("healthmonitors", SECOND),
    ("health monitors", SECOND), ("health_monitors", SECOND),
    ("  HEALTH__MONITORS  ", SECOND),
])
def test_spelling_variants_resolve_on_every_path_without_mutating_intent(mode, label, identifier):
    intent = TaskIntent(action="locate", target=reference(label, mode, identifier, node_type="object"))
    raw = copy.deepcopy(intent.model_dump())
    result = make_api().resolve_entity(intent.target)
    assert result.status == ("resolved_set" if mode == "all" else "resolved")
    assert result.selected.node_id == identifier
    assert result.selected.label == ("bed" if identifier == FIRST else "healthmonitor")
    assert intent.model_dump() == raw
    assert result.reference is intent.target


@pytest.mark.parametrize("mode", ["exact", "one", "all", "nearest"])
@pytest.mark.parametrize("label", ["missing", "missing beds", "bedside", "health", "monitor"])
def test_unknown_or_partial_labels_are_rejected(mode, label):
    assert make_api().resolve_entity(reference(label, mode)).status == "not_found"


@pytest.mark.parametrize("mode", ["exact", "one", "all", "nearest"])
def test_colliding_canonical_spellings_are_not_resolved_by_type_or_distance(mode):
    api = make_api(("healthmonitor", "health_monitor"))
    assert api.resolve_entity(reference("health monitors", mode, node_type="object")).status == "not_found"


@pytest.mark.parametrize("label,identifier", [("bed", FIRST), ("beds", SECOND)])
def test_exact_canonical_label_wins_over_plural_collision(label, identifier):
    result = make_api(("bed", "beds")).resolve_entity(reference(label, "all"))
    assert [c.node_id for c in result.selected_many] == [identifier]


@pytest.mark.parametrize("mode", ["exact", "one", "all", "nearest"])
@pytest.mark.parametrize("constraint", [
    {"node_type": "room"}, {"room": "office"},
    {"locations": [LocationConstraint(node_id=OTHER_ROOM, node_type="room")]},
    {"locations": [LocationConstraint(node_id=ROOM, node_type="place")]},
    {"locations": [LocationConstraint(text="missing", node_type="room")]},
    {"relations": [RelationConstraint(relation="connected_to", target_node_id=SECOND)]},
    {"relations": [RelationConstraint(relation="near", target_text="missing")]},
])
def test_normalization_does_not_relax_hard_constraints(mode, constraint):
    assert make_api().resolve_entity(reference("beds", mode, **constraint)).status == "not_found"


@pytest.mark.parametrize("mode", ["one", "all", "nearest"])
def test_normalized_labels_keep_valid_location_and_relation_constraints(mode):
    ref = reference("health monitors", mode, SECOND, node_type="object",
                    locations=[LocationConstraint(node_id=OTHER_ROOM, node_type="room")],
                    relations=[RelationConstraint(relation="near", target_node_id=FIRST)])
    assert make_api().resolve_entity(ref).selected.node_id == SECOND


def test_canonical_ontology_is_dynamic_and_not_a_bed_monitor_alias_table():
    api = make_api(("supply_cart", "battery"))
    assert api.resolve_entity(reference("supply carts", "all")).selected.node_id == FIRST
    assert api.resolve_entity(reference("batteries", "all")).selected.node_id == SECOND
    api._index.nodes[SECOND].label = "supplycart"
    api._index.labels.pop("battery")
    api._index.labels["supplycart"].append(SECOND)
    assert api.resolve_entity(reference("supply carts", "all")).status == "not_found"


def test_api_dataclass_reference_keeps_raw_label():
    ref = APIReference(reference_id="target", text="beds", label="beds", quantifier="all")
    result = make_api().resolve_entity(ref)
    assert result.selected.node_id == FIRST
    assert result.reference is ref and ref.label == "beds"


@pytest.mark.parametrize("mode", ["exact", "one", "all", "nearest"])
@pytest.mark.parametrize("label", ["room", "rooms"])
@pytest.mark.parametrize("canonical_type", ["room", "rooms", "Rooms"])
def test_explicit_type_generic_does_not_require_room_semantic_label(mode, label, canonical_type):
    api = make_api()
    node = api._index.nodes.pop(ROOM)
    region_id = "6917529027641081856"
    api._index.labels.pop("ward")
    api._index.types["room"].remove(ROOM)
    node.node_id, node.label = region_id, "unknown"
    node.node_type = canonical_type
    api._index.nodes[region_id] = node
    api._index.labels["unknown"].append(region_id)
    api._index.types[canonical_type.lower()].append(region_id)
    api._resolver._index_cache.pop(ROOM)
    api._resolver._index_cache[region_id] = {"label": "unknown", "node_type": canonical_type}
    ref = reference(label, mode, region_id, node_type="room",
                    locations=[LocationConstraint(node_id=region_id, node_type="room")])
    result = api.resolve_entity(ref)
    assert result.selected.node_id == region_id
    assert result.selected.label == "unknown"
    assert result.reference is ref and ref.label == label


@pytest.mark.parametrize("mode", ["exact", "one", "all", "nearest"])
@pytest.mark.parametrize("label,node_type", [
    ("rooms", None), ("rooms", "object"), ("ward", "room"), ("wards", "room"),
    ("missing", "room"),
])
def test_unknown_room_semantics_are_not_replaced_by_type(mode, label, node_type):
    api = make_api(("unknown", "unknown"))
    api._index.nodes[ROOM].label = "unknown"
    api._index.labels.pop("ward")
    api._index.labels["unknown"].append(ROOM)
    api._resolver._index_cache[ROOM]["label"] = "unknown"
    assert api.resolve_entity(reference(label, mode, ROOM, node_type=node_type)).status == "not_found"


def test_type_generic_keeps_location_rejection_and_real_semantic_label_priority():
    api = make_api()
    assert api.resolve_entity(reference("rooms", "all", node_type="room",
        locations=[LocationConstraint(node_id=FIRST, node_type="room")])).status == "not_found"
    assert [c.node_id for c in api.resolve_entity(reference("wards", "all", node_type="room")).selected_many] == [ROOM]
    assert api.resolve_entity(reference("wards", "all", node_type="object")).status == "not_found"


def test_generic_label_supplies_candidates_for_natural_language_region_query():
    ref = EntityReference(text="the mapped region", label="room", node_type="room",
        locations=[LocationConstraint(node_id=ROOM, node_type="room")])
    result = make_api().resolve_entity(ref)
    assert result.status == "resolved" and result.selected.node_id == ROOM


def test_missing_exact_id_is_not_replaced_with_an_existing_plural_label():
    ref = EntityReference(text="100000000000099", label="beds", node_type="object")
    assert make_api().resolve_entity(ref).status == "not_found"


@pytest.mark.parametrize("canonical,query", [("beds", "bed"), ("supply_carts", "supply cart")])
def test_singular_query_can_match_plural_canonical(canonical, query):
    result = make_api((canonical, "healthmonitor")).resolve_entity(reference(query, "all"))
    assert [c.node_id for c in result.selected_many] == [FIRST]


@pytest.mark.parametrize("mode", ["one", "any", "all", "nearest"])
@pytest.mark.parametrize("text", ["beds", "health monitors", "health_monitors"])
def test_natural_text_without_model_label_uses_unique_ontology(mode, text):
    ref = EntityReference(text=text, quantifier=mode, position=[0, 0, 0])
    raw = copy.deepcopy(ref.model_dump())
    result = make_api().resolve_entity(ref)
    assert result.selected.node_id == (FIRST if text == "beds" else SECOND)
    assert result.reference is ref and ref.model_dump() == raw


@pytest.mark.parametrize("mode", ["one", "any", "all", "nearest"])
@pytest.mark.parametrize("text", ["screens", "screen", "monitor", "missing"])
def test_unknown_natural_text_never_becomes_a_partial_semantic_alias(mode, text):
    ref = EntityReference(text=text, quantifier=mode, position=[0, 0, 0])
    assert make_api(("medicalscreen", "healthmonitor")).resolve_entity(ref).status == "not_found"


@pytest.mark.parametrize("mode", ["one", "all", "nearest"])
def test_colliding_natural_text_without_label_fails_closed(mode):
    ref = EntityReference(text="health monitors", quantifier=mode, position=[0, 0, 0])
    assert make_api(("healthmonitor", "health_monitor")).resolve_entity(ref).status == "not_found"


@pytest.mark.parametrize("mode", ["exact", "one", "all", "nearest"])
@pytest.mark.parametrize("kind,identifier", [("place", PLACE), ("room", ROOM)])
@pytest.mark.parametrize("form", ["{kind} {identifier}", "inside {kind} {identifier}",
                                "  {kind} ID {identifier}  "])
@pytest.mark.parametrize("field", ["relation", "location", "legacy_room"])
def test_raw_model_explicit_region_text_is_adapted_without_mutation(mode, kind, identifier, form, field):
    text = form.format(kind=kind, identifier=identifier)
    kwargs = ({"relations": [RelationConstraint(relation="inside", target_text=text)]}
              if field == "relation" else
              {"locations": [LocationConstraint(text=text, node_type=kind)]}
              if field == "location" else {"room": text})
    ref = reference("beds", mode, **kwargs)
    raw = copy.deepcopy(ref.model_dump())
    api = make_api()
    api._index.children[PLACE] = [FIRST]
    result = api.resolve_entity(ref)
    assert result.selected.node_id == FIRST
    assert result.reference is ref and ref.model_dump() == raw


@pytest.mark.parametrize("kwargs", [
    {"target_text": f"place {ROOM}"},
    {"target_text": f"room {PLACE}"},
    {"target_text": f"place {PLACE}", "target_node_type": "room"},
    {"target_text": f"place {PLACE}", "target_node_id": ROOM},
    {"target_text": f"room {ROOM}", "target_label": "office"},
    {"target_node_id": ROOM, "target_node_type": "place"},
    {"target_node_id": ROOM, "target_label": "office"},
    {"target_text": "place 100000000000099", "target_label": "ward"},
])
@pytest.mark.parametrize("mode", ["exact", "one", "all", "nearest"])
def test_explicit_relation_conflicts_fail_closed(mode, kwargs):
    ref = reference("beds", mode, relations=[RelationConstraint(relation="inside", **kwargs)])
    assert make_api().resolve_entity(ref).status == "not_found"


def test_verbatim_relation_phrase_uses_semantic_retrieval_then_graph_hierarchy(monkeypatch):
    calls = []

    def search_semantic(query, *, top_k, node_type, db_dir, threshold):
        calls.append((query, top_k, node_type, str(db_dir), threshold))
        return SemanticSearchResult(
            status="matched",
            candidates=[SemanticCandidate(
                node_id=FIRST,
                canonical_label="bed",
                node_type="objects",
                similarity_score=0.72,
            )],
            label_ranking=[LabelScore(
                label="bed", node_type="objects", score=0.72, count=1
            )],
        )

    monkeypatch.setattr("semantic_retriever.search_semantic", search_semantic)
    api = make_api()
    ref = EntityReference(
        text="room",
        label="room",
        node_type="room",
        relations=[RelationConstraint(
            relation="contains",
            target_text="a place for a patient to rest",
            target_label=None,
            target_node_type="object",
        )],
    )

    result = api.resolve_entity(ref)

    assert result.status == "resolved"
    assert result.selected.node_id == ROOM
    assert calls == [(
        "a place for a patient to rest", 5, "object", "/unused-offline", 0.35
    )]


def test_semantic_relation_retrieval_rejects_below_threshold_phrase(monkeypatch):
    monkeypatch.setattr(
        "semantic_retriever.search_semantic",
        lambda *_args, **_kwargs: SemanticSearchResult(
            status="not_found",
            label_ranking=[LabelScore(
                label="bed", node_type="objects", score=0.2, count=1
            )],
        ),
    )
    api = make_api()
    ref = EntityReference(
        text="room",
        label="room",
        node_type="room",
        relations=[RelationConstraint(
            relation="contains",
            target_text="a wildly unrelated phrase",
            target_label=None,
            target_node_type="object",
        )],
    )

    assert api.resolve_entity(ref).status == "not_found"


@pytest.mark.parametrize("location", [
    LocationConstraint(text=f"place {PLACE}", node_type="room"),
    LocationConstraint(node_id=ROOM, text=f"place {PLACE}", node_type="room"),
    LocationConstraint(node_id=ROOM, node_type="place"),
    LocationConstraint(node_id=FIRST, node_type="room"),
    LocationConstraint(text="room 100000000000099", node_type="room"),
])
def test_explicit_location_conflicts_fail_closed(location):
    assert make_api().resolve_entity(reference("beds", "all", locations=[location])).status == "not_found"


@pytest.mark.parametrize("text", [f"place {PLACE}", f"room ID {ROOM}", f"inside room {ROOM}"])
def test_top_level_typed_id_is_exact_and_keeps_raw_reference(text):
    ref = EntityReference(text=text)
    result = make_api().resolve_entity(ref)
    assert result.selected.node_id == (PLACE if "place" in text else ROOM)
    assert result.reference is ref and ref.text == text


@pytest.mark.parametrize("text,kwargs", [
    (f"place {ROOM}", {}), (f"room {PLACE}", {}),
    (f"place {PLACE}", {"node_type": "room"}),
    ("place 100000000000099", {"label": "corridor"}),
    (f"room {ROOM}", {"label": "office"}),
])
def test_top_level_typed_id_does_not_relax_constraints(text, kwargs):
    assert make_api().resolve_entity(EntityReference(text=text, **kwargs)).status == "not_found"


def test_ontology_public_method_is_current_sorted_detached_labels_only():
    api = make_api()
    labels = api.get_label_ontology()
    assert labels == ["bed", "corridor", "healthmonitor", "office", "ward"]
    labels.append("invented")
    api._index.labels["empty"] = []
    api._index.labels["dangling"].append("missing-node")
    api._index.labels["stale"].append(FIRST)
    api._index.nodes[SECOND].label = "battery"
    api._index.labels.pop("healthmonitor")
    api._index.labels["battery"].append(SECOND)
    assert api.get_label_ontology() == ["battery", "bed", "corridor", "office", "ward"]
    assert all(isinstance(label, str) for label in api.get_label_ontology())


@pytest.mark.parametrize("mode", ["exact", "one", "all", "nearest"])
def test_relation_target_label_uses_same_ontology_normalization(mode):
    ref = reference("health monitors", mode, SECOND, relations=[
        RelationConstraint(relation="near", target_label="beds", target_node_type="object")])
    assert make_api().resolve_entity(ref).selected.node_id == SECOND


@pytest.mark.parametrize("kind", ["place", "room"])
def test_short_typed_ids_are_exact_without_resolver_fallback(kind):
    api = make_api()
    node = SceneEntity(node_id="42", node_type=kind, label="short_region")
    api._index.nodes["42"] = node
    ref = EntityReference(text=f"{kind} ID 42")
    result = api.resolve_entity(ref)
    assert result.status == "resolved" and result.selected.node_id == "42"


@pytest.mark.parametrize("mode", ["exact", "one", "all", "nearest"])
def test_missing_short_numeric_relation_anchor_cannot_fall_back_to_label(mode):
    ref = reference("beds", mode, relations=[RelationConstraint(
        relation="inside", target_text="42", target_label="ward", target_node_type="room")])
    assert make_api().resolve_entity(ref).status == "not_found"
