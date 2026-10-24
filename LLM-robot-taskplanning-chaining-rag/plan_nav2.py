#!/usr/bin/env python3
"""Plan a Hospital Nav2 route from one natural-language instruction."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.error import URLError
from urllib.request import Request, urlopen

import yaml


ROOT = Path(__file__).resolve().parents[1]
LLM_DIR = ROOT / "LLM-robot-taskplanning-chaining-rag"
RAG_DIR = ROOT / "RAG" / "khronos_3dsg_rag"
NAV2_PACKAGE = ROOT / "khronos-nav2-bridge" / "khronos_nav2_bridge"
NAV2_SCRIPTS = NAV2_PACKAGE / "scripts"
DEFAULT_MODEL = "gemma4:e2b-it-qat"
DEFAULT_RAG_DIR = RAG_DIR / "outputs" / "hospital_p1000_reproducible_20261024_v5" / "vector_db"
DEFAULT_BUNDLE = NAV2_PACKAGE / "config" / "hospital_p1000_floor_transitions.yaml"
DEFAULTS_PATH = NAV2_PACKAGE / "config" / "hospital_nav2_plan_defaults.yaml"
RUNNER_SCRIPT = NAV2_SCRIPTS / "run_plan_only.sh"

for path in (LLM_DIR, RAG_DIR / "src", NAV2_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from main_rag import load_config, run_structured_task_planner  # noqa: E402
from main import _validate_build_manifest  # noqa: E402
from prepare_instruction_mission import build_candidate_mission  # noqa: E402


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def verify_rag_build(db_dir: str | Path, *, expected_source: dict | None = None) -> dict:
    """Verify the generated RAG artifact against its build manifest."""
    database = Path(db_dir).expanduser().resolve(strict=True)
    if database.name != "vector_db":
        raise ValueError("RAG DB must be the vector_db child of a manifest-bearing build")
    root = database.parent
    manifest_path = root / "build_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"reproducible RAG manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") not in (
            "hospital-rag-build-manifest-v1", "hospital-rag-build-manifest-v2",
            "hospital-rag-build-manifest-v3"):
        raise ValueError("unsupported RAG build manifest schema")
    _validate_build_manifest(manifest)
    build_config = manifest.get("build_config")
    if not isinstance(build_config, dict) or build_config.get("dataset") != "hospital_p1000":
        raise ValueError("RAG build is not labeled as Hospital P1000")
    if not build_config.get("embedding_model_revision"):
        raise ValueError("RAG manifest does not pin the embedding-model revision")
    if expected_source is not None:
        expected_inputs = manifest.get("inputs", {})
        expected_dsg = expected_source.get("dsg_sha256")
        expected_csv = expected_source.get("semantic_review_csv_sha256")
        if expected_inputs.get("dsg", {}).get("sha256") != expected_dsg:
            raise ValueError("RAG database was built from an unexpected DSG snapshot")
        if expected_inputs.get("semantic_overrides", {}).get("sha256") != expected_csv:
            raise ValueError("RAG database was built from an unexpected label-review CSV")
        if build_config.get("embedding_model") != expected_source.get("embedding_model"):
            raise ValueError("RAG database uses an unexpected embedding model")
        if (build_config.get("embedding_model_revision")
                != expected_source.get("embedding_model_revision")):
            raise ValueError("RAG database uses an unexpected embedding-model revision")
        if build_config.get("embedding_device") != expected_source.get("embedding_device"):
            raise ValueError("RAG database uses an unexpected embedding device")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or "vector_db/chroma.sqlite3" not in artifacts:
        raise ValueError("RAG manifest does not describe a Chroma vector database")
    for relative, expected in artifacts.items():
        rel_path = Path(relative)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            raise ValueError(f"RAG manifest contains an unsafe artifact path: {relative}")
        artifact = (root / rel_path).resolve(strict=True)
        if root not in artifact.parents:
            raise ValueError(f"RAG artifact escapes its build directory: {relative}")
        # Chroma updates sqlite metadata on collection access; its build-time
        # hash remains in the manifest as a snapshot, not a runtime invariant.
        if relative == "vector_db/chroma.sqlite3":
            if artifact.stat().st_size <= 0:
                raise ValueError("Chroma sqlite database is empty")
            continue
        if (not isinstance(expected, dict)
                or expected.get("sha256") != _sha256_file(artifact)
                or expected.get("size_bytes") != artifact.stat().st_size):
            raise ValueError(f"RAG artifact hash/size mismatch: {relative}")

    from build_vector_db import collection_content_sha256, load_vector_db
    collection = load_vector_db(database)
    if collection.count() != manifest.get("counts", {}).get("documents"):
        raise ValueError("Chroma collection document count does not match the build manifest")
    collection_metadata = collection.metadata or {}
    for key in ("embedding_model", "embedding_model_revision", "embedding_device"):
        if collection_metadata.get(key) != build_config.get(key):
            raise ValueError(f"Chroma collection {key} does not match the build manifest")
    if manifest.get("schema_version") == "hospital-rag-build-manifest-v3":
        content_manifest_path = root / "vector_content_manifest.json"
        content_manifest = json.loads(content_manifest_path.read_text(encoding="utf-8"))
        if (content_manifest.get("schema_version") != "hospital-rag-vector-content-v1"
                or content_manifest.get("document_count") != collection.count()):
            raise ValueError("RAG vector content manifest does not match collection count")
        if collection_content_sha256(collection) != content_manifest.get("content_sha256"):
            raise ValueError("RAG vector records/embeddings do not match content manifest")
        vector_content_sha256 = content_manifest["content_sha256"]
    else:
        vector_content_sha256 = None

    return {
        "build_id": manifest.get("build_id"),
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256_file(manifest_path),
        "source_dsg_sha256": manifest.get("inputs", {}).get("dsg", {}).get("sha256"),
        "semantic_review_csv_sha256": manifest.get("inputs", {}).get(
            "semantic_overrides", {}).get("sha256"),
        "canonical_graph_sha256": artifacts["canonical_graph_v2.json"]["sha256"],
        "vector_content_sha256": vector_content_sha256,
        "embedding_model": build_config.get("embedding_model"),
        "embedding_model_revision": build_config.get("embedding_model_revision"),
        "embedding_device": build_config.get("embedding_device"),
        "document_count": manifest.get("counts", {}).get("documents"),
        "verified_collection_count": collection.count(),
    }


def ollama_model_digest(host: str, model_name: str) -> str | None:
    """Read the local Ollama model digest when its tags API is available."""
    request = Request(host.rstrip("/") + "/api/tags", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=3.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    for model in payload.get("models", []):
        if model.get("name") == model_name or model.get("model") == model_name:
            return model.get("digest")
    return None


def build_plan_record(instruction: str, pipeline_result: dict,
                      mission_result: dict, nav2_result: dict,
                      metadata: dict) -> dict:
    """Combine stage outputs without conflating route planning with execution."""
    pipeline_status = pipeline_result.get("status") if isinstance(pipeline_result, dict) else None
    if pipeline_status != "valid":
        status = pipeline_status or "pipeline_invalid"
    elif mission_result.get("status") != "ready":
        status = mission_result.get("status", "mission_invalid")
    elif (nav2_result.get("status") == "planned"
          and nav2_result.get("execution_performed") is False):
        status = "planned"
    else:
        status = nav2_result.get("status", "nav2_plan_failed")

    return {
        "schema_version": "hospital-natural-language-nav2-plan-v1",
        "status": status,
        "execution_performed": False,
        "instruction": instruction,
        "instruction_sha256": _sha256_bytes(instruction.encode("utf-8")),
        "model": {"name": metadata.get("model_name"),
                  "digest": metadata.get("model_digest")},
        "intent_prompt_profile": metadata.get("intent_prompt_profile"),
            "task_chain_backend": metadata.get("task_chain_backend"),
        "provenance": {
            "rag_build_id": metadata.get("rag_build_id"),
            "rag_manifest_sha256": metadata.get("rag_manifest_sha256"),
            "rag_vector_content_sha256": metadata.get("rag_vector_content_sha256"),
            "source_dsg_sha256": metadata.get("source_dsg_sha256"),
            "semantic_review_csv_sha256": metadata.get("semantic_review_csv_sha256"),
            "canonical_graph_sha256": metadata.get("canonical_graph_sha256"),
            "map_bundle_id": metadata.get("map_bundle_id"),
            "map_bundle_sha256": metadata.get("map_bundle_sha256"),
            "start_profile_sha256": metadata.get("start_profile_sha256"),
            "prompt_source_sha256": metadata.get("prompt_source_sha256", {}),
            "llm_runtime_options": metadata.get("llm_runtime_options", {}),
            "ollama_config_sha256": metadata.get("ollama_config_sha256"),
        },
        "pipeline_result": pipeline_result,
        "mission_preparation": mission_result,
        "nav2_plan": nav2_result,
    }


def _write_new_json(path: str | Path, payload: dict) -> None:
    output = Path(path).expanduser()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing plan output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, output)
    finally:
        if temp.exists():
            temp.unlink()


def _load_defaults(path: Path, map_bundle: dict) -> dict:
    defaults = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(defaults, dict) or defaults.get("schema_version") != "hospital-nav2-plan-defaults-v1":
        raise ValueError("unsupported Hospital Nav2 plan defaults schema")
    if defaults.get("map_bundle_id") != map_bundle.get("map_bundle_id"):
        raise ValueError("Nav2 plan defaults and map bundle IDs do not match")
    expected_rag = defaults.get("rag_source")
    if (not isinstance(expected_rag, dict)
            or any(not isinstance(expected_rag.get(key), str) or not expected_rag[key]
                   for key in ("dsg_sha256", "semantic_review_csv_sha256",
                               "embedding_model", "embedding_model_revision",
                               "embedding_device"))):
        raise ValueError("Nav2 plan defaults must pin DSG, review CSV, and embedding provenance")
    if not isinstance(defaults.get("model"), str) or not defaults["model"]:
        raise ValueError("Nav2 plan defaults must pin a local Ollama model name")
    if defaults.get("robot_model") not in ("point", "footprint"):
        raise ValueError("Nav2 plan defaults must set robot_model to point or footprint")
    return defaults


def _prompt_source_hashes() -> dict:
    sources = {
        "planner": LLM_DIR / "main_rag.py",
        "intent_parser": LLM_DIR / "llm" / "intent_parser.py",
        "intent_schema": LLM_DIR / "planner_schemas" / "intent.py",
        "intent_contract": LLM_DIR / "llm" / "intent_contract.py",
        "intent_audit": LLM_DIR / "llm" / "intent_audit_evidence.py",
        "plan_contract": LLM_DIR / "llm" / "plan_contract.py",
        "task_plan_schema": LLM_DIR / "schema.py",
        "prompt": LLM_DIR / "prompt.py",
        "grounding": LLM_DIR / "grounding" / "orchestrator.py",
        "rag_retriever": LLM_DIR / "rag_retriever.py",
        "rag_config": RAG_DIR / "config.yaml",
        "rag_index": RAG_DIR / "src" / "canonical_index.py",
        "scene_graph_api": RAG_DIR / "src" / "scene_graph_api.py",
        "document_builder": RAG_DIR / "src" / "build_documents.py",
        "dsg_parser": RAG_DIR / "src" / "parse_3dsg.py",
        "nav2_mission_adapter": NAV2_SCRIPTS / "prepare_instruction_mission.py",
        "nav2_runner": NAV2_SCRIPTS / "run_task_chain_nav2.py",
        "candidate_route_planner": NAV2_SCRIPTS / "candidate_route_planner.py",
        "floor_transition_core": NAV2_SCRIPTS / "floor_transition_core.py",
        "hospital_plan_defaults": NAV2_PACKAGE / "config" / "hospital_nav2_plan_defaults.yaml",
    }
    return {name: _sha256_file(path) for name, path in sources.items()}


def _run_ros_plan(mission_path: Path, bundle_path: Path, output_path: Path,
                  robot_model: str, ros_domain_id: int) -> tuple[int, dict, str]:
    child = NAV2_SCRIPTS / "run_plan_only.sh"
    environment = os.environ.copy()
    environment["ROS_DOMAIN_ID"] = str(ros_domain_id)
    completed = subprocess.run(
        ["bash", str(child), str(mission_path), str(bundle_path),
         str(output_path), robot_model],
        check=False, capture_output=True, text=True, env=environment, cwd=ROOT,
    )
    if output_path.is_file():
        result = json.loads(output_path.read_text(encoding="utf-8"))
    else:
        result = {"status": "nav2_plan_failed", "execution_performed": False,
                  "error": "Nav2 runner did not create a result file"}
    diagnostics = completed.stderr[-4000:] if completed.returncode else ""
    return completed.returncode, result, diagnostics


def run_instruction(args) -> dict:
    config = load_config(LLM_DIR / "config_rag.yaml")
    bundle_path = Path(args.bundle).expanduser().resolve(strict=True)
    defaults_path = Path(args.defaults).expanduser().resolve(strict=True)
    bundle = yaml.safe_load(bundle_path.read_text(encoding="utf-8"))
    defaults = _load_defaults(defaults_path, bundle)
    model_name = args.model or defaults.get("model", DEFAULT_MODEL)
    rag_info = verify_rag_build(args.db_dir,
                                expected_source=defaults.get("rag_source"))
    canonical_path = Path(args.db_dir).expanduser().resolve().parent / "canonical_graph_v2.json"
    canonical_graph = json.loads(canonical_path.read_text(encoding="utf-8"))
    if args.floor_tolerance_m is not None:
        defaults["floor_z_tolerance_m"] = args.floor_tolerance_m

    model_digest = ollama_model_digest(config.get("ollama_host", "http://127.0.0.1:11434"),
                                       model_name)
    pipeline = run_structured_task_planner(
        args.instruction, model_name=model_name, db_dir=args.db_dir,
        intent_prompt_profile=args.intent_prompt_profile,
        runtime_options={"num_ctx": args.num_ctx,
                         "num_predict": args.num_predict,
                         "request_timeout_s": args.request_timeout_s},
        task_chain_backend=args.task_chain_backend,
    )
    pipeline_dict = pipeline.model_dump(mode="json") if hasattr(pipeline, "model_dump") else pipeline
    if pipeline_dict.get("status") != "valid":
        return build_plan_record(args.instruction, pipeline_dict,
                                 {"status": "not_attempted"},
                                 {"status": "not_attempted"}, {
                                     "model_name": model_name,
                                     "model_digest": model_digest,
                                     "intent_prompt_profile": args.intent_prompt_profile,
                                     "task_chain_backend": args.task_chain_backend,
                                     "rag_build_id": rag_info["build_id"],
                                     "rag_manifest_sha256": rag_info["manifest_sha256"],
                                     "rag_vector_content_sha256": rag_info.get(
                                         "vector_content_sha256"),
                                     "source_dsg_sha256": rag_info["source_dsg_sha256"],
                                     "semantic_review_csv_sha256": rag_info[
                                         "semantic_review_csv_sha256"],
                                     "canonical_graph_sha256": rag_info[
                                         "canonical_graph_sha256"],
                                     "map_bundle_id": bundle["map_bundle_id"],
                                     "map_bundle_sha256": _sha256_file(bundle_path),
                                     "start_profile_sha256": _sha256_file(defaults_path),
                                     "prompt_source_sha256": _prompt_source_hashes(),
                                     "llm_runtime_options": _effective_llm_runtime(config, args),
                                     "ollama_config_sha256": _sha256_file(
                                         LLM_DIR / "config_rag.yaml"),
                                 })

    mission_result = build_candidate_mission(
        pipeline_dict, bundle, defaults["initial_state"],
        task_id=f"instruction-{_sha256_bytes(args.instruction.encode('utf-8'))[:16]}",
        floor_tolerance_m=float(defaults["floor_z_tolerance_m"]),
        canonical_graph=canonical_graph,
    )
    metadata = {
        "model_name": model_name,
        "model_digest": model_digest,
        "intent_prompt_profile": args.intent_prompt_profile,
        "task_chain_backend": args.task_chain_backend,
        "rag_build_id": rag_info["build_id"],
        "rag_manifest_sha256": rag_info["manifest_sha256"],
        "rag_vector_content_sha256": rag_info.get("vector_content_sha256"),
        "source_dsg_sha256": rag_info["source_dsg_sha256"],
        "semantic_review_csv_sha256": rag_info["semantic_review_csv_sha256"],
        "canonical_graph_sha256": rag_info["canonical_graph_sha256"],
        "map_bundle_id": bundle["map_bundle_id"],
        "map_bundle_sha256": _sha256_file(bundle_path),
        "start_profile_sha256": _sha256_file(defaults_path),
        "prompt_source_sha256": _prompt_source_hashes(),
        "llm_runtime_options": _effective_llm_runtime(config, args),
        "ollama_config_sha256": _sha256_file(LLM_DIR / "config_rag.yaml"),
    }
    if mission_result.get("status") != "ready":
        return build_plan_record(args.instruction, pipeline_dict, mission_result,
                                 {"status": "not_attempted"}, metadata)

    with tempfile.TemporaryDirectory(prefix="hospital-nav2-plan-") as temporary_dir:
        temporary = Path(temporary_dir)
        mission_path = temporary / "candidate_mission.json"
        route_path = temporary / "nav2_plan.json"
        mission_path.write_text(json.dumps(mission_result["mission"], indent=2,
                                           ensure_ascii=False) + "\n", encoding="utf-8")
        return_code, nav2_result, diagnostics = _run_ros_plan(
            mission_path, bundle_path, route_path, args.robot_model, args.ros_domain_id)
        if diagnostics:
            nav2_result["runner_stderr_tail"] = diagnostics
        if return_code and nav2_result.get("status") == "planned":
            nav2_result["status"] = "nav2_plan_failed"
        return build_plan_record(args.instruction, pipeline_dict, mission_result,
                                 nav2_result, metadata)


def _effective_llm_runtime(config: dict, args) -> dict:
    options = dict(config.get("options") or {})
    options.update({"num_ctx": args.num_ctx, "num_predict": args.num_predict})
    return {"options": options, "request_timeout_s": args.request_timeout_s}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instruction", "-c", required=True,
                        help="one natural-language Hospital navigation instruction")
    parser.add_argument("--model", default=None)
    parser.add_argument("--db-dir", default=str(DEFAULT_RAG_DIR))
    parser.add_argument("--bundle", default=str(DEFAULT_BUNDLE))
    parser.add_argument("--defaults", default=str(DEFAULTS_PATH))
    parser.add_argument("--output", type=Path,
                        help="write a new JSON result; existing files are never overwritten")
    parser.add_argument("--intent-prompt-profile", default="hospital_taskchain_surface_v1")
    parser.add_argument("--task-chain-backend",
                        choices=("grounded_deterministic", "llm2"),
                        default="grounded_deterministic",
                        help="compile grounded navigation steps deterministically by default; use llm2 for comparison")
    parser.add_argument("--num-ctx", type=int, default=4096,
                        help="bounded Ollama context for this Nav2 planning run")
    parser.add_argument("--num-predict", type=int, default=512,
                        help="maximum output tokens per local LLM call")
    parser.add_argument("--request-timeout-s", type=int, default=360,
                        help="timeout for each local LLM request (cold starts may be slow)")
    parser.add_argument("--floor-tolerance-m", type=float)
    parser.add_argument("--robot-model", choices=("point", "footprint"), default=None)
    parser.add_argument("--ros-domain-id", type=int, default=77)
    args = parser.parse_args(argv)

    try:
        if args.robot_model is None:
            profile = yaml.safe_load(Path(args.defaults).read_text(encoding="utf-8"))
            args.robot_model = profile.get("robot_model", "point")
        result = run_instruction(args)
        if args.output is not None:
            _write_new_json(args.output, result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["status"] == "planned" else 2
    except Exception as error:
        failure = {"schema_version": "hospital-natural-language-nav2-plan-v1",
                   "status": "system_error", "execution_performed": False,
                   "error": f"{type(error).__name__}: {error}"}
        print(json.dumps(failure, indent=2, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
