# LLM + Semantic RAG + Graph Grounding Final Joint Evaluation Report (v3, Mistral)

- Date: 2026-08-06
- Model: `mistral:latest`
- Data: `joint_cases_v3.jsonl` (30 cases; ground truth generated automatically from canonical data, including Room constraints)
- Pipeline: NL -> LLM Intent -> Semantic RAG Top-1 -> deterministic Graph Grounding (predicted label + Room)
- Safeguards: no ground-truth leakage, no full-instruction fallback, strict intent gate, and comparison against ground-truth node IDs

## Acceptance Criteria (Specification Section 25)

| Criterion | Value | Met |
|---|---|---|
| Benchmark validator | PASS | ✅ |
| Intent Gate ≥80% | 96.67% | ✅ |
| Semantic Label Hit@1 ≥85% | 95.83% | ✅ |
| Semantic Recall@5 ≥90% | 100.00% | ✅ |
| Graph Grounding Exact ≥90% | 93.33% | ✅ |
| Full Joint Exact ≥80% | 93.33% | ✅ |
| Room-constrained Grounding ≥80% | 100.00% | ✅ |
| Ambiguous Candidate F1 ≥0.90 | 1.000 | ✅ |
| Set Exact Match ≥0.90 | 100.00% | ✅ |
| Invalid node ID Rate = 0 | 0 (canonical deterministic lookup) | ✅ |
| Failure Stage Coverage = 100% | ✅ | ✅ |

## Failed Cases (2/30)

- `joint_v3_029`: `Find something employees can sit on duri` stage=`semantic_retrieval`
- `joint_v3_030`: `Find items people use for writing at wor` stage=`intent_quantifier`

## Failures by Stage

- {"semantic_retrieval": 1, "intent_quantifier": 1}

## Notes

- Graph Grounding uses deterministic canonical label + Room resolution, driven by the predicted label and without ground-truth leakage.
- The single-target `resolve_entity` path depends on text semantics and is not suitable after separating Semantic RAG into its own stage; therefore this run uses deterministic label resolution.
- Semantic embeddings, retriever, threshold, and planner were not changed in this round.
