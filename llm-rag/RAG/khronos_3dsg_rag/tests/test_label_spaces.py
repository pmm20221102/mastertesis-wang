"""Lightweight regressions for numeric label decoding and build provenance."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "src"))
import main
import parse_3dsg as parser
from parse_3dsg import parse_3dsg, resolve_label_space


class LabelSpaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "dsg.json"
        self.write_labels([72, 1, 5, 109, 4294967295])

    def write_labels(self, labels):
        self.source.write_text(json.dumps({
            "nodes": [{"id": i + 1, "layer": 2, "attributes": {
                "semantic_label": label, "position": [1, 2, 3]}}
                for i, label in enumerate(labels)], "edges": [],
        }), encoding="utf-8")

    def test_hospital_native_ids_and_unknown_are_decoded(self):
        graph = parse_3dsg(self.source, dataset="hospital-p1000")
        self.assertEqual([n.label for n in graph.nodes],
                         ["chair", "door", "cabinet", "nightstand", ""])
        self.assertEqual([n.raw_attrs["semantic_label"] for n in graph.nodes],
                         [72, 1, 5, 109, 4294967295])

    def test_all_109_classes_are_read_from_bundled_source(self):
        import yaml
        source = parser.HOSPITAL_LABEL_SOURCE
        expected = yaml.safe_load(source.read_bytes())["label_names"]
        self.write_labels(list(range(110)))
        graph = parse_3dsg(self.source, label_space="hospital109")
        self.assertEqual([n.label for n in graph.nodes], [entry["name"] for entry in expected])
        _, provenance = resolve_label_space("hospital_p1000")
        self.assertEqual(provenance["label_count"], 110)
        self.assertEqual(provenance["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())

    def test_missing_hospital_source_fails_without_office_fallback(self):
        with patch.object(parser, "HOSPITAL_LABEL_SOURCE", self.root / "missing.yaml"):
            with self.assertRaises(FileNotFoundError):
                parse_3dsg(self.source, dataset="hospital-p1000")

    def test_incomplete_hospital_source_fails_without_office_fallback(self):
        broken = self.root / "broken.yaml"
        broken.write_text("total_semantic_labels: 110\nlabel_names: [{label: 1, name: door}]\n")
        with patch.object(parser, "HOSPITAL_LABEL_SOURCE", broken):
            with self.assertRaisesRegex(ValueError, "unique labels 0..109"):
                parse_3dsg(self.source, dataset="hospital-p1000")

    def test_hospital_does_not_inherit_ade20k_environment(self):
        with patch.dict(os.environ, {"RAG_LABEL_SPACE": "ade20k"}):
            self.assertEqual(parse_3dsg(self.source, dataset="hospital-p1000")
                             .nodes[0].label, "chair")

    def test_hospital_rejects_conflicting_explicit_label_space(self):
        for label_space in ("office", "ade20k"):
            with self.subTest(label_space=label_space), self.assertRaisesRegex(
                    ValueError, "Hospital.*hospital109"):
                parse_3dsg(self.source, dataset="hospital-p1000", label_space=label_space)

    def test_implicit_office_rejects_out_of_range_object_ids(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
                ValueError, "label.space"):
            parse_3dsg(self.source)

    def test_office_and_ade20k_remain_available_per_call(self):
        self.write_labels([1, 5, 11])
        self.assertEqual([n.label for n in parse_3dsg(self.source, label_space="office").nodes],
                         ["ventilation_piping", "floor", "chairs"])
        self.assertEqual([n.label for n in parse_3dsg(self.source, label_space="ade20k").nodes],
                         ["wall", "stairs", "table"])
        with patch.dict(os.environ, {"RAG_LABEL_SPACE": "ade20k"}):
            self.assertEqual(parse_3dsg(self.source).nodes[0].label, "wall")

    def test_unknown_only_override_does_not_change_native_objects(self):
        overrides = self.root / "overrides.json"
        overrides.write_text(json.dumps({
            "schema_version": "hospital_object_semantic_overrides_v1",
            "items": [{"object_node_id": "5", "original_semantic_label": 4294967295,
                       "fine_label_id": None, "label_type": "human_custom",
                       "fine_label_name": "Entrance", "annotation_status": "human_confirmed"}],
        }), encoding="utf-8")
        graph = parse_3dsg(self.source, overrides, dataset="hospital-p1000")
        self.assertEqual([n.label for n in graph.nodes],
                         ["chair", "door", "cabinet", "nightstand", "Entrance"])

    def test_builder_writes_correct_corpus_and_canonical_provenance(self):
        output = self.root / "legacy"
        with patch.object(main, "build_vector_db"):
            result = main._build_one(self.source, output, {}, dataset="hospital-p1000")
        self.assertEqual(result["status"], "ok")
        graph = json.loads((output / "canonical_graph_v2.json").read_text())
        self.assertEqual(graph["nodes"]["1"]["label"], "chair")
        docs = [json.loads(line) for line in (output / "rag_documents.jsonl").read_text().splitlines()]
        self.assertTrue(any("chair" in doc["text"] for doc in docs))
        manifest = json.loads((output / "canonical_graph_v2.manifest.json").read_text())
        self.assertEqual(manifest["label_space"]["name"], "hospital109")
        label_source = PACKAGE / "config" / "tartanground_hospital_p1000_fine_label_space.yaml"
        self.assertEqual(manifest["label_space"]["source_sha256"],
                         hashlib.sha256(label_source.read_bytes()).hexdigest())

    def test_reproducible_build_binds_mapping_and_manifest(self):
        from types import SimpleNamespace
        import build_vector_db
        output = self.root / "reproducible"
        fake_collection = SimpleNamespace(count=lambda: 1)
        with patch.object(main, "build_vector_db"), \
             patch.object(build_vector_db, "load_vector_db", return_value=fake_collection), \
             patch.object(build_vector_db, "collection_content_sha256", return_value="a" * 64):
            main._build_reproducible(self.source, output, {}, dataset="hospital-p1000")
        manifest = json.loads((output / "build_manifest.json").read_text())
        self.assertEqual(manifest["build_config"]["label_space"]["name"], "hospital109")
        self.assertTrue(main._validate_build_manifest(manifest))
        self.assertEqual(manifest["build_config"]["label_space"], json.loads(
            (output / "canonical_graph_v2.manifest.json").read_text())["label_space"])
        changed_config = dict(manifest["build_config"])
        changed_config["label_space"] = dict(changed_config["label_space"], source_sha256="0" * 64)
        changed = main._build_manifest(self.source, output, None, changed_config,
                                       counts=manifest["counts"])
        self.assertNotEqual(changed["build_id"], manifest["build_id"])
        self.assertEqual(json.loads((output / "canonical_graph_v2.json").read_text())
                         ["nodes"]["1"]["label"], "chair")

    def test_legacy_cli_honors_dataset(self):
        from argparse import Namespace
        args = Namespace(config="missing.yaml", reproducible=False, review_csv="", all=False,
                         semantic_overrides="", input=str(self.source), output=str(self.root / "out"),
                         dataset="hospital-p1000", label_space="")
        with patch.object(main, "_build_one", return_value={"status": "error", "error": "test"}) as build:
            with self.assertRaises(SystemExit):
                main.cmd_build(args)
        self.assertEqual(build.call_args.kwargs["dataset"], "hospital-p1000")

    def test_batch_build_passes_configured_label_space_per_source(self):
        from argparse import Namespace
        from concurrent.futures import ThreadPoolExecutor
        args = Namespace(config="unused.yaml", reproducible=False, review_csv="", all=True,
                         semantic_overrides="", input="", output=str(self.root / "batch"),
                         dataset="", label_space="")
        config = {"dsg_sources": {"arbitrary": str(self.source)},
                  "label_spaces": {"arbitrary": "hospital109"}}
        with patch.object(main, "load_config", return_value=config), \
             patch.object(main, "ProcessPoolExecutor", ThreadPoolExecutor), \
             patch.object(main, "_build_one", return_value={
                 "status": "error", "name": "arbitrary", "error": "test"}) as build:
            main.cmd_build(args)
        self.assertEqual(build.call_args.kwargs["label_space"], "hospital109")

    def test_export_cli_uses_hospital_mapping_and_manifest(self):
        output = self.root / "export"
        subprocess.run([sys.executable, str(PACKAGE / "src" / "export_canonical_graph.py"),
                        str(self.source), "--dataset", "hospital-p1000", "-o", str(output)],
                       check=True, capture_output=True, text=True)
        self.assertEqual(json.loads((output / "canonical_graph_v2.json").read_text())
                         ["nodes"]["1"]["label"], "chair")
        self.assertEqual(json.loads((output / "canonical_graph_v2.manifest.json").read_text())
                         ["label_space"]["name"], "hospital109")

    def test_build_cli_rejects_hospital_conflict_before_indexing(self):
        output = self.root / "bad-build"
        result = subprocess.run([
            sys.executable, str(PACKAGE / "src" / "main.py"), "build", "--reproducible",
            "--input", str(self.source), "--output", str(output),
            "--dataset", "hospital-p1000", "--label-space", "office"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Hospital datasets require label space hospital109", result.stderr)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
