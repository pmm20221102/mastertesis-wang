# RAG Evaluation Report

**Mode**: structured
**Model**: gemma4:e4b
**Repeats**: 1

## Grounding Metrics

- **Total cases**: 100
- **Pipeline status accuracy**: 0.1600
- **Expected-role status accuracy**: 0.1600 (n=100)
- **Expected-role entity-ID accuracy**: 0.1000 (n=50)
- **Grounding exact success rate**: 0.1600 (16/100)
- **Case failure count**: 84
- **Spurious role count**: 3
- **Spurious role rate**: 0.0291
- **Error rate**: 0.0000
- **Avg latency**: 3568.9ms

### Set Query Metrics
- **Cases**: 10
- **Macro Precision**: 0.0000
- **Macro Recall**: 0.0000
- **Macro F1**: 0.0000
- **Micro Precision**: 0.0000
- **Micro Recall**: 0.0000
- **Micro F1**: 0.0000
- **Exact set match**: 0/10 (0.0000)

### Spatial Metrics
- **Cases**: 10
- **Target ID accuracy**: 0.0000 (0/10)
- **Avg distance error**: 0.0000m

## Planning Metrics

- **Total cases**: 60
- **Planner invoked**: 60
- **Pre-set status**: 0

### Pipeline Status (all cases)
- **Status accuracy**: 0.0000

### Planner Generation (planner-invoked only)
- **Status accuracy**: 0.0000
- **Actions accuracy (subset)**: 0.0000
- **Actions exact sequence**: 0.0000
- **Actions ordered subsequence**: 0.0000
- **Steps compliance**: 0.0000
- **Validation pass rate**: 0.0000

### Target IDs
- **Accuracy**: 0.0000 (n=60)
- **N/A count**: 0

### ID Safety
- **Hallucinated ID rate**: N/A (no emitted IDs)
- **Total emitted IDs**: 0
- **Total hallucinated IDs**: 0
- **Total ungrounded IDs**: 0
- **Total wrong-role IDs**: 0

### Other
- **Error rate**: 1.0000
- **Avg latency**: 5293.2ms
- **Avg planner latency**: 5293.2ms
