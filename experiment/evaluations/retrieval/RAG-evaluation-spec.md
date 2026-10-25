# RAG Evaluation Subproject Specification

## 1. Project Goal

Create a parallel evaluation subproject to validate the correctness of the existing Scene-Graph-based RAG system.

The main RAG project builds a vector database from scene graph data, then retrieves relevant chunks for user queries. Since the office and apartment datasets contain groundtruth annotations, this evaluation project must use those groundtruth files to measure whether the RAG retrieval system can find the correct scene graph information.

The evaluation target is primarily **retrieval correctness**, not free-form LLM answer quality.

The project should answer questions such as:

* Can the RAG system retrieve the correct object node when queried by object name or category?
* Can it retrieve the correct room/place for an object?
* Can it retrieve all relevant objects in a room?
* Can it retrieve time-dependent object states or changes if temporal information exists?
* Can it distinguish similar objects, rooms, and relations?
* Does hybrid search with metadata improve over pure vector search?

---

## 2. Relationship to Main RAG Project

This evaluation project should be implemented as a **parallel subproject**, not mixed directly into the main scene graph RAG pipeline.

Recommended directory:

```text
rag_eval/
```

The evaluation code may call the existing RAG project through a stable interface, for example:

```python
retriever.search(query: str, top_k: int, filters: dict | None) -> list[RetrievedChunk]
```

If the main project does not yet expose such an interface, create a small adapter layer instead of rewriting the main RAG logic.

The evaluation project should not directly modify chunking, embedding, or vector database logic unless explicitly required. Its role is to test the existing RAG system.

---

## 3. Core Design Principle

Do not evaluate RAG by asking an LLM whether the answer looks correct.

Use groundtruth-based automatic evaluation.

The groundtruth must be parsed independently from the RAG index. The evaluator should compare retrieved chunk metadata against groundtruth labels.

A retrieved chunk is considered correct if its metadata matches the expected groundtruth entity, for example:

```json
{
  "dataset": "office",
  "scene_id": "office_001",
  "object_id": "obj_23",
  "object_category": "chair",
  "room_id": "room_2",
  "room_name": "meeting room",
  "time_start": 12.4,
  "time_end": 28.9,
  "node_id": "O_23"
}
```

The evaluation should prefer metadata-level matching over text-level similarity.

---

## 4. Required Subproject Structure

Create the following structure:

```text
rag_eval/
├── README.md
├── configs/
│   ├── eval_office.yaml
│   ├── eval_apartment.yaml
│   └── eval_all.yaml
├── data/
│   ├── groundtruth/
│   │   ├── office/
│   │   └── apartment/
│   ├── eval_cases/
│   │   ├── office_cases.jsonl
│   │   ├── apartment_cases.jsonl
│   │   └── combined_cases.jsonl
│   └── reports/
├── rag_eval/
│   ├── __init__.py
│   ├── schemas.py
│   ├── groundtruth_loader.py
│   ├── query_generator.py
│   ├── retrieval_adapter.py
│   ├── metrics.py
│   ├── evaluator.py
│   └── report_writer.py
├── scripts/
│   ├── build_eval_cases.py
│   ├── run_eval.py
│   └── compare_retrievers.py
└── requirements.txt
```

---

## 5. Data Schemas

### 5.1 Groundtruth Entity Schema

Normalize office and apartment groundtruth into a common internal schema:

```python
@dataclass
class GroundTruthEntity:
    dataset: str
    scene_id: str | None
    entity_type: str              # object, room, place, relation, temporal_event
    entity_id: str
    category: str | None
    name: str | None
    room_id: str | None
    room_name: str | None
    position: dict | None         # x, y, z if available
    time_start: float | None
    time_end: float | None
    attributes: dict
```

The exact parser can be completed after the office and apartment groundtruth files are provided.

---

### 5.2 Evaluation Case Schema

Each evaluation case should be stored as JSONL.

Example:

```json
{
  "case_id": "office_object_location_0001",
  "dataset": "office",
  "query_type": "object_location",
  "query": "Where is the mug located in the office scene?",
  "language": "en",
  "expected": {
    "entity_type": "object",
    "object_id": "obj_17",
    "object_category": "mug",
    "room_id": "room_3",
    "room_name": "kitchen"
  },
  "relevant_ids": ["obj_17", "room_3"],
  "difficulty": "easy",
  "notes": "Generated from groundtruth."
}
```

Do not generate evaluation cases from retrieved RAG chunks. They must be generated from groundtruth.

---

## 6. Query Categories

The evaluation should include several query categories.

### 6.1 Object Existence Queries

Purpose: test whether RAG can find whether an object exists in the scene.

Examples:

```text
Is there a mug in the office scene?
Does the apartment contain a sofa?
Which laptops are visible in the scene?
```

Groundtruth target:

```text
object category / object id
```

Metrics:

```text
Hit@k, Recall@k, Precision@k
```

---

### 6.2 Object Location Queries

Purpose: test whether RAG retrieves the correct object and its room/place.

Examples:

```text
Where is the mug?
In which room is the chair located?
Where can I find the laptop in the apartment?
```

Groundtruth target:

```text
object_id + room_id / room_name
```

A result is fully correct if both the object and room match.

---

### 6.3 Room Contains Object Queries

Purpose: test room-level retrieval.

Examples:

```text
What objects are in the kitchen?
List the objects in the meeting room.
Which objects are located in the bedroom?
```

Groundtruth target:

```text
all object_ids belonging to a room
```

Metrics:

```text
Recall@k, Precision@k, F1@k
```

This category may have multiple correct chunks.

---

### 6.4 Category Retrieval Queries

Purpose: test whether the system can retrieve all objects of a semantic category.

Examples:

```text
Find all chairs in the office.
Which tables are present in the apartment?
Retrieve all monitors in the scene.
```

Groundtruth target:

```text
all objects with the requested category
```

This is useful for testing semantic category matching.

---

### 6.5 Spatial Relation Queries

Only implement this if the groundtruth contains relation information or if relations are encoded in the scene graph.

Examples:

```text
Which object is on the table?
What is near the sofa?
Which objects are close to the desk?
```

Groundtruth target:

```text
relation tuple: subject_id, relation_type, object_id
```

Metrics:

```text
Hit@k, Recall@k, MRR@k
```

---

### 6.6 Temporal Queries

Only implement this if the scene graph or groundtruth contains time-dependent information.

Examples:

```text
Where was the mug at time t?
Which objects changed location?
What objects appeared or disappeared during the sequence?
Where is the object in the latest scene state?
```

Groundtruth target:

```text
object_id + time interval + location / state
```

A result is correct only if the retrieved chunk matches the correct object and the relevant time window.

---

### 6.7 Negative Queries

Purpose: test whether RAG retrieves irrelevant information when the answer should be absent.

Examples:

```text
Is there a bathtub in the office scene?
Is there a conference table in the bedroom?
Is there a printer in the kitchen?
```

Groundtruth target:

```text
no matching object / no matching relation
```

Metrics:

```text
false positive rate
```

The evaluator should check whether the system incorrectly retrieves confident but irrelevant chunks.

---

### 6.8 Multi-language Query Variants

For each important query type, generate English, German, and Chinese variants if possible.

Example:

```text
EN: Where is the mug?
DE: Wo befindet sich die Tasse?
ZH: 杯子在哪里？
```

This is important because the user often works with Chinese questions, English papers, and German documents.

---

## 7. Retrieval Modes to Compare

The evaluation should support multiple retrieval modes.

At minimum:

```text
1. vector_only
2. bm25_only
3. hybrid_vector_bm25
4. hybrid_with_metadata_filter
5. hybrid_with_reranker
```

If not all modes are implemented in the main RAG system yet, implement the evaluator so that missing modes can be skipped.

The comparison should answer:

* Does vector search alone work?
* Does BM25 help with object names, room names, and IDs?
* Does metadata filtering improve precision?
* Does reranking improve top-1 / top-3 accuracy?
* Which query categories fail most often?

---

## 8. Metrics

Implement the following metrics.

### 8.1 Hit@k

A case is successful if at least one correct groundtruth entity appears in the top-k retrieved chunks.

Use:

```text
Hit@1
Hit@3
Hit@5
Hit@10
```

---

### 8.2 Recall@k

For cases with multiple relevant entities, measure how many expected entities were retrieved.

Example:

```text
query: "What objects are in the kitchen?"
groundtruth objects: 10
retrieved correct objects in top 20: 7

Recall@20 = 0.7
```

---

### 8.3 Precision@k

Measure how many retrieved chunks are correct.

Example:

```text
top 10 retrieved chunks
correct chunks: 4

Precision@10 = 0.4
```

---

### 8.4 MRR@k

Mean Reciprocal Rank measures how early the first correct result appears.

Example:

```text
first correct result at rank 2
MRR = 1 / 2 = 0.5
```

---

### 8.5 nDCG@k

Optional but useful. Use graded relevance:

```text
3 = exact object + exact room + exact time
2 = correct object but incomplete location/time
1 = correct category or related room only
0 = irrelevant
```

---

### 8.6 Coverage

Measure whether the expected groundtruth entities are actually present in the RAG index.

This is important because retrieval cannot succeed if the correct object/room was never indexed.

Report:

```text
Index coverage = indexed expected entities / total expected entities
```

---

### 8.7 Latency

Measure average retrieval time per query.

Report:

```text
mean latency
median latency
p95 latency
```

---

## 9. Evaluation Pipeline

The evaluation should run in this order:

```text
1. Load groundtruth files.
2. Normalize groundtruth into common schema.
3. Generate deterministic evaluation cases.
4. Save evaluation cases as JSONL.
5. For each query case:
   a. Send query to RAG retriever.
   b. Collect top-k results.
   c. Extract metadata from retrieved chunks.
   d. Compare retrieved metadata with expected groundtruth.
   e. Compute metrics.
6. Save detailed per-query results.
7. Save summary report.
8. Save failure analysis report.
```

Do not regenerate evaluation cases during every run unless explicitly requested.

---

## 10. Output Reports

The evaluator should generate:

```text
data/reports/eval_summary.json
data/reports/eval_summary.csv
data/reports/eval_summary.md
data/reports/failures.jsonl
data/reports/per_case_results.jsonl
```

The Markdown report should contain:

```text
1. Dataset summary
2. Number of evaluation cases
3. Retrieval mode comparison
4. Metrics by query type
5. Metrics by language
6. Metrics by difficulty
7. Top failure examples
8. Recommendations for improving the RAG system
```

Example report table:

```text
| Dataset | Mode | Hit@1 | Hit@5 | Recall@10 | MRR@10 | Avg Latency |
|---------|------|-------|-------|-----------|--------|-------------|
| office | vector_only | 0.52 | 0.76 | 0.61 | 0.64 | 0.12s |
| office | hybrid | 0.68 | 0.89 | 0.78 | 0.75 | 0.18s |
| apartment | vector_only | ... | ... | ... | ... | ... |
```

---

## 11. Failure Analysis

For failed cases, store:

```json
{
  "case_id": "office_object_location_0001",
  "query": "Where is the mug?",
  "expected": {
    "object_id": "obj_17",
    "room_name": "kitchen"
  },
  "retrieved_top_5": [
    {
      "rank": 1,
      "score": 0.82,
      "metadata": {
        "object_id": "obj_22",
        "object_category": "cup",
        "room_name": "meeting room"
      },
      "text_preview": "..."
    }
  ],
  "failure_reason": "wrong_object_or_wrong_room"
}
```

Failure categories should include:

```text
wrong_object
wrong_room
wrong_time
category_confusion
language_mismatch
missing_metadata
not_indexed
retrieved_chunk_has_no_entity_id
ambiguous_query
```

---

## 12. Important Implementation Requirements

1. Do not use LLM-generated answers as the primary evaluation signal.
2. Use groundtruth entity IDs, room IDs, object categories, and timestamps for scoring.
3. Make the evaluator independent from the indexed chunks.
4. Do not generate groundtruth from the RAG output.
5. Use deterministic query generation with a fixed random seed.
6. Store every evaluation case and every retrieval result.
7. Support office and apartment as separate datasets.
8. Support combined evaluation across both datasets.
9. Allow top-k to be configurable.
10. Allow retrieval mode to be configurable.
11. Make it possible to run the evaluation repeatedly and compare results over time.

---

## 13. Minimal Version First

Implement V1 first with the following scope:

```text
Datasets:
- office
- apartment

Query types:
- object existence
- object location
- room contains objects
- category retrieval

Metrics:
- Hit@1
- Hit@5
- Hit@10
- Recall@10
- Precision@10
- MRR@10
- Index coverage

Retrieval modes:
- vector_only
- hybrid_vector_bm25 if available
```

Do not implement temporal queries or relation queries in V1 unless the groundtruth schema clearly supports them.

---

## 14. Example Commands

Build evaluation cases:

```bash
python scripts/build_eval_cases.py --dataset office --gt-path data/groundtruth/office --output data/eval_cases/office_cases.jsonl
python scripts/build_eval_cases.py --dataset apartment --gt-path data/groundtruth/apartment --output data/eval_cases/apartment_cases.jsonl
```

Run evaluation:

```bash
python scripts/run_eval.py --config configs/eval_office.yaml
python scripts/run_eval.py --config configs/eval_apartment.yaml
python scripts/run_eval.py --config configs/eval_all.yaml
```

Compare retrieval modes:

```bash
python scripts/compare_retrievers.py --cases data/eval_cases/combined_cases.jsonl --modes vector_only hybrid hybrid_rerank
```

---

## 15. Success Criteria

The evaluation subproject is successful if it can:

1. Parse office and apartment groundtruth into a unified schema.
2. Generate deterministic evaluation cases from groundtruth.
3. Query the existing RAG retriever.
4. Compare retrieved chunks against groundtruth.
5. Compute retrieval metrics automatically.
6. Produce clear reports showing which query types succeed or fail.
7. Identify whether RAG failures come from:

   * missing indexed data,
   * bad chunking,
   * missing metadata,
   * weak embedding retrieval,
   * poor BM25/metadata filtering,
   * lack of reranking,
   * ambiguous query design.

---

## 16. Expected Final Deliverables

The agent should deliver:

```text
rag_eval/ subproject
README.md
config files
groundtruth loader stubs
evaluation case generator
retrieval adapter
metrics implementation
evaluation runner
report writer
sample evaluation cases
sample report with mock data if real groundtruth is not yet provided
```

If the real office and apartment groundtruth files are not yet available, implement the code using placeholder loaders and clearly mark where dataset-specific parsing needs to be completed later.

Do not block the whole implementation just because the groundtruth files are not yet provided.
