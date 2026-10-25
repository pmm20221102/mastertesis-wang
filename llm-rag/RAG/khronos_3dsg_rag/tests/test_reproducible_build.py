import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from main import _build_manifest, _require_new_output, _validate_build_manifest  # noqa: E402


class ReproducibleBuildTests(unittest.TestCase):
    def test_manifest_hashes_inputs_and_generated_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dsg = root / "source-dsg.json"
            overrides = root / "reviewed-overrides.json"
            output = root / "rag-output"
            output.mkdir()
            documents = output / "rag_documents.jsonl"
            documents.write_text('{"doc_id":"node-1"}\n', encoding="utf-8")
            vector_db = output / "vector_db"
            vector_db.mkdir()
            (vector_db / "chroma.sqlite3").write_bytes(b"index-fixture")
            dsg.write_bytes(b"source-graph")
            overrides.write_bytes(b"human-confirmed-labels")

            config = {
                "dataset": "hospital_p1000",
                "embedding_model": "test-embedding-model",
                "aliases": {"chairs": ["chair"]},
            }
            manifest = _build_manifest(
                dsg, output, overrides, config,
                counts={"nodes": 1, "edges": 0, "documents": 1},
            )
            repeated = _build_manifest(
                dsg, output, overrides, config,
                counts={"nodes": 1, "edges": 0, "documents": 1},
            )

            self.assertEqual(manifest, repeated)
            self.assertEqual(manifest["inputs"]["dsg"]["sha256"],
                             hashlib.sha256(b"source-graph").hexdigest())
            self.assertEqual(manifest["inputs"]["semantic_overrides"]["sha256"],
                             hashlib.sha256(b"human-confirmed-labels").hexdigest())
            self.assertEqual(manifest["artifacts"]["rag_documents.jsonl"]["sha256"],
                             hashlib.sha256(documents.read_bytes()).hexdigest())
            self.assertEqual(manifest["counts"]["documents"], 1)
            self.assertNotIn(str(root), json.dumps(manifest))
            self.assertTrue(_validate_build_manifest(manifest))

            tampered = dict(manifest)
            tampered["build_id"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "build_id"):
                _validate_build_manifest(tampered)

    def test_reproducible_build_refuses_existing_output_without_changing_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "existing-output"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("preserve", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                _require_new_output(output)

            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_build_id_is_stable_when_chroma_internal_files_differ(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dsg = root / "source-dsg.json"
            labels = root / "reviewed-overrides.json"
            dsg.write_bytes(b"same-source")
            labels.write_bytes(b"same-reviewed-labels")
            config = {"dataset": "hospital_p1000", "embedding_model": "model-v1"}
            outputs = []
            for index in (1, 2):
                output = root / f"build-{index}"
                output.mkdir()
                (output / "canonical_graph_v2.json").write_bytes(b"same-canonical-graph")
                (output / "rag_documents.jsonl").write_bytes(b"same-document-corpus")
                vector_dir = output / "vector_db" / f"random-collection-{index}"
                vector_dir.mkdir(parents=True)
                (vector_dir / "index.bin").write_bytes(f"index-{index}".encode())
                outputs.append(_build_manifest(
                    dsg, output, labels, config,
                    counts={"nodes": 1, "edges": 0, "documents": 1},
                ))

            self.assertEqual(outputs[0]["build_id"], outputs[1]["build_id"])
            self.assertNotEqual(outputs[0]["artifacts"], outputs[1]["artifacts"])


if __name__ == "__main__":
    unittest.main()
