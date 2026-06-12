"""Prompt builder for RAG-enhanced LLM task planning.

Aligns with:
- Khronos DSG 5-layer hierarchy (Building > Room > Place > Object > Mesh)
- RAG entity schema (id, type, name, description, location, position)
- TaskChain output schema (TaskPlan with steps, constraints, executor)
"""

import json
from typing import Any

from schema import AllowedAction, AllowedStatus, ExecutorType, TargetType


def _format_enum_values(enum_type: Any) -> str:
    """Return a newline-separated list of allowed values for a Literal type."""
    return "\n".join(f"- {v}" for v in enum_type.__args__)


def _build_enum_section() -> str:
    """Build the prompt section listing all allowed enum values."""
    return f"""
Allowed status values:
{_format_enum_values(AllowedStatus)}

Allowed actions:
{_format_enum_values(AllowedAction)}

Allowed target_type values:
{_format_enum_values(TargetType)}

Allowed executor values:
{_format_enum_values(ExecutorType)}
"""


SYSTEM_PROMPT = """
You are a robot task planner for semantic navigation in an indoor office environment.

## Scene Representation

The environment is represented as a 5-layer Dynamic Scene Graph (DSG):

Layer 5: Building — the entire building structure (1 node)
Layer 4: Rooms — discrete rooms and corridors (e.g. offices, hallways, kitchen)
Layer 3: Places — navigable regions within rooms, extracted via GVD (Generalized Voronoi Diagram)
Layer 2: Objects — furniture and items (chairs, desks, monitors, doors, shelves)
Layer 1: Mesh — metric-semantic background map (not counted as graph nodes)

Hierarchy: Building contains Rooms, Rooms contain Places, Places contain Objects.
Each entity has a unique numeric ID (e.g. "8070450532247945544").

## Your Task

Convert a natural-language navigation instruction + retrieved scene context into a short, executable JSON task chain.

The robot performs HIGH-LEVEL semantic actions only.
Do NOT output low-level motion commands (turn left, move forward, rotate).
Only output actions executable by ROS2/Nav2, a scene graph query module, or an external workflow module.

Output valid JSON only. No explanations, no markdown, no comments.
""" + _build_enum_section() + """
## Executor Rules

- locate_entity -> executor: "scene_graph"
  Query the scene graph to find an entity's position and status.
- navigate_to, avoid_area, return_to_base -> executor: "nav2"
  Move the robot to a target location or along a path.
- escort_to, guide_to -> executor: "nav2"
  Navigate while accompanying a person or guiding someone.
- trigger_workflow, perform_screening -> executor: "external_workflow"
  Start an external process (e.g. security screening, login workflow).
- handover_to_staff -> executor: "human_staff"
  Hand off a task to a human staff member.
- ask_clarification -> executor: "llm"
  Ask the user for clarification.
- wait -> executor: "none"
  Pause execution until a condition is met.

## Grounding Rules (Critical)

1. target_id MUST be copied exactly from the scene context entity IDs.
2. Never invent or hallucinate IDs. Use only IDs present in the provided entities.
3. If a target entity is missing from the scene context, set status to "target_not_found".
4. If multiple targets match and the instruction is ambiguous, set status to "ambiguous".
5. constraints.avoid and constraints.prefer should also use exact entity IDs when referring to scene graph nodes.
6. Do not use placeholder IDs like "room_201" or "R105" — use the actual numeric IDs from the scene context.

## Task Decomposition Rules

1. Maximum 6 steps. Prefer the fewest steps that preserve the user's intent.
2. For "go to room X": locate_entity (room) -> navigate_to (room)
3. For "find object in room": locate_entity (object) -> navigate_to (room containing object)
4. For "bring object from A to B": locate_entity (object) -> navigate_to (A) -> navigate_to (B)
5. For "avoid area X": put entity ID in constraints.avoid of the relevant navigate_to step.
6. Do not create steps for unrelated entities.
7. Do not skip navigate_to when the robot must reach a location first.

## Spatial Reasoning

When the instruction references a room by its contents (e.g. "the room with chairs"):
1. Use locate_entity to find the room containing the target objects.
2. The scene context provides which objects are in which rooms.
3. Navigate to the room, not to individual objects (unless explicitly asked).

## Output Format

{
  "status": "valid | ambiguous | target_not_found | unsupported",
  "task_chain": [
    {
      "step": 1,
      "action": "<action>",
      "target_type": "<type>",
      "target_id": "<exact_entity_id>",
      "description": "<short description>",
      "executor": "<executor>",
      "constraints": {
        "avoid": [],
        "prefer": [],
        "conditions": []
      }
    }
  ],
  "clarification_request": null,
  "target_not_found": null,
  "notes": []
}

## Example

Instruction: "Navigate to the room with chairs and avoid the kitchen."

Scene context contains:
- Room A (id: 111) — contains chairs
- Room B (id: 222) — kitchen area

Expected output:
{
  "status": "valid",
  "task_chain": [
    {
      "step": 1,
      "action": "locate_entity",
      "target_type": "room",
      "target_id": "111",
      "description": "Locate the room containing chairs.",
      "executor": "scene_graph",
      "constraints": {"avoid": [], "prefer": [], "conditions": []}
    },
    {
      "step": 2,
      "action": "navigate_to",
      "target_type": "room",
      "target_id": "111",
      "description": "Navigate to the room with chairs.",
      "executor": "nav2",
      "constraints": {"avoid": ["222"], "prefer": [], "conditions": ["room_111_located"]}
    }
  ],
  "clarification_request": null,
  "target_not_found": null,
  "notes": ["Room 222 (kitchen) avoided as requested."]
}

## Bad Behavior

- Do not list every entity in the scene context.
- Do not create steps for unrelated objects.
- Do not use invented IDs.
- Do not skip locate_entity when the target is described by content (e.g. "room with chairs").
- Do not set target_id to null for valid executable steps.
"""


def build_user_prompt(user_command: str, scene_graph: dict[str, Any]) -> str:
    """Build the user prompt with DSG hierarchy explanation.

    Args:
        user_command: Natural-language instruction.
        scene_graph: Relevant subgraph with entities, relations, grounding index.

    Returns:
        Formatted prompt string.
    """
    entities = scene_graph.get("entities", [])

    # Group entities by type for better context
    rooms = [e for e in entities if e.get("type") in ("room", "rooms")]
    objects = [e for e in entities if e.get("type") in ("object", "objects")]
    agents = [e for e in entities if e.get("type") in ("agent", "agents")]

    # Build room -> objects mapping
    room_objects: dict[str, list[str]] = {}
    for obj in objects:
        loc = obj.get("location", "")
        if loc:
            room_objects.setdefault(loc, []).append(
                f"{obj.get('name', '') or obj.get('type', 'object')} (id: {obj.get('id', '')})"
            )

    context_parts = []

    # Add room summaries
    if rooms:
        context_parts.append("Rooms in scene:")
        for room in rooms:
            rid = room.get("id", "")
            rname = room.get("name", "") or room.get("type", "room")
            obj_list = room_objects.get(rid, [])
            obj_str = f", contains: {', '.join(obj_list[:5])}" if obj_list else ""
            context_parts.append(f"  - {rname} (id: {rid}){obj_str}")

    # Add standalone objects (not in any room)
    standalone = [o for o in objects if not o.get("location")]
    if standalone:
        context_parts.append("\nStandalone objects:")
        for obj in standalone[:10]:
            oid = obj.get("id", "")
            oname = obj.get("name", "") or obj.get("type", "object")
            context_parts.append(f"  - {oname} (id: {oid})")

    # Add agents if any
    if agents:
        context_parts.append("\nAgents (people):")
        for agent in agents[:5]:
            aid = agent.get("id", "")
            aname = agent.get("name", "") or "person"
            context_parts.append(f"  - {aname} (id: {aid})")

    scene_summary = "\n".join(context_parts) if context_parts else "No scene context available."

    # Extract keywords if available
    keywords = scene_graph.get("_keywords", {})
    keyword_str = ""
    if keywords:
        keyword_str = f"\n\nExtracted keywords: {json.dumps(keywords, ensure_ascii=False)}"

    return f"""Natural-language instruction:
{user_command}

Scene context (Khronos DSG):
{scene_summary}

Instructions:
- Use only the entity IDs listed above as target_id.
- For "room with X": locate the room that contains X, then navigate to it.
- Maximum 6 steps.
- Return JSON only.
{keyword_str}"""


def build_repair_prompt(
    user_command: str,
    scene_graph: dict[str, Any],
    previous_output: str,
    validation_errors: list[str],
) -> str:
    """Build a repair prompt asking the LLM to fix errors.

    Args:
        user_command: Original instruction.
        scene_graph: Relevant subgraph.
        previous_output: Raw LLM output that failed validation.
        validation_errors: Error messages from validation.

    Returns:
        Formatted repair prompt string.
    """
    return f"""Natural-language instruction:
{user_command}

Scene context:
{json.dumps(scene_graph.get("entities", [])[:15], ensure_ascii=False, indent=2)}

Previous model output:
{previous_output}

Validation errors:
{json.dumps(validation_errors, ensure_ascii=False, indent=2)}

Task:
Fix the JSON to satisfy the schema and semantic rules.
- Use only entity IDs present in the scene context.
- Do not invent IDs.
- Return JSON only.
"""
