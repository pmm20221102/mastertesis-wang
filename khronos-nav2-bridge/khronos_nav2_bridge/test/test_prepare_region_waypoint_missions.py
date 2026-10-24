import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import importlib.util


root = pathlib.Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    temp = pathlib.Path(directory)
    pgm = temp / "map.pgm"
    width = height = 30
    pixels = [255] * (width * height)
    pixels[(height - 1 - 10) * width + 10] = 0
    pixels[(height - 1 - 14) * width + 19] = 0
    pgm.write_text("P2\n30 30\n255\n" + " ".join(map(str, pixels)) + "\n")
    map_yaml = temp / "map.yaml"
    map_yaml.write_text("image: map.pgm\nresolution: 0.1\norigin: [0.0, 0.0, 0.0]\n")
    graph = {
        "header": {"frame": "map"},
        "nodes": [
            {"id": 1, "pose_stamped": {"header": {"frame": "map"}, "orientation": [1, 0, 0, 0], "position": [1.0, 1.0, 0]}},
            {"id": 2, "pose_stamped": {"header": {"frame": "map"}, "orientation": [1, 0, 0, 0], "position": [2.0, 2.0, 0]}},
        ],
        "edges": [],
    }
    graph_path = temp / "graph.json"
    graph_path.write_text(json.dumps(graph))
    original_path = temp / "original.json"
    original_path.write_text(json.dumps(graph))
    semantic_map = temp / "semantic.json"
    semantic_map.write_text(json.dumps({"room_to_waypoint": {
        "room-a": {"status": "ok", "waypoint_id": 1},
        "room-b": {"status": "ok", "waypoint_id": 2},
    }}))
    case = {
        "case_id": "case-01",
        "instruction": "route",
        "expected_room_ids": ["room-a", "room-b"],
        "waypoint_ids": [1, 2],
        "expected_waypoints": 2,
    }
    corpus = temp / "corpus.jsonl"
    corpus.write_text(json.dumps(case) + "\n")
    selected_file = temp / "selected.json"
    selected_file.write_text("{}")
    selection = temp / "selection.json"
    selection.write_text(json.dumps({
        "all_cases_pass": True,
        "semantic_passes": 1,
        "rows": [{
            "case_id": "case-01",
            "selected_model": "model",
            "semantic_pass": True,
            "selected_output": str(selected_file),
        }],
    }))
    output = temp / "missions.json"
    overrides = temp / "overrides.json"
    overrides.write_text(json.dumps({
        "schema_version": "region-approach-overrides-v1",
        "targets": {
            "1": {
                "waypoint_id": 1,
                "nav2_pose": {
                    "x": 1.6,
                    "y": 1.4,
                    "z": 0.0,
                    "yaw": 1.5707963267948966,
                },
                "trial_successes": 2,
                "trial_count": 2,
            }
        },
    }))
    completed = subprocess.run([
        sys.executable,
        str(root / "scripts/prepare_region_waypoint_missions.py"),
        "--corpus", str(corpus),
        "--selection", str(selection),
        "--semantic-map", str(semantic_map),
        "--graph", str(graph_path),
        "--original-graph", str(original_path),
        "--map", str(map_yaml),
        "--output", str(output),
        "--start-x", "2.5",
        "--start-y", "2.5",
        "--start-yaw", "0.25",
        "--approach-overrides", str(overrides),
    ], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text())

assert result["semantic_gate"]["passes"] == 1
assert result["navigation_start"] == {"x": 2.5, "y": 2.5, "yaw": 0.25}
assert result["geometry_gate"]["eligible"] is True
assert result["geometry_gate"]["distinct_targets"] == 2
assert result["geometry_gate"]["start_connected_targets"] == 2
assert result["geometry_gate"]["total_waypoints"] == 2
assert result["targets"]["1"]["projection_distance_m"] <= 0.8
assert result["targets"]["1"]["footprint_safe"] is True
assert result["targets"]["1"]["nav2_pose"] == {
    "x": 1.6,
    "y": 1.4,
    "z": 0.0,
    "yaw": 1.5707963267948966,
}
assert result["targets"]["1"]["projection_method"] == "validated_approach_override"

spec = importlib.util.spec_from_file_location(
    "prepare_region_waypoint_missions",
    root / "scripts/prepare_region_waypoint_missions.py",
)
sys.path.insert(0, str(root / "scripts"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as candidate_directory:
    candidate_temp = pathlib.Path(candidate_directory)
    candidate_pgm = candidate_temp / "map.pgm"
    candidate_pgm.write_text(
        "P2\n30 30\n255\n" + " ".join(map(str, pixels)) + "\n"
    )
    candidate_yaml = candidate_temp / "map.yaml"
    candidate_yaml.write_text(
        "image: map.pgm\nresolution: 0.1\norigin: [0.0, 0.0, 0.0]\n"
    )
    map_data = module.loadmap(candidate_yaml)
    candidates = module.approach_candidates(
        {"x": 1.0, "y": 1.0, "z": 0.0, "yaw": 0.0},
        map_data,
        {"x": 2.5, "y": 2.5, "yaw": 0.25},
        max_distance=0.8,
        yaw_steps=8,
        limit=6,
    )
    assert len(candidates) == 6
    assert [row["clearance_m"] for row in candidates] == sorted(
        [row["clearance_m"] for row in candidates], reverse=True
    )
    assert all(row["projection_distance_m"] <= 0.8 for row in candidates)
    assert all(module.safe(row["nav2_pose"], map_data)[0] for row in candidates)
    assert all(row["start_connected"] for row in candidates)
    assert len({tuple(row["cell"]) for row in candidates}) >= 3
    assert len({round(row["nav2_pose"]["yaw"], 6) for row in candidates}) > 1
