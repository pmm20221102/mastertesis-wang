"""校验语义检索测试用例（dev / test）。

校验点：
- query 非空
- matched 用例的 expected_label 必须存在于向量库真实 label 列表
- matched 用例的 expected_node_type 合法
- not_found 用例不得带 expected_label
- expected_status 只能为 matched / not_found
- （test）query 不出现于 LABEL_DESCRIPTIONS 与 alias 表（审查 P0 要求）
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from build_vector_db import load_vector_db  # noqa: E402

VALID_TYPES = {"object", "place", "room", "building"}
DB_DIR = Path(__file__).resolve().parent.parent / "outputs" / "office_dense" / "vector_db"

# 与 LABEL_DESCRIPTIONS / alias 重合的明确同义词（test 不应使用）
RESERVED_TERMS = {
    # LABEL_DESCRIPTIONS
    "chair", "seat", "seating", "sitting", "sign", "notice", "marker", "signage",
    "billboard", "table", "desk", "workstation", "wall", "mounted", "furnishing",
    "cabinet", "shelf", "shelving", "unit", "equipment", "machine", "stationery",
    "accessory", "supply", "item", "furniture",
    # alias（config.yaml）
    "door", "entrance", "gate", "corridor", "hallway", "passage", "room", "area",
    "space", "person", "human", "agent", "robot", "mobile", "base", "partition",
    "floor", "ground", "level",
}

STOPWORDS = {"a", "an", "the", "in", "on", "for", "to", "of", "at", "and", "this", "that"}


def get_real_labels(db_dir) -> set[str]:
    col = load_vector_db(str(db_dir))
    labels: set[str] = set()
    for meta in col.peek(limit=col.count())["metadatas"]:
        if meta.get("node_id") and meta.get("label"):
            labels.add(str(meta["label"]))
    return labels


def tokenize(q: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", q.lower()) if t not in STOPWORDS}


def validate(cases_path: str, is_test: bool):
    cases = [json.loads(l) for l in open(cases_path, encoding="utf-8") if l.strip()]
    errors: list[str] = []
    real_labels = get_real_labels(DB_DIR)
    label_counts: Counter = Counter()

    for c in cases:
        cid = c.get("case_id", "?")
        q = c.get("query", "")
        status = c.get("expected_status")

        if not q.strip():
            errors.append(f"{cid}: query 为空")
        if status == "matched":
            exp = c.get("expected_label")
            if not exp:
                errors.append(f"{cid}: matched 但缺 expected_label")
            elif exp not in real_labels:
                errors.append(f"{cid}: expected_label '{exp}' 不在真实 label 列表 {sorted(real_labels)}")
            else:
                label_counts[exp] += 1
            nt = c.get("expected_node_type")
            if nt and nt not in VALID_TYPES:
                errors.append(f"{cid}: 非法 expected_node_type '{nt}'")
        elif status == "not_found":
            if c.get("expected_label"):
                errors.append(f"{cid}: not_found 不应带 expected_label")
        else:
            errors.append(f"{cid}: 非法 expected_status '{status}'")

        # test 集：query 不得使用 RESERVED 明确同义词
        if is_test:
            overlap = tokenize(q) & RESERVED_TERMS
            if overlap:
                errors.append(f"{cid}: test query 使用保留词 {sorted(overlap)}（LABEL_DESCRIPTIONS/alias）")

    return errors, len(cases), sorted(real_labels), dict(label_counts)


def main():
    cases_path = sys.argv[1] if len(sys.argv) > 1 else "semantic_dev_cases.jsonl"
    is_test = "test" in Path(cases_path).name
    errors, total, real_labels, label_counts = validate(cases_path, is_test)
    print(f"文件: {Path(cases_path).name} | 总用例: {total} | 真实 label ({len(real_labels)}): {real_labels}")
    print(f"每个 label 的测试数量: {label_counts}")
    if is_test:
        print("test 集重合检查: 已启用")
    if errors:
        print("校验失败:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("VALIDATION PASSED")


if __name__ == "__main__":
    main()
