# LLM-robot-taskplanning-chaining-rag Test Report

## Date: 2026-07-15

## Environment

- **OS**: Ubuntu 24.04 (WSL2)
- **Python**: 3.10+
- **Ollama**: not available (mock tests only)
- **ChromaDB**: not available (mock tests only)

## Test Classification

| Category | Description | Count |
|----------|-------------|-------|
| Unit/Contract tests | Static, import, and mock tests (no external deps) | 21 |
| Integration tests | Requires Ollama + ChromaDB (not yet run) | 0 |
| Full evaluation | End-to-end Ollama + RAG + LLM (not yet run) | 0 |

**Important**: The 21 tests below verify code structure and deterministic behavior. They do NOT prove:
- LLM intent parsing accuracy
- RAG grounding accuracy
- Task planning correctness
- End-to-end system effectiveness

## Test Results Summary

### 1. Python Compilation Check

```bash
python -m compileall . -q
```

**Result: PASSED**

### 2. Unit/Contract Tests

```bash
pytest -q
```

**Result: 19 passed, 2 skipped** (skipped tests require `ollama` package)

| Test Suite | Passed | Description |
|-----------|--------|-------------|
| Import Tests | 5 | Module imports compile correctly |
| Canonical Index Tests | 5 | Parent/child, bidirectional, ancestor, lookup, cache |
| Navigation Target Tests | 4 | Schema, object→place, room→room, not-found |
| Pipeline Contract Tests | 7 | GroundingResult, IntentSchema, ResolutionResult |

### 3. CLI Verification

```bash
python main_rag.py --help
```

Output confirms structured/legacy/baseline modes available.

## Evaluation Cases (not yet executed)

| Type | Count | Coverage |
|------|-------|----------|
| Grounding cases | 12 | exact_id, unique_label, duplicate_label, not_found, room_relation, alias, plural, person, room_number, neighbor, multi_ref, category |
| Planning cases | 8 | navigate, navigate+avoid, locate, escort, ambiguous, not_found, unsupported, multi_step |

## Known Limitations

1. **Full evaluation requires Ollama**: Intent parsing and planning need LLM
2. **Full evaluation requires ChromaDB**: Vector database with scene graph data
3. **Evaluation ground truth needs review**: Some cases may have incorrect expected values
4. **Oracle modes not fully implemented**: oracle_intent/oracle_grounding need case data

## What This Report Proves

- All Python files compile without syntax errors
- Schema definitions are correct and instantiable
- GroundingResult status priority works correctly
- Canonical Index parent/child relationships are correctly oriented
- NavigationTarget correctly returns parent place for objects
- No arbitrary ID fallback in the new pipeline

## What This Report Does NOT Prove

- Ollama Intent Parser accuracy
- RAG grounding accuracy
- LLM Task Planning correctness
- Hallucinated ID rate
- End-to-end success rate
- Structured pipeline superiority over legacy
