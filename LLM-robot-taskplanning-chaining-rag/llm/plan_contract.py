"""Request and validation contract for grounded LLM drafts, never ID inference."""
from __future__ import annotations

import json
import re

from schema import PlanDraft


class PlanDraftContractError(ValueError):
    """Recoverable model-output failure, distinct from transport/callback errors."""


FORMAT_ONLY_SYNTHETIC_EXAMPLE = '''FORMAT-ONLY SYNTHETIC EXAMPLE (independent of evaluation instructions):
This example demonstrates structure only. Its IDs are fictional: never copy example IDs;
select IDs only from GROUNDED TASK and the current INTENT CONTRACT.
Instruction: "Visit the linen staging area first, then the service lift lobby."
Evidence: staging area id=demo-area-LS-03; lift lobby id=demo-area-SL-08.
Output pattern:
{"status":"valid","task_chain":[
  {"step":1,"action":"navigate_to","target_type":"area","target_ref":"step_0_destination","target_id":"demo-area-LS-03","candidate_node_ids":[],"selection_policy":null,"description":"Navigate to the linen staging area","executor":"nav2","constraints":{"avoid":[],"prefer":[],"conditions":[]}},
  {"step":2,"action":"navigate_to","target_type":"area","target_ref":"step_1_destination","target_id":"demo-area-SL-08","candidate_node_ids":[],"selection_policy":null,"description":"Navigate to the service lift lobby","executor":"nav2","constraints":{"avoid":[],"prefer":[],"conditions":[]}}
]}
Ambiguous example:
Instruction: "Take me to a service lift lobby."
Grounded candidate reference step_0_destination has two candidates:
demo-area-SL-08 and demo-area-SL-11. Neither candidate is selected here.
{"status":"valid","task_chain":[
  {"step":1,"action":"navigate_to","target_type":"area","target_ref":"step_0_destination","target_id":null,"candidate_node_ids":["demo-area-SL-08","demo-area-SL-11"],"selection_policy":"deferred_to_nav2","description":"Navigate to a feasible service lift lobby","executor":"nav2","constraints":{"avoid":[],"prefer":[],"conditions":[]}}
]}'''


def _bound_contract(grounded_task: dict) -> tuple[list[str], dict[str, list[str]]]:
    refs = set()
    constraints = {"avoid": set(), "prefer": set()}
    for role, data in (grounded_task.get("references") or {}).items():
        if data.get("node_id") or data.get("node_ids") or data.get("candidate_node_ids"):
            refs.add(role)
    for index, step in enumerate(grounded_task.get("steps") or []):
        for role, data in (step.get("references") or {}).items():
            if data.get("node_id") or data.get("node_ids") or data.get("candidate_node_ids"):
                refs.add(f"step_{index}_{role}")
    for index, constraint in enumerate(grounded_task.get("constraints") or []):
        kind = constraint.get("type")
        if kind not in constraints:
            continue
        if constraint.get("node_id") or constraint.get("node_ids"):
            key = constraint.get("key") or f"constraint_{kind}_{index}"
            refs.add(key)
            constraints[kind].add(key)
        elif kind == "prefer":
            # Explicit route policies are not entity references or invented IDs.
            policy = constraint.get("policy") or constraint.get("description")
            if policy:
                constraints[kind].add(policy)
    return sorted(refs), {key: sorted(values) for key, values in constraints.items()}


def _grounded_ids_by_ref(grounded_task: dict) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Return exact and deferred IDs by the only references LLM2 may select."""
    exact: dict[str, list[str]] = {}
    candidates: dict[str, list[str]] = {}

    def add(ref: str, data: dict) -> None:
        node_ids = data.get("node_ids") or ([data["node_id"]] if data.get("node_id") else [])
        if node_ids:
            exact[ref] = sorted({str(node_id) for node_id in node_ids})
        candidate_ids = data.get("candidate_node_ids") or []
        if candidate_ids:
            candidates[ref] = sorted({str(node_id) for node_id in candidate_ids})

    for role, data in (grounded_task.get("references") or {}).items():
        if isinstance(data, dict):
            add(role, data)
    for index, step in enumerate(grounded_task.get("steps") or []):
        for role, data in (step.get("references") or {}).items():
            if isinstance(data, dict):
                add(f"step_{index}_{role}", data)
    for index, constraint in enumerate(grounded_task.get("constraints") or []):
        ids = constraint.get("node_ids") or ([constraint["node_id"]] if constraint.get("node_id") else [])
        if ids:
            ref = constraint.get("key") or f"constraint_{constraint.get('type', 'avoid')}_{index}"
            exact[ref] = sorted({str(node_id) for node_id in ids})
    return exact, candidates


def grounded_plan_schema(grounded_task: dict) -> dict:
    """Expose role refs and only the node IDs grounded for this request."""
    refs, constraints = _bound_contract(grounded_task)
    exact_by_ref, candidates_by_ref = _grounded_ids_by_ref(grounded_task)
    allowed_ids = sorted({node_id for values in exact_by_ref.values() for node_id in values}
                         | {node_id for values in candidates_by_ref.values() for node_id in values})
    allowed_candidate_ids = sorted({node_id for values in candidates_by_ref.values() for node_id in values})
    schema = PlanDraft.model_json_schema()
    schema["additionalProperties"] = False
    step = schema["$defs"]["PlanDraftStep"]
    step["additionalProperties"] = False
    step["required"] = list(dict.fromkeys(step["required"] + [
        "target_ref", "target_id", "candidate_node_ids", "selection_policy",
    ]))
    step["properties"]["step"]["minimum"] = 1
    step["properties"]["target_ref"] = {
        "type": "string", "enum": ["", *refs],
        "description": "An available grounded role used to validate target_id; empty only for no-ref or multi-role actions.",
    }
    step["properties"]["target_id"] = {
        "anyOf": ([{"type": "string", "enum": allowed_ids}] if allowed_ids else [])
                 + [{"type": "null"}],
        "description": "Exact node ID from the current RAG evidence, or null only for deferred candidates/no-ref actions.",
    }
    candidate_ids_schema = {
        "type": "array",
        "uniqueItems": True,
        "description": "Use [] for a uniquely resolved reference; for a deferred reference, include its complete candidate ID set.",
    }
    if allowed_candidate_ids:
        candidate_ids_schema["items"] = {
            "type": "string",
            "enum": allowed_candidate_ids,
        }
    else:
        candidate_ids_schema["items"] = {"type": "string"}
        candidate_ids_schema["maxItems"] = 0
    step["properties"]["candidate_node_ids"] = candidate_ids_schema
    step["properties"]["selection_policy"] = {
        "anyOf": [{"type": "string", "enum": ["deferred_to_nav2"]}, {"type": "null"}],
        "description": "Must be null for a uniquely resolved target; deferred_to_nav2 only when target_id is null and candidate_node_ids is the full grounded candidate set.",
    }
    step["properties"]["role_refs"] = {"anyOf": [
        {"type": "object", "properties": {
            role: {"type": "string", "enum": refs}
            for role in ("actor", "target", "source", "destination") if refs},
         "additionalProperties": False}, {"type": "null"}], "default": None}
    resolution_variants = []

    def add_resolution_modes(ref: str, binding: dict, *, required: list[str] | None = None,
                             actions: list[str] | None = None,
                             exclude_multirole: bool = False,
                             exclude_role_refs: bool = False) -> None:
        """Add unique/candidate branches bound to exactly one grounded reference."""
        exact_ids = exact_by_ref.get(ref, [])
        candidate_ids = candidates_by_ref.get(ref, [])
        base = dict(binding)
        if actions:
            base["action"] = {"enum": actions}
        branch_required = list(required or [])
        if exact_ids:
            properties = {
                **base,
                "target_id": {"type": "string", "enum": exact_ids},
                "candidate_node_ids": {"type": "array", "maxItems": 0},
                "selection_policy": {"type": "null"},
            }
            branch = {"properties": properties}
            if branch_required:
                branch["required"] = branch_required
            if exclude_multirole:
                branch["not"] = {"properties": {
                    "action": {"enum": ["escort_to", "guide_to"]}
                }}
            if exclude_role_refs:
                branch["not"] = {"properties": {"role_refs": {"type": "object"}}, "required": ["role_refs"]}
            resolution_variants.append(branch)
        if candidate_ids:
            candidate_array = {
                "type": "array",
                "items": {"type": "string", "enum": candidate_ids},
                "uniqueItems": True,
                "minItems": len(candidate_ids),
                "maxItems": len(candidate_ids),
                "allOf": [
                    {"contains": {"const": node_id}}
                    for node_id in candidate_ids
                ],
            }
            properties = {
                **base,
                "target_id": {"type": "null"},
                "candidate_node_ids": candidate_array,
                "selection_policy": {"const": "deferred_to_nav2"},
            }
            branch = {"properties": properties}
            if branch_required:
                branch["required"] = branch_required
            if exclude_multirole:
                branch["not"] = {"properties": {
                    "action": {"enum": ["escort_to", "guide_to"]}
                }}
            resolution_variants.append(branch)
            if exclude_role_refs:
                branch["not"] = {"properties": {"role_refs": {"type": "object"}}, "required": ["role_refs"]}

    # Direct target_ref bindings cover ordinary single-role steps.
    for ref in refs:
        add_resolution_modes(ref, {"target_ref": {"const": ref}}, exclude_role_refs=True)

    # Multi-role actions use destination as their primary target in the parser.
    for action in ("escort_to", "guide_to"):
        for ref in refs:
            role_binding = {
                "target_ref": {"const": ""},
                "action": {"const": action},
                "role_refs": {
                    "required": ["destination"],
                    "properties": {"destination": {"const": ref}},
                },
            }
            add_resolution_modes(ref, role_binding, required=["role_refs"], actions=[action])

    # For other role-ref plans, mirror the parser's target > destination > source > actor precedence.
    role_order = ("target", "destination", "source", "actor")
    for index, role in enumerate(role_order):
        higher_priority_roles = list(role_order[:index])
        for ref in refs:
            role_refs_schema = {
                "required": [role],
                "properties": {role: {"const": ref}},
            }
            if higher_priority_roles:
                role_refs_schema["not"] = {"required": higher_priority_roles}
            role_binding = {"target_ref": {"const": ""}, "role_refs": role_refs_schema}
            add_resolution_modes(
                ref, role_binding, required=["role_refs"], exclude_multirole=True
            )

    # No-reference actions carry no semantic ID or deferred candidates.
    resolution_variants.append({
        "properties": {
            "target_ref": {"const": ""},
            "action": {"enum": ["wait", "ask_clarification", "return_to_base"]},
            "target_id": {"type": "null"},
            "candidate_node_ids": {"type": "array", "maxItems": 0},
            "selection_policy": {"type": "null"},
        },
        "not": {"properties": {"role_refs": {"type": "object"}}, "required": ["role_refs"]},
    })
    step["allOf"] = [{"oneOf": resolution_variants}]
    constraint_schema = schema["$defs"]["Constraints"]

    constraint_schema["additionalProperties"] = False
    for field, values in constraints.items():
        prop = constraint_schema["properties"][field]
        if values:
            prop["items"] = {"type": "string", "enum": values}
        else:
            prop["maxItems"] = 0
    return schema


def _parse_grounded_plan_draft(raw_content: str, grounded_task: dict) -> PlanDraft:
    """Reject unavailable refs/extra fields even if the model backend ignores schema."""
    content = raw_content.strip()
    fence = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", content, re.IGNORECASE)
    if fence:
        content = fence.group(1)
    data = json.loads(content)
    schema = grounded_plan_schema(grounded_task)
    if not isinstance(data, dict):
        raise ValueError("PlanDraft must be a JSON object")
    if set(data) - set(schema["properties"]):
        raise ValueError("Unexpected PlanDraft fields; raw node IDs are not allowed")
    step_schema = schema["$defs"]["PlanDraftStep"]
    refs, constraints = _bound_contract(grounded_task)
    exact_by_ref, candidates_by_ref = _grounded_ids_by_ref(grounded_task)
    for step in data.get("task_chain", []):
        if not isinstance(step, dict):
            raise ValueError("PlanDraft steps must be objects")
        if set(step) - set(step_schema["properties"]):
            raise ValueError("Unexpected step fields; target_id/role_ids are not allowed")
        if not isinstance(step.get("step"), int) or isinstance(step["step"], bool) or step["step"] < 1:
            raise ValueError("step numbers must be one-based positive integers")
        if "target_ref" not in step or step["target_ref"] not in ["", *refs]:
            raise ValueError("Missing or unavailable target_ref; output an available grounded role")
        roles = step.get("role_refs")
        primary_ref = step.get("target_ref")
        if roles:
            primary_ref = (roles.get("destination") if step.get("action") in ("escort_to", "guide_to")
                           else next((roles.get(role) for role in ("target", "destination", "source", "actor")
                                      if roles.get(role)), None))
        expected_ids = exact_by_ref.get(primary_ref, [])
        expected_candidates = candidates_by_ref.get(primary_ref, [])
        target_id = step.get("target_id")
        candidate_ids = step.get("candidate_node_ids", [])
        if expected_candidates:
            if step.get("action") in ("wait", "ask_clarification", "return_to_base"):
                raise ValueError("no-ref actions cannot carry candidate targets")
            if target_id is not None:
                raise ValueError("candidate target must not select a singular target_id")
            if sorted(set(candidate_ids)) != expected_candidates or len(candidate_ids) != len(set(candidate_ids)):
                raise ValueError("candidate_node_ids must preserve the exact grounded candidate set")
            if step.get("selection_policy") != "deferred_to_nav2":
                raise ValueError("candidate target requires selection_policy='deferred_to_nav2'")
        elif candidate_ids:
            raise ValueError("candidate_node_ids are only valid for a grounded candidate reference")
        elif step.get("selection_policy") == "deferred_to_nav2":
            # A stale deferred marker is redundant when an exact, grounded ID is
            # already selected and no candidate IDs are present. Canonicalize only
            # this fully grounded case; never infer or replace target IDs.
            if target_id is not None and str(target_id) in expected_ids:
                step["selection_policy"] = None
            else:
                raise ValueError("deferred_to_nav2 requires a grounded candidate set")
        elif step.get("selection_policy") is not None:
            raise ValueError("unsupported selection policy for an exact target")

        if not expected_candidates:
            if step.get("action") in ("wait", "ask_clarification", "return_to_base"):
                if target_id is not None:
                    raise ValueError("no-ref actions require target_id=null")
            elif not target_id or str(target_id) not in expected_ids:
                raise ValueError("target_id must be an exact ID grounded for the selected reference")
        if step.get("action") in ("wait", "ask_clarification", "return_to_base"):
            if step["target_ref"] != "" or roles:
                raise ValueError("No-ref actions require an empty target_ref and no role_refs")
        if roles is not None:
            if not isinstance(roles, dict) or any(
                key not in ("actor", "target", "source", "destination")
                or value not in refs for key, value in roles.items()
            ):
                raise ValueError("role_refs must map valid roles to available grounded references")
            if target_id and target_id not in exact_by_ref.get(primary_ref, []):
                raise ValueError("target_id does not match the selected grounded role ID")
        values = step.get("constraints", {})
        if not isinstance(values, dict) or set(values) - {"avoid", "prefer", "conditions"}:
            raise ValueError("Unexpected or malformed step constraints")
        for field, allowed in constraints.items():
            items = values.get(field, [])
            if not isinstance(items, list) or any(value not in allowed for value in items):
                raise ValueError(f"constraints.{field} must use declared constraint roles or policies")
    return PlanDraft.model_validate(data)


def parse_grounded_plan_draft(raw_content: str, grounded_task: dict) -> PlanDraft:
    """Classify JSON, shape and reference failures under the existing repair budget."""
    try:
        return _parse_grounded_plan_draft(raw_content, grounded_task)
    except (ValueError, TypeError) as error:
        raise PlanDraftContractError(f"Invalid grounded PlanDraft: {error}") from error
