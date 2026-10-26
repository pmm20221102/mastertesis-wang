#!/usr/bin/env python3
"""Package generated Khronos maps and a RAG build as a Nav2 run profile."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
NAV2_PACKAGE = ROOT / "khronos-nav2-bridge" / "khronos_nav2_bridge"
NAV2_SCRIPTS = NAV2_PACKAGE / "scripts"
if str(NAV2_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(NAV2_SCRIPTS))

from floor_transition_core import validate_transition_bundle  # noqa: E402
from fuse_places_into_occupancy import (  # noqa: E402
    FREE,
    load_nav2_map,
    world_to_image_cell,
)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a YAML mapping: {path}")
    return value


def _require_hash(value, name: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _check_free_pose(floor: int, pose: dict, map_data: dict) -> dict:
    grid, metadata = map_data
    cell = world_to_image_cell(
        float(pose["x"]), float(pose["y"]), metadata["origin"],
        metadata["resolution"], metadata["width"], metadata["height"],
    )
    if cell is None or grid[cell[0]][cell[1]] != FREE:
        raise ValueError(f"pose on floor {floor} is not free in generated map")
    return {"floor": floor, "x": float(pose["x"]), "y": float(pose["y"]),
            "cell_row": cell[0], "cell_column": cell[1]}


def package_nav2_run(maps_manifest_path: str | Path, rag_build_dir: str | Path,
                     bundle_template_path: str | Path, defaults_template_path: str | Path,
                     output_dir: str | Path) -> dict:
    """Create hash-pinned Nav2 bundle/defaults and reject invalid portal cells."""
    maps_manifest_path = Path(maps_manifest_path).expanduser().resolve(strict=True)
    rag_root = Path(rag_build_dir).expanduser().resolve(strict=True)
    bundle_template_path = Path(bundle_template_path).expanduser().resolve(strict=True)
    defaults_template_path = Path(defaults_template_path).expanduser().resolve(strict=True)
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing run profile: {output}")

    maps_manifest = json.loads(maps_manifest_path.read_text(encoding="utf-8"))
    if maps_manifest.get("status") != "GENERATED_AND_RECOMPUTED":
        raise ValueError("map manifest is not marked GENERATED_AND_RECOMPUTED")
    raw_floors = maps_manifest.get("floors")
    if not isinstance(raw_floors, list) or not raw_floors:
        raise ValueError("map manifest must contain generated floors")
    generated = {}
    source_dsg_hashes = set()
    source_mesh_hashes = set()
    map_data = {}
    for item in raw_floors:
        floor = item.get("floor")
        if isinstance(floor, bool) or not isinstance(floor, int) or floor < 0:
            raise ValueError("map manifest floor IDs must be non-negative integers")
        inputs = item.get("inputs", {})
        source_dsg_hashes.add(_require_hash(inputs.get("original_dsg_sha256"),
                                            "map source DSG hash"))
        source_mesh_hashes.add(_require_hash(inputs.get("mesh_sha256"),
                                             "map source mesh hash"))
        map_output = item.get("output", {})
        yaml_path = Path(map_output.get("map_yaml", "")).expanduser().resolve(strict=True)
        pgm_path = Path(map_output.get("map_pgm", "")).expanduser().resolve(strict=True)
        if yaml_path.parent != pgm_path.parent:
            raise ValueError(f"floor {floor} map YAML and image must share a directory")
        yaml_hash = _require_hash(map_output.get("map_yaml_sha256"),
                                  f"floor {floor} YAML hash")
        pgm_hash = _require_hash(map_output.get("map_pgm_sha256"),
                                 f"floor {floor} image hash")
        if _sha256_file(yaml_path) != yaml_hash or _sha256_file(pgm_path) != pgm_hash:
            raise ValueError(f"floor {floor} generated map hash mismatch")
        report_path = yaml_path.parent / "report.json"
        if not report_path.is_file():
            raise FileNotFoundError(f"floor {floor} map report is missing: {report_path}")
        generated[str(floor)] = {
            "floor_z_m": float(item["floor_z"]),
            "report_path": str(report_path),
            "map_yaml_path": str(yaml_path),
            "map_yaml_sha256": yaml_hash,
            "map_pgm_path": str(pgm_path),
            "map_pgm_sha256": pgm_hash,
        }
        map_data[floor] = load_nav2_map(yaml_path)
    if len(source_dsg_hashes) != 1 or len(source_mesh_hashes) != 1:
        raise ValueError("all generated floors must use the same source DSG and mesh")
    if len(generated) != len(raw_floors):
        raise ValueError("map manifest contains duplicate floor IDs")

    rag_manifest_path = rag_root / "build_manifest.json"
    canonical_graph_path = rag_root / "canonical_graph_v2.json"
    if not rag_manifest_path.is_file() or not canonical_graph_path.is_file():
        raise FileNotFoundError("RAG build must include its manifest and canonical graph")
    rag_manifest = json.loads(rag_manifest_path.read_text(encoding="utf-8"))
    rag_inputs = rag_manifest.get("inputs", {})
    rag_dsg_hash = _require_hash(rag_inputs.get("dsg", {}).get("sha256"),
                                 "RAG source DSG hash")
    if rag_dsg_hash != next(iter(source_dsg_hashes)):
        raise ValueError("RAG and map artifacts were built from different DSG files")
    build_config = rag_manifest.get("build_config", {})
    expected_rag = {
        "dsg_sha256": rag_dsg_hash,
        "semantic_review_csv_sha256": (rag_inputs.get("semantic_overrides") or {}).get("sha256"),
        "embedding_model": build_config.get("embedding_model"),
        "embedding_model_revision": build_config.get("embedding_model_revision"),
        "embedding_device": build_config.get("embedding_device"),
    }
    for key in ("embedding_model", "embedding_model_revision", "embedding_device"):
        if not isinstance(expected_rag[key], str) or not expected_rag[key]:
            raise ValueError(f"RAG manifest is missing {key}")
    if expected_rag["semantic_review_csv_sha256"] is not None:
        _require_hash(expected_rag["semantic_review_csv_sha256"],
                      "RAG review CSV hash")

    bundle = _load_yaml(bundle_template_path)
    template_floors = bundle.get("floors")
    if not isinstance(template_floors, dict) or set(map(str, template_floors)) != set(generated):
        raise ValueError("transition template floors do not match generated map floors")
    bundle["floors"] = {
        str(floor): {**template_floors.get(floor, template_floors.get(str(floor), {})),
                     **generated[str(floor)]}
        for floor in sorted(map(int, generated))
    }
    if not isinstance(bundle.get("transitions"), list) or not bundle["transitions"]:
        raise ValueError("transition template must contain reviewed floor transitions")

    checked_poses = []
    for transition in bundle["transitions"]:
        for side, floor_key in (("entry_pose", "from_floor"), ("exit_pose", "to_floor")):
            floor = transition[floor_key]
            if floor not in map_data:
                raise ValueError(f"transition references missing floor {floor}")
            try:
                checked = _check_free_pose(floor, transition[side], map_data[floor])
            except ValueError as error:
                raise ValueError(
                    f"transition {transition['transition_id']} {side} is not free in floor {floor} map"
                ) from error
            checked["transition_id"] = transition["transition_id"]
            checked["side"] = side
            checked_poses.append(checked)

    defaults = _load_yaml(defaults_template_path)
    initial = defaults.get("initial_state", {})
    initial_floor = initial.get("floor_id")
    if initial_floor not in map_data or not isinstance(initial.get("pose"), dict):
        raise ValueError("defaults initial state must reference a generated floor and pose")
    try:
        initial_check = _check_free_pose(initial_floor, initial["pose"],
                                         map_data[initial_floor])
    except ValueError as error:
        raise ValueError(f"initial pose is not free in floor {initial_floor} map") from error
    checked_poses.append({"transition_id": "initial_state", "side": "pose",
                          **initial_check})

    identity = {
        "floors": {floor: {"yaml": config["map_yaml_sha256"],
                           "pgm": config["map_pgm_sha256"]}
                   for floor, config in sorted(bundle["floors"].items())},
        "transitions": bundle["transitions"],
    }
    bundle_digest = hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    bundle["map_bundle_id"] = f"hospital-p1000-khronos-run-{bundle_digest[:16]}"
    defaults["map_bundle_id"] = bundle["map_bundle_id"]
    defaults["rag_source"] = expected_rag
    if (not isinstance(defaults.get("model"), str) or not defaults["model"]
            or defaults.get("robot_model") not in ("point", "footprint")):
        raise ValueError("defaults template must pin a model and robot_model")
    validate_transition_bundle(bundle, verify_files=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    try:
        bundle_path = staging / "hospital_floor_transitions.yaml"
        defaults_path = staging / "hospital_nav2_plan_defaults.yaml"
        bundle_path.write_text(yaml.safe_dump(bundle, sort_keys=False), encoding="utf-8")
        defaults_path.write_text(yaml.safe_dump(defaults, sort_keys=False), encoding="utf-8")
        profile_manifest = {
            "schema_version": "hospital-nav2-run-profile-v1",
            "map_bundle_id": bundle["map_bundle_id"],
            "source_dsg_sha256": rag_dsg_hash,
            "source_mesh_sha256": next(iter(source_mesh_hashes)),
            "rag_build_id": rag_manifest.get("build_id"),
            "rag_manifest_sha256": _sha256_file(rag_manifest_path),
            "review_csv_sha256": expected_rag["semantic_review_csv_sha256"],
            "checked_free_poses": checked_poses,
            "bundle_sha256": _sha256_file(bundle_path),
            "defaults_sha256": _sha256_file(defaults_path),
        }
        (staging / "run_profile_manifest.json").write_text(
            json.dumps(profile_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.rename(staging, output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {"output_dir": str(output), "bundle_path": str(output / bundle_path.name),
            "defaults_path": str(output / defaults_path.name),
            "manifest_path": str(output / "run_profile_manifest.json"),
            "map_bundle_id": bundle["map_bundle_id"],
            "validated_portal_count": len(checked_poses) - 1}


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maps-manifest", type=Path, required=True)
    parser.add_argument("--rag-build", type=Path, required=True)
    parser.add_argument("--bundle-template", type=Path,
                        default=NAV2_PACKAGE / "config" / "hospital_p1000_floor_transitions.yaml")
    parser.add_argument("--defaults-template", type=Path,
                        default=NAV2_PACKAGE / "config" / "hospital_nav2_plan_defaults.yaml")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = package_nav2_run(args.maps_manifest, args.rag_build, args.bundle_template,
                              args.defaults_template, args.output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
