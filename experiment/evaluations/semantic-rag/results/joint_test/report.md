# LLM + Semantic RAG + Graph Grounding Joint Test Report

- Model: `gemma4:e4b` (LLM / Intent Parser / Graph Grounding unchanged)
- Cases: 30

## Success Rate by Stage

| Stage | Success rate | Threshold | Met |
|---|---|---|---|
| Intent | 100.00% | - | - |
| Semantic Retrieval | 96.67% | ≥85% Hit@1 | - |
| Graph Grounding | 96.67% | - | - |
| Correct final node ID | 96.67% | - | - |
| **Joint success** | **96.67%** | ≥80-85% | ✅ |

## Failure by Stage

- `{"semantic_retrieval": 1}` (failure-stage coverage: 100%)

## Failed Case

- `joint_011`: `Find storage attached to the structure.` stage=`semantic_retrieval` exp=large_static_wall_furniture

## Data Limitations

- Canonical Room nodes have no room number (`label=unknown`), so Room constraints such as `room 0752` cannot be matched exactly during grounding. Record this as a data limitation.
- The Office ontology has only five meaningful object labels.
