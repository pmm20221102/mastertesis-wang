# Intent Parser Evaluation Report

**Model**: gemma4:e2b-it-qat
**Repeats**: 1
**Temperature**: 0.0

## Overall Metrics

- **Total cases**: 160
- **Schema validity**: 0.8688
- **Action accuracy (post-canonicalization)**: 0.8250
- **Action accuracy (raw LLM)**: 0.9496
- **Canonicalization rate**: 0.7562
- **Role presence P/R/F1**: 0.8375 / 0.8250 / 0.8292
- **Spurious role rate**: 0.0714
- **Quantifier accuracy**: 0.4875
- **Aggregate positive-case accuracy**: 0.5000 (n=10)
- **Strict exact match**: 0.0000 (0/160)
- **Avg latency**: 5312.8ms

- **Location F1**: 0.2000
- **Relation F1**: 0.3500
- **Multi-step exact**: 0.2000 (n=10)

## Failure Breakdown

- **missing_role**: 7
- **schema_error**: 21
- **wrong_action**: 7
- **wrong_label**: 14
- **wrong_node_type**: 111

## Category Breakdown

- **aggregate**: action=1.0000, strict=0.0000 (n=10)
- **ambiguous**: action=1.0000, strict=0.0000 (n=5)
- **avoid_prefer**: action=0.9000, strict=0.0000 (n=10)
- **escort_guide**: action=0.3000, strict=0.0000 (n=10)
- **locate_target**: action=1.0000, strict=0.0000 (n=15)
- **multi_step**: action=0.6667, strict=0.0000 (n=15)
- **navigate_target**: action=1.0000, strict=0.0000 (n=15)
- **relation**: action=1.0000, strict=0.0000 (n=20)
- **room_place_constraint**: action=1.0000, strict=0.0000 (n=20)
- **set_quantifier**: action=1.0000, strict=0.0000 (n=10)
- **source_destination**: action=0.0000, strict=0.0000 (n=10)
- **spatial**: action=1.0000, strict=0.0000 (n=15)
- **unsupported**: action=0.0000, strict=0.0000 (n=5)