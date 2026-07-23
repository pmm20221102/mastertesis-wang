"""Intent parser for LLM task planning.

Supports:
1. Deterministic fast path for exact node IDs
2. LLM structured parser with role rules
3. Intent semantic validator/canonicalizer
4. Spurious role detection and removal
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

# Add parent directory to path for local schemas import
sys.path.insert(0, str(Path(__file__).parent.parent))

from ollama import Client

from planner_schemas.intent import EntityReference, TaskIntent, TaskConstraint

logger = logging.getLogger(__name__)

# Patterns for exact node IDs - only match explicit "node/entity/target ID" patterns
# NOT general 12-20 digit numbers (which could be room/place IDs)
EXACT_TARGET_PATTERNS = [
    r"\b(?:node|entity|target)\s+(?:id\s*)?(\d{12,20})\b",
    r"\b(?:navigate|go|move)\s+to\s+(?:node|entity)\s+(\d{12,20})\b",
    r"\b(?:find|locate|search)\s+(?:node|entity)\s+(\d{12,20})\b",
]

# Pattern for room/place IDs in constraints (e.g., "in room 807...")
ROOM_ID_PATTERN = r"\b(?:room|place|in)\s+(\d{12,20})\b"

# Actions that should NOT have source/destination
LOCATE_ACTIONS = {"locate"}
# Actions that should have actor
ESCORT_ACTIONS = {"escort", "guide"}
# Actions that should have target or destination
NAVIGATE_ACTIONS = {"navigate"}

INTENT_SYSTEM_PROMPT = """You are a robot task intent parser.

Convert natural language robot instructions into structured TaskIntent JSON.

RULES:
1. Extract the action and entity references from the instruction.
2. Do NOT invent or output node IDs (long numeric identifiers).
3. PRESERVE room numbers, floor numbers, door numbers explicitly provided by the user (e.g., "Room 201").
4. Use natural language descriptions for entities (e.g., "the chair near the desk").
5. For manipulation tasks (pick, grasp, carry, place), set action to "unsupported".
6. Return ONLY valid JSON, no explanations.

ROLE RULES:
- locate/find instructions: Only set "target". Do NOT create "source" or "destination".
- navigate instructions: Set "target" (where to go) or "destination" (final destination).
- "from A to B" instructions: Set "source" (starting point) AND "destination" (ending point).
- escort/guide instructions: Set "actor" (person being escorted) AND "destination".
- Only create roles that are explicitly mentioned or clearly implied.

Output format:
{
  "action": "navigate|escort|guide|avoid|locate|wait|ask_clarification|unsupported",
  "actor": {"text": "...", "label": "...", "node_type": "...", "room": "..."} or null,
  "target": {"text": "...", "label": "...", "node_type": "...", "room": "...", "relations": [...], "quantifier": "one|all|nearest|any"} or null,
  "source": {"text": "...", "label": "...", "node_type": "..."} or null,
  "destination": {"text": "...", "label": "...", "node_type": "..."} or null,
  "constraints": [{"type": "avoid|prefer|condition", "description": "...", "target_text": "...", "target_label": "..."}],
  "description": "brief description of the task"
}

FEW-SHOT EXAMPLES:

1. "Navigate to the chair"
→ {"action": "navigate", "target": {"text": "chair", "label": "chairs", "node_type": "objects"}}

2. "Find the chair in room 8070450532247930074"
→ {"action": "locate", "target": {"text": "chair", "label": "chairs", "node_type": "objects", "room": "8070450532247930074"}}

3. "Find all chairs"
→ {"action": "locate", "target": {"text": "chairs", "label": "chairs", "node_type": "objects", "quantifier": "all"}}

4. "Find the chair near the desk"
→ {"action": "locate", "target": {"text": "chair", "label": "chairs", "node_type": "objects", "relations": [{"relation": "near", "target_text": "desk", "target_label": "desks"}]}}

5. "Escort the person to the entrance"
→ {"action": "escort", "actor": {"text": "person", "node_type": "agents"}, "destination": {"text": "entrance", "label": "entrance", "node_type": "places"}}

6. "Navigate from the office to the kitchen"
→ {"action": "navigate", "source": {"text": "office", "node_type": "rooms"}, "destination": {"text": "kitchen", "node_type": "rooms"}}

7. "Pick up the chair"
→ {"action": "unsupported"}
"""


def _detect_exact_node_id(text: str) -> str | None:
    """Detect exact node ID in text.

    Only matches explicit "node/entity/target ID" patterns.
    Does NOT match room/place IDs in constraints.
    """
    text_lower = text.lower()

    # First check if this is a room/place constraint (e.g., "in room 807...")
    # If so, don't use fast path - let LLM parse the intent
    if re.search(ROOM_ID_PATTERN, text_lower):
        # Check if the room ID is the main target or just a constraint
        # If there's also a target pattern, don't use fast path
        has_target_pattern = any(re.search(p, text_lower) for p in EXACT_TARGET_PATTERNS)
        if not has_target_pattern:
            # Room ID is a constraint, not the target - don't use fast path
            return None

    # Check for explicit target node patterns
    for pattern in EXACT_TARGET_PATTERNS:
        matches = re.search(pattern, text_lower)
        if matches:
            return matches.group(1)

    return None


def _detect_action_from_text(text: str) -> str:
    """Detect action from natural language text."""
    text_lower = text.lower()
    if any(w in text_lower for w in ["pick up", "grasp", "carry", "place", "lift"]):
        return "unsupported"
    if any(w in text_lower for w in ["escort", "guide", "accompany"]):
        return "escort"
    if any(w in text_lower for w in ["find", "locate", "search"]):
        return "locate"
    if any(w in text_lower for w in ["navigate", "go to", "go to the"]):
        return "navigate"
    if any(w in text_lower for w in ["avoid", "bypass", "skip"]):
        return "avoid"
    if any(w in text_lower for w in ["wait", "pause"]):
        return "wait"
    if any(w in text_lower for w in ["ask", "clarify"]):
        return "ask_clarification"
    return "navigate"  # Default


def _build_intent_from_exact_id(node_id: str, action: str) -> TaskIntent:
    """Build TaskIntent directly from exact node ID without LLM."""
    return TaskIntent(
        action=action,
        target=EntityReference(
            text=node_id,
            node_type=None,
        ),
        description=f"Target node {node_id}",
    )


def validate_and_canonicalize_intent(
    user_command: str,
    intent: TaskIntent,
) -> TaskIntent:
    """Validate and canonicalize parsed intent.

    Removes spurious roles and fixes common LLM errors.
    """
    text_lower = user_command.lower()

    # Action deterministic override - don't let small model misclassify actions
    rule_action = _detect_action_from_text(user_command)
    if rule_action != "navigate":  # navigate is default, so only override if unambiguous
        if intent.action != rule_action:
            logger.info(f"Overriding action from '{intent.action}' to '{rule_action}' based on text")
            intent.action = rule_action

    # Detect if user explicitly mentions from/to
    has_from = any(w in text_lower for w in ["from", "starting at", "beginning at"])
    has_to = any(w in text_lower for w in ["to", "towards", "into"])

    # Rule 1: locate/find should NOT have source/destination
    if intent.action in LOCATE_ACTIONS:
        if intent.source and not has_from:
            logger.info(f"Removing spurious source for locate action")
            intent.source = None
        if intent.destination and not has_to:
            logger.info(f"Removing spurious destination for locate action")
            intent.destination = None

    # Rule 2: navigate single target should not have both target and destination pointing to same entity
    if intent.action in NAVIGATE_ACTIONS:
        if intent.target and intent.destination:
            if intent.target.text == intent.destination.text:
                # Same entity in both, remove destination
                intent.destination = None

    # Rule 3: escort/guide must have actor
    if intent.action in ESCORT_ACTIONS:
        if not intent.actor:
            # Try to infer from text
            if "person" in text_lower or "patient" in text_lower:
                intent.actor = EntityReference(text="person", node_type="agents")

    # Rule 4: Remove room/place ID from room field if it's the same as target
    if intent.target and intent.target.room:
        # Keep room as-is for now (will be resolved during grounding)
        pass

    # Rule 5: Remove constraints that weren't mentioned by user
    if intent.constraints:
        # Check if user actually mentioned avoid/prefer
        has_avoid = any(w in text_lower for w in ["avoid", "bypass", "skip", "don't go"])
        has_prefer = any(w in text_lower for w in ["prefer", "preferably", "if possible"])

        cleaned_constraints = []
        for c in intent.constraints:
            if c.type == "avoid" and not has_avoid:
                # Check if constraint target was explicitly mentioned
                if c.target_text and c.target_text.lower() in text_lower:
                    cleaned_constraints.append(c)
                else:
                    logger.info(f"Removing spurious avoid constraint: {c.description}")
            elif c.type == "prefer" and not has_prefer:
                if c.target_text and c.target_text.lower() in text_lower:
                    cleaned_constraints.append(c)
                else:
                    logger.info(f"Removing spurious prefer constraint: {c.description}")
            else:
                cleaned_constraints.append(c)
        intent.constraints = cleaned_constraints

    # Rule 6: quantifier should match user text
    if intent.target:
        if any(w in text_lower for w in ["all", "every", "each"]):
            intent.target.quantifier = "all"
        elif any(w in text_lower for w in ["nearest", "closest", "nearby"]):
            intent.target.quantifier = "nearest"

    # Rule 7: Remove spurious relations not mentioned by user
    if intent.target and intent.target.relations:
        cleaned_relations = []
        for rel in intent.target.relations:
            # Check if relation target was mentioned in user text
            if rel.target_text and rel.target_text.lower() in text_lower:
                cleaned_relations.append(rel)
            else:
                logger.info(f"Removing spurious relation: {rel.relation} {rel.target_text}")
        intent.target.relations = cleaned_relations

    # Rule 8: Remove spurious room constraint if not mentioned
    if intent.target and intent.target.room:
        # Check if room was explicitly mentioned (as number or name)
        room_text = intent.target.room.lower()
        if room_text not in text_lower and not any(w in text_lower for w in ["room", "place", "office"]):
            logger.info(f"Removing spurious room constraint: {intent.target.room}")
            intent.target.room = None

    return intent


def parse_intent_with_llm(
    user_command: str,
    model: str = "mistral",
    config: dict | None = None,
) -> TaskIntent:
    """Parse natural language command into structured TaskIntent using LLM.

    Supports:
    1. Exact-ID fast path (no LLM needed)
    2. LLM structured parser
    3. Intent semantic validation/canonicalization
    """
    from main_rag import get_ollama_host, load_config, CONFIG_PATH

    if config is None:
        config = {}

    # Step 1: Check for exact node ID fast path
    exact_id = _detect_exact_node_id(user_command)
    if exact_id:
        logger.info(f"Exact node ID detected: {exact_id}")
        action = _detect_action_from_text(user_command)
        intent = _build_intent_from_exact_id(exact_id, action)
        return intent

    # Step 2: LLM structured parsing
    ollama_host = config.get("ollama_host", "")
    if not ollama_host:
        ollama_host = get_ollama_host(config)

    client = Client(host=ollama_host, timeout=120)

    options = config.get("options", {
        "temperature": 0.1,
        "num_ctx": 4096,
        "num_predict": 512,
    })

    # Use Ollama structured output with JSON schema
    response = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_command},
        ],
        format=TaskIntent.model_json_schema(),
        options=options,
    )

    raw_content = response.message.content
    if not raw_content:
        raise ValueError("LLM returned empty response")

    # Parse using Pydantic validation
    try:
        intent = TaskIntent.model_validate_json(raw_content)
    except Exception as e:
        logger.error(f"Failed to validate TaskIntent: {e}")
        logger.error(f"Raw response: {raw_content}")
        raise ValueError(f"Invalid TaskIntent from LLM: {e}")

    # Step 3: Validate and canonicalize
    intent = validate_and_canonicalize_intent(user_command, intent)

    return intent


def _parse_entity_ref(data: dict | None) -> EntityReference | None:
    """Parse entity reference from dict."""
    if not data or not data.get("text"):
        return None
    return EntityReference(
        text=data["text"],
        node_type=data.get("node_type"),
        label=data.get("label"),
        name=data.get("name"),
        room=data.get("room"),
        relations=data.get("relations", []),
        attributes=data.get("attributes", {}),
        position=data.get("position"),
        quantifier=data.get("quantifier", "one"),
    )


def _parse_constraint(data: dict) -> TaskConstraint:
    """Parse constraint from dict."""
    return TaskConstraint(
        type=data.get("type", "avoid"),
        description=data.get("description", ""),
        target_text=data.get("target_text"),
        target_label=data.get("target_label"),
    )


def intent_to_dict(intent: TaskIntent) -> dict[str, Any]:
    """Convert TaskIntent to dictionary for serialization."""
    return {
        "action": intent.action,
        "actor": _entity_ref_to_dict(intent.actor) if intent.actor else None,
        "target": _entity_ref_to_dict(intent.target) if intent.target else None,
        "source": _entity_ref_to_dict(intent.source) if intent.source else None,
        "destination": _entity_ref_to_dict(intent.destination) if intent.destination else None,
        "constraints": [
            {
                "type": c.type,
                "description": c.description,
                "target_text": c.target_text,
                "target_label": c.target_label,
            }
            for c in intent.constraints
        ],
        "description": intent.description,
    }


def _entity_ref_to_dict(ref: EntityReference) -> dict[str, Any]:
    """Convert EntityReference to dictionary."""
    result = {
        "text": ref.text,
        "node_type": ref.node_type,
        "label": ref.label,
        "name": ref.name,
        "room": ref.room,
        "attributes": ref.attributes,
        "position": ref.position,
        "quantifier": ref.quantifier,
    }
    if ref.relations:
        result["relations"] = [
            {
                "relation": r.relation,
                "target_text": r.target_text,
                "target_label": r.target_label,
                "target_node_type": r.target_node_type,
            }
            for r in ref.relations
        ]
    if ref.aggregate:
        result["aggregate"] = {
            "operation": ref.aggregate.operation,
            "target_labels": ref.aggregate.target_labels,
            "value": ref.aggregate.value,
        }
    return result
