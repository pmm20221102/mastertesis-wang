# RAG Evaluation Report

**Mode**: oracle_grounding
**Model**: gemma4:e2b-it-qat
**Repeats**: 1

## Planning Metrics

- **Total cases**: 45
- **Planner invoked**: 32
- **Pre-set status**: 13

### Pipeline Status (all cases)
- **Status accuracy**: 0.8444

### Planner Generation (planner-invoked only)
- **Status accuracy**: 0.7812
- **Actions accuracy (subset)**: 0.3125
- **Actions exact sequence**: 0.3125
- **Actions ordered subsequence**: 0.3125
- **Steps compliance**: 0.7812
- **Validation pass rate**: 1.0000

### Target IDs
- **Accuracy**: 1.0000 (n=25)
- **N/A count**: 20

### ID Safety
- **Hallucinated ID rate**: 0.0000
- **Ungrounded ID rate**: 0.0000
- **Wrong-role ID rate**: 0.0000
- **Total emitted IDs**: 25
- **Total hallucinated IDs**: 0
- **Total ungrounded IDs**: 0
- **Total wrong-role IDs**: 0

### Other
- **Error rate**: 0.1556
- **Avg latency**: 5074.1ms
- **Avg planner latency**: 7135.5ms
