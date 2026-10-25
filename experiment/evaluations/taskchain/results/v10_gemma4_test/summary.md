# RAG Evaluation Report

**Mode**: oracle_grounding
**Model**: gemma4:e2b-it-qat
**Repeats**: 1

## Grounding Metrics

- **Total cases**: 6
- **Pipeline status accuracy**: 1.0000
- **Expected-role status accuracy**: 1.0000 (n=2)
- **Expected-role entity-ID accuracy**: 0.5000 (n=2)
- **Spurious role count**: 4
- **Spurious role rate**: 0.6667
- **Error rate**: 0.0000
- **Avg latency**: 12075.7ms

## Planning Metrics

- **Total cases**: 7
- **Planner invoked**: 4
- **Pre-set status**: 3

### Pipeline Status (all cases)
- **Status accuracy**: 1.0000

### Planner Generation (planner-invoked only)
- **Status accuracy**: 1.0000
- **Actions accuracy (subset)**: 0.7500
- **Actions exact sequence**: 0.5000
- **Actions ordered subsequence**: 0.7500
- **Steps compliance**: 1.0000
- **Validation pass rate**: 1.0000

### Target IDs
- **Accuracy**: 1.0000 (n=3)
- **N/A count**: 4

### ID Safety
- **Hallucinated ID rate**: 0.0000
- **Ungrounded ID rate**: 0.0000
- **Wrong-role ID rate**: 0.0000
- **Total emitted IDs**: 5
- **Total hallucinated IDs**: 0
- **Total ungrounded IDs**: 0
- **Total wrong-role IDs**: 0

### Other
- **Error rate**: 0.0000
- **Avg latency**: 6158.0ms
- **Avg planner latency**: 10776.5ms
