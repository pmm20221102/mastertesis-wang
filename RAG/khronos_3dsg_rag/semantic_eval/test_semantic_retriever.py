"""semantic_retriever 行为测试（适配 v3 SemanticSearchResult 接口）。

覆盖：
- collection.query() 确实被调用（search_semantic 内部真实调用）
- top_k 参数生效
- node_type 过滤生效
- 返回 node ID 真实存在
- seat 能召回 chairs
- canonical label 原词能正确召回
- 未知语义标记 not_found（status / threshold）
- 失败案例写入 failures.jsonl（run_semantic_eval 生成）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from semantic_retriever import search_semantic  # noqa: E402

RAG_ROOT = Path(__file__).resolve().parent.parent
DB = RAG_ROOT / "outputs" / "office_dense" / "vector_db"
CANON = RAG_ROOT / "outputs" / "office_dense" / "canonical_graph_v2.json"
THRESHOLD = 0.35


def load_canonical_ids() -> set[str]:
    d = json.load(open(CANON, encoding="utf-8"))
    return set(d["nodes"].keys())


def run_tests() -> int:
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print(f"  [PASS] {name}")
        else:
            failures.append(name)
            print(f"  [FAIL] {name} {detail}")

    # 1. collection.query() 被真实调用（返回非空即执行）
    r = search_semantic("chair", top_k=5, db_dir=DB)
    check("collection.query() 调用返回结果", len(r.candidates) > 0, f"got {len(r.candidates)}")

    # 2. top_k 生效
    r3 = search_semantic("chair", top_k=3, db_dir=DB)
    check("top_k=3 生效", len(r3.candidates) == 3, f"got {len(r3.candidates)}")

    # 3. node_type 过滤生效
    r = search_semantic("chair", top_k=5, node_type="object", db_dir=DB)
    check("node_type=object 过滤", len(r.candidates) > 0 and all(c.node_type == "objects" for c in r.candidates),
          f"types={[c.node_type for c in r.candidates]}")

    # 4. 返回 node ID 真实存在
    valid = load_canonical_ids()
    all_ids = []
    for q in ["chair", "sign", "table", "large static wall furniture"]:
        all_ids += [c.node_id for c in search_semantic(q, top_k=5, db_dir=DB).candidates]
    invalid = [n for n in all_ids if n not in valid]
    check("node ID 全部真实存在", len(invalid) == 0, f"invalid={invalid[:3]}")

    # 5. seat 能召回 chairs（label-level）
    r = search_semantic("seat", top_k=5, db_dir=DB)
    labels = [x.label for x in r.label_ranking]
    check("seat 召回 chairs", "chairs" in labels, f"labels={labels}")

    # 6. canonical label 原词能正确召回
    r = search_semantic("large static wall furniture", top_k=5, db_dir=DB)
    labels = [x.label for x in r.label_ranking]
    check("canonical 原词召回", "large_static_wall_furniture" in labels, f"labels={labels}")

    # 7. 未知语义（not_found）：elevator 应被判 not_found
    r = search_semantic("elevator", top_k=5, db_dir=DB, threshold=THRESHOLD)
    check("未知语义标记 not_found", r.status == "not_found", f"status={r.status} top={r.label_ranking[0].score if r.label_ranking else 0}")

    # 8. 失败案例写入 failures.jsonl（run_semantic_eval 生成，检查存在性）
    fails_file = Path(__file__).resolve().parent / "results" / "test_v1" / "known" / "failures.jsonl"
    check("failures.jsonl 已生成", fails_file.exists())

    if failures:
        print(f"\n{len(failures)} 项失败: {failures}")
        return 1
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(run_tests())
