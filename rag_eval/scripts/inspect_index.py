#!/usr/bin/env python3
"""Inspect RAG index metadata."""

import argparse
import json
import logging
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "RAG" / "khronos_3dsg_rag" / "src"))

from build_vector_db import load_vector_db


def main():
    parser = argparse.ArgumentParser(description="Inspect RAG index")
    parser.add_argument("--db-dir", required=True, help="Vector DB directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    collection = load_vector_db(args.db_dir)
    total = collection.count()
    print(f"Total documents in index: {total}")

    all_meta = collection.get(include=["metadatas", "documents"], limit=min(total, 100))
    metadatas = all_meta.get("metadatas", [])
    documents = all_meta.get("documents", [])

    type_counts = Counter(m.get("node_type", "unknown") for m in metadatas)
    chunk_counts = Counter(m.get("chunk_type", "unknown") for m in metadatas)
    symbol_count = sum(1 for m in metadatas if m.get("object_symbol"))
    bb_count = sum(1 for m in metadatas if m.get("bounding_box_center"))
    dataset_counts = Counter(m.get("dataset", "unknown") for m in metadatas)

    print(f"\nChunk types: {dict(chunk_counts)}")
    print(f"Node types: {dict(type_counts)}")
    print(f"Documents with object_symbol: {symbol_count}/{len(metadatas)}")
    print(f"Documents with bounding_box_center: {bb_count}/{len(metadatas)}")
    print(f"Datasets: {dict(dataset_counts)}")

    print("\n--- Sample metadata ---")
    for i in range(min(3, len(metadatas))):
        print(f"\nDoc {i}:")
        print(f"  metadata: {json.dumps(metadatas[i], indent=4, default=str)}")
        if i < len(documents):
            print(f"  text: {documents[i][:200]}")


if __name__ == "__main__":
    main()
