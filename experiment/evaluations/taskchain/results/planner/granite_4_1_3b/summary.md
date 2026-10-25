# RAG Evaluation Report

**Mode**: oracle_grounding
**Model**: granite4.1:3b
**Repeats**: 3

## Planning Metrics

- **Total cases**: 150
- **Planner invoked**: 126
- **Pre-set status**: 24

### Pipeline Status (all cases)
- **Status accuracy**: 0.5133

### Planner Generation (planner-invoked only)
- **Status accuracy**: 0.4206
- **Actions accuracy (subset)**: 0.6190
- **Actions exact sequence**: 0.5635
- **Actions ordered subsequence**: 0.5873
- **Steps compliance**: 1.0000
- **Validation pass rate**: 0.4206

### Target IDs
- **Accuracy**: 0.8733 (n=150)
- **N/A count**: 0

### ID Safety
- **Hallucinated ID rate**: 0.0000
- **Ungrounded ID rate**: 0.1519
- **Wrong-role ID rate**: 0.1013
- **Total emitted IDs**: 158
- **Total hallucinated IDs**: 0
- **Total ungrounded IDs**: 24
- **Total wrong-role IDs**: 16

### Other
- **Error rate**: 0.4867
- **Avg latency**: 2164.7ms
- **Avg planner latency**: 2577.0ms
