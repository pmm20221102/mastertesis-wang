# Semantic Vector Retrieval Evaluation Report (Test v1, Held-Out)

- Date: 2026-08-05
- Data source: Office ground truth (`office_launch_dense`, 3,096 nodes / 12,335 edges)
- Vector database: `outputs/office_dense/vector_db` (6,201 documents)
- Embedding model：`paraphrase-multilingual-MiniLM-L12-v2`
- Node-type mode: `unknown`
- `not_found` threshold: `0.35` (selected on the Dev set)

## Label-Level Metrics

| Metric | Value | Threshold | Met |
|---|---|---|---|
| Test cases | 23 (15 matched + 8 not_found) | - | - |
| Label Hit@1 | 93.33% | ≥80% | ✅ |
| Label Recall@3 | 93.33% | - | - |
| Label Recall@5 | 93.33% | ≥90% | ✅ |
| Label MRR | 0.933 | - | - |
| Not-found Precision / Recall / F1 | 0.89 / 1.00 / 0.94 | - | - |
| Non-existent node ID rate | 0.0 | =0 | ✅ |

## Latency

- Cold start: `7630 ms` (first embedding-model load)
- Warm mean / median / p95：`55` / `55` / `59` ms

## Dataset Notes

- Development set: `semantic_dev_cases.jsonl` (33 cases, including LABEL_DESCRIPTIONS expansions; for debugging and threshold selection only)
- Test set: `semantic_test_cases_v1.jsonl` (23 held-out cases)
- Test queries do not occur in LABEL_DESCRIPTIONS or the alias table (see `validate_semantic_cases.py`).
- Canonical labels: `chairs, large_office_objects, large_static_wall_furniture, signs, small_office_objects`
- Test cases per label: `{"chairs": 3, "signs": 3, "small_office_objects": 3, "large_static_wall_furniture": 3, "large_office_objects": 3}`
- Includes hard negatives (semantically similar categories that are absent from the database).
- Constraints: do not invent labels, change expected values to fit results, or use 18-digit node IDs.

## Failed Case

- `semantic_t_012`: `built-in storage in the office` (expected=large_static_wall_furniture)

## Current Office Dataset Limitations

- There are few semantic categories: five meaningful object canonical labels (`small_office_objects`, `large_static_wall_furniture`, `chairs`, `signs`, `large_office_objects`); the room `unknown` label is excluded from semantic matching.
- `large_office_objects` and `small_office_objects` are coarse categories. Some phrases (e.g. `table` / `desk`) are ambiguous and are flagged in Dev; Test uses clearer unseen expressions.
- The set is not artificially expanded to 100 cases; it remains a valid 20–40-case evaluation.
