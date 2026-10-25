"""Convert human-reviewed Hospital object labels to the DSG override schema."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path


SCHEMA_VERSION = "hospital_object_semantic_overrides_v1"
UNKNOWN_SEMANTIC_LABEL = 4294967295
REQUIRED_COLUMNS = {
    "object_node_id",
    "selected_label_id",
    "selected_label_name",
    "custom_label",
    "review_status",
}


def build_override_payload(csv_path: str | Path) -> dict:
    """Build a deterministic sidecar using only confirmed selected labels."""
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"review CSV not found: {path}")
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    items_by_id: dict[str, dict] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = set(reader.fieldnames or ())
        missing = sorted(REQUIRED_COLUMNS - fields)
        if missing:
            raise ValueError(f"review CSV is missing columns: {missing}")

        for row_number, row in enumerate(reader, start=2):
            node_id = (row.get("object_node_id") or "").strip()
            if not node_id.isdigit() or int(node_id) <= 0:
                raise ValueError(f"row {row_number}: object_node_id must be a positive integer")
            if node_id in items_by_id:
                raise ValueError(f"row {row_number}: duplicate object_node_id {node_id}")
            if (row.get("review_status") or "").strip().lower() != "confirmed":
                raise ValueError(f"row {row_number}: annotation is not confirmed")

            custom_label = (row.get("custom_label") or "").strip()
            selected_name = (row.get("selected_label_name") or "").strip()
            if custom_label:
                label_type = "human_custom"
                label_id = None
                label_name = custom_label
            else:
                raw_label_id = (row.get("selected_label_id") or "").strip()
                if not raw_label_id.isdigit():
                    raise ValueError(f"row {row_number}: selected_label_id must be an integer")
                label_id = int(raw_label_id)
                if not 1 <= label_id <= 109:
                    raise ValueError(f"row {row_number}: selected_label_id must be in 1..109")
                if not selected_name:
                    raise ValueError(f"row {row_number}: selected_label_name is required")
                label_type = "fine_ontology"
                label_name = selected_name

            items_by_id[node_id] = {
                "object_node_id": node_id,
                "original_semantic_label": UNKNOWN_SEMANTIC_LABEL,
                "fine_label_id": label_id,
                "label_type": label_type,
                "fine_label_name": label_name,
                "annotation_status": "human_confirmed",
            }

    if not items_by_id:
        raise ValueError("review CSV contains no confirmed object rows")

    return {
        "schema_version": SCHEMA_VERSION,
        "provenance": {
            "source_csv_name": path.name,
            "source_csv_sha256": source_hash,
            "reviewed_row_count": len(items_by_id),
        },
        "items": [items_by_id[node_id] for node_id in sorted(items_by_id, key=int)],
    }
