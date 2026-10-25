# Intent Parser Evaluation Report

**Model**: gemma4:e2b-it-qat
**Repeats**: 3
**Temperature**: 0.0

## Overall Metrics

- **Total cases**: 300
- **Schema validity**: 1.0000
- **Action accuracy**: 0.6900
- **Role presence P/R/F1**: 1.0000 / 1.0000 / 1.0000
- **Spurious role rate**: 0.0000
- **Quantifier accuracy**: 0.6200
- **Aggregate accuracy**: 0.8700
- **Strict exact match**: 0.4400 (132/300)
- **Avg latency**: 5486.5ms

- **Location F1**: 0.0000
- **Relation F1**: 0.0000

## Failure Breakdown

- **wrong_action**: 93
- **wrong_label**: 3
- **wrong_node_type**: 72

## Category Breakdown

- **ambiguous**: action=1.0000, strict=0.6000 (n=15)
- **complex**: action=0.6000, strict=0.0000 (n=30)
- **exact_id**: action=0.6000, strict=0.6000 (n=15)
- **not_found**: action=1.0000, strict=1.0000 (n=30)
- **object_navigation**: action=0.5000, strict=0.3000 (n=30)
- **place_constraint**: action=1.0000, strict=0.5333 (n=45)
- **relation**: action=0.2000, strict=0.0000 (n=45)
- **room_constraint**: action=1.0000, strict=0.5000 (n=30)
- **set_query**: action=1.0000, strict=1.0000 (n=30)
- **spatial**: action=0.2000, strict=0.2000 (n=30)