import sys
import unittest
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_SCRIPTS = ROOT / "khronos-nav2-bridge" / "khronos_nav2_bridge" / "scripts"
sys.path.insert(0, str(BRIDGE_SCRIPTS))

from generate_khronos_only import (
    DEFAULT_OUTPUT,
    FLOOR_HEIGHTS,
    GRID_GEOMETRY_MANIFEST,
    MESH_LOWER_OFFSET_M,
    MESH_UPPER_OFFSET_M,
    MANUAL_TOPOLOGY_LINKS,
    compose_khronos_only_grid,
    load_grid_geometry,
    place_component_sizes,
    repair_dsg_topology,
)
from fuse_places_into_occupancy import FREE, OCCUPIED, UNKNOWN, load_nav2_map, save_nav2_map


class KhronosOnlyGridTests(unittest.TestCase):
    def test_default_output_uses_a_fresh_rebuildable_directory(self):
        self.assertEqual(DEFAULT_OUTPUT.name, "hospital-p1000-khronos-only-generated")

    def test_grid_geometry_manifest_contains_no_baseline_occupancy(self):
        geometry = load_grid_geometry(GRID_GEOMETRY_MANIFEST, 0)

        self.assertEqual(geometry, {
            "mode": "trinary",
            "image": "map.pgm",
            "width": 238,
            "height": 339,
            "resolution": 0.1,
            "origin": [-5.8, -10.4, 0.0],
            "negate": 0,
            "occupied_thresh": 0.65,
            "free_thresh": 0.196,
        })

    def test_grid_geometry_round_trips_all_checked_in_map_assets(self):
        assets = ROOT / "khronos-nav2-bridge/khronos_nav2_bridge/generated/hospital-p1000-khronos-only"
        for floor in FLOOR_HEIGHTS:
            source_yaml = assets / f"floor_{floor}/map.yaml"
            source_grid, _ = load_nav2_map(source_yaml)
            geometry = load_grid_geometry(GRID_GEOMETRY_MANIFEST, floor)
            with tempfile.TemporaryDirectory() as temporary:
                generated_yaml = save_nav2_map(Path(temporary), source_grid, geometry)
                self.assertEqual(generated_yaml.read_bytes(), source_yaml.read_bytes())
                self.assertEqual((Path(temporary) / "map.pgm").read_bytes(),
                                 (source_yaml.parent / "map.pgm").read_bytes())

    def test_floor_planes_use_khronos_global_floor_coordinates(self):
        expected = {0: -1.2, 1: 2.3, 2: 5.8, 3: 9.3}
        self.assertEqual(set(FLOOR_HEIGHTS), set(expected))
        for floor, z in expected.items():
            self.assertAlmostEqual(FLOOR_HEIGHTS[floor], z)
        self.assertEqual(MESH_LOWER_OFFSET_M, 0.05)
        self.assertEqual(MESH_UPPER_OFFSET_M, 2.0)

    def test_only_khronos_masks_define_known_cells_and_mesh_wins(self):
        place_support = [
            [False, True, True],
            [False, False, True],
        ]
        mesh_obstacles = [
            [False, False, True],
            [True, False, False],
        ]

        grid = compose_khronos_only_grid(place_support, mesh_obstacles)

        self.assertEqual(
            grid,
            [
                [UNKNOWN, FREE, OCCUPIED],
                [OCCUPIED, UNKNOWN, FREE],
            ],
        )

    def test_repair_adds_only_approved_topology_links_without_mutating_source(self):
        graph = {
            "layer_names": {"PLACES": {"layer": 3, "partition": 0}},
            "nodes": [
                {"id": "a", "layer": 3, "partition": 0,
                 "attributes": {"position": [0, 0, 0.1], "distance": 1.0}},
                {"id": "b", "layer": 3, "partition": 0,
                 "attributes": {"position": [0, 0, 0.1], "distance": 0.8}},
                {"id": "c", "layer": 3, "partition": 0,
                 "attributes": {"position": [1, 0, 0.1], "distance": 0.6}},
                {"id": "d", "layer": 3, "partition": 0,
                 "attributes": {"position": [1.2, 0, 0.1], "distance": 0.5}},
            ],
            "edges": [],
        }
        specs = [
            {"source": "a", "target": "b", "expected_separation_m": 0.0,
             "tolerance_m": 1e-6, "reason": "coincident split"},
            {"source": "c", "target": "d", "expected_separation_m": 0.2,
             "tolerance_m": 1e-6, "reason": "nearby split"},
        ]
        repaired, audit = repair_dsg_topology(graph, specs)
        repaired_twice, audit_twice = repair_dsg_topology(repaired, specs)

        self.assertEqual(len(graph["edges"]), 0)
        self.assertEqual(len(repaired["edges"]), 2)
        self.assertEqual(len(repaired_twice["edges"]), 2)
        self.assertEqual(audit["added_edges"], 2)
        self.assertEqual(audit_twice["added_edges"], 0)
        self.assertEqual(audit["place_components_before"], [1, 1, 1, 1])
        self.assertEqual(audit["place_components_after"], [2, 2])
        self.assertAlmostEqual(repaired["edges"][1]["info"]["weight"], 0.3)
        self.assertTrue(all(e["info"]["metadata"]["provenance"] == "manual_topology_override"
                            for e in repaired["edges"]))
        self.assertEqual(len(MANUAL_TOPOLOGY_LINKS), 2)


if __name__ == "__main__":
    unittest.main()
