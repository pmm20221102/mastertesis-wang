import json
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
from parse_3dsg import parse_3dsg  # noqa: E402


class ParseSemanticOverridesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dsg_path = self.root / "dsg.json"
        self.override_path = self.root / "overrides.json"
        self.dsg_data = {
            "nodes": [{
                "id": 123, "layer": 2, "partition": 0,
                "attributes": {
                    "type": "ObjectNodeAttributes",
                    "semantic_label": 4294967295,
                    "name": "", "position": [1.0, 2.0, 3.0],
                },
            }],
            "edges": [],
            "layer_names": {"OBJECTS": {"layer": 2, "partition": 0}},
        }
        self.dsg_path.write_text(json.dumps(self.dsg_data), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_overrides(self, items):
        self.override_path.write_text(json.dumps({
            "schema_version": "hospital_object_semantic_overrides_v1",
            "items": items,
        }), encoding="utf-8")

    def test_applies_confirmed_fine_label_without_mutating_raw_semantic_label(self):
        self.write_overrides([{
            "object_node_id": "123", "original_semantic_label": 4294967295,
            "fine_label_id": 1, "label_type": "fine_ontology",
            "fine_label_name": "door", "annotation_status": "human_confirmed",
        }])
        graph = parse_3dsg(self.dsg_path, semantic_overrides_path=self.override_path)
        node = graph.node_index["123"]
        self.assertEqual(node.label, "door")
        self.assertEqual(node.name, "door")
        self.assertEqual(node.raw_attrs["semantic_label"], 4294967295)

    def test_without_override_file_preserves_unknown_label(self):
        node = parse_3dsg(self.dsg_path).node_index["123"]
        self.assertEqual(node.label, "")

    def test_rejects_override_when_base_node_is_not_unknown(self):
        self.dsg_data["nodes"][0]["attributes"]["semantic_label"] = 13
        self.dsg_path.write_text(json.dumps(self.dsg_data), encoding="utf-8")
        self.write_overrides([{
            "object_node_id": "123", "original_semantic_label": 4294967295,
            "fine_label_id": 1, "label_type": "fine_ontology",
            "fine_label_name": "door", "annotation_status": "human_confirmed",
        }])
        with self.assertRaisesRegex(ValueError, "not unknown"):
            parse_3dsg(self.dsg_path, semantic_overrides_path=self.override_path)

    def test_rejects_non_confirmed_annotation(self):
        self.write_overrides([{
            "object_node_id": "123", "original_semantic_label": 4294967295,
            "fine_label_id": 1, "label_type": "fine_ontology",
            "fine_label_name": "door", "annotation_status": "assistant_estimate",
        }])
        with self.assertRaisesRegex(ValueError, "human_confirmed"):
            parse_3dsg(self.dsg_path, semantic_overrides_path=self.override_path)

    def test_rejects_duplicate_node_ids(self):
        item = {
            "object_node_id": "123", "original_semantic_label": 4294967295,
            "fine_label_id": 1, "label_type": "fine_ontology",
            "fine_label_name": "door", "annotation_status": "human_confirmed",
        }
        self.write_overrides([item, item])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_3dsg(self.dsg_path, semantic_overrides_path=self.override_path)

    def test_rejects_sidecar_node_missing_from_dsg(self):
        self.write_overrides([{
            "object_node_id": "456", "original_semantic_label": 4294967295,
            "fine_label_id": 1, "label_type": "fine_ontology",
            "fine_label_name": "door", "annotation_status": "human_confirmed",
        }])
        with self.assertRaisesRegex(ValueError, "missing from DSG"):
            parse_3dsg(self.dsg_path, semantic_overrides_path=self.override_path)

    def test_rejects_non_object_sidecar_document(self):
        self.override_path.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "JSON object"):
            parse_3dsg(self.dsg_path, semantic_overrides_path=self.override_path)

    def test_rejects_ontology_label_without_valid_id(self):
        self.write_overrides([{
            "object_node_id": "123", "original_semantic_label": 4294967295,
            "fine_label_id": None, "label_type": "fine_ontology",
            "fine_label_name": "door", "annotation_status": "human_confirmed",
        }])
        with self.assertRaisesRegex(ValueError, "fine_label_id"):
            parse_3dsg(self.dsg_path, semantic_overrides_path=self.override_path)

    def test_rejects_custom_label_with_ontology_id(self):
        self.write_overrides([{
            "object_node_id": "123", "original_semantic_label": 4294967295,
            "fine_label_id": 1, "label_type": "human_custom",
            "fine_label_name": "Entrance", "annotation_status": "human_confirmed",
        }])
        with self.assertRaisesRegex(ValueError, "custom label.*fine_label_id"):
            parse_3dsg(self.dsg_path, semantic_overrides_path=self.override_path)


if __name__ == "__main__":
    unittest.main()
