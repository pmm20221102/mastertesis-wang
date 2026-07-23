# RAG Evaluation Report

**Mode**: oracle_grounding
**Model**: gemma4:e2b-it-qat
**Repeats**: 1

## Planning Metrics

- **Total cases**: 60
- **Planner invoked**: 60
- **Pre-set status**: 0

### Pipeline Status (all cases)
- **Status accuracy**: 0.8167

### Planner Generation (planner-invoked only)
- **Status accuracy**: 0.8167
- **Actions accuracy (subset)**: 0.9333
- **Actions exact sequence**: 0.8333
- **Actions ordered subsequence**: 0.9333
- **Steps compliance**: 1.0000
- **Validation pass rate**: 0.8167

### Target IDs
- **Accuracy**: 0.7333 (n=60)
- **N/A count**: 0

### ID Safety
- **Hallucinated ID rate**: 0.0000
- **Ungrounded ID rate**: 0.0253
- **Wrong-role ID rate**: 0.0127
- **Total emitted IDs**: 79
- **Total hallucinated IDs**: 0
- **Total ungrounded IDs**: 2
- **Total wrong-role IDs**: 1

### Other
- **Error rate**: 0.1833
- **Avg latency**: 11089.6ms
- **Avg planner latency**: 11089.6ms
