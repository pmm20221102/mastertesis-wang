# Intent Parser Evaluation Report

**Model**: gemma4:e2b-it-qat
**Repeats**: 1
**Temperature**: 0.0

## Overall Metrics

- **Total cases**: 60
- **Schema validity**: 0.9167
- **Action accuracy (post-canonicalization)**: 0.8667
- **Canonicalization rate**: 0.7833
- **Role presence P/R/F1**: 0.8750 / 0.8583 / 0.8639
- **Spurious role rate**: 0.0536
- **Quantifier accuracy**: 0.4500
- **Aggregate positive-case accuracy**: 0.4000 (n=5)
- **Strict exact match**: 0.0000 (0/60)
- **Avg latency**: 5640.1ms

- **Location F1**: 0.0000
- **Relation F1**: 0.0000
- **Multi-step exact**: 0.0000 (n=5)

## Failure Breakdown

- **missing_role**: 2
- **schema_error**: 5
- **wrong_action**: 3
- **wrong_target_label**: 9
- **wrong_target_node_type**: 41

## Category Breakdown

- **aggregate**: action=1.0000, strict=0.0000 (n=5)
- **avoid_prefer**: action=1.0000, strict=0.0000 (n=5)
- **locate_target**: action=1.0000, strict=0.0000 (n=7)
- **multi_step**: action=0.4000, strict=0.0000 (n=5)
- **navigate_target**: action=1.0000, strict=0.0000 (n=8)
- **relation**: action=1.0000, strict=0.0000 (n=10)
- **room_place_constraint**: action=1.0000, strict=0.0000 (n=10)
- **set_quantifier**: action=1.0000, strict=0.0000 (n=5)
- **source_destination**: action=0.0000, strict=0.0000 (n=5)