#!/usr/bin/env python3
"""Conservatively fuse Khronos Place support into a Nav2 occupancy map."""

import math
import json
import argparse
import hashlib
from collections import Counter
from pathlib import Path

import yaml

UNKNOWN = -1
FREE = 0
OCCUPIED = 100
PGM_OCCUPIED = 0
PGM_UNKNOWN = 205
PGM_FREE = 254


def read_pgm(path):
    """Read 8-bit P2 or P5 PGM pixels in top-to-bottom image order."""
    data = Path(path).read_bytes()
    offset = 0
    whitespace = b" \t\r\n\v\f"

    def token():
        nonlocal offset
        while offset < len(data):
            if data[offset] in whitespace:
                offset += 1
            elif data[offset] == ord("#"):
                newline = data.find(b"\n", offset)
                if newline < 0:
                    raise ValueError("malformed PGM comment")
                offset = newline + 1
            else:
                break
        start = offset
        while offset < len(data) and data[offset] not in whitespace + b"#":
            offset += 1
        if start == offset:
            raise ValueError("malformed PGM header or raster")
        return data[start:offset].decode("ascii")

    magic = token()
    if magic not in ("P2", "P5"):
        raise ValueError("only grayscale P2 and P5 PGM maps are supported")
    width, height, max_value = int(token()), int(token()), int(token())
    if width <= 0 or height <= 0 or not 1 <= max_value <= 255:
        raise ValueError("unsupported PGM dimensions or max value")

    if magic == "P5":
        if offset >= len(data) or data[offset] not in whitespace:
            raise ValueError("missing PGM raster delimiter")
        if data[offset:offset + 2] == b"\r\n":
            offset += 2
        else:
            offset += 1
        raster = data[offset:]
        if len(raster) != width * height:
            raise ValueError("PGM raster size does not match its dimensions")
        return list(raster), width, height

    samples = [int(token()) for _ in range(width * height)]
    if any(sample < 0 or sample > max_value for sample in samples):
        raise ValueError("PGM sample is outside the declared range")
    return [round(sample * 255 / max_value) for sample in samples], width, height


def load_nav2_map(yaml_path):
    """Load a trinary Nav2 map and convert it to top-down occupancy rows."""
    yaml_path = Path(yaml_path).resolve(strict=True)
    metadata = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or metadata.get("mode", "trinary") != "trinary":
        raise ValueError("only trinary Nav2 maps are supported")
    try:
        resolution = float(metadata["resolution"])
        origin = [float(value) for value in metadata["origin"]]
        negate = int(metadata.get("negate", 0))
        occupied_thresh = float(metadata["occupied_thresh"])
        free_thresh = float(metadata["free_thresh"])
        image_path = (yaml_path.parent / metadata["image"]).resolve(strict=True)
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise ValueError("invalid Nav2 map YAML") from error
    if (len(origin) != 3 or not all(math.isfinite(value) for value in origin)
            or abs(origin[2]) > 1e-9 or not math.isfinite(resolution) or resolution <= 0
            or negate not in (0, 1) or not 0 <= free_thresh < occupied_thresh <= 1
            or image_path.parent != yaml_path.parent):
        raise ValueError("unsupported Nav2 map geometry or thresholds")

    pixels, width, height = read_pgm(image_path)
    grid = []
    for row in range(height):
        occupancy_row = []
        for pixel in pixels[row * width:(row + 1) * width]:
            occupancy = pixel / 255.0 if negate else (255 - pixel) / 255.0
            if occupancy > occupied_thresh:
                occupancy_row.append(OCCUPIED)
            elif occupancy < free_thresh:
                occupancy_row.append(FREE)
            else:
                occupancy_row.append(UNKNOWN)
        grid.append(occupancy_row)
    parsed = dict(metadata)
    parsed.update({"width": width, "height": height, "resolution": resolution,
                   "origin": origin, "negate": negate,
                   "occupied_thresh": occupied_thresh, "free_thresh": free_thresh,
                   "yaml_path": str(yaml_path), "image_path": str(image_path)})
    return grid, parsed


def save_nav2_map(output_dir, grid, metadata):
    """Write a trinary P5 map with the source map's exact grid geometry."""
    output_dir = Path(output_dir)
    height = len(grid)
    width = len(grid[0]) if height else 0
    if (width != metadata["width"] or height != metadata["height"]
            or any(len(row) != width for row in grid)):
        raise ValueError("occupancy grid dimensions do not match map metadata")
    output_dir.mkdir(parents=True, exist_ok=True)
    pixel_for = {OCCUPIED: PGM_OCCUPIED, UNKNOWN: PGM_UNKNOWN, FREE: PGM_FREE}
    pixels = bytearray()
    for row in grid:
        for value in row:
            try:
                pixels.append(pixel_for[value])
            except KeyError as error:
                raise ValueError(f"invalid occupancy value: {value}") from error
    (output_dir / "map.pgm").write_bytes(
        f"P5\n{width} {height}\n255\n".encode("ascii") + pixels
    )
    output_metadata = {
        key: value for key, value in metadata.items()
        if key not in {"width", "height", "yaml_path", "image_path"}
    }
    output_metadata["image"] = "map.pgm"
    (output_dir / "map.yaml").write_text(
        yaml.safe_dump(output_metadata, sort_keys=False), encoding="utf-8"
    )
    return output_dir / "map.yaml"


def fuse_cells(base_grid, place_support, mesh_obstacles):
    """Return a fused occupancy grid without modifying the base grid.

    Place support fills only previously unknown cells. Projected mesh
    obstacles are occupied regardless of the base or Place-support value.
    """
    height = len(base_grid)
    if len(place_support) != height or len(mesh_obstacles) != height:
        raise ValueError("grid and masks must have the same height")

    fused = []
    for row_index in range(height):
        base_row = base_grid[row_index]
        support_row = place_support[row_index]
        obstacle_row = mesh_obstacles[row_index]
        if len(support_row) != len(base_row) or len(obstacle_row) != len(base_row):
            raise ValueError("grid and masks must have the same width")
        fused_row = []
        for value, supported, obstacle in zip(base_row, support_row, obstacle_row):
            if obstacle:
                fused_row.append(OCCUPIED)
            elif value == UNKNOWN and supported:
                fused_row.append(FREE)
            else:
                fused_row.append(value)
        fused.append(fused_row)
    return fused


def world_to_image_cell(x, y, origin, resolution, width, height):
    """Convert map-frame XY to top-down PGM (row, column), or return None."""
    if (not math.isfinite(float(x)) or not math.isfinite(float(y))
            or not math.isfinite(float(resolution)) or resolution <= 0
            or width <= 0 or height <= 0):
        raise ValueError("finite coordinates and positive map geometry are required")
    origin_x, origin_y = map(float, origin[:2])
    column = math.floor((float(x) - origin_x) / resolution)
    bottom_row = math.floor((float(y) - origin_y) / resolution)
    if column < 0 or column >= width or bottom_row < 0 or bottom_row >= height:
        return None
    return height - 1 - bottom_row, column


def rasterize_place_support(
    nodes,
    edges,
    origin,
    resolution,
    width,
    height,
    *,
    floor_z=None,
    place_band_height=None,
    floor_z_bounds=None,
    clearances=None,
    clearance_margin_m=0.0,
    radius=None,
    max_edge_weight=None,
):
    """Rasterize the intersection of 3D GVD support with a 2D floor plane.

    In clearance mode, Place.distance and edge bottleneck weights are 3D radii;
    their spheres/capsules are intersected with ``z=floor_z`` before rasterizing.
    Fixed-radius mode is retained for legacy callers and must be requested
    explicitly with ``radius``.
    """
    legacy_radius_mode = clearances is None
    if legacy_radius_mode == (radius is None):
        raise ValueError("provide exactly one of clearances or explicit legacy radius")
    if (width <= 0 or height <= 0 or resolution <= 0
            or not math.isfinite(float(resolution))
            or not math.isfinite(float(clearance_margin_m)) or clearance_margin_m < 0):
        raise ValueError("invalid map geometry or Place support parameters")
    if not legacy_radius_mode:
        if floor_z is None or not math.isfinite(float(floor_z)):
            raise ValueError("GVD-clearance projection requires a finite floor_z plane")
        if floor_z_bounds is not None:
            raise ValueError("floor_z_bounds cannot filter 3D GVD support; use floor_z plane")
        target_z = float(floor_z)
        z_min = z_max = None
        include_z_min = include_z_max = True
    elif floor_z_bounds is None:
        if (floor_z is None or place_band_height is None
                or not math.isfinite(float(floor_z))
                or not math.isfinite(float(place_band_height)) or place_band_height < 0):
            raise ValueError("provide finite floor_z and non-negative place_band_height")
        z_min, z_max = float(floor_z), float(floor_z) + float(place_band_height)
        include_z_min, include_z_max = True, True
        target_z = float(floor_z)
    else:
        if len(floor_z_bounds) != 2 or floor_z_bounds == (None, None):
            raise ValueError("floor_z_bounds must have at least one finite bound")
        z_min, z_max = floor_z_bounds
        if z_min is not None and not math.isfinite(float(z_min)):
            raise ValueError("lower floor z bound must be finite or None")
        if z_max is not None and not math.isfinite(float(z_max)):
            raise ValueError("upper floor z bound must be finite or None")
        if z_min is not None and z_max is not None and z_max <= z_min:
            raise ValueError("upper floor z bound must exceed lower bound")
        include_z_min, include_z_max = True, False  # adjacent storeys are half-open
        target_z = float(floor_z) if floor_z is not None else 0.0
    if legacy_radius_mode:
        if not math.isfinite(float(radius)) or radius < 0:
            raise ValueError("legacy radius must be finite and non-negative")
        if max_edge_weight is not None and (
                not math.isfinite(float(max_edge_weight)) or max_edge_weight < 0):
            raise ValueError("legacy max_edge_weight must be finite and non-negative")
    elif max_edge_weight is not None:
        raise ValueError("max_edge_weight is not valid in GVD-clearance mode")

    origin_x, origin_y = map(float, origin[:2])
    if not math.isfinite(origin_x) or not math.isfinite(origin_y):
        raise ValueError("map origin must be finite")

    eligible = {}
    eligible_clearances = {}
    for node_id, position in nodes.items():
        if len(position) < 3 or not all(math.isfinite(float(value)) for value in position[:3]):
            continue
        z = float(position[2])
        if legacy_radius_mode:
            if z_min is not None and (z < float(z_min) if include_z_min else z <= float(z_min)):
                continue
            if z_max is not None and (z > float(z_max) if include_z_max else z >= float(z_max)):
                continue
        eligible[node_id] = tuple(map(float, position[:3]))
        if not legacy_radius_mode:
            if node_id not in clearances:
                raise ValueError(f"missing GVD clearance for Place node {node_id}")
            clearance = float(clearances[node_id])
            if not math.isfinite(clearance) or clearance < 0:
                raise ValueError(f"invalid GVD clearance for Place node {node_id}")
            eligible_clearances[node_id] = clearance

    support = [[False] * width for _ in range(height)]

    def fill_capsule(start, end, support_radius, *, project_3d=False):
        support_radius = max(0.0, float(support_radius) - clearance_margin_m)
        if support_radius <= 0:
            return
        radius_sq = support_radius * support_radius
        ax, ay = start[:2]
        bx, by = end[:2]
        col0 = max(0, math.floor((min(ax, bx) - support_radius - origin_x) / resolution))
        col1 = min(width - 1, math.floor((max(ax, bx) + support_radius - origin_x) / resolution))
        bottom0 = max(0, math.floor((min(ay, by) - support_radius - origin_y) / resolution))
        bottom1 = min(height - 1, math.floor((max(ay, by) + support_radius - origin_y) / resolution))
        az = float(start[2]) if project_3d else target_z
        bz = float(end[2]) if project_3d else target_z
        dx, dy, dz = bx - ax, by - ay, bz - az
        length_sq = dx * dx + dy * dy + dz * dz
        for bottom_row in range(bottom0, bottom1 + 1):
            image_row = height - 1 - bottom_row
            py = origin_y + (bottom_row + 0.5) * resolution
            for column in range(col0, col1 + 1):
                px = origin_x + (column + 0.5) * resolution
                z_delta = target_z - az if project_3d else 0.0
                t = 0.0 if length_sq == 0 else max(
                    0.0,
                    min(1.0, ((px - ax) * dx + (py - ay) * dy + z_delta * dz) / length_sq),
                )
                nearest_x = ax + t * dx
                nearest_y = ay + t * dy
                nearest_z = az + t * dz
                distance_sq = ((px - nearest_x) ** 2 + (py - nearest_y) ** 2
                               + ((target_z - nearest_z) ** 2 if project_3d else 0.0))
                if distance_sq <= radius_sq + 1e-12:
                    support[image_row][column] = True

    for node_id, (x, y, z) in eligible.items():
        node_radius = radius if legacy_radius_mode else eligible_clearances[node_id]
        position = (x, y, z if not legacy_radius_mode else target_z)
        fill_capsule(position, position, node_radius, project_3d=not legacy_radius_mode)

    for source, target, weight in edges:
        if source not in eligible or target not in eligible:
            continue
        weight = float(weight)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("Place edge weights must be finite and non-negative")
        if legacy_radius_mode:
            if max_edge_weight is not None and weight > max_edge_weight:
                continue
            edge_radius = radius
        else:
            edge_radius = min(weight, eligible_clearances[source], eligible_clearances[target])
        fill_capsule(
            eligible[source], eligible[target], edge_radius,
            project_3d=not legacy_radius_mode,
        )
    return support


def load_dsg_places(dsg_path, *, include_clearances=False):
    """Read PLACES coordinates/edges and optionally GVD clearances from a DSG."""
    data = json.loads(Path(dsg_path).read_text(encoding="utf-8"))
    place_key = data.get("layer_names", {}).get("PLACES", {"layer": 3, "partition": 0})
    place_layer = place_key.get("layer", 3)
    place_partition = place_key.get("partition", 0)
    nodes = {}
    clearances = {}
    for node in data.get("nodes", []):
        layer_name = node.get("layer_name")
        if layer_name == "PLACES":
            is_place = True
        else:
            layer = node.get("layer")
            partition = node.get("partition", 0)
            is_place = (str(layer) == str(place_layer)
                        and str(partition) == str(place_partition))
        if not is_place:
            continue
        attributes = node.get("attributes", {})
        position = attributes.get("position") or attributes.get("world_position")
        if (not isinstance(position, (list, tuple)) or len(position) < 3
                or any(not math.isfinite(float(value)) for value in position[:3])):
            continue
        nodes[node["id"]] = tuple(map(float, position[:3]))
        if include_clearances:
            raw_distance = attributes.get("distance")
            if raw_distance is None:
                raise ValueError(f"PLACES node {node['id']} has no GVD distance")
            distance = float(raw_distance)
            if not math.isfinite(distance) or distance < 0:
                raise ValueError(f"PLACES node {node['id']} has invalid GVD distance")
            clearances[node["id"]] = distance
    if not nodes:
        raise ValueError("DSG has no PLACES nodes with finite 3D positions")

    edges = []
    for edge in data.get("edges", []):
        source, target = edge.get("source"), edge.get("target")
        if source not in nodes or target not in nodes or source == target:
            continue
        raw_weight = edge.get("info", {}).get("weight")
        if include_clearances and raw_weight is None:
            raise ValueError(f"PLACES edge {source}->{target} has no GVD clearance weight")
        weight = float(raw_weight) if raw_weight is not None else math.dist(nodes[source], nodes[target])
        if math.isfinite(weight) and weight >= 0:
            edges.append((source, target, weight))
    if include_clearances:
        return nodes, edges, clearances
    return nodes, edges


def iter_ply_xyz(mesh_path):
    """Stream XYZ vertices from an ASCII PLY, honoring declared property order."""
    with Path(mesh_path).open("r", encoding="ascii") as stream:
        if stream.readline().strip() != "ply":
            raise ValueError("invalid PLY header")
        format_name = None
        vertex_count = None
        in_vertex = False
        properties = []
        while True:
            line = stream.readline()
            if not line:
                raise ValueError("PLY header has no end_header")
            parts = line.split()
            if not parts or parts[0] in ("comment", "obj_info"):
                continue
            if parts[0] == "format":
                format_name = parts[1]
            elif parts[0] == "element":
                in_vertex = parts[1] == "vertex"
                if in_vertex:
                    vertex_count = int(parts[2])
            elif parts[0] == "property" and in_vertex:
                if parts[1] == "list":
                    raise ValueError("list properties are not supported in PLY vertex records")
                properties.append(parts[2])
            elif parts[0] == "end_header":
                break
        if format_name != "ascii" or vertex_count is None:
            raise ValueError("only ASCII PLY files with a vertex element are supported")
        try:
            x_index, y_index, z_index = (properties.index(axis) for axis in ("x", "y", "z"))
        except ValueError as error:
            raise ValueError("PLY vertex records must define x, y, and z") from error
        for _ in range(vertex_count):
            values = stream.readline().split()
            if len(values) < len(properties):
                raise ValueError("malformed PLY vertex record")
            point = (float(values[x_index]), float(values[y_index]), float(values[z_index]))
            if not all(math.isfinite(value) for value in point):
                raise ValueError("PLY contains a non-finite vertex")
            yield point


def rasterize_mesh_obstacles(
    mesh_path,
    *,
    origin,
    resolution,
    width,
    height,
    floor_z,
    lower_offset,
    upper_offset,
):
    """Project mesh vertices in the configured vertical obstacle band."""
    if (not math.isfinite(float(floor_z)) or lower_offset < 0
            or upper_offset <= lower_offset):
        raise ValueError("invalid mesh obstacle height band")
    mask = [[False] * width for _ in range(height)]
    in_band = 0
    clipped = 0
    projected = 0
    for x, y, z in iter_ply_xyz(mesh_path):
        if not floor_z + lower_offset <= z <= floor_z + upper_offset:
            continue
        in_band += 1
        cell = world_to_image_cell(x, y, origin, resolution, width, height)
        if cell is None:
            clipped += 1
            continue
        row, column = cell
        mask[row][column] = True
        projected += 1
    if in_band == 0:
        raise ValueError("mesh has no vertices in the configured floor obstacle band")
    return mask, {
        "points_in_band": in_band,
        "points_projected": projected,
        "points_clipped": clipped,
        "occupied_cells": sum(sum(row) for row in mask),
    }


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def convert_map(
    base_yaml,
    mesh_path,
    dsg_path,
    output_dir,
    *,
    floor,
    floor_z,
    support_radius=None,
    place_band_height=0.25,
    max_edge_weight=None,
    clearance_margin_m=0.0,
    floor_z_bounds=None,
    mesh_lower_offset=0.18,
    mesh_upper_offset=1.8,
    dry_run=False,
):
    """Fuse one floor map and optionally write it with a planner report."""
    output_dir = Path(output_dir)
    if not dry_run and output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty output: {output_dir}")

    base_grid, metadata = load_nav2_map(base_yaml)
    use_gvd_clearance = support_radius is None
    loaded = load_dsg_places(dsg_path, include_clearances=use_gvd_clearance)
    if use_gvd_clearance:
        nodes, edges, clearances = loaded
    else:
        nodes, edges = loaded
        clearances = None
    support = rasterize_place_support(
        nodes, edges, metadata["origin"], metadata["resolution"],
        metadata["width"], metadata["height"], floor_z=floor_z,
        place_band_height=place_band_height, floor_z_bounds=floor_z_bounds,
        clearances=clearances, clearance_margin_m=clearance_margin_m,
        max_edge_weight=(None if use_gvd_clearance else
                         (1.3 if max_edge_weight is None else max_edge_weight)),
        radius=support_radius,
    )
    obstacles, mesh_stats = rasterize_mesh_obstacles(
        mesh_path, origin=metadata["origin"], resolution=metadata["resolution"],
        width=metadata["width"], height=metadata["height"], floor_z=floor_z,
        lower_offset=mesh_lower_offset, upper_offset=mesh_upper_offset,
    )
    fused = fuse_cells(base_grid, support, obstacles)
    support_count = sum(sum(row) for row in support)
    if support_count == 0:
        raise ValueError(f"floor {floor} has no rasterized Place support")

    base_counts = Counter(value for row in base_grid for value in row)
    fused_counts = Counter(value for row in fused for value in row)
    supported_base_counts = Counter(
        base_grid[row][column]
        for row in range(metadata["height"])
        for column in range(metadata["width"])
        if support[row][column]
    )
    obstacle_base_counts = Counter(
        base_grid[row][column]
        for row in range(metadata["height"])
        for column in range(metadata["width"])
        if obstacles[row][column]
    )
    newly_free = sum(
        base_grid[row][column] == UNKNOWN and fused[row][column] == FREE
        for row in range(metadata["height"])
        for column in range(metadata["width"])
    )
    if newly_free == 0:
        raise ValueError(f"floor {floor} Place support filled no previously unknown cells")

    if use_gvd_clearance:
        lower_z = upper_z = None
        eligible_nodes = {
            node_id for node_id, position in nodes.items()
            if abs(position[2] - floor_z) <= clearances[node_id]
        }
        eligible_edges = sum(
            max(min(nodes[source][2], nodes[target][2]) - floor_z,
                floor_z - max(nodes[source][2], nodes[target][2]), 0.0)
            <= min(weight, clearances[source], clearances[target])
            for source, target, weight in edges
        )
    else:
        if floor_z_bounds is None:
            lower_z, upper_z = floor_z, floor_z + place_band_height
            eligible_nodes = {
                node_id for node_id, position in nodes.items()
                if lower_z <= position[2] <= upper_z
            }
        else:
            lower_z, upper_z = floor_z_bounds
            eligible_nodes = {
                node_id for node_id, position in nodes.items()
                if (lower_z is None or position[2] >= lower_z)
                and (upper_z is None or position[2] < upper_z)
            }
        legacy_edge_limit = 1.3 if max_edge_weight is None else max_edge_weight
        eligible_edges = sum(
            source in eligible_nodes and target in eligible_nodes and weight <= legacy_edge_limit
            for source, target, weight in edges
        )
    report = {
        "status": "GENERATED",
        "floor": int(floor),
        "floor_z": float(floor_z),
        "coordinate_frame_contract": "map/world XY; input occupancy map uses the source-verified identity map-to-odom convention",
        "inputs": {
            "base_yaml": str(Path(base_yaml).resolve()),
            "base_yaml_sha256": sha256_file(base_yaml),
            "base_pgm": metadata["image_path"],
            "base_pgm_sha256": sha256_file(metadata["image_path"]),
            "mesh": str(Path(mesh_path).resolve()),
            "mesh_sha256": sha256_file(mesh_path),
            "dsg": str(Path(dsg_path).resolve()),
            "dsg_sha256": sha256_file(dsg_path),
        },
        "parameters": {
            "resolution": metadata["resolution"],
            "origin": metadata["origin"],
            "width": metadata["width"],
            "height": metadata["height"],
            "place_support_model": ("node_and_edge_gvd_clearance" if use_gvd_clearance
                                     else "legacy_fixed_radius"),
            "place_projection": ("3d_sphere_and_capsule_intersection_with_floor_plane"
                                 if use_gvd_clearance else "legacy_xy_radius"),
            "clearance_margin_m": float(clearance_margin_m),
            "place_band_m": (None if use_gvd_clearance else [lower_z, upper_z]),
            "support_radius_m": (None if use_gvd_clearance else float(support_radius)),
            "max_recorded_edge_weight_m": (
                None if use_gvd_clearance else float(legacy_edge_limit)
            ),
            "mesh_obstacle_band_m": [
                float(floor_z + mesh_lower_offset), float(floor_z + mesh_upper_offset)
            ],
        },
        "place_graph": {
            "place_nodes_total": len(nodes),
            "place_nodes_reaching_floor_plane": len(eligible_nodes),
            "place_edges_total": len(edges),
            "edges_reaching_floor_plane": int(eligible_edges),
            "support_cells": support_count,
        },
        "mesh_projection": mesh_stats,
        "cell_counts": {
            "base": {"unknown": base_counts[UNKNOWN], "free": base_counts[FREE],
                     "occupied": base_counts[OCCUPIED]},
            "supported_base_cells": {"unknown": supported_base_counts[UNKNOWN],
                                      "free": supported_base_counts[FREE],
                                      "occupied": supported_base_counts[OCCUPIED]},
            "mesh_base_cells": {"unknown": obstacle_base_counts[UNKNOWN],
                                "free": obstacle_base_counts[FREE],
                                "occupied": obstacle_base_counts[OCCUPIED]},
            "newly_filled_unknown_to_free": int(newly_free),
            "final": {"unknown": fused_counts[UNKNOWN], "free": fused_counts[FREE],
                      "occupied": fused_counts[OCCUPIED]},
        },
        "invariants": {
            "original_occupied_preserved": all(
                base_grid[row][column] != OCCUPIED or fused[row][column] == OCCUPIED
                for row in range(metadata["height"])
                for column in range(metadata["width"])
            ),
            "place_only_fills_unknown": all(
                fused[row][column] != FREE
                or base_grid[row][column] == FREE
                or (base_grid[row][column] == UNKNOWN and support[row][column]
                    and not obstacles[row][column])
                for row in range(metadata["height"])
                for column in range(metadata["width"])
            ),
            "mesh_obstacles_occupied": all(
                not obstacles[row][column] or fused[row][column] == OCCUPIED
                for row in range(metadata["height"])
                for column in range(metadata["width"])
            ),
        },
    }
    if not all(report["invariants"].values()):
        raise AssertionError("conservative fusion invariant failed")
    if dry_run:
        return report

    output_dir.mkdir(parents=True, exist_ok=True)
    map_yaml = save_nav2_map(output_dir, fused, metadata)
    map_pgm = output_dir / "map.pgm"
    report["hashes"] = {
        "pgm": sha256_file(map_pgm),
        "yaml": sha256_file(map_yaml),
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def verify_fusion(
    base_yaml,
    fused_yaml,
    mesh_path,
    dsg_path,
    *,
    floor,
    floor_z,
    support_radius=None,
    place_band_height=0.25,
    max_edge_weight=None,
    clearance_margin_m=0.0,
    floor_z_bounds=None,
    mesh_lower_offset=0.18,
    mesh_upper_offset=1.8,
):
    """Recompute and verify a saved fusion against its immutable inputs."""
    base_grid, base_metadata = load_nav2_map(base_yaml)
    fused_grid, fused_metadata = load_nav2_map(fused_yaml)
    geometry_keys = ("width", "height", "resolution", "origin")
    if any(base_metadata[key] != fused_metadata[key] for key in geometry_keys):
        raise AssertionError("fused map geometry differs from the base occupancy grid")
    use_gvd_clearance = support_radius is None
    loaded = load_dsg_places(dsg_path, include_clearances=use_gvd_clearance)
    if use_gvd_clearance:
        nodes, edges, clearances = loaded
    else:
        nodes, edges = loaded
        clearances = None
    support = rasterize_place_support(
        nodes, edges, base_metadata["origin"], base_metadata["resolution"],
        base_metadata["width"], base_metadata["height"], floor_z=floor_z,
        place_band_height=place_band_height, floor_z_bounds=floor_z_bounds,
        clearances=clearances, clearance_margin_m=clearance_margin_m,
        max_edge_weight=(None if use_gvd_clearance else
                         (1.3 if max_edge_weight is None else max_edge_weight)),
        radius=support_radius,
    )
    obstacles, _ = rasterize_mesh_obstacles(
        mesh_path, origin=base_metadata["origin"], resolution=base_metadata["resolution"],
        width=base_metadata["width"], height=base_metadata["height"], floor_z=floor_z,
        lower_offset=mesh_lower_offset, upper_offset=mesh_upper_offset,
    )
    expected = fuse_cells(base_grid, support, obstacles)
    if fused_grid != expected:
        raise AssertionError("saved fused pixels differ from conservative fusion of source inputs")
    report_path = Path(fused_yaml).resolve().parent / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected_hashes = {
        "pgm": sha256_file(fused_metadata["image_path"]),
        "yaml": sha256_file(fused_yaml),
    }
    if report.get("status") != "GENERATED" or report.get("floor") != floor:
        raise AssertionError("saved fusion report has an invalid status or floor id")
    if report.get("hashes") != expected_hashes:
        raise AssertionError("saved fusion report hashes do not match its map files")
    return {
        "valid": True,
        "map_pixels_match_expected_fusion": True,
        "geometry_unchanged": True,
        "report_hashes_match": True,
        "floor": int(floor),
        "width": base_metadata["width"],
        "height": base_metadata["height"],
        "resolution": base_metadata["resolution"],
        "origin": base_metadata["origin"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-yaml", required=True, type=Path)
    parser.add_argument("--mesh", required=True, type=Path)
    parser.add_argument("--dsg", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--floor", required=True, type=int)
    parser.add_argument("--floor-z", required=True, type=float)
    parser.add_argument("--support-radius", type=float, default=None,
                        help="legacy fixed-radius mode; omit to use per-Place GVD clearance")
    parser.add_argument("--place-band-height", type=float, default=0.25)
    parser.add_argument("--floor-z-min", type=float, default=None)
    parser.add_argument("--floor-z-max", type=float, default=None)
    parser.add_argument("--max-edge-weight", type=float, default=None,
                        help="legacy fixed-radius edge filter only")
    parser.add_argument("--clearance-margin", type=float, default=0.0)
    parser.add_argument("--mesh-lower-offset", type=float, default=0.18)
    parser.add_argument("--mesh-upper-offset", type=float, default=1.8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if (args.floor_z_min is None) != (args.floor_z_max is None):
        parser.error("--floor-z-min and --floor-z-max must be supplied together")
    floor_z_bounds = ((args.floor_z_min, args.floor_z_max)
                      if args.floor_z_min is not None else None)
    if args.verify_only:
        result = verify_fusion(
            args.base_yaml, args.output / "map.yaml", args.mesh, args.dsg,
            floor=args.floor, floor_z=args.floor_z,
            support_radius=args.support_radius,
            place_band_height=args.place_band_height,
            max_edge_weight=args.max_edge_weight,
            clearance_margin_m=args.clearance_margin,
            floor_z_bounds=floor_z_bounds,
            mesh_lower_offset=args.mesh_lower_offset,
            mesh_upper_offset=args.mesh_upper_offset,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    result = convert_map(
        args.base_yaml, args.mesh, args.dsg, args.output,
        floor=args.floor, floor_z=args.floor_z,
        support_radius=args.support_radius,
        place_band_height=args.place_band_height,
        max_edge_weight=args.max_edge_weight,
        clearance_margin_m=args.clearance_margin,
        floor_z_bounds=floor_z_bounds,
        mesh_lower_offset=args.mesh_lower_offset,
        mesh_upper_offset=args.mesh_upper_offset,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
