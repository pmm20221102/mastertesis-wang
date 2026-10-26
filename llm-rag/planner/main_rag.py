"""RAG-enhanced LLM Task Planner for robot navigation.

Usage:
    python main_rag.py                                    # Interactive mode (structured)
    python main_rag.py --command "navigate to room 201"   # Single command
    python main_rag.py --mode legacy                      # Legacy baseline mode
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import ValidationError

from schema import TaskPlan, TaskStep, PlanDraft, Constraints, PipelineResult
from llm.plan_contract import (FORMAT_ONLY_SYNTHETIC_EXAMPLE, PlanDraftContractError,
                               grounded_plan_schema, parse_grounded_plan_draft)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config_rag.yaml"
DEFAULT_OUTPUT_PATH = BASE_DIR / "task_plan_output_rag.json"

# Import local schemas first (before RAG)
from planner_schemas.intent import EntityReference, TaskIntent, TaskConstraint

# Add RAG src to path for imports (after local schemas)
RAG_SRC = BASE_DIR.parent / "RAG" / "khronos_3dsg_rag" / "src"
sys.path.insert(0, str(RAG_SRC))

# Import new structured pipeline components
from llm.intent_parser import parse_intent_with_llm, intent_to_dict
from grounding.orchestrator import GroundingOrchestrator
from scene_graph_api import SceneGraphRAG
from api_schemas import ResolutionResult

# Import legacy components for baseline comparison
from rag_retriever import retrieve_scene_context, retrieve_with_keywords
from prompt import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt


def load_config(config_path: str | Path | None = None) -> dict:
    """Load YAML config file."""
    if config_path is None:
        config_path = CONFIG_PATH
    config_path = Path(config_path)
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def merge_runtime_options(config: dict, runtime_options: dict | None) -> dict:
    """Apply a small, validated per-run Ollama option override without mutating config."""
    if not isinstance(config, dict):
        raise ValueError("config must be a mapping")
    if runtime_options is None:
        return dict(config)
    if not isinstance(runtime_options, dict):
        raise ValueError("runtime_options must be a mapping")
    unsupported = sorted(set(runtime_options) - {"num_ctx", "num_predict", "request_timeout_s"})
    if unsupported:
        raise ValueError(f"unsupported runtime option overrides: {unsupported}")
    normalized = dict(config)
    options = dict(config.get("options") or {})
    for name, value in runtime_options.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        if name == "request_timeout_s":
            normalized["ollama_request_timeout_s"] = value
        else:
            options[name] = value
    normalized["options"] = options
    return normalized


# --- Legacy helper stubs (deprecated, kept for backward compatibility) ---

def extract_json_object(text: str) -> str:
    """Extract JSON object from LLM response text."""
    # Find first { and last }
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return text[start:end]
    return text


def build_entity_index(entities: list[dict]) -> dict:
    """Build entity lookup index by ID."""
    return {ent.get("id", ""): ent for ent in entities if ent.get("id")}


def normalize_text(text: str) -> str:
    """Normalize text for comparison."""
    return text.lower().strip()


def detect_intent(text: str) -> tuple[bool, bool, bool]:
    """Detect transfer, screening, and avoid intents from text."""
    transfer = any(w in text for w in ["transfer", "bring", "carry", "move", "escort"])
    screening = any(w in text for w in ["screen", "scan", "check"])
    avoid = any(w in text for w in ["avoid", "bypass", "skip"])
    return transfer, screening, avoid


def find_restricted_entities(entities_by_id: dict) -> set:
    """Find restricted entity IDs."""
    restricted = set()
    for eid, ent in entities_by_id.items():
        desc = ent.get("description", "").lower()
        if "restricted" in desc or "authorized" in desc:
            restricted.add(eid)
    return restricted


def _validate_chain_structure(task_plan: TaskPlan) -> list[str]:
    """Validate task chain structure."""
    errors = []
    if not task_plan.task_chain:
        errors.append("Empty task chain")
    for i, step in enumerate(task_plan.task_chain):
        if step.step != i + 1:
            errors.append(f"Step numbering incorrect: expected {i+1}, got {step.step}")
    return errors


def _validate_transfer_requirements(task_plan: TaskPlan, transfer_intent: bool) -> list[str]:
    """Validate transfer requirements."""
    return []


def _validate_screening_requirement(task_plan: TaskPlan, screening_intent: bool) -> list[str]:
    """Validate screening requirements."""
    return []


def _validate_avoid_requirement(task_plan: TaskPlan, avoid_intent: bool, restricted_ids: set) -> list[str]:
    """Validate avoid requirements."""
    return []


def _validate_step_numbering(task_plan: TaskPlan) -> list[str]:
    """Validate step numbering."""
    errors = []
    for i, step in enumerate(task_plan.task_chain):
        if step.step != i + 1:
            errors.append(f"Step {step.step} should be {i+1}")
    return errors


def canonicalize_task_plan(task_plan: TaskPlan, scene_context: dict, user_command: str) -> TaskPlan:
    """Canonicalize task plan (placeholder for legacy mode)."""
    return task_plan


def build_rule_based_transfer_plan(user_command: str, scene_context: dict) -> TaskPlan | None:
    """Build rule-based transfer plan (placeholder for legacy mode)."""
    return None


def extract_relevant_subgraph(user_command: str, scene_graph: dict) -> dict:
    """Extract relevant subgraph from scene graph (placeholder for baseline mode)."""
    return scene_graph


def validate_task_plan_semantics(task_plan, scene_context, user_command):
    """Strict semantic validation for RAG context with target_id checks."""
    entities_by_id = build_entity_index(scene_context.get("entities", []))
    command_norm = normalize_text(user_command)
    transfer_intent, screening_intent, avoid_intent = detect_intent(command_norm)
    restricted_entity_ids = find_restricted_entities(entities_by_id)

    errors = []
    errors.extend(_validate_chain_structure(task_plan))
    errors.extend(_validate_transfer_requirements(task_plan, transfer_intent))
    errors.extend(_validate_screening_requirement(task_plan, screening_intent))
    errors.extend(_validate_avoid_requirement(task_plan, avoid_intent, restricted_entity_ids))
    errors.extend(_validate_step_numbering(task_plan))

    if task_plan.status != "valid" and task_plan.task_chain:
        errors.append("Non-valid status must not contain an executable task_chain")
    if len(task_plan.task_chain) > 6:
        errors.append("task_chain exceeds the maximum of 6 steps")

    # Strict target_id validation: check that all target_ids exist in scene context
    for step in task_plan.task_chain:
        expected_executor = ACTION_EXECUTOR_MAP.get(step.action)
        if expected_executor and step.executor != expected_executor:
            errors.append(f"Step {step.step}: action '{step.action}' requires executor '{expected_executor}'")
        if step.action not in ("wait", "ask_clarification", "return_to_base") and not step.target_id:
            errors.append(f"Step {step.step}: executable action requires a target_id")
        if step.target_id and step.action not in ("wait", "ask_clarification", "return_to_base"):
            if step.target_id not in entities_by_id:
                errors.append(f"Step {step.step}: target_id '{step.target_id}' not found in scene context")

    return errors

ACTION_EXECUTOR_MAP = {
    "locate_entity": "scene_graph",
    "navigate_to": "nav2",
    "escort_to": "nav2",
    "guide_to": "nav2",
    "avoid_area": "nav2",
    "wait": "none",
    "handover_to_staff": "human_staff",
    "trigger_workflow": "external_workflow",
    "perform_screening": "external_workflow",
    "return_to_base": "nav2",
    "ask_clarification": "llm",
}


def _fix_constraints(task_plan: TaskPlan) -> TaskPlan:
    """Fix missing or malformed constraints fields."""
    for step in task_plan.task_chain:
        if not hasattr(step, 'constraints') or step.constraints is None:
            step.constraints = Constraints()
        # Ensure avoid, prefer, conditions are lists
        if not isinstance(step.constraints.avoid, list):
            step.constraints.avoid = []
        if not isinstance(step.constraints.prefer, list):
            step.constraints.prefer = []
        if not isinstance(step.constraints.conditions, list):
            step.constraints.conditions = []
    return task_plan


def _infer_target_ref(action: str, ref_lookup: dict) -> str:
    """Infer target_ref from action type when LLM omits it.

    Priority: destination > target > actor > constraint
    """
    if action in ("wait", "ask_clarification", "return_to_base"):
        return ""

    # escort/guide → actor
    if action in ("escort_to", "guide_to"):
        return "actor" if "actor" in ref_lookup else "target"

    # locate → target
    if action == "locate_entity":
        return "target" if "target" in ref_lookup else ""

    # navigate → destination if available, else target
    if action == "navigate_to":
        if "destination" in ref_lookup:
            return "destination"
        return "target" if "target" in ref_lookup else ""

    # avoid_area → constraint
    if action == "avoid_area":
        return "constraint_avoid_0"

    # Default: try target
    return "target" if "target" in ref_lookup else ""


def _resolve_target_refs(task_plan: TaskPlan, grounded_task: dict) -> TaskPlan:
    """Validate LLM2-selected IDs against refs and attach deferred candidate sets."""
    lookup = {}
    candidate_lookup = {}
    for role, data in grounded_task.get("references", {}).items():
        if isinstance(data, dict):
            ids = data.get("node_ids") or ([data["node_id"]] if data.get("node_id") else [])
            if ids:
                lookup[role] = list(ids)
            candidate_ids = data.get("candidate_node_ids") or []
            if candidate_ids:
                candidate_lookup[role] = list(candidate_ids)
    for index, data in enumerate(grounded_task.get("steps", [])):
        for role, reference in (data.get("references") or {}).items():
            ids = reference.get("node_ids") or ([reference["node_id"]] if reference.get("node_id") else [])
            if ids:
                lookup[f"step_{index}_{role}"] = list(ids)
            candidate_ids = reference.get("candidate_node_ids") or []
            if candidate_ids:
                candidate_lookup[f"step_{index}_{role}"] = list(candidate_ids)
    for index, constraint in enumerate(grounded_task.get("constraints", [])):
        ids = constraint.get("node_ids") or ([constraint["node_id"]] if constraint.get("node_id") else [])
        if ids:
            key = constraint.get("key") or f"constraint_{constraint.get('type', 'avoid')}_{index}"
            lookup.setdefault(key, []).extend(identifier for identifier in ids
                                             if identifier not in lookup.get(key, []))

    expanded = []
    for original in task_plan.task_chain:
        target_ids = lookup.get(original.target_ref, [])
        if original.role_refs:
            target_ids = []  # Multi-role binding takes precedence.
        if original.target_id:
            if target_ids and original.target_id not in target_ids:
                raise ValueError(
                    f"LLM2 target_id {original.target_id!r} does not belong to ref {original.target_ref!r}"
                )
            variants = [original.target_id]
        else:
            variants = target_ids or [None]
        for target_id in variants:
            step = original.model_copy(deep=True) if len(variants) > 1 else original
            if target_id is not None:
                step.target_id = target_id
            deferred_ids = candidate_lookup.get(original.target_ref, []) if original.target_ref else []
            if deferred_ids:
                step.target_id = None
                step.candidate_node_ids = sorted(set(deferred_ids))
                step.selection_policy = "deferred_to_nav2"
            if step.role_refs:
                role_ids = {}
                role_candidate_ids = {}
                for role, reference in step.role_refs.items():
                    ids = lookup.get(reference, [])
                    if len(ids) > 1:
                        raise ValueError(f"Multi-role reference '{reference}' requires an explicit singular selection")
                    if ids:
                        role_ids[role] = ids[0]
                    candidate_ids = candidate_lookup.get(reference, [])
                    if candidate_ids:
                        role_candidate_ids[role] = sorted(set(candidate_ids))
                    else:
                        if not ids:
                            logger.warning(f"Step {step.step}: unresolved role ref {role}={reference!r}")
                step.role_ids = role_ids
                step.role_candidate_node_ids = role_candidate_ids
                primary_role = ("destination" if step.action in ("escort_to", "guide_to")
                                else next((role for role in ("target", "destination", "source", "actor")
                                           if role in role_ids or role in role_candidate_ids), None))
                if primary_role in role_candidate_ids:
                    step.target_id = None
                    step.candidate_node_ids = role_candidate_ids[primary_role]
                    step.selection_policy = "deferred_to_nav2"
                else:
                    step.target_id = role_ids.get(primary_role)
            for field in ("avoid", "prefer"):
                values = getattr(step.constraints, field)
                setattr(step.constraints, field,
                        [identifier for value in values for identifier in lookup.get(value, [value])])
            expanded.append(step)
    if len(expanded) > 6:
        raise ValueError("Expanded task_chain exceeds the maximum of 6 steps")
    if len(expanded) != len(task_plan.task_chain):
        for index, step in enumerate(expanded, start=1):
            step.step = index
    task_plan.task_chain = expanded
    return task_plan


def compile_grounded_navigation_plan(intent: TaskIntent, grounded_task: dict,
                                    user_command: str) -> TaskPlan:
    """Compile ordered navigation intents into Nav2 steps without another LLM call.

    This is deliberately narrow: it preserves RAG-selected IDs/candidate sets and
    rejects unsupported actions or route constraints instead of dropping them.
    """
    if intent.action != "navigate" and not intent.steps:
        raise PlanDraftContractError("grounded deterministic backend supports navigation only")
    if intent.constraints or grounded_task.get("constraints"):
        raise PlanDraftContractError(
            "grounded deterministic backend does not interpret route constraints")

    intent_steps = intent.steps or [intent]
    grounded_steps = grounded_task.get("steps") or []
    chain = []
    for index, intent_step in enumerate(intent_steps):
        if intent_step.action != "navigate":
            raise PlanDraftContractError(
                f"grounded deterministic backend does not support action {intent_step.action!r}")
        if intent_step.constraints:
            raise PlanDraftContractError(
                "grounded deterministic backend does not interpret step route constraints")
        role = "destination" if intent_step.destination is not None else "target"
        if getattr(intent_step, role, None) is None:
            raise PlanDraftContractError(f"navigation step {index + 1} has no target/destination")

        if intent.steps:
            if index >= len(grounded_steps):
                raise PlanDraftContractError(f"RAG grounding is missing navigation step {index + 1}")
            grounded_refs = grounded_steps[index].get("references") or {}
            target_ref = f"step_{index}_{role}"
        else:
            grounded_refs = grounded_task.get("references") or {}
            target_ref = role
        reference = grounded_refs.get(role)
        if not isinstance(reference, dict):
            raise PlanDraftContractError(f"RAG grounding is missing {target_ref}")
        if reference.get("selected_many") or len(reference.get("node_ids") or []) > 1:
            raise PlanDraftContractError(
                f"{target_ref} resolves to a set; ordered all-instance navigation is not defined")

        node_type = (reference.get("node_type")
                     or getattr(getattr(intent_step, role), "node_type", None)
                     or "unknown")
        node_type = str(node_type).lower().rstrip("s")
        target_type = {"agent": "person", "place": "area"}.get(node_type, node_type)
        if target_type not in {"person", "room", "object", "area"}:
            raise PlanDraftContractError(f"unsupported grounded navigation target type {node_type!r}")

        if reference.get("candidate_node_ids"):
            anchors = reference.get("candidate_navigation_targets") or []
            anchor_ids = {item.get("node_id") for item in anchors if isinstance(item, dict)}
            if (len(anchors) != len(reference["candidate_node_ids"])
                    or anchor_ids != set(reference["candidate_node_ids"])):
                raise PlanDraftContractError(f"{target_ref} candidate navigation anchors are incomplete")
        elif reference.get("node_id") and not isinstance(reference.get("navigation_target"), dict):
            raise PlanDraftContractError(f"{target_ref} has no Nav2 navigation anchor")
        elif not reference.get("node_id"):
            raise PlanDraftContractError(f"{target_ref} has neither a unique ID nor candidates")

        chain.append(TaskStep(
            step=index + 1,
            action="navigate_to",
            target_type=target_type,
            target_ref=target_ref,
            description=intent_step.description or user_command,
            executor="nav2",
            constraints=Constraints(),
        ))
    if not chain:
        raise PlanDraftContractError("navigation intent produced no ordered Nav2 steps")
    return _resolve_target_refs(TaskPlan(status="valid", task_chain=chain), grounded_task)


def _auto_fill_target_id(task_plan: TaskPlan, scene_context: dict[str, Any]) -> TaskPlan:
    """Auto-fill missing target_id based on target_type, notes, and available entities.

    IMPORTANT: This function should NOT create arbitrary IDs. It can only:
    1. Canonicalize aliases that have unique matches
    2. Verify that existing IDs are valid
    3. If no ID found, leave it as None (status should be ambiguous/not_found)
    """
    entities = scene_context.get("entities", [])
    if not entities:
        return task_plan

    # Build entity ID lookup
    entity_by_id = {ent.get("id", ""): ent for ent in entities if ent.get("id")}

    for step in task_plan.task_chain:
        if step.target_id:
            # Verify existing ID exists in scene context
            if step.target_id not in entity_by_id:
                logger.warning(f"Step {step.step} target_id {step.target_id} not found in scene context")
                # Don't replace, let validator handle this
            continue

        if step.action in ("wait", "ask_clarification", "return_to_base"):
            continue

        # Try 1: Extract entity ID from notes and description (only if unique match)
        for text in (task_plan.notes or []) + [step.description]:
            matches = re.findall(r'(?:entity|node|ID)\s+(\d{5,})', text, re.IGNORECASE)
            if matches and len(matches) == 1:
                step.target_id = matches[0]
                break
            matches = re.findall(r'\((\d{10,})\)', text)
            if matches and len(matches) == 1:
                step.target_id = matches[0]
                break
            matches = re.findall(r'\b(\d{10,})\b', text)
            if matches and len(matches) == 1:
                step.target_id = matches[0]
                break

        # IMPORTANT: Do NOT auto-fill from type_entities or entities[0]
        # If no ID found, leave as None - validator will set status to ambiguous/not_found

    return task_plan


def get_ollama_host(config: dict[str, Any]) -> str:
    """Return the Ollama server URL."""
    configured = config.get("ollama_host", "")
    if configured:
        return configured
    # Try resolv.conf nameserver (WSL2 Windows host IP)
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                if line.strip().startswith("nameserver"):
                    ip = line.strip().split()[1]
                    return f"http://{ip}:11434"
    except Exception:
        pass
    # Fallback to default gateway
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        )
        parts = result.stdout.split()
        for i, p in enumerate(parts):
            if p == "via" and i + 1 < len(parts):
                return f"http://{parts[i+1]}:11434"
    except Exception:
        pass
    return "http://127.0.0.1:11434"


def get_default_model(config: dict[str, Any]) -> str:
    """Return the default model name from config."""
    models = config.get("models", {})
    for name, cfg in models.items():
        if cfg.get("default", False):
            # Return the actual model name, not the YAML key
            return cfg.get("name", name)
    return "mistral"


def run_llm_once(model: str, prompt: str, config: dict[str, Any]) -> str:
    """Send a prompt to LLM and return raw text response."""
    from ollama import Client
    host_url = get_ollama_host(config)
    client = Client(host=host_url, timeout=300)
    options = config.get("options", {"temperature": 0.1, "num_ctx": 8192, "num_predict": 2048})

    response = client.chat(
        model=model,
        think=False,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        format=TaskPlan.model_json_schema(),
        options=options,
    )

    raw_content = response.message.content
    if not raw_content:
        raise ValueError("LLM 返回内容为空")
    return raw_content


def build_rag_enhanced_prompt(user_command: str, scene_context: dict[str, Any]) -> str:
    """Build user prompt with RAG-retrieved context.

    Includes rich entity information to help LLM understand the scene.
    """
    entities = scene_context.get("entities", [])
    keywords = scene_context.get("_keywords", {})

    # Build detailed entity list with useful information
    detailed_entities = []
    for ent in entities[:15]:  # Limit to 15 entities max
        entity_info = {
            "id": ent["id"],
            "type": ent.get("type", "unknown"),
            "label": ent.get("name", ""),
            "location": ent.get("location", ""),
        }
        # Add description if available
        if ent.get("description"):
            entity_info["description"] = ent["description"][:200]  # Truncate long descriptions
        # Add position if available
        if ent.get("position"):
            entity_info["position"] = ent["position"]
        # Add parent/child/neighbor info if available
        if ent.get("parent_ids"):
            entity_info["parent_ids"] = ent["parent_ids"]
        if ent.get("child_ids"):
            entity_info["child_ids"] = ent["child_ids"]
        if ent.get("neighbor_ids"):
            entity_info["neighbor_ids"] = ent["neighbor_ids"]
        detailed_entities.append(entity_info)

    prompt_parts = [
        f"User instruction: {user_command}",
        "",
        "Available entities (use 'id' as target_id):",
        json.dumps(detailed_entities, ensure_ascii=False, indent=2),
        "",
        "IMPORTANT RULES:",
        "- Each step MUST have a 'target_id' from the entities above.",
        "- If you cannot find a matching entity, set status to 'ambiguous' or 'target_not_found'.",
        "- Do NOT invent or hallucinate IDs. Use only IDs present in the provided entities.",
        "- If multiple entities match, choose the most relevant one or set status to 'ambiguous'.",
    ]

    if keywords:
        prompt_parts.append(f"\nExtracted keywords: {json.dumps(keywords, ensure_ascii=False)}")

    return "\n".join(prompt_parts)


def run_rag_task_planner(
    user_command: str,
    model_name: str | None = None,
    use_llm_keywords: bool = True,
    db_dir: str | None = None,
) -> TaskPlan:
    """Run the RAG-enhanced task planner.

    Args:
        user_command: Natural language instruction.
        model_name: Optional model override.
        use_llm_keywords: Whether to use LLM for keyword extraction.
        db_dir: Optional vector DB path override.

    Returns:
        A validated TaskPlan instance.
    """
    config = load_config(CONFIG_PATH)
    model = model_name or get_default_model(config)

    # Stage 1: RAG retrieval
    logger.info("Retrieving scene context from RAG...")
    scene_context = retrieve_with_keywords(
        user_command,
        db_dir=db_dir,
        top_k=20,
        use_llm_keywords=use_llm_keywords,
        model=model,
        config=config,
    )
    logger.info(f"Retrieved {len(scene_context.get('entities', []))} entities")

    # Stage 2: Build prompt
    user_prompt = build_rag_enhanced_prompt(user_command, scene_context)
    prompt_for_attempt = user_prompt
    last_raw_content = ""
    last_errors: list[str] = []

    # Stage 3: LLM planning with retry
    for attempt in range(2):
        raw_content = run_llm_once(model, prompt_for_attempt, config)
        last_raw_content = raw_content
        cleaned = extract_json_object(raw_content)

        try:
            task_plan = TaskPlan.model_validate_json(cleaned)
        except ValidationError as validation_error:
            last_errors = [str(validation_error)]
            if attempt == 1:
                break
            prompt_for_attempt = build_repair_prompt(user_command, scene_context, raw_content, last_errors)
            continue

        # Canonicalize and validate
        task_plan = canonicalize_task_plan(task_plan, scene_context, user_command)

        # Post-process: fix constraints and fill missing target_id
        task_plan = _fix_constraints(task_plan)
        task_plan = _auto_fill_target_id(task_plan, scene_context)

        semantic_errors = validate_task_plan_semantics(task_plan, scene_context, user_command)
        if semantic_errors:
            last_errors = semantic_errors
            if attempt == 1:
                break
            prompt_for_attempt = build_repair_prompt(user_command, scene_context, raw_content, semantic_errors)
            continue

        return task_plan

    # Fallback
    fallback_plan = build_rule_based_transfer_plan(user_command, scene_context)
    if fallback_plan:
        return fallback_plan

    logger.error("LLM 输出未通过校验")
    logger.error("原始输出：%s", last_raw_content)
    raise ValueError("LLM 输出未通过 schema 或 semantic validator")


# Rename legacy function for ablation
run_legacy_task_planner = run_rag_task_planner


def _can_defer_instance_selection(grounding) -> bool:
    """Allow chain planning only when every blocking ambiguity has candidates."""
    if getattr(grounding, "status", None) != "ambiguous":
        return False
    required_refs = getattr(grounding, "_required_references", set())
    if not required_refs:
        return False
    saw_candidate_set = False
    for key in required_refs:
        result = grounding.references.get(key)
        if result is None:
            return False
        if result.status == "ambiguous":
            candidate_ids = [candidate.node_id for candidate in result.candidates]
            if (not candidate_ids
                    or any(not isinstance(node_id, str) or not node_id.strip() for node_id in candidate_ids)
                    or len(set(candidate_ids)) != len(candidate_ids)):
                return False
            saw_candidate_set = True
        elif result.status not in ("resolved", "resolved_set"):
            return False
    for key in getattr(grounding, "_required_constraints", set()):
        result = grounding.constraints.get(key)
        if result is None or result.status not in ("resolved", "resolved_set"):
            return False
    return saw_candidate_set


def apply_grounding_selections(grounding, selections: dict[str, str]) -> None:
    """Resolve explicit UI choices only when IDs belong to current RAG candidates."""
    if not isinstance(selections, dict) or not selections:
        raise ValueError("grounding selections must be a non-empty reference-to-node mapping")
    pending = []
    for reference_key, node_id in selections.items():
        result = grounding.references.get(reference_key)
        if result is None:
            raise ValueError(f"unknown grounding reference: {reference_key}")
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError(f"selected node ID for {reference_key} must be a non-empty string")
        available = list(result.candidates)
        if result.status == "resolved" and result.selected is not None:
            available.append(result.selected)
        selected = next((candidate for candidate in available
                         if candidate.node_id == node_id), None)
        if selected is None:
            raise ValueError(f"selected node {node_id} is not a candidate for {reference_key}")
        pending.append((result, selected))

    for result, selected in pending:
        result.selected = selected
        result.selected_many = []
        result.candidates = [selected]
        result.status = "resolved"
    grounding.recompute_status()


def _emit_progress(callback: Callable[[dict], None] | None,
                   stage: str, message: str, **details) -> None:
    if callback is None:
        return
    try:
        callback({"stage": stage, "message": message, "details": details})
    except Exception:
        logger.exception("Progress callback failed at stage %s", stage)


def run_structured_task_planner(
    user_command: str,
    model_name: str | None = None,
    db_dir: str | None = None,
    intent_prompt_profile: str | None = None,
    runtime_options: dict | None = None,
    task_chain_backend: str = "llm2",
    interactive_selection: bool = False,
    grounding_selections: dict[str, str] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> PipelineResult:
    """Run the new structured pipeline: Intent → Grounding → Planning.

    This is the proposed method that separates entity selection from task planning.

    Args:
        user_command: Natural language instruction.
        model_name: Optional model override.
        db_dir: Optional vector DB path override.

    Returns:
        PipelineResult with full provenance for both success and failure.
    """
    import time

    config = merge_runtime_options(load_config(CONFIG_PATH), runtime_options)
    if task_chain_backend not in ("llm2", "grounded_deterministic"):
        raise ValueError("task_chain_backend must be 'llm2' or 'grounded_deterministic'")
    model = model_name or get_default_model(config)
    _emit_progress(progress_callback, "intent_parsing", "LLM 正在解析导航意图。")

    # Determine db_dir
    repair_enabled = config.get("bounded_repair_enabled", True)
    if not isinstance(repair_enabled, bool):
        raise ValueError("bounded_repair_enabled must be boolean")
    if db_dir is None:
        rag_config = config.get("rag", {})
        db_dir = rag_config.get("db_dir", str(BASE_DIR.parent / "RAG" / "khronos_3dsg_rag" / "outputs" / "vector_db"))

    logger.info("=== Structured Pipeline ===")

    latency = {}
    intent_dict = None
    grounding_dict = None
    grounded_task_dict = None
    task_plan_dict = None
    validation_errors = []
    intent_attempts = []

    def intent_callback(attempts):
        intent_attempts[:] = attempts

    def pipeline_result(**fields):
        return PipelineResult(intent_parse_attempts=intent_attempts, **fields)

    # Read canonical labels, not vector embeddings or evaluation annotations.
    # Adapters without this optional method retain the legacy parser interface.
    rag = None
    t0 = time.perf_counter()
    intent_config = dict(config)
    if intent_prompt_profile is not None:
        intent_config["intent_prompt_profile"] = intent_prompt_profile
    try:
        rag = SceneGraphRAG(db_dir)
        get_ontology = getattr(rag, "get_label_ontology", None)
        if callable(get_ontology):
            intent_config["scene_ontology"] = get_ontology()
    except Exception as error:
        logger.warning(f"Scene ontology unavailable before intent parsing: {error}")
    latency["ontology_ms"] = (time.perf_counter() - t0) * 1000

    # Stage 1: Intent Parsing (LLM extracts semantic intent, no node IDs)
    logger.info("Stage 1: Parsing intent...")
    t0 = time.perf_counter()
    try:
        intent = parse_intent_with_llm(user_command, model=model, config=intent_config,
                                       attempt_callback=intent_callback)
        intent_dict = intent_to_dict(intent)
        latency["intent_ms"] = (time.perf_counter() - t0) * 1000
        logger.info(f"Parsed intent: action={intent.action}, "
                   f"target={intent.target.text if intent.target else None}, "
                   f"destination={intent.destination.text if intent.destination else None}")
        _emit_progress(progress_callback, "intent_parsed", "意图解析完成。",
                       intent=intent_dict)

        # A parser-requested clarification is an intentional user-facing
        # outcome, not a navigation target that should be sent through RAG.
        if intent.action == "ask_clarification":
            return pipeline_result(
                status="ambiguous",
                intent=intent_dict,
                latency_ms=latency,
                error=intent.description or "The destination needs clarification.",
            )

        # Check for unsupported action
        if intent.action == "unsupported":
            return pipeline_result(
                status="unsupported",
                intent=intent_dict,
                latency_ms=latency,
                error="Task involves manipulation capabilities not supported by the robot",
            )
    except Exception as e:
        latency["intent_ms"] = (time.perf_counter() - t0) * 1000
        logger.error(f"Intent parsing failed: {e}")
        return pipeline_result(
            status="system_error",
            intent=intent_dict,
            latency_ms=latency,
            error=f"Intent parsing failed: {e}",
        )

    # Stage 2: Grounding (RAG resolves entity references to node IDs)
    logger.info("Stage 2: Grounding entities...")
    _emit_progress(progress_callback, "rag_grounding", "正在查询 RAG 并定位场景图目标。")
    t0 = time.perf_counter()
    try:
        if rag is None:
            rag = SceneGraphRAG(db_dir)
        grounding = GroundingOrchestrator(rag).ground_intent(intent)
    except Exception as error:
        latency["grounding_ms"] = (time.perf_counter() - t0) * 1000
        return pipeline_result(status="system_error", intent=intent_dict, latency_ms=latency,
                              error=f"Grounding failed: {error}")
    if grounding_selections:
        try:
            apply_grounding_selections(grounding, grounding_selections)
        except ValueError as error:
            grounding_dict = grounding.to_dict()
            _emit_progress(progress_callback, "selection_rejected",
                           "该候选已不匹配当前 RAG 结果，请重新选择。", error=str(error))
            return pipeline_result(status="ambiguous", intent=intent_dict,
                                  grounding=grounding_dict, latency_ms=latency,
                                  error=str(error))
    grounding_dict = grounding.to_dict()
    latency["grounding_ms"] = (time.perf_counter() - t0) * 1000
    logger.info(f"Grounding status: {grounding.status}")
    _emit_progress(progress_callback, "rag_grounded", "RAG 目标定位完成。",
                   grounding=grounding_dict)

    if grounding.status == "system_error":
        return pipeline_result(status="system_error", intent=intent_dict, grounding=grounding_dict,
                              latency_ms=latency, error="; ".join(grounding.errors))

    # Stage 3: Handle non-ready status
    interactive_ambiguous = [
        key for key, result in grounding.references.items()
        if result.status == "ambiguous"
        and key in getattr(grounding, "_required_references", set())
    ]
    if interactive_selection and interactive_ambiguous:
        _emit_progress(progress_callback, "awaiting_user_selection",
                       "找到多个匹配地点，请选择一个继续。",
                       references=interactive_ambiguous)
        return pipeline_result(
            status="ambiguous", intent=intent_dict, grounding=grounding_dict,
            latency_ms=latency,
            error="Please select one of the matching destinations.",
        )
    deferred_instance_selection = _can_defer_instance_selection(grounding)
    if grounding.status == "ambiguous" and not deferred_instance_selection:
        logger.warning(f"Ambiguous grounding: {grounding.errors}")
        return pipeline_result(
            status="ambiguous",
            intent=intent_dict,
            grounding=grounding_dict,
            latency_ms=latency,
            error="Multiple candidates found. Please provide more specific details.",
        )
    if deferred_instance_selection:
        logger.info("Grounding has candidate sets; defer instance choice to Nav2 planning")

    if grounding.status == "not_found":
        logger.warning(f"Entity not found: {grounding.errors}")
        return pipeline_result(
            status="target_not_found",
            intent=intent_dict,
            grounding=grounding_dict,
            latency_ms=latency,
            error="; ".join(grounding.errors),
        )

    if grounding.status == "unsupported":
        return pipeline_result(
            status="unsupported",
            intent=intent_dict,
            grounding=grounding_dict,
            latency_ms=latency,
            error="; ".join(grounding.errors),
        )

    # Stage 4: Build grounded task for planner
    logger.info("Stage 4: Building grounded task...")
    _emit_progress(progress_callback, "planning", "目标已确定，正在生成任务链。")
    try:
        grounded_task = _build_grounded_task(intent, grounding, rag)
        grounded_task_dict = grounded_task
    except Exception as error:
        return pipeline_result(status="system_error", intent=intent_dict, grounding=grounding_dict,
                              latency_ms=latency, error=f"Grounded task construction failed: {error}")

    # Stage 5: Generate plan using grounded facts (LLM only decomposes, doesn't select IDs)
    logger.info("Stage 5: Generating task plan...")
    t0 = time.perf_counter()
    raw_draft = {}
    planning_attempts = [{"phase": task_chain_backend, "raw": raw_draft}]
    try:
        if task_chain_backend == "grounded_deterministic":
            task_plan = compile_grounded_navigation_plan(intent, grounded_task, user_command)
        else:
            task_plan = _generate_grounded_plan(user_command, grounded_task, model, config,
                                               draft_callback=raw_draft.update)
        # Stage 5.1: Deterministically resolve target_ref → target_id
        task_plan = _resolve_target_refs(task_plan, grounded_task)
        latency["planning_ms"] = (time.perf_counter() - t0) * 1000
    except (ValidationError, PlanDraftContractError) as e:
        # Invalid JSON/schema is a model output failure, not a transport failure.
        # It shares the single repair budget with semantic validation below.
        task_plan = TaskPlan(status="system_error", task_chain=[])
        failure_label = ("Grounded task-chain compilation error"
                         if task_chain_backend == "grounded_deterministic"
                         else "Draft schema error")
        validation_errors = [f"{failure_label}: {e}"]
        planning_attempts[-1]["error"] = str(e)
        latency["planning_ms"] = (time.perf_counter() - t0) * 1000
    except Exception as e:
        latency["planning_ms"] = (time.perf_counter() - t0) * 1000
        logger.error(f"Plan generation failed: {e}")
        planning_attempts[-1]["error"] = str(e)
        return pipeline_result(
            status="system_error",
            intent=intent_dict,
            grounding=grounding_dict,
            grounded_task=grounded_task_dict,
            latency_ms=latency,
            error=f"Plan generation failed: {e}",
            raw_plan_draft=raw_draft or None, planning_attempts=planning_attempts,
        )

    # Stage 6: Validate plan against grounding
    logger.info("Stage 6: Validating plan...")
    _emit_progress(progress_callback, "validating_plan", "正在检查任务链和目标约束。")
    t0 = time.perf_counter()
    validation_errors = validation_errors or _validate_grounded_plan(task_plan, grounding, grounded_task)
    if validation_errors and repair_enabled and task_chain_backend == "llm2":
        logger.warning(f"Validation errors: {validation_errors}")
        # Try repair once
        repair_draft = {}
        planning_attempts[0]["validation_errors"] = list(validation_errors)
        planning_attempts.append({"phase": "repair", "raw": repair_draft})
        try:
            task_plan = _repair_plan(task_plan, validation_errors, grounded_task, model, config,
                                     draft_callback=repair_draft.update, raw_draft=raw_draft)
            # Re-resolve target_ref after repair
            task_plan = _resolve_target_refs(task_plan, grounded_task)
            validation_errors = _validate_grounded_plan(task_plan, grounding, grounded_task)
            planning_attempts[-1]["validation_errors"] = list(validation_errors)
        except Exception as error:
            planning_attempts[-1]["error"] = str(error)
            logger.warning(f"Plan repair failed: {error}")
    latency["validation_ms"] = (time.perf_counter() - t0) * 1000
    _emit_progress(progress_callback,
                   "plan_ready" if not validation_errors else "plan_invalid",
                   "任务链生成完成。" if not validation_errors else "任务链未通过验证。",
                   status="valid" if not validation_errors else "system_error",
                   task_plan=json.loads(task_plan.model_dump_json()),
                   grounded_task=grounded_task_dict)

    task_plan_dict = json.loads(task_plan.model_dump_json())

    if validation_errors:
        return pipeline_result(
            status="system_error",
            intent=intent_dict,
            grounding=grounding_dict,
            grounded_task=grounded_task_dict,
            task_plan=task_plan_dict,
            raw_plan_draft=raw_draft or None, planning_attempts=planning_attempts,
            validation_errors=validation_errors,
            latency_ms=latency,
            error=f"Plan validation failed: {validation_errors}",
        )

    return pipeline_result(
        status=task_plan.status,
        intent=intent_dict,
        grounding=grounding_dict,
        grounded_task=grounded_task_dict,
        task_plan=task_plan_dict,
        raw_plan_draft=raw_draft or None, planning_attempts=planning_attempts,
        validation_errors=[],
        latency_ms=latency,
    )


def _candidate_navigation_record(candidate, rag: SceneGraphRAG) -> dict:
    """Serialize one RAG candidate and its Nav2 goal without selecting it."""
    nav_target = rag.get_navigation_target(candidate.node_id)
    nav_dict = None
    if nav_target:
        nav_dict = {
            "navigation_node_id": nav_target.navigation_node_id,
            "position": nav_target.position,
            "frame_id": nav_target.frame_id,
            "goal_type": nav_target.goal_type,
        }
    return {
        "node_id": candidate.node_id,
        "node_type": candidate.node_type,
        "label": candidate.label,
        "room_id": candidate.room_id,
        "place_id": candidate.place_id,
        "position": candidate.position,
        "navigation_target": nav_dict,
    }


def _build_grounded_task(intent, grounding, rag: SceneGraphRAG) -> dict:
    """Build a grounded task dict from intent and grounding results.

    Includes:
    - Resolved references with node IDs
    - Navigation targets for each reference
    - Constraints (avoid, prefer, condition)
    - Step-specific grounding for multi-step intents
    """
    grounded = {
        "action": intent.action,
        "requested_roles": [role for role in ("actor", "target", "source", "destination")
                            if getattr(intent, role, None) is not None],
        "description": intent.description,
        "references": {},
        "constraints": [],
        "steps": [],  # For multi-step intents
        "instance_selection_deferred": False,
    }

    for key in ["actor", "target", "source", "destination"]:
        ref_result = grounding.references.get(key)
        if ref_result and ref_result.status == "resolved" and ref_result.selected:
            selected = ref_result.selected

            # Get navigation target
            nav_target = rag.get_navigation_target(selected.node_id)
            nav_dict = None
            if nav_target:
                nav_dict = {
                    "navigation_node_id": nav_target.navigation_node_id,
                    "position": nav_target.position,
                    "frame_id": nav_target.frame_id,
                    "goal_type": nav_target.goal_type,
                }

            grounded["references"][key] = {
                "node_id": selected.node_id,
                "node_type": selected.node_type,
                "label": selected.label,
                "room_id": selected.room_id,
                "place_id": selected.place_id,
                "position": selected.position,
                "navigation_target": nav_dict,
            }
        elif ref_result and ref_result.status == "ambiguous" and ref_result.candidates:
            candidates = sorted(ref_result.candidates, key=lambda item: item.node_id)
            labels = sorted({candidate.label for candidate in candidates if candidate.label})
            grounded["references"][key] = {
                "node_type": ref_result.reference.node_type or candidates[0].node_type,
                "label": ref_result.reference.label or (labels[0] if len(labels) == 1 else None),
                "candidate_count": len(candidates),
                "candidate_node_ids": [candidate.node_id for candidate in candidates],
                "candidate_navigation_targets": [
                    _candidate_navigation_record(candidate, rag) for candidate in candidates
                ],
                "selection_policy": "deferred_to_nav2",
            }
            grounded["instance_selection_deferred"] = True

    # Add constraints
    for key, result in grounding.constraints.items():
        if result.status == "resolved" and result.selected:
            # Extract constraint type from key (e.g., "constraint_avoid_0" -> "avoid")
            parts = key.split("_constraint_", 1)[-1].split("_")
            constraint_type = parts[1] if parts[0] == "constraint" else parts[0]
            grounded["constraints"].append({
                "key": key,
                "type": constraint_type,
                "node_id": result.selected.node_id,
                "node_type": result.selected.node_type,
                "label": result.selected.label,
            })

    for key, result in grounding.constraints.items():
        if result.status == "resolved_set":
            parts = key.split("_constraint_", 1)[-1].split("_")
            constraint_type = parts[1] if parts[0] == "constraint" else parts[0]
            for selected in result.selected_many:
                grounded["constraints"].append({
                    "key": key, "type": constraint_type, "node_id": selected.node_id,
                    "node_type": selected.node_type, "label": selected.label,
                })
    grounded["constraints"].extend(getattr(grounding, "condition_constraints", []))

    # Add step-specific grounding for multi-step intents
    if intent.steps:
        for i, step in enumerate(intent.steps):
            step_refs = {}
            for role in ["actor", "target", "source", "destination"]:
                step_key = f"step_{i}_{role}"
                ref_result = grounding.references.get(step_key)
                if ref_result and ref_result.status == "resolved" and ref_result.selected:
                    selected = ref_result.selected
                    nav_target = rag.get_navigation_target(selected.node_id)
                    nav_dict = None
                    if nav_target:
                        nav_dict = {
                            "navigation_node_id": nav_target.navigation_node_id,
                            "position": nav_target.position,
                            "frame_id": nav_target.frame_id,
                            "goal_type": nav_target.goal_type,
                        }
                    step_refs[role] = {
                        "node_id": selected.node_id,
                        "node_type": selected.node_type,
                        "label": selected.label,
                        "room_id": selected.room_id,
                        "navigation_target": nav_dict,
                    }
                elif ref_result and ref_result.status == "ambiguous" and ref_result.candidates:
                    candidates = sorted(ref_result.candidates, key=lambda item: item.node_id)
                    labels = sorted({candidate.label for candidate in candidates if candidate.label})
                    step_refs[role] = {
                        "node_type": ref_result.reference.node_type or candidates[0].node_type,
                        "label": ref_result.reference.label or (labels[0] if len(labels) == 1 else None),
                        "candidate_count": len(candidates),
                        "candidate_node_ids": [candidate.node_id for candidate in candidates],
                        "candidate_navigation_targets": [
                            _candidate_navigation_record(candidate, rag) for candidate in candidates
                        ],
                        "selection_policy": "deferred_to_nav2",
                    }
                    grounded["instance_selection_deferred"] = True

            grounded["steps"].append({
                "action": step.action,
                "requested_roles": [role for role in ("actor", "target", "source", "destination")
                                    if getattr(step, role, None) is not None],
                "description": step.description,
                "references": step_refs,
            })

    for key, result in grounding.references.items():
        if result.status != "resolved_set":
            continue
        selection = {"node_ids": [candidate.node_id for candidate in result.selected_many],
                     "selected_many": [{
                         "node_id": candidate.node_id, "node_type": candidate.node_type,
                         "label": candidate.label, "room_id": candidate.room_id,
                         "place_id": candidate.place_id, "position": candidate.position,
                         "navigation_target": _candidate_navigation_record(candidate, rag)[
                             "navigation_target"],
                     } for candidate in result.selected_many]}
        if key in ("actor", "target", "source", "destination"):
            grounded["references"][key] = selection
        else:
            match = re.fullmatch(r"step_(\d+)_(actor|target|source|destination)", key)
            if match and int(match.group(1)) < len(grounded["steps"]):
                grounded["steps"][int(match.group(1))]["references"][match.group(2)] = selection
    return grounded


def _capture_plan_draft(raw_content, draft_callback):
    if draft_callback is None:
        return
    try:
        raw = json.loads(raw_content)
    except (TypeError, json.JSONDecodeError):
        raw = None
    draft_callback(raw if isinstance(raw, dict) else {"_raw_text": raw_content})


def _intent_plan_contract(grounded_task: dict) -> list[dict]:
    """Ordered obligations derived from predicted intent, never evaluation gold.

    Set references expand within their requested step. Escort/guide remain macro
    actions; this is a validator contract, not a replacement plan generator.
    """
    actions = {"navigate": "navigate_to", "locate": "locate_entity",
               "escort": "escort_to", "guide": "guide_to", "avoid": "avoid_area",
               "wait": "wait", "ask_clarification": "ask_clarification"}
    tasks = grounded_task.get("steps") or [grounded_task]
    multi = bool(grounded_task.get("steps"))
    contract = []

    if multi:
        root_action = actions.get(grounded_task.get("action"))
        root_refs = grounded_task.get("references") or {}
        requested = grounded_task.get("requested_roles") or []
        if root_action == "navigate_to":
            role_order = (("destination", "target", "source") if requested
                          else ("target", "destination", "source"))
            root_role = next((key for key in role_order
                              if key in root_refs and (not requested or key in requested)), None)
        elif root_action == "locate_entity":
            root_role = next((key for key in ("target", "actor") if key in root_refs), None)
        else:
            root_role = None

        if root_role is not None:
            root_data = root_refs[root_role]

            def identity_ids(reference):
                ids = reference.get("node_ids") or reference.get("candidate_node_ids") or []
                if not ids and reference.get("node_id"):
                    ids = [reference["node_id"]]
                return {str(node_id) for node_id in ids}

            root_identity = identity_ids(root_data)
            nested_identity = set()
            for nested in tasks:
                nested_reference = (nested.get("references") or {}).get(root_role, {})
                nested_identity.update(identity_ids(nested_reference))
            if root_identity and not root_identity.issubset(nested_identity):
                root_ids = root_data.get("node_ids") or (
                    [root_data["node_id"]] if root_data.get("node_id") else []
                )
                contract.append({
                    "action": root_action,
                    "ref": root_role,
                    "node_ids": root_ids,
                    "count": len(root_ids) or 1,
                    "intent_step": -1,
                })

    for index, task in enumerate(tasks):
        action = actions.get(task.get("action"))
        if not action:
            continue
        refs = task.get("references") or {}
        role = None
        if action in ("navigate_to", "escort_to", "guide_to"):
            requested = task.get("requested_roles") or []
            candidates = ("destination", "target", "source") if requested else ("target", "destination", "source")
            role = next((key for key in candidates if key in refs and (not requested or key in requested)), None)
        elif action == "locate_entity":
            role = next((key for key in ("target", "actor") if key in refs), None)
        elif action == "avoid_area":
            constraint = next((c for c in grounded_task.get("constraints", [])
                               if c.get("type") == "avoid" and c.get("node_id")
                               and (not multi or str(c.get("key", "")).startswith(f"step_{index}_"))), None)
            if constraint:
                role = constraint.get("key")
                ids = list(dict.fromkeys(c["node_id"] for c in grounded_task.get("constraints", [])
                                         if c.get("key") == role and c.get("node_id")))
                refs = {role: {"node_ids": ids}}
        ref_name = f"step_{index}_{role}" if multi and role in ("actor", "target", "source", "destination") else role
        data = refs.get(role, {})
        ids = data.get("node_ids") or ([data["node_id"]] if data.get("node_id") else [])
        obligation = {"action": action, "ref": ref_name, "node_ids": ids,
                      "count": len(ids) or 1, "intent_step": index}
        prefix = f"step_{index}_" if multi else ""
        if action in ("escort_to", "guide_to"):
            obligation["role_refs"] = {role: prefix + role for role in ("actor", "destination")}
            actor_ids = refs.get("actor", {}).get("node_ids") or [refs.get("actor", {}).get("node_id")]
            obligation["preparation"] = {"action": "locate_entity", "ref": prefix + "actor",
                                          "node_ids": [id for id in actor_ids if id]}
        elif action == "navigate_to" and "source" in refs and "destination" in refs:
            source = refs["source"]
            obligation["preparation"] = {"action": "navigate_to", "ref": prefix + "source",
                "node_ids": source.get("node_ids") or ([source["node_id"]] if source.get("node_id") else [])}
        contract.append(obligation)
    return contract


def _planner_semantic_context(grounded_task: dict) -> dict:
    """Expose grounded IDs and relation/anchor evidence, but not raw coordinates."""
    allowed = {"node_id", "node_ids", "candidate_node_ids", "node_type", "label",
               "candidate_count", "selection_policy", "room_id", "place_id"}
    def project(task):
        references = {}
        for role, data in task.get("references", {}).items():
            projected = {key: value for key, value in data.items() if key in allowed}
            navigation = data.get("navigation_target") or {}
            if navigation.get("navigation_node_id"):
                projected["navigation_node_id"] = navigation["navigation_node_id"]
                projected["navigation_goal_type"] = navigation.get("goal_type")
            candidates = []
            for candidate in data.get("candidate_navigation_targets") or []:
                candidate_navigation = candidate.get("navigation_target") or {}
                item = {key: candidate.get(key) for key in
                        ("node_id", "node_type", "label", "room_id", "place_id")
                        if candidate.get(key) is not None}
                if candidate_navigation.get("navigation_node_id"):
                    item["navigation_node_id"] = candidate_navigation["navigation_node_id"]
                candidates.append(item)
            if candidates:
                projected["candidate_navigation_targets"] = candidates
            relations = list(data.get("relations") or [])
            for predicate, related_id in (("located_in_room", data.get("room_id")),
                                           ("supported_by_place", data.get("place_id"))):
                if related_id:
                    relations.append({"predicate": predicate, "related_node_id": related_id})
            if relations:
                projected["relation_evidence"] = relations
            members = data.get("selected_many") or []
            for key in ("node_type", "label"):
                if projected.get(key):
                    continue
                values = {member.get(key) for member in members if member.get(key)}
                if len(values) == 1:
                    projected[key] = next(iter(values))
            count = data.get("candidate_count")
            if count is None:
                count = len(data.get("candidate_node_ids") or data.get("node_ids")
                            or data.get("selected_many") or [])
                if not count and data.get("node_id"):
                    count = 1
            if count:
                projected["candidate_count"] = count
            if "selection_policy" not in projected:
                if data.get("candidate_node_ids"):
                    projected["selection_policy"] = "deferred_to_nav2"
                elif data.get("node_ids") or data.get("selected_many"):
                    projected["selection_policy"] = "expand_all"
                elif data.get("node_id"):
                    projected["selection_policy"] = "fixed"
            references[role] = projected
        constraints = [
            {key: value for key, value in constraint.items()
             if key in {"key", "type", "node_id", "node_ids", "label", "target_label", "policy"}}
            for constraint in task.get("constraints", [])
        ]
        return {
            "action": task.get("action"),
            "requested_roles": task.get("requested_roles", []),
            "references": references,
            "constraints": constraints,
        }
    context = project(grounded_task)
    context["steps"] = [project(task) for task in grounded_task.get("steps", [])]
    return context


def _intent_plan_prompt_contract(grounded_task: dict) -> list[dict]:
    """Keep the action/ref contract visible to the LLM while hiding node IDs."""
    safe_contract = []
    for obligation in _intent_plan_contract(grounded_task):
        safe = {key: value for key, value in obligation.items() if key != "node_ids"}
        preparation = obligation.get("preparation")
        if preparation:
            safe["preparation"] = {key: value for key, value in preparation.items()
                                   if key != "node_ids"}
        safe_contract.append(safe)
    return safe_contract


def _generate_grounded_plan(user_command: str, grounded_task: dict, model: str, config: dict,
                            draft_callback=None) -> TaskPlan:
    """Generate task plan from grounded task (LLM only decomposes, doesn't select IDs)."""
    # Build available target_refs from grounded task
    available_refs = list(grounded_task.get("references", {}).keys())
    constraint_refs = [c.get("key") or f"constraint_{c['type']}_{i}"
                       for i, c in enumerate(grounded_task.get("constraints", [])) if c.get("node_id")]

    # Build step-specific refs
    step_refs = []
    for i, step_data in enumerate(grounded_task.get("steps", [])):
        for role in step_data.get("references", {}).keys():
            step_refs.append(f"step_{i}_{role}")

    prompt = f"""You are a robot task planner. Output ONLY valid JSON.

TASK: Decompose the grounded task into executable steps.

GROUNDED TASK:
{json.dumps(_planner_semantic_context(grounded_task), indent=2)}

INTENT CONTRACT (preserve every requested action, count, reference and order):
{json.dumps(_intent_plan_prompt_contract(grounded_task), indent=2)}

AVAILABLE ROLES: {available_refs}
STEP-SPECIFIC REFS: {step_refs if step_refs else "none"}
CONSTRAINT REFS: {constraint_refs if constraint_refs else "none"}

RULES:
1. Select target_ref based on the SEMANTIC ROLE of each step:
   - Navigate to the target object → "target"
   - Navigate to the final destination → "destination"
   - Locate the requested object → "target"
   - Escort the person → "actor"
   - Avoid an area → use constraint ref like "constraint_avoid_0"
   - For multi-step: use step-specific refs like "step_0_target", "step_1_destination"

2. Use correct executor:
   - locate_entity → "scene_graph"
   - navigate_to, escort_to, guide_to, avoid_area → "nav2"
   - perform_screening → "external_workflow"
   - wait → "none"

3. Choose exactly one target-resolution mode for each executable step:
   - UNIQUE: target_id is the exact grounded ID for target_ref; candidate_node_ids=[] and
     selection_policy=null.
   - AMBIGUOUS: target_id=null; candidate_node_ids must equal the complete candidate set for that target_ref; selection_policy="deferred_to_nav2".
   These modes are mutually exclusive. Never choose one ID from an ambiguous candidate set.
   Never invent IDs, copy IDs from examples, or use a navigation_node_id as semantic target_id.
   The chosen target_ref and its target_id/candidate_node_ids must come from the same grounded
   reference; IDs from another reference are invalid even if they appear elsewhere in GROUNDED TASK.

4. A simple executable step MUST have a non-empty target_ref from AVAILABLE ROLES or
   STEP-SPECIFIC REFS. For escort_to/guide_to, use target_ref="" and populate role_refs.
   No-reference actions (wait, ask_clarification, return_to_base) use target_ref="".

5. For multi-step tasks:
   - Each step must have a target_ref from available refs
   - Use step-specific refs for step-dependent targets
   - Step N's target_ref should reference step N's grounded entity

6. A candidate set is a valid grounded role. When selection_policy is "deferred_to_nav2" and
   candidate_count > 1, the target is already grounded even though no individual node is selected.
   Use its available semantic role as target_ref exactly as for a unique reference. Keep one semantic action
   for that requested target; keep target_id null, preserve the full candidate_node_ids set, and do not
   expand one ambiguous request into repeated actions. The adapter selects a feasible instance later.

7. Preserve the INTENT CONTRACT exactly. locate means locate_entity, never navigate_to.
   Do not insert navigation before locate, omit requested steps, or reorder them.
   For a resolved plural/set reference required by the intent contract, emit one ordered action per
   required node ID. Do not assume the binder will select or expand instances for LLM2.
   Only a preparation explicitly listed in the contract may precede its main action
   (e.g. locate actor before escort/guide, or visit source before destination).
   Semantic node_type object -> target_type object; room -> room; place -> area;
   agent -> person. Do not use a navigation anchor as the semantic target/type.
   wait, ask_clarification, and return_to_base use an empty target_ref.

8. For constraints.avoid and constraints.prefer, output only the declared constraint role names.
   Use only a supplied explicit route policy for a non-entity preference.

{FORMAT_ONLY_SYNTHETIC_EXAMPLE}

OUTPUT CONTRACT:
Return only a JSON object matching the supplied schema. Include every required field in every
task_chain step, including target_id, candidate_node_ids, selection_policy, target_ref, and
constraints. The complete synthetic example above demonstrates the field structure; its IDs are
fictional and must never be copied. For escort_to/guide_to, set target_ref="" and use role_refs
with grounded role names; apply the same UNIQUE or AMBIGUOUS rule to the selected primary role.
"""

    from ollama import Client
    client = Client(host=get_ollama_host(config),
                    timeout=config.get("ollama_request_timeout_s", 120))
    options = config.get("options", {"temperature": 0.1, "num_ctx": 4096, "num_predict": 2048})

    response = client.chat(
        model=model,
        think=False,
        messages=[
            {"role": "system", "content": "Output valid JSON only. No explanations."},
            {"role": "user", "content": prompt},
        ],
        format=grounded_plan_schema(grounded_task),
        options=options,
    )

    raw_content = response.message.content
    _capture_plan_draft(raw_content, draft_callback)
    if not raw_content:
        raise PlanDraftContractError("LLM returned empty response")

    # Parse as PlanDraft first (enforces grounded target IDs and target_ref)
    plan_draft = parse_grounded_plan_draft(raw_content, grounded_task)

    # Preserve LLM2-selected grounded IDs; the resolver validates and attaches adapter metadata.
    task_plan = TaskPlan(
        status=plan_draft.status,
        task_chain=[],
        clarification_request=plan_draft.clarification_request,
        target_not_found=plan_draft.target_not_found,
        notes=plan_draft.notes,
    )
    for step in plan_draft.task_chain:
        task_plan.task_chain.append(
            TaskStep(
                step=step.step,
                action=step.action,
                target_type=step.target_type,
                target_ref=step.target_ref,
                target_id=step.target_id,
                candidate_node_ids=step.candidate_node_ids,
                selection_policy=step.selection_policy,
                role_refs=step.role_refs,  # P0 #30: copy role_refs from PlanDraft
                description=step.description,
                executor=step.executor,
                constraints=step.constraints,
            )
        )
    task_plan = _fix_constraints(task_plan)
    return task_plan


def _validate_grounded_plan(task_plan: TaskPlan, grounding, grounded_task: dict | None = None) -> list[str]:
    """Validate that all target_ids in plan come from grounding results.

    Checks:
    1. All target_ids exist in grounded references (role-specific)
    2. Role-specific ID correctness (target_ref -> expected node_id)
    3. Action-executor mapping
    4. Action-target_ref validity
    5. target_ref exists in available refs (taskbook 7.3)
    6. step_N_role bound to correct step/role (taskbook 8.1)
    7. Avoid constraints use constraint IDs
    8. Status/task_chain consistency
    9. Missing target_id for executable steps
    10. Step numbering
    11. Duplicate action-target pairs
    """
    import re
    from schema import ACTION_EXECUTOR_MAP, ACTION_TARGET_REFS

    errors = []
    # Reference IDs: actor, target, source, destination
    allowed_ref_ids = grounding.get_resolved_ids()
    # Constraint IDs: avoid, prefer, etc.
    allowed_constraint_ids = grounding.get_constraint_ids()
    # All allowed IDs
    allowed_all_ids = allowed_ref_ids | allowed_constraint_ids
    avoid_ids = grounding.get_constraint_avoid_ids()

    # Build available target_ref set (taskbook 7.3): top-level roles + constraint keys + step refs
    available_refs = set()
    for role in ["actor", "target", "source", "destination"]:
        if grounding.get_resolved_id_by_role(role):
            available_refs.add(role)
    for key in grounding.constraints.keys():
        available_refs.add(key)
    step_ref_to_id = {}
    for step_idx, step_data in enumerate((grounded_task or {}).get("steps", [])):
        for role, ref_data in (step_data.get("references", {}) or {}).items():
            ref_name = f"step_{step_idx}_{role}"
            available_refs.add(ref_name)
            if isinstance(ref_data, dict) and ref_data.get("node_id"):
                step_ref_to_id[ref_name] = ref_data["node_id"]

    # Build role → node_id mapping for role-specific validation
    role_to_id = {}
    for role in ["actor", "target", "source", "destination"]:
        rid = grounding.get_resolved_id_by_role(role)
        if rid:
            role_to_id[role] = rid

    # Build constraint key → node_id mapping
    constraint_key_to_id = {}
    for key, result in grounding.constraints.items():
        if result.status in ("resolved", "resolved_set") and result.selected:
            constraint_key_to_id[key] = result.selected.node_id

    # Actions that require a target_id
    ACTIONS_REQUIRING_TARGET = {
        "navigate_to", "escort_to", "guide_to", "locate_entity",
        "avoid_area", "handover_to_staff", "trigger_workflow",
        "perform_screening",
    }

    expected_ref_ids = {}
    candidate_ref_ids = {}
    semantic_types = {}
    def record_types(reference):
        members = list(reference.get("selected_many") or [reference])
        members.extend(reference.get("candidate_navigation_targets") or [])
        for member in members:
            canonical = str(member.get("node_type") or "").lower()
            mapped = {"objects": "object", "rooms": "room", "place": "area", "places": "area",
                      "agent": "person", "agents": "person"}.get(canonical, canonical)
            if member.get("node_id") and mapped in ("object", "room", "area", "person"):
                semantic_types[member["node_id"]] = mapped
    for role, data in (grounded_task or {}).get("references", {}).items():
        record_types(data)
        ids = set(data.get("node_ids") or ([data["node_id"]] if data.get("node_id") else []))
        if ids:
            expected_ref_ids[role] = ids
        candidate_ids = set(data.get("candidate_node_ids") or [])
        if candidate_ids:
            candidate_ref_ids[role] = candidate_ids
    for index, data in enumerate((grounded_task or {}).get("steps", [])):
        for role, reference in (data.get("references") or {}).items():
            record_types(reference)
            ids = set(reference.get("node_ids") or ([reference["node_id"]] if reference.get("node_id") else []))
            if ids:
                expected_ref_ids[f"step_{index}_{role}"] = ids
            candidate_ids = set(reference.get("candidate_node_ids") or [])
            if candidate_ids:
                candidate_ref_ids[f"step_{index}_{role}"] = candidate_ids
    for ref_name, expected_candidates in candidate_ref_ids.items():
        rag_result = grounding.references.get(ref_name)
        rag_candidates = ({candidate.node_id for candidate in rag_result.candidates}
                          if rag_result and rag_result.status == "ambiguous" else set())
        if rag_candidates != expected_candidates:
            errors.append(f"Candidate IDs for '{ref_name}' do not match raw RAG grounding")
    for ref_name, rag_result in grounding.references.items():
        if rag_result.status != "ambiguous" or not rag_result.candidates:
            continue
        rag_candidates = {candidate.node_id for candidate in rag_result.candidates}
        if candidate_ref_ids.get(ref_name) != rag_candidates:
            errors.append(f"RAG grounding candidate set for '{ref_name}' is missing or mismatched")
    available_refs.update(expected_ref_ids)
    available_refs.update(candidate_ref_ids)
    for ref_name, expected_ids in expected_ref_ids.items():
        rag_result = grounding.references.get(ref_name)
        if rag_result is None:
            actual_ids = set()
        elif rag_result.status == "resolved":
            actual_ids = {rag_result.selected.node_id} if rag_result.selected else set()
        elif rag_result.status == "resolved_set":
            actual_ids = {candidate.node_id for candidate in rag_result.selected_many}
        else:
            actual_ids = set()
        if actual_ids != expected_ids:
            errors.append(f"Resolved IDs for '{ref_name}' do not match raw RAG grounding")

    # Type contracts are derived from the raw resolver output, not its serialized copy.
    semantic_types = {}
    for rag_result in grounding.references.values():
        if rag_result.status == "ambiguous":
            members = rag_result.candidates
        elif rag_result.status == "resolved_set":
            members = rag_result.selected_many
        else:
            members = [rag_result.selected] if rag_result.selected else []
        for member in members:
            canonical = str(member.node_type or "").lower()
            mapped = {"objects": "object", "rooms": "room", "place": "area", "places": "area",
                      "agent": "person", "agents": "person"}.get(canonical, canonical)
            if mapped in ("object", "room", "area", "person"):
                semantic_types[member.node_id] = mapped
    contract = _intent_plan_contract(grounded_task or {})
    if contract:
        if task_plan.status != "valid":
            errors.append("Ready intent contract requires a valid plan")
        offset = 0
        for obligation in contract:
            preparations = []
            allowed_preparation = obligation.get("preparation")
            if allowed_preparation and offset < len(task_plan.task_chain):
                candidate = task_plan.task_chain[offset]
                if (candidate.action == allowed_preparation["action"]
                    and candidate.target_ref == allowed_preparation["ref"]
                    and candidate.target_id in allowed_preparation["node_ids"]):
                    preparations.append(candidate)
                    offset += 1
            segment = task_plan.task_chain[offset:offset + obligation["count"]]
            offset += obligation["count"]
            if len(segment) != obligation["count"]:
                requested_step = ("top-level target" if obligation["intent_step"] < 0
                                  else f"step {obligation['intent_step']}")
                errors.append(f"Intent contract missing requested {requested_step}")
            for step in segment:
                if step.action != obligation["action"]:
                    errors.append(f"Step {step.step}: intent contract requires action '{obligation['action']}'")
                primary_ref = ((step.role_refs or {}).get("destination")
                               if step.action in ("escort_to", "guide_to") else step.target_ref)
                # For single tasks equivalent refs are allowed only with the same
                # canonical identity. Explicit ordered steps must keep their scope.
                if (grounded_task or {}).get("steps") and (primary_ref or None) != obligation["ref"]:
                    errors.append(f"Step {step.step}: intent contract requires ref '{obligation['ref']}'")
                for role, expected_ref in obligation.get("role_refs", {}).items():
                    if (step.role_refs or {}).get(role) != expected_ref:
                        errors.append(f"Step {step.step}: intent contract requires {role} ref '{expected_ref}'")
            if obligation["node_ids"] and {step.target_id for step in segment} != set(obligation["node_ids"]):
                errors.append(f"Intent contract target coverage/order failed at intent step {obligation['intent_step']}")
            for constraint in (grounded_task or {}).get("constraints", []):
                scope = re.match(r"^step_(\d+)_", constraint.get("key") or "")
                if scope and (obligation["intent_step"] < 0
                              or int(scope.group(1)) != obligation["intent_step"]):
                    continue
                for step in preparations + segment:
                    kind = constraint.get("type")
                    if kind in ("avoid", "prefer") and step.executor == "nav2" and constraint.get("node_id"):
                        if constraint["node_id"] not in getattr(step.constraints, kind):
                            errors.append(f"Step {step.step}: scoped {kind} constraint '{constraint['node_id']}' missing")
                    if kind == "condition" and constraint.get("description"):
                        if constraint["description"] not in step.constraints.conditions:
                            errors.append(f"Step {step.step}: scoped condition '{constraint['description']}' missing")
        if offset != len(task_plan.task_chain):
            errors.append("Intent contract contains extra executable steps")
    if task_plan.status != "valid" and task_plan.task_chain:
        errors.append("Non-valid status must not contain an executable task_chain")
    if len(task_plan.task_chain) > 6:
        errors.append("task_chain exceeds the maximum of 6 steps")

    # Check that plan is not empty when status should be valid
    if task_plan.status == "valid" and not task_plan.task_chain:
        errors.append("Status is 'valid' but task_chain is empty")

    # Track seen action-target pairs for duplicate detection
    seen_action_targets = set()

    for i, step in enumerate(task_plan.task_chain):
        # Check step numbering
        if step.step != i + 1:
            errors.append(f"Step numbering: expected {i+1}, got {step.step}")

        expected_executor = ACTION_EXECUTOR_MAP.get(step.action)
        if expected_executor and step.executor != expected_executor:
            errors.append(f"Step {step.step}: action '{step.action}' requires executor '{expected_executor}', got '{step.executor}'")
        if step.action in ("locate_entity", "navigate_to") and step.target_id in semantic_types:
            if step.target_type != semantic_types[step.target_id]:
                errors.append(f"Step {step.step}: semantic target_type must be '{semantic_types[step.target_id]}'")
        if step.action in ("wait", "ask_clarification", "return_to_base"):
            if (step.target_ref or step.target_id or step.role_refs or step.role_ids
                    or step.candidate_node_ids or step.role_candidate_node_ids):
                errors.append(f"Step {step.step}: no-ref action must not carry target or role IDs")
            for avoid_id in step.constraints.avoid:
                if avoid_id not in allowed_all_ids:
                    errors.append(f"Step {step.step}: avoid_id '{avoid_id}' not in grounded references or constraints")
            continue

        # Check target_ref or role_refs is present for executable actions
        if not step.target_ref and not step.role_refs:
            errors.append(f"Step {step.step}: action '{step.action}' requires target_ref or role_refs but got empty")

        # Check action-target_ref validity
        valid_target_refs = ACTION_TARGET_REFS.get(step.action, set())
        if step.target_ref:
            is_valid_ref = (
                step.target_ref in valid_target_refs
                or (bool(re.fullmatch(r"step_\d+_(actor|target|source|destination)", step.target_ref))
                    and step.target_ref.rsplit("_", 1)[-1] in valid_target_refs)
                or (step.action in ("avoid_area", "trigger_workflow") and step.target_ref in constraint_key_to_id)
            )
            if not is_valid_ref:
                errors.append(f"Step {step.step}: action '{step.action}' cannot use target_ref '{step.target_ref}'")

        # taskbook 7.3: target_ref must be an available ref from grounded task
        if step.target_ref and step.target_ref not in available_refs:
            errors.append(f"Step {step.step}: target_ref '{step.target_ref}' not in available refs {sorted(available_refs)}")

        # taskbook 8.1: step-specific ref must bind to the correct step's role id
        if step.target_ref:
            _m = re.match(r"^step_(\d+)_(\w+)$", step.target_ref)
            if _m:
                if (step.target_ref not in step_ref_to_id
                        and step.target_ref not in expected_ref_ids
                        and step.target_ref not in candidate_ref_ids):
                    errors.append(
                        f"Step {step.step}: step-specific ref '{step.target_ref}' has no grounded entity "
                        f"(step {_m.group(1)} role '{_m.group(2)}' missing)"
                    )
                elif step.target_id and step.target_id not in expected_ref_ids.get(step.target_ref, {step_ref_to_id.get(step.target_ref)}):
                    errors.append(
                        f"Step {step.step}: step-specific ref '{step.target_ref}' bound to wrong id "
                        f"(expected {expected_ref_ids.get(step.target_ref) or step_ref_to_id.get(step.target_ref)}, got {step.target_id})"
                    )

        # P0 #30: Validate role_refs
        if step.role_refs:
            for role_name, ref_value in step.role_refs.items():
                if role_name not in ("actor", "target", "source", "destination"):
                    errors.append(f"Step {step.step}: invalid role_refs key '{role_name}'")
                ref_role = ref_value.rsplit("_", 1)[-1] if ref_value else ""
                if ref_role not in valid_target_refs or ref_value not in available_refs:
                    errors.append(f"Step {step.step}: role_refs[{role_name}]='{ref_value}' not in valid refs for '{step.action}'")
                expected_candidates = candidate_ref_ids.get(ref_value, set())
                if expected_candidates:
                    actual_candidates = set(step.role_candidate_node_ids.get(role_name, []))
                    if actual_candidates != expected_candidates:
                        errors.append(f"Step {step.step}: role_candidate_node_ids[{role_name}] do not match grounded ref '{ref_value}'")
                    if (step.role_ids or {}).get(role_name) is not None:
                        errors.append(f"Step {step.step}: deferred role '{role_name}' must not select a singular role_id")
                else:
                    expected_id = role_to_id.get(ref_value) or step_ref_to_id.get(ref_value) or constraint_key_to_id.get(ref_value)
                    if not expected_id or (step.role_ids or {}).get(role_name) != expected_id:
                        errors.append(f"Step {step.step}: role_ids[{role_name}] does not match grounded ref '{ref_value}'")
                if step.action in ("escort_to", "guide_to") and ref_role != role_name:
                    errors.append(f"Step {step.step}: role '{role_name}' cannot bind to '{ref_value}'")

        if step.action in ("escort_to", "guide_to"):
            if not step.role_refs or not {"actor", "destination"}.issubset(step.role_refs):
                errors.append(f"Step {step.step}: escort/guide requires actor and destination role_refs")
            else:
                for role in ("actor", "destination"):
                    ref_name = step.role_refs[role]
                    if ref_name not in role_to_id and ref_name not in candidate_ref_ids:
                        errors.append(f"Step {step.step}: escort/guide role '{role}' has no grounded entity")
                    expected_candidates = candidate_ref_ids.get(ref_name, set())
                    actual_candidates = set(step.role_candidate_node_ids.get(role, []))
                    if expected_candidates != actual_candidates:
                        errors.append(f"Step {step.step}: {role} candidate IDs do not match grounded reference")
                destination_ref = step.role_refs["destination"]
                destination_candidates = candidate_ref_ids.get(destination_ref, set())
                if destination_candidates:
                    if step.target_id is not None:
                        errors.append(f"Step {step.step}: deferred destination selection must not set target_id")
                    if set(step.candidate_node_ids) != destination_candidates:
                        errors.append(f"Step {step.step}: destination candidate IDs do not match grounded reference")
                elif step.target_id != step.role_ids.get("destination"):
                    errors.append(f"Step {step.step}: escort/guide target_id must be destination")

        # P0 #30: Validate role_ids (if resolved)
        if step.role_ids:
            if not step.role_refs:
                errors.append(f"Step {step.step}: role_ids require corresponding role_refs")
            for role_name, node_id in step.role_ids.items():
                if node_id and node_id not in allowed_all_ids:
                    errors.append(f"Step {step.step}: role_ids[{role_name}]='{node_id}' not in grounded references")

        # Check missing target_id for executable steps
        has_deferred_target = bool(step.candidate_node_ids or step.role_candidate_node_ids)
        if step.action in ACTIONS_REQUIRING_TARGET and not step.target_id and not has_deferred_target:
            errors.append(f"Step {step.step}: action '{step.action}' requires target_id but got None")

        if step.candidate_node_ids:
            target_candidate_ref = step.target_ref
            if step.role_refs:
                primary_role = ("destination" if step.action in ("escort_to", "guide_to")
                                else next((role for role in ("target", "destination", "source", "actor")
                                           if role in step.role_refs), None))
                target_candidate_ref = step.role_refs.get(primary_role) if primary_role else None
            expected_candidates = candidate_ref_ids.get(target_candidate_ref, set())
            actual_candidates = set(step.candidate_node_ids)
            if not expected_candidates or actual_candidates != expected_candidates:
                errors.append(f"Step {step.step}: candidate IDs do not match grounded target reference")
            if step.target_id is not None:
                errors.append(f"Step {step.step}: deferred candidate selection must not set target_id")
            if step.selection_policy != "deferred_to_nav2":
                errors.append(f"Step {step.step}: candidate target requires deferred_to_nav2 selection_policy")
        else:
            target_candidate_ref = step.target_ref
            if step.role_refs:
                primary_role = ("destination" if step.action in ("escort_to", "guide_to")
                                else next((role for role in ("target", "destination", "source", "actor")
                                           if role in step.role_refs), None))
                target_candidate_ref = step.role_refs.get(primary_role) if primary_role else None
            if target_candidate_ref in candidate_ref_ids:
                errors.append(f"Step {step.step}: candidate target reference must preserve its candidate IDs")

        for role, candidate_ids in step.role_candidate_node_ids.items():
            if not step.role_refs or role not in step.role_refs:
                errors.append(f"Step {step.step}: candidate role '{role}' is missing from role_refs")
                continue
            expected_candidates = candidate_ref_ids.get(step.role_refs[role], set())
            if set(candidate_ids) != expected_candidates or not expected_candidates:
                errors.append(f"Step {step.step}: role '{role}' candidate IDs do not match grounded reference")

        # Check target_id exists in grounded references
        if step.target_id and step.target_id not in allowed_all_ids:
            errors.append(f"Step {step.step}: target_id '{step.target_id}' not in grounded references")

        # Role-specific ID validation
        if step.target_ref and not step.role_refs and step.target_ref in role_to_id:
            expected_id = role_to_id[step.target_ref]
            if step.target_id and step.target_id not in expected_ref_ids.get(step.target_ref, {expected_id}):
                errors.append(f"Step {step.step}: target_ref='{step.target_ref}' requires {expected_id}, got {step.target_id}")

        if not step.role_refs and step.target_ref in expected_ref_ids and step.target_id not in expected_ref_ids[step.target_ref]:
            errors.append(f"Step {step.step}: target_id does not belong to selected reference set")

        # Check avoid constraints use constraint IDs
        if step.constraints and step.constraints.avoid:
            for avoid_id in step.constraints.avoid:
                if avoid_id not in allowed_all_ids:
                    errors.append(f"Step {step.step}: avoid_id '{avoid_id}' not in grounded references or constraints")

        # Check duplicate action-target pairs
        action_target_key = (step.action, step.target_id or step.target_ref)
        if action_target_key in seen_action_targets and not (contract and (grounded_task or {}).get("steps")):
            errors.append(f"Step {step.step}: duplicate action-target pair '{step.action}' with '{step.target_ref or step.target_id}'")
        seen_action_targets.add(action_target_key)

    # Check that avoid_ids from grounding are in the plan's constraints
    if avoid_ids:
        plan_avoid_ids = set()
        for step in task_plan.task_chain:
            if step.constraints and step.constraints.avoid:
                plan_avoid_ids.update(step.constraints.avoid)
        missing_avoid = avoid_ids - plan_avoid_ids
        if missing_avoid:
            errors.append(f"Avoid constraints not applied: {missing_avoid}")

    # taskbook 11.7/13: prefer constraints must be preserved in the plan
    prefer_ids = grounding.get_constraint_prefer_ids()
    if prefer_ids:
        plan_prefer_ids = set()
        for step in task_plan.task_chain:
            if step.constraints and step.constraints.prefer:
                plan_prefer_ids.update(step.constraints.prefer)
        missing_prefer = prefer_ids - plan_prefer_ids
        if missing_prefer:
            errors.append(f"Prefer constraints not applied: {missing_prefer}")

    required_conditions = {c.get("description") for c in (grounded_task or {}).get("constraints", [])
                           if c.get("type") == "condition" and c.get("description")}
    plan_conditions = {condition for step in task_plan.task_chain for condition in step.constraints.conditions}
    if required_conditions - plan_conditions:
        errors.append(f"Conditions not applied: {sorted(required_conditions - plan_conditions)}")
    return errors


def _collect_grounded_ids(value, id_keys=None) -> set[str]:
    id_keys = id_keys or {"node_id", "node_ids", "target_id", "role_ids",
                          "candidate_node_ids", "navigation_node_id"}
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in id_keys:
                if isinstance(child, str):
                    found.add(child)
                elif isinstance(child, (list, tuple)):
                    found.update(item for item in child if isinstance(item, str))
            found.update(_collect_grounded_ids(child, id_keys))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_collect_grounded_ids(child, id_keys))
    return found


def _safe_plan_repair_summary(raw_draft, task_plan: TaskPlan, available_refs: set[str]) -> dict:
    """Keep plan shape for repair while discarding IDs and free-form text."""
    source = raw_draft if isinstance(raw_draft, dict) else task_plan.model_dump(mode="json")
    allowed_actions = {"navigate_to", "locate_entity", "escort_to", "guide_to", "avoid_area",
                       "wait", "ask_clarification", "handover_to_staff", "trigger_workflow",
                       "perform_screening", "return_to_base"}
    allowed_target_types = {"person", "room", "object", "area", "workflow", "base", "unknown"}
    allowed_executors = {"nav2", "scene_graph", "llm", "external_workflow", "human_staff", "none"}
    steps = []
    for raw in source.get("task_chain", []):
        if not isinstance(raw, dict):
            continue
        step = {}
        number = raw.get("step")
        if isinstance(number, int) and not isinstance(number, bool):
            step["step"] = number
        if raw.get("action") in allowed_actions:
            step["action"] = raw["action"]
        if raw.get("target_type") in allowed_target_types:
            step["target_type"] = raw["target_type"]
        ref = raw.get("target_ref")
        if isinstance(ref, str) and (not ref or ref in available_refs):
            step["target_ref"] = ref
        if raw.get("executor") in allowed_executors:
            step["executor"] = raw["executor"]
        roles = raw.get("role_refs")
        if isinstance(roles, dict):
            safe_roles = {role: ref for role, ref in roles.items()
                          if role in {"actor", "target", "source", "destination"}
                          and isinstance(ref, str) and ref in available_refs}
            if safe_roles:
                step["role_refs"] = safe_roles
        steps.append(step)
    status = source.get("status")
    return {"status": status if status in {"valid", "ambiguous", "target_not_found", "unsupported", "system_error"}
            else task_plan.status, "task_chain": steps}


def _safe_repair_errors(errors: list[str], grounded_task: dict, raw_draft=None) -> list[str]:
    """Redact scene IDs from validator diagnostics before a model repair call."""
    import re

    identifiers = _collect_grounded_ids(grounded_task)
    identifiers.update(_collect_grounded_ids(raw_draft))
    identifiers = sorted(identifiers, key=len, reverse=True)
    sanitized = []
    for error in errors:
        message = str(error)
        for identifier in identifiers:
            if identifier:
                message = message.replace(identifier, "<scene-node>")
        message = re.sub(r"(?<!\d)\d{12,20}(?!\d)", "<scene-node>", message)
        sanitized.append(message)
    return sanitized


def _repair_plan(task_plan: TaskPlan, errors: list[str], grounded_task: dict, model: str, config: dict,
                 draft_callback=None, raw_draft=None) -> TaskPlan:
    """Attempt to repair a plan with validation errors.

    Uses the same ID-grounded PlanDraft contract as initial generation.
    """
    # Build available target_refs from grounded task
    available_refs = list(grounded_task.get("references", {}).keys())

    # Build step-specific refs
    step_refs = []
    for i, step_data in enumerate(grounded_task.get("steps", [])):
        for role in step_data.get("references", {}).keys():
            step_refs.append(f"step_{i}_{role}")

    prompt = f"""The following task plan has validation errors. Fix them.

GROUNDED TASK:
{json.dumps(_planner_semantic_context(grounded_task), indent=2)}

INTENT CONTRACT (preserve actions, coverage, refs and order):
{json.dumps(_intent_plan_prompt_contract(grounded_task), indent=2)}

AVAILABLE TARGET_REFS: {available_refs}
STEP-SPECIFIC REFS: {step_refs if step_refs else "none"}

CURRENT PLAN:
{json.dumps(_safe_plan_repair_summary(raw_draft, task_plan, set(available_refs)), indent=2)}

ERRORS:
{json.dumps(_safe_repair_errors(errors, grounded_task, raw_draft), indent=2)}

RULES:
1. Use only valid target_ref values from available refs or step-specific refs.
2. Choose exactly one target-resolution mode for each executable step:
   - UNIQUE: target_id is the exact grounded ID for target_ref; candidate_node_ids=[] and selection_policy=null.
   - AMBIGUOUS: target_id=null; candidate_node_ids must equal the complete candidate set for that target_ref; selection_policy="deferred_to_nav2".
   Never select one ID from an ambiguous set. The selected target_ref and its IDs must come from the same
   grounded reference. Never copy synthetic example IDs or invent IDs.
3. Use correct executor for each action.
4. For multi-step: use step_N_target, step_N_destination etc.
5. A simple executable step MUST have a non-empty target_ref from the available refs.
   Multi-role escort_to/guide_to steps use target_ref="" and role_refs; no-reference actions
   (wait, ask_clarification, return_to_base) also use target_ref="".
6. Preserve the INTENT CONTRACT. locate -> locate_entity, not navigate_to.
   Keep ordered step scopes and all requested steps. object -> object, room -> room,
   place -> area, agent -> person. wait/ask_clarification use empty refs.
   Only listed preparation steps may precede the corresponding main action.
   Apply global avoid/prefer to every navigation step, and scoped conditions to
   the corresponding intent step (including its preparation, if any).
7. In constraints.avoid/prefer use declared constraint role names, not node IDs
   or object labels; non-entity preferences may use their declared route policy.

Output valid JSON only.
{FORMAT_ONLY_SYNTHETIC_EXAMPLE}
"""

    from ollama import Client
    client = Client(host=get_ollama_host(config),
                    timeout=config.get("ollama_request_timeout_s", 120))
    options = config.get("options", {"temperature": 0.1, "num_ctx": 4096, "num_predict": 2048})

    response = client.chat(
        model=model,
        think=False,
        messages=[
            {"role": "system", "content": "Output valid JSON only. No explanations."},
            {"role": "user", "content": prompt},
        ],
        format=grounded_plan_schema(grounded_task),
        options=options,
    )

    raw_content = response.message.content
    _capture_plan_draft(raw_content, draft_callback)
    if not raw_content:
        raise PlanDraftContractError("LLM repair returned empty response")

    try:
        plan_draft = parse_grounded_plan_draft(raw_content, grounded_task)

        # Convert PlanDraft to TaskPlan
        repaired = TaskPlan(
            status=plan_draft.status,
            task_chain=[],
            clarification_request=plan_draft.clarification_request,
            target_not_found=plan_draft.target_not_found,
            notes=plan_draft.notes,
        )
        for step in plan_draft.task_chain:
            repaired.task_chain.append(
                TaskStep(
                    step=step.step,
                    action=step.action,
                    target_type=step.target_type,
                    target_ref=step.target_ref,
                    target_id=step.target_id,
                    candidate_node_ids=step.candidate_node_ids,
                    selection_policy=step.selection_policy,
                    role_refs=step.role_refs,
                    description=step.description,
                    executor=step.executor,
                    constraints=step.constraints,
                )
            )
        repaired = _fix_constraints(repaired)
        return repaired
    except Exception as e:
        raise ValueError(f"Repair draft schema failed: {e}") from e


def run_baseline_task_planner(user_command: str, model_name: str | None = None) -> TaskPlan:
    """Run the baseline (no-RAG) task planner using pre-built scene_graph.json.

    This loads the original scene_graph.json for comparison.
    """
    config = load_config(CONFIG_PATH)
    model = model_name or get_default_model(config)

    # Load pre-built scene graph (from RAG project)
    scene_graph_path = Path(__file__).parent.parent / "RAG" / "khronos_3dsg_rag" / "data" / "dsg.json"
    if not scene_graph_path.exists():
        # Fallback to original project's scene graph
        scene_graph_path = (
            Path(__file__).resolve().parents[2]
            / "experiment" / "evaluations" / "baseline" / "scene_graph.json"
        )

    with open(scene_graph_path, "r", encoding="utf-8") as f:
        scene_graph = json.load(f)

    relevant_subgraph = extract_relevant_subgraph(user_command, scene_graph)

    user_prompt = build_user_prompt(user_command, relevant_subgraph)
    prompt_for_attempt = user_prompt
    last_raw_content = ""
    last_errors: list[str] = []

    for attempt in range(2):
        raw_content = run_llm_once(model, prompt_for_attempt, config)
        last_raw_content = raw_content
        cleaned = extract_json_object(raw_content)

        try:
            task_plan = TaskPlan.model_validate_json(cleaned)
        except ValidationError as validation_error:
            last_errors = [str(validation_error)]
            if attempt == 1:
                break
            prompt_for_attempt = build_repair_prompt(user_command, relevant_subgraph, raw_content, last_errors)
            continue

        task_plan = canonicalize_task_plan(task_plan, relevant_subgraph, user_command)
        semantic_errors = validate_task_plan_semantics(task_plan, relevant_subgraph, user_command)
        if semantic_errors:
            last_errors = semantic_errors
            if attempt == 1:
                break
            prompt_for_attempt = build_repair_prompt(user_command, relevant_subgraph, raw_content, semantic_errors)
            continue

        return task_plan

    fallback_plan = build_rule_based_transfer_plan(user_command, relevant_subgraph)
    if fallback_plan:
        return fallback_plan

    raise ValueError("Baseline planner failed")


def interactive_mode(mode: str = "structured"):
    """Interactive mode for testing.

    Args:
        mode: Pipeline mode ('structured', 'legacy', or 'baseline')
    """
    print(f"RAG-Enhanced Task Planner (mode: {mode})")
    print("Type 'quit' to exit, 'compare' to run both structured and legacy\n")

    while True:
        try:
            command = input("Navigation instruction: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not command or command.lower() == "quit":
            break

        if command.lower() == "compare":
            command = input("Enter instruction to compare: ").strip()
            if not command:
                continue

            print("\n--- Structured Pipeline ---")
            try:
                structured_result = run_structured_task_planner(command)
                print(_result_json(structured_result))
            except Exception as e:
                print(f"Error: {e}")

            print("\n--- Legacy Pipeline ---")
            try:
                legacy_result = run_legacy_task_planner(command)
                print(legacy_result.model_dump_json(indent=2))
            except Exception as e:
                print(f"Error: {e}")

            print()
            continue

        print(f"\n--- {mode.capitalize()} Pipeline ---")
        try:
            if mode == "structured":
                result = run_structured_task_planner(command)
                print(_result_json(result))
            elif mode == "legacy":
                result = run_legacy_task_planner(command)
                print(result.model_dump_json(indent=2))
            else:
                result = run_baseline_task_planner(command)
                print(result.model_dump_json(indent=2))

            # Save output
            with open(DEFAULT_OUTPUT_PATH, "w", encoding="utf-8") as f:
                if isinstance(result, dict):
                    f.write(_result_json(result))
                else:
                    f.write(result.model_dump_json(indent=2))
            print(f"\nSaved to {DEFAULT_OUTPUT_PATH}")
        except Exception as e:
            print(f"Error: {e}")
        print()


def _result_json(result) -> str:
    """Serialize pipeline models as JSON objects, not repr strings."""
    if hasattr(result, "model_dump_json"):
        return result.model_dump_json(indent=2)
    return json.dumps(result, indent=2)


def main():
    parser = argparse.ArgumentParser(description="RAG-Enhanced Task Planner")
    parser.add_argument("--command", "-c", help="Navigation instruction")
    parser.add_argument("--model", "-m", help="Model name override")
    parser.add_argument("--mode", choices=["structured", "legacy", "baseline"], default="structured",
                       help="Pipeline mode: structured (default), legacy, or baseline")
    parser.add_argument("--compare", action="store_true", help="Compare structured vs legacy")
    parser.add_argument("--no-llm-keywords", action="store_true", help="Skip LLM keyword extraction (legacy mode)")
    parser.add_argument("--db-dir", help="Vector DB directory path")
    parser.add_argument("--output-json", help="Attempt-local JSON output path (default preserved)")
    args = parser.parse_args()
    output_path = Path(args.output_json) if args.output_json else DEFAULT_OUTPUT_PATH

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Use command from config if not provided via CLI
    command = args.command
    if not command:
        config = load_config()
        command = config.get("input_command", "")

    if command:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if args.compare:
            # Compare structured vs legacy
            print("=== Structured Pipeline ===")
            try:
                structured_result = run_structured_task_planner(command, args.model, args.db_dir)
                print(_result_json(structured_result))
                with open(output_path, "w") as f:
                    f.write(_result_json(structured_result))
            except Exception as e:
                print(f"Error: {e}")

            print("\n=== Legacy Pipeline ===")
            try:
                legacy_result = run_legacy_task_planner(command, args.model, not args.no_llm_keywords, args.db_dir)
                print(legacy_result.model_dump_json(indent=2))
                baseline_path = BASE_DIR / "task_plan_output_baseline.json"
                with open(baseline_path, "w") as f:
                    f.write(legacy_result.model_dump_json(indent=2))
            except Exception as e:
                print(f"Error: {e}")
        elif args.mode == "structured":
            # Default: structured pipeline
            result = run_structured_task_planner(command, args.model, args.db_dir)
            print(_result_json(result))
            with open(output_path, "w") as f:
                f.write(_result_json(result))
            print(f"Saved to {output_path}")
        elif args.mode == "legacy":
            # Legacy pipeline
            result = run_legacy_task_planner(command, args.model, not args.no_llm_keywords, args.db_dir)
            print(result.model_dump_json(indent=2))
            with open(output_path, "w") as f:
                f.write(result.model_dump_json(indent=2))
            print(f"Saved to {output_path}")
        else:
            # Baseline (no RAG)
            result = run_baseline_task_planner(command, args.model)
            print(result.model_dump_json(indent=2))
            with open(output_path, "w") as f:
                f.write(result.model_dump_json(indent=2))
            print(f"Saved to {output_path}")
    else:
        interactive_mode(args.mode)


if __name__ == "__main__":
    main()
