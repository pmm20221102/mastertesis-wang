"""Pure map loading and coordinate placement for the Hospital floor mosaic."""

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path

import yaml

from floor_transition_core import compile_mission, validate_transition_bundle


@dataclass(frozen=True)
class FloorGrid:
    floor_id: int
    width: int
    height: int
    resolution: float
    origin_x: float
    origin_y: float
    occupancy: tuple[int, ...]
    yaml_sha256: str = ""
    pgm_sha256: str = ""


@dataclass(frozen=True)
class TilePlacement:
    floor_id: int
    cell_x: int
    cell_y: int
    content_cell_x: int
    content_cell_y: int


@dataclass(frozen=True)
class MosaicGrid:
    width: int
    height: int
    resolution: float
    origin_x: float
    origin_y: float
    data: tuple[int, ...]
    placements: dict[int, TilePlacement]
    tile_width: int
    tile_height: int
    panel_x: int
    panel_width: int


@dataclass(frozen=True)
class RoomLabel:
    node_id: int
    label: str
    floor_id: int
    x: float
    y: float
    z: float
    half_level: bool = False


def _read_pgm(path: Path) -> tuple[bytes, int, int]:
    raw = path.read_bytes()
    offset = 0
    whitespace = b" \t\r\n\v\f"

    def token() -> bytes:
        nonlocal offset
        while offset < len(raw):
            if raw[offset] in whitespace:
                offset += 1
            elif raw[offset] == ord("#"):
                end = raw.find(b"\n", offset)
                if end < 0:
                    raise ValueError(f"malformed PGM comment: {path}")
                offset = end + 1
            else:
                break
        start = offset
        while offset < len(raw) and raw[offset] not in whitespace + b"#":
            offset += 1
        if start == offset:
            raise ValueError(f"malformed PGM header: {path}")
        return raw[start:offset]

    magic = token()
    if magic != b"P5":
        raise ValueError(f"only P5 PGM maps are supported: {path}")
    try:
        width, height, maximum = int(token()), int(token()), int(token())
    except ValueError as error:
        raise ValueError(f"invalid PGM dimensions: {path}") from error
    if width <= 0 or height <= 0 or maximum != 255:
        raise ValueError(f"unsupported PGM geometry/max value: {path}")
    if offset >= len(raw) or raw[offset] not in whitespace:
        raise ValueError(f"missing PGM raster delimiter: {path}")
    if raw[offset:offset + 2] == b"\r\n":
        offset += 2
    else:
        offset += 1
    pixels = raw[offset:]
    if len(pixels) != width * height:
        raise ValueError(f"PGM raster size mismatch: {path}")
    return pixels, width, height


def _to_trinary_occupancy(gray: int, negate: int, free_thresh: float,
                          occupied_thresh: float) -> int:
    probability = gray / 255.0 if negate else (255 - gray) / 255.0
    if probability >= occupied_thresh:
        return 100
    if probability <= free_thresh:
        return 0
    return -1


def load_floor_grids(bundle: dict) -> dict[int, FloorGrid]:
    """Load exactly the four hash-verified, yaw-zero trinary floor maps."""
    normalized = validate_transition_bundle(bundle, verify_files=True)
    if set(normalized["floors"]) != {"0", "1", "2", "3"}:
        raise ValueError("floor mosaic requires exactly floors 0, 1, 2, and 3")

    result = {}
    for floor_key, config in normalized["floors"].items():
        floor_id = int(floor_key)
        yaml_path = Path(config["map_yaml_path"]).resolve(strict=True)
        pgm_path = Path(config["map_pgm_path"]).resolve(strict=True)
        metadata = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if metadata.get("mode", "trinary") != "trinary":
            raise ValueError(f"floor {floor_id} map mode must be trinary")
        image_path = (yaml_path.parent / metadata["image"]).resolve(strict=True)
        if image_path != pgm_path:
            raise ValueError(f"floor {floor_id} YAML image path/hash contract mismatch")
        resolution = float(metadata["resolution"])
        origin = metadata["origin"]
        if (not math.isfinite(resolution) or resolution <= 0.0
                or not isinstance(origin, list) or len(origin) < 3
                or not all(math.isfinite(float(value)) for value in origin[:3])
                or abs(float(origin[2])) > 1e-9):
            raise ValueError(f"floor {floor_id} has unsupported map geometry")
        negate = int(metadata.get("negate", 0))
        free_thresh = float(metadata["free_thresh"])
        occupied_thresh = float(metadata["occupied_thresh"])
        if (negate not in (0, 1) or not 0.0 <= free_thresh < occupied_thresh <= 1.0):
            raise ValueError(f"floor {floor_id} has invalid trinary thresholds")
        pixels, width, height = _read_pgm(pgm_path)
        occupancy = []
        for ros_y in range(height):
            image_y = height - 1 - ros_y
            row_start = image_y * width
            occupancy.extend(
                _to_trinary_occupancy(pixels[row_start + x], negate,
                                      free_thresh, occupied_thresh)
                for x in range(width)
            )
        result[floor_id] = FloorGrid(
            floor_id=floor_id,
            width=width,
            height=height,
            resolution=resolution,
            origin_x=float(origin[0]),
            origin_y=float(origin[1]),
            occupancy=tuple(occupancy),
            yaml_sha256=config["map_yaml_sha256"],
            pgm_sha256=config["map_pgm_sha256"],
        )
    return result


def compose_floor_mosaic(floors: dict[int, FloorGrid], gap_cells: int = 20,
                         panel_width_cells: int = 280,
                         tile_aspect: tuple[int, int] = (16, 9)) -> MosaicGrid:
    """Arrange undistorted maps in four equal-aspect tiles plus a white sidebar."""
    if set(floors) != {0, 1, 2, 3}:
        raise ValueError("mosaic requires exactly floor IDs 0, 1, 2, and 3")
    if isinstance(gap_cells, bool) or not isinstance(gap_cells, int) or gap_cells < 1:
        raise ValueError("gap_cells must be a positive integer")
    if (isinstance(panel_width_cells, bool) or not isinstance(panel_width_cells, int)
            or panel_width_cells < 1):
        raise ValueError("panel_width_cells must be a positive integer")
    if (not isinstance(tile_aspect, tuple) or len(tile_aspect) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) or value < 1
                   for value in tile_aspect)):
        raise ValueError("tile_aspect must contain two positive integers")
    resolutions = {floor.resolution for floor in floors.values()}
    if len(resolutions) != 1:
        raise ValueError("all mosaic floors must use one shared resolution")
    for floor_id, floor in floors.items():
        if (floor.floor_id != floor_id or floor.width <= 0 or floor.height <= 0
                or len(floor.occupancy) != floor.width * floor.height):
            raise ValueError(f"invalid occupancy grid for floor {floor_id}")

    tile_height = max(floor.height for floor in floors.values())
    aspect_width, aspect_height = tile_aspect
    tile_width = max(max(floor.width for floor in floors.values()),
                     math.ceil(tile_height * aspect_width / aspect_height))
    map_width = 2 * tile_width + gap_cells
    panel_x = map_width + gap_cells
    width = panel_x + panel_width_cells
    height = 2 * tile_height + gap_cells
    placements = {
        0: TilePlacement(0, 0, tile_height + gap_cells,
                         (tile_width - floors[0].width) // 2,
                         (tile_height - floors[0].height) // 2),
        1: TilePlacement(1, tile_width + gap_cells, tile_height + gap_cells,
                         (tile_width - floors[1].width) // 2,
                         (tile_height - floors[1].height) // 2),
        2: TilePlacement(2, 0, 0,
                         (tile_width - floors[2].width) // 2,
                         (tile_height - floors[2].height) // 2),
        3: TilePlacement(3, tile_width + gap_cells, 0,
                         (tile_width - floors[3].width) // 2,
                         (tile_height - floors[3].height) // 2),
    }
    data = [-1] * (width * height)
    # The right-hand visualization-only sidebar is white and is not a Nav2 map.
    for y in range(height):
        start = y * width + panel_x
        data[start:start + panel_width_cells] = [0] * panel_width_cells
    for floor_id, floor in floors.items():
        placement = placements[floor_id]
        for y in range(floor.height):
            source = y * floor.width
            target = ((placement.cell_y + placement.content_cell_y + y) * width
                      + placement.cell_x + placement.content_cell_x)
            data[target:target + floor.width] = floor.occupancy[source:source + floor.width]
    return MosaicGrid(width, height, next(iter(resolutions)), 0.0, 0.0,
                      tuple(data), placements, tile_width, tile_height,
                      panel_x, panel_width_cells)


def map_pose_to_mosaic(pose_xy: tuple[float, float], floor: FloorGrid,
                       tile: TilePlacement, mosaic: MosaicGrid) -> tuple[float, float]:
    """Convert source-map world coordinates into the floor tile's mosaic coordinates."""
    x, y = (float(value) for value in pose_xy)
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("pose coordinates must be finite")
    return (
        mosaic.origin_x + (tile.cell_x + tile.content_cell_x) * mosaic.resolution
        + (x - floor.origin_x),
        mosaic.origin_y + (tile.cell_y + tile.content_cell_y) * mosaic.resolution
        + (y - floor.origin_y),
    )


def map_to_mosaic_translation(floor: FloorGrid, tile: TilePlacement,
                              mosaic: MosaicGrid) -> tuple[float, float]:
    """Return translation for the visualization-only mosaic <- map transform."""
    return (
        mosaic.origin_x + (tile.cell_x + tile.content_cell_x) * mosaic.resolution
        - floor.origin_x,
        mosaic.origin_y + (tile.cell_y + tile.content_cell_y) * mosaic.resolution
        - floor.origin_y,
    )


def map_grid_origin_in_mosaic(floor: FloorGrid, tile: TilePlacement,
                              mosaic: MosaicGrid) -> tuple[float, float]:
    """Return the origin for publishing one floor grid directly in mosaic frame."""
    return (
        mosaic.origin_x + (tile.cell_x + tile.content_cell_x) * mosaic.resolution,
        mosaic.origin_y + (tile.cell_y + tile.content_cell_y) * mosaic.resolution,
    )


def mission_floor_sequence(mission: dict, bundle: dict) -> list[int]:
    """Expand a mission into the adjacent-floor sequence used by its sidebar."""
    compiled = compile_mission(mission, bundle, verify_files=False)
    sequence = [compiled["initial_state"]["floor_id"]]
    for action in compiled["actions"]:
        floor_id = action["floor_id"]
        if floor_id != sequence[-1]:
            sequence.append(floor_id)
    return sequence


def extract_room_labels(dsg: dict, floor_heights: dict[int, float],
                        half_level_offset_m: float = 2.0) -> list[RoomLabel]:
    """Create short, stable ID labels from DSG ROOMS nodes without inventing names."""
    nodes = dsg.get("nodes") if isinstance(dsg, dict) else None
    layer_names = dsg.get("layer_names") if isinstance(dsg, dict) else None
    room_layer = (layer_names.get("ROOMS", {}).get("layer")
                  if isinstance(layer_names, dict) else None)
    if not isinstance(nodes, list) or room_layer is None:
        raise ValueError("DSG must contain nodes and a ROOMS layer definition")
    if not floor_heights or any(not math.isfinite(float(z)) for z in floor_heights.values()):
        raise ValueError("floor_heights must be a non-empty finite mapping")
    if not math.isfinite(half_level_offset_m) or half_level_offset_m <= 0:
        raise ValueError("half_level_offset_m must be positive and finite")
    candidates = []
    for node in nodes:
        if node.get("layer") != room_layer:
            continue
        attributes = node.get("attributes", {})
        position = attributes.get("position")
        if not isinstance(position, (list, tuple)) or len(position) < 3:
            raise ValueError(f"ROOMS node {node.get('id')} has no 3D position")
        x, y, z = (float(value) for value in position[:3])
        node_id = int(node["id"])
        if not all(math.isfinite(value) for value in (x, y, z)):
            raise ValueError(f"ROOMS node {node_id} has a non-finite position")
        candidates.append((node_id, x, y, z))
    candidates.sort(key=lambda item: item[0])
    labels = []
    for index, (node_id, x, y, z) in enumerate(candidates, start=1):
        floor_id = min(floor_heights, key=lambda key: abs(z - float(floor_heights[key])))
        half_level = (floor_id == max(floor_heights)
                      and z - float(floor_heights[floor_id]) > half_level_offset_m)
        labels.append(RoomLabel(node_id, f"R{index:02d}", int(floor_id),
                                x, y, z, half_level))
    return labels


def room_label_to_mosaic(room: RoomLabel, floors: dict[int, FloorGrid],
                         mosaic: MosaicGrid, max_snap_distance_m: float = 0.5
                         ) -> tuple[float, float] | None:
    """Place a room ID on the nearest free cell within a bounded snap radius."""
    floor = floors.get(room.floor_id)
    if floor is None or not math.isfinite(max_snap_distance_m) or max_snap_distance_m < 0:
        return None
    resolution = floor.resolution
    source_x = math.floor((room.x - floor.origin_x) / resolution)
    source_y = math.floor((room.y - floor.origin_y) / resolution)
    radius = math.ceil(max_snap_distance_m / resolution)
    candidates = []
    for iy in range(source_y - radius, source_y + radius + 1):
        for ix in range(source_x - radius, source_x + radius + 1):
            if not (0 <= ix < floor.width and 0 <= iy < floor.height):
                continue
            if floor.occupancy[iy * floor.width + ix] != 0:
                continue
            center_x = floor.origin_x + (ix + 0.5) * resolution
            center_y = floor.origin_y + (iy + 0.5) * resolution
            distance = math.hypot(center_x - room.x, center_y - room.y)
            if distance <= max_snap_distance_m:
                candidates.append((distance, center_x, center_y))
    if not candidates:
        return None
    _, x, y = min(candidates)
    return map_pose_to_mosaic((x, y), floor, mosaic.placements[room.floor_id], mosaic)


def as_rgb_float(color) -> tuple[float, float, float]:
    """Normalize RGB channels for ROS generated messages, which require Python floats."""
    try:
        channels = tuple(float(value) for value in color)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("RGB color must contain three finite channels") from error
    if len(channels) != 3 or not all(math.isfinite(value) for value in channels):
        raise ValueError("RGB color must contain three finite channels")
    if any(value < 0.0 or value > 1.0 for value in channels):
        raise ValueError("RGB channels must be in the range 0..1")
    return channels


def identify_floor_by_geometry(grid_geometry: dict, floors: dict[int, FloorGrid],
                               tolerance: float = 1e-5) -> int | None:
    """Return a floor only when full active map geometry matches exactly one tile."""
    try:
        width = int(grid_geometry["width"])
        height = int(grid_geometry["height"])
        resolution = float(grid_geometry["resolution"])
        origin_x = float(grid_geometry["origin_x"])
        origin_y = float(grid_geometry["origin_y"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in
               (resolution, origin_x, origin_y, tolerance)) or tolerance < 0.0:
        return None
    matches = [
        floor_id for floor_id, floor in floors.items()
        if width == floor.width and height == floor.height
        and math.isclose(resolution, floor.resolution, rel_tol=0.0, abs_tol=tolerance)
        and math.isclose(origin_x, floor.origin_x, rel_tol=0.0, abs_tol=tolerance)
        and math.isclose(origin_y, floor.origin_y, rel_tol=0.0, abs_tol=tolerance)
    ]
    return matches[0] if len(matches) == 1 else None


def floor_occupancy_matches(floor: FloorGrid, occupancy_data) -> bool:
    """Require a live map-server grid to match the verified raster cell-for-cell."""
    try:
        values = tuple(int(value) for value in occupancy_data)
    except (TypeError, ValueError, OverflowError):
        return False
    return len(values) == floor.width * floor.height and values == floor.occupancy
