"""生成 supported-scope 36 benchmark（二次审查 6.2/16）。

从 e2e_test_50_v1.jsonl 筛选当前已完整实现范围的类别（排除 avoid/prefer/escort），
生成独立的 e2e_supported_scope_36_v1.jsonl + 冻结 manifest。
保持 held-out（Test 模板），case_id 重编号 e2e_ss_xxx。
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC = BASE / "cases" / "e2e_test_50_v1.jsonl"
OUT = BASE / "cases" / "e2e_supported_scope_36_v1.jsonl"
KEEP = {"single_navigate", "room_constrained_navigate", "ordered_source_destination",
        "ambiguous_gate", "not_found_gate", "unsupported_gate"}


def main():
    cases = [json.loads(l) for l in open(SRC, encoding="utf-8") if l.strip()]
    kept = [c for c in cases if c["category"] in KEEP]
    for i, c in enumerate(kept, 1):
        c["case_id"] = f"e2e_ss_{i:03d}"
    with open(OUT, "w", encoding="utf-8") as f:
        for c in kept:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    manifest = {
        "benchmark_status": "ready", "benchmark_file": OUT.name,
        "benchmark_file_sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
        "case_count": len(kept), "categories": dict(Counter(c["category"] for c in kept)),
        "source": SRC.name, "seed": 42,
    }
    with open(OUT.with_suffix(".manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"{OUT.name}: {len(kept)} 条, {manifest['categories']}")


if __name__ == "__main__":
    sys.exit(main())
