"""Real intent runner/parser with FakeClient metadata and synthetic config/cases."""
import copy
import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    guard_path = ROOT.parent / "docs/optimization-review-20261003/verify_offline.py"
    source = guard_path.read_text()
    marker = 'args = ["-q", "-s"'
    assert source.count(marker) == 1
    source = source.replace(marker, 'files = sys.argv[2:]\n' + marker, 1)
    sys.argv = [str(guard_path), "llm", str(Path(__file__).resolve()), *sys.argv[1:]]
    exec(compile(source, str(guard_path), "exec"))
    raise SystemExit("unreachable")

import pytest
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "experiment" / "evaluations" / "taskchain"))
import run_intent_eval as evaluation


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    config_path = tmp_path / "fixture-config.json"
    original = {"options": {"temperature": 0.7, "num_ctx": 1600, "num_predict": 128, "seed": 17},
                "models": {"default": "unused-default"}, "rag": {"db_dir": "unused-fixture"}}
    config_path.write_text(json.dumps(original))
    state = types.SimpleNamespace(loads=0, chats=[], lists=0, clients=[], loaded=None,
        metadata={"models": [{"model": "qwen3:fake", "digest": "d" * 64}]}, list_error=None, chat_error=None)
    main = types.ModuleType("main_rag")
    main.CONFIG_PATH = config_path
    def load(path):
        state.loads += 1
        state.loaded = json.loads(Path(path).read_text())
        return state.loaded
    main.load_config = load
    main.get_ollama_host = lambda config: config.get("ollama_host") or "http://fixture.invalid:11434"
    monkeypatch.setitem(sys.modules, "main_rag", main)
    client_module = types.ModuleType("ollama")
    class FakeClient:
        def __init__(self, host, timeout):
            state.clients.append({"host": host, "timeout": timeout})
        def chat(self, **kwargs):
            state.chats.append(copy.deepcopy(kwargs))
            if state.chat_error:
                raise state.chat_error
            return types.SimpleNamespace(message=types.SimpleNamespace(content='{"action":"wait"}', thinking=""))
        def list(self):
            state.lists += 1
            if state.list_error:
                raise state.list_error
            return state.metadata
    client_module.Client = FakeClient
    monkeypatch.setitem(sys.modules, "ollama", client_module)
    # Load real parser source: it imports the FakeClient IO module at call time.
    path = ROOT / "llm/intent_parser.py"
    spec = importlib.util.spec_from_file_location("llm.intent_parser", path)
    parser = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "llm.intent_parser", parser)
    spec.loader.exec_module(parser)
    subprocess = types.ModuleType("subprocess")
    subprocess.DEVNULL = -3
    subprocess.check_output = lambda *a, **k: "synthetic_revision"
    monkeypatch.setitem(sys.modules, "subprocess", subprocess)
    cases_path = tmp_path / "fixture-cases.jsonl"
    cases_path.write_text(json.dumps({"case_id": "fixture-wait", "instruction": "Wait.",
        "category": "wait", "oracle_intent": {"action": "wait"}}) + "\n")
    return state, original, config_path, cases_path, tmp_path / "out"


def run(runtime, **kwargs):
    state, original, config_path, cases_path, out = runtime
    summary = evaluation.run_intent_evaluation(str(cases_path), "qwen3:fake",
        repeats=2, temperature=0.25, output_dir=str(out), **kwargs)
    return summary, json.loads((out / "run_manifest.json").read_text())


def test_manifest_uses_actual_config_object_after_cli_and_parser(runtime):
    summary, manifest = run(runtime)
    state, original, config_path, cases_path, out = runtime
    assert state.loads == 1
    assert len(state.chats) == 2
    assert manifest["options"] == state.chats[0]["options"] == state.chats[1]["options"]
    assert manifest["options"] == {"temperature": 0.25, "num_ctx": 1600, "num_predict": 2048, "seed": 17}
    assert manifest["runtime_config_sha256"] == sha(state.loaded)
    assert manifest["config_hash"] == sha(state.loaded)[:16]
    assert manifest["runtime_options_sha256"] == sha(manifest["options"])
    assert manifest["config_source"]["loaded_config_sha256"] == sha(original)
    assert manifest["config_source"]["file_sha256"] == hashlib.sha256(config_path.read_bytes()).hexdigest()
    assert manifest["config_source"]["path"] == str(config_path)
    assert manifest["run_arguments"]["temperature"] == 0.25
    assert manifest["runtime_adjustments"]["options"]["num_predict"] == {"before": 128, "after": 2048}
    assert manifest["provenance_version"] == "intent_runtime_provenance_v1"
    assert summary["provenance_version"] == manifest["provenance_version"]


def test_new_case_and_final_summary_record_same_runtime(runtime):
    summary, manifest = run(runtime)
    state, original, config_path, cases_path, out = runtime
    records = [json.loads(line) for line in (out / "per_case_results.jsonl").read_text().splitlines()]
    assert len(records) == 2
    for record in records:
        trace = record["runtime_provenance"]
        assert trace["options"] == manifest["options"]
        assert trace["options_sha256"] == manifest["runtime_options_sha256"]
        assert trace["host"] == state.clients[0]["host"]
        assert trace["parser_path"] == "llm_eval"
        assert trace["model"] == state.chats[0]["model"]
        assert trace["request_contract"]["think"] == state.chats[0]["think"] is False
        assert record["provenance_version"] == manifest["provenance_version"]
    saved_summary = json.loads((out / "summary.json").read_text())
    assert saved_summary == summary
    assert saved_summary["runtime_config_sha256"] == manifest["runtime_config_sha256"]


def test_digest_unqueried_is_explicit(runtime):
    _, manifest = run(runtime)
    state = runtime[0]
    assert state.lists == 0
    assert manifest["model_digest"] == "unknown"
    assert manifest["model_digest_verification"] == {
        "status": "not_verified", "reason": "lookup_disabled", "source": None}
    assert "sourceDigest" not in manifest


@pytest.mark.parametrize("typed", [False, True])
def test_optional_fake_metadata_digest_full_value_and_host(runtime, typed):
    state = runtime[0]
    if typed:
        state.metadata = types.SimpleNamespace(models=[
            types.SimpleNamespace(model="qwen3:fake", digest="d" * 64)])
    _, manifest = run(runtime, verify_model_digest=True)
    assert state.lists == 1
    assert state.clients[-1]["host"] == "http://fixture.invalid:11434"
    assert manifest["model_digest"] == "d" * 64
    assert manifest["model_digest_verification"]["status"] == "verified"
    assert manifest["model_digest_verification"]["source"] == "ollama.list"
    assert manifest["model_digest_verification"]["scope"] == "post_run_metadata_only"
    assert manifest["model_digest_verification"]["queried_host"] == state.clients[-1]["host"]


@pytest.mark.parametrize("metadata,reason", [
    ({"models": []}, "requested_model_not_found"),
    ({"models": [{"model": "qwen3:fake"}]}, "digest_missing"),
])
def test_digest_metadata_gaps_not_silently_swallowed(runtime, metadata, reason):
    state = runtime[0]
    state.metadata = metadata
    _, manifest = run(runtime, verify_model_digest=True)
    assert manifest["model_digest_verification"]["status"] == "not_verified"
    assert manifest["model_digest_verification"]["reason"] == reason
    assert manifest["model_digest"] == "unknown"


def test_digest_query_failure_has_reason(runtime):
    state = runtime[0]
    state.list_error = RuntimeError("synthetic metadata failure")
    _, manifest = run(runtime, verify_model_digest=True)
    assert manifest["model_digest_verification"]["status"] == "not_verified"
    assert manifest["model_digest_verification"]["reason"] == "query_failed"
    assert manifest["model_digest_verification"]["error_type"] == "RuntimeError"
    assert state.loads == 1


def test_cli_override_sources_are_explicit(runtime, monkeypatch):
    state, original, config_path, cases_path, out = runtime
    monkeypatch.setattr(sys, "argv", ["eval.run_intent_eval", "--benchmark", str(cases_path),
        "--model=qwen3:fake", "--temperature", "0.25", "--repeats", "2", "--output", str(out)])
    evaluation.main()
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["invocation"]["source"] == "cli"
    assert manifest["invocation"]["explicit_overrides"]["temperature"] == 0.25
    assert manifest["invocation"]["explicit_overrides"]["model"] == "qwen3:fake"
    assert manifest["invocation"]["explicit_overrides"]["repeats"] == 2
    assert "category" not in manifest["invocation"]["explicit_overrides"]


def test_default_options_not_backfilled_by_manifest(runtime):
    state, original, config_path, cases_path, out = runtime
    config_path.write_text("{}")
    evaluation.run_intent_evaluation(str(cases_path), "fixture-plain",
        output_dir=str(out))
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert state.chats[0]["options"] == manifest["options"] == {"temperature": 0}
    assert manifest["runtime_defaults_verification"] == "server_defaults_not_verified"


def test_source_file_absence_is_marked_not_fabricated(runtime, monkeypatch):
    state, original, config_path, cases_path, out = runtime
    main = sys.modules["main_rag"]
    main.CONFIG_PATH = config_path.parent / "missing-config.yaml"
    def load(path):
        state.loads += 1
        state.loaded = {}
        return state.loaded
    monkeypatch.setattr(main, "load_config", load)
    evaluation.run_intent_evaluation(str(cases_path), "fixture-plain", output_dir=str(out))
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["config_source"]["file_sha256"] is None
    assert manifest["config_source"]["file_status"] == "not_verified"
    assert manifest["config_source"]["file_reason"] == "file_not_found"


def test_full_hashes_match_actual_prompt_schema_and_case_bytes(runtime):
    _, manifest = run(runtime)
    state, original, config_path, cases_path, out = runtime
    request = state.chats[0]
    assert manifest["prompt_sha256"] == hashlib.sha256(request["messages"][0]["content"].encode()).hexdigest()
    assert manifest["schema_sha256"] == sha(request["format"])
    assert manifest["benchmark_sha256"] == hashlib.sha256(cases_path.read_bytes()).hexdigest()
    assert manifest["evaluator_source_sha256"] == hashlib.sha256(Path(evaluation.__file__).read_bytes()).hexdigest()


def test_fast_path_records_config_without_claiming_chat(runtime, monkeypatch):
    state, original, config_path, cases_path, out = runtime
    from planner_schemas.intent import TaskIntent
    parser = sys.modules["llm.intent_parser"]
    monkeypatch.setattr(parser, "parse_intent_with_llm", lambda *a, **k: TaskIntent(action="wait"))
    _, manifest = run(runtime)
    records = [json.loads(line) for line in (out / "per_case_results.jsonl").read_text().splitlines()]
    assert state.chats == [] and state.lists == 0
    assert records[0]["runtime_provenance"]["parser_path"] == "fast_path"
    assert records[0]["runtime_provenance"]["request_contract"] is None
    assert manifest["options"]["num_predict"] == 128


def test_provenance_added_without_altering_parser_or_scorer(runtime):
    state, original, config_path, cases_path, out = runtime
    current, _ = run(runtime)
    backup = ROOT / "tests/fixtures/INTENT_before_runtime_provenance_run_intent_eval.py"
    spec = importlib.util.spec_from_file_location("intent_before_provenance", backup)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    oracle = {"case_id": "fixture-wait", "instruction": "Wait.", "oracle_intent": {"action": "wait"}}
    prior = old.evaluate_intent_case(oracle, "qwen3:fake", copy.deepcopy(state.loaded))
    now = evaluation.evaluate_intent_case(oracle, "qwen3:fake", copy.deepcopy(state.loaded))
    assert prior["strict_exact_match"] == now["strict_exact_match"]
    assert prior["strict_intent_match"] == now["strict_intent_match"]
    assert prior["canonicalized_intent"] == now["canonicalized_intent"]


def test_cli_defaults_and_filter_preserved(runtime, monkeypatch, capsys):
    state, original, config_path, cases_path, out = runtime
    monkeypatch.setattr(sys, "argv", ["eval.run_intent_eval", "--benchmark", str(cases_path),
        "--output", str(out), "--category", "wait"])
    evaluation.main()
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["model"] == "qwen2.5:0.5b"
    assert manifest["temperature"] == 0 and manifest["repeats"] == 1
    assert manifest["options"]["temperature"] == 0 and manifest["options"]["num_predict"] == 128
    assert manifest["run_arguments"]["category_filter"] == "wait"
    assert "temperature" not in manifest["invocation"]["explicit_overrides"]
    assert len(state.chats) == 1 and state.lists == 0


def test_parser_failure_keeps_runtime_options_and_call_budget(runtime):
    state, original, config_path, cases_path, out = runtime
    state.chat_error = ValueError("synthetic parser IO failure")
    summary, manifest = run(runtime)
    records = [json.loads(line) for line in (out / "per_case_results.jsonl").read_text().splitlines()]
    assert len(state.chats) == len(records) == 2
    assert all(not record["schema_valid"] for record in records)
    assert all(record["runtime_provenance"]["options"] == manifest["options"] for record in records)
    assert all(record["runtime_provenance"]["parser_path"] == "not_verified" for record in records)
    assert state.lists == 0 and summary["schema_validity_rate"] == 0


def test_accepted_cli_abbreviations_are_traced(runtime, monkeypatch, capsys):
    state, original, config_path, cases_path, out = runtime
    monkeypatch.setattr(sys, "argv", ["eval.run_intent_eval", "--benchmark", str(cases_path),
        "--output", str(out), "--temp=0.33", "--rep", "2"])
    evaluation.main()
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["options"]["temperature"] == 0.33
    assert manifest["invocation"]["explicit_overrides"]["temperature"] == 0.33
    assert manifest["invocation"]["explicit_overrides"]["repeats"] == 2


def test_mixed_provenance_does_not_merge_intent_summary(runtime, tmp_path):
    _, manifest = run(runtime)
    out = runtime[-1]
    row = json.loads((out / "predictions.jsonl").read_text().splitlines()[0])
    legacy = copy.deepcopy(row)
    legacy.pop("provenance_version")
    legacy.pop("runtime_provenance")
    legacy.pop("runtime_contract_sha256", None)
    mixed = evaluation.compute_intent_summary([row, legacy], [])
    assert mixed["score_versions_compatible"] is False
    assert mixed["strict_intent_match_rate"] is None
    assert mixed["strict_exact_match_rate"] is None
    assert len(mixed["summaries_by_contract"]) == 2
    evaluation.write_intent_summary_md(mixed, tmp_path / "mixed.md")
    assert "cannot be combined" in (tmp_path / "mixed.md").read_text()


def test_different_runtime_contracts_do_not_merge(runtime):
    _, manifest = run(runtime)
    row = json.loads((runtime[-1] / "predictions.jsonl").read_text().splitlines()[0])
    other = copy.deepcopy(row)
    other["runtime_contract_sha256"] = "different-runtime"
    summary = evaluation.compute_intent_summary([row, other], [])
    assert summary["runtime_contracts_compatible"] is False
    assert summary["action_accuracy"] is None
    assert len(summary["summaries_by_contract"]) == 2


def test_same_run_fast_path_and_parser_adjustments_share_contract(runtime, monkeypatch):
    state, original, config_path, cases_path, out = runtime
    from planner_schemas.intent import TaskIntent
    parser = sys.modules["llm.intent_parser"]
    actual = parser.parse_intent_with_llm
    def parse(instruction, *args, **kwargs):
        if instruction == "fixture fast path":
            return TaskIntent(action="wait")
        return actual(instruction, *args, **kwargs)
    monkeypatch.setattr(parser, "parse_intent_with_llm", parse)
    cases_path.write_text("\n".join(json.dumps({"case_id": str(i), "instruction": text,
        "oracle_intent": {"action": "wait"}}) for i, text in enumerate(("fixture fast path", "Wait."))))
    summary, manifest = run(runtime)
    records = [json.loads(line) for line in (out / "predictions.jsonl").read_text().splitlines()]
    assert len({r["runtime_contract_sha256"] for r in records}) == 1
    assert records[0]["runtime_provenance"]["options"]["num_predict"] == 128
    assert records[1]["runtime_provenance"]["options"]["num_predict"] == 2048
    assert summary["score_versions_compatible"] and summary["runtime_contracts_compatible"]
    assert summary["strict_intent_match_rate"] == 1


def test_public_functions_and_result_fields_are_retained(runtime):
    import ast
    backup = ROOT / "tests/fixtures/INTENT_before_runtime_provenance_run_intent_eval.py"
    old_tree = ast.parse(backup.read_text())
    current_tree = ast.parse(Path(evaluation.__file__).read_text())
    signatures = lambda tree: {n.name: n.args for n in tree.body if isinstance(n, ast.FunctionDef)}
    old, current = signatures(old_tree), signatures(current_tree)
    for name, args in old.items():
        assert name in current
        original_names = [arg.arg for arg in args.args]
        assert [arg.arg for arg in current[name].args][:len(original_names)] == original_names
    state = runtime[0]
    run(runtime)
    spec = importlib.util.spec_from_file_location("intent_original_contract", backup)
    prior = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prior)
    case = {"case_id": "wait", "instruction": "Wait.", "oracle_intent": {"action": "wait"}}
    before = prior.evaluate_intent_case(case, "qwen3:fake", copy.deepcopy(state.loaded))
    after = evaluation.evaluate_intent_case(case, "qwen3:fake", copy.deepcopy(state.loaded))
    assert set(before) <= set(after)
    assert set(prior.compute_intent_summary([before], [case])) <= set(
        evaluation.compute_intent_summary([after], [case]))
