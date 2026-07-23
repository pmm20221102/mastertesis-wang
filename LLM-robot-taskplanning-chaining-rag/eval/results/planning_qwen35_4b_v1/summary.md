# RAG Evaluation Report

**Mode**: oracle_grounding
**Model**: qwen3.5:4b
**Repeats**: 1

## Planning Metrics

- **Total cases**: 45
- **Planner invoked**: 25
- **Pre-set status**: 20

### Pipeline Status (all cases)
- **Status accuracy**: 0.8222

### Planner Generation (planner-invoked only)
- **Status accuracy**: 0.6800
- **Actions accuracy (subset)**: 0.6800
- **Actions exact sequence**: 0.6800
- **Actions ordered subsequence**: 0.6800
- **Steps compliance**: 0.6800
- **Validation pass rate**: 1.0000

### Target IDs
- **Accuracy**: 0.6800 (n=25)
- **N/A count**: 20

### ID Safety
- **Hallucinated ID rate**: 0.0000
- **Ungrounded ID rate**: 0.0000
- **Wrong-role ID rate**: 0.0000
- **Total emitted IDs**: 17
- **Total hallucinated IDs**: 0
- **Total ungrounded IDs**: 0
- **Total wrong-role IDs**: 0

### Other
- **Error rate**: 0.0000
- **Avg latency**: 50357.1ms
- **Avg planner latency**: 90642.7ms
