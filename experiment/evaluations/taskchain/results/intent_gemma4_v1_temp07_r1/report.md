# Intent Parser Evaluation Report

**Model**: gemma4:e2b-it-qat
**Repeats**: 1
**Temperature**: 0.7

## Overall Metrics

- **Total cases**: 100
- **Schema validity**: 1.0000
- **Action accuracy**: 0.6900
- **Role presence P/R/F1**: 1.0000 / 1.0000 / 1.0000
- **Spurious role rate**: 0.0000
- **Quantifier accuracy**: 0.6600
- **Aggregate accuracy**: 0.9000
- **Strict exact match**: 0.4400 (44/100)
- **Avg latency**: 4893.0ms

- **Location F1**: 0.0000
- **Relation F1**: 0.0000

## Failure Breakdown

- **wrong_action**: 31
- **wrong_label**: 1
- **wrong_node_type**: 24

## Category Breakdown

- **ambiguous**: action=1.0000, strict=0.6000 (n=5)
- **complex**: action=0.6000, strict=0.0000 (n=10)
- **exact_id**: action=0.6000, strict=0.6000 (n=5)
- **not_found**: action=1.0000, strict=1.0000 (n=10)
- **object_navigation**: action=0.5000, strict=0.3000 (n=10)
- **place_constraint**: action=1.0000, strict=0.5333 (n=15)
- **relation**: action=0.2000, strict=0.0000 (n=15)
- **room_constraint**: action=1.0000, strict=0.5000 (n=10)
- **set_query**: action=1.0000, strict=1.0000 (n=10)
- **spatial**: action=0.2000, strict=0.2000 (n=10)