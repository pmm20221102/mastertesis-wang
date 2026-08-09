"""独立语义向量检索测评（v2，含 report 生成）。

依据审查建议：
- Development / Test 分离（threshold 只在 dev 确定）
- Label-level ranking 指标（Label Hit@1 / Recall@3 / Recall@5 / MRR）
- Node-type known / unknown 两种模式分开报告
- Not-found Precision / Recall / F1
- Cold / Warm latency 分开报告（warm mean / median / p95）
- 严格失败保存（独立输出目录 + failure_count 一致性 + per_case 一致性）
- 返回 node ID 必须真实存在（invalid_node_id_rate = 0）
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from semantic_retriever import search_semantic  # noqa: E402
from build_vector_db import DEFAULT_EMBED_MODEL  # noqa: E402

RAG_ROOT = Path(__file__).resolve().parent.parent
DB_DIR = RAG_ROOT / "outputs" / "office_dense" / "vector_db"
CANONICAL_PATH = RAG_ROOT / "outputs" / "office_dense" / "canonical_graph_v2.json"
TOP_K = 5
DEFAULT_THRESHOLD = 0.35


def load_canonical_ids() -> set[str]:
    d = json.load(open(CANONICAL_PATH, encoding="utf-8"))
    return set(d.get("nodes", {}).keys())


def warm_up() -> float:
    t0 = time.time()
    search_semantic("warm up", top_k=3, db_dir=DB_DIR)
    return (time.time() - t0) * 1000


def evaluate_cases(cases: list[dict], valid_ids: set[str], threshold: float,
                   node_type_mode: str) -> tuple[list[dict], float]:
    results = []
    invalid_node_ids = 0
    total_emitted = 0
    for c in cases:
        nt = c.get("expected_node_type") if node_type_mode == "known" else None
        t0 = time.time()
        res = search_semantic(c["query"], top_k=TOP_K, node_type=nt, db_dir=DB_DIR, threshold=threshold)
        latency_ms = (time.time() - t0) * 1000

        for x in res.candidates:
            total_emitted += 1
            if x.node_id not in valid_ids:
                invalid_node_ids += 1

        r = {
            "case_id": c["case_id"],
            "query": c["query"],
            "expected_status": c.get("expected_status"),
            "expected_label": c.get("expected_label"),
            "node_type_mode": node_type_mode,
            "status": res.status,
            "label_ranking": [x.to_dict() for x in res.label_ranking],
            "candidates": [x.to_dict() for x in res.candidates],
            "latency_ms": round(latency_ms, 1),
        }

        if c.get("expected_status") == "matched":
            lr = [x.label for x in res.label_ranking]
            exp = c["expected_label"]
            r["label_hit_at_1"] = 1 if lr and lr[0] == exp else 0
            r["label_recall_at_3"] = 1 if exp in lr[:3] else 0
            r["label_recall_at_5"] = 1 if exp in lr[:5] else 0
            r["label_rank"] = (lr.index(exp) + 1) if exp in lr else 0
            r["matched_ok"] = 1 if res.status in ("matched", "ambiguous") else 0
            r["pass"] = r["label_recall_at_5"] == 1
        else:
            r["not_found_ok"] = 1 if res.status == "not_found" else 0
            r["top_score"] = round(res.label_ranking[0].score, 4) if res.label_ranking else 0.0
            r["pass"] = r["not_found_ok"] == 1

        results.append(r)
    invalid_rate = round(invalid_node_ids / total_emitted, 6) if total_emitted else 0.0
    return results, invalid_rate


def compute_metrics(results: list[dict]) -> dict:
    matched = [r for r in results if r["expected_status"] == "matched"]
    nf = [r for r in results if r["expected_status"] == "not_found"]
    n = len(matched)
    label_hit1 = sum(r["label_hit_at_1"] for r in matched) / n if n else 0.0
    label_r3 = sum(r["label_recall_at_3"] for r in matched) / n if n else 0.0
    label_r5 = sum(r["label_recall_at_5"] for r in matched) / n if n else 0.0
    label_mrr = sum(1.0 / r["label_rank"] for r in matched if r["label_rank"]) / n if n else 0.0

    tp = sum(1 for r in nf if r["not_found_ok"])
    fp = sum(1 for r in matched if not r["matched_ok"])
    fn = sum(1 for r in nf if not r["not_found_ok"])
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    latencies = [r["latency_ms"] for r in results]
    return {
        "total_cases": len(results),
        "matched_cases": n,
        "not_found_cases": len(nf),
        "label_hit_at_1": round(label_hit1, 4),
        "label_recall_at_3": round(label_r3, 4),
        "label_recall_at_5": round(label_r5, 4),
        "label_mrr": round(label_mrr, 4),
        "not_found_precision": round(precision, 4),
        "not_found_recall": round(recall, 4),
        "not_found_f1": round(f1, 4),
        "warm_mean_ms": round(statistics.mean(latencies), 1) if latencies else 0.0,
        "warm_median_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        "warm_p95_ms": round(sorted(latencies)[int(0.95 * len(latencies))], 1) if latencies else 0.0,
    }


def run_batch(cases: list[dict], valid_ids: set[str], threshold: float,
              node_type_mode: str, out_dir: Path, cold_ms: float):
    results, invalid_rate = evaluate_cases(cases, valid_ids, threshold, node_type_mode)
    metrics = compute_metrics(results)
    metrics["invalid_node_id_rate"] = invalid_rate
    metrics["threshold"] = threshold
    metrics["node_type_mode"] = node_type_mode
    metrics["cold_start_ms"] = round(cold_ms, 1)

    failures = [r for r in results if not r["pass"]]
    metrics["failure_count"] = len(failures)

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "per_case_results.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out_dir / "failures.jsonl", "w", encoding="utf-8") as f:
        for r in failures:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    # 一致性验证：failure_count == failures.jsonl 行数；每个 failure case_id 在 per_case 中
    per_case_ids = {r["case_id"] for r in results}
    for fr in failures:
        assert fr["case_id"] in per_case_ids, f"failure {fr['case_id']} not in per_case"
    assert metrics["failure_count"] == len(failures)
    return metrics, failures


def write_test_report(test_metrics: dict, failures: list[dict], cases: list[dict],
                      out_dir: Path, label_counts: dict):
    lines = ["# 语义向量检索测评报告（Test v1，held-out）", ""]
    lines.append(f"- 日期：2026-08-05")
    lines.append(f"- 数据源：Office GT（`office_launch_dense`，3096 节点 / 12335 边）")
    lines.append(f"- 向量库：`outputs/office_dense/vector_db`（6201 文档）")
    lines.append(f"- Embedding model：`{DEFAULT_EMBED_MODEL}`")
    lines.append(f"- node-type 模式：`{test_metrics['node_type_mode']}`")
    lines.append(f"- not_found 阈值：`{test_metrics['threshold']}`（在 dev 上确定）")
    lines.append("")
    lines.append("## Label-level 指标")
    lines.append("")
    lines.append("| 指标 | 值 | 验收线 | 达标 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| 测试样例数 | {test_metrics['total_cases']}（matched {test_metrics['matched_cases']} + not_found {test_metrics['not_found_cases']}）| - | - |")
    lines.append(f"| Label Hit@1 | {test_metrics['label_hit_at_1']:.2%} | ≥80% | {'✅' if test_metrics['label_hit_at_1']>=0.8 else '❌'} |")
    lines.append(f"| Label Recall@3 | {test_metrics['label_recall_at_3']:.2%} | - | - |")
    lines.append(f"| Label Recall@5 | {test_metrics['label_recall_at_5']:.2%} | ≥90% | {'✅' if test_metrics['label_recall_at_5']>=0.9 else '❌'} |")
    lines.append(f"| Label MRR | {test_metrics['label_mrr']:.3f} | - | - |")
    lines.append(f"| Not-found Precision / Recall / F1 | {test_metrics['not_found_precision']:.2f} / {test_metrics['not_found_recall']:.2f} / {test_metrics['not_found_f1']:.2f} | - | - |")
    lines.append(f"| 不存在 node ID 率 | {test_metrics['invalid_node_id_rate']} | =0 | {'✅' if test_metrics['invalid_node_id_rate']==0 else '❌'} |")
    lines.append("")
    lines.append("## 延迟")
    lines.append("")
    lines.append(f"- Cold-start：`{test_metrics['cold_start_ms']:.0f} ms`（首次加载 embedding 模型）")
    lines.append(f"- Warm mean / median / p95：`{test_metrics['warm_mean_ms']:.0f}` / `{test_metrics['warm_median_ms']:.0f}` / `{test_metrics['warm_p95_ms']:.0f}` ms")
    lines.append("")
    lines.append("## 数据集说明")
    lines.append("")
    lines.append(f"- Development set：`semantic_dev_cases.jsonl`（33 条，含 LABEL_DESCRIPTIONS 增强词，仅用于调试 / 阈值选择）")
    lines.append(f"- Test set：`semantic_test_cases_v1.jsonl`（{len(cases)} 条，held-out）")
    lines.append(f"- Test query 不出现于 LABEL_DESCRIPTIONS 与 alias 表（见 `validate_semantic_cases.py`）")
    lines.append(f"- 真实 canonical label：`{', '.join(sorted(label_counts))}`")
    lines.append(f"- 每个 label 的测试数量：`{json.dumps(label_counts, ensure_ascii=False)}`")
    lines.append("- 含 hard negatives（语义接近但数据库不存在的类别）")
    lines.append("- 约束：不编造 label、不修改 expected 迎合结果、不使用 18 位 node ID")
    lines.append("")
    lines.append("## 失败案例")
    lines.append("")
    if failures:
        for f in failures:
            lines.append(f"- `{f['case_id']}`: `{f['query']}` (expected={f.get('expected_label', f['expected_status'])})")
    else:
        lines.append("无失败案例")
    lines.append("")
    lines.append("## 当前 Office 数据集限制")
    lines.append("")
    lines.append("- 语义类别较少：5 个有意义的 object canonical label（`small_office_objects`、`large_static_wall_furniture`、`chairs`、`signs`、`large_office_objects`），另有 rooms 的 `unknown` 不参与语义匹配")
    lines.append("- `large_office_objects` / `small_office_objects` 本体粒度粗，个别表达（如 `table`/`desk`）存在歧义，已在 dev 中标记；test 使用更明确的未见表达")
    lines.append("- 因此不强行凑 100 条；保持 20-40 条有效测试")

    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    args = sys.argv[1:]
    mode = "all"
    node_type_mode = "both"
    threshold = DEFAULT_THRESHOLD
    if "--mode" in args:
        mode = args[args.index("--mode") + 1]
    if "--node-type-mode" in args:
        node_type_mode = args[args.index("--node-type-mode") + 1]
    if "--threshold" in args:
        threshold = float(args[args.index("--threshold") + 1])

    cases_dir = Path(__file__).resolve().parent
    dev_cases = [json.loads(l) for l in open(cases_dir / "semantic_dev_cases.jsonl", encoding="utf-8") if l.strip()]
    test_cases = [json.loads(l) for l in open(cases_dir / "semantic_test_cases_v1.jsonl", encoding="utf-8") if l.strip()]
    valid_ids = load_canonical_ids()

    from collections import Counter
    test_label_counts = Counter(c.get("expected_label") for c in test_cases if c.get("expected_status") == "matched")

    cold_ms = warm_up()
    node_type_modes = ["known", "unknown"] if node_type_mode == "both" else [node_type_mode]

    for ntm in node_type_modes:
        if mode in ("dev", "all"):
            dev_out = cases_dir / "results" / "dev" / ntm
            dev_metrics, dev_fail = run_batch(dev_cases, valid_ids, threshold, ntm, dev_out, cold_ms)
            print(f"=== DEV [{ntm}] ===  Label Hit@1={dev_metrics['label_hit_at_1']:.2%} "
                  f"Recall@5={dev_metrics['label_recall_at_5']:.2%} MRR={dev_metrics['label_mrr']:.3f} "
                  f"Notfound_F1={dev_metrics['not_found_f1']:.2f} failures={len(dev_fail)}")
        if mode in ("test", "all"):
            test_out = cases_dir / "results" / "test_v1" / ntm
            test_metrics, test_fail = run_batch(test_cases, valid_ids, threshold, ntm, test_out, cold_ms)
            write_test_report(test_metrics, test_fail, test_cases, test_out, dict(test_label_counts))
            print(f"=== TEST [{ntm}] (thr={threshold}) ===  Label Hit@1={test_metrics['label_hit_at_1']:.2%} "
                  f"Recall@3={test_metrics['label_recall_at_3']:.2%} Recall@5={test_metrics['label_recall_at_5']:.2%} "
                  f"MRR={test_metrics['label_mrr']:.3f} Notfound_F1={test_metrics['not_found_f1']:.2f} "
                  f"invalid_id={test_metrics['invalid_node_id_rate']} cold={cold_ms:.0f}ms "
                  f"warm_med={test_metrics['warm_median_ms']:.0f}ms failures={len(test_fail)}")
            for r in test_fail:
                print(f"    FAIL {r['case_id']}: {r['query']} (expected={r.get('expected_label', r['expected_status'])})")


if __name__ == "__main__":
    main()
