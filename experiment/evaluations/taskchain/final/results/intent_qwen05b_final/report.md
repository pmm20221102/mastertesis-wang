# Intent Parser Evaluation Report

**Model**: qwen2.5:0.5b
**Repeats**: 1
**Temperature**: 0.0

## Overall Metrics

- **Total cases**: 60
- **Schema validity**: 1.0000
- **Action accuracy (post-canonicalization)**: 0.8167
- **Canonicalization rate**: 0.9667
- **Role presence P/R/F1**: 0.9250 / 0.9333 / 0.9250
- **Spurious role rate**: 0.1268
- **Quantifier accuracy**: 0.4167
- **Aggregate positive-case accuracy**: 0.0000 (n=5)
- **Spurious aggregate count**: 8
- **Strict exact match**: 0.0000 (0/60)
- **Avg latency**: 905.3ms

- **Location F1**: 0.5000
- **Relation F1**: 0.0000
- **Multi-step exact**: 0.2000 (n=5)

## Failure Breakdown

- **missing_role**: 5
- **wrong_action**: 11
- **wrong_target_label**: 21
- **wrong_target_node_type**: 23

## Category Breakdown

- **aggregate**: action=1.0000, strict=0.0000 (n=5)
- **avoid_prefer**: action=1.0000, strict=0.0000 (n=5)
- **locate_target**: action=1.0000, strict=0.0000 (n=7)
- **multi_step**: action=0.4000, strict=0.0000 (n=5)
- **navigate_target**: action=0.7500, strict=0.0000 (n=8)
- **relation**: action=0.9000, strict=0.0000 (n=10)
- **room_place_constraint**: action=0.5000, strict=0.0000 (n=10)
- **set_quantifier**: action=1.0000, strict=0.0000 (n=5)
- **source_destination**: action=1.0000, strict=0.0000 (n=5)