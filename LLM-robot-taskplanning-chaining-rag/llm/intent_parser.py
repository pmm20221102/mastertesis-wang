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
2. Do NOT invent or output node IDs (long numeric identifiers like 5692549928996306945).
3. PRESERVE room numbers, floor numbers, door numbers explicitly provided by the user (e.g., "Room 201").
4. Use natural language descriptions for entities (e.g., "the chair near the desk").
5. For manipulation tasks (pick, grasp, carry), set action to "unsupported".
6. Return ONLY valid JSON, no explanations.

ROLE RULES:
- locate/find instructions: Only set "target". Do NOT create "source" or "destination".
- navigate instructions: Set "target" (where to go) or "destination" (final destination).
- "from A to B" instructions: Set "source" (starting point) AND "destination" (ending point).
- escort/guide instructions: Set "actor" (person being escorted) AND "destination".
- Only create roles that are explicitly mentioned or clearly implied.

SCHEMA:
{
  "action": "navigate|escort|guide|avoid|locate|wait|ask_clarification|unsupported",
  "actor": EntityReference or null,
  "target": EntityReference or null,
  "source": EntityReference or null,
  "destination": EntityReference or null,
  "constraints": [TaskConstraint],
  "description": "brief description",
  "steps": [IntentStep] or null  // only for multi-step instructions
}

EntityReference:
{
  "text": "natural language description",
  "label": "canonical label (e.g., chairs, places, rooms)",
  "node_type": "object|place|room|agent",
  "room": "room number or ID if mentioned",
  "locations": [LocationConstraint],  // for Place + Room intersection
  "relations": [RelationConstraint],  // for near, inside, connected_to
  "quantifier": "one|all|nearest|any",
  "aggregate": AggregateConstraint or null
}

LocationConstraint: {"text": "...", "node_type": "place|room"}
RelationConstraint: {"relation": "inside|contains|near|connected_to", "target_text": "...", "target_label": "..."}
AggregateConstraint: {"operation": "count_in_location|max_count|min_count", "target_labels": [...], "location": LocationConstraint}

FEW-SHOT EXAMPLES:

1. "Navigate to the chair"
→ {"action": "navigate", "target": {"text": "chair", "label": "chairs", "node_type": "object"}}

2. "Find the chair in room 201"
→ {"action": "locate", "target": {"text": "chair", "label": "chairs", "node_type": "object", "room": "201"}}

3. "Find all chairs"
→ {"action": "locate", "target": {"text": "chairs", "label": "chairs", "node_type": "object", "quantifier": "all"}}

4. "Find the chair near the desk"
→ {"action": "locate", "target": {"text": "chair", "label": "chairs", "node_type": "object", "relations": [{"relation": "near", "target_text": "desk", "target_label": "desks"}]}}

5. "Navigate to the chair in Place A inside Room 201"
→ {"action": "navigate", "target": {"text": "chair", "label": "chairs", "node_type": "object", "locations": [{"text": "Place A", "node_type": "place"}, {"text": "Room 201", "node_type": "room"}]}}

6. "Find the small_office_object inside place 928841"
→ {"action": "locate", "target": {"text": "small_office_object", "label": "small_office_objects", "node_type": "object", "relations": [{"relation": "inside", "target_text": "place 928841"}]}}

7. "How many chairs are in Room 201?"
→ {"action": "locate", "target": {"text": "chairs", "label": "chairs", "node_type": "object", "quantifier": "all", "aggregate": {"operation": "count_in_location", "target_labels": ["chairs"], "location": {"text": "Room 201", "node_type": "room"}}}}

8. "Which room has the most chairs?"
→ {"action": "locate", "target": {"text": "chairs", "label": "chairs", "node_type": "object", "quantifier": "all", "aggregate": {"operation": "max_count", "target_labels": ["chairs"]}}}

9. "Escort the person to the entrance"
→ {"action": "escort", "actor": {"text": "person", "node_type": "agent"}, "destination": {"text": "entrance", "node_type": "place"}}

10. "Navigate from the office to the kitchen"
→ {"action": "navigate", "source": {"text": "office", "node_type": "room"}, "destination": {"text": "kitchen", "node_type": "room"}}

11. "First find the chair, then navigate to the office"
→ {"action": "navigate", "target": {"text": "chair", "label": "chairs", "node_type": "object"}, "destination": {"text": "office", "node_type": "room"}, "steps": [{"action": "locate", "target": {"text": "chair", "label": "chairs", "node_type": "object"}}, {"action": "navigate", "destination": {"text": "office", "node_type": "room"}}]}

12. "Pick up the chair"
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
    """Detect action from natural language text using word-boundary regex."""
    import re
    text_lower = text.lower()
    # Manipulation: use word boundaries, exclude "place" as spatial entity
    if re.search(r"\b(pick\s+up|grasp|carry|lift)\b", text_lower):
        return "unsupported"
    # Escort: "escort" or "accompany" are clear; "guide" needs actor context
    if re.search(r"\b(escort|accompany)\b", text_lower):
        return "escort"
    if re.search(r"\bguide\b", text_lower) and re.search(r"\b(person|patient|him|her|them)\b", text_lower):
        return "escort"
    if re.search(r"\b(find|locate|search)\b", text_lower):
        return "locate"
    if re.search(r"\b(navigate|go\s+to)\b", text_lower):
        return "navigate"
    if re.search(r"\b(avoid|bypass|skip)\b", text_lower):
        return "avoid"
    if re.search(r"\b(wait|pause)\b", text_lower):
        return "wait"
    if re.search(r"\b(ask|clarify)\b", text_lower):
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
    eval_mode: bool = False,
) -> TaskIntent:
    """Validate and canonicalize parsed intent.

    Removes spurious roles and fixes common LLM errors.
    In eval_mode, action override is disabled to measure pure LLM accuracy.
    """
    text_lower = user_command.lower()

    # Action deterministic override - don't let small model misclassify actions
    # Skip in eval_mode to measure pure LLM action accuracy
    if not eval_mode:
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
    eval_mode: bool = False,
) -> TaskIntent | dict:
    """Parse natural language command into structured TaskIntent using LLM.

    In eval_mode, returns a dict with raw_lmm_output and canonicalized_intent
    to separate LLM ability from rule contributions.
    """
    from ollama import Client
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
        "num_ctx": 8192,
        "num_predict": 512,
    })
    # Thinking models need enough tokens to finish thinking + produce content
    is_thinking_model = any(k in model.lower() for k in ["qwen3.5", "qwen3", "thinking"])
    if is_thinking_model and options.get("num_predict", 0) < 2048:
        options["num_predict"] = 2048

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
    thinking_text = response.message.thinking

    # Thinking model fallback: if content is empty but thinking has text,
    # extract JSON from the thinking field
    if not raw_content and thinking_text:
        logger.info(f"Model returned empty content, extracting from thinking field")
        raw_content = _extract_json_from_thinking(thinking_text)

    if not raw_content:
        raise ValueError("LLM returned empty response")

    # Parse using Pydantic validation
    raw_json = None
    try:
        raw_json = json.loads(raw_content)
    except json.JSONDecodeError:
        raw_json = {"_raw_text": raw_content}

    try:
        intent = TaskIntent.model_validate_json(raw_content)
    except Exception as e:
        logger.error(f"Failed to validate TaskIntent: {e}")
        logger.error(f"Raw response: {raw_content}")
        raise ValueError(f"Invalid TaskIntent from LLM: {e}")

    # Step 3: Validate and canonicalize
    canonicalized = validate_and_canonicalize_intent(user_command, intent, eval_mode=eval_mode)

    if eval_mode:
        return {
            "raw_lmm_output": raw_json,
            "canonicalized_intent": canonicalized,
            "thinking_text": thinking_text[:500] if thinking_text else None,
            "raw_content": raw_content,
        }

    return canonicalized


def _extract_json_from_thinking(thinking_text: str) -> str | None:
    """Extract JSON from thinking model's reasoning text.

    Thinking models (qwen3.5) may put all output in the 'thinking' field.
    This function tries to find a JSON object in the thinking text.
    """
    import re
    # Find the outermost JSON object
    # Try to find ```json ... ``` blocks first
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", thinking_text, re.DOTALL)
    if json_match:
        return json_match.group(1)

    # Try to find a JSON object (greedy match for the outermost braces)
    brace_start = thinking_text.find("{")
    if brace_start == -1:
        return None

    depth = 0
    for i in range(brace_start, len(thinking_text)):
        if thinking_text[i] == "{":
            depth += 1
        elif thinking_text[i] == "}":
            depth -= 1
            if depth == 0:
                return thinking_text[brace_start:i + 1]

    # If we didn't find matching braces, try a simpler approach
    json_candidate = thinking_text[brace_start:]
    # Find last } and slice
    last_brace = json_candidate.rfind("}")
    if last_brace > 0:
        return json_candidate[:last_brace + 1]

    return None


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
    """Convert TaskIntent to dictionary for serialization.

    Uses Pydantic model_dump to preserve ALL fields including:
    locations, relations.target_node_id, aggregate.location, steps, etc.
    """
    return intent.model_dump(mode="json")
