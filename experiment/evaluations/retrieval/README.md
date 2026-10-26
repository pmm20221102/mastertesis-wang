# RAG Retrieval Evaluation Subproject

This framework evaluates retrieval accuracy in the Khronos scene-graph RAG system.

## Quick Start

```bash
# Activate the environment
conda activate llm-task-planner

# Generate evaluation cases
cd rag_eval
python scripts/build_eval_cases.py \
    --dataset office \
    --gt-dir data/groundtruth/office \
    --output data/eval_cases/office_cases.jsonl

# Run the evaluation
python scripts/run_eval.py --config configs/eval_office.yaml

# View the results
cat data/reports/eval_summary.md
```

## Project Structure

```text
rag_eval/
├── configs/                      # Evaluation configuration
│   ├── eval_office.yaml
│   ├── eval_apartment.yaml
│   └── eval_all.yaml
├── data/
│   ├── groundtruth/              # Ground-truth data
│   │   ├── office/office/        # Office dataset
│   │   └── apartment/apartment/  # Apartment dataset
│   ├── eval_cases/               # Generated evaluation cases
│   └── reports/                  # Evaluation reports
├── rag_eval/                     # Core modules
│   ├── schemas.py                # Data-class definitions
│   ├── groundtruth_loader.py     # Ground-truth parsing
│   ├── query_generator.py        # Evaluation-case generation
│   ├── retrieval_adapter.py      # RAG retrieval adapter
│   ├── metrics.py                # Evaluation metrics
│   ├── evaluator.py              # Evaluation runner
│   └── report_writer.py          # Report generation
└── scripts/
    ├── build_eval_cases.py       # Build evaluation cases
    ├── run_eval.py               # Run the evaluation
    └── inspect_index.py          # Inspect the RAG index
```

## Ground-Truth Format

`gt_dsg_consolidated.json`:
- Contains only layer-2 object nodes.
- `attributes.name`: object symbol, e.g. `O(0)` or `O(1)`.
- `attributes.bounding_box`: position and dimensions.
- `semantic_label`: unannotated (`4294967295`).

`gt_changes.csv`:
- `ObjectSymbol`: object symbol.
- `AppearedAt`: appearance time in nanoseconds.
- `DisappearedAt`: disappearance time (`0` means not disappeared).

## V1 Evaluation Objectives

| Category | Description | Query type |
|------|------|----------|
| A. Index Coverage | Whether ground-truth objects appear in the RAG index | N/A (statistical) |
| B. Object Symbol Retrieval | Retrieve by object symbol | `Retrieve O(370)` |
| C. Object Attribute Retrieval | Retrieve by attribute | `bounding box of O(370)` |
| D. Change Retrieval | Retrieve object changes | `Which objects disappeared?` |

## Evaluation Metrics

- **Hit@k**: whether the top-k results contain at least one correct result.
- **MRR@k**: reciprocal rank of the first correct result.
- **Recall@k**: proportion of correct results included in the top-k.
- **Precision@k**: proportion of top-k results that are correct.
- **Index Coverage**: proportion of ground-truth objects represented in the RAG index.

## Important Limitations

### Differences Between Ground Truth and the RAG Index

1. **Ground truth** (`gt_dsg_consolidated.json`):
   - Objects have a `name` field, e.g. `O(0)` or `O(1)`.
   - The position coordinate frame may differ from the SLAM output.

2. **RAG index** (Khronos SLAM output):
   - The object `name` field is empty.
   - A 64-bit integer is used as `node_id`.
   - Object-symbol information is unavailable.

### Position-Based Matching

The evaluator matches ground-truth objects to RAG-index objects by position (distance between bounding-box centers).

Default threshold: 5.0 meters (configurable).

### Query-Type Compatibility

V1 queries such as `Retrieve O(0)` may not match RAG-index content directly because:
- The RAG index has no object-symbol information.
- Vector search uses semantic similarity rather than exact matching.

**Recommendation**: Future versions should use position-based queries or rebuild the RAG index with object-symbol information.

## Configuration

`configs/eval_office.yaml`:
```yaml
dataset: office
gt_dir: data/groundtruth/office
db_dir: llm-rag/RAG/khronos_3dsg_rag/outputs/khronos_office/vector_db
top_k: 10
seed: 42
output_dir: data/reports
```

## Inspect the RAG Index

```bash
python experiment/evaluations/retrieval/scripts/inspect_index.py --db-dir llm-rag/RAG/khronos_3dsg_rag/outputs/khronos_office/vector_db
```

## Output Reports

- `eval_summary.json`: structured results
- `eval_summary.md`: Markdown report
- `per_case_results.jsonl`: detailed results for each case
- `failures.jsonl`: analysis of failed cases

## Potential Improvements

1. Rebuild the RAG index with object-symbol information.
2. Use position-based queries instead of object-symbol queries.
3. Add more ground-truth annotations (room, category).
4. Support comparisons across retrieval modes (`vector_only`, `hybrid`, `bm25`).
