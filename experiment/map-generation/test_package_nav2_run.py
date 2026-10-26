import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
NAV2_SCRIPTS = ROOT / "khronos-nav2-bridge" / "khronos_nav2_bridge" / "scripts"
sys.path.insert(0, str(NAV2_SCRIPTS))
sys.path.insert(0, str(ROOT / "llm-rag" / "planner"))

from fuse_places_into_occupancy import FREE, OCCUPIED, save_nav2_map  # noqa: E402
from floor_transition_core import validate_transition_bundle  # noqa: E402
from package_nav2_run import package_nav2_run  # noqa: E402
import plan_nav2  # noqa: E402


class PackageNav2RunTests(unittest.TestCase):
    def _fixture(self, root, *, occupied_portal=False):
        maps_root = root / "maps"
        floors = []
        for floor in (0, 1):
            floor_dir = maps_root / f"floor_{floor}"
            floor_dir.mkdir(parents=True)
            grid = [[FREE, FREE], [FREE, FREE]]
            if occupied_portal and floor == 1:
                grid[1][1] = OCCUPIED
            metadata = {
                "image": "map.pgm", "mode": "trinary", "width": 2, "height": 2,
                "resolution": 0.1, "origin": [0.0, 0.0, 0.0], "negate": 0,
                "occupied_thresh": 0.65, "free_thresh": 0.196,
            }
            map_yaml = save_nav2_map(floor_dir, grid, metadata)
            report = floor_dir / "report.json"
            report.write_text(json.dumps({"floor": floor}), encoding="utf-8")
            floors.append({
                "floor": floor,
                "floor_z": float(floor),
                "inputs": {"original_dsg_sha256": "a" * 64,
                           "mesh_sha256": "b" * 64},
                "output": {
                    "map_yaml": str(map_yaml),
                    "map_yaml_sha256": __import__("hashlib").sha256(
                        map_yaml.read_bytes()).hexdigest(),
                    "map_pgm": str(floor_dir / "map.pgm"),
                    "map_pgm_sha256": __import__("hashlib").sha256(
                        (floor_dir / "map.pgm").read_bytes()).hexdigest(),
                },
            })
        maps_manifest = maps_root / "manifest.json"
        maps_manifest.write_text(json.dumps({"status": "GENERATED_AND_RECOMPUTED",
                                             "map_count": 2, "floors": floors}),
                                 encoding="utf-8")

        bundle_template = root / "bundle-template.yaml"
        bundle_template.write_text(yaml.safe_dump({
            "schema_version": "hospital-floor-transitions-v1",
            "map_bundle_id": "historical-map-id",
            "frame_id": "map",
            "floors": {
                str(floor): {"floor_z_m": float(floor), "report_path": "old/report.json",
                             "map_yaml_path": "old/map.yaml", "map_yaml_sha256": "0" * 64,
                             "map_pgm_path": "old/map.pgm", "map_pgm_sha256": "0" * 64}
                for floor in (0, 1)
            },
            "transitions": [
                {"transition_id": "0_to_1", "from_floor": 0, "to_floor": 1,
                 "entry_pose": {"frame_id": "map", "x": 0.05, "y": 0.05, "yaw": 0.0},
                 "exit_pose": {"frame_id": "map", "x": 0.15, "y": 0.05, "yaw": 0.0}},
                {"transition_id": "1_to_0", "from_floor": 1, "to_floor": 0,
                 "entry_pose": {"frame_id": "map", "x": 0.15, "y": 0.05, "yaw": 0.0},
                 "exit_pose": {"frame_id": "map", "x": 0.05, "y": 0.05, "yaw": 0.0}},
            ],
        }), encoding="utf-8")

        defaults_template = root / "defaults-template.yaml"
        defaults_template.write_text(yaml.safe_dump({
            "schema_version": "hospital-nav2-plan-defaults-v1",
            "map_bundle_id": "historical-map-id",
            "model": "gemma4:e2b-it-qat",
            "rag_source": {
                "dsg_sha256": "0" * 64,
                "semantic_review_csv_sha256": "1" * 64,
                "embedding_model": "old-model",
                "embedding_model_revision": "2" * 40,
                "embedding_device": "cuda",
            },
            "initial_state": {"floor_id": 0, "pose": {
                "frame_id": "map", "x": 0.05, "y": 0.05, "yaw": 0.0}},
            "floor_z_tolerance_m": 0.25,
            "robot_model": "point",
        }), encoding="utf-8")

        rag_root = root / "rag"
        rag_root.mkdir()
        (rag_root / "canonical_graph_v2.json").write_text("{}", encoding="utf-8")
        (rag_root / "build_manifest.json").write_text(json.dumps({
            "inputs": {"dsg": {"sha256": "a" * 64}},
            "build_config": {
                "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
                "embedding_model_revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
                "embedding_device": "cpu",
            },
        }), encoding="utf-8")
        return maps_manifest, bundle_template, defaults_template, rag_root

    def test_builds_fresh_nav2_bundle_and_defaults_from_generated_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest, bundle_template, defaults_template, rag_root = self._fixture(root)
            output = root / "nav2-run"

            result = package_nav2_run(manifest, rag_root, bundle_template,
                                      defaults_template, output)

            bundle = yaml.safe_load(Path(result["bundle_path"]).read_text())
            defaults = yaml.safe_load(Path(result["defaults_path"]).read_text())
            validate_transition_bundle(bundle, verify_files=True)
            plan_nav2._load_defaults(Path(result["defaults_path"]), bundle)
            self.assertNotEqual(bundle["map_bundle_id"], "historical-map-id")
            self.assertEqual(defaults["map_bundle_id"], bundle["map_bundle_id"])
            self.assertEqual(defaults["rag_source"]["dsg_sha256"], "a" * 64)
            self.assertIsNone(defaults["rag_source"]["semantic_review_csv_sha256"])
            self.assertEqual(result["validated_portal_count"], 4)

    def test_rejects_transition_pose_not_free_in_generated_map(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest, bundle_template, defaults_template, rag_root = self._fixture(
                root, occupied_portal=True)

            with self.assertRaisesRegex(ValueError, "transition.*not free"):
                package_nav2_run(manifest, rag_root, bundle_template,
                                 defaults_template, root / "nav2-run")


if __name__ == "__main__":
    unittest.main()
