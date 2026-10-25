"""Offline exact-output and deterministic scan bounds for edge completion."""

import ast
import copy
import hashlib
import importlib.machinery
import importlib.util
import random
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "RAG/khronos_3dsg_rag/src"))

from api_schemas import SceneEntity
from canonical_index import CanonicalGraphIndex

# Frozen, byte-for-byte pre-change source; importing only defines classes/functions.
loader = importlib.machinery.SourceFileLoader(
    "canonical_index_legacy", str(ROOT / "planner/tests/fixtures/canonical_index_pre_optimization.py")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
legacy_module = importlib.util.module_from_spec(spec)
loader.exec_module(legacy_module)
legacy_complete = legacy_module.CanonicalGraphIndex._complete_bidirectional_edges


def test_only_completion_body_changed_and_backup_is_original():
    original = (ROOT / "planner/tests/fixtures/canonical_index_pre_optimization.py").read_bytes()
    assert hashlib.sha256(original).hexdigest() == (
        "47dc83a0f45f537e2ace84307f6e9be6e276c605542a0bb3715eafdcc179c4e8"
    )
    trees = [ast.parse(original), ast.parse(
        (ROOT / "RAG/khronos_3dsg_rag/src/canonical_index.py").read_bytes()
    )]
    for tree in trees:
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "CanonicalGraphIndex")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                      and n.name == "_complete_bidirectional_edges")
        method.body = []
    assert ast.dump(trees[0]) == ast.dump(trees[1])


def snapshot(index):
    # Include dict insertion order, list order, duplicate entries and entity fields.
    return (
        [(name, list(getattr(index, name).items()))
         for name in ("parents", "children", "neighbors", "labels", "types")],
        [(nid, asdict(entity)) for nid, entity in index.nodes.items()],
        tuple(index.__dict__),
        index._loaded,
    )


def check_equivalence(index):
    legacy = copy.deepcopy(index)
    original_lists = {
        (name, nid): ids for name in ("parents", "children", "neighbors")
        for nid, ids in getattr(index, name).items()
    }
    legacy_complete(legacy)
    index._complete_bidirectional_edges()
    assert snapshot(index) == snapshot(legacy)
    for (name, nid), ids in original_lists.items():
        assert getattr(index, name)[nid] is ids
    legacy._compute_ancestors()
    index._compute_ancestors()
    assert snapshot(index) == snapshot(legacy)
    # Calls rebuild membership from current lists, including external edits.
    index.parents["external"].extend(["room_second", "room_second"])
    legacy.parents["external"].extend(["room_second", "room_second"])
    index.neighbors["external"].append("dangling")
    legacy.neighbors["external"].append("dangling")
    legacy_complete(legacy)
    index._complete_bidirectional_edges()
    assert snapshot(index) == snapshot(legacy)
    before = copy.deepcopy(snapshot(index))
    index._complete_bidirectional_edges()
    assert snapshot(index) == before


@pytest.mark.parametrize("seed", range(100))
def test_generated_graph_exact_output(seed):
    rng = random.Random(seed)
    index = CanonicalGraphIndex()
    ids = [f"n{i}" for i in range(12)]
    for i, nid in enumerate(ids):
        index.nodes[nid] = SceneEntity(
            node_id=nid, node_type=("objects", "places", "rooms", "buildings")[i % 4],
            label="fixture", hierarchy_path_ids=["existing"],
        )
    for name in ("parents", "children", "neighbors"):
        for nid in rng.sample(ids + ["dangling"], rng.randrange(14)):
            getattr(index, name)[nid] = [
                rng.choice(ids + ["dangling"]) for _ in range(rng.randrange(8))
            ]
    check_equivalence(index)


def test_order_duplicates_first_parent_and_missing_nodes():
    index = CanonicalGraphIndex()
    for nid, kind in (("obj", "objects"), ("room_first", "rooms"),
                      ("room_second", "rooms")):
        index.nodes[nid] = SceneEntity(node_id=nid, node_type=kind, label="fixture")
    index.parents["obj"] = ["room_first", "room_first", "room_second", "missing"]
    index.children["room_second"] = ["other", "obj", "obj"]
    index.neighbors["obj"] = ["obj", "missing", "missing"]
    index.neighbors["empty"] = []
    check_equivalence(index)
    assert index.parents["obj"] == ["room_first", "room_first", "room_second", "missing"]
    assert index.children["room_second"] == ["other", "obj", "obj", "external"]
    assert index.nodes["obj"].room_id == "room_first"


def test_empty_graph_exact_output():
    check_equivalence(CanonicalGraphIndex())


class ScanList(list):
    """Count comparisons in membership scans and elements consumed by iteration."""
    probes = 0
    visits = 0

    def __contains__(self, value):
        for item in list.__iter__(self):
            type(self).probes += 1
            if item == value:
                return True
        return False

    def __iter__(self):
        for item in list.__iter__(self):
            type(self).visits += 1
            yield item


@pytest.mark.parametrize("direction", ["parents", "children", "neighbors"])
@pytest.mark.parametrize("degree", [32, 128, 512])
def test_high_degree_completion_has_linear_scan_bound(direction, degree):
    def fixture():
        index = CanonicalGraphIndex()
        leaves = [f"leaf{i}" for i in range(degree)]
        opposite = {"parents": "children", "children": "parents",
                    "neighbors": "neighbors"}[direction]
        for leaf in leaves:
            getattr(index, direction)[leaf] = ScanList(["hub", "hub"])
        getattr(index, opposite)["hub"] = ScanList(leaves + leaves[:3])
        return index

    legacy, optimized = fixture(), fixture()
    ScanList.probes = ScanList.visits = 0
    legacy_complete(legacy)
    legacy_probes = ScanList.probes
    assert legacy_probes >= degree * (degree - 1) // 2
    ScanList.probes = ScanList.visits = 0
    optimized._complete_bidirectional_edges()
    probes, visits = ScanList.probes, ScanList.visits
    assert snapshot(optimized) == snapshot(legacy)
    assert probes == 0, f"legacy={legacy_probes}, optimized={probes} list comparisons"
    assert visits <= 12 * degree + 24, f"{visits} iteration visits for degree={degree}"


@pytest.mark.parametrize("version", [1, 2])
def test_json_fixture_loading_keeps_existing_semantics(version, tmp_path):
    import json

    data = {
        "schema_version": version,
        "nodes": {"obj": {"node_type": "object", "label": "chair"}},
        "edges": ({"parents": {"obj": ["missing", "missing"]}}
                  if version == 1 else {"hierarchy": [{"parent": "missing", "child": "obj"}]}),
    }
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    current = CanonicalGraphIndex()
    legacy = legacy_module.CanonicalGraphIndex()
    current.load_from_json(path)
    legacy.load_from_json(path)
    assert snapshot(current) == snapshot(legacy)
    # Loaded guard still ignores subsequent fixture changes.
    path.write_text('{"nodes": {}}', encoding="utf-8")
    current.load_from_json(path)
    legacy.load_from_json(path)
    assert snapshot(current) == snapshot(legacy)
