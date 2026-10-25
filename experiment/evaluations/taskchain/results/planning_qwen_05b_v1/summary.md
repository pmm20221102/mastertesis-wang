# RAG Evaluation Report

**Mode**: oracle_grounding
**Model**: qwen2.5:0.5b
**Repeats**: 1

## Planning Metrics

- **Total cases**: 45
- **Planner invoked**: 25
- **Pre-set status**: 20

### Pipeline Status (all cases)
- **Status accuracy**: 0.9333

### Planner Generation (planner-invoked only)
- **Status accuracy**: 0.8800
- **Actions accuracy (subset)**: 0.3600
- **Actions exact sequence**: 0.3600
- **Actions ordered subsequence**: 0.3600
- **Steps compliance**: 0.8800
- **Validation pass rate**: 1.0000

### Target IDs
- **Accuracy**: 0.8800 (n=25)
- **N/A count**: 20

### ID Safety
- **Hallucinated ID rate**: 0.0000
- **Ungrounded ID rate**: 0.0000
- **Wrong-role ID rate**: 0.0000
- **Total emitted IDs**: 22
- **Total hallucinated IDs**: 0
- **Total ungrounded IDs**: 0
- **Total wrong-role IDs**: 0

### Other
- **Error rate**: 0.0000
- **Avg latency**: 2660.4ms
- **Avg planner latency**: 4788.8ms
