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
import json
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from build_documents import build_all_documents
from build_vector_db import build_vector_db
from parse_3dsg import parse_3dsg
from query_rag import query_rag

logger = logging.getLogger("khronos_3dsg_rag")


def load_config(config_path: str | Path) -> dict:
    config_path = Path(config_path)
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build_one(input_path: str, output_dir: str, aliases: dict, dataset: str = "") -> dict:
    """Build a single RAG database. Runs in a subprocess for parallelism."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)

    if not input_path.exists():
        return {"status": "error", "name": output_dir.name, "error": f"File not found: {input_path}"}

    graph = parse_3dsg(input_path)
    docs = build_all_documents(graph, aliases=aliases, dataset=dataset or output_dir.name, source_file=input_path.name)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Save JSONL
    docs_path = output_dir / "rag_documents.jsonl"
    with open(docs_path, "w", encoding="utf-8") as f:
        for doc in docs:
            f.write(json.dumps({"doc_id": doc.doc_id, "text": doc.text, "metadata": doc.metadata}, ensure_ascii=False) + "\n")

    # Build vector DB
    db_dir = output_dir / "vector_db"
    build_vector_db(docs, db_dir)

    return {
        "status": "ok",
        "name": output_dir.name,
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "docs": len(docs),
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

    if args.all:
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
        result = _build_one(input_path, output_dir, aliases, dataset=Path(input_path).stem)

        if result["status"] == "ok":
            print(f"[1/2] Parsed: {result['nodes']} nodes, {result['edges']} edges")
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
