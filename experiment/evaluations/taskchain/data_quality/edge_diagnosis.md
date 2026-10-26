# Edge Diagnosis Report

## Data Sources

| Data source | Nodes | Edges |
|--------|--------|------|
| Raw DSG (`dsg.json`) | 402 | 288 |
| RAG documents (`rag_documents.jsonl`) | 1,332 | 4,675 |
| Canonical graph (`canonical_graph.json`) | 1,332 | 0 |

## Diagnostic Findings

### 1. Does the Raw DSG Contain Edges?

**Yes.** The raw DSG contains 288 edges, all between nodes in Layer 2 (objects).

### 2. Where Were the Edges Lost?

**The edges were lost when the canonical graph was reconstructed from ChromaDB metadata.**

Causes:
- `build_documents.py` truncates `parent_ids[:5]`, `child_ids[:10]`, and `neighbor_ids[:10]`.
- `canonical_index.py` reconstructs the canonical graph from ChromaDB metadata.
- Edge data in ChromaDB metadata is truncated or lost.

### 3. The 4,675 Relations in the RAG Documents

These are `connected_to` relations between Places, not edges from the raw DSG.

The 288 raw-DSG edges are relations between objects.

### 4. Proposed Fix

**Export the canonical graph directly from the raw DSG**, without using ChromaDB metadata.

```text
parse raw DSG (dsg.json)
    ├── canonical_graph.json (complete graph, no truncation)
    ├── rag_documents.jsonl (for embeddings; may be truncated)
    └── vector_db
```

### 5. Raw DSG Edge Structure

```json
{
  "source": 6989586621679009792,
  "target": 6989586621679009793,
  "info": {
    "type": "EdgeAttributes",
    "weight": 1.0
  }
}
```

All edges are between objects in Layer 2; there are no hierarchy edges.

### 6. Data Limitations

The raw DSG contains only Layer 2 (objects), with no:
- Room nodes
- Place nodes  
- Building nodes
- Hierarchy edges (Room → Place → Object)

This means the **Room/Place/Object hierarchy does not exist in the current dataset**.

Therefore:
- Room-constrained queries cannot be tested.
- Place topology cannot be tested.
- Only object-level queries are supported.

### 7. Recommendation

Create a constrained benchmark that tests only capabilities supported by the available data:
- Object label ambiguity
- Set query
- Exact ID sanity
- Not-found
- Object navigation

**Do not test Room-constrained Grounding when the data does not support it.**
