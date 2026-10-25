#!/usr/bin/env python3
"""Validate grounding benchmark."""

import json
import sys
from pathlib import Path

def validate_benchmark(benchmark_path: str) -> dict:
    """Validate benchmark file."""
    with open(benchmark_path, 'r') as f:
        cases = [json.loads(line) for line in f]
    
    results = {
        'case_count': {'value': len(cases), 'expected': 100, 'pass': len(cases) == 100},
        'unique_case_ids': {'pass': True},
        'no_duplicate_queries': {'pass': True},
        'categories': {'pass': True},
    }
    
    # Check unique case_ids
    case_ids = [c['case_id'] for c in cases]
    unique_ids = len(set(case_ids))
    results['unique_case_ids'] = {'value': unique_ids, 'pass': unique_ids == len(case_ids)}
    
    # Check no duplicate queries
    queries = [c['instruction'] for c in cases]
    query_counts = {}
    for q in queries:
        query_counts[q] = query_counts.get(q, 0) + 1
    duplicates = {q: c for q, c in query_counts.items() if c > 1}
    results['no_duplicate_queries'] = {'value': len(duplicates), 'pass': len(duplicates) == 0}
    
    # Check categories
    category_counts = {}
    for case in cases:
        for tag in case.get('tags', []):
            category_counts[tag] = category_counts.get(tag, 0) + 1
    results['categories'] = {'counts': category_counts, 'pass': len(category_counts) > 0}
    
    # Overall
    results['overall'] = all(r['pass'] for r in results.values() if isinstance(r, dict) and 'pass' in r)
    
    return results


if __name__ == "__main__":
    benchmark_path = sys.argv[1] if len(sys.argv) > 1 else "eval/cases/grounding_benchmark_100_v1.jsonl"
    results = validate_benchmark(benchmark_path)
    print(json.dumps(results, indent=2))
