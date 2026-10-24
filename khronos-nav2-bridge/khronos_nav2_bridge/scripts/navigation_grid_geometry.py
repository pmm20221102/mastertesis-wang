"""Grid helpers for conservative, footprint-aware Hospital Nav2 planning."""

import math
import pathlib
import re


FOOTPRINT = ((0.38, 0.28), (0.38, -0.28), (-0.38, -0.28), (-0.38, 0.28))


def loadmap(map_yaml):
    """Load an ASCII PGM map and its ROS map YAML geometry."""
    yaml_text = pathlib.Path(map_yaml).read_text()
    image = re.search(r"image:\s*(\S+)", yaml_text).group(1)
    origin = [
        float(value)
        for value in re.search(r"origin:\s*\[([^]]+)", yaml_text)
        .group(1)
        .split(",")
    ]
    resolution = float(re.search(r"resolution:\s*([0-9.]+)", yaml_text).group(1))
    lines = (pathlib.Path(map_yaml).parent / image).read_text().splitlines()
    width, height = map(int, lines[1].split())
    values = list(map(int, " ".join(lines[3:]).split()))
    return values, width, height, resolution, origin


def pose(node_id, graph, original_graph):
    """Resolve a graph node pose, restoring z from the original graph if present."""
    node = next(node for node in graph["nodes"] if int(node["id"]) == node_id)
    stamped = node["pose_stamped"]
    z = stamped["position"][2]
    original = next(
        (node for node in original_graph["nodes"] if int(node["id"]) == node_id),
        None,
    )
    if original:
        z = original["pose_stamped"]["position"][2]
    w, x, y, z_rotation = stamped["orientation"]
    return {
        "x": float(stamped["position"][0]),
        "y": float(stamped["position"][1]),
        "z": float(z),
        "yaw": math.atan2(
            2 * (w * z_rotation + x * y),
            1 - 2 * (y * y + z_rotation * z_rotation),
        ),
    }


def safe(pose_xy, map_data):
    """Return whether the robot footprint avoids occupied and unknown cells."""
    values, width, height, resolution, origin = map_data
    cosine = math.cos(pose_xy.get("yaw", 0.0))
    sine = math.sin(pose_xy.get("yaw", 0.0))
    polygon = [
        (cosine * x - sine * y, sine * x + cosine * y)
        for x, y in FOOTPRINT
    ]
    world_polygon = [
        (pose_xy["x"] + x, pose_xy["y"] + y) for x, y in polygon
    ]
    min_x = min(x for x, _ in world_polygon)
    max_x = max(x for x, _ in world_polygon)
    min_y = min(y for _, y in world_polygon)
    max_y = max(y for _, y in world_polygon)
    map_max_x = origin[0] + width * resolution
    map_max_y = origin[1] + height * resolution
    epsilon = 1e-12
    if (
        min_x < origin[0] - epsilon
        or min_y < origin[1] - epsilon
        or max_x > map_max_x + epsilon
        or max_y > map_max_y + epsilon
    ):
        return False, "out_of_bounds"

    # Separating-axis tests include obstacle cells that intersect the footprint
    # boundary even when their centers lie outside the polygon.
    axes = [(1.0, 0.0), (0.0, 1.0)]
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        edge_x, edge_y = x2 - x1, y2 - y1
        if edge_x != 0.0 or edge_y != 0.0:
            axes.append((-edge_y, edge_x))
    intervals = []
    for axis_x, axis_y in axes:
        projections = [axis_x * x + axis_y * y for x, y in polygon]
        intervals.append(
            (axis_x, axis_y, min(projections), max(projections))
        )

    min_i = max(0, math.floor((min_x - origin[0]) / resolution) - 1)
    max_i = min(width - 1, math.floor((max_x - origin[0]) / resolution) + 1)
    min_j = max(0, math.floor((min_y - origin[1]) / resolution) - 1)
    max_j = min(height - 1, math.floor((max_y - origin[1]) / resolution) + 1)
    for j in range(min_j, max_j + 1):
        for i in range(min_i, max_i + 1):
            cell_x = origin[0] + (i + 0.5) * resolution - pose_xy["x"]
            cell_y = origin[1] + (j + 0.5) * resolution - pose_xy["y"]
            separated = False
            for axis_x, axis_y, polygon_min, polygon_max in intervals:
                cell_projection = axis_x * cell_x + axis_y * cell_y
                cell_radius = 0.5 * resolution * (abs(axis_x) + abs(axis_y))
                if (
                    polygon_max < cell_projection - cell_radius - epsilon
                    or cell_projection + cell_radius < polygon_min - epsilon
                ):
                    separated = True
                    break
            if separated:
                continue
            value = values[(height - 1 - j) * width + i]
            if value < 250:
                return False, "occupied" if value < 100 else "unknown"
    return True, "free"


def known_free_bfs_candidate(
    start, goal, map_data, max_distance=4.0, acceptance_tolerance=0.8
):
    """Find a nearby footprint-safe cell in the start-connected known-free set."""
    values, width, height, resolution, origin = map_data
    start_x = round((start["x"] - origin[0]) / resolution)
    start_y = round((start["y"] - origin[1]) / resolution)
    queue = [(start_x, start_y)]
    seen = {(start_x, start_y)}
    for cell_x, cell_y in queue:
        for delta_x, delta_y in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbor = (cell_x + delta_x, cell_y + delta_y)
            if neighbor in seen or not (
                0 <= neighbor[0] < width and 0 <= neighbor[1] < height
            ):
                continue
            if values[(height - 1 - neighbor[1]) * width + neighbor[0]] >= 250:
                seen.add(neighbor)
                queue.append(neighbor)

    candidates = []
    for cell_x, cell_y in seen:
        x = origin[0] + (cell_x + 0.5) * resolution
        y = origin[1] + (cell_y + 0.5) * resolution
        distance = math.hypot(x - goal["x"], y - goal["y"])
        if distance > max_distance or distance > acceptance_tolerance:
            continue
        is_safe, _ = safe({"x": x, "y": y}, map_data)
        if is_safe:
            candidates.append(
                (
                    distance,
                    math.hypot(cell_x - start_x, cell_y - start_y) * resolution,
                    cell_x,
                    cell_y,
                    x,
                    y,
                )
            )
    if not candidates:
        return None
    distance, geodesic, cell_x, cell_y, x, y = min(candidates)
    return {
        "x": x,
        "y": y,
        "z": 0.0,
        "yaw": goal.get("yaw", 0.0),
        "distance_m": distance,
        "graph_geodesic_m": geodesic,
        "cell": [cell_x, cell_y],
    }
