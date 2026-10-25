"""CLI entry point for Khronos 3DSG RAG pipeline.

Usage:
    python src/main.py build --all                         # parallel build from config
    python src/main.py build --input data/3DSG.json -o out # single build
    python src/main.py query --db outputs/khronos_office -q "chair near table"
    python src/main.py query --all -q "navigate to room 201"  # search all dbs
    python src/main.py list                                # list available dbs
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from build_documents import build_all_documents
from build_vector_db import (DEFAULT_EMBED_DEVICE, DEFAULT_EMBED_MODEL,
                             DEFAULT_EMBED_MODEL_REVISION, build_vector_db)
from export_canonical_graph import export_canonical_graph, save_canonical_graph
from parse_3dsg import parse_3dsg
from query_rag import query_rag

logger = logging.getLogger("khronos_3dsg_rag")


def load_config(config_path: str | Path) -> dict:
    config_path = Path(config_path)
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_new_output(path: str | Path) -> Path:
    """Fail closed rather than silently mixing or overwriting a prior index."""
    output = Path(path)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"reproducible build output already exists: {output}")
    return output


def _package_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _build_identity_payload(manifest: dict) -> dict:
    stable_names = {"build_config.json", "canonical_graph_v2.json",
                    "canonical_graph_v2.manifest.json", "rag_documents.jsonl",
                    "semantic_overrides.json"}
    artifacts = manifest.get("artifacts", {})
    stable_artifacts = {name: artifact for name, artifact in artifacts.items()
                        if name in stable_names}
    payload = {
        "schema_version": manifest.get("schema_version"),
        "inputs": manifest.get("inputs"),
        "build_config_sha256": manifest.get("build_config_sha256"),
        "counts": manifest.get("counts"),
        "stable_artifacts": stable_artifacts,
    }
    if manifest.get("schema_version") in ("hospital-rag-build-manifest-v2",
                                           "hospital-rag-build-manifest-v3"):
        payload["toolchain"] = manifest.get("toolchain")
    return payload


def _validate_build_manifest(manifest: dict) -> bool:
    if (not isinstance(manifest, dict)
            or manifest.get("schema_version") not in (
                "hospital-rag-build-manifest-v1", "hospital-rag-build-manifest-v2",
                "hospital-rag-build-manifest-v3")):
        raise ValueError("unsupported RAG build manifest schema")
    build_config = manifest.get("build_config")
    if not isinstance(build_config, dict):
        raise ValueError("RAG build manifest is missing build_config")
    config_bytes = json.dumps(build_config, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(config_bytes).hexdigest() != manifest.get("build_config_sha256"):
        raise ValueError("RAG build manifest build_config_sha256 mismatch")
    identity_bytes = json.dumps(_build_identity_payload(manifest), sort_keys=True,
                                ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(identity_bytes).hexdigest() != manifest.get("build_id"):
        raise ValueError("RAG build manifest build_id mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("RAG build manifest is missing artifact hashes")
    instance_bytes = json.dumps(artifacts, sort_keys=True,
                                separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(instance_bytes).hexdigest() != manifest.get("artifact_instance_id"):
        raise ValueError("RAG build manifest artifact_instance_id mismatch")
    return True


def _build_manifest(source_dsg: str | Path, output_dir: str | Path,
                    semantic_overrides: str | Path | None, build_config: dict,
                    *, counts: dict) -> dict:
    """Describe exact build inputs, effective config, toolchain, and outputs."""
    source = Path(source_dsg)
    output = Path(output_dir)
    inputs = {
        "dsg": {"name": source.name, "sha256": _sha256_file(source),
                "size_bytes": source.stat().st_size},
    }
    if semantic_overrides is not None:
        labels = Path(semantic_overrides)
        inputs["semantic_overrides"] = {
            "name": labels.name, "sha256": _sha256_file(labels),
            "size_bytes": labels.stat().st_size,
        }
    config_bytes = json.dumps(build_config, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
    artifacts = {}
    for artifact in sorted(path for path in output.rglob("*") if path.is_file()):
        relative = artifact.relative_to(output).as_posix()
        if relative == "build_manifest.json":
            continue
        artifacts[relative] = {"sha256": _sha256_file(artifact),
                               "size_bytes": artifact.stat().st_size}

    manifest = {
        "schema_version": "hospital-rag-build-manifest-v3",
        "inputs": inputs,
        "build_config": build_config,
        "build_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "toolchain": {
            "python": sys.version.split()[0],
            "chromadb": _package_version("chromadb"),
            "sentence_transformers": _package_version("sentence-transformers"),
            "transformers": _package_version("transformers"),
            "huggingface_hub": _package_version("huggingface-hub"),
            "torch": _package_version("torch"),
            "numpy": _package_version("numpy"),
        },
        "counts": counts,
        "artifacts": artifacts,
    }
    identity_bytes = json.dumps(_build_identity_payload(manifest), sort_keys=True,
                                ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    manifest["build_id"] = hashlib.sha256(identity_bytes).hexdigest()
    instance_bytes = json.dumps(artifacts, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
    manifest["artifact_instance_id"] = hashlib.sha256(instance_bytes).hexdigest()
    return manifest


def _build_reproducible(input_path: str | Path, output_path: str | Path,
                        aliases: dict, dataset: str,
                        semantic_overrides: str | Path | None = None,
                        review_csv: str | Path | None = None,
                        embedding_model: str = DEFAULT_EMBED_MODEL,
                        embedding_model_revision: str | None = None,
                        embedding_device: str = DEFAULT_EMBED_DEVICE) -> dict:
    """Build a complete new index in staging, then atomically publish it."""
    source = Path(input_path)
    if not source.is_file():
        raise FileNotFoundError(f"DSG input not found: {source}")
    if semantic_overrides is not None and review_csv is not None:
        raise ValueError("provide either --semantic-overrides or --review-csv, not both")
    if embedding_model_revision is None and embedding_model == DEFAULT_EMBED_MODEL:
        embedding_model_revision = DEFAULT_EMBED_MODEL_REVISION
    if (not isinstance(embedding_model_revision, str)
            or not re.fullmatch(r"[0-9a-f]{40}", embedding_model_revision)):
        raise ValueError("reproducible builds require a pinned 40-character embedding model revision")
    output = _require_new_output(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    _require_new_output(output)

    input_labels = Path(review_csv) if review_csv is not None else (
        Path(semantic_overrides) if semantic_overrides is not None else None)
    if input_labels is not None and not input_labels.is_file():
        raise FileNotFoundError(f"semantic label input not found: {input_labels}")
    build_config = {
        "dataset": dataset,
        "aliases": aliases,
        "embedding_model": embedding_model,
        "embedding_model_revision": embedding_model_revision,
        "embedding_device": embedding_device,
        "semantic_override_schema": ("hospital_object_semantic_overrides_v1"
                                      if input_labels is not None else None),
    }

    with tempfile.TemporaryDirectory(prefix=f".{output.name}.staging-",
                                     dir=output.parent) as temporary_dir:
        staging = Path(temporary_dir) / "artifact"
        staging.mkdir()
        (staging / "build_config.json").write_text(
            json.dumps(build_config, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        override_path = None
        if review_csv is not None:
            from hospital_review_csv import build_override_payload
            payload = build_override_payload(review_csv)
            override_path = staging / "semantic_overrides.json"
            override_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
        elif semantic_overrides is not None:
            import shutil
            override_path = staging / "semantic_overrides.json"
            shutil.copyfile(semantic_overrides, override_path)

        result = _build_one(source, staging, aliases, dataset=dataset,
                            semantic_overrides_path=str(override_path) if override_path else None,
                            embedding_model=embedding_model,
                            embedding_model_revision=embedding_model_revision,
                            embedding_model_device=embedding_device)
        if result.get("status") != "ok":
            raise RuntimeError(result.get("error", "RAG build failed"))

        from build_vector_db import collection_content_sha256, load_vector_db
        collection = load_vector_db(staging / "vector_db")
        content_manifest = {
            "schema_version": "hospital-rag-vector-content-v1",
            "document_count": collection.count(),
            "content_sha256": collection_content_sha256(collection),
        }
        (staging / "vector_content_manifest.json").write_text(
            json.dumps(content_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

        manifest = _build_manifest(
            source, staging, input_labels, build_config,
            counts={"nodes": result["nodes"], "edges": result["edges"],
                    "documents": result["docs"]},
        )
        (staging / "build_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _require_new_output(output)
        os.rename(staging, output)

    result["output"] = str(output)
    result["canonical_graph"] = (str(output / "canonical_graph_v2.json")
                                 if result.get("canonical_graph") else None)
    result["build_manifest"] = str(output / "build_manifest.json")
    result["build_id"] = manifest["build_id"]
    return result


def _build_one(
    input_path: str,
    output_dir: str,
    aliases: dict,
    dataset: str = "",
    semantic_overrides_path: str | None = None,
    embedding_model: str = DEFAULT_EMBED_MODEL,
    embedding_model_revision: str | None = None,
    embedding_model_device: str | None = None,
) -> dict:
    """Build a single RAG database. Runs in a subprocess for parallelism."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)

    if not input_path.exists():
        return {"status": "error", "name": output_dir.name, "error": f"File not found: {input_path}"}

    graph = parse_3dsg(input_path, semantic_overrides_path=semantic_overrides_path)
    docs = build_all_documents(graph, aliases=aliases, dataset=dataset or output_dir.name, source_file=input_path.name)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Save JSONL
    docs_path = output_dir / "rag_documents.jsonl"
    with open(docs_path, "w", encoding="utf-8") as f:
        for doc in docs:
            f.write(json.dumps({"doc_id": doc.doc_id, "text": doc.text, "metadata": doc.metadata}, ensure_ascii=False) + "\n")

    canonical_graph_path = None
    if semantic_overrides_path is not None:
        canonical_data = export_canonical_graph(graph, source_path=input_path)
        canonical_graph_path, _ = save_canonical_graph(canonical_data, output_dir)

    # Build vector DB
    db_dir = output_dir / "vector_db"
    build_vector_db(docs, db_dir, embed_model=embedding_model,
                    embed_model_revision=embedding_model_revision,
                    embed_model_device=embedding_model_device)

    return {
        "status": "ok",
        "name": output_dir.name,
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "docs": len(docs),
        "canonical_graph": str(canonical_graph_path) if canonical_graph_path else None,
        "output": str(output_dir),
    }


def _merge_aliases(config: dict) -> dict:
    """Merge aliases + zh_aliases into one dict."""
    all_aliases = dict(config.get("aliases", {}))
    for k, v in config.get("zh_aliases", {}).items():
        all_aliases[k] = v
    return all_aliases


def cmd_build(args: argparse.Namespace) -> None:
    """Build RAG database(s)."""
    config = load_config(Path(args.config))
    aliases = _merge_aliases(config)

    if args.reproducible:
        if args.all or not args.input or not args.output:
            print("Error: --reproducible requires one explicit --input and --output.", file=sys.stderr)
            sys.exit(2)
        try:
            result = _build_reproducible(
                args.input,
                args.output,
                aliases,
                dataset=args.dataset or Path(args.input).stem,
                semantic_overrides=args.semantic_overrides or None,
                review_csv=args.review_csv or None,
                embedding_model=args.embedding_model,
                embedding_model_revision=args.embedding_model_revision or None,
                embedding_device=args.embedding_device,
            )
        except (FileExistsError, FileNotFoundError, ValueError, RuntimeError) as error:
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if args.review_csv:
        print("Error: --review-csv requires --reproducible.", file=sys.stderr)
        sys.exit(2)

    if args.all:
        if args.semantic_overrides:
            print("Error: --semantic-overrides is supported only for a single --input build.", file=sys.stderr)
            sys.exit(1)
        # Parallel build from config dsg_sources
        sources = config.get("dsg_sources", {})
        if not sources:
            print("Error: no dsg_sources in config.yaml", file=sys.stderr)
            sys.exit(1)

        output_root = Path(args.output) if args.output else Path(config.get("output_dir", "outputs"))
        tasks = []
        for name, dsg_path in sources.items():
            out = output_root / name
            tasks.append((dsg_path, str(out), aliases, name))

        print(f"Building {len(tasks)} databases in parallel ...\n")
        t0 = time.time()

        with ProcessPoolExecutor(max_workers=min(len(tasks), 4)) as pool:
            futures = {pool.submit(_build_one, inp, out, al, ds): ds for inp, out, al, ds in tasks}
            for future in as_completed(futures):
                result = future.result()
                if result["status"] == "ok":
                    print(f"  ✓ {result['name']}: {result['nodes']} nodes, {result['edges']} edges, {result['docs']} docs -> {result['output']}")
                else:
                    print(f"  ✗ {result['name']}: {result['error']}")

        elapsed = time.time() - t0
        print(f"\nDone in {elapsed:.1f}s")

    else:
        # Single build
        input_path = args.input or config.get("input_dsg", "data/3DSG.json")
        output_dir = args.output or config.get("output_dir", "outputs")

        print(f"Building from {input_path} ...\n")
        result = _build_one(
            input_path,
            output_dir,
            aliases,
            dataset=Path(input_path).stem,
            semantic_overrides_path=args.semantic_overrides or None,
        )

        if result["status"] == "ok":
            print(f"[1/2] Parsed: {result['nodes']} nodes, {result['edges']} edges")
            if result.get("canonical_graph"):
                print(f"[2/3] Exported structured index -> {result['canonical_graph']}")
                print(f"[3/3] Indexed: {result['docs']} docs -> {result['output']}")
            else:
                print(f"[2/2] Indexed: {result['docs']} docs -> {result['output']}")
            print(f"\nQuery with:")
            print(f'  python src/main.py query --db {result["output"]} --query "chair near table"')
        else:
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)


def cmd_query(args: argparse.Namespace) -> None:
    """Query RAG database(s)."""
    config = load_config(Path(args.config))
    output_root = Path(args.output) if args.output else Path(config.get("output_dir", "outputs"))

    if args.all:
        # Search across all dbs
        db_dirs = sorted(output_root.iterdir()) if output_root.exists() else []
        db_dirs = [d for d in db_dirs if (d / "vector_db").exists()]

        if not db_dirs:
            print(f"Error: no vector DBs found in {output_root}", file=sys.stderr)
            print("Run 'build --all' first.", file=sys.stderr)
            sys.exit(1)

        print(f"Query: \"{args.query}\"  (searching {len(db_dirs)} databases)\n")

        all_results = []
        for db_dir in db_dirs:
            db_path = db_dir / "vector_db"
            results = query_rag(args.query, str(db_path), top_k=args.top_k)
            for r in results:
                r["db_name"] = db_dir.name
            all_results.extend(results)

        # Sort by score across all dbs
        all_results.sort(key=lambda x: x["score"], reverse=True)
        all_results = all_results[:args.top_k]

        _print_results(all_results)
        if args.json:
            print(json.dumps(all_results, ensure_ascii=False, indent=2))

    elif args.list:
        # List available databases
        db_dirs = sorted(output_root.iterdir()) if output_root.exists() else []
        db_dirs = [d for d in db_dirs if (d / "vector_db").exists()]

        if not db_dirs:
            print(f"No databases found in {output_root}")
            return

        print(f"Available databases in {output_root}:\n")
        for d in db_dirs:
            docs_path = d / "rag_documents.jsonl"
            count = 0
            if docs_path.exists():
                with open(docs_path) as f:
                    count = sum(1 for _ in f)
            print(f"  {d.name:30s}  {count:5d} docs  ->  query --db {d}")

    else:
        # Query single db
        db_dir = Path(args.db) if args.db else None
        if not db_dir or not db_dir.exists():
            # Try name-based lookup
            if args.db:
                candidate = output_root / args.db / "vector_db"
                if candidate.exists():
                    db_dir = candidate.parent
            if not db_dir or not (db_dir / "vector_db").exists():
                print(f"Error: vector DB not found. Use --list to see available databases.", file=sys.stderr)
                sys.exit(1)

        print(f"Query: \"{args.query}\"  (db: {db_dir.name})\n")
        results = query_rag(args.query, str(db_dir / "vector_db"), top_k=args.top_k)
        _print_results(results)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))


def _print_results(results: list[dict]) -> None:
    if not results:
        print("No results found.")
        return
    for r in results:
        db_tag = f"  [{r.get('db_name', '')}]" if "db_name" in r else ""
        print(f"[Rank {r['rank']}] score={r['score']:.4f}  node={r['node_id']} ({r['node_type']}){db_tag}")
        print(f"  {r['text']}")
        if r["metadata"].get("related_nodes"):
            print(f"  related: {r['metadata']['related_nodes']}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Khronos 3DSG -> RAG pipeline")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    sub = parser.add_subparsers(dest="command", required=True)

    # build
    p_build = sub.add_parser("build", help="Build RAG from 3DSG JSON")
    p_build.add_argument("--all", action="store_true", help="Build all sources in config (parallel)")
    p_build.add_argument("--input", "-i", default="", help="Input 3DSG JSON path")
    p_build.add_argument("--output", "-o", default="", help="Output directory")
    p_build.add_argument(
        "--semantic-overrides",
        default="",
        help="Optional human-confirmed semantic sidecar (single-input builds only)",
    )
    p_build.add_argument("--review-csv", default="",
                         help="Human-confirmed Hospital review CSV; requires --reproducible")
    p_build.add_argument("--reproducible", action="store_true",
                         help="Build in a new staging directory and write a provenance manifest")
    p_build.add_argument("--dataset", default="",
                         help="Stable dataset label for document metadata in a reproducible build")
    p_build.add_argument("--embedding-model", default=DEFAULT_EMBED_MODEL,
                         help="Sentence-Transformers model used to build the vector index")
    p_build.add_argument("--embedding-model-revision", default="",
                         help="Pinned 40-character Hugging Face commit for reproducible builds")
    p_build.add_argument("--embedding-device", choices=("cpu", "cuda"), default=DEFAULT_EMBED_DEVICE,
                         help="Pinned embedding device for reproducible builds (default: cpu)")

    # query
    p_query = sub.add_parser("query", help="Query the RAG database")
    p_query.add_argument("--db", "-d", default="", help="Vector DB name or path")
    p_query.add_argument("--all", action="store_true", help="Search across all databases")
    p_query.add_argument("--list", "-l", action="store_true", help="List available databases")
    p_query.add_argument("--query", "-q", default="", help="Query string")
    p_query.add_argument("--top-k", "-k", type=int, default=5, help="Number of results")
    p_query.add_argument("--json", action="store_true", help="Also print JSON output")
    p_query.add_argument("--output", "-o", default="", help="Output root directory")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.command == "build":
        cmd_build(args)
    elif args.command == "query":
        cmd_query(args)


if __name__ == "__main__":
    main()
