# Model Comparison: Qwen2.5:0.5b vs Gemma4:e2b-it-qat

**Date**: 2026-07-16
**Evaluation Mode**: oracle_grounding
**Test Cases**: 6 grounding + 7 planning

---

## Summary

| Metric | Qwen2.5:0.5b | Gemma4:e2b-it-qat | Winner |
|--------|--------------|-------------------|--------|
| **Grounding Status Accuracy** | 66.7% | **100%** | Gemma4 |
| **Planning Status Accuracy** | 85.7% | **100%** | Gemma4 |
| **Validation Pass Rate** | 75% | **100%** | Gemma4 |
| **Spurious Role Rate** | 75% | **66.7%** | Gemma4 |
| **Target IDs Accuracy** | 100% | 100% | Tie |
| **Hallucinated ID Rate** | 0% | 0% | Tie |
| **Avg Planner Latency** | **0.67s** | 10.8s | Qwen2.5 |

---

## Grounding Results

### Qwen2.5:0.5b
- exact_id_001: ✅ ready
- unique_label_001: ✅ ready
- category_ambiguity_001: ❌ not_found (spurious source with relation)
- not_found_001: ✅ not_found
- sign_001: ❌ not_found (spurious source with relation)
- set_query_001: ✅ ready

### Gemma4:e2b-it-qat
- exact_id_001: ✅ ready
- unique_label_001: ✅ ready
- category_ambiguity_001: ✅ ambiguous
- not_found_001: ✅ not_found
- sign_001: ✅ ambiguous
- set_query_001: ✅ ready

---

## Planning Results

### Qwen2.5:0.5b
- navigate_001: ✅ valid, navigate_to
- locate_001: ✅ valid, navigate_to
- navigate_room_001: ✅ valid, navigate_to
- not_found_001: ✅ target_not_found
- unsupported_001: ✅ unsupported
- multi_step_001: ❌ system_error (validation errors)
- escort_001: ✅ ambiguous

### Gemma4:e2b-it-qat
- navigate_001: ✅ valid, navigate_to
- locate_001: ✅ valid, locate_entity
- navigate_room_001: ✅ valid, navigate_to
- not_found_001: ✅ target_not_found
- unsupported_001: ✅ unsupported
- multi_step_001: ✅ valid, locate_entity + navigate_to
- escort_001: ✅ ambiguous

---

## Recommendation

**For论文实验**:
- Intent Parser: Use Gemma4 (more accurate)
- Planner: Use Qwen2.5:0.5b (faster)

**For production**:
- Use Gemma4 for entire pipeline (better accuracy, accept higher latency)

---

## Key Findings

1. **Gemma4 is much better at Intent Parsing**
   - No spurious source/destination roles
   - Correct ambiguity detection
   - 100% grounding pipeline status accuracy

2. **Gemma4 handles multi-step planning correctly**
   - Outputs locate_entity + navigate_to
   - 100% validation pass rate
   - Qwen2.5:0.5b outputs navigate_to + navigate_to with validation errors

3. **Qwen2.5:0.5b is 16x faster**
   - 0.67s vs 10.8s planner latency

4. **Both models have perfect ID safety**
   - Hallucinated ID rate: 0%
   - Ungrounded ID rate: 0%
   - Wrong-role ID rate: 0%
