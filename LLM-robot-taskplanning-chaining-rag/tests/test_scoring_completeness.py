"""CPU fixtures execute real scorers with parser/grounding/generation IO isolated."""
import ast
import copy
import json
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    guard_path = ROOT.parent / "docs/optimization-review-20261003/verify_offline.py"
    source = guard_path.read_text()
    marker = 'args = ["-q", "-s"'
    assert source.count(marker) == 1, "Shared runner argument marker changed"
    source = source.replace(marker, 'files = sys.argv[2:]\n' + marker, 1)
    sys.argv = [str(guard_path), "llm", str(Path(__file__).resolve()), *sys.argv[1:]]
    exec(compile(source, str(guard_path), "exec"))
    raise SystemExit("unreachable")

import pytest

sys.path.insert(0, str(ROOT))
from schema import PipelineResult, SimplePlanDraft
from planner_schemas.intent import TaskIntent
from compile_simple_plan import compile_simple_plan


def load_scorer(name):
    """Load actual code, removing only eager imports of external pipeline IO."""
    path = ROOT / "eval" / (name + ".py")
    tree = ast.parse(path.read_text())
    blocked = {"main_rag", "grounding.orchestrator", "api_schemas"}
    tree.body = [n for n in tree.body if not (
        isinstance(n, ast.ImportFrom) and n.module in blocked)]
    module = types.ModuleType("fixture_" + name)
    module.__file__ = str(path)
    exec(compile(tree, str(path), "exec"), module.__dict__)
    return module


@pytest.fixture
def pipeline():
    return load_scorer("run_pipeline_eval")


@pytest.fixture
def simple():
    return load_scorer("run_planner_simple_eval")


def summarize(pipeline, grounding=(), planning=(), cases=None):
    return pipeline.compute_summary({"mode": "fake", "model": "fake", "repeats": 1,
        "grounding_results": list(grounding), "planning_results": list(planning)}, cases)


def ground(monkeypatch, pipeline, expected, predicted_ids=("a", "b"), evidence=None,
           status="ambiguous", raises=False):
    intent = TaskIntent(action="locate", target={"text": "chair"})
    parser = types.ModuleType("llm.intent_parser")
    parser.parse_intent_with_llm = lambda *a, **k: intent
    parser.intent_to_dict = lambda value: value.model_dump(mode="json")
    rag = types.ModuleType("scene_graph_api")
    rag.SceneGraphRAG = lambda *a, **k: object()
    resolution = types.SimpleNamespace(status=status, selected=None, selected_many=[],
        candidates=[types.SimpleNamespace(node_id=x) for x in predicted_ids])
    references = {"target": resolution}
    outcome = types.SimpleNamespace(status=status, references=references,
        get_resolved_ids=lambda: set(), to_dict=lambda: {})
    def resolve(intent):
        if raises:
            raise RuntimeError("synthetic grounding failure")
        return outcome
    orchestrator = types.ModuleType("grounding.orchestrator")
    orchestrator.GroundingOrchestrator = lambda *a: types.SimpleNamespace(ground_intent=resolve)
    for module in (parser, rag, orchestrator):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    case = {"case_id": "g", "instruction": "synthetic", "tags": ["ambiguous"],
            "expected_grounding": expected}
    if evidence is not None:
        case["ground_truth_evidence"] = evidence
    return pipeline.evaluate_grounding_case(case, "fake", "fake", {}), case


@pytest.mark.parametrize("predicted,ok", [(("b", "a"), True), (("a", "wrong"), False),
                                         (("a",), False), (("a", "b", "extra"), False)])
def test_ambiguous_candidates_exact(monkeypatch, pipeline, predicted, ok):
    result, _ = ground(monkeypatch, pipeline, {"pipeline_status": "ambiguous",
        "target": {"status": "ambiguous", "candidate_node_ids": ["a", "b"]}}, predicted)
    assert result["grounding_case_success"] is ok
    assert result["grounding_evaluable"]
    assert result["role_metrics"]["target"]["candidate_set_correct"] is ok


def test_existing_evidence_shape_is_used_only_for_target(monkeypatch, pipeline):
    result, _ = ground(monkeypatch, pipeline, {"pipeline_status": "ambiguous",
        "target": {"status": "ambiguous", "candidate_count": 2}},
        predicted_ids=("a", "wrong"), evidence={"candidate_node_ids": ["a", "b"]})
    assert not result["grounding_case_success"]
    assert result["grounding_evaluable"]


@pytest.mark.parametrize("expected", [{}, {"pipeline_status": "ambiguous",
    "target": {"status": "ambiguous", "candidate_count": 2}},
    {"pipeline_status": "ready", "target": {"status": "resolved"}}])
def test_missing_ground_truth_is_not_exact_evaluable(monkeypatch, pipeline, expected):
    result, case = ground(monkeypatch, pipeline, expected)
    assert not result.get("grounding_case_success")
    assert result["grounding_evaluable"] is False
    summary = summarize(pipeline, [result], cases=[case])["grounding"]
    assert summary["grounding_exact_evaluated_count"] == 0
    assert summary["grounding_exact_unevaluable_count"] == 1
    assert summary["grounding_exact_evaluated_success_rate"] is None


def test_grounding_exception_retains_candidate_gt_denominator(monkeypatch, pipeline):
    result, case = ground(monkeypatch, pipeline, {"pipeline_status": "ambiguous",
        "target": {"status": "ambiguous", "candidate_node_ids": ["a", "b"]}}, raises=True)
    summary = summarize(pipeline, [result], cases=[case])["grounding"]
    assert result["grounding_evaluable"]
    assert summary["candidate_set_evaluated_count"] == 1
    assert summary["candidate_set_accuracy"] == 0


def planning(monkeypatch, pipeline, expected, fails=False, chain=None):
    def outcome(*a, **k):
        if fails:
            raise RuntimeError("synthetic planner failure")
        return PipelineResult(status="valid", task_plan={"status": "valid", "task_chain": chain or [{
            "action": "wait", "target_ref": None, "constraints": {"avoid": [], "prefer": []}}]})
    monkeypatch.setattr(pipeline, "run_structured_task_planner", outcome, raising=False)
    return pipeline.evaluate_planning_case({"case_id": "p", "instruction": "synthetic",
        "expected_plan": expected}, "fake", "fake", {})


def test_planning_failure_retains_all_declared_gt(monkeypatch, pipeline):
    expected = {"status": "valid", "required_actions": ["navigate_to"], "expected_target_ids": ["a"],
        "expected_avoid_ids": ["b"], "expected_prefer_ids": ["quiet"],
        "expected_steps": [{"target_ref": "target", "role_refs": {"actor": "actor"}},
                           {"target_ref": "destination"}]}
    result = planning(monkeypatch, pipeline, expected, fails=True)
    assert result["step_target_ref_total"] == 2
    assert result["step_target_ref_accuracy"] == 0
    assert result["constraint_avoid_correct"] is False
    assert result["constraint_prefer_correct"] is False
    assert result["role_refs_correct"] is False
    summary = summarize(pipeline, planning=[result])["planning"]
    assert summary["step_target_ref_evaluated_count"] == 1
    assert summary["step_target_ref_denominator"] == 2
    assert summary["constraint_prefer_evaluated_count"] == 1
    assert summary["constraint_prefer_accuracy"] == 0


@pytest.mark.parametrize("fails", [False, True])
def test_declared_empty_constraint_gt_is_evaluable(monkeypatch, pipeline, fails):
    result = planning(monkeypatch, pipeline, {"expected_avoid_ids": [], "expected_prefer_ids": []}, fails)
    assert result["constraint_avoid_correct"] is (not fails)
    assert result["constraint_prefer_correct"] is (not fails)


def test_no_ref_step_still_counts(monkeypatch, pipeline):
    result = planning(monkeypatch, pipeline, {"expected_steps": [{"target_ref": None}]})
    assert result["step_target_ref_total"] == 1
    assert result["step_target_ref_accuracy"] == 1


def test_planning_without_gt_is_marked(monkeypatch, pipeline):
    result = planning(monkeypatch, pipeline, {})
    assert result["planning_evaluable"] is False


def test_pipeline_mixed_versions_do_not_average(monkeypatch, pipeline):
    current = planning(monkeypatch, pipeline, {"status": "valid", "required_actions": ["wait"]})
    old = copy.deepcopy(current)
    old.pop("metric_contract_version", None)
    old["status_correct"] = False
    summary = summarize(pipeline, planning=[current, old])
    assert summary["score_versions_compatible"] is False
    assert summary["planning"] is None
    assert len(summary["summaries_by_version"]) == 2


def simple_case(identifier="s"):
    return {"case_id": identifier, "instruction": "synthetic", "oracle_grounding": {},
        "expected_symbolic_plan": {"status": "valid", "steps": [{"action": "wait", "ref": None}]},
        "expected_compiled_plan": {"steps": [{"action": "wait", "target_id": None, "executor": "none"}]}}


@pytest.mark.parametrize("stage", ["compiler", "validator"])
def test_simple_compile_or_validation_exception_keeps_sample(monkeypatch, simple, stage):
    monkeypatch.setattr(simple, "build_mock_grounding", lambda gt: types.SimpleNamespace(
        get_resolved_ids=lambda: set(), get_constraint_ids=lambda: set()))
    monkeypatch.setattr(simple, "generate_simple_plan", lambda *a, **k:
        SimplePlanDraft(status="valid", steps=[{"action": "wait", "ref": None}]))
    def failure(*a, **k):
        raise RuntimeError("synthetic " + stage)
    monkeypatch.setattr(simple, "compile_simple_plan",
                        failure if stage == "compiler" else compile_simple_plan)
    monkeypatch.setattr(simple, "_validate_grounded_plan", failure, raising=False)
    result = simple.evaluate_case(simple_case(), "fake", {})
    assert result["case_id"] == "s"
    assert result["failure_stage"] == stage
    assert result["schema_valid"] and result["strict_symbolic_success"]
    assert result["attempt_count"] == 1
    assert result["compiled_exact_match"] is False
    assert result["compiled_validation_pass"] is False
    assert result["final_symbolic_plan"]["steps"][0]["action"] == "wait"


def test_simple_schema_failure_keeps_gt_metrics(monkeypatch, simple):
    monkeypatch.setattr(simple, "build_mock_grounding", lambda gt: object())
    def fail(*a, **k):
        raise ValueError("synthetic schema failure")
    monkeypatch.setattr(simple, "generate_simple_plan", fail)
    result = simple.evaluate_case(simple_case(), "fake", {})
    assert result["role_ref_total"] == 1
    assert result["role_ref_accuracy"] == 0
    assert result["status_correct"] is False
    assert result["compiled_exact_match"] is False


def test_simple_invalid_compiled_plan_counts_expected_exact(monkeypatch, simple):
    monkeypatch.setattr(simple, "build_mock_grounding", lambda gt: types.SimpleNamespace(
        get_resolved_ids=lambda: set(), get_constraint_ids=lambda: set()))
    monkeypatch.setattr(simple, "generate_simple_plan", lambda *a, **k:
        SimplePlanDraft(status="valid", steps=[{"action": "wait", "ref": None}]))
    monkeypatch.setattr(simple, "_validate_grounded_plan", lambda *a, **k: ["synthetic invalid"], raising=False)
    result = simple.evaluate_case(simple_case(), "fake", {})
    assert result["compiled_exact_match"] is False


def test_simple_run_keeps_compile_failure_and_next_case(monkeypatch, simple, tmp_path):
    cases = tmp_path / "fixture.jsonl"
    cases.write_text("\n".join(json.dumps(simple_case(x)) for x in ("bad", "good")))
    monkeypatch.setattr(simple, "CASES", cases)
    monkeypatch.setattr(simple, "build_mock_grounding", lambda gt: types.SimpleNamespace(
        get_resolved_ids=lambda: set(), get_constraint_ids=lambda: set()))
    monkeypatch.setattr(simple, "generate_simple_plan", lambda *a, **k:
        SimplePlanDraft(status="valid", steps=[{"action": "wait", "ref": None}]))
    monkeypatch.setattr(simple, "_validate_grounded_plan", lambda *a, **k: [], raising=False)
    def compile(draft, gt):
        if not hasattr(compile, "called"):
            compile.called = True
            raise RuntimeError("synthetic compiler")
        return compile_simple_plan(draft, gt)
    monkeypatch.setattr(simple, "compile_simple_plan", compile)
    monkeypatch.setattr(simple, "_write_manifest", lambda *a: None)
    summary = simple.run_eval("fake", 1, tmp_path / "out")
    records = [json.loads(line) for line in (tmp_path / "out/per_case_results.jsonl").read_text().splitlines()]
    assert len(records) == summary["total_cases"] == 2
    assert summary["final_validation_pass"] == 0.5
    assert summary["failure_count"] == 1


def test_simple_mixed_versions_are_partitioned(monkeypatch, simple, tmp_path):
    cases = tmp_path / "fixture.jsonl"
    cases.write_text("\n".join(json.dumps(simple_case(x)) for x in ("old", "new")))
    monkeypatch.setattr(simple, "CASES", cases)
    def evaluate(case, *a):
        return {"case_id": case["case_id"], "planner_invoked": True, "schema_valid": True,
            "scorer_version": simple.SCORER_VERSION, "latency_ms": 0,
            **({"metric_contract_version": simple.METRIC_CONTRACT_VERSION} if case["case_id"] == "new" else {})}
    monkeypatch.setattr(simple, "evaluate_case", evaluate)
    monkeypatch.setattr(simple, "_write_manifest", lambda *a: None)
    summary = simple.run_eval("fake", 1, tmp_path / "out")
    assert summary["score_versions_compatible"] is False
    assert summary["symbolic_schema_validity"] is None
    assert len(summary["summaries_by_version"]) == 2


def test_mixed_pipeline_markdown_remains_usable(monkeypatch, pipeline, tmp_path):
    row = planning(monkeypatch, pipeline, {"status": "valid"})
    old = copy.deepcopy(row)
    old.pop("metric_contract_version")
    report = summarize(pipeline, planning=[row, old])
    path = tmp_path / "summary.md"
    pipeline.write_summary_md(report, path)
    assert "cannot be combined" in path.read_text()


def test_oracle_prefer_gt_survives_planner_exception(monkeypatch, pipeline):
    def fail(*a, **k):
        raise RuntimeError("synthetic planner failure")
    monkeypatch.setattr(pipeline, "_run_planner_with_oracle_grounding", fail)
    case = {"case_id": "p", "instruction": "synthetic", "expected_plan": {"status": "valid"},
            "oracle_grounding": {"constraints": [{"type": "prefer", "value": "quiet"}]}}
    result = pipeline.evaluate_planning_case(case, "fake", "fake", {}, oracle_grounding=True)
    assert result["constraint_prefer_correct"] is False
    assert summarize(pipeline, planning=[result])["planning"]["constraint_prefer_evaluated_count"] == 1


def test_grounding_evidence_does_not_mutate_gt(pipeline):
    case = {"expected_grounding": {"pipeline_status": "ambiguous", "target": {"status": "ambiguous"}},
            "ground_truth_evidence": {"candidate_node_ids": ["a", "b"]}}
    original = copy.deepcopy(case)
    expected, evaluable, _ = pipeline._grounding_evidence(case)
    assert evaluable and expected["target"]["candidate_node_ids"] == ["a", "b"]
    assert case == original


def test_extra_role_cannot_borrow_target_candidate_gt(pipeline):
    expected, evaluable, reasons = pipeline._grounding_evidence({
        "expected_grounding": {"pipeline_status": "ambiguous",
            "target": {"status": "ambiguous"}, "source": {"status": "ambiguous"}},
        "ground_truth_evidence": {"candidate_node_ids": ["a", "b"]}})
    assert not evaluable and "source:missing_candidate_gt" in reasons
    assert "candidate_node_ids" not in expected["source"]


def test_simple_manifest_tags_completeness_contract(simple, monkeypatch, tmp_path):
    fixture = tmp_path / "cases.jsonl"
    fixture.write_text(json.dumps(simple_case()))
    monkeypatch.setattr(simple, "CASES", fixture)
    simple._write_manifest(tmp_path, "fake", 1)
    manifest = json.loads((tmp_path / "run_manifest.json").read_text())
    assert manifest["scorer_version"] == simple.SCORER_VERSION
    assert manifest["metric_contract_version"] == "simple_completeness_v3"


def test_existing_public_function_signatures_preserved():
    base = ROOT / "tests/fixtures"
    for name in ("run_pipeline_eval.py", "run_planner_simple_eval.py"):
        before = ast.parse((base / ("EVAL_before_completeness_" + name)).read_text())
        after = ast.parse((ROOT / "eval" / name).read_text())
        signatures = lambda tree: {n.name: ast.dump(n.args) for n in tree.body
                                   if isinstance(n, ast.FunctionDef)}
        original, current = signatures(before), signatures(after)
        assert all(current.get(name) == args for name, args in original.items())


def test_step_ref_micro_denominator_includes_failed_expected_steps(monkeypatch, pipeline):
    good = planning(monkeypatch, pipeline, {"expected_steps": [{"target_ref": None}]})
    failed = planning(monkeypatch, pipeline, {"expected_steps": [
        {"target_ref": "target"}, {"target_ref": "source"}, {"target_ref": "destination"}]}, fails=True)
    summary = summarize(pipeline, planning=[good, failed])["planning"]
    assert summary["step_target_ref_accuracy"] == 0.5  # legacy macro metric
    assert summary["step_target_ref_micro_accuracy"] == 0.25
    assert summary["step_target_ref_correct_count"] == 1
    assert summary["step_target_ref_denominator"] == 4


def test_legacy_rows_do_not_invent_new_micro_accuracy(monkeypatch, pipeline):
    legacy = planning(monkeypatch, pipeline, {"expected_steps": [{"target_ref": None}]})
    legacy.pop("metric_contract_version")
    legacy.pop("step_target_ref_correct_count")
    legacy.pop("planning_evaluable")
    summary = summarize(pipeline, planning=[legacy])["planning"]
    assert summary["step_target_ref_micro_accuracy"] is None
    assert summary["step_target_ref_correct_count"] is None
    assert summary["planning_evaluability_unknown_count"] == 1
