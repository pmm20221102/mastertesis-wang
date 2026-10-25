# Benchmark Validation Report

## Validation Results

| Check | Status |
|-------|--------|
| Case count = 100 | PASS |
| Unique case_ids | PASS |
| No duplicate queries | PASS |
| Categories present | PASS |

## Overall: PASS

## Category Distribution

| Category | Count |
|----------|-------|
| ambiguous | 15 |
| room_constraint | 25 |
| set_query | 10 |
| not_found | 10 |
| spatial | 10 |
| exact_id | 5 |
| obj_nav | 15 |
| complex | 10 |

## Notes

- All 100 cases have unique case_ids
- All 100 cases have unique queries
- Room constraint cases use place IDs (not room IDs) due to data structure
- Set query cases have complete expected node ID sets
