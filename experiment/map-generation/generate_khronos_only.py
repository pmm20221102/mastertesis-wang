#!/usr/bin/env python3
"""Build Khronos-only trinary maps from Khronos Places and mesh evidence.

The checked-in grid geometry manifest supplies only floor extents, resolution,
and origin. No depth-map occupancy pixels are read by this generator.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_SCRIPTS = (
    ROOT / "khronos-nav2-bridge" / "khronos_nav2_bridge" / "scripts"
)
sys.path.insert(0, str(BRIDGE_SCRIPTS))

from fuse_places_into_occupancy import (  # noqa: E402
    FREE,
    OCCUPIED,
    UNKNOWN,
    fuse_cells,
    load_dsg_places,
    load_nav2_map,
    rasterize_mesh_obstacles,
    rasterize_place_support,
    save_nav2_map,
    sha256_file,
)


GRID_GEOMETRY_MANIFEST = Path(__file__).resolve().with_name("grid_geometry.json")
KHRONOS_ROOT = (
    ROOT
    / "datasets"
    / "Hospital-workstation-backup-20261006"
    / "tartanground_khronos"
    / "reports"
    / "khronos_output"
    / "fine_full_p1000"
)
FLOOR_HEIGHTS = {0: -1.2, 1: 2.3, 2: 5.8, 3: 9.3}
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent / "output"
    / "hospital-p1000-khronos-only-generated"
)

CLEARANCE_MARGIN_M = 0.0
MESH_LOWER_OFFSET_M = 0.05
MESH_UPPER_OFFSET_M = 2.0


def load_grid_geometry(manifest_path: Path, floor: int) -> dict:
    """Load one floor's geometry-only raster contract, never occupancy values."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict)
            or manifest.get("schema_version") != "hospital-nav2-grid-geometry-v1"
            or manifest.get("occupancy_values_consumed") is not False):
        raise ValueError("unsupported grid-geometry manifest or occupancy policy")
    floors = manifest.get("floors")
    entry = floors.get(str(floor)) if isinstance(floors, dict) else None
    if not isinstance(entry, dict):
        raise ValueError(f"grid geometry is missing floor {floor}")
    encoding = manifest.get("map_encoding")
    if (not isinstance(encoding, dict) or encoding.get("mode") != "trinary"
            or not isinstance(encoding.get("image"), str)
            or not encoding["image"].strip()):
        raise ValueError("grid geometry manifest has an invalid trinary map encoding")
    width, height = entry.get("width"), entry.get("height")
    if (isinstance(width, bool) or not isinstance(width, int) or width <= 0
            or isinstance(height, bool) or not isinstance(height, int) or height <= 0):
        raise ValueError(f"floor {floor} width and height must be positive integers")
    try:
        resolution = float(entry["resolution_m"])
        origin = [float(value) for value in entry["origin"]]
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"floor {floor} geometry values are invalid") from error
    if (not math.isfinite(resolution) or resolution <= 0 or len(origin) != 3
            or any(not math.isfinite(value) for value in origin)):
        raise ValueError(f"floor {floor} resolution and origin must be finite")
    negate = encoding.get("negate")
    try:
        occupied_threshold = float(encoding["occupied_thresh"])
        free_threshold = float(encoding["free_thresh"])
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError("grid geometry manifest has invalid occupancy thresholds") from error
    if (isinstance(negate, bool) or negate not in (0, 1)
            or not math.isfinite(occupied_threshold) or not math.isfinite(free_threshold)
            or not 0 <= free_threshold < occupied_threshold <= 1):
        raise ValueError("grid geometry manifest has unsupported trinary encoding values")
    return {"image": encoding["image"], "mode": encoding["mode"],
            "resolution": resolution, "origin": origin, "negate": negate,
            "occupied_thresh": occupied_threshold, "free_thresh": free_threshold,
            "width": width, "height": height}

MANUAL_TOPOLOGY_LINKS = [
    {
        "source": 8070450532247983547,
        "target": 8070450532247988661,
        "expected_separation_m": 0.0,
        "tolerance_m": 1e-5,
        "reason": "user-approved link between coincident Place nodes from components 1 and 3",
    },
    {
        "source": 8070450532247941822,
        "target": 8070450532247938064,
        "expected_separation_m": 0.2,
        "tolerance_m": 0.02,
        "reason": "user-approved link between Place nodes from components 1 and 2 at 0.2 m",
    },
]


def place_component_sizes(dsg_data):
    """Return PLACES-only undirected component sizes, largest first."""
    layer = dsg_data.get("layer_names", {}).get("PLACES", {"layer": 3, "partition": 0})
    place_ids = set()
    for node in dsg_data.get("nodes", []):
        is_place = node.get("layer_name") == "PLACES" or (
            str(node.get("layer")) == str(layer.get("layer", 3))
            and str(node.get("partition", 0)) == str(layer.get("partition", 0))
        )
        if is_place and node.get("id") is not None:
            place_ids.add(node["id"])
    parent = {node_id: node_id for node_id in place_ids}
    sizes = {node_id: 1 for node_id in place_ids}

    def find(node_id):
        while parent[node_id] != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    for edge in dsg_data.get("edges", []):
        source, target = edge.get("source"), edge.get("target")
        if source not in place_ids or target not in place_ids or source == target:
            continue
        root_a, root_b = find(source), find(target)
        if root_a == root_b:
            continue
        if sizes[root_a] < sizes[root_b]:
            root_a, root_b = root_b, root_a
        parent[root_b] = root_a
        sizes[root_a] += sizes[root_b]
    counts = {}
    for node_id in place_ids:
        root = find(node_id)
        counts[root] = counts.get(root, 0) + 1
    return sorted(counts.values(), reverse=True)


def repair_dsg_topology(dsg_data, link_specs=None):
    """Return a copied DSG with explicitly audited Place-to-Place link overrides."""
    derived = copy.deepcopy(dsg_data)
    layer = derived.get("layer_names", {}).get("PLACES", {"layer": 3, "partition": 0})
    place_nodes = {}
    for node in derived.get("nodes", []):
        is_place = node.get("layer_name") == "PLACES" or (
            str(node.get("layer")) == str(layer.get("layer", 3))
            and str(node.get("partition", 0)) == str(layer.get("partition", 0))
        )
        if not is_place:
            continue
        attributes = node.get("attributes", {})
        position = attributes.get("position") or attributes.get("world_position")
        distance = attributes.get("distance")
        if (not isinstance(position, (list, tuple)) or len(position) < 3
                or distance is None
                or not all(math.isfinite(float(value)) for value in position[:3])
                or not math.isfinite(float(distance)) or float(distance) < 0):
            continue
        place_nodes[node["id"]] = {
            "position": tuple(map(float, position[:3])),
            "distance": float(distance),
        }
    if not place_nodes:
        raise ValueError("DSG has no PLACES nodes with positions and GVD distances")

    existing = {
        frozenset((edge.get("source"), edge.get("target")))
        for edge in derived.get("edges", [])
    }
    components_before = place_component_sizes(dsg_data)
    audit = []
    for spec in (MANUAL_TOPOLOGY_LINKS if link_specs is None else link_specs):
        source, target = spec["source"], spec["target"]
        if source not in place_nodes or target not in place_nodes:
            raise ValueError(f"manual topology link endpoints are not PLACES: {source}, {target}")
        if source == target:
            raise ValueError("manual topology link cannot be a self-edge")
        source_pos = place_nodes[source]["position"]
        target_pos = place_nodes[target]["position"]
        separation = math.dist(source_pos, target_pos)
        expected = float(spec["expected_separation_m"])
        tolerance = float(spec["tolerance_m"])
        if abs(separation - expected) > tolerance:
            raise ValueError(
                f"manual link {source}-{target} separation changed: "
                f"{separation:.6f} m vs expected {expected:.6f} +/- {tolerance:.6f} m"
            )
        key = frozenset((source, target))
        already_exists = key in existing
        clearance_lower_bound = max(
            0.0,
            min(place_nodes[source]["distance"], place_nodes[target]["distance"]) - separation,
        )
        if not already_exists:
            derived.setdefault("edges", []).append({
                "source": source,
                "target": target,
                "info": {
                    "metadata": {
                        "provenance": "manual_topology_override",
                        "reason": spec["reason"],
                        "clearance_estimate": (
                            "max(0, min(endpoint Place.distance) - endpoint separation); "
                            "conservative estimate, not a sampled GVD edge"
                        ),
                    },
                    "type": "EdgeAttributes",
                    "weight": clearance_lower_bound,
                    "weighted": True,
                },
            })
            existing.add(key)
        audit.append({
            "source": source,
            "target": target,
            "reason": spec["reason"],
            "endpoint_separation_m": separation,
            "edge_clearance_estimate_m": clearance_lower_bound,
            "added": not already_exists,
        })
    return derived, {
        "requested_links": audit,
        "added_edges": sum(item["added"] for item in audit),
        "place_components_before": components_before,
        "place_components_after": place_component_sizes(derived),
    }


def compose_khronos_only_grid(place_support, mesh_obstacles):
    """Create a grid whose known cells come only from Khronos support masks."""
    if len(place_support) != len(mesh_obstacles):
        raise ValueError("Place and mesh masks must have equal heights")
    if not place_support:
        raise ValueError("Khronos masks must be non-empty")
    width = len(place_support[0])
    if width <= 0:
        raise ValueError("Khronos masks must have positive width")
    if any(len(row) != width for row in place_support + mesh_obstacles):
        raise ValueError("Khronos masks must be rectangular and have equal widths")
    unknown_grid = [[UNKNOWN] * width for _ in range(len(place_support))]
    return fuse_cells(unknown_grid, place_support, mesh_obstacles)


def _counts(grid):
    counts = Counter(cell for row in grid for cell in row)
    return {
        "unknown": int(counts[UNKNOWN]),
        "free": int(counts[FREE]),
        "occupied": int(counts[OCCUPIED]),
    }


def _mask_count(mask):
    return sum(sum(bool(cell) for cell in row) for row in mask)


def build_floor(floor: int, floor_z: float, mesh: Path, dsg: Path,
                original_dsg: Path, topology_audit: dict, out_root: Path):
    output_dir = out_root / f"floor_{floor}"
    if not GRID_GEOMETRY_MANIFEST.is_file():
        raise FileNotFoundError(f"grid geometry manifest not found: {GRID_GEOMETRY_MANIFEST}")
    if not mesh.is_file() or not dsg.is_file():
        raise FileNotFoundError("Khronos mesh.ply and dsg.json must both exist")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty output: {output_dir}")

    metadata = load_grid_geometry(GRID_GEOMETRY_MANIFEST, floor)
    nodes, edges, clearances = load_dsg_places(dsg, include_clearances=True)
    place_support = rasterize_place_support(
        nodes,
        edges,
        metadata["origin"],
        metadata["resolution"],
        metadata["width"],
        metadata["height"],
        floor_z=floor_z,
        clearances=clearances,
        clearance_margin_m=CLEARANCE_MARGIN_M,
    )
    mesh_obstacles, mesh_stats = rasterize_mesh_obstacles(
        mesh,
        origin=metadata["origin"],
        resolution=metadata["resolution"],
        width=metadata["width"],
        height=metadata["height"],
        floor_z=floor_z,
        lower_offset=MESH_LOWER_OFFSET_M,
        upper_offset=MESH_UPPER_OFFSET_M,
    )
    grid = compose_khronos_only_grid(place_support, mesh_obstacles)

    output_dir.mkdir(parents=True, exist_ok=False)
    map_yaml = save_nav2_map(output_dir, grid, metadata)
    actual_grid, actual_metadata = load_nav2_map(map_yaml)
    if actual_grid != grid:
        raise AssertionError(f"written map pixels failed recomputation: floor {floor}")
    geometry_keys = ("width", "height", "resolution", "origin")
    if any(metadata[key] != actual_metadata[key] for key in geometry_keys):
        raise AssertionError(f"written map geometry changed: floor {floor}")
    if any(
        actual_grid[row][column] != UNKNOWN
        for row in range(metadata["height"])
        for column in range(metadata["width"])
        if not place_support[row][column] and not mesh_obstacles[row][column]
    ):
        raise AssertionError(f"unsupported cells did not remain unknown: floor {floor}")
    if any(
        mesh_obstacles[row][column] and actual_grid[row][column] != OCCUPIED
        for row in range(metadata["height"])
        for column in range(metadata["width"])
    ):
        raise AssertionError(f"mesh obstacle precedence failed: floor {floor}")

    place_nodes_intersecting_plane = sum(
        abs(position[2] - floor_z) <= clearances[node_id]
        for node_id, position in nodes.items()
    )
    edge_candidates = []
    for source, target, weight in edges:
        z0, z1 = nodes[source][2], nodes[target][2]
        vertical_gap = (z0 - floor_z if floor_z < min(z0, z1)
                        else floor_z - z1 if floor_z > max(z0, z1) else 0.0)
        if vertical_gap <= weight:
            edge_candidates.append((source, target, weight))
    relevant_node_ids = {
        node_id for node_id, position in nodes.items()
        if abs(position[2] - floor_z) <= clearances[node_id]
    }
    relevant_clearances = [clearances[node_id] for node_id in relevant_node_ids]
    clearance_values = {
        "min_m": min(relevant_clearances) if relevant_clearances else None,
        "median_m": (sorted(relevant_clearances)[len(relevant_clearances) // 2]
                     if relevant_clearances else None),
        "max_m": max(relevant_clearances) if relevant_clearances else None,
    }
    report = {
        "status": "GENERATED_AND_RECOMPUTED",
        "condition": "khronos_only",
        "floor": floor,
        "floor_z": floor_z,
        "base_map_role": "geometry_only; occupancy cell values are excluded",
        "parameters": {
            "resolution_m": metadata["resolution"],
            "origin": metadata["origin"],
            "width": metadata["width"],
            "height": metadata["height"],
            "place_projection": "exact 3D sphere/capsule intersection with horizontal floor_z plane",
            "place_support_model": "per-Place 3D distance and per-edge 3D bottleneck clearance",
            "clearance_margin_m": CLEARANCE_MARGIN_M,
            "mesh_obstacle_band_m": [
                floor_z + MESH_LOWER_OFFSET_M,
                floor_z + MESH_UPPER_OFFSET_M,
            ],
            "unknown_outside_khronos_masks": True,
            "mesh_occupied_wins_over_place_support": True,
        },
        "inputs": {
            "geometry_yaml": str(base_yaml),
            "geometry_yaml_sha256": sha256_file(base_yaml),
            "geometry_pgm_sha256": sha256_file(metadata["image_path"]),
            "mesh": str(mesh),
            "mesh_sha256": sha256_file(mesh),
            "dsg": str(dsg),
            "dsg_sha256": sha256_file(dsg),
            "original_dsg": str(original_dsg),
            "original_dsg_sha256": sha256_file(original_dsg),
        },
        "khronos_evidence": {
            "place_nodes_total": len(nodes),
            "place_nodes_reaching_floor_plane": int(place_nodes_intersecting_plane),
            "place_clearance_m": clearance_values,
            "edges_reaching_floor_plane": len(edge_candidates),
            "manual_topology_edges": topology_audit["requested_links"],
            "place_support_cells": int(_mask_count(place_support)),
            "mesh_projection": mesh_stats,
            "place_mesh_overlap_cells": int(
                sum(
                    place_support[row][column] and mesh_obstacles[row][column]
                    for row in range(metadata["height"])
                    for column in range(metadata["width"])
                )
            ),
        },
        "cell_counts": _counts(grid),
        "verification": {
            "pixels_reloaded_match": True,
            "geometry_manifest_sha256": sha256_file(GRID_GEOMETRY_MANIFEST),
            "geometry_reference_only": True,
            "occupancy_values_consumed": False,
            "unsupported_cells_unknown": True,
            "mesh_cells_occupied": True,
        },
        "output": {
            "map_yaml": str(map_yaml),
            "map_yaml_sha256": sha256_file(map_yaml),
            "map_pgm": str(output_dir / "map.pgm"),
            "map_pgm_sha256": sha256_file(output_dir / "map.pgm"),
        },
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--mesh", type=Path, default=KHRONOS_ROOT / "mesh.ply")
    parser.add_argument("--dsg", type=Path, default=KHRONOS_ROOT / "dsg.json")
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty output: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)
    repaired_dsg = args.output / "dsg_topology_repaired.json"
    if repaired_dsg.exists():
        raise FileExistsError(f"refusing to overwrite derived DSG: {repaired_dsg}")
    original_data = json.loads(args.dsg.read_text(encoding="utf-8"))
    repaired_data, topology_audit = repair_dsg_topology(original_data)
    repaired_dsg.write_text(json.dumps(repaired_data, indent=2) + "\n", encoding="utf-8")
    reports = [
        build_floor(floor, floor_z, args.mesh, repaired_dsg, args.dsg,
                    topology_audit, args.output)
        for floor, floor_z in FLOOR_HEIGHTS.items()
    ]
    manifest = {
        "status": "GENERATED_AND_RECOMPUTED",
        "condition": "khronos_only_clearance_khronos_global_planes_band005_200_manual_topology",
        "map_count": len(reports),
        "floor_height_calibration": {
            "floor_z_by_floor_m": FLOOR_HEIGHTS,
            "method": (
                "Khronos global mesh horizontal-face plane fits, cross-checked with "
                "direct camera-pose projection of semantic floor pixels"
            ),
            "note": (
                "Heights remain in the shared Khronos world frame; no mesh z shift "
                "or per-floor local re-zeroing is applied."
            ),
        },
        "mesh_height_band": {
            "above_floor_m": [MESH_LOWER_OFFSET_M, MESH_UPPER_OFFSET_M],
            "rationale": (
                "0.05 m lower cutoff is near the observed floor-plane residual scale; "
                "2.0 m upper cutoff is a nominal human-clearance target."
            ),
            "status": "chosen evaluation parameter; not a robot-specific collision model",
        },
        "additional_level_policy": (
            "The F3.5 upper half-level is not a fifth output map; only geometry "
            "intersecting each of the four primary floor slices is rasterized."
        ),
        "definition": (
            "Four primary Khronos global floor planes; mesh obstacle points in the "
            "0.05-2.0 m human-clearance band; per-Place 3D GVD-clearance "
            "intersection and mesh obstacle projection in the same world frame; "
            "depth baseline occupancy values are not used."
        ),
        "topology_repair": topology_audit,
        "grid_geometry_manifest": {
            "path": GRID_GEOMETRY_MANIFEST.name,
            "sha256": sha256_file(GRID_GEOMETRY_MANIFEST),
            "occupancy_values_consumed": False,
        },
        "derived_dsg": str(repaired_dsg),
        "derived_dsg_sha256": sha256_file(repaired_dsg),
        "floors": reports,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
