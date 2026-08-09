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
from typing import Any

import yaml
from pydantic import ValidationError

from schema import TaskPlan, TaskStep, PlanDraft, Constraints, PipelineResult

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

    # Strict target_id validation: check that all target_ids exist in scene context
    for step in task_plan.task_chain:
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
    """Deterministically resolve target_ref to target_id from grounded task.

    This eliminates LLM hallucination of node IDs by having the program
    fill in the actual IDs based on the role reference.

    Supports:
    - Top-level roles: actor, target, source, destination
    - Step-specific: step_N_target, step_N_source, step_N_destination
    - Constraints: constraint_avoid_X, constraint_prefer_X
    """
    # Build reference lookup from grounded task
    ref_lookup = {}
    for role, ref_data in grounded_task.get("references", {}).items():
        if isinstance(ref_data, dict) and ref_data.get("node_id"):
            ref_lookup[role] = ref_data["node_id"]

    # Build step-specific reference lookup
    for step_idx, step_data in enumerate(grounded_task.get("steps", [])):
        step_refs = step_data.get("references", {})
        for role, ref_data in step_refs.items():
            if isinstance(ref_data, dict) and ref_data.get("node_id"):
                ref_lookup[f"step_{step_idx}_{role}"] = ref_data["node_id"]

    # Build constraint lookup
    constraint_lookup = {}
    for i, constr in enumerate(grounded_task.get("constraints", [])):
        if isinstance(constr, dict) and constr.get("node_id"):
            constraint_lookup[f"constraint_{constr.get('type', 'avoid')}_{i}"] = constr["node_id"]

    # Merge lookups
    all_lookup = {**ref_lookup, **constraint_lookup}

    for step in task_plan.task_chain:
        # NO auto-inference of missing target_ref (taskbook 7.2).
        # Missing target_ref was already rejected at PlanDraft schema level.
        # If it still ends up empty here (no-ref action), skip ID resolution.

        # Resolve target_ref to target_id
        if step.target_ref and step.target_ref in all_lookup:
            step.target_id = all_lookup[step.target_ref]
        elif step.target_ref and step.target_ref.startswith("step_"):
            # Step-specific ref not found — may be multi-step issue
            logger.warning(f"Step {step.step}: target_ref '{step.target_ref}' not found in grounded task refs")

        # P0 #30: Resolve role_refs → role_ids
        if step.role_refs:
            role_ids = {}
            for role_name, ref_value in step.role_refs.items():
                if ref_value in all_lookup:
                    role_ids[role_name] = all_lookup[ref_value]
                elif ref_value.startswith("step_"):
                    logger.warning(f"Step {step.step}: role_refs[{role_name}]='{ref_value}' not found")
                else:
                    role_ids[role_name] = ref_value  # Keep as-is if not resolvable
            step.role_ids = role_ids
            # Also set target_id from first role if not already set
            if not step.target_id and role_ids:
                first_id = next(iter(role_ids.values()), None)
                if first_id:
                    step.target_id = first_id

        # Resolve avoid references in constraints
        if step.constraints and step.constraints.avoid:
            resolved_avoid = []
            for avoid_ref in step.constraints.avoid:
                if avoid_ref in all_lookup:
                    resolved_avoid.append(all_lookup[avoid_ref])
                else:
                    resolved_avoid.append(avoid_ref)
            step.constraints.avoid = resolved_avoid

        # Resolve prefer references in constraints (taskbook 11.7)
        if step.constraints and step.constraints.prefer:
            resolved_prefer = []
            for pref_ref in step.constraints.prefer:
                if pref_ref in all_lookup:
                    resolved_prefer.append(all_lookup[pref_ref])
                else:
                    resolved_prefer.append(pref_ref)
            step.constraints.prefer = resolved_prefer

    return task_plan


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


def run_structured_task_planner(
    user_command: str,
    model_name: str | None = None,
    db_dir: str | None = None,
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

    config = load_config(CONFIG_PATH)
    model = model_name or get_default_model(config)

    # Determine db_dir
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

    # Stage 1: Intent Parsing (LLM extracts semantic intent, no node IDs)
    logger.info("Stage 1: Parsing intent...")
    t0 = time.time()
    try:
        intent = parse_intent_with_llm(user_command, model=model, config=config)
        intent_dict = intent_to_dict(intent)
        latency["intent_ms"] = (time.time() - t0) * 1000
        logger.info(f"Parsed intent: action={intent.action}, "
                   f"target={intent.target.text if intent.target else None}, "
                   f"destination={intent.destination.text if intent.destination else None}")

        # Check for unsupported action
        if intent.action == "unsupported":
            return PipelineResult(
                status="unsupported",
                intent=intent_dict,
                latency_ms=latency,
                error="Task involves manipulation capabilities not supported by the robot",
            )
    except Exception as e:
        latency["intent_ms"] = (time.time() - t0) * 1000
        logger.error(f"Intent parsing failed: {e}")
        return PipelineResult(
            status="system_error",
            intent=intent_dict,
            latency_ms=latency,
            error=f"Intent parsing failed: {e}",
        )

    # Stage 2: Grounding (RAG resolves entity references to node IDs)
    logger.info("Stage 2: Grounding entities...")
    t0 = time.time()
    rag = SceneGraphRAG(db_dir)
    grounding = GroundingOrchestrator(rag).ground_intent(intent)
    grounding_dict = grounding.to_dict()
    latency["grounding_ms"] = (time.time() - t0) * 1000
    logger.info(f"Grounding status: {grounding.status}")

    # Stage 3: Handle non-ready status
    if grounding.status == "ambiguous":
        logger.warning(f"Ambiguous grounding: {grounding.errors}")
        return PipelineResult(
            status="ambiguous",
            intent=intent_dict,
            grounding=grounding_dict,
            latency_ms=latency,
            error="Multiple candidates found. Please provide more specific details.",
        )

    if grounding.status == "not_found":
        logger.warning(f"Entity not found: {grounding.errors}")
        return PipelineResult(
            status="target_not_found",
            intent=intent_dict,
            grounding=grounding_dict,
            latency_ms=latency,
            error="; ".join(grounding.errors),
        )

    if grounding.status == "unsupported":
        return PipelineResult(
            status="unsupported",
            intent=intent_dict,
            grounding=grounding_dict,
            latency_ms=latency,
            error="; ".join(grounding.errors),
        )

    # Stage 4: Build grounded task for planner
    logger.info("Stage 4: Building grounded task...")
    grounded_task = _build_grounded_task(intent, grounding, rag)
    grounded_task_dict = grounded_task

    # Stage 5: Generate plan using grounded facts (LLM only decomposes, doesn't select IDs)
    logger.info("Stage 5: Generating task plan...")
    t0 = time.time()
    try:
        task_plan = _generate_grounded_plan(user_command, grounded_task, model, config)
        # Stage 5.1: Deterministically resolve target_ref → target_id
        task_plan = _resolve_target_refs(task_plan, grounded_task)
        latency["planning_ms"] = (time.time() - t0) * 1000
    except Exception as e:
        latency["planning_ms"] = (time.time() - t0) * 1000
        logger.error(f"Plan generation failed: {e}")
        return PipelineResult(
            status="system_error",
            intent=intent_dict,
            grounding=grounding_dict,
            grounded_task=grounded_task_dict,
            latency_ms=latency,
            error=f"Plan generation failed: {e}",
        )

    # Stage 6: Validate plan against grounding
    logger.info("Stage 6: Validating plan...")
    t0 = time.time()
    validation_errors = _validate_grounded_plan(task_plan, grounding, grounded_task)
    if validation_errors:
        logger.warning(f"Validation errors: {validation_errors}")
        # Try repair once
        try:
            task_plan = _repair_plan(task_plan, validation_errors, grounded_task, model, config)
            # Re-resolve target_ref after repair
            task_plan = _resolve_target_refs(task_plan, grounded_task)
            validation_errors = _validate_grounded_plan(task_plan, grounding, grounded_task)
        except Exception:
            pass
    latency["validation_ms"] = (time.time() - t0) * 1000

    task_plan_dict = json.loads(task_plan.model_dump_json())

    if validation_errors:
        return PipelineResult(
            status="system_error",
            intent=intent_dict,
            grounding=grounding_dict,
            grounded_task=grounded_task_dict,
            task_plan=task_plan_dict,
            validation_errors=validation_errors,
            latency_ms=latency,
            error=f"Plan validation failed: {validation_errors}",
        )

    return PipelineResult(
        status=task_plan.status,
        intent=intent_dict,
        grounding=grounding_dict,
        grounded_task=grounded_task_dict,
        task_plan=task_plan_dict,
        validation_errors=[],
        latency_ms=latency,
    )


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
        "description": intent.description,
        "references": {},
        "constraints": [],
        "steps": [],  # For multi-step intents
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

    # Add constraints
    for key, result in grounding.constraints.items():
        if result.status == "resolved" and result.selected:
            # Extract constraint type from key (e.g., "constraint_avoid_0" -> "avoid")
            parts = key.split("_")
            constraint_type = parts[1] if len(parts) > 1 else "avoid"
            grounded["constraints"].append({
                "type": constraint_type,
                "node_id": result.selected.node_id,
                "node_type": result.selected.node_type,
                "label": result.selected.label,
            })

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
                            "goal_type": nav_target.goal_type,
                        }
                    step_refs[role] = {
                        "node_id": selected.node_id,
                        "node_type": selected.node_type,
                        "label": selected.label,
                        "room_id": selected.room_id,
                        "navigation_target": nav_dict,
                    }

            grounded["steps"].append({
                "action": step.action,
                "description": step.description,
                "references": step_refs,
            })

    return grounded


def _generate_grounded_plan(user_command: str, grounded_task: dict, model: str, config: dict) -> TaskPlan:
    """Generate task plan from grounded task (LLM only decomposes, doesn't select IDs)."""
    # Build available target_refs from grounded task
    available_refs = list(grounded_task.get("references", {}).keys())
    constraint_refs = [f"constraint_{c['type']}_{i}" for i, c in enumerate(grounded_task.get("constraints", []))]

    # Build step-specific refs
    step_refs = []
    for i, step_data in enumerate(grounded_task.get("steps", [])):
        for role in step_data.get("references", {}).keys():
            step_refs.append(f"step_{i}_{role}")

    prompt = f"""You are a robot task planner. Output ONLY valid JSON.

TASK: Decompose the grounded task into executable steps.

GROUNDED TASK:
{json.dumps(grounded_task, indent=2)}

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

3. Do NOT output node IDs. The system resolves roles to IDs.

4. Every executable step MUST have a non-empty target_ref from AVAILABLE ROLES
   or STEP-SPECIFIC REFS. A missing or invalid target_ref is a fatal schema error —
   never leave target_ref empty.

5. For multi-step tasks:
   - Each step must have a target_ref from available refs
   - Use step-specific refs for step-dependent targets
   - Step N's target_ref should reference step N's grounded entity

OUTPUT:
{{
  "status": "valid",
  "task_chain": [
    {{"step": 1, "action": "navigate_to", "target_type": "object", "target_ref": "target", "description": "Navigate to chair", "executor": "nav2", "constraints": {{"avoid": [], "prefer": [], "conditions": []}}}}
  ]
}}

For multi-role actions (escort_to, guide_to), use role_refs:
{{
  "step": 1, "action": "escort_to", "target_type": "person", "target_ref": "",
  "role_refs": {{"actor": "actor", "destination": "destination"}},
  "description": "Escort person to destination", "executor": "nav2",
  "constraints": {{"avoid": [], "prefer": [], "conditions": []}}
}}
"""

    from ollama import Client
    client = Client(host=get_ollama_host(config), timeout=120)
    options = config.get("options", {"temperature": 0.1, "num_ctx": 4096, "num_predict": 2048})

    response = client.chat(
        model=model,
        think=False,
        messages=[
            {"role": "system", "content": "Output valid JSON only. No explanations."},
            {"role": "user", "content": prompt},
        ],
        format=PlanDraft.model_json_schema(),
        options=options,
    )

    raw_content = response.message.content
    if not raw_content:
        raise ValueError("LLM returned empty response")

    # Parse as PlanDraft first (enforces target_ref)
    plan_draft = PlanDraft.model_validate_json(raw_content)

    # Convert PlanDraft to TaskPlan (target_id will be filled later by _resolve_target_refs)
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
                target_id=None,  # Will be filled by _resolve_target_refs
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
        if result.status == "resolved" and result.selected:
            constraint_key_to_id[key] = result.selected.node_id

    # Actions that require a target_id
    ACTIONS_REQUIRING_TARGET = {
        "navigate_to", "escort_to", "guide_to", "locate_entity",
        "avoid_area", "handover_to_staff", "trigger_workflow",
        "perform_screening",
    }

    # Check that plan is not empty when status should be valid
    if task_plan.status == "valid" and not task_plan.task_chain:
        errors.append("Status is 'valid' but task_chain is empty")

    # Track seen action-target pairs for duplicate detection
    seen_action_targets = set()

    for i, step in enumerate(task_plan.task_chain):
        # Check step numbering
        if step.step != i + 1:
            errors.append(f"Step numbering: expected {i+1}, got {step.step}")

        # Skip non-executable steps
        if step.action in ("wait", "ask_clarification", "return_to_base"):
            continue

        # Check target_ref or role_refs is present for executable actions
        if not step.target_ref and not step.role_refs:
            errors.append(f"Step {step.step}: action '{step.action}' requires target_ref or role_refs but got empty")

        # Check action-executor mapping
        expected_executor = ACTION_EXECUTOR_MAP.get(step.action)
        if expected_executor and step.executor != expected_executor:
            errors.append(f"Step {step.step}: action '{step.action}' requires executor '{expected_executor}', got '{step.executor}'")

        # Check action-target_ref validity
        valid_target_refs = ACTION_TARGET_REFS.get(step.action, set())
        if step.target_ref:
            is_valid_ref = (
                step.target_ref in valid_target_refs
                or step.target_ref.startswith("step_")
                or step.target_ref.startswith("constraint_")
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
                if step.target_ref not in step_ref_to_id:
                    errors.append(
                        f"Step {step.step}: step-specific ref '{step.target_ref}' has no grounded node_id "
                        f"(step {_m.group(1)} role '{_m.group(2)}' missing)"
                    )
                elif step.target_id and step.target_id != step_ref_to_id[step.target_ref]:
                    errors.append(
                        f"Step {step.step}: step-specific ref '{step.target_ref}' bound to wrong id "
                        f"(expected {step_ref_to_id[step.target_ref]}, got {step.target_id})"
                    )

        # P0 #30: Validate role_refs
        if step.role_refs:
            for role_name, ref_value in step.role_refs.items():
                if role_name not in ("actor", "target", "source", "destination"):
                    errors.append(f"Step {step.step}: invalid role_refs key '{role_name}'")
                if ref_value and ref_value not in valid_target_refs and not ref_value.startswith("step_") and not ref_value.startswith("constraint_"):
                    errors.append(f"Step {step.step}: role_refs[{role_name}]='{ref_value}' not in valid refs for '{step.action}'")

        # P0 #30: Validate role_ids (if resolved)
        if step.role_ids:
            for role_name, node_id in step.role_ids.items():
                if node_id and node_id not in allowed_all_ids:
                    errors.append(f"Step {step.step}: role_ids[{role_name}]='{node_id}' not in grounded references")

        # Check missing target_id for executable steps
        if step.action in ACTIONS_REQUIRING_TARGET and not step.target_id:
            errors.append(f"Step {step.step}: action '{step.action}' requires target_id but got None")

        # Check target_id exists in grounded references
        if step.target_id and step.target_id not in allowed_all_ids:
            errors.append(f"Step {step.step}: target_id '{step.target_id}' not in grounded references")

        # Role-specific ID validation
        if step.target_ref and step.target_ref in role_to_id:
            expected_id = role_to_id[step.target_ref]
            if step.target_id and step.target_id != expected_id:
                errors.append(f"Step {step.step}: target_ref='{step.target_ref}' requires {expected_id}, got {step.target_id}")

        # Check avoid constraints use constraint IDs
        if step.constraints and step.constraints.avoid:
            for avoid_id in step.constraints.avoid:
                if avoid_id not in allowed_all_ids:
                    errors.append(f"Step {step.step}: avoid_id '{avoid_id}' not in grounded references or constraints")

        # Check duplicate action-target pairs
        action_target_key = (step.action, step.target_ref or step.target_id)
        if action_target_key in seen_action_targets:
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

    return errors


def _repair_plan(task_plan: TaskPlan, errors: list[str], grounded_task: dict, model: str, config: dict) -> TaskPlan:
    """Attempt to repair a plan with validation errors.

    Uses PlanDraft schema to ensure LLM doesn't output raw node IDs.
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
{json.dumps(grounded_task, indent=2)}

AVAILABLE TARGET_REFS: {available_refs}
STEP-SPECIFIC REFS: {step_refs if step_refs else "none"}

CURRENT PLAN:
{task_plan.model_dump_json(indent=2)}

ERRORS:
{json.dumps(errors, indent=2)}

RULES:
1. Use only valid target_ref values from available refs or step-specific refs.
2. Do NOT output raw node IDs (numbers). The system resolves target_ref to IDs.
3. Use correct executor for each action.
4. For multi-step: use step_N_target, step_N_destination etc.
5. Every executable step MUST have a non-empty target_ref from the available refs.
   A missing target_ref is exactly what caused the validation error — fix it.

Output valid JSON only.
"""

    from ollama import Client
    client = Client(host=get_ollama_host(config), timeout=120)
    options = config.get("options", {"temperature": 0.1, "num_ctx": 4096, "num_predict": 2048})

    response = client.chat(
        model=model,
        think=False,
        messages=[
            {"role": "system", "content": "Output valid JSON only. No explanations."},
            {"role": "user", "content": prompt},
        ],
        format=PlanDraft.model_json_schema(),
        options=options,
    )

    raw_content = response.message.content
    if not raw_content:
        return task_plan  # Return original if repair fails

    try:
        plan_draft = PlanDraft.model_validate_json(raw_content)

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
                    target_id=None,
                    description=step.description,
                    executor=step.executor,
                    constraints=step.constraints,
                )
            )
        repaired = _fix_constraints(repaired)
        return repaired
    except Exception as e:
        logger.warning(f"Repair failed: {e}")
        return task_plan  # Return original on failure


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
        scene_graph_path = Path(__file__).parent.parent / "LLM-robot-taskplanning-chaining" / "scene_graph.json"

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
                print(json.dumps(structured_result, indent=2, default=str))
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
                print(json.dumps(result, indent=2, default=str))
            elif mode == "legacy":
                result = run_legacy_task_planner(command)
                print(result.model_dump_json(indent=2))
            else:
                result = run_baseline_task_planner(command)
                print(result.model_dump_json(indent=2))

            # Save output
            with open(DEFAULT_OUTPUT_PATH, "w", encoding="utf-8") as f:
                if isinstance(result, dict):
                    json.dump(result, f, indent=2, default=str)
                else:
                    f.write(result.model_dump_json(indent=2))
            print(f"\nSaved to {DEFAULT_OUTPUT_PATH}")
        except Exception as e:
            print(f"Error: {e}")
        print()


def main():
    parser = argparse.ArgumentParser(description="RAG-Enhanced Task Planner")
    parser.add_argument("--command", "-c", help="Navigation instruction")
    parser.add_argument("--model", "-m", help="Model name override")
    parser.add_argument("--mode", choices=["structured", "legacy", "baseline"], default="structured",
                       help="Pipeline mode: structured (default), legacy, or baseline")
    parser.add_argument("--compare", action="store_true", help="Compare structured vs legacy")
    parser.add_argument("--no-llm-keywords", action="store_true", help="Skip LLM keyword extraction (legacy mode)")
    parser.add_argument("--db-dir", help="Vector DB directory path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Use command from config if not provided via CLI
    command = args.command
    if not command:
        config = load_config()
        command = config.get("input_command", "")

    if command:
        if args.compare:
            # Compare structured vs legacy
            print("=== Structured Pipeline ===")
            try:
                structured_result = run_structured_task_planner(command, args.model, args.db_dir)
                print(json.dumps(structured_result, indent=2, default=str))
                with open(DEFAULT_OUTPUT_PATH, "w") as f:
                    json.dump(structured_result, f, indent=2, default=str)
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
            print(json.dumps(result, indent=2, default=str))
            with open(DEFAULT_OUTPUT_PATH, "w") as f:
                json.dump(result, f, indent=2, default=str)
            print(f"Saved to {DEFAULT_OUTPUT_PATH}")
        elif args.mode == "legacy":
            # Legacy pipeline
            result = run_legacy_task_planner(command, args.model, not args.no_llm_keywords, args.db_dir)
            print(result.model_dump_json(indent=2))
            with open(DEFAULT_OUTPUT_PATH, "w") as f:
                f.write(result.model_dump_json(indent=2))
            print(f"Saved to {DEFAULT_OUTPUT_PATH}")
        else:
            # Baseline (no RAG)
            result = run_baseline_task_planner(command, args.model)
            print(result.model_dump_json(indent=2))
            with open(DEFAULT_OUTPUT_PATH, "w") as f:
                f.write(result.model_dump_json(indent=2))
            print(f"Saved to {DEFAULT_OUTPUT_PATH}")
    else:
        interactive_mode(args.mode)


if __name__ == "__main__":
    main()
