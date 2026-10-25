# RAG Evaluation Report

**Mode**: oracle_grounding
**Model**: mistral:latest
**Repeats**: 3

## Planning Metrics

- **Total cases**: 150
- **Planner invoked**: 126
- **Pre-set status**: 24

### Pipeline Status (all cases)
- **Status accuracy**: 0.5400

### Planner Generation (planner-invoked only)
- **Status accuracy**: 0.4524
- **Actions accuracy (subset)**: 0.6905
- **Actions exact sequence**: 0.6190
- **Actions ordered subsequence**: 0.6429
- **Steps compliance**: 0.9762
- **Validation pass rate**: 0.4524

### Target IDs
- **Accuracy**: 0.8200 (n=150)
- **N/A count**: 0

### ID Safety
- **Hallucinated ID rate**: 0.0000
- **Ungrounded ID rate**: 0.1200
- **Wrong-role ID rate**: 0.1000
- **Total emitted IDs**: 150
- **Total hallucinated IDs**: 0
- **Total ungrounded IDs**: 18
- **Total wrong-role IDs**: 15

### Other
- **Error rate**: 0.4600
- **Avg latency**: 4493.8ms
- **Avg planner latency**: 5349.8ms
