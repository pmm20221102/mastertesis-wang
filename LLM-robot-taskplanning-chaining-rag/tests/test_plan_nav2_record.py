from pathlib import Path
import json
import sys
import tempfile

import pytest
import chromadb
from chromadb.config import Settings
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import plan_nav2  # noqa: E402
from main import _build_manifest  # noqa: E402
import build_vector_db as vector_builder  # noqa: E402


def test_plan_record_marks_path_planning_as_non_execution_and_keeps_provenance():
    pipeline = {"status": "valid", "task_plan": {"status": "valid",
                "task_chain": [{"step": 1, "action": "navigate_to"}]}}
    mission_result = {"status": "ready", "mission": {"task_id": "instruction-1"}}
    route_result = {"status": "planned", "execution_performed": False,
                    "route_selection": {"selected_target_ids": ["room-1"],
                                        "segment_results": [{"path_length_m": 4.0}]}}
    metadata = {
        "model_name": "gemma4:e2b-it-qat",
        "model_digest": "sha256:model",
        "prompt_profile": "hospital_taskchain_surface_v1",
        "rag_build_id": "rag-build-1",
        "rag_manifest_sha256": "sha256:rag",
        "map_bundle_id": "hospital-map-1",
        "map_bundle_sha256": "sha256:map",
        "start_profile_sha256": "sha256:start",
        "llm_runtime_options": {"num_ctx": 4096, "num_predict": 512,
                                "request_timeout_s": 360},
        "ollama_config_sha256": "a" * 64,
        "rag_vector_content_sha256": "b" * 64,
    }

    result = plan_nav2.build_plan_record(
        "Take me to the room with the bed.", pipeline, mission_result,
        route_result, metadata,
    )

    assert result["status"] == "planned"
    assert result["execution_performed"] is False
    assert result["model"] == {"name": "gemma4:e2b-it-qat", "digest": "sha256:model"}
    assert result["provenance"]["rag_build_id"] == "rag-build-1"
    assert result["provenance"]["map_bundle_sha256"] == "sha256:map"
    assert result["provenance"]["llm_runtime_options"]["request_timeout_s"] == 360
    assert len(result["provenance"]["ollama_config_sha256"]) == 64
    assert result["provenance"]["rag_vector_content_sha256"] == "b" * 64
    assert result["nav2_plan"]["route_selection"]["segment_results"][0]["path_length_m"] == 4.0


def test_invalid_pipeline_never_becomes_a_successful_plan_record():
    result = plan_nav2.build_plan_record(
        "Find a room with a bed.",
        {"status": "ambiguous", "error": "please clarify"},
        {"status": "not_attempted"},
        {"status": "not_attempted"},
        {"model_name": "gemma4:e2b-it-qat"},
    )

    assert result["status"] == "ambiguous"
    assert result["execution_performed"] is False
    assert result["nav2_plan"]["status"] == "not_attempted"


def test_pipeline_provenance_hashes_prompt_grounding_and_nav2_adapters():
    hashes = plan_nav2._prompt_source_hashes()

    assert {"intent_schema", "grounding", "rag_index", "rag_config",
            "task_plan_schema", "nav2_mission_adapter"} <= hashes.keys()
    assert all(len(value) == 64 for value in hashes.values())


def test_ros_plan_runner_uses_repository_root_for_relative_map_bundle_paths(
        monkeypatch, tmp_path):
    calls = {}

    def fake_run(command, **kwargs):
        calls.update(kwargs)
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(plan_nav2.subprocess, "run", fake_run)
    plan_nav2._run_ros_plan(tmp_path / "mission.json", tmp_path / "bundle.yaml",
                            tmp_path / "plan.json", "point", 77)

    assert calls["cwd"] == plan_nav2.ROOT


def test_rag_build_verification_checks_source_and_embedding_device():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "hospital_rag"
        vector = root / "vector_db"
        vector.mkdir(parents=True)
        dsg = Path(temp_dir) / "dsg.json"
        labels = Path(temp_dir) / "reviews.csv"
        dsg.write_text("dsg", encoding="utf-8")
        labels.write_text("reviewed labels", encoding="utf-8")
        build_config = {
            "dataset": "hospital_p1000",
            "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
            "embedding_model_revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
            "embedding_device": "cuda",
        }
        (root / "build_config.json").write_text(json.dumps(build_config), encoding="utf-8")
        (root / "canonical_graph_v2.json").write_text("{}", encoding="utf-8")
        (root / "rag_documents.jsonl").write_text('{"doc_id":"1"}\n', encoding="utf-8")
        (vector / "chroma.sqlite3").write_bytes(b"vector index")
        manifest = _build_manifest(dsg, root, labels, build_config,
                                   counts={"nodes": 1, "edges": 0, "documents": 1})
        (root / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        expected_source = {
            "dsg_sha256": manifest["inputs"]["dsg"]["sha256"],
            "semantic_review_csv_sha256": manifest["inputs"]["semantic_overrides"]["sha256"],
            "embedding_model": build_config["embedding_model"],
            "embedding_model_revision": build_config["embedding_model_revision"],
            "embedding_device": "cpu",
        }

        with pytest.raises(ValueError, match="embedding device"):
            plan_nav2.verify_rag_build(vector, expected_source=expected_source)


def test_rag_verification_allows_chroma_runtime_metadata_updates():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "hospital_rag"
        vector = root / "vector_db"
        vector.mkdir(parents=True)
        dsg = Path(temp_dir) / "dsg.json"
        labels = Path(temp_dir) / "reviews.csv"
        dsg.write_text("dsg", encoding="utf-8")
        labels.write_text("reviewed labels", encoding="utf-8")
        config = {
            "dataset": "hospital_p1000",
            "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
            "embedding_model_revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
            "embedding_device": "cpu",
        }
        (root / "build_config.json").write_text(json.dumps(config), encoding="utf-8")
        (root / "canonical_graph_v2.json").write_text("{}", encoding="utf-8")
        (root / "rag_documents.jsonl").write_text('{"doc_id":"1"}\n', encoding="utf-8")
        client = chromadb.PersistentClient(
            path=str(vector), settings=Settings(anonymized_telemetry=False))
        embedding = vector_builder.SentenceTransformerEmbeddingFunction(
            config["embedding_model"],
            revision=config["embedding_model_revision"], device="cpu")
        embedding._model = type("FakeModel", (), {
            "encode": lambda self, texts, show_progress_bar=False:
            np.tile(np.array([[1.0, 0.0]]), (len(texts), 1))})()
        collection = client.create_collection(
            "scene_graph",
            embedding_function=embedding,
            metadata={"hnsw:space": "cosine", "embedding_model": config["embedding_model"],
                      "embedding_model_revision": config["embedding_model_revision"],
                      "embedding_device": "cpu"},
        )
        collection.add(ids=["1"], documents=["chair"], metadatas=[{"node_id": "1"}])
        content_manifest = {
            "schema_version": "hospital-rag-vector-content-v1",
            "document_count": collection.count(),
            "content_sha256": vector_builder.collection_content_sha256(collection),
        }
        (root / "vector_content_manifest.json").write_text(
            json.dumps(content_manifest), encoding="utf-8")
        manifest = _build_manifest(dsg, root, labels, config,
                                   counts={"nodes": 1, "edges": 0, "documents": 1})
        (root / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        collection.modify(metadata={
            "embedding_model": config["embedding_model"],
            "embedding_model_revision": config["embedding_model_revision"],
            "embedding_device": "cpu", "runtime_note": "Chroma persists access metadata",
        })
        loaded = vector_builder.load_vector_db(vector)
        assert loaded.count() == 1
        loaded.query(query_embeddings=[[1.0, 0.0]], n_results=1)
        expected_source = {
            "dsg_sha256": manifest["inputs"]["dsg"]["sha256"],
            "semantic_review_csv_sha256": manifest["inputs"]["semantic_overrides"]["sha256"],
            "embedding_model": config["embedding_model"],
            "embedding_model_revision": config["embedding_model_revision"],
            "embedding_device": config["embedding_device"],
        }

        result = plan_nav2.verify_rag_build(vector, expected_source=expected_source)

        assert result["document_count"] == 1
        collection.update(ids=["1"], embeddings=[[0.1, 0.9]])
        with pytest.raises(ValueError, match="vector records/embeddings"):
            plan_nav2.verify_rag_build(vector, expected_source=expected_source)
