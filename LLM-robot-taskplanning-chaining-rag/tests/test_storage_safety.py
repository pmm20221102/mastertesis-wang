"""Fake IO boundaries for destructive-output safety; real build/main logic."""
import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
ROOT = Path(__file__).resolve().parents[2]


def source_functions(relative, names, namespace):
    path = ROOT / relative
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def vector_builder(tmp_path, fail=None):
    events, collections = [], {}
    class FakeCollection:
        def __init__(self, name, documents):
            self.name, self.documents = name, list(documents)
        def add(self, **values):
            events.append(("add", self.name))
            if fail == "add":
                raise RuntimeError("embedding failed")
            self.documents.extend(values["ids"])
        def modify(self, name):
            events.append(("rename", self.name, name))
            if fail == "promote" and name == "scene_graph" and self.documents != ["old"]:
                raise RuntimeError("promotion failed")
            collections.pop(self.name)
            self.name = name
            collections[name] = self
    old = FakeCollection("scene_graph", ["old"])
    collections[old.name] = old
    class NotFound(Exception):
        pass
    class Client:
        def get_collection(self, name, **kwargs):
            if name not in collections:
                raise NotFound(name)
            return collections[name]
        def delete_collection(self, name):
            events.append(("delete", name))
            collections.pop(name)
        def create_collection(self, name, **kwargs):
            assert name not in collections
            result = FakeCollection(name, [])
            collections[name] = result
            return result
    namespace = dict(Path=Path, json=json,
        chromadb=SimpleNamespace(PersistentClient=lambda **kwargs: Client(), Collection=FakeCollection),
        Settings=lambda **kwargs: None, NotFoundError=NotFound, RagDocument=object,
        SentenceTransformerEmbeddingFunction=lambda model, **kwargs: None,
        DEFAULT_EMBED_MODEL="fake", COLLECTION_NAME="scene_graph",
        logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a: None))
    source_functions("RAG/khronos_3dsg_rag/src/build_vector_db.py", {"build_vector_db"}, namespace)
    document = SimpleNamespace(doc_id="new", text="new", metadata={})
    return namespace["build_vector_db"], [document], old, collections, events


@pytest.mark.parametrize("failure", ["add", "promote"])
def test_failed_db_rebuild_preserves_old_collection(tmp_path, failure):
    build, docs, old, collections, events = vector_builder(tmp_path, failure)
    with pytest.raises(RuntimeError):
        build(docs, tmp_path / "db")
    assert collections["scene_graph"] is old
    assert old.documents == ["old"]
    assert not any(event[0] == "delete" for event in events)


def test_successful_db_rebuild_switches_after_indexing_without_deletion(tmp_path):
    build, docs, old, collections, events = vector_builder(tmp_path)
    result = build(docs, tmp_path / "db")
    assert result.name == "scene_graph" and result.documents == ["new"]
    assert old in collections.values()
    assert not any(event[0] == "delete" for event in events)
    assert events[0][0] == "add"
