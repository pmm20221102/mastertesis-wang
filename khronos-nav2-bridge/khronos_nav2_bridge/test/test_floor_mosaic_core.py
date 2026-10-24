from pathlib import Path
import hashlib
import sys

import yaml


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))
from floor_mosaic_core import (
    FloorGrid,
    extract_room_labels,
    compose_floor_mosaic,
    floor_occupancy_matches,
    identify_floor_by_geometry,
    load_floor_grids,
    map_pose_to_mosaic,
    map_grid_origin_in_mosaic,
    mission_floor_sequence,
    room_label_to_mosaic,
    as_rgb_float,
)


def test_four_floor_mosaic_places_tiles_in_fixed_quadrants_and_unknown_gutters():
    floors = {
        0: FloorGrid(0, 2, 2, 0.1, -1.0, -2.0, (1, 2, 3, 4)),
        1: FloorGrid(1, 1, 2, 0.1, 10.0, 20.0, (5, 6)),
        2: FloorGrid(2, 3, 1, 0.1, 30.0, 40.0, (7, 8, 9)),
        3: FloorGrid(3, 1, 1, 0.1, 50.0, 60.0, (10,)),
    }

    mosaic = compose_floor_mosaic(floors, gap_cells=1, panel_width_cells=6)

    assert (mosaic.tile_width, mosaic.tile_height) == (4, 2)
    assert (mosaic.width, mosaic.height, mosaic.resolution) == (16, 5, 0.1)
    assert (mosaic.placements[0].cell_x, mosaic.placements[0].cell_y) == (0, 3)
    assert (mosaic.placements[1].cell_x, mosaic.placements[1].cell_y) == (5, 3)
    assert (mosaic.placements[2].cell_x, mosaic.placements[2].cell_y) == (0, 0)
    assert (mosaic.placements[3].cell_x, mosaic.placements[3].cell_y) == (5, 0)
    assert (mosaic.panel_x, mosaic.panel_width) == (10, 6)
    assert abs(mosaic.tile_width / mosaic.tile_height - 16 / 9) < 0.25

    at = lambda x, y: mosaic.data[y * mosaic.width + x]
    assert [at(1, 3), at(2, 3), at(1, 4), at(2, 4)] == [1, 2, 3, 4]
    assert [at(6, 3), at(6, 4)] == [5, 6]
    assert [at(0, 0), at(1, 0), at(2, 0)] == [7, 8, 9]
    assert at(6, 0) == 10
    assert at(4, 3) == -1
    assert at(4, 1) == -1
    assert all(at(x, y) == 0 for x in range(mosaic.panel_x, mosaic.width)
               for y in range(mosaic.height))


def test_map_pose_uses_source_origin_then_tile_offset():
    floor = FloorGrid(3, 1, 1, 0.1, 50.0, 60.0, (0,))
    mosaic = compose_floor_mosaic(
        {
            0: FloorGrid(0, 2, 2, 0.1, 0.0, 0.0, (0,) * 4),
            1: FloorGrid(1, 1, 2, 0.1, 0.0, 0.0, (0,) * 2),
            2: FloorGrid(2, 3, 1, 0.1, 0.0, 0.0, (0,) * 3),
            3: floor,
        },
        gap_cells=1,
    )

    assert map_pose_to_mosaic((50.5, 60.5), floor, mosaic.placements[3], mosaic) == (
        1.1, 0.5
    )
    grid_origin = map_grid_origin_in_mosaic(floor, mosaic.placements[3], mosaic)
    assert abs(grid_origin[0] - 0.6) < 1e-9 and grid_origin[1] == 0.0


def test_hc031_route_starts_at_fixed_f0_and_expands_adjacent_floor_transitions():
    transitions = []
    for source, target in ((0, 1), (1, 0), (1, 2), (2, 1), (2, 3), (3, 2)):
        transitions.append({
            "transition_id": f"F{source}_F{target}",
            "from_floor": source,
            "to_floor": target,
            "entry_pose": {"x": 0, "y": 0, "yaw": 0},
            "exit_pose": {"x": 0, "y": 0, "yaw": 0},
        })
    bundle = {
        "schema_version": "hospital-floor-transitions-v1",
        "map_bundle_id": "tiny-test", "frame_id": "map",
        "floors": {str(f): {
            "report_path": f"/map/f{f}/report.json",
            "map_yaml_path": f"/map/f{f}/map.yaml",
            "map_yaml_sha256": "a" * 64,
            "map_pgm_path": f"/map/f{f}/map.pgm",
            "map_pgm_sha256": "b" * 64,
        } for f in range(4)},
        "transitions": transitions,
    }
    mission = {
        "schema_version": "hospital-multifloor-mission-v1",
        "task_id": "HC-031-F0-DEMO", "initial_floor": 0,
        "initial_pose": {"x": 14.85, "y": 3.65, "yaw": 0, "frame_id": "map"},
        "stops": [
            {"stop_index": i, "floor_id": floor,
             "floor_assignment_source": "fixture",
             "candidate_targets": [{
                 "target_id": str(i), "floor_id": floor,
                 "floor_assignment_source": "fixture",
                 "navigation_target": {"frame_id": "map", "position": [0, 0, 0],
                     "floor_id": floor, "floor_assignment_source": "fixture"},
             }]} for i, floor in enumerate((3, 2, 1))
        ],
    }

    assert mission_floor_sequence(mission, bundle) == [0, 1, 2, 3, 2, 1]


def test_room_labels_are_deterministic_and_project_f35_onto_f3():
    dsg = {
        "layer_names": {"ROOMS": {"layer": 4}},
        "nodes": [
            {"id": 22, "layer": 4,
             "attributes": {"position": [1.0, 2.0, 12.45], "name": ""}},
            {"id": 11, "layer": 4,
             "attributes": {"position": [3.0, 4.0, 9.4], "name": ""}},
        ],
    }

    labels = extract_room_labels(dsg, {0: -1.2, 1: 2.3, 2: 5.8, 3: 9.3})

    assert [(room.node_id, room.label, room.floor_id, room.half_level)
            for room in labels] == [
                (11, "R01", 3, False), (22, "R02", 3, True)
            ]


def test_room_labels_snap_to_nearest_free_cell_and_follow_tile_content_offset():
    floors = {
        0: FloorGrid(0, 2, 2, 1.0, 0.0, 0.0, (0, 0, 100, 100)),
        1: FloorGrid(1, 2, 2, 1.0, 0.0, 0.0, (0,) * 4),
        2: FloorGrid(2, 2, 2, 1.0, 0.0, 0.0, (0,) * 4),
        3: FloorGrid(3, 2, 2, 1.0, 0.0, 0.0, (0,) * 4),
    }
    mosaic = compose_floor_mosaic(floors, gap_cells=1, panel_width_cells=4)
    room = extract_room_labels({
        "layer_names": {"ROOMS": {"layer": 4}},
        "nodes": [{"id": 1, "layer": 4,
                   "attributes": {"position": [0.25, 0.25, -1.2]}}],
    }, {0: -1.2, 1: 2.3, 2: 5.8, 3: 9.3})[0]

    point = room_label_to_mosaic(room, floors, mosaic)

    assert point == (1.5, 3.5)


def test_black_marker_rgb_is_always_encoded_as_float_channels():
    black = as_rgb_float((0, 0, 0))
    assert black == (0.0, 0.0, 0.0)
    assert all(type(channel) is float for channel in black)


def test_map_loader_matches_nav2_trinary_thresholds_and_vertical_flip(tmp_path):
    bundle = _make_bundle(tmp_path)

    floors = load_floor_grids(bundle)

    assert floors[0].occupancy == (-1, 0, 100, 0)
    assert floors[0].resolution == 0.1
    assert (floors[0].origin_x, floors[0].origin_y) == (-10.0, -20.0)


def test_active_floor_identification_requires_one_exact_geometry_match():
    floors = {
        0: FloorGrid(0, 2, 2, 0.1, -10.0, -20.0, (0,) * 4),
        1: FloorGrid(1, 2, 2, 0.1, -30.0, -40.0, (0,) * 4),
        2: FloorGrid(2, 2, 2, 0.1, -50.0, -60.0, (0,) * 4),
        3: FloorGrid(3, 2, 2, 0.1, -70.0, -80.0, (0,) * 4),
    }

    assert identify_floor_by_geometry(
        {"width": 2, "height": 2, "resolution": 0.1,
         "origin_x": -30.0, "origin_y": -40.0}, floors
    ) == 1
    assert identify_floor_by_geometry(
        {"width": 2, "height": 2, "resolution": 0.1,
         "origin_x": -5.0, "origin_y": -4.0}, floors
    ) is None
    ambiguous = dict(floors)
    ambiguous[2] = FloorGrid(2, 2, 2, 0.1, -30.0, -40.0, (0,) * 4)
    assert identify_floor_by_geometry(
        {"width": 2, "height": 2, "resolution": 0.1,
         "origin_x": -30.0, "origin_y": -40.0}, ambiguous
    ) is None


def test_live_map_cells_must_exactly_match_the_verified_floor_raster():
    floor = FloorGrid(0, 2, 2, 0.1, 0.0, 0.0, (-1, 0, 100, 0))
    assert floor_occupancy_matches(floor, [-1, 0, 100, 0])
    assert not floor_occupancy_matches(floor, [-1, 0, 99, 0])
    assert not floor_occupancy_matches(floor, [-1, 0])
def _make_bundle(root: Path):
    pixels = b"P5\n2 2\n255\n" + bytes([0, 255, 128, 255])
    floors = {}
    for floor in range(4):
        directory = root / f"f{floor}"
        directory.mkdir()
        pgm = directory / "map.pgm"
        pgm.write_bytes(pixels)
        yaml_path = directory / "map.yaml"
        metadata = {
            "image": "map.pgm", "mode": "trinary", "resolution": 0.1,
            "origin": [-10.0 - 20.0 * floor, -20.0 - 20.0 * floor, 0.0],
            "negate": 0, "occupied_thresh": 0.65, "free_thresh": 0.196,
        }
        yaml_path.write_text(yaml.safe_dump(metadata), encoding="utf-8")
        report = directory / "report.json"
        report.write_text("{}\n", encoding="utf-8")
        floors[str(floor)] = {
            "floor_z_m": floor * 3.5,
            "report_path": str(report),
            "map_yaml_path": str(yaml_path),
            "map_yaml_sha256": hashlib.sha256(yaml_path.read_bytes()).hexdigest(),
            "map_pgm_path": str(pgm),
            "map_pgm_sha256": hashlib.sha256(pgm.read_bytes()).hexdigest(),
        }
    edge = {
        "transition_id": "F0_F1", "from_floor": 0, "to_floor": 1,
        "entry_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "exit_pose": {"x": 1.0, "y": 1.0, "yaw": 0.0},
    }
    return {
        "schema_version": "hospital-floor-transitions-v1",
        "map_bundle_id": "tiny-test", "frame_id": "map",
        "floors": floors, "transitions": [edge],
    }


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
