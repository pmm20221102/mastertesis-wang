#!/usr/bin/env python3
"""Verify the checked-in Hospital route bundle resolves from the repo root."""

from pathlib import Path
import hashlib
import json
import sys

import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from floor_transition_core import validate_transition_bundle  # noqa: E402


def test_hospital_default_bundle_uses_relative_verified_map_assets():
    bundle_path = (ROOT / "khronos-nav2-bridge/khronos_nav2_bridge/config"
                   / "hospital_p1000_floor_transitions.yaml")
    bundle = yaml.safe_load(bundle_path.read_text(encoding="utf-8"))

    for floor in bundle["floors"].values():
        for key in ("report_path", "map_yaml_path", "map_pgm_path"):
            assert not Path(floor[key]).is_absolute(), (key, floor[key])

    asset_root = ROOT / "khronos-nav2-bridge/khronos_nav2_bridge/generated/hospital-p1000-khronos-only"
    manifest = json.loads((asset_root / "map_manifest.json").read_text(encoding="utf-8"))
    for field in ("generator", "grid_geometry_manifest"):
        source = ROOT / "experiment/map-generation" / Path(
            manifest["inputs"][field]).name
        expected = manifest["inputs"][f"{field}_sha256"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
    for floor, config in bundle["floors"].items():
        record = manifest["floors"][str(floor)]
        assert hashlib.sha256((ROOT / config["map_yaml_path"]).read_bytes()).hexdigest() == record[
            "map_yaml_sha256"]
        assert hashlib.sha256((ROOT / config["map_pgm_path"]).read_bytes()).hexdigest() == record[
            "map_pgm_sha256"]

    assert validate_transition_bundle(bundle, verify_files=True)["map_bundle_id"] == (
        "hospital-p1000-khronos-only-20261019-band005-200")


def test_hospital_nav2_launch_defaults_use_the_packaged_khronos_only_map():
    launch_root = ROOT / "khronos-nav2-bridge/khronos_nav2_bridge/launch"
    for name in ("hospital_nav2.launch.py", "hospital_nav2_rviz.launch.py",
                 "hospital_nav2_multifloor_rviz.launch.py"):
        source = (launch_root / name).read_text(encoding="utf-8")
        assert "hospital-p1000-khronos-only" in source, name
        assert "/home/nenlian/" not in source, name


if __name__ == "__main__":
    test_hospital_default_bundle_uses_relative_verified_map_assets()
    print("Hospital map bundle: PASS")
