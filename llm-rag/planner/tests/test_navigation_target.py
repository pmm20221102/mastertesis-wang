"""Test 4: NavigationTarget schema and API."""

import sys
from pathlib import Path

# Add paths
sys.path.insert(0, str(Path(__file__).parent.parent))
RAG_SRC = Path(__file__).parent.parent.parent / "RAG" / "khronos_3dsg_rag" / "src"
sys.path.insert(0, str(RAG_SRC))


def test_navigation_target_schema():
    """Test that NavigationTarget schema is correct."""
    from api_schemas import NavigationTarget

    target = NavigationTarget(
        target_entity_id="chair_1",
        navigation_node_id="place_1",
        position=[1.0, 2.0, 0.0],
        frame_id="map",
        room_id="room_1",
        place_id="place_1",
        building_id="building_1",
        goal_type="place",
        position_source="parent_place",
    )

    assert target.target_entity_id == "chair_1"
    assert target.navigation_node_id == "place_1"
    assert target.position == [1.0, 2.0, 0.0]
    assert target.frame_id == "map"
    assert target.goal_type == "place"
    assert target.position_source == "parent_place"

    print("✓ test_navigation_target_schema passed")


def test_navigation_target_for_object():
    """Test navigation target for object returns parent place."""
    from canonical_index import CanonicalGraphIndex
    from scene_graph_api import SceneGraphRAG
    from api_schemas import SceneEntity
    from unittest.mock import MagicMock

    # Create mock index
    index = CanonicalGraphIndex()
    index.nodes["chair_1"] = SceneEntity(
        node_id="chair_1", node_type="objects", label="chair",
        place_id="place_1", room_id="room_1",
    )
    index.nodes["place_1"] = SceneEntity(
        node_id="place_1", node_type="places", label="place",
        position=[1.0, 2.0, 0.0],
    )

    # Create RAG with mock index
    rag = SceneGraphRAG("/mock/db")
    rag._index = index

    # Test
    target = rag.get_navigation_target("chair_1")

    assert target is not None
    assert target.target_entity_id == "chair_1"
    assert target.navigation_node_id == "place_1"
    assert target.position == [1.0, 2.0, 0.0]
    assert target.goal_type == "place"
    assert target.position_source == "parent_place"

    print("✓ test_navigation_target_for_object passed")


def test_navigation_target_for_room():
    """Test navigation target for room returns room position."""
    from canonical_index import CanonicalGraphIndex
    from scene_graph_api import SceneGraphRAG
    from api_schemas import SceneEntity

    # Create mock index
    index = CanonicalGraphIndex()
    index.nodes["room_1"] = SceneEntity(
        node_id="room_1", node_type="rooms", label="room",
        position=[5.0, 10.0, 0.0],
    )

    # Create RAG with mock index
    rag = SceneGraphRAG("/mock/db")
    rag._index = index

    # Test
    target = rag.get_navigation_target("room_1")

    assert target is not None
    assert target.target_entity_id == "room_1"
    assert target.navigation_node_id == "room_1"
    assert target.position == [5.0, 10.0, 0.0]
    assert target.goal_type == "node"
    assert target.position_source == "node_position"

    print("✓ test_navigation_target_for_room passed")


def test_navigation_target_not_found():
    """Test navigation target for nonexistent node returns None."""
    from canonical_index import CanonicalGraphIndex
    from scene_graph_api import SceneGraphRAG

    # Create RAG with empty index
    rag = SceneGraphRAG("/mock/db")
    rag._index = CanonicalGraphIndex()

    # Test
    target = rag.get_navigation_target("nonexistent")

    assert target is None

    print("✓ test_navigation_target_not_found passed")


if __name__ == "__main__":
    test_navigation_target_schema()
    test_navigation_target_for_object()
    test_navigation_target_for_room()
    test_navigation_target_not_found()
    print("\n=== All navigation target tests passed ===")
