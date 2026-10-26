# Khronos 3D Scene Graph RAG

Khronos 3D Dynamic Scene Graph RAG system. Provides structured scene graph parsing, canonical graph export, vector retrieval, and deterministic Grounding backend.

---

## Overview

Converts structured scene graphs (DSG JSON) from Khronos SLAM into a canonical graph and searchable vector database, supporting natural language and structured queries for objects, rooms, spatial relationships, and hierarchical constraints.

---

## Core Features

- **Canonical Graph Export**: export directly from raw DSG with 100% hierarchy validity.
- **Hard Constraint Filtering**: hard constraints on `node_type`, label, location, and relation.
- **Fast Path Resolution**: direct queries by exact ID, nearest position, or label.
- **Multi-Location Constraints**: `LocationConstraint` supports simultaneous Place and Room constraints.
- **Relation Filtering**: `inside`, `near`, `connected_to`, and transitive `contains` relations.
- **Aggregate Queries**: `max_count`, `min_count`, and `count_in_location`.
- **Graph-Aware Chunks**: Entity, Context, and Region documents include graph-structure metadata.

---

## Project Structure

```
khronos_3dsg_rag/
├── README.md
├── data/
│   └── dsg.json                 # Khronos SLAM output (1,858 nodes, 6,205 edges)
├── outputs/
│   ├── canonical_graph.json      # v1 canonical graph (legacy)
│   ├── canonical_graph_v2.json   # v2 canonical graph (exported directly from raw DSG)
│   ├── canonical_graph_v2.manifest.json
│   ├── rag_documents.jsonl       # RAG documents
│   ├── vector_db/                # ChromaDB vector database
│   └── khronos_office_new/       # Office dataset output
│       ├── canonical_graph_v2.json
│       ├── canonical_graph_v2.manifest.json
│       ├── rag_documents.jsonl
│       └── vector_db/
└── src/
    ├── main.py                   # CLI entry point
    ├── parse_3dsg.py             # DSG JSON parser (including position fixes)
    ├── export_canonical_graph.py # Canonical graph exporter (directly from raw DSG)
    ├── build_documents.py        # RAG document builder (graph-aware enrichment)
    ├── build_vector_db.py        # ChromaDB vector index builder
    ├── canonical_index.py        # Canonical index (loads v1/v2 formats)
    ├── scene_graph_api.py        # Scene-graph API (hard constraints + fast paths)
    ├── entity_resolver.py        # Entity resolver
    ├── graph_ranker.py           # Graph-aware ranker
    ├── query_rag.py              # RAG query interface
    ├── api_schemas.py            # API data models
    └── schemas.py                # Internal data models
```

---

## Canonical Graph v2

### Optional Human-Confirmed Fine-Semantic Overrides

Hospital P1000 unknown object labels can be supplied as a separate, provenance-tracked sidecar. The parser applies only entries marked `human_confirmed` to matching object-layer nodes whose original Khronos `semantic_label` is the unknown sentinel `4294967295`. Raw DSG attributes are not changed. Fine-label IDs remain in the sidecar; the RAG parser uses the fine-label name as the node label because these IDs are not Khronos semantic-label IDs.

Export a separate canonical graph for the structured `SceneGraphRAG` index:

```bash
python src/export_canonical_graph.py /path/to/hospital/full-data/dsg.json \
  --semantic-overrides /path/to/hospital-object-semantic-overrides.json \
  --output-dir outputs/hospital_p1000_manual_semantic_overlay
```

The single-input builder below writes both the canonical graph and optional Chroma vector DB in the same output folder when the sidecar is supplied (do not use `--all` for this dataset-specific file):

```bash
python src/main.py build \
  --input /path/to/hospital/full-data/dsg.json \
  --semantic-overrides /path/to/hospital-object-semantic-overrides.json \
  --output outputs/hospital_p1000_manual_semantic_overlay
```

Non-confirmed entries, missing/non-object nodes, or nodes whose source label is not the unknown sentinel fail closed.

### Reproducible Hospital P1000 build

The reproducible build converts the human review CSV into a hashed semantic
sidecar, exports the canonical graph and documents, and builds a pinned vector
index. It never edits the source DSG or review CSV. The command refuses to
overwrite an existing output directory; choose a new output name for each
build. The manifest records input hashes, software versions, embedding model
revision/device, artifact hashes, and the semantic build ID. A separate stable
logical digest covers every Chroma document, metadata record, and stored vector;
runtime SQLite metadata changes do not invalidate the content check.

```bash
cd /path/to/Masterthesis-wang
python3 llm-rag/RAG/khronos_3dsg_rag/src/main.py \
  --config llm-rag/RAG/khronos_3dsg_rag/config.yaml build \
  --reproducible \
  --dataset hospital-p1000 \
  --input /path/to/hospital/full-data/dsg.json \
  --review-csv /path/to/hospital-object-review-2026-10-12.csv \
  --embedding-model paraphrase-multilingual-MiniLM-L12-v2 \
  --embedding-model-revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
  --embedding-device cpu \
  --output llm-rag/RAG/khronos_3dsg_rag/outputs/hospital_p1000_reproducible_20261024_v5
```

The 27 GB source snapshot and reviewed CSV are local inputs and are intentionally
not stored in Git. Replace their input paths when rebuilding on another machine.
The v5 vector database itself is rebuildable and is intentionally excluded from
the source commit; this output path matches the default Nav2 planner profile.

The pinned embedding model runs on CPU so the GPU remains available to Ollama.
Rebuilding requires the pinned model revision to be available locally or
downloadable. Do not overwrite or relabel an existing run; compare its manifest
and source hashes first.

### Export Method

Export directly from the raw DSG without passing through ChromaDB or RAG chunk metadata:

Direct export from raw DSG, without going through ChromaDB or RAG chunk metadata:

```bash
cd llm-rag/RAG/khronos_3dsg_rag
python src/export_canonical_graph.py data/dsg.json -o outputs/
```

### Data Statistics

| Metric | Value |
|--------------|-----------|
| Total nodes | 1,858 |
| Object nodes | 191 |
| Agent nodes | 394 |
| Place nodes | 1,262 |
| Room nodes | 10 |
| Building nodes | 1 |
| Hierarchy edges | 1,867 |
| Neighbor edges | 4,338 |
| Hierarchy validity | 100% |
| Object→Place coverage | 100% |
| Place→Room coverage | 98.9% |
| Position coverage | 100% (1,262/1,262 unique) |

### Canonical Graph Schema v2

```json
{
  "schema_version": 2,
  "source_dsg_hash": "27a21bd8f590bfde",
  "total_nodes": 1858,
  "nodes": {
    "NODE_ID": {
      "node_id": "...",
      "layer": 2,
      "partition": 0,
      "node_type": "object",
      "label": "chairs",
      "position": [x, y, z],
      "parent_place_ids": ["..."],
      "parent_room_ids": ["..."],
      "child_ids": ["..."],
      "neighbor_ids": ["..."],
      "hierarchy_path_ids": ["..."],
      "room_id": "...",
      "place_id": "..."
    }
  },
  "edges": {
    "hierarchy": [{"parent": "...", "child": "..."}],
    "neighbors": [{"source": "...", "target": "..."}]
  }
}
```

---

## Scene Graph API

### Fast Paths

```python
from scene_graph_api import SceneGraphRAG

rag = SceneGraphRAG(db_dir)

# 1. Exact ID fast path
result = rag.resolve_entity(EntityReference(text="5692549928996306945"))

# 2. Nearest-by-position (direct canonical-index traversal)
result = rag.resolve_entity(EntityReference(
    text="nearest place",
    node_type="place",
    position=[0, 20, 2],
    quantifier="nearest",
))

# 3. All-by-label (direct canonical-index lookup)
result = rag.resolve_entity(EntityReference(
    text="chairs",
    label="chairs",
    node_type="object",
    quantifier="all",
))
```

### Hard Constraint Filtering

```python
from planner_schemas.intent import EntityReference, LocationConstraint, RelationConstraint

# Location constraint (Place + Room simultaneously)
ref = EntityReference(
    text="chair",
    label="chairs",
    node_type="object",
    locations=[
        LocationConstraint(node_id="PLACE_ID", node_type="place"),
        LocationConstraint(node_id="ROOM_ID", node_type="room"),
    ],
)

# Relation constraint (inside / near / connected_to / contains)
ref = EntityReference(
    text="object",
    label="chairs",
    node_type="object",
    relations=[RelationConstraint(
        relation="inside",
        target_node_id="PLACE_ID",
        target_node_type="place",
    )],
)
```

### Aggregate Queries

```python
from planner_schemas.intent import AggregateConstraint, LocationConstraint

ref = EntityReference(
    text="room with most chairs",
    node_type="room",
    aggregate=AggregateConstraint(
        operation="count_in_location",
        target_labels=["chairs"],
        location=LocationConstraint(node_id="ROOM_ID", node_type="room"),
    ),
)
```

---

## Installation

```bash
# Run from the repository root with the shared project environment:
.venv/bin/pip install -r llm-rag/requirements.txt
```

---

## Usage

### Export the Canonical Graph

```bash
cd llm-rag/RAG/khronos_3dsg_rag
python src/export_canonical_graph.py data/dsg.json -o outputs/
```

### Build the Vector Database

```bash
cd llm-rag/RAG/khronos_3dsg_rag
python src/main.py build --all
```

### Query

```bash
python src/main.py query --db outputs/vector_db -q "chair near table" --top-k 5
```

### Python API

```python
from scene_graph_api import SceneGraphRAG
from api_schemas import EntityReference

rag = SceneGraphRAG("outputs/vector_db")

# Simple query
result = rag.resolve_entity(EntityReference(text="chairs", label="chairs", node_type="object"))
print(f"Status: {result.status}, Candidates: {len(result.candidates)}")

# Spatial query
result = rag.resolve_entity(EntityReference(
    text="nearest place", node_type="place", position=[0, 20, 2], quantifier="nearest"
))
print(f"Nearest: {result.selected.node_id}")

# Set query
result = rag.resolve_entity(EntityReference(
    text="chairs", label="chairs", node_type="object", quantifier="all"
))
print(f"All chairs: {len(result.selected_many)}")
```

---

## Data Flow

```
Khronos SLAM
    ↓
dsg.json (SPARK-DSG format, 1858 nodes, 6205 edges)
    ↓ parse_3dsg.py (with position fix)
SceneGraph (nodes + edges + valid positions)
    ↓ export_canonical_graph.py
canonical_graph_v2.json (100% hierarchy validity)
    ↓ build_documents.py
RAG Documents (entity/context/region)
    ↓ build_vector_db.py
ChromaDB Vector Database
    ↓
SceneGraphRAG.resolve_entity()
    ├── Exact ID fast path
    ├── Nearest-by-position fast path (canonical index)
    ├── All-by-label fast path (canonical index)
    └── Standard semantic path (EntityResolver + hard filters)
    ↓
ResolutionResult (status + candidates + selected)
```

---

## Dependencies

- `chromadb` — vector database
- `sentence-transformers` — embedding model
- `pyyaml` — configuration parsing

---

## Tests

```bash
cd llm-rag/RAG/khronos_3dsg_rag
python -m pytest src/ -v
```

---

## Evaluation Results

### Oracle-Intent Grounding (v3 benchmark, 100 cases)

| Metric | Value |
|--------------|-----------|
| Pipeline status accuracy | 100% |
| Grounding exact success | 100/100 |
| Set P/R/F1 | 1.0 / 1.0 / 1.0 |
| Spatial target-ID accuracy | 10/10 |
| Spatial distance error | 0.0m |
| Case failure count | 0 |
| Avg latency | 271ms |

### Category Breakdown

| Category | Accuracy | Count |
|----------------|------------------|-------------|
| ambiguous | 100% | 5/5 |
| obj_nav | 100% | 10/10 |
| place_constraint | 100% | 15/15 |
| room_constraint | 100% | 10/10 |
| set_query | 100% | 10/10 |
| not_found | 100% | 10/10 |
| spatial | 100% | 10/10 |
| exact_id | 100% | 5/5 |
| complex | 100% | 10/10 |
| relation | 100% | 15/15 |

---

## Version History

### v18 (2026-07-23) — RAG Grounding Module Complete

- `LocationConstraint` supports simultaneous Place + Room constraints.
- `RelationConstraint.target_node_id` supports full node IDs.
- Add the `count_in_location` aggregate operation.
- transitive contains/inside（BFS descendants）
- Unify the constraint pipeline across nearest/all/standard paths.
- Benchmark v3: byte-identical reproducibility and manifest-hash integrity.
- Evaluation：grounding_exact_success, Set P/R/F1, Spatial ID/distance
- Require `target_ref` (no silent guessing) and support step-specific references.

### v17 (2026-07-23) — Complex/Relation Schema Fixes

- LocationConstraint schema（Place + Room）
- RelationConstraint.target_node_id
- count_in_location aggregate
- Complex cases use the `locations` field.
- The `inside` relation uses the `relations` field.

### v16 (2026-07-23) — Hard Constraints + Spatial/Set Fixes

- node_type / label hard filter
- Nearest queries traverse the canonical index directly.
- All queries use the canonical index for exact set resolution.
- Spatial 0/10 → 10/10, Set P=0.616 → 1.0

### v15 (2026-07-17) — Canonical Graph v2 + Position Fixes

- `export_canonical_graph.py` exports directly from the raw DSG.
- Hierarchy validity: 26.4% → 100%
- Fix the position parser (Place/Room positions are no longer all `[0,0,0]`).
- Preserve benchmark-manifest hash integrity.
- Include category-level validator passes in the overall result.

### v14 (2026-07-16) — Structured-Pipeline Improvements

- Add the PlanDraft schema (the LLM outputs `target_ref`).
- Compile `target_ref` → `target_id` deterministically.
- Role-specific validator
- Add a 100-case Grounding benchmark.

---

## Paper-Ready Claims

### Claims That Can Be Made

> Given oracle structured intents, the v18 scene-graph Grounding backend achieved 100% pipeline-status accuracy, 100% resolved target-ID accuracy, 100% set exact match, and 100% spatial target-ID accuracy across all 100 development benchmark cases. The canonical graph exporter achieved 100% hierarchy validity with 100% Object-to-Place and 98.9% Place-to-Room coverage.

### Claims That Must Not Be Made

- End-to-end LLM+RAG accuracy = 100% (not tested with a real LLM).
- Natural-language understanding accuracy = 100% (only oracle-intent mode was tested).
- Cross-dataset generalization is proven (no cross-dataset test was run).
