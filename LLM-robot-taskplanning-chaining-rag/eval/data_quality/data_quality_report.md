# Data Quality Report

## Dataset: khronos_office

### Node Statistics

| Type | Count |
|------|-------|
| Total nodes | 1858 |
| Objects (layer 2) | 585 |
| Places (layer 3) | 1262 |
| Rooms (layer 4) | 10 |
| Buildings (layer 5) | 1 |

### Edge Statistics

| Type | Count |
|------|-------|
| Total edges | 6205 |
| Parent edges | 282 |
| Child edges | 1833 |
| Neighbor edges | 1666 |

### Hierarchy Coverage

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| Object with parent | 0% | ≥95% | FAIL |
| Place with parent | 21.6% | ≥95% | FAIL |
| Unknown Room labels | 10 | 0 | FAIL |

### Notes

- All 10 rooms have unknown labels (semantic_label=0)
- Object hierarchy coverage is 0% because canonical graph only has same-layer neighbor edges
- Place hierarchy coverage is 21.6% because most places don't have explicit parent edges

### Recommendations

1. Room labels need to be added from official dataset or scene configuration
2. Object → Place → Room hierarchy needs to be explicitly established
3. Canonical graph should include hierarchy edges from raw DSG
