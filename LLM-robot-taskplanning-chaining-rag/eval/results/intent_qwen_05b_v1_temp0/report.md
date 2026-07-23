# Intent Parser Evaluation Report

**Model**: qwen2.5:0.5b
**Repeats**: 1
**Temperature**: 0.0

## Overall Metrics

- **Total cases**: 100
- **Schema validity**: 1.0000
- **Action accuracy**: 0.6700
- **Role presence P/R/F1**: 0.9650 / 1.0000 / 0.9767
- **Spurious role rate**: 0.0654
- **Quantifier accuracy**: 0.6600
- **Aggregate accuracy**: 0.8300
- **Strict exact match**: 0.0400 (4/100)
- **Avg latency**: 749.9ms

- **Location F1**: 0.2500
- **Relation F1**: 0.0000

## Failure Breakdown

- **wrong_action**: 33
- **wrong_label**: 16
- **wrong_node_type**: 47

## Category Breakdown

- **ambiguous**: action=1.0000, strict=0.0000 (n=5)
- **complex**: action=0.6000, strict=0.0000 (n=10)
- **exact_id**: action=0.6000, strict=0.6000 (n=5)
- **not_found**: action=1.0000, strict=0.1000 (n=10)
- **object_navigation**: action=0.3000, strict=0.0000 (n=10)
- **place_constraint**: action=1.0000, strict=0.0000 (n=15)
- **relation**: action=0.2000, strict=0.0000 (n=15)
- **room_constraint**: action=1.0000, strict=0.0000 (n=10)
- **set_query**: action=1.0000, strict=0.0000 (n=10)
- **spatial**: action=0.2000, strict=0.0000 (n=10)