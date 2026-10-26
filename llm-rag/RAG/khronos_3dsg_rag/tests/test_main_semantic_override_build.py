import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "src"))
import main  # noqa: E402


class SemanticOverrideBuildTests(unittest.TestCase):
    def test_build_without_review_overrides_still_writes_canonical_graph(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dsg_path = root / "dsg.json"
            output_dir = root / "rag-output"
            dsg_path.write_text(json.dumps({
                "nodes": [{
                    "id": 123, "layer": 2, "partition": 0,
                    "attributes": {
                        "type": "ObjectNodeAttributes",
                        "semantic_label": 6,
                        "name": "chair", "position": [1.0, 2.0, 3.0],
                    },
                }],
                "edges": [],
                "layer_names": {"OBJECTS": {"layer": 2, "partition": 0}},
            }), encoding="utf-8")

            with patch.object(main, "build_all_documents", return_value=[]), \
                 patch.object(main, "build_vector_db"):
                result = main._build_one(
                    str(dsg_path), str(output_dir), {}, dataset="hospital-p1000")

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["canonical_graph"],
                             str(output_dir / "canonical_graph_v2.json"))
            graph = json.loads((output_dir / "canonical_graph_v2.json").read_text(
                encoding="utf-8"))
            self.assertTrue(graph["nodes"]["123"]["label"])

    def test_single_build_writes_canonical_graph_with_manual_label(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dsg_path = root / "dsg.json"
            override_path = root / "overrides.json"
            output_dir = root / "rag-output"
            dsg_path.write_text(json.dumps({
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
            }), encoding="utf-8")
            override_path.write_text(json.dumps({
                "schema_version": "hospital_object_semantic_overrides_v1",
                "items": [{
                    "object_node_id": "123",
                    "original_semantic_label": 4294967295,
                    "fine_label_id": None,
                    "fine_label_name": "Entrance",
                    "label_type": "human_custom",
                    "annotation_status": "human_confirmed",
                }],
            }), encoding="utf-8")

            with patch.object(main, "build_all_documents", return_value=[]), \
                 patch.object(main, "build_vector_db"):
                result = main._build_one(
                    str(dsg_path), str(output_dir), {}, dataset="hospital-test",
                    semantic_overrides_path=str(override_path),
                )

            self.assertEqual(result["status"], "ok")
            graph = json.loads((output_dir / "canonical_graph_v2.json").read_text(encoding="utf-8"))
            self.assertEqual(graph["nodes"]["123"]["label"], "Entrance")


if __name__ == "__main__":
    unittest.main()
