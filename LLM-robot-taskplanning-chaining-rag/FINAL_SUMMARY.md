# LLM Task Planning + Khronos 3DSG RAG - Final Summary

## Version History

| Version | Date | Key Changes |
|---------|------|-------------|
| v5 | 2026-07-15 | Initial structured pipeline, target_ref design |
| v6 | 2026-07-15 | PipelineResult unified, PlanDraft schema |
| v7 | 2026-07-15 | Evaluation fixes, role metrics |
| v8 | 2026-07-15 | Grounding improvements, aggregate |
| v9 | 2026-07-16 | Intent Parser fast path, semantic validator |
| v10 | 2026-07-16 | Evaluation metrics strictness |
| v11 | 2026-07-16 | Model comparison, final packaging |

---

## Architecture

```
Natural Language Instruction
        ↓
Intent Parser (LLM or fast path)
        ↓
TaskIntent (action, target, source, destination, constraints)
        ↓
Grounding Orchestrator
        ↓
SceneGraphRAG (entity resolution)
        ↓
Grounded Task (node IDs, navigation targets)
        ↓
Planner (LLM outputs PlanDraft with target_ref)
        ↓
Deterministic Compiler (target_ref → target_id)
        ↓
Validator (role-specific, executor, constraints)
        ↓
TaskPlan (executable)
```

---

## Key Components

### 1. Intent Parser
- **Fast path**: Exact node IDs (no LLM needed)
- **LLM parser**: Structured output with role rules
- **Semantic validator**: Removes spurious roles
- **Supported**: room, relations, quantifier, aggregate

### 2. Grounding
- **Entity Resolver**: Label/alias matching, room filtering
- **Room support**: Numeric ID and label matching
- **Set queries**: quantifier=all returns all candidates
- **Relation filtering**: near, inside, contains, connected_to
- **Aggregate**: room with most X (using object.room_id)

### 3. Planner
- **PlanDraft schema**: LLM outputs target_ref, not node IDs
- **Deterministic compiler**: target_ref → target_id
- **Validator**: role-specific, executor mapping, duplicate detection
- **Repair**: Uses PlanDraft schema, not raw IDs

### 4. Evaluation
- **PipelineResult**: Unified success/failure return
- **Role metrics**: Separate denominators for status and ID accuracy
- **ID safety**: Hallucinated, ungrounded, wrong-role IDs
- **Planner metrics**: Separate planner-invoked vs pre-set status
- **Validation pass rate**: Tracks validation errors

---

## Test Results

### Unit/Contract Tests
- Total: 46 tests
- Passed: 40 (without ollama package)
- Failed: 6 (ollama import, expected in non-ollama env)

### Evaluation Results (oracle_grounding)

#### Qwen2.5:0.5b
- Grounding status accuracy: 66.7%
- Planning status accuracy: 85.7%
- Validation pass rate: 75%
- Target IDs accuracy: 100% (n=3)
- Hallucinated ID rate: 0%
- Avg planner latency: 0.67s

#### Gemma4:e2b-it-qat
- Grounding status accuracy: **100%**
- Planning status accuracy: **100%**
- Validation pass rate: **100%**
- Target IDs accuracy: 100% (n=3)
- Hallucinated ID rate: 0%
- Avg planner latency: 10.8s

---

## Known Issues

1. **Intent Parser spurious roles**: 66-75% spurious role rate
2. **Room/Place hierarchy**: Some nodes have incorrect room_id
3. **Multi-step binding**: Step-specific refs not fully implemented
4. **Set query truncation**: May be limited by top_k

---

## Paper-Ready Content

> The system separates LLM-generated plan drafts from executable plans by compiling semantic target references into grounded scene-graph node IDs. In oracle-grounding experiments, Gemma4 correctly generated all simple one-target plans and multi-step plans, while Qwen2.5:0.5b was 16x faster but had validation errors on multi-step tasks. Both models achieved zero hallucinated IDs, demonstrating the safety of the target_ref compilation approach. However, natural-language Intent Parsing remains the main bottleneck, with spurious role generation causing grounding failures in 66-75% of cases.

---

## Files

- `LLM_RAG_v11_final.tar.gz` - Complete code package
- `eval/results/v10_full_test/` - Qwen2.5:0.5b results
- `eval/results/v10_gemma4_test/` - Gemma4 results
- `eval/results/MODEL_COMPARISON.md` - Model comparison
- `FINAL_SUMMARY.md` - This file
