"""Planner v3 Simple 评估 runner（Planner阶段全面审查 第 12/13/19 节）。

流程：Oracle Grounding → SimplePlanDraft（LLM 只输出 action+ref）→ Deterministic
Compiler → Validator。最多 1 次 repair（只提供 allowed actions + available refs + errors）。

指标分三层：
- LLM Symbolic：Schema Validity / Status / Exact Action Sequence / Ordered Coverage /
  Role Ref / Strict Symbolic Success / First-pass / Post-repair / Repair Rate
- Compiler：确定性（单元测试覆盖，不在 LLM 指标）
- Final：Compiled Validation Pass / Compiled Exact Match / ID Safety

保存完整 provenance：first_pass_raw/parsed/errors、repair_raw、final_symbolic/compiled、attempt_count。

用法：
  python -m eval.run_planner_simple_eval --model mistral:latest --repeats 3 --output eval/results/planner_v3_simple/mistral_latest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from collections import Counter
from pathlib import Path

LLM_ROOT = Path(__file__).resolve().parents[3] / "llm-rag" / "planner"
RAG_ROOT = LLM_ROOT.parent / "RAG" / "khronos_3dsg_rag"
sys.path.insert(0, str(LLM_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from schema import SimplePlanDraft, SimplePlanStep, AllowedStatus, ACTION_EXECUTOR_MAP  # noqa: E402
from compile_simple_plan import compile_simple_plan  # noqa: E402
from main_rag import get_ollama_host, _validate_grounded_plan  # noqa: E402
from grounding.orchestrator import GroundingResult  # noqa: E402
from api_schemas import ResolutionResult, ResolutionCandidate  # noqa: E402
from planner_schemas.intent import EntityReference  # noqa: E402

SCORER_VERSION = "simple_planner_metrics_v2"
METRIC_CONTRACT_VERSION = "simple_completeness_v3"
DEFAULT_MODEL = "mistral:latest"
CASES = Path(__file__).resolve().parent / "cases" / "planner_benchmark_50_v3_simple.jsonl"
OUT_BASE = Path(__file__).resolve().parent / "results" / "planner_v3_simple"
CONFIG_BASE = {"ollama_host": "http://127.0.0.1:11434", "options": {"temperature": 0, "num_ctx": 8192}}
VALID_ROLES = {"actor", "target", "source", "destination"}


def slug(m: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", m.lower()).strip("_")


def build_simple_prompt(instruction: str, grounded_task: dict, errors: list[str] | None = None) -> str:
    refs = sorted(grounded_task.get("references", {}).keys())
    head = "You are a robot task planner. Decompose the grounded task into a minimal high-level action sequence."
    if errors:
        head = ("The previous plan failed validation. Output a corrected plan that satisfies the errors. "
                "You are a robot task planner.")
    return f"""{head}

GROUNDED TASK:
{json.dumps(grounded_task, indent=2, ensure_ascii=False)}

AVAILABLE REFS: {refs if refs else "[] (no entities; e.g. return to base)"}
{'ERRORS:' + json.dumps(errors, ensure_ascii=False) if errors else ''}

RULES:
- Output ONLY "status" and "steps".
- Each step has "action" (one of: navigate_to, escort_to, wait, return_to_base) and "ref".
- "ref" MUST be one of the AVAILABLE REFS; set it to null only for return_to_base / wait.
- Do NOT output node IDs, executor, target_type, constraints, or step numbers — the system fills them in.
- Output ONLY the actions the instruction actually requires. Do NOT add return_to_base, wait, or any extra step unless the instruction explicitly asks for it.
- "First go to A, then go to B" -> two navigate_to steps with refs "source" then "destination".
- Escort -> one escort_to step with ref "destination" (the system binds the actor).
- Return to base -> one return_to_base step with ref null.

Output JSON only. Example:
{{"status": "valid", "steps": [{{"action": "navigate_to", "ref": "target"}}]}}
"""


def generate_simple_plan(instruction: str, grounded_task: dict, model: str, config: dict,
                         errors: list[str] | None = None) -> SimplePlanDraft:
    from ollama import Client
    client = Client(host=get_ollama_host(config), timeout=120)
    options = config.get("options", {"temperature": 0.1, "num_ctx": 4096, "num_predict": 2048})
    prompt = build_simple_prompt(instruction, grounded_task, errors)
    response = client.chat(
        model=model, think=False,
        messages=[{"role": "system", "content": "Output valid JSON only. No explanations."},
                  {"role": "user", "content": prompt}],
        format=SimplePlanDraft.model_json_schema(),
        options=options,
    )
    raw = response.message.content
    if not raw:
        raise ValueError("LLM returned empty response")
    return SimplePlanDraft.model_validate_json(raw)


def build_grounded_task(og: dict, instruction: str) -> dict:
    gt = {"action": og.get("action", "navigate"), "description": instruction, "references": {}, "constraints": []}
    for role in VALID_ROLES:
        ref = og.get(role)
        if ref and ref.get("node_id"):
            gt["references"][role] = {
                "node_id": str(ref["node_id"]), "node_type": ref.get("node_type", "unknown"),
                "label": ref.get("label", ""), "room_id": ref.get("room_id"),
                "place_id": ref.get("place_id"), "navigation_target": ref.get("navigation_target"),
            }
    for c in og.get("constraints", []):
        if c.get("type") == "avoid" and c.get("node_id"):
            gt["constraints"].append({"type": "avoid", "node_id": str(c["node_id"]),
                                      "node_type": c.get("node_type", "unknown"), "label": c.get("label", "")})
        elif c.get("type") == "prefer":
            gt["constraints"].append({"type": "prefer", "node_id": c.get("value") or c.get("label", ""),
                                      "label": c.get("label", ""), "value": c.get("value")})
    return gt


def build_mock_grounding(gt: dict) -> GroundingResult:
    g = GroundingResult()
    for role, ref in gt["references"].items():
        g.add_result(role, ResolutionResult(
            status="resolved", reference=EntityReference(text=ref.get("label", role)),
            selected=ResolutionCandidate(node_id=ref["node_id"], node_type=ref.get("node_type", "unknown"),
                                         label=ref.get("label", "")),
        ))
    for i, c in enumerate(gt["constraints"]):
        # prefer 的 node_id 是 route policy（非 graph node）；避免用 policy 值作为 id
        node_id = c.get("node_id") or f"prefer_{c.get('label', i)}"
        g.add_constraint_result(f"constraint_{c['type']}_{i}", ResolutionResult(
            status="resolved", reference=EntityReference(text=c.get("label", f"c{i}")),
            selected=ResolutionCandidate(node_id=node_id, node_type=c.get("node_type", "unknown"),
                                         label=c.get("label", "")),
        ))
    return g


def _sym_expect(case: dict) -> dict:
    return case.get("expected_symbolic_plan", {})


def evaluate_case(case: dict, model: str, config: dict) -> dict:
    instruction = case["instruction"]
    og = case["oracle_grounding"]
    t0 = time.time()
    base = {"case_id": case["case_id"], "category": case.get("category"), "instruction": instruction,
            "scorer_version": SCORER_VERSION, "metric_contract_version": METRIC_CONTRACT_VERSION}

    # status gate：不调用 Planner
    if "pipeline_status" in og:
        status = og["pipeline_status"]
        if status == "not_found":
            status = "target_not_found"
        expected = _sym_expect(case)
        status_correct = (status == expected.get("status"))
        base.update({
            "planner_invoked": False, "predicted_status": status, "expected_status": expected.get("status"),
            "status_gate_correct": status_correct, "error": None,
            "latency_ms": (time.time() - t0) * 1000, "attempt_count": 0,
            "final_symbolic_plan": {"status": status, "steps": []},
            "final_compiled_plan": None, "validation_errors": [], "repair_rate": None,
        })
        return base

    expected = _sym_expect(case)
    exp_status = expected.get("status", "valid")
    exp_steps = expected.get("steps", [])
    exp_actions = [s["action"] for s in exp_steps]
    exp_refs = [s.get("ref") for s in exp_steps]
    # Defaults remain scoreable when generation or compilation fails.
    base.update({
        "status_correct": False, "action_exact_sequence": False, "action_ordered": False,
        "role_ref_accuracy": 0.0 if exp_refs else None, "role_ref_total": len(exp_refs),
        "compiled_exact_match": False if case.get("expected_compiled_plan") is not None else None,
        "failure_stage": None,
    })

    gt = build_grounded_task(og, instruction)
    mock = build_mock_grounding(gt)

    first_pass_errors = []
    repair_errors = []
    draft = None
    attempt_count = 0
    repair_rate = None
    errors = []

    try:
        # first pass
        attempt_count += 1
        draft = generate_simple_plan(instruction, gt, model, config)
        first_pass_errors = []
    except Exception as e:
        first_pass_errors = [str(e)]
        # repair（最多 1 次）：只提供 allowed actions + refs + schema error
        try:
            attempt_count += 1
            draft = generate_simple_plan(instruction, gt, model, config, errors=first_pass_errors)
            repair_rate = 1
            repair_errors = []
        except Exception as e2:
            repair_errors = [str(e2)]
            repair_rate = 1
            base.update({
                "planner_invoked": True, "predicted_status": "system_error",
                "expected_status": exp_status, "status_gate_correct": False,
                "error": f"Schema failed: {first_pass_errors + repair_errors}",
                "schema_valid": False, "strict_symbolic_success": False,
                "failure_stage": "schema",
                "compiled_validation_pass": False,
                "attempt_count": attempt_count, "repair_rate": repair_rate,
                "first_pass_errors": first_pass_errors, "repair_errors": repair_errors,
                "final_symbolic_plan": None, "final_compiled_plan": None,
                "validation_errors": first_pass_errors + repair_errors,
                "latency_ms": (time.time() - t0) * 1000,
            })
            return base

    # ---- LLM symbolic metrics ----
    pred_actions = [s.action for s in draft.steps]
    pred_refs = [s.ref for s in draft.steps]
    status_correct = draft.status == exp_status
    action_exact = pred_actions == exp_actions

    def _ordered(sub, seq):
        it = iter(seq)
        return all(x in it for x in sub)
    action_ordered = _ordered(exp_actions, pred_actions) if exp_actions else True

    # ref 匹配：长度必须一致；ref=None 与 None 匹配（return_to_base 等无 ref 动作）
    if len(pred_refs) != len(exp_refs):
        ref_accuracy = 0.0
    elif not exp_refs:
        ref_accuracy = 1.0
    else:
        ref_accuracy = sum(1 for e, p in zip(exp_refs, pred_refs) if e == p) / len(exp_refs)
    ref_total = len(exp_refs)

    schema_valid = True
    strict_symbolic = bool(status_correct and action_exact and ref_accuracy == 1.0)
    base.update({
        "planner_invoked": True, "predicted_status": draft.status, "expected_status": exp_status,
        "status_correct": status_correct, "status_gate_correct": status_correct,
        "schema_valid": schema_valid, "action_exact_sequence": action_exact, "action_ordered": action_ordered,
        "predicted_actions": pred_actions, "expected_actions": exp_actions,
        "role_ref_accuracy": ref_accuracy, "role_ref_total": ref_total,
        "strict_symbolic_success": strict_symbolic,
        "first_pass_errors": first_pass_errors, "repair_errors": repair_errors,
        "attempt_count": attempt_count, "repair_rate": repair_rate,
        "final_symbolic_plan": json.loads(draft.model_dump_json()),
    })
    plan = None
    failure_stage = "compiler"
    try:
        plan = compile_simple_plan(draft, gt)
        failure_stage = "validator"
        errors = _validate_grounded_plan(plan, mock, grounded_task=gt)
    except Exception as exc:
        base.update({
            "compiled_validation_pass": False, "error": str(exc),
            "failure_stage": failure_stage, "validation_errors": [str(exc)],
            "final_compiled_plan": json.loads(plan.model_dump_json()) if plan else None,
            "latency_ms": (time.time() - t0) * 1000,
        })
        return base

    # ---- Final compiled metrics ----
    compiled_valid = (not errors and plan.status == "valid")
    exp_compiled = case.get("expected_compiled_plan")
    compiled_exact = False if exp_compiled is not None else None
    if exp_compiled is not None and compiled_valid:
        ec_steps = exp_compiled.get("steps", [])
        if len(ec_steps) == len(plan.task_chain):
            compiled_exact = all(
                s.get("action") == ts.action and
                s.get("target_id") == ts.target_id and
                s.get("executor") == ts.executor and
                set(s.get("constraints", {}).get("avoid", [])) == set(ts.constraints.avoid) and
                set(s.get("constraints", {}).get("prefer", [])) == set(ts.constraints.prefer)
                for s, ts in zip(ec_steps, plan.task_chain)
            )
        else:
            compiled_exact = False
    elif exp_compiled is None:
        compiled_exact = None

    emitted_ids = []
    for ts in plan.task_chain:
        if ts.target_id:
            emitted_ids.append(ts.target_id)
        if ts.role_ids:
            emitted_ids += list(ts.role_ids.values())
        emitted_ids += list(ts.constraints.avoid)
    allowed_ids = mock.get_resolved_ids() | mock.get_constraint_ids()
    hallucinated = [e for e in emitted_ids if e not in allowed_ids]

    base.update({
        "planner_invoked": True, "predicted_status": draft.status, "expected_status": exp_status,
        "status_correct": status_correct, "status_gate_correct": status_correct,
        "schema_valid": schema_valid,
        "action_exact_sequence": action_exact, "action_ordered": action_ordered,
        "predicted_actions": pred_actions, "expected_actions": exp_actions,
        "role_ref_accuracy": ref_accuracy, "role_ref_total": ref_total,
        "strict_symbolic_success": strict_symbolic,
        "compiled_validation_pass": compiled_valid,
        "compiled_exact_match": compiled_exact,
        "hallucinated_ids": hallucinated, "emitted_ids": emitted_ids,
        "error": None if compiled_valid else (errors or None),
        "failure_stage": None if compiled_valid else "validator",
        "validation_errors": errors,
        "first_pass_errors": first_pass_errors, "repair_errors": repair_errors,
        "attempt_count": attempt_count, "repair_rate": repair_rate,
        "final_symbolic_plan": json.loads(draft.model_dump_json()),
        "final_compiled_plan": json.loads(plan.model_dump_json()) if plan else None,
        "latency_ms": (time.time() - t0) * 1000,
    })
    return base


def compute_summary(results: list[dict], model: str, repeats: int) -> dict:
    """Keep homogeneous metric contracts separate, including unversioned history."""
    n = len(results)
    inv = [r for r in results if r.get("planner_invoked")]
    gates = [r for r in results if not r.get("planner_invoked")]

    def rate(rs, key, only_invoked=False):
        pool = rs if not only_invoked else [r for r in rs if r.get("planner_invoked")]
        vals = [r for r in pool if r.get(key) is not None]
        return sum(float(r[key]) for r in vals) / len(vals) if vals else None

    summary = {
        "model": model, "repeats": repeats, "total_cases": n,
        "planner_invoked_count": len(inv), "pre_set_status_count": len(gates),
        "status_gate_accuracy": rate(gates, "status_gate_correct"),
        # LLM symbolic（planner-invoked）
        "symbolic_schema_validity": rate(inv, "schema_valid", only_invoked=True),
        "symbolic_status_accuracy": rate(inv, "status_correct", only_invoked=True),
        "symbolic_exact_action_sequence": rate(inv, "action_exact_sequence", only_invoked=True),
        "symbolic_ordered_action_coverage": rate(inv, "action_ordered", only_invoked=True),
        "symbolic_role_ref_accuracy": rate(inv, "role_ref_accuracy", only_invoked=True),
        "symbolic_strict_success": rate(inv, "strict_symbolic_success", only_invoked=True),
        "first_pass_success": sum(bool(r.get("strict_symbolic_success")) and not r.get("first_pass_errors")
                                  and r.get("repair_rate") is None for r in inv) / len(inv) if inv else None,
        "first_pass_denominator": len(inv),
        "scorer_version": SCORER_VERSION,
        "post_repair_success": rate([r for r in inv if r.get("repair_rate") is not None], "strict_symbolic_success"),
        "repair_rate": (sum(1 for r in inv if r.get("repair_rate") is not None) / len(inv)) if inv else None,
        # Final compiled
        "final_validation_pass": rate(inv, "compiled_validation_pass", only_invoked=True),
        "final_compiled_exact_match": rate(inv, "compiled_exact_match", only_invoked=True),
        # ID safety
        "hallucinated_id_rate": sum(1 for r in inv if r.get("hallucinated_ids")) / len(inv) if inv else None,
        "total_emitted_ids": sum(len(r.get("emitted_ids", [])) for r in inv),
        "error_rate": sum(1 for r in inv if r.get("error")) / len(inv) if inv else None,
        "avg_latency_ms": sum(r["latency_ms"] for r in results) / n if n else 0,
    }

    versions = {(r.get("scorer_version", "legacy_unversioned"),
                 r.get("metric_contract_version", "legacy_contract")) for r in results}
    summary["scorer_version"] = next(iter(versions))[0] if len(versions) == 1 else "mixed"
    summary["metric_contract_version"] = next(iter(versions))[1] if len(versions) == 1 else "mixed"
    summary["score_versions_compatible"] = len(versions) <= 1
    if len(versions) > 1:
        by_version = {}
        for version in sorted(versions):
            subset = [r for r in results if (
                r.get("scorer_version", "legacy_unversioned"),
                r.get("metric_contract_version", "legacy_contract")) == version]
            by_version[" / ".join(version)] = compute_summary(subset, model, repeats)
        for key in summary:
            if key not in {"model", "repeats", "total_cases", "planner_invoked_count",
                           "pre_set_status_count", "first_pass_denominator", "total_emitted_ids",
                           "scorer_version", "metric_contract_version", "score_versions_compatible"}:
                summary[key] = None
        summary["summaries_by_version"] = by_version
    return summary


def run_eval(model: str, repeats: int, output_dir: Path):
    cases = [json.loads(l) for l in open(CASES, encoding="utf-8") if l.strip()]
    config = dict(CONFIG_BASE)
    results = []
    for repeat in range(repeats):
        for case in cases:
            r = evaluate_case(case, model, config)
            r["repeat"] = repeat
            results.append(r)

    summary = compute_summary(results, model, repeats)
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "per_case_results.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    failures = [r for r in results if r.get("error") or not r.get("compiled_validation_pass", True)
                or (r.get("planner_invoked") and not r.get("strict_symbolic_success", False))]
    with open(output_dir / "failures.jsonl", "w", encoding="utf-8") as f:
        for r in failures:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    summary["failure_count"] = len(failures)
    with open(output_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    _write_manifest(output_dir, model, repeats)

    print(f"=== Planner v3 Simple [{model}] ===")
    for k, v in summary.items():
        if v is not None:
            print(f"  {k}: {v}")
    return summary


def _write_manifest(output_dir: Path, model: str, repeats: int):
    bm = CASES.read_bytes()
    self_code = Path(__file__).resolve().read_bytes()
    cc_code = (LLM_ROOT / "compile_simple_plan.py").read_bytes()
    sc_code = (LLM_ROOT / "schema.py").read_bytes()
    manifest = {
        "created_at": "2026-08-06",
        "run_command": f"python -m eval.run_planner_simple_eval --model {model} --repeats {repeats}",
        "mode": "oracle_grounding_simple",
        "scorer_version": SCORER_VERSION, "metric_contract_version": METRIC_CONTRACT_VERSION,
        "model": model, "repeats": repeats,
        "benchmark": {"file": str(CASES), "sha256": hashlib.sha256(bm).hexdigest()},
        "evaluator": {"file": str(Path(__file__).resolve()), "sha256": hashlib.sha256(self_code).hexdigest()},
        "compiler": {"file": "compile_simple_plan.py", "sha256": hashlib.sha256(cc_code).hexdigest()},
        "schema": {"file": "schema.py", "sha256": hashlib.sha256(sc_code).hexdigest()},
        "environment": {"python": "3.12.13 (llm-task-planner)", "chromadb": "1.5.9"},
    }
    with open(output_dir / "run_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--output")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    out = Path(args.output) if args.output else OUT_BASE / slug(args.model)
    run_eval(args.model, args.repeats, out)


if __name__ == "__main__":
    main()
