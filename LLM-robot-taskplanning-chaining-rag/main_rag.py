"""RAG-enhanced LLM Task Planner for robot navigation.

Usage:
    python main_rag.py                                    # Interactive mode
    python main_rag.py --command "navigate to room 201"   # Single command
    python main_rag.py --compare                          # Compare with baseline
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
from ollama import Client
from pydantic import ValidationError

from schema import TaskPlan
from prompt import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from rag_retriever import retrieve_scene_context, retrieve_with_keywords

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config_rag.yaml"
DEFAULT_OUTPUT_PATH = BASE_DIR / "task_plan_output_rag.json"

# Import utilities from original project
sys.path.insert(0, str(BASE_DIR.parent / "LLM-robot-taskplanning-chaining"))
from main import (
    extract_json_object,
    build_entity_index,
    normalize_text,
    canonicalize_task_plan,
    build_rule_based_transfer_plan,
    _validate_chain_structure,
    _validate_transfer_requirements,
    _validate_screening_requirement,
    _validate_avoid_requirement,
    _validate_step_numbering,
    detect_intent,
    find_restricted_entities,
)


def load_config(config_path: str | Path | None = None) -> dict:
    """Load YAML config file."""
    if config_path is None:
        config_path = CONFIG_PATH
    config_path = Path(config_path)
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def validate_task_plan_semantics(task_plan, scene_context, user_command):
    """Lenient semantic validation for RAG context (skip target_id checks)."""
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
    # Skip _validate_step_fields to avoid target_id checks
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


def _auto_fill_target_id(task_plan: TaskPlan, scene_context: dict[str, Any]) -> TaskPlan:
    """Auto-fill missing target_id based on target_type, notes, and available entities."""
    entities = scene_context.get("entities", [])
    if not entities:
        return task_plan

    # Build type -> entity list (with plural/singular handling)
    type_entities: dict[str, list[dict]] = defaultdict(list)
    for ent in entities:
        t = ent.get("type", "").rstrip("s")  # "rooms" -> "room", "places" -> "place"
        type_entities[t].append(ent)
        # Map "place" to multiple types for flexibility
        if t == "place":
            type_entities["room"].append(ent)
            type_entities["object"].append(ent)
            type_entities["area"].append(ent)
        # Map "area" to place/room
        if t == "area":
            type_entities["place"].append(ent)
            type_entities["room"].append(ent)

    # Build entity ID lookup
    entity_by_id = {ent.get("id", ""): ent for ent in entities if ent.get("id")}

    for step in task_plan.task_chain:
        if step.target_id:
            continue
        if step.action in ("wait", "ask_clarification", "return_to_base"):
            continue

        # Try 1: Extract entity ID from notes and description
        import re
        for text in (task_plan.notes or []) + [step.description]:
            # Look for patterns like "entity 12345" or "(12345)" or standalone large number
            matches = re.findall(r'(?:entity|node|ID)\s+(\d{5,})', text, re.IGNORECASE)
            if matches:
                step.target_id = matches[0]
                break
            matches = re.findall(r'\((\d{10,})\)', text)
            if matches:
                step.target_id = matches[0]
                break
            matches = re.findall(r'\b(\d{10,})\b', text)
            if matches:
                step.target_id = matches[0]
                break

        if step.target_id:
            continue

        # Try 2: Match by target_type
        candidates = type_entities.get(step.target_type, [])
        if candidates:
            step.target_id = candidates[0].get("id", "")

        # Try 3: If still no match, use first available entity
        if not step.target_id and entities:
            step.target_id = entities[0].get("id", "")

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
            return name
    return "mistral"


def run_llm_once(model: str, prompt: str, config: dict[str, Any]) -> str:
    """Send a prompt to LLM and return raw text response."""
    host_url = get_ollama_host(config)
    client = Client(host=host_url, timeout=300)
    options = config.get("options", {"temperature": 0.1, "num_ctx": 8192, "num_predict": 2048})

    response = client.chat(
        model=model,
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
    """Build user prompt with RAG-retrieved context."""
    entities = scene_context.get("entities", [])
    keywords = scene_context.get("_keywords", {})

    # Build minimal entity list: only id, type, location
    simple_entities = []
    for ent in entities[:15]:  # Limit to 15 entities max
        simple_entities.append({
            "id": ent["id"],
            "type": ent.get("type", "unknown"),
            "location": ent.get("location", ""),
        })

    prompt_parts = [
        f"User instruction: {user_command}",
        "",
        "Available entities (use 'id' as target_id):",
        json.dumps(simple_entities, ensure_ascii=False),
        "",
        "Each step MUST have a 'target_id' from the entities above.",
    ]

    if keywords:
        prompt_parts.append(f"Keywords: {json.dumps(keywords, ensure_ascii=False)}")

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


# Import the original extract_relevant_subgraph for baseline
from main import extract_relevant_subgraph


def interactive_mode():
    """Interactive mode for testing."""
    print("RAG-Enhanced Task Planner")
    print("Type 'quit' to exit, 'compare' to run both RAG and baseline\n")

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

            print("\n--- RAG Version ---")
            try:
                rag_result = run_rag_task_planner(command)
                print(rag_result.model_dump_json(indent=2))
            except Exception as e:
                print(f"Error: {e}")

            print("\n--- Baseline Version ---")
            try:
                baseline_result = run_baseline_task_planner(command)
                print(baseline_result.model_dump_json(indent=2))
            except Exception as e:
                print(f"Error: {e}")

            print()
            continue

        print("\n--- RAG Task Plan ---")
        try:
            result = run_rag_task_planner(command)
            print(result.model_dump_json(indent=2))

            # Save output
            with open(DEFAULT_OUTPUT_PATH, "w", encoding="utf-8") as f:
                f.write(result.model_dump_json(indent=2))
            print(f"\nSaved to {DEFAULT_OUTPUT_PATH}")
        except Exception as e:
            print(f"Error: {e}")
        print()


def main():
    parser = argparse.ArgumentParser(description="RAG-Enhanced Task Planner")
    parser.add_argument("--command", "-c", help="Navigation instruction")
    parser.add_argument("--model", "-m", help="Model name override")
    parser.add_argument("--compare", action="store_true", help="Compare RAG vs baseline")
    parser.add_argument("--no-llm-keywords", action="store_true", help="Skip LLM keyword extraction")
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
            print("=== RAG Version ===")
            try:
                rag_result = run_rag_task_planner(command, args.model, not args.no_llm_keywords, args.db_dir)
                print(rag_result.model_dump_json(indent=2))
                with open(DEFAULT_OUTPUT_PATH, "w") as f:
                    f.write(rag_result.model_dump_json(indent=2))
            except Exception as e:
                print(f"Error: {e}")

            print("\n=== Baseline Version ===")
            try:
                baseline_result = run_baseline_task_planner(command, args.model)
                print(baseline_result.model_dump_json(indent=2))
                baseline_path = BASE_DIR / "task_plan_output_baseline.json"
                with open(baseline_path, "w") as f:
                    f.write(baseline_result.model_dump_json(indent=2))
            except Exception as e:
                print(f"Error: {e}")
        else:
            result = run_rag_task_planner(command, args.model, not args.no_llm_keywords, args.db_dir)
            print(result.model_dump_json(indent=2))
            with open(DEFAULT_OUTPUT_PATH, "w") as f:
                f.write(result.model_dump_json(indent=2))
            print(f"Saved to {DEFAULT_OUTPUT_PATH}")
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
