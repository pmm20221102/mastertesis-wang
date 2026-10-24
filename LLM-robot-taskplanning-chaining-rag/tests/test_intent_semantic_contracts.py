"""Intent contracts, isolated from model services and evaluation datasets."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llm.intent_parser import parse_intent_with_llm, validate_and_canonicalize_intent
from planner_schemas.intent import TaskIntent


def serve(monkeypatch, *payloads):
    import ollama
    calls = []
    responses = iter(payloads)

    def chat(self, **kwargs):
        calls.append(kwargs)
        payload = next(responses)
        return SimpleNamespace(message=SimpleNamespace(
            content=payload if isinstance(payload, str) else json.dumps(payload), thinking=None))

    monkeypatch.setattr(ollama.Client, "chat", chat)
    return calls


OK = {"complete": True, "ordered": True, "issues": []}
BAD = {"complete": False, "ordered": False, "issues": ["Missing or reordered step"]}
COMMAND = "First find the bed, then go to the ward, finally wait."
FULL = {"action": "wait", "steps": [
    {"action": "locate", "target": {"text": "bed"}},
    {"action": "navigate", "target": {"text": "ward"}},
    {"action": "wait"}]}
# Existing audit contracts deliberately opt in; default production contracts
# below use DEFAULT_CONFIG and must never rely on a model audit verdict.
DEFAULT_CONFIG = {"ollama_host": "http://127.0.0.1:11434"}
CONFIG = {**DEFAULT_CONFIG, "intent_audit_in_production": True}


@pytest.mark.parametrize("kind,identifier", [("place", "928841"), ("room", "201"),
                                               ("place", "8070450532247928832")])
@pytest.mark.parametrize("role", ["target", "source", "destination", "actor"])
def test_explicit_relation_id_all_roles(kind, identifier, role):
    text = f"{kind} {identifier}"
    intent = TaskIntent.model_validate({"action": "navigate", role: {
        "text": "bed", "relations": [{"relation": "inside", "target_text": text}]}})
    result = validate_and_canonicalize_intent(f"Go to bed inside {text}", intent, eval_mode=True)
    relation = getattr(result, role).relations[0]
    assert relation.target_node_id == identifier
    assert relation.target_node_type == kind


def test_step_relation_id():
    intent = TaskIntent.model_validate({"action": "wait", "steps": [{"action": "locate",
        "target": {"text": "bed", "relations": [{"relation": "inside", "target_text": "Place ID 42"}]}}]})
    result = validate_and_canonicalize_intent("Find bed inside Place ID 42", intent)
    assert result.steps[0].target.relations[0].target_node_id == "42"


def test_invented_relation_id_rejected():
    intent = TaskIntent.model_validate({"action": "locate", "target": {"text": "bed",
        "relations": [{"relation": "inside", "target_text": "place 42", "target_node_id": "99"}]}})
    with pytest.raises(ValueError, match="ID"):
        validate_and_canonicalize_intent("Find bed inside place 42", intent)


def test_named_room_does_not_invent_id():
    intent = TaskIntent.model_validate({"action": "locate", "target": {"text": "bed",
        "relations": [{"relation": "inside", "target_text": "room North"}]}})
    assert validate_and_canonicalize_intent("Find bed inside room North", intent).target.relations[0].target_node_id is None


@pytest.mark.parametrize("partial", [
    {"action": "wait"},
    {"action": "wait", "steps": [FULL["steps"][0], FULL["steps"][2]]},
    {"action": "wait", "steps": list(reversed(FULL["steps"]))}])
def test_missing_or_reordered_steps_reparsed(monkeypatch, partial):
    calls = serve(monkeypatch, partial, FULL)
    result = parse_intent_with_llm(COMMAND, config=DEFAULT_CONFIG)
    assert [step.action for step in result.steps] == ["locate", "navigate", "wait"]
    assert result.action == "wait"
    assert len(calls) == 2
    assert all(COMMAND in str(call["messages"]) for call in calls)


def test_persistent_omission_fails_bounded(monkeypatch):
    calls = serve(monkeypatch, {"action": "wait"}, {"action": "wait"})
    with pytest.raises(ValueError, match="completeness"):
        parse_intent_with_llm(COMMAND, config=DEFAULT_CONFIG)
    assert len(calls) == 2


def test_multistep_exact_id_does_not_bypass_llm(monkeypatch):
    command = "Go to node 8070450532247928832; wait."
    payload = {"action": "wait", "steps": [{"action": "navigate", "target": {
        "text": "8070450532247928832"}}, {"action": "wait"}]}
    calls = serve(monkeypatch, payload, OK)
    result = parse_intent_with_llm(command, config=CONFIG)
    assert len(result.steps) == 2
    assert len(calls) == 2


def test_ontology_and_unknown_class(monkeypatch):
    calls = serve(monkeypatch, {"action": "locate", "target": {
        "text": "alien_scanner", "label": "small_office_objects"}}, OK)
    result = parse_intent_with_llm("Find alien_scanner", config=CONFIG,
                                 scene_ontology=["hospital_bed", "iv_stand"])
    assert result.target.text == "alien_scanner"
    assert result.target.label is None
    prompt = calls[0]["messages"][0]["content"]
    assert "hospital_bed" in prompt and "iv_stand" in prompt
    assert "small_office_object" not in prompt


def test_old_api_and_eval_output(monkeypatch):
    serve(monkeypatch, {"action": "locate", "target": {"text": "bed"}}, OK)
    result = parse_intent_with_llm("Find bed", "mistral", CONFIG, True)
    assert result["canonicalized_intent"].target.text == "bed"
    assert result["raw_lmm_output"]["action"] == "locate"


def test_invalid_audit_fails_closed(monkeypatch):
    invalid = {"complete": "true", "ordered": True, "issues": []}
    calls = serve(monkeypatch, FULL, invalid, FULL, invalid)
    with pytest.raises(ValueError, match="completeness"):
        parse_intent_with_llm(COMMAND, config=CONFIG)
    assert len(calls) == 4


def test_config_ontology_keeps_legacy_call(monkeypatch):
    calls = serve(monkeypatch, {"action": "locate", "target": {
        "text": "bed", "label": "hospital_bed"}}, OK)
    config = {**CONFIG, "scene_ontology": ["hospital_bed", "iv_stand"]}
    result = parse_intent_with_llm("Find bed", "mistral", config, False)
    assert result.target.label == "hospital_bed"
    assert "hospital_bed" in calls[0]["messages"][0]["content"]
    assert config["scene_ontology"] == ["hospital_bed", "iv_stand"]


def test_repair_trace_preserves_initial_raw_output(monkeypatch):
    first = {"action": "wait"}
    serve(monkeypatch, first, BAD, FULL, OK)
    result = parse_intent_with_llm(COMMAND, "mistral", {**CONFIG, "intent_audit_in_eval": True}, True)
    assert result["raw_lmm_output"] == first
    assert result["raw_content"] == json.dumps(first)
    assert result["intent_parse_attempts"][0]["raw_lmm_output"] == first
    assert result["intent_parse_attempts"][1]["raw_lmm_output"] == FULL
    assert result["canonicalized_intent"].steps[-1].action == "wait"


def test_bare_id_relation_with_explicit_type(monkeypatch):
    payload = {"action": "locate", "target": {"text": "bed", "relations": [
        {"relation": "inside", "target_text": "42", "target_node_type": "place"}]}}
    serve(monkeypatch, payload, OK)
    result = parse_intent_with_llm("Find bed inside place 42", config=CONFIG)
    assert result.target.relations[0].target_node_id == "42"


def test_unmentioned_numeric_relation_rejected():
    intent = TaskIntent.model_validate({"action": "locate", "target": {"text": "bed",
        "relations": [{"relation": "inside", "target_text": "place 99"}]}})
    with pytest.raises(ValueError, match="ID"):
        validate_and_canonicalize_intent("Find bed inside place 42", intent)


def test_implicit_sequence_is_audited(monkeypatch):
    command = "Find the bed. Go to the ward. Wait."
    calls = serve(monkeypatch, {"action": "wait"}, BAD, FULL, OK)
    result = parse_intent_with_llm(command, config=CONFIG)
    assert [step.action for step in result.steps] == ["locate", "navigate", "wait"]
    assert len(calls) == 4


def test_explicit_id_survives_relation_wording_variation():
    intent = TaskIntent.model_validate({"action": "locate", "target": {"text": "bed",
        "relations": [{"relation": "inside", "target_text": "Place ID 42"}]}})
    result = validate_and_canonicalize_intent("Find bed inside place 42", intent)
    assert len(result.target.relations) == 1
    assert result.target.relations[0].target_node_id == "42"


def test_failed_repair_keeps_raw_trace(monkeypatch):
    first = {"action": "wait"}
    serve(monkeypatch, first, BAD, first, BAD)
    with pytest.raises(ValueError) as caught:
        parse_intent_with_llm(COMMAND, config=CONFIG)
    assert caught.value.intent_parse_attempts[0]["raw_lmm_output"] == first
    assert len(caught.value.intent_parse_attempts) == 2


def test_auditor_receives_ontology(monkeypatch):
    calls = serve(monkeypatch, {"action": "locate", "target": {"text": "bed"}}, OK)
    parse_intent_with_llm("Find bed", config={**CONFIG, "scene_ontology": ["hospital_bed"]})
    assert json.loads(calls[1]["messages"][1]["content"])["scene_ontology"] == ["hospital_bed"]


def test_legacy_eval_makes_one_call(monkeypatch):
    calls = serve(monkeypatch, {"action": "wait"}, OK)
    result = parse_intent_with_llm("Wait.", config=CONFIG, eval_mode=True)
    assert result["raw_lmm_output"] == {"action": "wait"}
    assert len(calls) == 1


def test_production_callback_retains_parse_and_audit_raw(monkeypatch):
    parsed = {"action": "wait"}
    calls = serve(monkeypatch, parsed, OK)
    snapshots = []
    result = parse_intent_with_llm("Wait.", config=CONFIG, attempt_callback=snapshots.append)
    assert result.action == "wait"
    assert len(snapshots) == 2
    assert snapshots[0][0]["raw_lmm_output"] == parsed
    assert snapshots[0][0]["audit_status"] == "not_started"
    final = snapshots[-1][0]
    assert final["raw_content"] == json.dumps(parsed)
    assert final["audit_raw_content"] == json.dumps(OK)
    assert final["completeness_audit"] == OK
    assert final["request_counts"] == {"parse": 1, "audit": 1}
    assert len(calls) == 2


def test_callback_receives_independent_deep_copies(monkeypatch):
    parsed = {"action": "wait"}
    serve(monkeypatch, parsed, OK)
    snapshots = []

    def callback(attempts):
        snapshots.append(attempts)
        attempts[0]["raw_lmm_output"]["action"] = "unsupported"
        attempts[0]["request_counts"]["parse"] = 99

    result = parse_intent_with_llm("Wait.", config=CONFIG, eval_mode=True,
        attempt_callback=callback)
    assert result["raw_lmm_output"] == parsed
    assert result["intent_parse_attempts"][0]["request_counts"] == {"parse": 1, "audit": 0}
    assert snapshots[0][0]["raw_lmm_output"]["action"] == "unsupported"


def test_reparse_callback_retains_both_attempts(monkeypatch):
    first = {"action": "wait"}
    serve(monkeypatch, first, BAD, FULL, OK)
    snapshots = []
    # Sentence-separated commands are intentionally outside the CPU grammar;
    # opt-in model auditing retains its own complete repair/trace contract.
    result = parse_intent_with_llm("Find the bed. Go to the ward. Wait.", config=CONFIG,
                                  attempt_callback=snapshots.append)
    assert result.steps[-1].action == "wait"
    assert [len(snapshot) for snapshot in snapshots] == [1, 1, 2, 2]
    assert snapshots[0][0]["audit_status"] == "not_started"
    assert snapshots[-1][0]["audit_raw_content"] == json.dumps(BAD)
    assert snapshots[-1][1]["raw_lmm_output"] == FULL
    assert snapshots[-1][1]["audit_raw_content"] == json.dumps(OK)
    assert snapshots[-1][1]["request_counts"] == {"parse": 2, "audit": 2}


@pytest.mark.parametrize("phase", ["parse_transport", "parse_schema", "canonicalization",
                                    "audit_transport", "audit_schema", "completeness"])
def test_callback_retains_errors_and_partial_raw(monkeypatch, phase):
    import ollama
    calls = []
    snapshots = []
    payload = {"action": "wait"}
    if phase == "canonicalization":
        payload = {"action": "locate", "target": {"text": "bed", "relations": [
            {"relation": "inside", "target_text": "place 99"}]}}

    def chat(self, **kwargs):
        is_audit = "complete" in kwargs["format"].get("properties", {})
        calls.append(is_audit)
        if phase == ("audit_transport" if is_audit else "parse_transport"):
            raise RuntimeError("synthetic transport failure")
        raw = json.dumps(BAD if phase == "completeness" else OK) if is_audit else json.dumps(payload)
        if phase == ("audit_schema" if is_audit else "parse_schema"):
            raw = '{"unexpected": true}'
        return SimpleNamespace(message=SimpleNamespace(content=raw, thinking=None))

    monkeypatch.setattr(ollama.Client, "chat", chat)
    with pytest.raises((ValueError, RuntimeError)) as caught:
        parse_intent_with_llm("Find bed inside place 42", config=CONFIG,
                              attempt_callback=snapshots.append)
    final = snapshots[-1][-1]
    assert final["error_phase"] == phase
    assert final["error"]
    assert final["request_counts"] == {"parse": calls.count(False), "audit": calls.count(True)}
    assert caught.value.intent_parse_attempts == snapshots[-1]
    if phase != "parse_transport":
        assert final["raw_content"]
    if phase in ("audit_schema", "completeness"):
        assert final["audit_raw_content"]


def test_fast_path_callback_reports_zero_model_calls(monkeypatch):
    calls = serve(monkeypatch)
    snapshots = []
    result = parse_intent_with_llm("Go to node 8070450532247928832", config=CONFIG,
                                  attempt_callback=snapshots.append)
    assert result.target.text == "8070450532247928832"
    assert snapshots == [[]]
    assert calls == []


@pytest.mark.parametrize("command", [
    "First find node 8070450532247928832, then navigate to node 8070450532247928833",
    "Find node 8070450532247928832 then navigate to node 8070450532247928833",
    "Find node 8070450532247928832 and navigate to node 8070450532247928833",
])
def test_ordered_exact_nodes_use_parser_and_audit(monkeypatch, command):
    payload = {"action": "navigate", "steps": [
        {"action": "locate", "target": {"text": "8070450532247928832"}},
        {"action": "navigate", "target": {"text": "8070450532247928833"}}]}
    calls = serve(monkeypatch, payload, OK)
    result = parse_intent_with_llm(command, config=CONFIG)
    assert [step.action for step in result.steps] == ["locate", "navigate"]
    assert [step.target.text for step in result.steps] == [
        "8070450532247928832", "8070450532247928833"]
    assert len(calls) == 2
    assert calls[0]["messages"][1]["content"] == command
    assert "complete" in calls[1]["format"]["properties"]


@pytest.mark.parametrize("command", [
    "First find node 8070450532247928832",
    "Find node 8070450532247928832 then wait",
    "Find node 8070450532247928832 inside place 42",
])
def test_sequence_or_constraint_is_not_pure_exact_target(monkeypatch, command):
    payload = {"action": "locate", "target": {"text": "8070450532247928832"}}
    if "then wait" in command:
        payload["steps"] = [{"action": "locate", "target": payload["target"]}, {"action": "wait"}]
    calls = serve(monkeypatch, payload, OK)
    parse_intent_with_llm(command, config=CONFIG)
    assert len(calls) == 2


@pytest.mark.parametrize("command,action", [
    ("Find node 8070450532247928832", "locate"),
    ("Navigate to node 8070450532247928832.", "navigate"),
])
def test_pure_single_exact_target_remains_zero_calls(monkeypatch, command, action):
    calls = serve(monkeypatch)
    result = parse_intent_with_llm(command, config=CONFIG)
    assert result.action == action
    assert result.target.text == "8070450532247928832"
    assert calls == []


def test_audit_prompt_requires_json_contract(monkeypatch):
    calls = serve(monkeypatch, {"action": "wait"}, OK)
    parse_intent_with_llm("Wait.", config=CONFIG)
    prompt = calls[1]["messages"][0]["content"]
    assert "Output ONLY JSON" in prompt
    assert "Markdown" in prompt and "code fences" in prompt
    assert json.dumps(calls[1]["format"]) in prompt
    assert json.dumps(OK) in prompt
    assert json.dumps(BAD) in prompt


@pytest.mark.parametrize("invalid", [
    "### Audit Results: **APPROVED**\nAll requested actions are represented.",
    '```json\n{"complete":true,"ordered":true,"issues":[]}\n```',
    {"complete": "true", "ordered": True, "issues": []},
    {"complete": True, "ordered": True, "issues": [], "explanation": "approved"},
])
def test_invalid_audit_reparses_once_retaining_actual_raw(monkeypatch, invalid):
    payload = {"action": "wait"}
    calls = serve(monkeypatch, payload, invalid, payload, OK)
    snapshots = []
    result = parse_intent_with_llm("Wait.", config=CONFIG, attempt_callback=snapshots.append)
    assert result.action == "wait"
    first, last = snapshots[-1]
    assert first["audit_raw_content"] == (invalid if isinstance(invalid, str) else json.dumps(invalid))
    assert first["audit_status"] == "error" and first["error_phase"] == "audit_schema"
    assert first["completeness_audit"] is None
    assert first["request_counts"] == {"parse": 1, "audit": 1}
    assert last["request_counts"] == {"parse": 2, "audit": 2}
    assert last["audit_raw_content"] == json.dumps(OK)
    assert len(calls) == 4
    assert "audit_schema" in calls[2]["messages"][-1]["content"]


@pytest.mark.parametrize("first,second", [
    ("### APPROVED", "### APPROVED"),
    (BAD, "### APPROVED"),
    ("### APPROVED", BAD),
])
def test_audit_schema_and_semantic_failures_share_single_reparse_budget(monkeypatch, first, second):
    payload = {"action": "wait"}
    calls = serve(monkeypatch, payload, first, payload, second)
    snapshots = []
    with pytest.raises(ValueError) as caught:
        parse_intent_with_llm("Wait.", config=CONFIG, attempt_callback=snapshots.append)
    assert len(calls) == 4
    assert len(snapshots[-1]) == 2
    assert snapshots[-1][-1]["request_counts"] == {"parse": 2, "audit": 2}
    assert caught.value.intent_parse_attempts == snapshots[-1]
    assert snapshots[-1][-1]["error_phase"] == ("audit_schema" if isinstance(second, str) else "completeness")


# Synthetic instruction/response contracts for the v2 failure modes. These
# tests do not load GPU artifacts, annotations, scene databases, or gold data.
@pytest.mark.parametrize("command,payload,scope_rule", [
    ("First go to the bed, then navigate to room 5908722711110090753.",
     {"action": "navigate", "steps": [
         {"action": "navigate", "target": {"text": "bed"}},
         {"action": "navigate", "destination": {"text": "room 5908722711110090753"}}]},
     "Do not validate grounding, execution, or plausibility."),
    ("Go to the bed.", {"action": "navigate", "target": {"text": "bed"}},
     "Known-category targets require a usable label or explicit generic node_type"),
    ("First go to the bed, then locate the healthmonitor, then go to room 5908722711110090753.",
     {"action": "navigate", "steps": [
         {"action": "navigate", "target": {"text": "bed"}},
         {"action": "locate", "target": {"text": "healthmonitor"}},
         {"action": "navigate", "destination": {"text": "room 5908722711110090753"}}]},
     "Do not require locations or source that the user did not provide."),
    ("Go to the bed.", {"action": "navigate", "target": {"text": "bed"}},
     "An implicit current start is not a missing source."),
    ("Go to a television.", {"action": "navigate", "target": {"text": "a television"}},
     "Unknown entities are not unsupported actions."),
    ("Go to a television.", {"action": "navigate", "target": {"text": "a television"}},
     "An entity absent from the ontology is not an instruction coverage failure."),
    ("Pick up the bed.", {"action": "unsupported"},
     "A single unsupported action does not require target or steps."),
])
def test_audit_scope_excludes_grounding_requirements(monkeypatch, command, payload, scope_rule):
    calls = serve(monkeypatch, payload, OK)
    result = parse_intent_with_llm(command, config={**CONFIG, "scene_ontology": ["bed", "healthmonitor"]})
    assert result.action == payload["action"]
    assert [step.action for step in (result.steps or [])] == [
        step["action"] for step in payload.get("steps", [])]
    if result.target:
        assert result.target.text == payload["target"]["text"]
        assert result.target.label is None
    assert len(calls) == 2
    assert scope_rule.lower() in calls[1]["messages"][0]["content"].lower()


def test_audit_requests_bounded_short_issues(monkeypatch):
    calls = serve(monkeypatch, {"action": "wait"}, OK)
    parse_intent_with_llm("Wait.", config=CONFIG)
    issues_schema = calls[1]["format"]["properties"]["issues"]
    assert issues_schema.get("maxItems") == 3
    assert 0 < issues_schema["items"].get("maxLength", 0) <= 160
    assert "at most 3 short sentences" in calls[1]["messages"][0]["content"].lower()


@pytest.mark.parametrize("issues", [["Missing step"] * 4, ["x" * 161]])
def test_verbose_audit_is_invalid_and_shares_reparse_budget(monkeypatch, issues):
    verdict = {"complete": False, "ordered": True, "issues": issues}
    calls = serve(monkeypatch, {"action": "wait"}, verdict, {"action": "wait"}, OK)
    snapshots = []
    parse_intent_with_llm("Wait.", config=CONFIG, attempt_callback=snapshots.append)
    assert len(calls) == 4
    assert snapshots[-1][0]["audit_status"] == "error"
    assert snapshots[-1][0]["error_phase"] == "audit_schema"
    assert snapshots[-1][0]["audit_raw_content"] == json.dumps(verdict)
    assert snapshots[-1][-1]["request_counts"] == {"parse": 2, "audit": 2}


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_production_whole_json_fence_preserves_raw(monkeypatch, newline):
    payload = {"action": "navigate", "target": {"text": "bed"}}
    fenced = f"  ```json{newline}{json.dumps(payload)}{newline}```  "
    calls = serve(monkeypatch, fenced, OK)
    snapshots = []
    result = parse_intent_with_llm("Go to the bed.", config=CONFIG, attempt_callback=snapshots.append)
    assert result.action == "navigate" and result.target.text == "bed"
    record = snapshots[-1][0]
    assert record["parse_raw_content"] == fenced
    assert record["raw_content"] == fenced
    assert record["raw_lmm_output"] == payload
    assert record["parse_normalization"] == "json_code_fence"
    assert record["request_counts"] == {"parse": 1, "audit": 1}
    assert len(calls) == 2


def test_fenced_reparse_completes_without_third_attempt(monkeypatch):
    payload = {"action": "navigate", "steps": [
        {"action": "navigate", "target": {"text": "bed"}},
        {"action": "locate", "target": {"text": "healthmonitor"}},
        {"action": "navigate", "destination": {"text": "room 42"}}]}
    command = "First go to the bed, then locate healthmonitor, then go to room 42."
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    calls = serve(monkeypatch, payload, BAD, fenced, OK)
    snapshots = []
    result = parse_intent_with_llm(command, config=CONFIG, attempt_callback=snapshots.append)
    assert [step.action for step in result.steps] == ["navigate", "locate", "navigate"]
    assert snapshots[-1][0]["parse_raw_content"] == json.dumps(payload)
    assert snapshots[-1][1]["parse_raw_content"] == fenced
    assert snapshots[-1][1]["parse_normalization"] == "json_code_fence"
    assert snapshots[-1][1]["request_counts"] == {"parse": 2, "audit": 2}
    assert len(calls) == 4


@pytest.mark.parametrize("raw", [
    'Explanation\n```json\n{"action":"wait"}\n```',
    '```json\n{"action":"wait"}\n```\nExplanation',
    '```json\n{"action":"wait"}\n```\n```json\n{"action":"wait"}\n```',
    '```python\n{"action":"wait"}\n```',
    '```json\n{"action":"wait"',
    '```json\n{"action":"invented"}\n```',
    '```json\n{"action":"wait"} {"action":"wait"}\n```',
])
def test_fence_recovery_never_accepts_prose_partial_or_invalid_objects(monkeypatch, raw):
    calls = serve(monkeypatch, raw, raw)
    snapshots = []
    with pytest.raises(ValueError):
        parse_intent_with_llm("Wait.", config=CONFIG, attempt_callback=snapshots.append)
    assert snapshots[-1][0]["parse_raw_content"] == raw
    assert snapshots[-1][0]["error_phase"] == "parse_schema"
    assert snapshots[-1][0]["request_counts"] == {"parse": 1, "audit": 0}
    assert snapshots[-1][1]["parse_raw_content"] == raw
    assert snapshots[-1][1]["error_phase"] == "parse_schema"
    assert snapshots[-1][1]["request_counts"] == {"parse": 2, "audit": 0}
    assert len(calls) == 2


def test_legacy_eval_fence_is_not_raw_json_success(monkeypatch):
    fenced = '```json\n{"action":"wait"}\n```'
    calls = serve(monkeypatch, fenced)
    snapshots = []
    with pytest.raises(ValueError) as caught:
        parse_intent_with_llm("Wait.", config=CONFIG, eval_mode=True,
                              attempt_callback=snapshots.append)
    assert snapshots[-1][0]["raw_content"] == fenced
    assert snapshots[-1][0]["raw_lmm_output"] is None
    assert caught.value.intent_parse_attempts[0]["parse_raw_content"] == fenced
    assert len(calls) == 1


def serve_default_parses(monkeypatch, *payloads):
    """Keep old model audits approving so only the CPU contract can catch loss."""
    import ollama
    calls = []
    responses = iter(payloads)

    def chat(self, **kwargs):
        calls.append(kwargs)
        audit = "complete" in kwargs["format"].get("properties", {})
        payload = OK if audit else next(responses)
        return SimpleNamespace(message=SimpleNamespace(
            content=payload if isinstance(payload, str) else json.dumps(payload), thinking=None))

    monkeypatch.setattr(ollama.Client, "chat", chat)
    return calls


@pytest.mark.parametrize("command,payload", [
    ("Go to a television.", {"action": "navigate", "target": {"text": "a television"}}),
    ("Pick up the bed.", {"action": "unsupported"}),
    ("Go to the room with most desks and computers.", {"action": "navigate", "target": {
        "text": "room with most desks and computers", "aggregate": {
            "operation": "max_count", "target_labels": ["desks", "computers"]}}}),
    ("Escort the patient from reception to the ward.", {"action": "escort",
        "actor": {"text": "patient"}, "source": {"text": "reception"},
        "destination": {"text": "ward"}}),
])
def test_default_single_step_never_calls_audit(monkeypatch, command, payload):
    calls = serve_default_parses(monkeypatch, payload)
    snapshots = []
    result = parse_intent_with_llm(command, config={**DEFAULT_CONFIG,
        "scene_ontology": ["bed", "desks", "computers"]}, attempt_callback=snapshots.append)
    assert result.action == payload["action"]
    if payload.get("target"):
        assert result.target.text == payload["target"]["text"]
    assert len(calls) == 1
    assert snapshots[-1][-1]["request_counts"] == {"parse": 1, "audit": 0}
    assert snapshots[-1][-1]["audit_status"] == "not_started"
    assert snapshots[-1][-1]["completeness_audit"] is None


@pytest.mark.parametrize("separator", [", then ", " and then ", ", next ",
                                        ", finally ", "; ", " and "])
@pytest.mark.parametrize("partial", [
    {"action": "wait"},
    {"action": "wait", "steps": [FULL["steps"][0], FULL["steps"][2]]},
    {"action": "wait", "steps": list(reversed(FULL["steps"]))},
    {"action": "wait", "steps": [*FULL["steps"], {"action": "wait"}]},
])
def test_default_explicit_action_sequence_reparses_loss_swap_and_extra(monkeypatch, separator, partial):
    command = "First find the bed" + separator + "go to the ward" + separator + "wait."
    calls = serve_default_parses(monkeypatch, partial, FULL)
    snapshots = []
    result = parse_intent_with_llm(command, config=DEFAULT_CONFIG, attempt_callback=snapshots.append)
    assert [step.action for step in result.steps] == ["locate", "navigate", "wait"]
    assert len(calls) == 2
    assert all("complete" not in call["format"].get("properties", {}) for call in calls)
    first, last = snapshots[-1]
    assert first["instruction_sequence_check"]["status"] == "mismatch"
    assert first["instruction_sequence_check"]["expected_actions"] == ["locate", "navigate", "wait"]
    assert last["instruction_sequence_check"]["status"] == "match"
    assert last["instruction_sequence_check"]["actual_actions"] == ["locate", "navigate", "wait"]
    assert last["request_counts"] == {"parse": 2, "audit": 0}
    assert all(command in str(call["messages"]) for call in calls)
    feedback = calls[1]["messages"][-1]["content"]
    assert "locate" in feedback and "navigate" in feedback and "wait" in feedback


def test_default_persistent_sequence_loss_fails_after_two_parses(monkeypatch):
    partial = {"action": "wait"}
    calls = serve_default_parses(monkeypatch, partial, partial)
    snapshots = []
    with pytest.raises(ValueError, match="sequence") as caught:
        parse_intent_with_llm(COMMAND, config=DEFAULT_CONFIG, attempt_callback=snapshots.append)
    assert len(calls) == 2
    assert snapshots[-1][-1]["error_phase"] == "instruction_sequence"
    assert snapshots[-1][-1]["raw_lmm_output"] == partial
    assert snapshots[-1][-1]["request_counts"] == {"parse": 2, "audit": 0}
    assert caught.value.intent_parse_attempts == snapshots[-1]


@pytest.mark.parametrize("verb,action", [
    ("navigate to", "navigate"), ("go to", "navigate"), ("move to", "navigate"),
    ("find", "locate"), ("locate", "locate"), ("search for", "locate"),
    ("escort", "escort"), ("guide", "guide"), ("avoid", "avoid"),
    ("pick up", "unsupported"), ("grasp", "unsupported"), ("carry", "unsupported"),
])
def test_default_explicit_clause_action_aliases(monkeypatch, verb, action):
    command = f"First {verb} the bed, then wait."
    payload = {"action": "wait", "steps": [{"action": action}, {"action": "wait"}]}
    calls = serve_default_parses(monkeypatch, payload)
    snapshots = []
    result = parse_intent_with_llm(command, config=DEFAULT_CONFIG, attempt_callback=snapshots.append)
    assert [step.action for step in result.steps] == [action, "wait"]
    diagnostic = snapshots[-1][-1]["instruction_sequence_check"]
    assert diagnostic["status"] == "match"
    assert diagnostic["expected_actions"] == [action, "wait"]
    assert len(calls) == 1


@pytest.mark.parametrize("command", [
    "Go to the room with desks and computers, then wait.",
    "Go to the nearest chair and desk; wait.",
])
def test_default_noun_conjunctions_do_not_add_actions(monkeypatch, command):
    payload = {"action": "wait", "steps": [
        {"action": "navigate", "target": {"text": "room with desks and computers"}},
        {"action": "wait"}]}
    calls = serve_default_parses(monkeypatch, payload)
    snapshots = []
    parse_intent_with_llm(command, config=DEFAULT_CONFIG, attempt_callback=snapshots.append)
    assert snapshots[-1][-1]["instruction_sequence_check"]["expected_actions"] == ["navigate", "wait"]
    assert len(calls) == 1


@pytest.mark.parametrize("command", [
    "Before going to the ward, find the bed.",
    "Find the bed after going to the ward.",
    "Find the bed, then inspect the monitor, then wait.",
    "Please perform the required checks and proceed as appropriate.",
])
def test_default_unsupported_sequence_grammar_is_unverified_not_reordered(monkeypatch, command):
    payload = {"action": "navigate", "steps": [
        {"action": "navigate", "target": {"text": "ward"}},
        {"action": "locate", "target": {"text": "bed"}}]}
    calls = serve_default_parses(monkeypatch, payload)
    snapshots = []
    result = parse_intent_with_llm(command, config=DEFAULT_CONFIG, attempt_callback=snapshots.append)
    assert result.model_dump(mode="json") == TaskIntent.model_validate(payload).model_dump(mode="json")
    assert snapshots[-1][-1]["instruction_sequence_check"]["status"] == "unverified"
    assert len(calls) == 1


def test_default_known_same_action_target_swap_reparses(monkeypatch):
    command = "First go to the bed, then go to the ward."
    payload = {"action": "navigate", "steps": [
        {"action": "navigate", "target": {"text": "ward"}},
        {"action": "navigate", "target": {"text": "bed"}}]}
    corrected = {"action": "navigate", "steps": list(reversed(payload["steps"]))}
    calls = serve_default_parses(monkeypatch, payload, corrected)
    snapshots = []
    result = parse_intent_with_llm(command, config=DEFAULT_CONFIG, attempt_callback=snapshots.append)
    assert [step.target.text for step in result.steps] == ["bed", "ward"]
    assert snapshots[-1][0]["instruction_sequence_check"]["status"] == "mismatch"
    assert snapshots[-1][-1]["instruction_sequence_check"]["expected_actions"] == ["navigate", "navigate"]
    assert snapshots[-1][-1]["instruction_sequence_check"]["status"] == "match"
    assert len(calls) == 2


def test_default_exact_single_target_keeps_zero_calls(monkeypatch):
    calls = serve_default_parses(monkeypatch)
    result = parse_intent_with_llm("Find node 8070450532247928832", config=DEFAULT_CONFIG)
    assert result.target.text == "8070450532247928832"
    assert calls == []


def test_default_fence_relation_provenance_ontology_and_trace_remain(monkeypatch):
    payload = {"action": "locate", "target": {"text": "alien_scanner", "label": "beds",
        "relations": [{"relation": "inside", "target_text": "place 42"}]}}
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    calls = serve_default_parses(monkeypatch, fenced)
    snapshots = []
    result = parse_intent_with_llm("Find alien_scanner inside place 42", config={
        **DEFAULT_CONFIG, "scene_ontology": ["bed"]}, attempt_callback=snapshots.append)
    assert result.target.label is None and result.target.text == "alien_scanner"
    assert result.target.relations[0].target_node_id == "42"
    assert "CURRENT SCENE ONTOLOGY" in calls[0]["messages"][0]["content"]
    assert snapshots[-1][-1]["parse_raw_content"] == fenced
    assert snapshots[-1][-1]["parse_normalization"] == "json_code_fence"
    assert snapshots[-1][-1]["request_counts"] == {"parse": 1, "audit": 0}
    assert len(calls) == 1


def test_eval_production_optin_does_not_enable_eval_audit_or_cpu_repair(monkeypatch):
    calls = serve_default_parses(monkeypatch, {"action": "wait"})
    result = parse_intent_with_llm(COMMAND, config=CONFIG, eval_mode=True)
    assert result["raw_lmm_output"] == {"action": "wait"}
    assert result["canonicalized_intent"].steps is None
    assert result["intent_parse_attempts"][-1]["request_counts"] == {"parse": 1, "audit": 0}
    assert len(calls) == 1


def test_default_locate_target_surface_swap_reparses(monkeypatch):
    command = "First locate the bed, then locate the healthmonitor."
    swapped = {"action": "locate", "steps": [
        {"action": "locate", "target": {"text": " THE HEALTH MONITOR "}},
        {"action": "locate", "target": {"text": "a BED"}}]}
    correct = {"action": "locate", "steps": list(reversed(swapped["steps"]))}
    calls = serve_default_parses(monkeypatch, swapped, correct)
    snapshots = []
    result = parse_intent_with_llm(command, config=DEFAULT_CONFIG, attempt_callback=snapshots.append)
    assert result.steps[0].target.text == "a BED"
    assert snapshots[-1][0]["instruction_sequence_check"]["status"] == "mismatch"
    assert snapshots[-1][-1]["instruction_sequence_check"]["status"] == "match"
    assert len(calls) == 2


@pytest.mark.parametrize("kind", ["room", "place"])
def test_default_explicit_location_id_swap_reparses(monkeypatch, kind):
    command = f"First go to {kind} 8070450532247928832, then go to {kind} 8070450532247928833."
    swapped = {"action": "navigate", "steps": [
        {"action": "navigate", "destination": {"text": "location Beta", "room": "8070450532247928833"}},
        {"action": "navigate", "target": {"text": "location Alpha", "locations": [
            {"text": "location", "node_id": "8070450532247928832", "node_type": kind}]}}]}
    correct = {"action": "navigate", "steps": list(reversed(swapped["steps"]))}
    calls = serve_default_parses(monkeypatch, swapped, correct)
    result = parse_intent_with_llm(command, config=DEFAULT_CONFIG)
    assert result.steps[0].target.locations[0].node_id == "8070450532247928832"
    assert len(calls) == 2


def test_default_unmatched_target_aliases_are_not_strongly_rejected(monkeypatch):
    command = "First locate the bed, then locate the healthmonitor."
    payload = {"action": "locate", "steps": [
        {"action": "locate", "target": {"text": "sleeping furniture"}},
        {"action": "locate", "target": {"text": "vital signs display"}}]}
    calls = serve_default_parses(monkeypatch, payload)
    result = parse_intent_with_llm(command, config=DEFAULT_CONFIG)
    assert result.steps[0].target.text == "sleeping furniture"
    assert result.steps[1].target.text == "vital signs display"
    assert len(calls) == 1


def test_default_repeated_target_and_previous_source_are_not_wrong_order(monkeypatch):
    command = "First locate bed, then go to bed, then go to ward."
    payload = {"action": "navigate", "steps": [
        {"action": "locate", "target": {"text": "bed"}},
        {"action": "navigate", "target": {"text": "bed"}},
        {"action": "navigate", "source": {"text": "bed"}, "destination": {"text": "ward"}}]}
    calls = serve_default_parses(monkeypatch, payload)
    result = parse_intent_with_llm(command, config=DEFAULT_CONFIG)
    assert result.steps[-1].source.text == "bed"
    assert len(calls) == 1


def test_known_location_digits_also_present_in_own_clause_are_not_wrong_order(monkeypatch):
    command = "First find node 8070450532247928832, then go to room 8070450532247928832."
    payload = {"action": "navigate", "steps": [
        {"action": "locate", "target": {"text": "8070450532247928832"}},
        {"action": "navigate", "destination": {"text": "room 8070450532247928832"}}]}
    calls = serve_default_parses(monkeypatch, payload, payload)
    result = parse_intent_with_llm(command, config=DEFAULT_CONFIG)
    assert result.steps[0].target.text == "8070450532247928832"
    assert len(calls) == 1


@pytest.mark.parametrize("sequence_first", [True, False])
def test_cpu_sequence_and_optional_audit_share_one_reparse_budget(monkeypatch, sequence_first):
    partial = {"action": "wait"}
    payloads = (partial, FULL, BAD) if sequence_first else (FULL, BAD, partial)
    calls = serve(monkeypatch, *payloads)
    snapshots = []
    with pytest.raises(ValueError) as caught:
        parse_intent_with_llm(COMMAND, config=CONFIG, attempt_callback=snapshots.append)
    assert len(calls) == 3
    assert snapshots[-1][-1]["request_counts"] == {"parse": 2, "audit": 1}
    assert caught.value.intent_parse_attempts == snapshots[-1]
