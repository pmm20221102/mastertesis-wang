import csv
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from hospital_review_csv import build_override_payload  # noqa: E402


class HospitalReviewCsvTests(unittest.TestCase):
    def write_csv(self, path, rows):
        fields = [
            "object_node_id", "suggested_label_id", "suggested_label_name",
            "selected_label_id", "selected_label_name", "custom_label",
            "review_status",
        ]
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_uses_only_confirmed_selected_labels_and_records_source_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "review.csv"
            self.write_csv(path, [{
                "object_node_id": "5692549928996306944",
                "suggested_label_id": "96",
                "suggested_label_name": "doorway",
                "selected_label_id": "1",
                "selected_label_name": "door",
                "custom_label": "",
                "review_status": "confirmed",
            }])

            payload = build_override_payload(path)

            self.assertEqual(
                payload["schema_version"],
                "hospital_object_semantic_overrides_v1",
            )
            self.assertEqual(payload["provenance"]["source_csv_sha256"],
                             hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(payload["items"], [{
                "object_node_id": "5692549928996306944",
                "original_semantic_label": 4294967295,
                "fine_label_id": 1,
                "label_type": "fine_ontology",
                "fine_label_name": "door",
                "annotation_status": "human_confirmed",
            }])

    def test_custom_label_uses_the_human_selected_custom_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "review.csv"
            self.write_csv(path, [{
                "object_node_id": "123",
                "suggested_label_id": "1",
                "suggested_label_name": "door",
                "selected_label_id": "",
                "selected_label_name": "",
                "custom_label": "reception desk",
                "review_status": "confirmed",
            }])

            item = build_override_payload(path)["items"][0]

            self.assertEqual(item["fine_label_id"], None)
            self.assertEqual(item["label_type"], "human_custom")
            self.assertEqual(item["fine_label_name"], "reception desk")

    def test_rejects_unconfirmed_or_duplicate_object_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "review.csv"
            row = {
                "object_node_id": "123",
                "suggested_label_id": "1",
                "suggested_label_name": "door",
                "selected_label_id": "1",
                "selected_label_name": "door",
                "custom_label": "",
                "review_status": "pending",
            }
            self.write_csv(path, [row])
            with self.assertRaisesRegex(ValueError, "confirmed"):
                build_override_payload(path)

            row["review_status"] = "confirmed"
            self.write_csv(path, [row, row])
            with self.assertRaisesRegex(ValueError, "duplicate"):
                build_override_payload(path)


if __name__ == "__main__":
    unittest.main()
