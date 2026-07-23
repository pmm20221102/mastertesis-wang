# RAG Evaluation Report

**Mode**: oracle_grounding
**Model**: qwen2.5:0.5b
**Repeats**: 1

## Grounding Metrics

- **Total cases**: 6
- **Pipeline status accuracy**: 0.6667
- **Role status accuracy**: 0.0000 (n=2)
- **Role entity-ID accuracy**: 0.0000
- **Error rate**: 0.0000
- **Avg latency**: 1271.8ms

## Planning Metrics

- **Total cases**: 7
- **Planner invoked**: 4
- **Pre-set status**: 3

### Pipeline Status (all cases)
- **Status accuracy**: 1.0000

### Planner Generation (planner-invoked only)
- **Status accuracy**: 1.0000
- **Actions accuracy (subset)**: 1.0000
- **Actions exact sequence**: 0.7500
- **Actions ordered subsequence**: 1.0000
- **Steps compliance**: 1.0000

### Target IDs
- **Accuracy**: 1.0000 (n=3)
- **N/A count**: 4

### ID Safety
- **Hallucinated ID rate**: 0.0000
- **Ungrounded ID rate**: 0.0000
- **Wrong-role ID rate**: 0.0000
- **Total emitted IDs**: 6
- **Total hallucinated IDs**: 0
- **Total ungrounded IDs**: 0
- **Total wrong-role IDs**: 0

### Other
- **Error rate**: 0.0000
- **Avg latency**: 354.3ms
- **Avg planner latency**: 620.0ms
