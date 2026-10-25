# Intent Parser Evaluation Report

**Model**: gemma4:e2b-it-qat
**Repeats**: 1
**Temperature**: 0.0

## Overall Metrics

- **Total cases**: 5
- **Schema validity**: 1.0000
- **Action accuracy (post-canonicalization)**: 0.6000
- **Action accuracy (raw LLM)**: 0.6000
- **Canonicalization rate**: 0.0000
- **Role presence P/R/F1**: 1.0000 / 1.0000 / 1.0000
- **Spurious role rate**: 0.0000
- **Quantifier accuracy**: 1.0000
- **Strict exact match**: 0.0000 (0/5)
- **Avg latency**: 22.0ms


## Failure Breakdown

- **wrong_action**: 2
- **wrong_node_type**: 3

## Category Breakdown

- **exact_id**: action=0.6000, strict=0.0000 (n=5)