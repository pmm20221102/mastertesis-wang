# Semantic Evaluation: Standalone Vector Retrieval Benchmark (v2)

This directory independently evaluates ChromaDB semantic vector retrieval for RAG. It does not call the LLM or planner.

## File Structure

```text
semantic_eval/
├── SEMANTIC_VECTOR_STATUS.md     # Vector database status
├── semantic_dev_cases.jsonl      # Development set (33 cases; includes LABEL_DESCRIPTIONS expansions)
├── semantic_test_cases_v1.jsonl  # Held-out test set (23 cases; unseen phrasings + hard negatives)
├── validate_semantic_cases.py    # Case validation, including split-overlap checks
├── run_semantic_eval.py          # Evaluation (dev/test, known/unknown, latency, report)
├── test_semantic_retriever.py    # Retrieval-interface behavior tests
├── results/                      # Results (dev/ + test_v1/ × known/unknown)
└── README.md
```

## Reproduction Steps

Dependencies: Python in the `llm-task-planner` Conda environment, `sentence-transformers`, `chromadb`, and `pyyaml`.

```bash
cd RAG/khronos_3dsg_rag  # module workspace root

# 1. Build the vector database (requires the Office ground-truth DSG)
python src/main.py build --input <GT_office_dsg.json> --output outputs/office_dense
python src/export_canonical_graph.py <GT_office_dsg.json> -o outputs/office_dense

# 2. Validate cases
python semantic_eval/validate_semantic_cases.py semantic_eval/semantic_dev_cases.jsonl
python semantic_eval/validate_semantic_cases.py semantic_eval/semantic_test_cases_v1.jsonl

# 3. Run the evaluation (dev + test, known/unknown modes)
python semantic_eval/run_semantic_eval.py --mode all --node-type-mode both --threshold 0.35

# 4. Test interface behavior
python semantic_eval/test_semantic_retriever.py
```

## Required Source Files

- `src/semantic_retriever.py` — semantic retrieval interface (`search_semantic` returns `SemanticSearchResult`)
- `src/build_vector_db.py` — ChromaDB loading (`load_vector_db` / `COLLECTION_NAME`)
- `src/build_documents.py` — document construction, including LABEL_DESCRIPTIONS
- `src/parse_3dsg.py`, `schemas.py`, `query_rag.py` — parsing and query dependencies
- `config.yaml` and `requirements.txt`

## Evaluation Scope

- Natural-language phrase → ChromaDB vector search → closest semantic canonical label/entity candidates
- Metrics: Label Hit@1, Label Recall@3/5, Label MRR, not-found P/R/F1, and cold/warm latency
- Data source: Office ground truth (`outputs/office_dense/vector_db`, 6,201 documents)
- Embedding model: `paraphrase-multilingual-MiniLM-L12-v2`

## Held-Out Test v1 Results

| Metric | Known | Unknown |
|---|---:|---:|
| Label Hit@1 | 93.3% | 93.3% |
| Label Recall@5 | 93.3% | 93.3% |
| Label MRR | 0.933 | 0.933 |
| Not-found F1 | 0.94 | 0.94 |
| Invalid node-ID rate | 0 | 0 |

## Dataset Notes

- Development: 33 cases, including expansion terms; for debugging and threshold selection only.
- Test: 23 cases (15 matched + 6 hard negatives + 2 not-found); queries do not appear in LABEL_DESCRIPTIONS or the alias list.
- Canonical labels: `small_office_objects`, `large_static_wall_furniture`, `chairs`, `signs`, and `large_office_objects`. The `unknown` room label is excluded.
- Limitation: the Office ontology has few categories; the benchmark is not artificially expanded to 100 cases.
