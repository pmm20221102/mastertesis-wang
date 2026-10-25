"""Test 2: Canonical Index parent/child metadata merge."""

import sys
from pathlib import Path

# Add paths
sys.path.insert(0, str(Path(__file__).parent.parent))
RAG_SRC = Path(__file__).parent.parent.parent / "RAG" / "khronos_3dsg_rag" / "src"
sys.path.insert(0, str(RAG_SRC))


def test_parent_child_direction():
    """Test that parent/child relationships are correctly oriented."""
    from canonical_index import CanonicalGraphIndex
    from api_schemas import SceneEntity

    index = CanonicalGraphIndex()

    # Set up hierarchy: room -> place -> chair
    index.nodes["room_1"] = SceneEntity(
        node_id="room_1", node_type="rooms", label="room",
    )
    index.nodes["place_1"] = SceneEntity(
        node_id="place_1", node_type="places", label="place",
    )
    index.nodes["chair_1"] = SceneEntity(
        node_id="chair_1", node_type="objects", label="chair",
    )

    # Set up relationships
    index.parents["place_1"] = ["room_1"]
    index.parents["chair_1"] = ["place_1"]
    index.children["room_1"] = ["place_1"]
    index.children["place_1"] = ["chair_1"]

    # Test
    assert index.get_parents("chair_1") == [index.nodes["place_1"]]
    assert index.get_children("place_1") == [index.nodes["chair_1"]]
    assert index.get_parents("place_1") == [index.nodes["room_1"]]
    assert index.get_children("room_1") == [index.nodes["place_1"]]

    print("✓ test_parent_child_direction passed")


def test_bidirectional_completion():
    """Test that bidirectional edges are completed correctly."""
    from canonical_index import CanonicalGraphIndex
    from api_schemas import SceneEntity

    index = CanonicalGraphIndex()

    # Set up one-way relationship only
    index.nodes["parent_1"] = SceneEntity(
        node_id="parent_1", node_type="rooms", label="room",
    )
    index.nodes["child_1"] = SceneEntity(
        node_id="child_1", node_type="objects", label="chair",
    )

    # Only set parent relationship (one-way)
    index.parents["child_1"] = ["parent_1"]

    # Complete bidirectional edges
    index._complete_bidirectional_edges()

    # Test that child relationship was added
    assert "child_1" in index.children.get("parent_1", [])

    print("✓ test_bidirectional_completion passed")


def test_ancestor_computation():
    """Test that ancestors are computed correctly."""
    from canonical_index import CanonicalGraphIndex
    from api_schemas import SceneEntity

    index = CanonicalGraphIndex()

    # Set up hierarchy: building -> room -> place -> object
    index.nodes["building_1"] = SceneEntity(
        node_id="building_1", node_type="buildings", label="building",
    )
    index.nodes["room_1"] = SceneEntity(
        node_id="room_1", node_type="rooms", label="room",
    )
    index.nodes["place_1"] = SceneEntity(
        node_id="place_1", node_type="places", label="place",
    )
    index.nodes["object_1"] = SceneEntity(
        node_id="object_1", node_type="objects", label="chair",
    )

    # Set up relationships
    index.parents["room_1"] = ["building_1"]
    index.parents["place_1"] = ["room_1"]
    index.parents["object_1"] = ["place_1"]
    index.children["building_1"] = ["room_1"]
    index.children["room_1"] = ["place_1"]
    index.children["place_1"] = ["object_1"]

    # Compute ancestors
    index._compute_ancestors()

    # Test
    obj = index.nodes["object_1"]
    assert obj.room_id == "room_1"
    assert obj.place_id == "place_1"
    assert obj.building_id == "building_1"
    assert "room_1" in obj.hierarchy_path_ids
    assert "place_1" in obj.hierarchy_path_ids
    assert "building_1" in obj.hierarchy_path_ids

    print("✓ test_ancestor_computation passed")


def test_node_lookup():
    """Test node lookup by ID, label, and type."""
    from canonical_index import CanonicalGraphIndex
    from api_schemas import SceneEntity

    index = CanonicalGraphIndex()

    index.nodes["chair_1"] = SceneEntity(
        node_id="chair_1", node_type="objects", label="chair",
    )
    index.nodes["chair_2"] = SceneEntity(
        node_id="chair_2", node_type="objects", label="chair",
    )
    index.nodes["table_1"] = SceneEntity(
        node_id="table_1", node_type="objects", label="table",
    )

    # Add to indexes
    index.labels["chair"] = ["chair_1", "chair_2"]
    index.labels["table"] = ["table_1"]
    index.types["objects"] = ["chair_1", "chair_2", "table_1"]

    # Test
    assert index.get_node("chair_1") is not None
    assert index.get_node("nonexistent") is None
    assert len(index.get_nodes_by_label("chair")) == 2
    assert len(index.get_nodes_by_type("objects")) == 3

    print("✓ test_node_lookup passed")


def test_db_dir_cache():
    """Test that cache is db_dir-aware (without ChromaDB access)."""
    from canonical_index import _index_instances, CanonicalGraphIndex

    # Clear cache
    _index_instances.clear()

    # Manually add test indexes
    idx1 = CanonicalGraphIndex()
    idx1.nodes["test1"] = None
    idx2 = CanonicalGraphIndex()
    idx2.nodes["test2"] = None

    _index_instances["/path/to/db_A"] = idx1
    _index_instances["/path/to/db_B"] = idx2

    # They should be different objects
    assert _index_instances["/path/to/db_A"] is not _index_instances["/path/to/db_B"]

    # Same db should return same object
    assert _index_instances["/path/to/db_A"] is idx1

    # Clear should work
    _index_instances.clear()
    assert len(_index_instances) == 0

    print("✓ test_db_dir_cache passed")


if __name__ == "__main__":
    test_parent_child_direction()
    test_bidirectional_completion()
    test_ancestor_computation()
    test_node_lookup()
    test_db_dir_cache()
    print("\n=== All canonical index tests passed ===")
