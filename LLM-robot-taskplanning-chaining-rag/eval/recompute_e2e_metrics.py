"""三审 5/6：离线重算 role value 匹配与 full-instruction fidelity。不重跑 LLM。

从已保存的 per_case_results.jsonl（含 predicted_intent_summary / e2e_success）+ benchmark
（oracle_intent / category）重算：
- role_value_ok（source/destination room 值匹配，三审 5.1）
- failure_stage 修正（source/destination 值错 → intent_role_value，替代 grounding_id 误归因）
- full_instruction_fidelity_success + fidelity_failure_reason（三审 6.1）
更新 per_case 与 summary（failure_by_stage / full_instruction_fidelity）。
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

LLM_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = Path(__file__).resolve().parent / "cases"
LIMIT_CATS = {"navigate_with_avoid", "navigate_with_prefer", "escort"}


def norm_room(t):
    if not t:
        return None
    m = re.search(r"(\d{3,})", str(t))
    return m.group(1) if m else None


def recompute(cases_name: str, results_path: Path):
    cases = [json.loads(l) for l in open(CASES_DIR / cases_name, encoding="utf-8") if l.strip()]
    cat_map = {c["case_id"]: c["category"] for c in cases}
    oracle = {c["case_id"]: c.get("oracle_intent", {}) for c in cases}

    runs = [json.loads(l) for l in open(results_path, encoding="utf-8") if l.strip()]
    n = len(runs)
    for d in runs:
        cid = d["case_id"]
        oi = oracle.get(cid, {})
        psum = d.get("predicted_intent_summary") or {}

        # role value 匹配（三审 5.1）
        rv_ok = True
        for role in ("source", "destination"):
            o = oi.get(role)
            if o:
                ov = norm_room(o.get("text"))
                pv = norm_room(psum.get(role))
                if ov and ov != pv:
                    rv_ok = False
        d["role_value_ok"] = rv_ok
        if d.get("failure_stage") == "grounding_id" and not rv_ok:
            d["failure_stage"] = "intent_role_value"

        # full-instruction fidelity（三审 6.1）
        cat = cat_map.get(cid, "")
        if cat in LIMIT_CATS:
            d["full_instruction_fidelity_success"] = False
            d["fidelity_failure_reason"] = "constraint_not_propagated" if "constraint" in cat else "actor_not_grounded"
        else:
            d["full_instruction_fidelity_success"] = bool(d.get("e2e_success"))
            d.pop("fidelity_failure_reason", None)

    # 写回 per_case
    with open(results_path, "w", encoding="utf-8") as f:
        for d in runs:
            f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")

    # 更新 summary（e2e 各 stage 不变，只改 failure_by_stage / full_instruction_fidelity / fidelity reason 计数）
    sp = results_path.parent / "summary.json"
    summary = json.load(open(sp, encoding="utf-8"))
    fail_stage = Counter(d.get("failure_stage") for d in runs if d.get("failure_stage"))
    summary["failure_by_stage"] = dict(fail_stage)
    summary["full_instruction_fidelity"] = round(
        sum(1 for d in runs if d.get("full_instruction_fidelity_success")) / n, 4) if n else None
    fid_reasons = Counter(d.get("fidelity_failure_reason") for d in runs if d.get("fidelity_failure_reason"))
    summary["fidelity_failure_reasons"] = dict(fid_reasons)
    json.dump(summary, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"{results_path.parent.name}/{results_path.name}: role_value_ok={sum(1 for d in runs if d.get('role_value_ok'))}/{n} "
          f"fidelity={summary['full_instruction_fidelity']} fail={dict(fail_stage)}")


if __name__ == "__main__":
    for spec in sys.argv[1:]:
        cases_name, res = spec.split(":")
        recompute(cases_name, Path(res))
