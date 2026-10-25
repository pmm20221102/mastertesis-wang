# Surface-v1 Four-Condition Ablation With LLM2

## Protocol

- Run: `runs/surface-v1-llm2-60x4-20261020/` (240/240 trials; one pass).
- Model: `gemma4:e2b-it-qat`, digest `07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c`.
- Dataset: the unchanged 60 public surface-v1 instructions; task SHA-256 `edc92900e2b45f10ae25d86d364eb7f783ba054f91f13cb42bc23f48dae65946`.
- Prompt: `hospital_taskchain_surface_v1`, SHA-256 `9e2ed2e20fe96698b7d57ba91436e5a06ebd0287cf94e6aa2e9a4ba1497f67c7`.
- Conditions: Full = LLM1 + structured RAG + LLM2; No LLM1 = rule parser + structured RAG + LLM2; Raw DSG = LLM1 + Raw DSG + LLM2; No LLM2 = LLM1 + structured RAG + deterministic compiler.
- Predictions did not read gold. After inference, all conditions were scored offline with the existing `score_taskchain_benchmark.score_records` function. No Nav2 execution was performed.

## Gold-scored task-chain success

| Condition | Overall | Basic 40 | Surface-form 20 | Exact navigable chains | Stop accuracy | Boundary behavior |
|---|---:|---:|---:|---:|---:|---:|
| Full | 47/60 (78.3%) | 38/40 (95.0%) | 9/20 (45.0%) | 42/53 (79.2%) | 58/71 (81.7%) | 5/7 (71.4%) |
| No LLM1 | 12/60 (20.0%) | 11/40 (27.5%) | 1/20 (5.0%) | 9/53 (17.0%) | 11/71 (15.5%) | 3/7 (42.9%) |
| Raw DSG | 40/60 (66.7%) | 38/40 (95.0%) | 2/20 (10.0%) | 35/53 (66.0%) | 49/71 (69.0%) | 5/7 (71.4%) |
| No LLM2 | 40/60 (66.7%) | 38/40 (95.0%) | 2/20 (10.0%) | 35/53 (66.0%) | 49/71 (69.0%) | 5/7 (71.4%) |

## Paired outcomes

- Full vs No LLM2: 40 both succeeded, 13 both failed, 7 Full-only successes, 0 No-LLM2-only successes. All seven Full-only wins are in the surface-form 20: 2 both succeeded, 11 both failed, 7 Full-only succeeded.
- Full vs Raw DSG: the same paired task-success pattern: 40 both succeeded, 13 both failed, 7 Full-only successes, 0 Raw-only successes.
- No LLM2 vs Raw DSG: identical task-level gold outcomes: 40 both succeeded and 20 both failed.

## Interpretation limits

The comparison suggests that, for this run, LLM2 contributes when paired with structured-RAG grounding on surface-form tasks; it produced no aggregate gain in the Raw-DSG arm. The earlier surface-v1 Full run scored 48/60, while this current-source Full scored 47/60. Model, prompt, and task hashes match, but execution-source hashes changed in `llm/plan_contract.py` and experiment runner/dispatcher files, so treat the earlier run as context rather than an exact paired replicate.

This is prompt-tuned development evidence, not untouched independent confirmation. Gold-scored task-chain success is not Nav2 path success or robot arrival. Raw predictions and provenance are in the run directory above.
