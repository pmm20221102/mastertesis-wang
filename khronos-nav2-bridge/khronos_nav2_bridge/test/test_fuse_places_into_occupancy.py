import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from fuse_places_into_occupancy import (  # noqa: E402
    fuse_cells,
    convert_map,
    load_dsg_places,
    load_nav2_map,
    rasterize_place_support,
    rasterize_mesh_obstacles,
    save_nav2_map,
    verify_fusion,
    world_to_image_cell,
)


class FuseCellPrecedenceTest(unittest.TestCase):
    def test_place_support_only_fills_unknown_and_mesh_obstacles_win(self):
        base = [
            [100, 0, -1, -1],
            [-1, -1, -1, 0],
        ]
        place_support = [
            [True, True, True, False],
            [False, True, False, True],
        ]
        mesh_obstacles = [
            [False, False, False, False],
            [False, False, True, False],
        ]

        fused = fuse_cells(base, place_support, mesh_obstacles)

        self.assertEqual(
            fused,
            [
                [100, 0, 0, -1],
                [-1, 0, 100, 0],
            ],
        )
        self.assertEqual(base[0], [100, 0, -1, -1])

    def test_rejects_masks_with_different_dimensions(self):
        with self.assertRaises(ValueError):
            fuse_cells([[-1]], [[], []], [[False]])


class PlaceRasterizationTest(unittest.TestCase):
    ORIGIN = (-2.05, -2.05)
    RESOLUTION = 0.1
    WIDTH = 41
    HEIGHT = 41

    def test_world_coordinates_respect_origin_and_image_row_orientation(self):
        self.assertEqual(
            world_to_image_cell(
                0.0, 0.0, self.ORIGIN, self.RESOLUTION, self.WIDTH, self.HEIGHT
            ),
            (20, 20),
        )
        self.assertEqual(
            world_to_image_cell(
                0.0, 1.9, self.ORIGIN, self.RESOLUTION, self.WIDTH, self.HEIGHT
            ),
            (1, 20),
        )

    def test_place_disks_and_only_recorded_edges_create_support(self):
        nodes = {
            "a": (0.0, 0.0, 0.10),
            "b": (0.5, 0.0, 0.10),
            "c": (3.5, 0.0, 0.10),
            "upper_floor": (0.0, 1.5, 0.50),
        }
        support = rasterize_place_support(
            nodes,
            [("a", "b", 0.5), ("b", "c", 1.5)],
            self.ORIGIN,
            self.RESOLUTION,
            self.WIDTH,
            self.HEIGHT,
            floor_z=0.0,
            place_band_height=0.25,
            max_edge_weight=1.3,
            radius=0.65,
        )
        cell = lambda x, y: world_to_image_cell(  # noqa: E731
            x, y, self.ORIGIN, self.RESOLUTION, self.WIDTH, self.HEIGHT
        )
        self.assertTrue(support[cell(0.6, 0.0)[0]][cell(0.6, 0.0)[1]])
        self.assertFalse(support[cell(0.0, 0.7)[0]][cell(0.0, 0.7)[1]])
        self.assertFalse(support[cell(2.0, 0.0)[0]][cell(2.0, 0.0)[1]])
        self.assertFalse(support[cell(0.0, 1.5)[0]][cell(0.0, 1.5)[1]])

    def test_gvd_clearance_controls_each_place_disk_and_edge_tube(self):
        nodes = {
            "tight": (0.0, 0.0, 0.1),
            "wide": (1.0, 0.0, 0.1),
            "edge_a": (4.0, 0.0, 0.1),
            "edge_b": (6.0, 0.0, 0.1),
        }
        clearances = {"tight": 0.2, "wide": 0.8, "edge_a": 1.5, "edge_b": 1.5}
        origin = (-3.05, -3.05)
        width, height = 121, 81
        support = rasterize_place_support(
            nodes,
            [("edge_a", "edge_b", 1.4)],
            origin,
            self.RESOLUTION,
            width,
            height,
            floor_z=0.1,
            clearances=clearances,
        )
        cell = lambda x, y: world_to_image_cell(  # noqa: E731
            x, y, origin, self.RESOLUTION, width, height
        )

        self.assertTrue(support[cell(0.0, 0.2)[0]][cell(0.0, 0.2)[1]])
        self.assertFalse(support[cell(0.0, 0.3)[0]][cell(0.0, 0.3)[1]])
        self.assertTrue(support[cell(1.0, 0.7)[0]][cell(1.0, 0.7)[1]])
        # The segment clearance 1.4 m is used even though the old fixed edge
        # cutoff was 1.3 m; endpoints are too far away to fill this cell.
        self.assertTrue(support[cell(5.0, 1.4)[0]][cell(5.0, 1.4)[1]])
        self.assertFalse(support[cell(5.0, 1.5)[0]][cell(5.0, 1.5)[1]])

    def test_clearance_must_reach_the_requested_floor_plane(self):
        nodes = {"touching": (0.0, 0.0, 0.3), "too_high": (1.0, 0.0, 0.6)}
        support = rasterize_place_support(
            nodes, [], self.ORIGIN, self.RESOLUTION, self.WIDTH, self.HEIGHT,
            floor_z=0.0, clearances={"touching": 0.5, "too_high": 0.5},
        )
        touching = world_to_image_cell(0.0, 0.0, self.ORIGIN, self.RESOLUTION, self.WIDTH, self.HEIGHT)
        too_high = world_to_image_cell(1.0, 0.0, self.ORIGIN, self.RESOLUTION, self.WIDTH, self.HEIGHT)
        self.assertTrue(support[touching[0]][touching[1]])
        self.assertFalse(support[too_high[0]][too_high[1]])

    def test_place_clearance_is_projected_as_3d_sphere_section(self):
        nodes = {"above_floor": (0.0, 0.0, 0.3)}
        support = rasterize_place_support(
            nodes, [], self.ORIGIN, self.RESOLUTION, self.WIDTH, self.HEIGHT,
            floor_z=0.0, clearances={"above_floor": 0.5},
        )
        cell = lambda x, y: world_to_image_cell(  # noqa: E731
            x, y, self.ORIGIN, self.RESOLUTION, self.WIDTH, self.HEIGHT
        )
        # The sphere-plane intersection radius is sqrt(0.5^2 - 0.3^2) = 0.4 m.
        self.assertTrue(support[cell(0.0, 0.4)[0]][cell(0.0, 0.4)[1]])
        self.assertFalse(support[cell(0.0, 0.5)[0]][cell(0.0, 0.5)[1]])

    def test_cross_height_edge_is_projected_by_3d_capsule_intersection(self):
        nodes = {"lower": (0.0, 0.0, -0.5), "upper": (0.0, 0.0, 0.5)}
        support = rasterize_place_support(
            nodes, [("lower", "upper", 0.4)], self.ORIGIN, self.RESOLUTION,
            self.WIDTH, self.HEIGHT, floor_z=0.0,
            clearances={"lower": 0.6, "upper": 0.6},
        )
        cell = lambda x, y: world_to_image_cell(  # noqa: E731
            x, y, self.ORIGIN, self.RESOLUTION, self.WIDTH, self.HEIGHT
        )
        # The 3D segment crosses z=0, so its bottleneck tube reaches this floor plane.
        self.assertTrue(support[cell(0.3, 0.0)[0]][cell(0.3, 0.0)[1]])
        self.assertFalse(support[cell(0.5, 0.0)[0]][cell(0.5, 0.0)[1]])


class PgmMapIoTest(unittest.TestCase):
    YAML_TEXT = """image: map.pgm
mode: trinary
resolution: 0.1
origin: [-1.0, 2.0, 0.0]
negate: 0
occupied_thresh: 0.65
free_thresh: 0.196
"""

    def test_reads_binary_p5_using_nav2_trinary_thresholds(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "map.yaml").write_text(self.YAML_TEXT, encoding="utf-8")
            (folder / "map.pgm").write_bytes(b"P5\n2 2\n255\n" + bytes([0, 205, 254, 254]))

            grid, metadata = load_nav2_map(folder / "map.yaml")

        self.assertEqual(grid, [[100, -1], [0, 0]])
        self.assertEqual((metadata["width"], metadata["height"]), (2, 2))
        self.assertEqual(metadata["origin"], [-1.0, 2.0, 0.0])

    def test_ascii_p2_and_saved_p5_round_trip_preserve_orientation(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            output = Path(temporary) / "output"
            source.mkdir()
            (source / "map.yaml").write_text(self.YAML_TEXT, encoding="utf-8")
            (source / "map.pgm").write_text(
                "P2\n2 2\n255\n0 205\n254 0\n", encoding="ascii"
            )
            grid, metadata = load_nav2_map(source / "map.yaml")
            save_nav2_map(output, grid, metadata)
            restored, restored_metadata = load_nav2_map(output / "map.yaml")

        self.assertEqual(grid, [[100, -1], [0, 100]])
        self.assertEqual(restored, grid)
        self.assertEqual(restored_metadata["origin"], metadata["origin"])
        self.assertEqual(restored_metadata["resolution"], metadata["resolution"])


class KhronosInputTest(unittest.TestCase):
    def test_loads_only_place_nodes_and_their_recorded_edges(self):
        graph = {
            "nodes": [
                {"id": "p1", "layer": 3, "attributes": {"position": [0, 0, 0.1]}},
                {"id": "p2", "layer": 3, "attributes": {"position": [1, 0, 0.1]}},
                {"id": "mp", "layer": 3, "partition": 1,
                 "attributes": {"position": [3, 0, 0.1]}},
                {"id": "o1", "layer": 4, "attributes": {"position": [2, 0, 0.1]}},
            ],
            "edges": [
                {"source": "p1", "target": "p2", "info": {"weight": 1.0}},
                {"source": "p1", "target": "mp", "info": {"weight": 0.5}},
                {"source": "p1", "target": "o1", "info": {"weight": 0.5}},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "dsg.json"
            path.write_text(__import__("json").dumps(graph), encoding="utf-8")
            nodes, edges = load_dsg_places(path)

        self.assertEqual(set(nodes), {"p1", "p2"})
        self.assertEqual(edges, [("p1", "p2", 1.0)])

    def test_loads_place_clearances_without_including_mesh_places(self):
        graph = {
            "layer_names": {"PLACES": {"layer": 3, "partition": 0}},
            "nodes": [
                {"id": "p1", "layer": 3, "partition": 0,
                 "attributes": {"position": [0, 0, 0.1], "distance": 0.4}},
                {"id": "p2", "layer": 3, "partition": 0,
                 "attributes": {"position": [1, 0, 0.1], "distance": 1.2}},
                {"id": "mp", "layer": 3, "partition": 1,
                 "attributes": {"position": [2, 0, 0.1], "distance": 3.0}},
            ],
            "edges": [
                {"source": "p1", "target": "p2", "info": {"weight": 0.3}},
                {"source": "p1", "target": "mp", "info": {"weight": 0.2}},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "dsg.json"
            path.write_text(__import__("json").dumps(graph), encoding="utf-8")
            nodes, edges, clearances = load_dsg_places(path, include_clearances=True)

        self.assertEqual(set(nodes), {"p1", "p2"})
        self.assertEqual(clearances, {"p1": 0.4, "p2": 1.2})
        self.assertEqual(edges, [("p1", "p2", 0.3)])

    def test_projects_only_mesh_points_in_the_floor_obstacle_band(self):
        ply = (
            "ply\n"
            "format ascii 1.0\n"
            "element vertex 4\n"
            "property uchar red\n"
            "property float x\n"
            "property float y\n"
            "property float z\n"
            "end_header\n"
            "1 0.0 0.0 0.1\n"
            "2 0.1 0.0 0.5\n"
            "3 0.2 0.0 2.0\n"
            "4 20.0 0.0 0.5\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mesh.ply"
            path.write_text(ply, encoding="ascii")
            mask, stats = rasterize_mesh_obstacles(
                path,
                origin=(-1.0, -1.0),
                resolution=0.1,
                width=20,
                height=20,
                floor_z=0.0,
                lower_offset=0.18,
                upper_offset=1.8,
            )

        self.assertEqual(stats["points_in_band"], 2)
        self.assertEqual(stats["points_clipped"], 1)
        self.assertEqual(stats["occupied_cells"], 1)
        row, column = world_to_image_cell(0.1, 0.0, (-1.0, -1.0), 0.1, 20, 20)
        self.assertTrue(mask[row][column])


class ConversionReportTest(unittest.TestCase):
    def test_generated_map_report_is_accepted_by_nav2_planner_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "base"
            base.mkdir()
            (base / "map.pgm").write_bytes(b"P5\n8 8\n255\n" + bytes([205] * 64))
            (base / "map.yaml").write_text(
                "image: map.pgm\nmode: trinary\nresolution: 0.1\n"
                "origin: [-0.2, -0.2, 0.0]\nnegate: 0\n"
                "occupied_thresh: 0.65\nfree_thresh: 0.196\n",
                encoding="utf-8",
            )
            dsg = root / "dsg.json"
            dsg.write_text(
                __import__("json").dumps({
                    "layer_names": {"PLACES": {"layer": 3, "partition": 0}},
                    "nodes": [{"id": "p", "layer": 3, "partition": 0,
                               "attributes": {"position": [0.1, 0.1, 0.1]}}],
                    "edges": [],
                }),
                encoding="utf-8",
            )
            mesh = root / "mesh.ply"
            mesh.write_text(
                "ply\nformat ascii 1.0\nelement vertex 1\n"
                "property float x\nproperty float y\nproperty float z\nend_header\n"
                "0.1 0.1 0.5\n",
                encoding="ascii",
            )
            output = root / "fused"
            report = convert_map(
                base / "map.yaml", mesh, dsg, output,
                floor=0, floor_z=0.0, support_radius=0.15,
            )

            sys.path.insert(0, str(SCRIPT_DIR))
            from plan_candidate_task_chain import load_floor_report

            loaded = load_floor_report(output / "report.json")
            verification = verify_fusion(
                base / "map.yaml", output / "map.yaml", mesh, dsg,
                floor=0, floor_z=0.0, support_radius=0.15,
            )

        self.assertEqual(loaded["floor"], 0)
        self.assertEqual(report["parameters"]["width"], 8)
        self.assertTrue(report["invariants"]["mesh_obstacles_occupied"])
        self.assertTrue(verification["map_pixels_match_expected_fusion"])

    def test_refuses_to_overwrite_non_empty_output_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "keep.txt").write_text("preserve", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                convert_map("missing.yaml", "missing.ply", "missing.json", output,
                            floor=0, floor_z=0.0)


if __name__ == "__main__":
    unittest.main()
