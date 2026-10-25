"""Legacy public reference compatibility; in-memory index/resolver only."""
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "RAG/khronos_3dsg_rag/src"))
from api_schemas import EntityReference, SceneEntity
from canonical_index import CanonicalGraphIndex
from scene_graph_api import SceneGraphRAG


def fixture_rag():
    index = CanonicalGraphIndex()
    index.nodes["chair"] = SceneEntity(node_id="chair", node_type="object",
                                      label="chair", position=[1.0, 2.0, 0.0])
    index.labels["chair"].append("chair")
    index.types["object"].append("chair")
    resolver = SimpleNamespace(resolve=lambda *args, **kwargs: [
        {"node_id": "chair", "score": 1.0}])
    rag = SceneGraphRAG("/not-a-real-database")
    rag._index = index
    rag._resolver = resolver
    return rag


def test_public_legacy_reference_resolves_without_richer_schema_attributes():
    ref = EntityReference(reference_id="query", text="chair", label="chair")
    original = asdict(ref)
    result = fixture_rag().resolve_entity(ref)
    assert result.status == "resolved"
    assert result.selected.node_id == "chair"
    assert result.reference is ref
    assert asdict(ref) == original


def test_legacy_reference_still_applies_type_filter():
    ref = EntityReference(reference_id="query", text="chair", node_type="room")
    result = fixture_rag().resolve_entity(ref)
    assert result.status == "not_found"
    assert result.selected is None


@pytest.mark.parametrize("quantifier", ["one", "all", "nearest"])
def test_richer_reference_keeps_quantifier_behavior(quantifier):
    ref = SimpleNamespace(text="chair", label="chair", node_type="object",
                          room=None, position=[0.0, 0.0, 0.0],
                          locations=[], relations=[], quantifier=quantifier)
    result = fixture_rag().resolve_entity(ref)
    assert result.status == ("resolved_set" if quantifier == "all" else "resolved")
    selected = result.selected_many if quantifier == "all" else [result.selected]
    assert [item.node_id for item in selected] == ["chair"]
