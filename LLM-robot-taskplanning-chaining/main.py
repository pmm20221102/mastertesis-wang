from pathlib import Path
import logging
import re
import subprocess
from collections import defaultdict
from typing import Any

import json
import yaml
from ollama import Client
from pydantic import ValidationError

from prompt import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from schema import TaskPlan

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.yaml"
DEFAULT_SCENE_GRAPH_PATH = BASE_DIR / "scene_graph.json"
DEFAULT_OUTPUT_PATH = BASE_DIR / "task_plan_output.json"

# ---------------------------------------------------------------------------
# Action / executor constants
# ---------------------------------------------------------------------------

NAVIGATION_ACTIONS = {"navigate_to", "escort_to", "guide_to", "avoid_area", "return_to_base"}
WORKFLOW_ACTIONS = {"trigger_workflow", "perform_screening"}
EXECUTOR_RULES: dict[str, str] = {
    "locate_entity": "scene_graph",
    "navigate_to": "nav2",
    "escort_to": "nav2",
    "guide_to": "nav2",
    "avoid_area": "nav2",
    "wait": "none",
    "ask_clarification": "llm",
    "handover_to_staff": "human_staff",
    "trigger_workflow": "external_workflow",
    "perform_screening": "external_workflow",
    "return_to_base": "nav2",
}

# ---------------------------------------------------------------------------
# Entity type sets
# ---------------------------------------------------------------------------

ROOM_LIKE_TYPES = {
    "room",
    "patient_room",
    "operating_room",
    "storage_room",
    "waiting_room",
    "pharmacy",
    "staff_station",
    "bathroom",
}
AREA_LIKE_TYPES = {"corridor", "vertical_connection"}
SPECIAL_WORKFLOW_IDS = {"first_screening", "initial_screening"}

# ---------------------------------------------------------------------------
# Scoring constants for entity–instruction matching
# ---------------------------------------------------------------------------

SCORE_EXACT_ALIAS = 15
SCORE_PARTIAL_ALIAS = 5
SCORE_EXACT_ROOM_NUMBER = 25
SCORE_PATIENT_BONUS = 40
SCORE_RESTRICTED_BONUS = 25
SCORE_CORRIDOR_BONUS = 10
SCORE_ROOM_TYPE_BONUS = 5
SCORE_AREA_TYPE_BONUS = 5
SCORE_DESCRIPTION_BONUS = 5
SCORE_MIN_THRESHOLD = 10


# ===================================================================
# Text helpers
# ===================================================================


def normalize_text(text: str) -> str:
    """Lower-case and replace all non-alphanumeric runs with a single space."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def compact_text(text: str) -> str:
    """Lower-case and strip every non-alphanumeric character."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def alias_forms(*values: Any) -> set[str]:
    """Return the set of normalized and compact alias forms for the given values."""
    forms: set[str] = set()
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        normalized = normalize_text(text)
        compact = compact_text(text)
        if normalized:
            forms.add(normalized)
        if compact:
            forms.add(compact)
    return forms


# ===================================================================
# Entity index helpers
# ===================================================================


def build_entity_index(entities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build an ``id → entity`` lookup dict from a list of entity dicts."""
    return {str(entity.get("id")): entity for entity in entities if entity.get("id")}


def detect_intent(command_norm: str) -> tuple[bool, bool, bool]:
    """Detect transfer, screening, and avoid intents from normalized text.

    Returns:
        A ``(transfer_intent, screening_intent, avoid_intent)`` tuple.
    """
    transfer_intent = any(kw in command_norm for kw in {"bring", "escort", "guide", "transfer"}) and "patient" in command_norm
    screening_intent = "screening" in command_norm
    avoid_intent = "avoid" in command_norm
    return transfer_intent, screening_intent, avoid_intent


def find_restricted_entities(entities_by_id: dict[str, dict[str, Any]]) -> set[str]:
    """Return entity IDs whose state or description indicates 'restricted'."""
    return {
        entity_id
        for entity_id, entity in entities_by_id.items()
        if entity.get("state") == "restricted" or "restricted" in normalize_text(entity.get("description", ""))
    }


def target_type_matches_entity(target_type: str, entity: dict[str, Any]) -> bool:
    """Check whether *entity* is compatible with the given *target_type*."""
    entity_type = normalize_text(entity.get("type", ""))
    entity_subtype = normalize_text(entity.get("subtype", ""))
    entity_id = str(entity.get("id", "")).strip().lower()

    if target_type == "person":
        return entity_type == "person"
    if target_type == "room":
        return entity_type in ROOM_LIKE_TYPES or entity_subtype in ROOM_LIKE_TYPES or "room" in entity_type or "room" in entity_subtype
    if target_type == "object":
        return entity_type not in {"person", "floor"} and "room" not in entity_type and "corridor" not in entity_type and entity_type not in {"vertical_connection"}
    if target_type == "area":
        return entity_type in AREA_LIKE_TYPES or "corridor" in entity_type or "corridor" in entity_subtype
    if target_type == "base":
        return entity_type == "entrance" or entity_id == "main_entrance"
    if target_type == "workflow":
        return False
    return True


# ===================================================================
# JSON extraction
# ===================================================================


def extract_json_object(text: str) -> str:
    """Extract the first well-formed JSON object from *text*.

    Strips markdown code fences and walks brace depth to find the complete
    JSON object, handling escaped strings correctly.
    """
    stripped = text.strip()

    if stripped.startswith("```json"):
        stripped = stripped[len("```json"):].strip()
    if stripped.startswith("```"):
        stripped = stripped[len("```"):].strip()
    if stripped.endswith("```"):
        stripped = stripped[:-len("```")].strip()

    start = stripped.find("{")
    if start == -1:
        return stripped

    depth = 0
    in_string = False
    escape = False

    for index in range(start, len(stripped)):
        char = stripped[index]

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return stripped[start:index + 1]

    return stripped[start:]


# ===================================================================
# Entity alias collection
# ===================================================================


def collect_entity_aliases(entity: dict[str, Any]) -> set[str]:
    """Collect all normalised alias forms for a single entity dict."""
    aliases: set[str] = set()
    entity_id = str(entity.get("id", "")).strip()
    if not entity_id:
        return aliases

    aliases.update(alias_forms(entity_id))
    aliases.update(alias_forms(entity.get("name")))
    aliases.update(alias_forms(entity.get("type")))
    aliases.update(alias_forms(entity.get("subtype")))
    aliases.update(alias_forms(entity.get("description")))

    # Room number shorthand: room_201 → R201
    if entity_id.startswith("room_"):
        room_number = entity_id.split("_", 1)[1]
        aliases.update(alias_forms(f"R{room_number}"))
        aliases.update(alias_forms(f"r{room_number}"))

    # Patient alias pattern
    if entity.get("type") == "person" and normalize_text(entity.get("subtype", "")) == "patient":
        aliases.update(alias_forms(f"A_{entity_id.upper()}"))

    # Hardcoded corridor alias
    if entity_id == "corridor_1f_operating":
        aliases.update(alias_forms("P1F_OR"))
        aliases.update(alias_forms("p1f_or"))

    # Restricted corridor
    if entity.get("state") == "restricted":
        aliases.update(alias_forms("restricted corridor"))
        aliases.update(alias_forms("restricted operating corridor"))

    entity_type = normalize_text(entity.get("type", ""))
    entity_subtype = normalize_text(entity.get("subtype", ""))
    description = normalize_text(entity.get("description", ""))

    if entity_type == "person" and entity_subtype == "patient":
        aliases.update(alias_forms("patient"))

    if "corridor" in entity_type or "corridor" in entity_subtype or "corridor" in description:
        aliases.update(alias_forms("corridor"))

    if entity_id == "main_entrance":
        aliases.update(alias_forms("main entrance"))

    return aliases


# ===================================================================
# Entity scoring
# ===================================================================


def _score_alias_matches(entity: dict[str, Any], command_norm: str, command_compact: str) -> int:
    """Score an entity based on alias substring matches within the command."""
    score = 0
    entity_id = str(entity.get("id", "")).strip()
    if not entity_id:
        return 0

    primary_ids = {entity_id, entity.get("name"), entity.get("subtype")}
    aliases = [entity_id, entity.get("name"), entity.get("type"), entity.get("subtype"), entity.get("description")]
    for alias in aliases:
        if not alias:
            continue
        normalized = normalize_text(str(alias))
        compact = compact_text(str(alias))
        if normalized and normalized in command_norm:
            score += SCORE_EXACT_ALIAS if alias in primary_ids else SCORE_PARTIAL_ALIAS
        elif compact and compact in command_compact:
            score += SCORE_EXACT_ALIAS if alias in primary_ids else SCORE_PARTIAL_ALIAS

    return score


def _score_room_number_match(entity: dict[str, Any], command_norm: str) -> int:
    """Score bonus for exact 3-digit room number matches in the entity ID."""
    entity_id = str(entity.get("id", "")).strip()
    for number in re.findall(r"\d{3}", entity_id):
        if re.search(rf"\b{re.escape(number)}\b", command_norm):
            return SCORE_EXACT_ROOM_NUMBER
    return 0


def _score_entity_type_bonus(entity: dict[str, Any], command_norm: str) -> int:
    """Score bonus based on entity type matching intent keywords."""
    score = 0
    entity_type = normalize_text(entity.get("type", ""))
    entity_subtype = normalize_text(entity.get("subtype", ""))

    if entity_type == "person" and entity_subtype == "patient" and "patient" in command_norm:
        score += SCORE_PATIENT_BONUS

    if entity.get("state") == "restricted" and ("restricted" in command_norm or "avoid" in command_norm):
        score += SCORE_RESTRICTED_BONUS

    if "corridor" in entity_type and "corridor" in command_norm:
        score += SCORE_CORRIDOR_BONUS

    if entity_type in ROOM_LIKE_TYPES and ("room" in command_norm or "ward" in command_norm):
        score += SCORE_ROOM_TYPE_BONUS

    if entity_type in AREA_LIKE_TYPES and ("corridor" in command_norm or "area" in command_norm):
        score += SCORE_AREA_TYPE_BONUS

    description = normalize_text(entity.get("description", ""))
    if description and description in command_norm:
        score += SCORE_DESCRIPTION_BONUS

    return score


def score_entity_for_instruction(entity: dict[str, Any], command_norm: str, command_compact: str) -> int:
    """Return a relevance score for *entity* against the user instruction.

    Higher scores indicate stronger relevance.  The score is composed of
    alias matches, room-number matches, and type-based bonuses.
    """
    score = _score_alias_matches(entity, command_norm, command_compact)
    score += _score_room_number_match(entity, command_norm)
    score += _score_entity_type_bonus(entity, command_norm)
    return score


# ===================================================================
# Subgraph expansion
# ===================================================================


def _expand_by_location(initial_entity_ids: set[str], entities_by_id: dict[str, dict[str, Any]]) -> set[str]:
    """Add entities that are the *location* container of an initial entity."""
    expanded: set[str] = set()
    for entity_id in initial_entity_ids:
        entity = entities_by_id.get(entity_id)
        if not entity:
            continue
        location = entity.get("location")
        if isinstance(location, str) and location in entities_by_id:
            expanded.add(location)
    return expanded


def _expand_persons_in_rooms(initial_entity_ids: set[str], entities_by_id: dict[str, dict[str, Any]]) -> set[str]:
    """Add person entities that reside inside an initially-selected room."""
    expanded: set[str] = set()
    for entity_id in initial_entity_ids:
        entity = entities_by_id.get(entity_id)
        if not entity:
            continue
        entity_type = normalize_text(entity.get("type", ""))
        if entity_type in ROOM_LIKE_TYPES:
            for candidate in entities_by_id.values():
                if candidate.get("location") == entity_id and normalize_text(candidate.get("type", "")) == "person":
                    expanded.add(str(candidate["id"]))
    return expanded


def _expand_by_relations(initial_entity_ids: set[str], entities_by_id: dict[str, dict[str, Any]], relations: list[dict[str, Any]]) -> set[str]:
    """Add entities connected to initial entities via scene-graph relations."""
    expanded: set[str] = set()
    for relation in relations:
        from_id = relation.get("from")
        to_id = relation.get("to")
        if from_id in initial_entity_ids and to_id in entities_by_id:
            expanded.add(str(to_id))
        if to_id in initial_entity_ids and from_id in entities_by_id:
            expanded.add(str(from_id))
    return expanded


def expand_relevant_entity_ids(
    initial_entity_ids: set[str],
    entities_by_id: dict[str, dict[str, Any]],
    relations: list[dict[str, Any]],
) -> set[str]:
    """Expand *initial_entity_ids* to include transitively-relevant entities.

    Expansion rules:
    1. Add the location container of each initial entity.
    2. Add persons residing in initially-selected rooms.
    3. Add entities linked by scene-graph relations.
    """
    expanded = set(initial_entity_ids)
    expanded |= _expand_by_location(initial_entity_ids, entities_by_id)
    expanded |= _expand_persons_in_rooms(initial_entity_ids, entities_by_id)
    expanded |= _expand_by_relations(initial_entity_ids, entities_by_id, relations)
    return expanded


# ===================================================================
# Grounding index
# ===================================================================


def canonicalize_identifier(value: str | None, grounding_index: dict[str, str]) -> str | None:
    """Resolve a possibly-aliased identifier through the grounding index.

    Tries exact match, then normalised, then compacted forms.
    Returns the canonical ID if found, otherwise the original value.
    """
    if value is None:
        return None

    candidate = str(value).strip()
    if not candidate:
        return None

    for lookup_key in (candidate, normalize_text(candidate), compact_text(candidate)):
        if lookup_key in grounding_index:
            return grounding_index[lookup_key]

    return candidate


def canonicalize_constraint_list(values: list[str], grounding_index: dict[str, str]) -> list[str]:
    """Resolve every value in a constraint list through the grounding index."""
    return [canonicalize_identifier(value, grounding_index) or value for value in values]


def build_llm_grounding_index(relevant_entities: list[dict[str, Any]], user_command: str) -> dict[str, str]:
    """Build an alias → canonical-ID mapping for the LLM prompt.

    Only unambiguous mappings (exactly one entity per alias) are included.
    Screening workflow IDs are injected when the command mentions screening.
    """
    alias_to_ids: dict[str, set[str]] = defaultdict(set)
    for entity in relevant_entities:
        entity_id = str(entity.get("id", "")).strip()
        if not entity_id:
            continue
        for alias in collect_entity_aliases(entity):
            alias_to_ids[alias].add(entity_id)

    grounding_index: dict[str, str] = {}
    for alias, entity_ids in alias_to_ids.items():
        if len(entity_ids) == 1:
            grounding_index[alias] = next(iter(entity_ids))

    command_norm = normalize_text(user_command)
    if "screening" in command_norm:
        grounding_index.setdefault("first screening", "first_screening")
        grounding_index.setdefault("initial screening", "first_screening")
        grounding_index.setdefault("first_screening", "first_screening")

    return grounding_index


def find_grounded_mentions(user_command: str, grounding_index: dict[str, str]) -> list[str]:
    """Find entity IDs mentioned (via aliases) in *user_command*, in order of appearance."""
    command_norm = normalize_text(user_command)
    command_compact = compact_text(user_command)
    mentions: list[tuple[int, str]] = []

    for alias, exact_id in grounding_index.items():
        normalized_alias = normalize_text(alias)
        compact_alias = compact_text(alias)

        position = -1
        if normalized_alias and normalized_alias in command_norm:
            position = command_norm.find(normalized_alias)
        elif compact_alias and compact_alias in command_compact:
            position = command_compact.find(compact_alias)

        if position >= 0:
            mentions.append((position, exact_id))

    mentions.sort(key=lambda item: item[0])
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for _, exact_id in mentions:
        if exact_id not in seen:
            ordered_ids.append(exact_id)
            seen.add(exact_id)
    return ordered_ids


# ===================================================================
# Task-plan canonicalization
# ===================================================================


def canonicalize_task_plan(task_plan: TaskPlan, relevant_subgraph: dict[str, Any], user_command: str) -> TaskPlan:
    """Map any alias target_ids and constraint values back to canonical IDs.

    Mutates *task_plan* in place and returns it.
    """
    grounding_index = relevant_subgraph.get("llm_grounding_index", {})
    command_norm = normalize_text(user_command)

    for step in task_plan.task_chain:
        if step.target_id:
            step.target_id = canonicalize_identifier(step.target_id, grounding_index)

        if step.action in WORKFLOW_ACTIONS and not step.target_id and "screening" in command_norm:
            step.target_id = canonicalize_identifier("first_screening", grounding_index) or "first_screening"

        step.constraints.avoid = canonicalize_constraint_list(step.constraints.avoid, grounding_index)
        step.constraints.prefer = canonicalize_constraint_list(step.constraints.prefer, grounding_index)
        step.constraints.conditions = canonicalize_constraint_list(step.constraints.conditions, grounding_index)

    return task_plan


# ===================================================================
# Semantic validation — individual checks
# ===================================================================


def _validate_chain_structure(task_plan: TaskPlan) -> list[str]:
    """Check basic task-chain structural invariants."""
    errors: list[str] = []
    if task_plan.status == "valid" and not task_plan.task_chain:
        errors.append("status is valid, but task_chain is empty")
    if len(task_plan.task_chain) > 6:
        errors.append("task_chain exceeds the 6-step limit")
    if task_plan.status != "valid" and task_plan.task_chain:
        errors.append("non-valid status must not include executable task steps")
    return errors


def _validate_transfer_requirements(task_plan: TaskPlan, transfer_intent: bool) -> list[str]:
    """Ensure transfer instructions include the required step sequence."""
    if not transfer_intent:
        return []
    errors: list[str] = []
    if not any(step.action == "locate_entity" and step.target_type == "person" for step in task_plan.task_chain):
        errors.append("transfer instruction requires a locate_entity step for the patient")
    if not any(step.action == "navigate_to" for step in task_plan.task_chain):
        errors.append("transfer instruction requires a navigate_to step")
    if not any(step.action in {"escort_to", "guide_to"} for step in task_plan.task_chain):
        errors.append("transfer instruction requires an escort_to or guide_to step")
    return errors


def _validate_screening_requirement(task_plan: TaskPlan, screening_intent: bool) -> list[str]:
    """Ensure screening instructions include a first_screening workflow step."""
    if not screening_intent:
        return []
    if not any(step.action in WORKFLOW_ACTIONS and step.target_id in {"first_screening", "initial_screening"} for step in task_plan.task_chain):
        return ["screening instruction requires a first_screening workflow step"]
    return []


def _validate_avoid_requirement(task_plan: TaskPlan, avoid_intent: bool, restricted_entity_ids: set[str]) -> list[str]:
    """Ensure avoid instructions reference restricted areas in constraints.avoid."""
    if not avoid_intent or not restricted_entity_ids:
        return []
    avoided_ids = {
        value
        for step in task_plan.task_chain
        for value in step.constraints.avoid
        if value in restricted_entity_ids
    }
    if not avoided_ids:
        return ["avoid instruction requires the restricted area to appear in constraints.avoid or notes"]
    return []


def _validate_step_fields(
    index: int,
    step: Any,
    entities_by_id: dict[str, dict[str, Any]],
    grounding_values: set[str],
    user_command: str,
) -> list[str]:
    """Validate an individual task step's fields (executor, description, grounding, constraints)."""
    errors: list[str] = []

    expected_executor = EXECUTOR_RULES.get(step.action)
    if expected_executor and step.executor != expected_executor:
        errors.append(f"step {index} action {step.action} must use executor {expected_executor}")

    if not step.description.strip():
        errors.append(f"step {index} description is empty")

    if step.action in WORKFLOW_ACTIONS:
        _validate_workflow_step(index, step, grounding_values, user_command, errors)
    elif step.action == "ask_clarification":
        if step.target_type != "unknown":
            errors.append(f"step {index} clarification step should use target_type unknown")
    else:
        _validate_entity_step(index, step, entities_by_id, errors)

    _validate_constraints(index, step, errors)
    return errors


def _validate_workflow_step(
    index: int,
    step: Any,
    grounding_values: set[str],
    user_command: str,
    errors: list[str],
) -> None:
    """Check that a workflow step has correct target_type, target_id, and grounding."""
    if step.target_type != "workflow":
        errors.append(f"step {index} workflow action must use target_type workflow")
    if not step.target_id:
        errors.append(f"step {index} workflow action requires a target_id")
    elif step.target_id not in grounding_values and step.target_id not in SPECIAL_WORKFLOW_IDS:
        if "screening" not in normalize_text(user_command):
            errors.append(f"step {index} workflow target_id {step.target_id} is not grounded")


def _validate_entity_step(
    index: int,
    step: Any,
    entities_by_id: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    """Check that a non-workflow step has a grounded target_id matching its target_type."""
    if not step.target_id:
        errors.append(f"step {index} requires a non-null target_id")
        return
    matched_entity = entities_by_id.get(step.target_id)
    if not matched_entity:
        errors.append(f"step {index} target_id {step.target_id} is not present in the relevant subgraph")
    elif not target_type_matches_entity(step.target_type, matched_entity):
        errors.append(f"step {index} target_type {step.target_type} does not match entity {step.target_id}")


def _validate_constraints(index: int, step: Any, errors: list[str]) -> None:
    """Check that constraint fields are lists of non-empty strings."""
    for field_name, values in (("avoid", step.constraints.avoid), ("prefer", step.constraints.prefer), ("conditions", step.constraints.conditions)):
        if not isinstance(values, list):
            errors.append(f"step {index} constraints.{field_name} must be a list")
            continue
        if any(not isinstance(value, str) or not value.strip() for value in values):
            errors.append(f"step {index} constraints.{field_name} must contain only non-empty strings")


def _validate_step_numbering(task_plan: TaskPlan) -> list[str]:
    """Ensure step numbers are sequential starting at 1."""
    errors: list[str] = []
    for index, step in enumerate(task_plan.task_chain, start=1):
        if step.step != index:
            errors.append(f"step numbers must be sequential starting at 1; expected {index}, got {step.step}")
    return errors


# ===================================================================
# Semantic validation — orchestrator
# ===================================================================


def validate_task_plan_semantics(
    task_plan: TaskPlan,
    relevant_subgraph: dict[str, Any],
    user_command: str,
) -> list[str]:
    """Run all semantic validation checks and return a list of error strings.

    An empty list means the plan passed all checks.
    """
    entities_by_id = build_entity_index(relevant_subgraph.get("entities", []))
    grounding_values = set(relevant_subgraph.get("llm_grounding_index", {}).values())
    command_norm = normalize_text(user_command)

    transfer_intent, screening_intent, avoid_intent = detect_intent(command_norm)
    restricted_entity_ids = find_restricted_entities(entities_by_id)

    errors: list[str] = []
    errors.extend(_validate_chain_structure(task_plan))
    errors.extend(_validate_transfer_requirements(task_plan, transfer_intent))
    errors.extend(_validate_screening_requirement(task_plan, screening_intent))
    errors.extend(_validate_avoid_requirement(task_plan, avoid_intent, restricted_entity_ids))
    errors.extend(_validate_step_numbering(task_plan))

    for index, step in enumerate(task_plan.task_chain, start=1):
        errors.extend(
            _validate_step_fields(index, step, entities_by_id, grounding_values, user_command)
        )

    return errors


# ===================================================================
# Rule-based fallback
# ===================================================================


def build_rule_based_transfer_plan(user_command: str, relevant_subgraph: dict[str, Any]) -> TaskPlan | None:
    """Build a deterministic transfer plan when the LLM fails.

    Handles the pattern: ``"bring patient from A to B"`` with optional
    screening and restricted-area avoidance.
    """
    command_norm = normalize_text(user_command)
    transfer_intent, screening_intent, avoid_intent = detect_intent(command_norm)

    if not transfer_intent:
        return None

    entities_by_id = build_entity_index(relevant_subgraph.get("entities", []))
    grounding_index = relevant_subgraph.get("llm_grounding_index", {})
    mentioned_ids = find_grounded_mentions(user_command, grounding_index)

    # --- Locate patient ---
    patient_id = next(
        (
            entity_id
            for entity_id in mentioned_ids
            if normalize_text(entities_by_id.get(entity_id, {}).get("type", "")) == "person"
            or normalize_text(entities_by_id.get(entity_id, {}).get("subtype", "")) == "patient"
        ),
        None,
    )

    if not patient_id:
        patient_id = next(
            (
                entity_id
                for entity_id, entity in entities_by_id.items()
                if normalize_text(entity.get("type", "")) == "person" and normalize_text(entity.get("subtype", "")) == "patient"
            ),
            None,
        )

    if not patient_id:
        return None

    # --- Source room ---
    source_room_id = entities_by_id.get(patient_id, {}).get("location")
    if not isinstance(source_room_id, str) or source_room_id not in entities_by_id:
        source_room_id = next(
            (
                entity_id
                for entity_id in mentioned_ids
                if target_type_matches_entity("room", entities_by_id.get(entity_id, {}))
            ),
            None,
        )

    if not source_room_id:
        return None

    # --- Destination room ---
    destination_room_id = None
    for entity_id in mentioned_ids:
        if entity_id == source_room_id:
            continue
        entity = entities_by_id.get(entity_id)
        if entity and target_type_matches_entity("room", entity):
            destination_room_id = entity_id
    if not destination_room_id:
        destination_room_id = next(
            (
                entity_id
                for entity_id, entity in entities_by_id.items()
                if target_type_matches_entity("room", entity)
                and entity_id != source_room_id
                and "operating" in normalize_text(entity.get("name", "") + " " + entity.get("description", ""))
            ),
            None,
        )

    if not destination_room_id:
        return None

    restricted_area_id = next(iter(find_restricted_entities(entities_by_id)), None)

    notes: list[str] = []
    avoid_constraint = [restricted_area_id] if avoid_intent and restricted_area_id else []
    if restricted_area_id and avoid_intent:
        notes.append(f"{restricted_area_id} should be avoided if possible or minimized if access to {destination_room_id} requires it.")

    task_chain = [
        {
            "step": 1,
            "action": "locate_entity",
            "target_type": "person",
            "target_id": patient_id,
            "description": f"Locate the patient in {source_room_id}",
            "executor": "scene_graph",
            "constraints": {"avoid": [], "prefer": [], "conditions": []},
        },
        {
            "step": 2,
            "action": "navigate_to",
            "target_type": "room",
            "target_id": source_room_id,
            "description": f"Navigate to {source_room_id} to reach the patient",
            "executor": "nav2",
            "constraints": {"avoid": [], "prefer": [], "conditions": ["patient_located"]},
        },
        {
            "step": 3,
            "action": "escort_to",
            "target_type": "room",
            "target_id": destination_room_id,
            "description": f"Escort the patient from {source_room_id} to {destination_room_id}",
            "executor": "nav2",
            "constraints": {
                "avoid": avoid_constraint,
                "prefer": ["shortest_safe_route"],
                "conditions": [f"robot_arrived_at_{source_room_id}", "patient_ready_for_transfer"],
            },
        },
    ]

    if screening_intent:
        task_chain.append(
            {
                "step": 4,
                "action": "trigger_workflow",
                "target_type": "workflow",
                "target_id": "first_screening",
                "description": f"Trigger the first screening workflow after arrival at {destination_room_id}",
                "executor": "external_workflow",
                "constraints": {
                    "avoid": [],
                    "prefer": [],
                    "conditions": [f"patient_arrived_at_{destination_room_id}"],
                },
            }
        )

    return TaskPlan.model_validate(
        {
            "status": "valid",
            "task_chain": task_chain,
            "clarification_request": None,
            "target_not_found": None,
            "notes": notes,
        }
    )


# ===================================================================
# Subgraph extraction
# ===================================================================


def extract_relevant_subgraph(user_command: str, scene_graph: dict[str, Any]) -> dict[str, Any]:
    """Extract the relevant subgraph from *scene_graph* for *user_command*.

    Scores every entity against the instruction, keeps those above the
    threshold, expands via location / relation links, and builds the
    grounding index for the LLM.
    """
    entities = scene_graph.get("entities", [])
    relations = scene_graph.get("relations", [])
    entities_by_id = build_entity_index(entities)

    command_norm = normalize_text(user_command)
    command_compact = compact_text(user_command)
    scored_entities: list[tuple[int, str]] = []
    for entity in entities:
        entity_id = str(entity.get("id", "")).strip()
        if not entity_id:
            continue
        score = score_entity_for_instruction(entity, command_norm, command_compact)
        if score > 0:
            scored_entities.append((score, entity_id))

    scored_entities.sort(key=lambda item: item[0], reverse=True)
    initial_entity_ids = {entity_id for score, entity_id in scored_entities if score >= SCORE_MIN_THRESHOLD}

    if not initial_entity_ids and scored_entities:
        initial_entity_ids.add(scored_entities[0][1])

    expanded_entity_ids = expand_relevant_entity_ids(initial_entity_ids, entities_by_id, relations)

    relevant_entities = [entities_by_id[eid] for eid in expanded_entity_ids if eid in entities_by_id]
    relevant_relations = [
        relation
        for relation in relations
        if relation.get("from") in expanded_entity_ids or relation.get("to") in expanded_entity_ids
    ]

    grounding_index = build_llm_grounding_index(relevant_entities, user_command)

    return {
        "scene_id": scene_graph.get("scene_id"),
        "scene_type": scene_graph.get("scene_type"),
        "description": scene_graph.get("description"),
        "user_command": user_command,
        "entities": relevant_entities,
        "relations": relevant_relations,
        "llm_grounding_index": grounding_index,
    }


# ===================================================================
# LLM interaction
# ===================================================================


def get_ollama_host(config: dict[str, Any]) -> str:
    """Return the Ollama server URL.

    If ``config['ollama_host']`` is set and non-empty, use it directly.
    Otherwise auto-detect the Windows host IP from the default gateway
    (works reliably in WSL2 where /etc/resolv.conf nameserver fails).
    """
    configured = config.get("ollama_host", "")
    if configured:
        return configured

    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        )
        parts = result.stdout.split()
        for i, part in enumerate(parts):
            if part == "via" and i + 1 < len(parts):
                return f"http://{parts[i + 1]}:11434"
    except Exception:
        pass

    return "http://127.0.0.1:11434"


def run_llm_once(model: str, prompt: str, config: dict[str, Any]) -> str:
    """Send a single prompt to the LLM and return the raw text response.

    Raises:
        ValueError: If the LLM returns an empty response.
    """
    host_url = get_ollama_host(config)
    client = Client(host=host_url, timeout=300)

    response = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        format=TaskPlan.model_json_schema(),
        options=config["options"],
    )

    raw_content = response.message.content
    if not raw_content:
        raise ValueError("LLM 返回内容为空")
    return raw_content


# ===================================================================
# Config
# ===================================================================


def load_config() -> dict:
    """Load and return the YAML configuration file."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_default_model(config: dict) -> str:
    """Return the name of the default model from *config*.

    Falls back to the first model if none is marked ``default: true``.
    """
    for key, info in config["models"].items():
        if info.get("default", False):
            return info["name"]
    return next(iter(config["models"].values()))["name"]


# ===================================================================
# Main entry point
# ===================================================================


def call_llm_task_planner(user_command: str, scene_graph: dict, model_name: str | None = None) -> TaskPlan:
    """Plan a robot task chain from a natural-language instruction.

    Runs the full pipeline: subgraph extraction → LLM call → schema
    validation → canonicalization → semantic validation → repair loop →
    rule-based fallback.

    Args:
        user_command: Natural-language instruction from the user.
        scene_graph: Full hospital scene graph dict.
        model_name: Optional override for the Ollama model name.

    Returns:
        A validated ``TaskPlan`` instance.

    Raises:
        ValueError: If neither the LLM nor the fallback produces a valid plan.
    """
    config = load_config()
    model = model_name or get_default_model(config)
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

    logger.error("模型输出不是合法的 TaskPlan schema 或未通过语义校验。")
    logger.error("原始输出：%s", last_raw_content)
    if last_errors:
        logger.error("校验错误：")
        for error in last_errors:
            logger.error("- %s", error)
    raise ValueError("LLM 输出未通过 schema 或 semantic validator")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    user_command = "Bring the patient from Room 201 to Operating Room 105, avoid the restricted operating corridor if possible, and trigger the first screening workflow after arrival."

    with open(DEFAULT_SCENE_GRAPH_PATH, "r", encoding="utf-8") as f:
        scene_graph = json.load(f)

    result = call_llm_task_planner(user_command, scene_graph)

    logger.info("\n=== Parsed Task Plan ===\n%s", result.model_dump_json(indent=2))

    with open(DEFAULT_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(result.model_dump_json(indent=2))
    logger.info("已保存到 %s", DEFAULT_OUTPUT_PATH)
