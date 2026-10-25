"""RAG query contracts using synthetic collections and an in-memory graph."""
from dataclasses import asdict, fields
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "RAG/khronos_3dsg_rag/src"))

if __name__ == "__main__":
    source = (ROOT / "docs/correctness-fixes-20261003/verify_offline.py").read_text()
    guard_namespace = {"__name__": "rag_query_offline_guard"}
    exec(compile(source.split("group = sys.argv[1]", 1)[0], "verify_offline.py", "exec"), guard_namespace)

import pytest
import query_rag as query_module
from api_schemas import EntityReference


def matches(meta, where):
    """Small fixture evaluator; production delegates filters to Chroma."""
    if not where:
        return True
    if "$and" in where:
        return all(matches(meta, clause) for clause in where["$and"])
    if "$or" in where:
        return any(matches(meta, clause) for clause in where["$or"])
    key, expected = next(iter(where.items()))
    if isinstance(expected, dict):
        operator, value = next(iter(expected.items()))
        if operator == "$in":
            return meta.get(key) in value
        if operator == "$gte":
            return meta.get(key, -math.inf) >= value
        if operator == "$ne":
            return meta.get(key) != value
        raise AssertionError("Unsupported fixture operator")
    return meta.get(key) == expected


class FakeCollection:
    def __init__(self):
        self.records = []
        self.get_calls = []
        self.query_calls = []
        self.add("seed", "room", "outside", 0, children=["reject", "accept", "later"],
                 neighbors=["reject", "accept", "later"])
        self.add("reject", "object", "outside", 0.1, parents=["seed"], neighbors=["seed"])
        self.add("accept", "object", "allowed", 1, parents=["seed"], neighbors=["seed"], confidence=0.9)
        self.add("later", "object", "allowed", 2, parents=["seed"], neighbors=["seed"], confidence=0.7)

    def add(self, node_id, node_type, room, x, children=(), parents=(), neighbors=(), confidence=0.0):
        self.records.append(("entity_" + node_id, node_id + " document", {
            "node_id": node_id, "node_type": node_type, "label": node_id,
            "room": room, "chunk_type": "entity", "confidence": confidence,
            "bounding_box_center": json.dumps([x, 0, 0]),
            "parent_ids": json.dumps(list(parents)), "child_ids": json.dumps(list(children)),
            "neighbor_ids": json.dumps(list(neighbors))}))

    def get(self, include=None, where=None):
        self.get_calls.append({"include": include, "where": where})
        rows = [row for row in self.records if matches(row[2], where)]
        return {"ids": [row[0] for row in rows], "documents": [row[1] for row in rows],
                "metadatas": [row[2] for row in rows]}

    def count(self):
        return len(self.records)

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        rows = [row for row in self.records if matches(row[2], kwargs.get("where"))][:kwargs["n_results"]]
        return {"ids": [[row[0] for row in rows]], "documents": [[row[1] for row in rows]],
                "metadatas": [[row[2] for row in rows]], "distances": [[0.1 + i * 0.1 for i in range(len(rows))]]}


@pytest.fixture
def collection(monkeypatch):
    value = FakeCollection()
    monkeypatch.setattr(query_module, "load_vector_db", lambda *args: value)
    import entity_resolver
    monkeypatch.setattr(entity_resolver, "resolve_to_parents", lambda *args, **kwargs: [
        {"node_id": "reject", "score": 0.99}, {"node_id": "accept", "score": 0.85},
        {"node_id": "later", "score": 0.7}])
    return value


@pytest.mark.parametrize("query_type,query", [
    ("hierarchy", "inside seed"), ("neighbor", "near seed"),
    ("spatial", "x=0 y=0"), ("reverse_hierarchy", "where is seed")])
def test_graph_route_applies_filter_before_top_k_without_filtering_seed(collection, query_type, query):
    where = {"room": "allowed"}
    result = query_module.query_rag(query, "/unused", top_k=1, where_filter=where, query_type=query_type)
    assert result[0]["node_id"] == "accept"
    assert all(row["metadata"]["room"] == "allowed" for row in result)
    assert not collection.query_calls  # A valid graph hit must remain a graph hit.
    assert any(call["where"] == where for call in collection.get_calls)
    assert result[0]["rank"] == 1


@pytest.mark.parametrize("where", [
    {"room": "allowed"}, {"node_type": "object"}, {"confidence": {"$gte": 0.8}},
    {"$and": [{"room": "allowed"}, {"confidence": {"$gte": 0.8}}]},
    {"$or": [{"label": {"$in": ["accept"]}}, {"room": "absent"}]},
    {"room": {"$ne": "outside"}},
])
def test_graph_and_vector_routes_obey_same_metadata_filter(collection, where):
    graph = query_module.query_rag("inside seed", "/unused", top_k=10, where_filter=where, query_type="hierarchy")
    vector = query_module.query_rag("semantic", "/unused", top_k=10, where_filter=where, query_type="semantic")
    assert {row["node_id"] for row in graph} == {row["node_id"] for row in vector}
    assert collection.query_calls[-1]["where"] == where


def test_direct_graph_lookup_accepts_optional_where_filter(collection):
    result = query_module.graph_lookup("/unused", "near seed", "neighbor", 1, where_filter={"room": "allowed"})
    assert [row["node_id"] for row in result] == ["accept"]


def test_empty_graph_filter_cannot_leak_unfiltered_seed_or_vector_hit(collection):
    where = {"room": "absent"}
    result = query_module.query_rag("inside seed", "/unused", where_filter=where, query_type="hierarchy")
    assert result == []
    assert collection.query_calls[-1]["where"] == where


def test_vector_fallback_preserves_filter_score_and_topology(collection):
    where = {"room": "allowed"}
    result = query_module.query_rag("near nonexistent", "/unused", top_k=2, where_filter=where, query_type="neighbor")
    assert [row["node_id"] for row in result] == ["accept", "later"]
    assert [row["score"] for row in result] == [0.9, 0.8]
    assert result[0]["metadata"]["parent_ids"] == ["seed"]
    assert collection.query_calls[-1]["where"] == where


@pytest.mark.parametrize("query_type,query", [("hierarchy", "inside seed"), ("neighbor", "near seed"),
                                              ("spatial", "x=0 y=0"), ("reverse_hierarchy", "where is seed")])
def test_filter_preserves_existing_relative_ranking_scores_and_topology(collection, query_type, query):
    baseline = query_module.query_rag(query, "/unused", top_k=10, query_type=query_type)
    filtered = query_module.query_rag(query, "/unused", top_k=10, where_filter={"room": "allowed"}, query_type=query_type)
    expected = [row for row in baseline if row["metadata"]["room"] == "allowed"]
    assert [row["node_id"] for row in filtered] == [row["node_id"] for row in expected]
    for actual, original in zip(filtered, expected):
        assert {key: value for key, value in actual.items() if key != "rank"} == {
            key: value for key, value in original.items() if key != "rank"}
    assert [row["rank"] for row in filtered] == list(range(1, len(filtered) + 1))


def test_old_graph_lookup_positional_call_and_spatial_adapter_preserved(collection):
    assert [row["node_id"] for row in query_module.graph_lookup("/unused", "inside seed", "hierarchy", 2)] == ["reject", "accept"]
    assert query_module.spatial_search("/unused", "x=0 y=0", 2) == query_module.graph_lookup("/unused", "x=0 y=0", "spatial", 2)


def test_filtered_seed_fallback_filters_before_top_k(collection):
    for _, _, meta in collection.records:
        meta["neighbor_ids"] = "[]"
    result = query_module.graph_lookup("/unused", "near object", "neighbor", 1, where_filter={"room": "allowed"})
    assert [row["node_id"] for row in result] == ["accept"]
    assert result[0]["score"] == 0.5
    assert not collection.query_calls


def test_no_filter_preserves_original_missing_edge_truncation(collection):
    collection.records[0][2]["child_ids"] = json.dumps(["missing", "accept"])
    result = query_module.graph_lookup("/unused", "inside seed", "hierarchy", 1)
    assert [row["node_id"] for row in result] == ["seed"]


def test_duplicate_node_chunks_return_metadata_from_matching_document(collection):
    collection.add("accept", "object", "outside", 100)
    _, text, meta = collection.records[-1]
    collection.records[-1] = ("context_accept", text, meta)
    result = query_module.graph_lookup("/unused", "inside seed", "hierarchy", 1, where_filter={"room": "allowed"})
    assert result[0]["node_id"] == "accept"
    assert result[0]["metadata"]["room"] == "allowed"


def test_reference_old_constructor_fields_and_required_id_preserved():
    reference = EntityReference("q", "chair", "object", "chair", "named", "room", {"color": "red"}, [0, 0, 0])
    expected = {"reference_id": "q", "text": "chair", "node_type": "object", "label": "chair",
                "name": "named", "room": "room", "attributes": {"color": "red"}, "position": [0, 0, 0]}
    assert {key: asdict(reference)[key] for key in expected} == expected
    assert [item.name for item in fields(reference)][:8] == list(expected)
    with pytest.raises(TypeError):
        EntityReference(text="chair")


def test_reference_optional_defaults_are_independent():
    first, second = EntityReference("a", "chair"), EntityReference("b", "chair")
    assert first.quantifier == "one"
    assert first.aggregate is None
    first.relations.append(SimpleNamespace(relation="near"))
    first.locations.append(SimpleNamespace(node_id="room"))
    assert second.relations == [] and second.locations == []


@pytest.mark.parametrize("quantifier", ["one", "all", "nearest"])
def test_public_richer_reference_passes_existing_api_hard_filters(quantifier):
    from canonical_index import CanonicalGraphIndex
    from api_schemas import SceneEntity
    from scene_graph_api import SceneGraphRAG
    index = CanonicalGraphIndex()
    for node_id, kind, label, x in [("chair1", "object", "chair", 1), ("chair2", "object", "chair", 2),
                                  ("desk", "object", "desk", 0), ("room", "room", "room", 0)]:
        node = SceneEntity(node_id=node_id, node_type=kind, label=label, position=[x, 0, 0], room_id="room")
        index.nodes[node_id] = node
        index.labels[label].append(node_id)
        index.types[kind].append(node_id)
    index.neighbors["desk"] = ["chair2"]
    rag = SceneGraphRAG("/unused")
    rag._index = index
    rag._resolver = SimpleNamespace(resolve=lambda *args, **kwargs: [
        {"node_id": "chair1", "score": 1.0}, {"node_id": "chair2", "score": 0.9}])
    reference = EntityReference("q", "chairs", label="chair", quantifier=quantifier, position=[0, 0, 0],
        locations=[SimpleNamespace(node_id="room", text="", node_type="room")],
        relations=[SimpleNamespace(relation="connected_to", target_node_id="desk", target_text="", target_label=None)])
    result = rag.resolve_entity(reference)
    assert result.status == ("resolved_set" if quantifier == "all" else "resolved")
    assert result.selected.node_id == "chair2"
    assert result.reference is reference


def test_reference_aggregate_metadata_can_be_carried_without_planner_dependency():
    aggregate = SimpleNamespace(operation="count_at_least", target_labels=["chair"], value=2)
    reference = EntityReference("q", "room", aggregate=aggregate)
    assert reference.aggregate is aggregate


if __name__ == "__main__":
    test_files = [str(Path(__file__).resolve())]
    if "--regression" in sys.argv:
        test_files += [str(Path(__file__).with_name(name)) for name in (
            "test_public_reference_compatibility.py", "test_rag_constraints_correctness.py")]
    result = pytest.main(["-q", "--tb=short", "-p", "no:cacheprovider", "-p", "no:logging",
                          "--basetemp", str(guard_namespace["temporary"] / "pytest"), *test_files])
    print("BLOCKED_SIDE_EFFECTS", guard_namespace["denied"])
    raise SystemExit(result if result else bool(guard_namespace["denied"]))
