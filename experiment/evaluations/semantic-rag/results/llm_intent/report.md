# LLM Intent Parser Evaluation Report

- Model: `gemma4:e4b` (Intent Parser unchanged)
- Cases: 30

## Metrics

| Metric | Value | Threshold | Met |
|---|---|---|---|
| Schema validity | 100.00% | ≥98% | ✅ |
| Action accuracy | 96.67% | ≥90% | ✅ |
| Target role F1 | 0.939 | ≥85% | ✅ |
| Spurious-role rate | 0.00% | ≤5% | ✅ |
| `node_type` provision rate | 0.00% | - | - |
| Location (Room) extraction | 66.67% | - | - |

## Notes

- In most cases, the current model (`gemma4:e4b`) does not emit `node_type` in `target.node_type`; instead, it outputs surface text plus inferred label/aggregate fields. The joint semantic evaluation therefore uses unknown mode, which does not depend on `node_type`.
- Room constraints in fields such as `aggregate.location` are included in the evaluation.
