"""Offline contract tests for task-chain planning with deferred instance selection."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main_rag as planner  # noqa: E402
from grounding.orchestrator import GroundingResult  # noqa: E402
from api_schemas import ResolutionCandidate, ResolutionResult  # noqa: E402
from planner_schemas.intent import EntityReference, TaskIntent  # noqa: E402
from schema import TaskPlan, TaskStep  # noqa: E402


NODE_IDS = ["stairs-a", "stairs-b", "stairs-c"]


def ambiguous_stairs_grounding():
    grounding = GroundingResult()
    candidates = [
        ResolutionCandidate(
            node_id=node_id,
            node_type="object",
            label="stairs",
            room_id=f"room-{index}",
            place_id=f"place-{index}",
            position=[float(index), 0.0, 0.0],
        )
        for index, node_id in enumerate(NODE_IDS)
    ]
    grounding.add_result("target", ResolutionResult(
        status="ambiguous",
        reference=EntityReference(text="stairs", label="stairs"),
        candidates=candidates,
        reason="Multiple stairs instances",
    ))
    return grounding


class FakeRag:
    def get_navigation_target(self, node_id):
        suffix = node_id.rsplit("-", 1)[-1]
        return SimpleNamespace(
            navigation_node_id=f"nav-place-{suffix}",
            position=[float(ord(suffix[0]) % 5), 2.0, 0.0],
            frame_id="map",
            goal_type="place",
        )


def test_ambiguous_reference_is_preserved_as_candidate_set_in_grounded_task():
    grounding = ambiguous_stairs_grounding()
    intent = TaskIntent(action="navigate", target=EntityReference(text="stairs", label="stairs"))

    grounded = planner._build_grounded_task(intent, grounding, FakeRag())

    target = grounded["references"]["target"]
    assert target["candidate_node_ids"] == NODE_IDS
    assert target["candidate_count"] == len(NODE_IDS)
    assert target["selection_policy"] == "deferred_to_nav2"
    assert [item["node_id"] for item in target["candidate_navigation_targets"]] == NODE_IDS
    assert grounded["instance_selection_deferred"] is True


def test_task_step_carries_candidates_without_selecting_an_instance():
    step = TaskStep(
        step=1,
        action="navigate_to",
        target_type="object",
        target_ref="target",
        target_id=None,
        candidate_node_ids=NODE_IDS,
        selection_policy="deferred_to_nav2",
        description="go to stairs",
        executor="nav2",
    )

    assert step.target_id is None
    assert step.candidate_node_ids == NODE_IDS
    assert step.selection_policy == "deferred_to_nav2"


def test_resolving_candidate_reference_does_not_expand_one_action_into_many():
    grounded = {
        "references": {
            "target": {
                "candidate_node_ids": NODE_IDS,
                "selection_policy": "deferred_to_nav2",
            }
        },
        "steps": [],
        "constraints": [],
    }
    plan = TaskPlan(status="valid", task_chain=[TaskStep(
        step=1,
        action="navigate_to",
        target_type="object",
        target_ref="target",
        target_id=None,
        description="go to stairs",
        executor="nav2",
    )])

    resolved = planner._resolve_target_refs(plan, grounded)

    assert len(resolved.task_chain) == 1
    assert resolved.task_chain[0].target_id is None
    assert resolved.task_chain[0].candidate_node_ids == NODE_IDS
    assert resolved.task_chain[0].selection_policy == "deferred_to_nav2"


def test_role_ref_only_navigation_preserves_deferred_candidates():
    grounded = {
        "references": {"target": {
            "candidate_node_ids": NODE_IDS,
            "selection_policy": "deferred_to_nav2",
        }},
        "steps": [],
        "constraints": [],
    }
    plan = TaskPlan(status="valid", task_chain=[TaskStep(
        step=1, action="navigate_to", target_type="object", target_ref="",
        role_refs={"target": "target"}, target_id=None,
        description="go to stairs", executor="nav2",
    )])

    step = planner._resolve_target_refs(plan, grounded).task_chain[0]

    assert step.candidate_node_ids == NODE_IDS
    assert step.selection_policy == "deferred_to_nav2"
    assert step.target_id is None


def test_multistep_candidate_references_remain_one_ordered_step_each():
    grounded = {
        "references": {},
        "steps": [
            {"references": {"target": {"candidate_node_ids": ["bed-a", "bed-b"]}}},
            {"references": {"destination": {"candidate_node_ids": ["screen-a"]}}},
        ],
        "constraints": [],
    }
    plan = TaskPlan(status="valid", task_chain=[
        TaskStep(step=1, action="navigate_to", target_type="object",
                 target_ref="step_0_target", target_id=None,
                 description="first stop", executor="nav2"),
        TaskStep(step=2, action="navigate_to", target_type="object",
                 target_ref="step_1_destination", target_id=None,
                 description="second stop", executor="nav2"),
    ])

    resolved = planner._resolve_target_refs(plan, grounded)

    assert len(resolved.task_chain) == 2
    assert [step.target_ref for step in resolved.task_chain] == [
        "step_0_target", "step_1_destination"
    ]
    assert [step.candidate_node_ids for step in resolved.task_chain] == [
        ["bed-a", "bed-b"], ["screen-a"]
    ]
    assert all(step.target_id is None for step in resolved.task_chain)


def test_candidate_chain_validates_exact_set_and_rejects_singular_target_id():
    grounding = ambiguous_stairs_grounding()
    grounded_task = {
        "references": {
            "target": {
                "candidate_node_ids": NODE_IDS,
                "selection_policy": "deferred_to_nav2",
                "label": "stairs",
                "node_type": "object",
            }
        },
        "steps": [],
        "constraints": [],
    }
    plan = TaskPlan(status="valid", task_chain=[TaskStep(
        step=1,
        action="navigate_to",
        target_type="object",
        target_ref="target",
        candidate_node_ids=NODE_IDS,
        selection_policy="deferred_to_nav2",
        description="go to stairs",
        executor="nav2",
    )])

    assert planner._validate_grounded_plan(plan, grounding, grounded_task) == []

    plan.task_chain[0].target_id = NODE_IDS[0]
    errors = planner._validate_grounded_plan(plan, grounding, grounded_task)
    assert any("deferred" in error and "target_id" in error for error in errors)


def test_step_specific_candidate_sets_validate_without_collapsing_chain():
    grounding = GroundingResult()
    references = {
        "step_0_target": ("bed-a", "bed-b"),
        "step_1_destination": ("screen-a", "screen-b"),
    }
    grounded_steps = []
    for index, (role, ids) in enumerate((
        ("target", references["step_0_target"]),
        ("destination", references["step_1_destination"]),
    )):
        candidates = [ResolutionCandidate(node_id=node_id, node_type="object", label="object")
                      for node_id in ids]
        grounding.add_result(f"step_{index}_{role}", ResolutionResult(
            status="ambiguous", reference=EntityReference(text=role), candidates=candidates,
        ))
        grounded_steps.append({"references": {role: {
            "candidate_node_ids": list(ids), "label": "object", "node_type": "object",
            "selection_policy": "deferred_to_nav2",
        }}})
    grounded_task = {"steps": grounded_steps, "references": {}, "constraints": []}
    plan = TaskPlan(status="valid", task_chain=[
        TaskStep(step=1, action="navigate_to", target_type="object",
                 target_ref="step_0_target", candidate_node_ids=list(references["step_0_target"]),
                 selection_policy="deferred_to_nav2", description="first", executor="nav2"),
        TaskStep(step=2, action="navigate_to", target_type="object",
                 target_ref="step_1_destination", candidate_node_ids=list(references["step_1_destination"]),
                 selection_policy="deferred_to_nav2", description="second", executor="nav2"),
    ])

    assert planner._validate_grounded_plan(plan, grounding, grounded_task) == []


def test_multirole_candidate_destination_does_not_require_singular_role_id():
    grounding = GroundingResult()
    actor = ResolutionCandidate(node_id="person-a", node_type="agent", label="person")
    destinations = [ResolutionCandidate(node_id=node_id, node_type="place", label="entrance")
                    for node_id in ("entry-a", "entry-b")]
    grounding.add_result("actor", ResolutionResult(
        status="resolved", reference=EntityReference(text="patient"), selected=actor,
    ))
    grounding.add_result("destination", ResolutionResult(
        status="ambiguous", reference=EntityReference(text="entrance"), candidates=destinations,
    ))
    grounded_task = {"references": {
        "actor": {"node_id": "person-a", "node_type": "agent", "label": "person"},
        "destination": {"candidate_node_ids": ["entry-a", "entry-b"],
                        "selection_policy": "deferred_to_nav2", "label": "entrance",
                        "node_type": "place"},
    }, "steps": [], "constraints": []}
    plan = TaskPlan(status="valid", task_chain=[TaskStep(
        step=1, action="escort_to", target_type="person", target_ref="",
        role_refs={"actor": "actor", "destination": "destination"},
        role_ids={"actor": "person-a"},
        role_candidate_node_ids={"destination": ["entry-a", "entry-b"]},
        candidate_node_ids=["entry-a", "entry-b"], selection_policy="deferred_to_nav2",
        target_id=None, description="escort to entrance", executor="nav2",
    )])

    assert planner._validate_grounded_plan(plan, grounding, grounded_task) == []


def test_candidate_set_validator_compares_against_raw_rag_grounding():
    grounding = ambiguous_stairs_grounding()
    grounded_task = {
        "references": {"target": {
            "candidate_node_ids": NODE_IDS[:-1], "selection_policy": "deferred_to_nav2",
            "label": "stairs", "node_type": "object",
        }},
        "steps": [], "constraints": [],
    }
    plan = TaskPlan(status="valid", task_chain=[TaskStep(
        step=1, action="navigate_to", target_type="object", target_ref="target",
        candidate_node_ids=NODE_IDS[:-1], selection_policy="deferred_to_nav2",
        target_id=None, description="stairs", executor="nav2",
    )])

    errors = planner._validate_grounded_plan(plan, grounding, grounded_task)

    assert any("RAG grounding" in error for error in errors)


def test_resolved_singleton_must_match_raw_rag_grounding():
    grounding = GroundingResult()
    grounding.add_result("target", ResolutionResult(
        status="resolved",
        reference=EntityReference(text="chair", label="chair"),
        selected=ResolutionCandidate(node_id="actual-chair", node_type="object", label="chair"),
    ))
    grounded_task = {"references": {"target": {
        "node_id": "invented-chair", "node_type": "object", "label": "chair",
    }}, "steps": [], "constraints": []}
    plan = TaskPlan(status="valid", task_chain=[TaskStep(
        step=1, action="navigate_to", target_type="object", target_ref="target",
        target_id="invented-chair", description="go to chair", executor="nav2",
    )])

    errors = planner._validate_grounded_plan(plan, grounding, grounded_task)

    assert any("Resolved IDs" in error and "raw RAG grounding" in error for error in errors)


def test_ambiguous_rag_reference_cannot_be_collapsed_to_single_grounded_id():
    grounding = ambiguous_stairs_grounding()
    grounded_task = {
        "references": {"target": {
            "node_id": NODE_IDS[0], "label": "stairs", "node_type": "object",
        }},
        "steps": [], "constraints": [],
    }
    plan = TaskPlan(status="valid", task_chain=[TaskStep(
        step=1, action="navigate_to", target_type="object", target_ref="target",
        target_id=NODE_IDS[0], description="stairs", executor="nav2",
    )])

    errors = planner._validate_grounded_plan(plan, grounding, grounded_task)

    assert any("candidate set" in error and "RAG grounding" in error for error in errors)


def test_llm_semantic_context_exposes_grounded_ids_but_not_navigation_positions():
    grounded = {
        "action": "navigate",
        "description": "untrusted free text includes map pose 88.1,42.2 and a node identifier",
        "references": {"target": {
            "node_id": "private-node-1",
            "node_ids": ["private-node-2"],
            "selected_many": [{"node_id": "private-node-3", "label": "stairs"}],
            "label": "stairs",
            "node_type": "object",
            "candidate_count": len(NODE_IDS),
            "candidate_node_ids": NODE_IDS,
                "selection_policy": "deferred_to_nav2",
            "candidate_navigation_targets": [{
                "node_id": NODE_IDS[0],
                "navigation_target": {"position": [12.5, 4.0, 0.0]},
            }],
        }},
        "constraints": [{"key": "constraint_avoid_0", "type": "avoid",
                         "node_id": "private-node-4", "label": "wall"}],
        "steps": [],
    }

    context = planner._planner_semantic_context(grounded)

    reference = context["references"]["target"]
    assert reference["candidate_node_ids"] == NODE_IDS
    assert reference["candidate_navigation_targets"] == [{"node_id": NODE_IDS[0]}]
    assert reference["selection_policy"] == "deferred_to_nav2"
    assert context["constraints"] == [{
        "key": "constraint_avoid_0", "type": "avoid", "node_id": "private-node-4", "label": "wall",
    }]
    serialized = str(context)
    assert all(node_id in serialized for node_id in NODE_IDS)
    assert "private-node-1" in serialized and "private-node-2" in serialized
    assert "88.1" not in serialized and "node identifier" not in serialized
    assert "12.5" not in serialized


def test_intent_plan_prompt_contract_never_exposes_grounded_node_ids():
    import json

    grounded = {
        "action": "navigate",
        "requested_roles": ["target"],
        "references": {"target": {
            "node_id": "private-target-id", "label": "chair", "node_type": "object",
        }},
        "constraints": [{
            "key": "constraint_avoid_0", "type": "avoid",
            "node_id": "private-obstacle-id", "label": "wall",
        }],
        "steps": [],
    }

    contract = planner._intent_plan_prompt_contract(grounded)
    serialized = json.dumps(contract)

    assert "private-target-id" not in serialized
    assert "private-obstacle-id" not in serialized
    assert contract[0]["ref"] == "target"


def test_planner_prompt_treats_candidate_set_role_as_grounded(monkeypatch):
    import json
    import ollama

    captured = {}
    response = {"status": "valid", "task_chain": [{
        "step": 1, "action": "navigate_to", "target_type": "object",
        "target_ref": "target", "target_id": None, "candidate_node_ids": NODE_IDS,
        "selection_policy": "deferred_to_nav2", "description": "go to stairs", "executor": "nav2",
        "constraints": {"avoid": [], "prefer": [], "conditions": []},
    }]}

    def fake_client(**kwargs):
        return SimpleNamespace(chat=lambda **call: captured.update(call) or SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(response))))

    monkeypatch.setattr(ollama, "Client", fake_client)
    grounded = {
        "action": "navigate", "requested_roles": ["target"],
        "references": {"target": {
            "node_type": "object", "label": "stairs", "candidate_count": len(NODE_IDS),
            "candidate_node_ids": NODE_IDS, "selection_policy": "deferred_to_nav2",
        }},
        "steps": [], "constraints": [],
    }

    planner._generate_grounded_plan("Navigate to the stairs.", grounded, "fake-model", {})

    prompt = "\n".join(message["content"] for message in captured["messages"])
    assert "candidate set is a valid grounded role" in prompt
    assert "Keep one semantic action" in prompt
    assert all(node_id in prompt for node_id in NODE_IDS)
    assert "FORMAT-ONLY SYNTHETIC EXAMPLE" in prompt
    assert "Choose exactly one target-resolution mode" in prompt
    assert "UNIQUE: target_id is the exact grounded ID" in prompt
    assert "AMBIGUOUS: target_id=null" in prompt
    assert "candidate_node_ids must equal the complete candidate set for that target_ref" in prompt


def test_repair_prompt_reuses_grounded_ids_and_synthetic_example(monkeypatch):
    import json
    import ollama

    captured = {}
    response = {"status": "valid", "task_chain": [{
        "step": 1, "action": "navigate_to", "target_type": "object",
        "target_ref": "target", "target_id": None,
        "candidate_node_ids": ["secret-node-a", "secret-node-b"],
        "selection_policy": "deferred_to_nav2", "description": "go to stairs", "executor": "nav2",
        "constraints": {"avoid": [], "prefer": [], "conditions": []},
    }]}

    def chat(self, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(response)))

    monkeypatch.setattr(ollama.Client, "chat", chat)
    grounded = {
        "action": "navigate", "requested_roles": ["target"],
        "references": {"target": {
            "node_type": "object", "label": "stairs", "candidate_count": 2,
            "candidate_node_ids": ["secret-node-a", "secret-node-b"],
            "candidate_navigation_targets": [{"node_id": "secret-node-a",
                                               "navigation_target": {"position": [4.0, 5.0]}}],
            "selection_policy": "deferred_to_nav2",
        }, "source": {"node_id": "private-singleton", "label": "corridor",
                      "node_type": "place"}},
        "steps": [], "constraints": [{
            "key": "constraint_avoid_0", "type": "avoid",
            "node_id": "secret-obstacle", "label": "wall",
        }],
    }

    raw_draft = {"status": "valid", "task_chain": [{
        "step": 1, "action": "navigate_to", "target_ref": "unlisted-node-id",
        "target_id": "unlisted-node-id", "description": "unlisted-node-id",
    }]}
    planner._repair_plan(TaskPlan(status="valid", task_chain=[]),
                         ["Invalid target_id unlisted-node-id; candidate ID mismatch"],
                         grounded, "fake-model", {}, raw_draft=raw_draft)

    prompt = "\n".join(message["content"] for message in captured["messages"])
    assert "secret-node-a" in prompt and "secret-node-b" in prompt
    assert "unlisted-node-id" not in prompt
    assert "private-singleton" in prompt
    assert "secret-obstacle" in prompt
    assert "candidate_count" in prompt
    assert "FORMAT-ONLY SYNTHETIC EXAMPLE" in prompt
    assert "Choose exactly one target-resolution mode" in prompt
    assert "UNIQUE: target_id is the exact grounded ID" in prompt
    assert "AMBIGUOUS: target_id=null" in prompt
    assert "candidate_node_ids must equal the complete candidate set for that target_ref" in prompt


def test_resolver_preserves_llm2_selected_grounded_id():
    grounded = {"references": {"target": {"node_ids": ["bed-a", "bed-b"]}},
                "steps": [], "constraints": []}
    plan = TaskPlan(status="valid", task_chain=[TaskStep(
        step=1, action="navigate_to", target_type="object", target_ref="target",
        target_id="bed-b", description="navigate to selected bed", executor="nav2",
    )])

    resolved = planner._resolve_target_refs(plan, grounded)

    assert len(resolved.task_chain) == 1
    assert resolved.task_chain[0].target_id == "bed-b"


def test_resolver_rejects_llm2_id_outside_selected_grounded_reference():
    grounded = {"references": {"target": {"node_ids": ["bed-a", "bed-b"]}},
                "steps": [], "constraints": []}
    plan = TaskPlan(status="valid", task_chain=[TaskStep(
        step=1, action="navigate_to", target_type="object", target_ref="target",
        target_id="room-invented", description="bad target", executor="nav2",
    )])

    with pytest.raises(ValueError, match="does not belong"):
        planner._resolve_target_refs(plan, grounded)


def test_pipeline_generates_valid_chain_while_deferring_candidate_selection(monkeypatch):
    grounding = ambiguous_stairs_grounding()
    intent = TaskIntent(action="navigate", target=EntityReference(text="stairs", label="stairs"))
    fake_rag = FakeRag()
    plan = TaskPlan(status="valid", task_chain=[TaskStep(
        step=1,
        action="navigate_to",
        target_type="object",
        target_ref="target",
        target_id=None,
        description="go to stairs",
        executor="nav2",
    )])
    monkeypatch.setattr(planner, "load_config", lambda *args: {})
    monkeypatch.setattr(planner, "get_default_model", lambda config: "fake-model")
    monkeypatch.setattr(planner, "SceneGraphRAG", lambda db_dir: fake_rag)
    monkeypatch.setattr(planner, "parse_intent_with_llm", lambda *args, **kwargs: intent)
    monkeypatch.setattr(planner, "GroundingOrchestrator", lambda rag: SimpleNamespace(
        ground_intent=lambda value: grounding
    ))
    monkeypatch.setattr(planner, "_generate_grounded_plan", lambda *args, **kwargs: plan)

    result = planner.run_structured_task_planner("Navigate to stairs.", db_dir="unused")

    assert result.status == "valid"
    assert result.grounding["status"] == "ambiguous"
    assert result.grounded_task["instance_selection_deferred"] is True
    step = result.task_plan["task_chain"][0]
    assert step["candidate_node_ids"] == NODE_IDS
    assert step["target_id"] is None


def test_only_candidate_backed_ambiguity_can_continue_to_task_chain():
    grounding = ambiguous_stairs_grounding()
    assert planner._can_defer_instance_selection(grounding) is True

    no_candidates = GroundingResult()
    no_candidates.add_result("target", ResolutionResult(
        status="ambiguous",
        reference=EntityReference(text="stairs", label="stairs"),
        candidates=[],
        reason="No candidate details",
    ))
    assert planner._can_defer_instance_selection(no_candidates) is False

    not_found = GroundingResult()
    not_found.add_result("target", ResolutionResult(
        status="not_found",
        reference=EntityReference(text="defibrillator", label="defibrillator"),
        reason="No matching class",
    ))
    assert planner._can_defer_instance_selection(not_found) is False

    invalid_candidate = GroundingResult()
    invalid_candidate.add_result("target", ResolutionResult(
        status="ambiguous",
        reference=EntityReference(text="stairs", label="stairs"),
        candidates=[ResolutionCandidate(node_id="", node_type="object", label="stairs")],
    ))
    assert planner._can_defer_instance_selection(invalid_candidate) is False

    duplicate_candidates = GroundingResult()
    duplicate_candidates.add_result("target", ResolutionResult(
        status="ambiguous",
        reference=EntityReference(text="stairs", label="stairs"),
        candidates=[
            ResolutionCandidate(node_id="stairs-a", node_type="object", label="stairs"),
            ResolutionCandidate(node_id="stairs-a", node_type="object", label="stairs"),
        ],
    ))
    assert planner._can_defer_instance_selection(duplicate_candidates) is False


def test_explicit_all_context_exposes_each_grounded_instance_id():
    grounded = {
        "action": "locate",
        "references": {"target": {
            "node_ids": ["chair-a", "chair-b"],
            "selected_many": [
                {"node_id": "chair-a", "node_type": "object", "label": "chair"},
                {"node_id": "chair-b", "node_type": "object", "label": "chair"},
            ],
        }},
        "steps": [],
    }

    context = planner._planner_semantic_context(grounded)

    assert context["references"]["target"]["node_ids"] == ["chair-a", "chair-b"]
    assert "chair-a" in str(context) and "chair-b" in str(context)
