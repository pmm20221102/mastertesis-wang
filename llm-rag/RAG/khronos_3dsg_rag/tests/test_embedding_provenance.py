import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import build_vector_db as builder  # noqa: E402


class EmbeddingProvenanceTests(unittest.TestCase):
    def test_collection_digest_is_order_independent_and_covers_vectors(self):
        class FakeCollection:
            def __init__(self, rows):
                self.rows = rows

            def get(self, include):
                assert include == ["embeddings", "documents", "metadatas"]
                rows = self.rows
                return {"ids": [r[0] for r in rows],
                        "documents": [r[1] for r in rows],
                        "metadatas": [r[2] for r in rows],
                        "embeddings": [r[3] for r in rows]}

        rows = [
            ("b", "door", {"node": "2"}, [0.25, 0.75]),
            ("a", "chair", {"node": "1"}, [0.5, 0.5]),
        ]
        original = builder.collection_content_sha256(FakeCollection(rows))
        reordered = builder.collection_content_sha256(FakeCollection(list(reversed(rows))))
        changed_vector = builder.collection_content_sha256(FakeCollection([
            rows[0], ("a", "chair", {"node": "1"}, [0.6, 0.4])]))

        self.assertEqual(original, reordered)
        self.assertNotEqual(original, changed_vector)

    def test_sentence_transformer_uses_the_pinned_revision(self):
        calls = []

        class FakeSentenceTransformer:
            def __init__(self, model_name, revision=None, device=None):
                calls.append((model_name, revision, device))

            def encode(self, texts, show_progress_bar=False):
                return types.SimpleNamespace(
                    tolist=lambda: [[0.25, 0.75] for _ in texts])

        fake_module = types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer)
        with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
            embedding = builder.SentenceTransformerEmbeddingFunction(
                "embedding-model", revision="a" * 40, device="cpu")
            vectors = embedding(["one", "two"])

        self.assertEqual(calls, [("embedding-model", "a" * 40, "cpu")])
        self.assertEqual([list(vector) for vector in vectors],
                         [[0.25, 0.75], [0.25, 0.75]])

    def test_loader_uses_revision_recorded_in_collection_metadata(self):
        calls = []
        collection = types.SimpleNamespace(metadata={
            "embedding_model": "embedding-model",
            "embedding_model_revision": "b" * 40,
            "embedding_device": "cpu",
        })

        class FakeClient:
            def get_collection(self, name, embedding_function=None):
                calls.append((name, embedding_function))
                return collection

        with tempfile.TemporaryDirectory() as temp_dir, \
             patch.object(builder.chromadb, "PersistentClient", return_value=FakeClient()), \
             patch.object(builder, "SentenceTransformerEmbeddingFunction",
                           side_effect=lambda model, revision=None, device=None:
                           (model, revision, device)):
            loaded = builder.load_vector_db(temp_dir)

        self.assertIs(loaded, collection)
        self.assertEqual(calls[0], ("scene_graph", None))
        self.assertEqual(calls[1], ("scene_graph", ("embedding-model", "b" * 40, "cpu")))


if __name__ == "__main__":
    unittest.main()
