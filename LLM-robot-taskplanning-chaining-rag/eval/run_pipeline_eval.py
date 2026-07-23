"""Pipeline evaluation runner for LLM Task Planning + Khronos 3DSG RAG.

This module evaluates the structured pipeline (Intent → Grounding → Planning)
against the legacy pipeline for comparison.

Usage:
    python -m eval.run_pipeline_eval --db /path/to/vector_db --mode structured
    python -m eval.run_pipeline_eval --db /path/to/vector_db --mode oracle_intent
    python -m eval.run_pipeline_eval --db /path/to/vector_db --mode oracle_grounding
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Add RAG src to path
RAG_SRC = Path(__file__).parent.parent.parent / "RAG" / "khronos_3dsg_rag" / "src"
sys.path.insert(0, str(RAG_SRC))

from main_rag import (
    run_structured_task_planner,
    load_config,
    CONFIG_PATH,
)
from schema import PipelineResult, ACTION_EXECUTOR_MAP, ACTION_TARGET_REFS

logger = logging.getLogger(__name__)


def load_cases(cases_path: str | Path) -> list[dict[str, Any]]:
    """Load evaluation cases from JSONL file."""
    cases = []
    with open(cases_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _build_oracle_intent(oracle: dict, instruction: str):
    """Build TaskIntent from oracle data using model_validate for full field support."""
    from planner_schemas.intent import TaskIntent, EntityReference, TaskConstraint

    def _build_ref(data):
        if not data:
            return None
        # Use model_validate to preserve all fields (relations, quantifier, aggregate)
        ref_data = {"text": data.get("text", "")}
        for field in ["label", "node_type", "name", "room"]:
            if field in data and data[field] is not None:
                ref_data[field] = data[field]
        if "relations" in data:
            ref_data["relations"] = data["relations"]
        if "locations" in data:
            ref_data["locations"] = data["locations"]
        if "quantifier" in data:
            ref_data["quantifier"] = data["quantifier"]
        if "aggregate" in data:
            ref_data["aggregate"] = data["aggregate"]
        if "position" in data:
            ref_data["position"] = data["position"]
        return EntityReference(**ref_data)

    constraints = []
    for c in oracle.get("constraints", []):
        constr_data = {
            "type": c.get("type", "avoid"),
            "description": c.get("description", ""),
        }
        if "target_text" in c:
            constr_data["target_text"] = c["target_text"]
        if "target_label" in c:
            constr_data["target_label"] = c["target_label"]
        constraints.append(TaskConstraint(**constr_data))

    return TaskIntent(
        action=oracle.get("action", "locate"),
        actor=_build_ref(oracle.get("actor")),
        target=_build_ref(oracle.get("target")),
        source=_build_ref(oracle.get("source")),
        destination=_build_ref(oracle.get("destination")),
        constraints=constraints,
        description=oracle.get("description", instruction),
        steps=oracle.get("steps"),
    )


def _compare_intent_slots(predicted: dict, expected: dict) -> dict[str, Any]:
    """Compare predicted intent slots against expected."""
    result = {
        "action_correct": predicted.get("action") == expected.get("action"),
        "role_slots": {},
    }

    for role in ["actor", "target", "source", "destination"]:
        pred_ref = predicted.get(role)
        exp_ref = expected.get(role)

        if exp_ref is None:
            # Expected no role, check if predicted also has none
            result["role_slots"][role] = {
                "expected_present": False,
                "predicted_present": pred_ref is not None,
                "correct": pred_ref is None,
            }
        elif pred_ref is None:
            # Expected role, but predicted has none
            result["role_slots"][role] = {
                "expected_present": True,
                "predicted_present": False,
                "correct": False,
            }
        else:
            # Both present, compare fields
            label_match = pred_ref.get("label") == exp_ref.get("label")
            room_match = pred_ref.get("room") == exp_ref.get("room")
            result["role_slots"][role] = {
                "expected_present": True,
                "predicted_present": True,
                "label_correct": label_match,
                "room_correct": room_match,
                "correct": label_match,  # Simplified: label match is primary
            }

    return result


def evaluate_grounding_case(
    case: dict[str, Any],
    db_dir: str,
    model: str,
    config: dict[str, Any],
    oracle_intent: bool = False,
) -> dict[str, Any]:
    """Evaluate a single grounding case with nested role ground truth."""
    from grounding.orchestrator import GroundingOrchestrator
    from scene_graph_api import SceneGraphRAG
    from llm.intent_parser import parse_intent_with_llm
    from llm.intent_parser import intent_to_dict

    instruction = case["instruction"]
    expected = case.get("expected_grounding", {})

    start_time = time.time()

    try:
        # Parse intent (or use oracle)
        if oracle_intent and "oracle_intent" in case:
            intent = _build_oracle_intent(case["oracle_intent"], instruction)
        else:
            intent = parse_intent_with_llm(instruction, model=model, config=config)

        parsed_intent = intent_to_dict(intent)

        # Ground entities
        rag = SceneGraphRAG(db_dir)
        grounding = GroundingOrchestrator(rag).ground_intent(intent)

        # Check pipeline status (ready/ambiguous/not_found)
        predicted_pipeline_status = grounding.status
        expected_pipeline_status = expected.get("pipeline_status", "ready")
        pipeline_status_correct = predicted_pipeline_status == expected_pipeline_status

        # Check nested role-specific ground truth
        role_metrics = {}
        role_status_correct_count = 0
        role_entity_id_correct_count = 0
        role_total = 0

        for role in ["actor", "target", "source", "destination"]:
            role_result = grounding.references.get(role)
            expected_role = expected.get(role)

            if expected_role is not None:
                # Expected role exists, evaluate it
                role_total += 1
                expected_status = expected_role.get("status", "resolved")
                expected_node_id = expected_role.get("node_id")

                if role_result:
                    predicted_status = role_result.status
                    predicted_node_id = role_result.selected.node_id if role_result.selected else None

                    status_correct = predicted_status == expected_status
                    node_id_correct = (predicted_node_id == expected_node_id) if expected_node_id else None

                    if status_correct:
                        role_status_correct_count += 1
                    if node_id_correct is True:
                        role_entity_id_correct_count += 1

                    role_metrics[role] = {
                        "expected_status": expected_status,
                        "predicted_status": predicted_status,
                        "status_correct": status_correct,
                        "expected_node_id": expected_node_id,
                        "predicted_node_id": predicted_node_id,
                        "node_id_correct": node_id_correct,
                    }
                else:
                    # Expected role but not predicted
                    role_metrics[role] = {
                        "expected_status": expected_status,
                        "predicted_status": None,
                        "status_correct": False,
                        "expected_node_id": expected_node_id,
                        "predicted_node_id": None,
                        "node_id_correct": False,
                    }
            elif role_result:
                # Predicted role but not expected (spurious role)
                role_metrics[role] = {
                    "expected_status": None,
                    "predicted_status": role_result.status,
                    "status_correct": False,  # Spurious role
                    "expected_node_id": None,
                    "predicted_node_id": role_result.selected.node_id if role_result.selected else None,
                    "node_id_correct": None,
                }

        # Compute role accuracies
        role_status_accuracy = role_status_correct_count / role_total if role_total > 0 else None
        role_entity_id_accuracy = role_entity_id_correct_count / role_total if role_total > 0 else None

        # Check node IDs (N/A if not specified)
        node_ids_correct = None
        if "node_ids" in expected and expected["node_ids"]:
            expected_ids = set(expected["node_ids"])
            resolved_ids = grounding.get_resolved_ids()
            node_ids_correct = bool(expected_ids & resolved_ids)

        # --- Set query metrics ---
        set_metrics = None
        expected_node_ids = expected.get("target", {}).get("node_ids") if expected.get("target") else None
        if expected_node_ids is not None:
            expected_set = set(expected_node_ids)
            target_result = grounding.references.get("target")
            if target_result and target_result.selected_many:
                predicted_set = set(c.node_id for c in target_result.selected_many)
            elif target_result and target_result.selected:
                predicted_set = {target_result.selected.node_id}
            else:
                predicted_set = set()

            tp = len(expected_set & predicted_set)
            precision = tp / len(predicted_set) if predicted_set else 0
            recall = tp / len(expected_set) if expected_set else 0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
            exact_match = expected_set == predicted_set

            set_metrics = {
                "expected_count": len(expected_set),
                "predicted_count": len(predicted_set),
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "exact_set_match": exact_match,
            }

        # --- Spatial metrics ---
        spatial_metrics = None
        if expected.get("target", {}).get("status") == "resolved" and "spatial" in case.get("tags", []):
            expected_spatial_id = expected.get("target", {}).get("node_id")
            target_result = grounding.references.get("target")
            predicted_spatial_id = target_result.selected.node_id if target_result and target_result.selected else None
            target_id_correct = (predicted_spatial_id == expected_spatial_id) if expected_spatial_id else None

            # Compute distance if positions available
            distance_error = None
            if expected_spatial_id and predicted_spatial_id:
                from scene_graph_api import SceneGraphRAG
                rag_check = SceneGraphRAG(db_dir)
                exp_node = rag_check.get_node(expected_spatial_id)
                pred_node = rag_check.get_node(predicted_spatial_id)
                ref_pos = case.get("oracle_intent", {}).get("target", {}).get("position")
                if exp_node and pred_node and ref_pos and len(ref_pos) >= 3:
                    import math
                    exp_dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(ref_pos, exp_node.position))) if exp_node.position else None
                    pred_dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(ref_pos, pred_node.position))) if pred_node.position else None
                    if exp_dist is not None and pred_dist is not None:
                        distance_error = abs(pred_dist - exp_dist)

            spatial_metrics = {
                "expected_node_id": expected_spatial_id,
                "predicted_node_id": predicted_spatial_id,
                "target_id_correct": target_id_correct,
                "distance_error": distance_error,
            }

        # --- Grounding exact success (per-category logic) ---
        tags = case.get("tags", [])
        primary_tag = tags[0] if tags else "unknown"
        grounding_case_success = False

        if primary_tag == "ambiguous":
            grounding_case_success = pipeline_status_correct
        elif primary_tag == "not_found":
            grounding_case_success = pipeline_status_correct
        elif primary_tag == "exact_id":
            grounding_case_success = pipeline_status_correct and all(
                m.get("node_id_correct") is True
                for m in role_metrics.values()
                if m.get("expected_node_id") is not None
            )
        elif primary_tag == "set_query":
            grounding_case_success = set_metrics is not None and set_metrics.get("exact_set_match", False)
        elif primary_tag == "spatial":
            grounding_case_success = spatial_metrics is not None and spatial_metrics.get("target_id_correct", False)
        elif primary_tag in ("place_constraint", "room_constraint", "obj_nav"):
            grounding_case_success = pipeline_status_correct and all(
                m.get("node_id_correct") is True
                for m in role_metrics.values()
                if m.get("expected_node_id") is not None
            )
        elif primary_tag == "complex":
            grounding_case_success = pipeline_status_correct and all(
                m.get("node_id_correct") is True
                for m in role_metrics.values()
                if m.get("expected_node_id") is not None
            )
        elif primary_tag == "relation":
            grounding_case_success = pipeline_status_correct

        # --- Case failure detection ---
        case_failure = any([
            not pipeline_status_correct,
            any(m.get("node_id_correct") is False for m in role_metrics.values()),
            set_metrics is not None and not set_metrics.get("exact_set_match", True),
            spatial_metrics is not None and not spatial_metrics.get("target_id_correct", True),
            not grounding_case_success,
        ])

        elapsed_ms = (time.time() - start_time) * 1000

        return {
            "case_id": case["case_id"],
            "instruction": instruction,
            "parsed_intent": parsed_intent,
            "tags": tags,
            "primary_tag": primary_tag,
            "predicted_pipeline_status": predicted_pipeline_status,
            "expected_pipeline_status": expected_pipeline_status,
            "pipeline_status_correct": pipeline_status_correct,
            "role_metrics": role_metrics,
            "role_status_accuracy": role_status_accuracy,
            "role_entity_id_accuracy": role_entity_id_accuracy,
            "node_ids_correct": node_ids_correct,
            "set_metrics": set_metrics,
            "spatial_metrics": spatial_metrics,
            "grounding_case_success": grounding_case_success,
            "case_failure": case_failure,
            "resolved_ids": list(grounding.get_resolved_ids()),
            "grounding": grounding.to_dict(),
            "latency_ms": elapsed_ms,
            "error": None,
        }
    except Exception as e:
        elapsed_ms = (time.time() - start_time) * 1000
        return {
            "case_id": case["case_id"],
            "instruction": instruction,
            "parsed_intent": None,
            "predicted_pipeline_status": "system_error",
            "expected_pipeline_status": expected.get("pipeline_status", "ready"),
            "pipeline_status_correct": False,
            "role_metrics": {},
            "role_status_accuracy": None,
            "role_entity_id_accuracy": None,
            "node_ids_correct": False,
            "resolved_ids": [],
            "grounding": None,
            "latency_ms": elapsed_ms,
            "error": str(e),
        }


def _run_planner_with_oracle_grounding(
    instruction: str,
    oracle_grounding: dict,
    model: str,
    config: dict[str, Any],
    db_dir: str,
) -> PipelineResult:
    """Run only the planner step with oracle grounding."""
    import time
    from main_rag import _generate_grounded_plan, _validate_grounded_plan, _resolve_target_refs

    t0 = time.time()

    # Check if oracle_grounding specifies a pipeline_status (not actual grounding)
    if "pipeline_status" in oracle_grounding:
        return PipelineResult(
            status=oracle_grounding["pipeline_status"],
            latency_ms={"oracle_ms": (time.time() - t0) * 1000},
        )

    # Build grounded task from oracle
    grounded_task = {
        "action": oracle_grounding.get("action", "navigate"),
        "description": instruction,
        "references": {},
        "constraints": [],
    }

    for role in ["actor", "target", "source", "destination"]:
        if role in oracle_grounding and oracle_grounding[role]:
            ref = oracle_grounding[role]
            if ref.get("node_id"):
                grounded_task["references"][role] = {
                    "node_id": ref["node_id"],
                    "node_type": ref.get("node_type", "unknown"),
                    "label": ref.get("label", ""),
                    "room_id": ref.get("room_id"),
                    "place_id": ref.get("place_id"),
                    "navigation_target": ref.get("navigation_target"),
                }

    for constr in oracle_grounding.get("constraints", []):
        if constr.get("node_id"):
            grounded_task["constraints"].append({
                "type": constr.get("type", "avoid"),
                "node_id": constr["node_id"],
                "node_type": constr.get("node_type", "unknown"),
                "label": constr.get("label", ""),
            })

    # Generate plan
    t_plan = time.time()
    try:
        task_plan = _generate_grounded_plan(instruction, grounded_task, model, config)
        task_plan = _resolve_target_refs(task_plan, grounded_task)
        plan_latency = (time.time() - t_plan) * 1000
    except Exception as e:
        plan_latency = (time.time() - t_plan) * 1000
        return PipelineResult(
            status="system_error",
            grounded_task=grounded_task,
            latency_ms={"planning_ms": plan_latency, "total_ms": (time.time() - t0) * 1000},
            error=str(e),
        )

    # Build mock grounding for validation
    from grounding.orchestrator import GroundingResult
    from api_schemas import ResolutionResult, ResolutionCandidate
    from planner_schemas.intent import EntityReference

    mock_grounding = GroundingResult()
    for role, ref_data in grounded_task["references"].items():
        ref = EntityReference(text=ref_data.get("label", role))
        mock_grounding.add_result(
            role,
            ResolutionResult(
                status="resolved",
                reference=ref,
                selected=ResolutionCandidate(
                    node_id=ref_data["node_id"],
                    node_type=ref_data.get("node_type", "unknown"),
                    label=ref_data.get("label", ""),
                ),
            ),
        )
    for i, constr in enumerate(grounded_task["constraints"]):
        ref = EntityReference(text=constr.get("label", f"constraint_{i}"))
        mock_grounding.add_constraint_result(
            f"constraint_{constr['type']}_{i}",
            ResolutionResult(
                status="resolved",
                reference=ref,
                selected=ResolutionCandidate(
                    node_id=constr["node_id"],
                    node_type=constr.get("node_type", "unknown"),
                    label=constr.get("label", ""),
                ),
            ),
        )

    # Validate
    validation_errors = _validate_grounded_plan(task_plan, mock_grounding)

    # If validation errors exist, mark as system_error
    if validation_errors:
        return PipelineResult(
            status="system_error",
            grounding=mock_grounding.to_dict(),
            grounded_task=grounded_task,
            task_plan=json.loads(task_plan.model_dump_json()),
            validation_errors=validation_errors,
            latency_ms={
                "planning_ms": plan_latency,
                "total_ms": (time.time() - t0) * 1000,
            },
            error=f"Validation failed: {validation_errors}",
        )

    return PipelineResult(
        status=task_plan.status,
        grounding=mock_grounding.to_dict(),
        grounded_task=grounded_task,
        task_plan=json.loads(task_plan.model_dump_json()),
        validation_errors=validation_errors,
        latency_ms={
            "planning_ms": plan_latency,
            "total_ms": (time.time() - t0) * 1000,
        },
    )


def evaluate_planning_case(
    case: dict[str, Any],
    db_dir: str,
    model: str,
    config: dict[str, Any],
    mode: str = "structured",
    oracle_grounding: bool = False,
    all_graph_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Evaluate a single planning case with comprehensive metrics."""
    instruction = case["instruction"]
    expected = case.get("expected_plan", {})

    start_time = time.time()

    try:
        planner_invoked = True
        if oracle_grounding and "oracle_grounding" in case:
            result = _run_planner_with_oracle_grounding(
                instruction, case["oracle_grounding"], model, config, db_dir
            )
            # Check if planner was actually invoked (not pre-set status)
            if "pipeline_status" in case.get("oracle_grounding", {}):
                planner_invoked = False
        elif mode == "structured":
            result = run_structured_task_planner(instruction, model_name=model, db_dir=db_dir)
        else:
            result = run_structured_task_planner(instruction, model_name=model, db_dir=db_dir)

        elapsed_ms = (time.time() - start_time) * 1000

        # Extract from PipelineResult
        predicted_status = result.status
        task_plan = result.task_plan
        task_chain = task_plan.get("task_chain", []) if task_plan else []
        grounding_data = result.grounding
        grounded_task_data = result.grounded_task
        validation_errors = result.validation_errors

        expected_status = expected.get("status", "valid")
        status_correct = predicted_status == expected_status

        # Extract predicted actions and target_refs
        predicted_actions = [step.get("action") for step in task_chain]
        predicted_target_refs = [step.get("target_ref") for step in task_chain if step.get("target_ref")]
        predicted_target_ids = [step.get("target_id") for step in task_chain if step.get("target_id")]

        # Check required actions
        required_actions = expected.get("required_actions", [])
        actions_present = all(a in predicted_actions for a in required_actions) if required_actions else True
        actions_exact = predicted_actions == required_actions if required_actions else True

        def is_subsequence(sub, seq):
            it = iter(seq)
            return all(item in it for item in sub)
        actions_ordered = is_subsequence(required_actions, predicted_actions) if required_actions else True

        # Check max steps
        max_steps = expected.get("max_steps", 10)
        steps_ok = len(task_chain) <= max_steps
        if expected_status == "valid":
            steps_ok = steps_ok and len(task_chain) > 0

        # Check target IDs against expected (N/A if not specified)
        target_ids_correct = None
        if "expected_target_ids" in expected:
            expected_target_ids = set(expected["expected_target_ids"])
            predicted_target_id_set = set(predicted_target_ids)
            target_ids_correct = expected_target_ids == predicted_target_id_set

        # Collect emitted IDs (all IDs emitted by the plan)
        emitted_ids = []
        for step in task_chain:
            tid = step.get("target_id")
            if tid:
                emitted_ids.append(tid)
            constr = step.get("constraints", {})
            if isinstance(constr, dict):
                for aid in constr.get("avoid", []):
                    emitted_ids.append(aid)

        # Collect grounding IDs
        grounding_ids = set()
        if grounded_task_data:
            for ref_data in grounded_task_data.get("references", {}).values():
                if isinstance(ref_data, dict) and ref_data.get("node_id"):
                    grounding_ids.add(ref_data["node_id"])
            for constr in grounded_task_data.get("constraints", []):
                if isinstance(constr, dict) and constr.get("node_id"):
                    grounding_ids.add(constr["node_id"])
        if grounding_data:
            for ref_data in grounding_data.get("references", {}).values():
                if isinstance(ref_data, dict) and ref_data.get("selected"):
                    grounding_ids.add(ref_data["selected"]["node_id"])
            for constr_data in grounding_data.get("constraints", {}).values():
                if isinstance(constr_data, dict) and constr_data.get("selected_node_id"):
                    grounding_ids.add(constr_data["selected_node_id"])

        # Compute ID safety metrics
        hallucinated_ids = []  # Not in graph
        ungrounded_ids = []    # In graph but not in grounding
        wrong_role_ids = []    # In grounding but wrong role

        for eid in emitted_ids:
            if all_graph_ids and eid not in all_graph_ids:
                hallucinated_ids.append(eid)
            elif eid not in grounding_ids:
                ungrounded_ids.append(eid)

        # Check role-specific ID correctness
        if "expected_steps" in expected:
            for i, step in enumerate(task_chain):
                if i < len(expected["expected_steps"]):
                    exp_step = expected["expected_steps"][i]
                    exp_target_ref = exp_step.get("target_ref")
                    exp_target_id = exp_step.get("target_id")
                    pred_target_ref = step.get("target_ref")
                    pred_target_id = step.get("target_id")

                    if exp_target_id and pred_target_id and pred_target_id != exp_target_id:
                        if pred_target_id in grounding_ids:
                            wrong_role_ids.append(pred_target_id)

        return {
            "case_id": case["case_id"],
            "instruction": instruction,
            "predicted_status": predicted_status,
            "expected_status": expected_status,
            "status_correct": status_correct,
            "planner_invoked": planner_invoked,
            "actions_correct": actions_present,
            "actions_exact_sequence": actions_exact,
            "actions_ordered": actions_ordered,
            "steps_ok": steps_ok,
            "num_steps": len(task_chain),
            "predicted_actions": predicted_actions,
            "predicted_target_refs": predicted_target_refs,
            "predicted_target_ids": predicted_target_ids,
            "target_ids_correct": target_ids_correct,
            "emitted_ids": emitted_ids,
            "grounding_ids": list(grounding_ids),
            "hallucinated_ids": hallucinated_ids,
            "ungrounded_ids": ungrounded_ids,
            "wrong_role_ids": wrong_role_ids,
            "validation_errors": validation_errors,
            "task_chain": task_chain,
            "grounding": grounding_data,
            "grounded_task": grounded_task_data,
            "latency_ms": elapsed_ms,
            "error": None,
        }
    except Exception as e:
        elapsed_ms = (time.time() - start_time) * 1000
        return {
            "case_id": case["case_id"],
            "instruction": instruction,
            "predicted_status": "system_error",
            "expected_status": expected.get("status", "valid"),
            "status_correct": False,
            "planner_invoked": True,
            "actions_correct": False,
            "actions_exact_sequence": False,
            "actions_ordered": False,
            "steps_ok": False,
            "num_steps": 0,
            "predicted_actions": [],
            "predicted_target_refs": [],
            "predicted_target_ids": [],
            "target_ids_correct": None,
            "emitted_ids": [],
            "grounding_ids": [],
            "hallucinated_ids": [],
            "ungrounded_ids": [],
            "wrong_role_ids": [],
            "validation_errors": [],
            "task_chain": [],
            "grounding": None,
            "grounded_task": None,
            "latency_ms": elapsed_ms,
            "error": str(e),
        }


def _check_benchmark_status(cases_path: str) -> tuple[bool, str]:
    """Check if benchmark manifest allows execution.

    Returns:
        (allowed, reason) - allowed=True means benchmark can run.
    """
    cases_path = Path(cases_path)
    # Look for manifest file
    manifest_candidates = [
        cases_path.with_suffix("").with_name(cases_path.stem.replace("_v2", "") + "_v2.manifest.json"),
        cases_path.parent / "grounding_benchmark_100_v2.manifest.json",
        cases_path.parent / "grounding_benchmark_100_v1.manifest.json",
    ]
    for mp in manifest_candidates:
        if mp.exists():
            with open(mp, "r") as f:
                manifest = json.load(f)
            status = manifest.get("benchmark_status", "unknown")
            if status == "blocked":
                reasons = manifest.get("blocking_reasons", [])
                return False, f"Benchmark BLOCKED: {'; '.join(reasons)}"
            return True, f"Benchmark status: {status}"
    return True, "No manifest found, proceeding"


def run_evaluation(
    db_dir: str,
    grounding_cases_path: str | None = None,
    planning_cases_path: str | None = None,
    model: str = "mistral",
    mode: str = "structured",
    repeats: int = 1,
    output_dir: str | None = None,
    allow_blocked: bool = False,
) -> dict[str, Any]:
    """Run full evaluation pipeline.

    Args:
        allow_blocked: If False, refuse to run on blocked benchmarks.
    """
    config = load_config(CONFIG_PATH)

    oracle_intent = mode == "oracle_intent"
    oracle_grounding = mode == "oracle_grounding"
    actual_mode = "structured" if mode in ("structured", "oracle_intent", "oracle_grounding") else mode

    # Check benchmark status gate
    if grounding_cases_path and not allow_blocked:
        allowed, reason = _check_benchmark_status(grounding_cases_path)
        if not allowed:
            logger.error(f"Refusing to run: {reason}")
            logger.error("Use --allow-blocked to force execution (development only)")
            return {
                "mode": mode,
                "model": model,
                "error": reason,
                "summary": {"error": reason},
            }

    # Load all graph IDs for hallucination detection
    all_graph_ids = None
    try:
        from scene_graph_api import SceneGraphRAG
        rag = SceneGraphRAG(db_dir)
        all_graph_ids = set(rag.get_all_node_ids())
        logger.info(f"Loaded {len(all_graph_ids)} graph node IDs for hallucination detection")
    except Exception as e:
        logger.warning(f"Could not load graph IDs: {e}")

    results = {
        "mode": mode,
        "model": model,
        "db_dir": db_dir,
        "repeats": repeats,
        "grounding_results": [],
        "planning_results": [],
        "summary": {},
    }

    # Load cases for category breakdown
    all_cases: list[dict] = []

    # Evaluate grounding cases
    if grounding_cases_path:
        logger.info(f"Evaluating grounding cases from {grounding_cases_path}")
        grounding_cases = load_cases(grounding_cases_path)
        all_cases.extend(grounding_cases)

        for repeat in range(repeats):
            for case in grounding_cases:
                result = evaluate_grounding_case(case, db_dir, model, config, oracle_intent)
                result["repeat"] = repeat
                results["grounding_results"].append(result)
                logger.info(f"  [{repeat}] {case['case_id']}: {result['predicted_pipeline_status']}")

    # Evaluate planning cases
    if planning_cases_path:
        logger.info(f"Evaluating planning cases from {planning_cases_path}")
        planning_cases = load_cases(planning_cases_path)
        all_cases.extend(planning_cases)

        for repeat in range(repeats):
            for case in planning_cases:
                result = evaluate_planning_case(case, db_dir, model, config, actual_mode, oracle_grounding, all_graph_ids)
                result["repeat"] = repeat
                results["planning_results"].append(result)
                logger.info(f"  [{repeat}] {case['case_id']}: {result['predicted_status']}")

    # Compute summary metrics with category breakdown
    results["summary"] = compute_summary(results, all_cases if all_cases else None)

    # Save results
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        with open(output_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(results["summary"], f, indent=2, ensure_ascii=False)

        # Save per-case results (without large fields)
        with open(output_dir / "per_case_results.jsonl", "w", encoding="utf-8") as f:
            for r in results["grounding_results"] + results["planning_results"]:
                r_copy = {k: v for k, v in r.items() if k not in ("grounding", "grounded_task", "task_chain", "parsed_intent")}
                f.write(json.dumps(r_copy, ensure_ascii=False) + "\n")

        # Save full results
        with open(output_dir / "full_results.jsonl", "w", encoding="utf-8") as f:
            for r in results["grounding_results"] + results["planning_results"]:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

        # Save failure cases (comprehensive)
        planning_failures = [
            r for r in results["planning_results"]
            if any([
                not r.get("status_correct"),
                not r.get("actions_exact_sequence"),
                not r.get("actions_ordered"),
                not r.get("steps_ok"),
                r.get("target_ids_correct") is False,
                bool(r.get("validation_errors")),
                bool(r.get("hallucinated_ids")),
                bool(r.get("ungrounded_ids")),
                bool(r.get("wrong_role_ids")),
                bool(r.get("error")),
            ])
        ]

        grounding_failures = [
            r for r in results["grounding_results"]
            if r.get("case_failure", False) or r.get("error")
        ]

        with open(output_dir / "planning_failures.jsonl", "w", encoding="utf-8") as f:
            for r in planning_failures:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

        with open(output_dir / "grounding_failures.jsonl", "w", encoding="utf-8") as f:
            for r in grounding_failures:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

        # Save hallucinated IDs
        hallucinated = [r for r in results["planning_results"] if r.get("hallucinated_ids")]
        with open(output_dir / "hallucinated_ids.jsonl", "w", encoding="utf-8") as f:
            for r in hallucinated:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

        # Save summary markdown
        write_summary_md(results["summary"], output_dir / "summary.md")

        logger.info(f"Results saved to {output_dir}")

    return results


def compute_summary(results: dict[str, Any], cases: list[dict] | None = None) -> dict[str, Any]:
    """Compute summary metrics from results.

    Args:
        results: Evaluation results dict.
        cases: Original benchmark cases (for category-level breakdown).
    """
    summary = {
        "mode": results["mode"],
        "model": results["model"],
        "repeats": results["repeats"],
    }

    # Build case_id -> tags lookup for category breakdown
    case_categories: dict[str, str] = {}
    if cases:
        for c in cases:
            tags = c.get("tags", [])
            case_categories[c["case_id"]] = tags[0] if tags else "unknown"

    # Grounding metrics
    if results["grounding_results"]:
        grounding = results["grounding_results"]
        total = len(grounding)
        pipeline_status_correct = sum(1 for r in grounding if r.get("pipeline_status_correct"))
        errors = sum(1 for r in grounding if r.get("error"))
        avg_latency = sum(r["latency_ms"] for r in grounding) / total if total > 0 else 0

        # Role-specific metrics with separate denominators
        role_status_correct = 0
        role_status_evaluated = 0
        role_entity_id_correct = 0
        role_id_evaluated = 0
        spurious_roles = 0
        total_predicted_roles = 0

        for r in grounding:
            for role, metrics in r.get("role_metrics", {}).items():
                expected_present = metrics.get("expected_status") is not None
                predicted_present = metrics.get("predicted_status") is not None

                # Count spurious roles (predicted but not expected)
                if predicted_present and not expected_present:
                    spurious_roles += 1
                total_predicted_roles += 1

                # Count expected role status accuracy
                if expected_present:
                    role_status_evaluated += 1
                    if metrics.get("status_correct"):
                        role_status_correct += 1

                # Count role entity-ID accuracy (only when expected_node_id exists)
                if expected_present and metrics.get("expected_node_id") is not None:
                    role_id_evaluated += 1
                    if metrics.get("node_id_correct") is True:
                        role_entity_id_correct += 1

        summary["grounding"] = {
            "total_cases": total,
            "pipeline_status_accuracy": pipeline_status_correct / total if total > 0 else 0,
            "role_status_accuracy": role_status_correct / role_status_evaluated if role_status_evaluated > 0 else None,
            "role_entity_id_accuracy": role_entity_id_correct / role_id_evaluated if role_id_evaluated > 0 else None,
            "role_status_evaluated_count": role_status_evaluated,
            "role_id_evaluated_count": role_id_evaluated,
            "spurious_role_count": spurious_roles,
            "spurious_role_rate": spurious_roles / total_predicted_roles if total_predicted_roles > 0 else 0,
            "error_rate": errors / total if total > 0 else 0,
            "avg_latency_ms": avg_latency,
        }

        # Category-level breakdown
        if case_categories:
            from collections import defaultdict
            cat_metrics: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct": 0, "errors": 0})
            for r in grounding:
                cat = case_categories.get(r["case_id"], "unknown")
                cat_metrics[cat]["total"] += 1
                if r.get("pipeline_status_correct"):
                    cat_metrics[cat]["correct"] += 1
                if r.get("error"):
                    cat_metrics[cat]["errors"] += 1

            category_summary = {}
            for cat, m in sorted(cat_metrics.items()):
                category_summary[cat] = {
                    "total": m["total"],
                    "correct": m["correct"],
                    "accuracy": m["correct"] / m["total"] if m["total"] > 0 else 0,
                    "error_rate": m["errors"] / m["total"] if m["total"] > 0 else 0,
                }
            summary["grounding"]["category_breakdown"] = category_summary

            # Grounding exact success rate
            exact_success_count = sum(1 for r in grounding if r.get("grounding_case_success", False))
            summary["grounding"]["grounding_exact_success_rate"] = exact_success_count / total if total > 0 else 0
            summary["grounding"]["grounding_exact_success_count"] = exact_success_count

            # Failure file completeness
            failure_count = sum(1 for r in grounding if r.get("case_failure", False))
            summary["grounding"]["case_failure_count"] = failure_count

            # Exact-ID separately reported (excluded from NL metrics)
            exact_id_cases = [r for r in grounding if case_categories.get(r["case_id"]) == "exact_id"]
            nl_cases = [r for r in grounding if case_categories.get(r["case_id"]) not in ("exact_id", "not_found")]
            if exact_id_cases:
                exact_correct = sum(1 for r in exact_id_cases if r.get("pipeline_status_correct"))
                summary["grounding"]["exact_id_accuracy"] = exact_correct / len(exact_id_cases)
                summary["grounding"]["exact_id_count"] = len(exact_id_cases)
            if nl_cases:
                nl_correct = sum(1 for r in nl_cases if r.get("grounding_case_success", False))
                summary["grounding"]["natural_language_accuracy"] = nl_correct / len(nl_cases)
                summary["grounding"]["natural_language_count"] = len(nl_cases)

            # Set query metrics
            set_cases = [r for r in grounding if r.get("set_metrics")]
            if set_cases:
                all_precisions = [r["set_metrics"]["precision"] for r in set_cases]
                all_recalls = [r["set_metrics"]["recall"] for r in set_cases]
                all_f1s = [r["set_metrics"]["f1"] for r in set_cases]
                exact_matches = sum(1 for r in set_cases if r["set_metrics"]["exact_set_match"])

                # Micro metrics (aggregate TP/FP/FN)
                total_tp = sum(r["set_metrics"]["predicted_count"] * r["set_metrics"]["precision"] for r in set_cases)
                total_predicted = sum(r["set_metrics"]["predicted_count"] for r in set_cases)
                total_expected = sum(r["set_metrics"]["expected_count"] for r in set_cases)
                micro_precision = total_tp / total_predicted if total_predicted > 0 else 0
                micro_recall = total_tp / total_expected if total_expected > 0 else 0
                micro_f1 = 2 * micro_precision * micro_recall / (micro_precision + micro_recall) if (micro_precision + micro_recall) > 0 else 0

                summary["grounding"]["set_metrics"] = {
                    "case_count": len(set_cases),
                    "macro_precision": sum(all_precisions) / len(all_precisions),
                    "macro_recall": sum(all_recalls) / len(all_recalls),
                    "macro_f1": sum(all_f1s) / len(all_f1s),
                    "micro_precision": micro_precision,
                    "micro_recall": micro_recall,
                    "micro_f1": micro_f1,
                    "exact_set_match_count": exact_matches,
                    "exact_set_match_rate": exact_matches / len(set_cases),
                }

            # Spatial metrics
            spatial_cases = [r for r in grounding if r.get("spatial_metrics")]
            if spatial_cases:
                spatial_id_correct = sum(1 for r in spatial_cases if r["spatial_metrics"].get("target_id_correct"))
                spatial_errors = [r["spatial_metrics"]["distance_error"] for r in spatial_cases if r["spatial_metrics"].get("distance_error") is not None]
                avg_distance_error = sum(spatial_errors) / len(spatial_errors) if spatial_errors else 0

                summary["grounding"]["spatial_metrics"] = {
                    "case_count": len(spatial_cases),
                    "target_id_accuracy": spatial_id_correct / len(spatial_cases),
                    "target_id_correct_count": spatial_id_correct,
                    "avg_distance_error": avg_distance_error,
                }

    # Planning metrics
    if results["planning_results"]:
        planning = results["planning_results"]
        total = len(planning)

        # Separate planner-invoked vs pre-set status cases
        planner_invoked = [r for r in planning if r.get("planner_invoked")]
        pre_set_status = [r for r in planning if not r.get("planner_invoked")]

        # Pipeline status metrics (all cases)
        status_correct = sum(1 for r in planning if r["status_correct"])

        # Planner generation metrics (only planner_invoked cases)
        if planner_invoked:
            pi_status_correct = sum(1 for r in planner_invoked if r["status_correct"])
            pi_actions_correct = sum(1 for r in planner_invoked if r["actions_correct"])
            pi_actions_exact = sum(1 for r in planner_invoked if r.get("actions_exact_sequence", False))
            pi_actions_ordered = sum(1 for r in planner_invoked if r.get("actions_ordered", False))
            pi_steps_ok = sum(1 for r in planner_invoked if r["steps_ok"])
            pi_validation_ok = sum(1 for r in planner_invoked if not r.get("validation_errors"))
            pi_latency = sum(r["latency_ms"] for r in planner_invoked) / len(planner_invoked)
        else:
            pi_status_correct = pi_actions_correct = pi_actions_exact = 0
            pi_actions_ordered = pi_steps_ok = pi_validation_ok = 0
            pi_latency = 0

        # Target IDs accuracy (only cases with expected_target_ids)
        target_ids_cases = [r for r in planning if r.get("target_ids_correct") is not None]
        target_ids_correct = sum(1 for r in target_ids_cases if r["target_ids_correct"])

        # ID safety metrics (N/A if all_graph_ids not available)
        has_graph_ids = any(r.get("emitted_ids") for r in planning)
        total_emitted = sum(len(r.get("emitted_ids", [])) for r in planning)
        total_hallucinated = sum(len(r.get("hallucinated_ids", [])) for r in planning)
        total_ungrounded = sum(len(r.get("ungrounded_ids", [])) for r in planning)
        total_wrong_role = sum(len(r.get("wrong_role_ids", [])) for r in planning)

        errors = sum(1 for r in planning if r.get("error"))
        avg_latency = sum(r["latency_ms"] for r in planning) / total if total > 0 else 0

        summary["planning"] = {
            "total_cases": total,
            "planner_invoked_count": len(planner_invoked),
            "pre_set_status_count": len(pre_set_status),
            # Pipeline status (all cases)
            "pipeline_status_accuracy": status_correct / total if total > 0 else 0,
            # Planner generation metrics (only planner_invoked)
            "planner_status_accuracy": pi_status_correct / len(planner_invoked) if planner_invoked else None,
            "planner_actions_accuracy": pi_actions_correct / len(planner_invoked) if planner_invoked else None,
            "planner_actions_exact_sequence": pi_actions_exact / len(planner_invoked) if planner_invoked else None,
            "planner_actions_ordered": pi_actions_ordered / len(planner_invoked) if planner_invoked else None,
            "planner_steps_compliance": pi_steps_ok / len(planner_invoked) if planner_invoked else None,
            "planner_validation_pass_rate": pi_validation_ok / len(planner_invoked) if planner_invoked else None,
            # Target IDs (N/A if not specified)
            "target_ids_accuracy": target_ids_correct / len(target_ids_cases) if target_ids_cases else None,
            "target_ids_evaluated_count": len(target_ids_cases),
            "target_ids_na_count": total - len(target_ids_cases),
            # ID safety (N/A if no emitted IDs)
            "hallucinated_id_rate": total_hallucinated / total_emitted if total_emitted > 0 else None,
            "ungrounded_id_rate": total_ungrounded / total_emitted if total_emitted > 0 else None,
            "wrong_role_id_rate": total_wrong_role / total_emitted if total_emitted > 0 else None,
            "total_emitted_ids": total_emitted,
            "total_hallucinated_ids": total_hallucinated,
            "total_ungrounded_ids": total_ungrounded,
            "total_wrong_role_ids": total_wrong_role,
            # Other
            "error_rate": errors / total if total > 0 else 0,
            "avg_latency_ms": avg_latency,
            "avg_planner_latency_ms": pi_latency,
        }

    return summary


def write_summary_md(summary: dict[str, Any], path: Path) -> None:
    """Write summary as markdown."""
    lines = [
        f"# RAG Evaluation Report",
        "",
        f"**Mode**: {summary.get('mode', 'unknown')}",
        f"**Model**: {summary.get('model', 'unknown')}",
        f"**Repeats**: {summary.get('repeats', 1)}",
        "",
    ]

    if "grounding" in summary:
        g = summary["grounding"]
        lines.extend([
            "## Grounding Metrics",
            "",
            f"- **Total cases**: {g['total_cases']}",
            f"- **Pipeline status accuracy**: {g['pipeline_status_accuracy']:.4f}",
        ])
        if g.get('role_status_accuracy') is not None:
            lines.append(f"- **Expected-role status accuracy**: {g['role_status_accuracy']:.4f} (n={g['role_status_evaluated_count']})")
        if g.get('role_entity_id_accuracy') is not None:
            lines.append(f"- **Expected-role entity-ID accuracy**: {g['role_entity_id_accuracy']:.4f} (n={g['role_id_evaluated_count']})")
        lines.extend([
            f"- **Grounding exact success rate**: {g.get('grounding_exact_success_rate', 0):.4f} ({g.get('grounding_exact_success_count', 0)}/{g['total_cases']})",
            f"- **Case failure count**: {g.get('case_failure_count', 0)}",
            f"- **Spurious role count**: {g.get('spurious_role_count', 0)}",
            f"- **Spurious role rate**: {g.get('spurious_role_rate', 0):.4f}",
            f"- **Error rate**: {g['error_rate']:.4f}",
            f"- **Avg latency**: {g['avg_latency_ms']:.1f}ms",
            "",
        ])

        # Set metrics
        if g.get("set_metrics"):
            sm = g["set_metrics"]
            lines.extend([
                "### Set Query Metrics",
                f"- **Cases**: {sm['case_count']}",
                f"- **Macro Precision**: {sm['macro_precision']:.4f}",
                f"- **Macro Recall**: {sm['macro_recall']:.4f}",
                f"- **Macro F1**: {sm['macro_f1']:.4f}",
                f"- **Micro Precision**: {sm['micro_precision']:.4f}",
                f"- **Micro Recall**: {sm['micro_recall']:.4f}",
                f"- **Micro F1**: {sm['micro_f1']:.4f}",
                f"- **Exact set match**: {sm['exact_set_match_count']}/{sm['case_count']} ({sm['exact_set_match_rate']:.4f})",
                "",
            ])

        # Spatial metrics
        if g.get("spatial_metrics"):
            sp = g["spatial_metrics"]
            lines.extend([
                "### Spatial Metrics",
                f"- **Cases**: {sp['case_count']}",
                f"- **Target ID accuracy**: {sp['target_id_accuracy']:.4f} ({sp['target_id_correct_count']}/{sp['case_count']})",
                f"- **Avg distance error**: {sp['avg_distance_error']:.4f}m",
                "",
            ])

    if "planning" in summary:
        p = summary["planning"]
        lines.extend([
            "## Planning Metrics",
            "",
            f"- **Total cases**: {p['total_cases']}",
            f"- **Planner invoked**: {p['planner_invoked_count']}",
            f"- **Pre-set status**: {p['pre_set_status_count']}",
            "",
            "### Pipeline Status (all cases)",
            f"- **Status accuracy**: {p['pipeline_status_accuracy']:.4f}",
            "",
            "### Planner Generation (planner-invoked only)",
        ])
        if p.get('planner_status_accuracy') is not None:
            lines.append(f"- **Status accuracy**: {p['planner_status_accuracy']:.4f}")
        if p.get('planner_actions_accuracy') is not None:
            lines.append(f"- **Actions accuracy (subset)**: {p['planner_actions_accuracy']:.4f}")
        if p.get('planner_actions_exact_sequence') is not None:
            lines.append(f"- **Actions exact sequence**: {p['planner_actions_exact_sequence']:.4f}")
        if p.get('planner_actions_ordered') is not None:
            lines.append(f"- **Actions ordered subsequence**: {p['planner_actions_ordered']:.4f}")
        if p.get('planner_steps_compliance') is not None:
            lines.append(f"- **Steps compliance**: {p['planner_steps_compliance']:.4f}")
        if p.get('planner_validation_pass_rate') is not None:
            lines.append(f"- **Validation pass rate**: {p['planner_validation_pass_rate']:.4f}")

        lines.extend(["", "### Target IDs"])
        if p.get('target_ids_accuracy') is not None:
            lines.append(f"- **Accuracy**: {p['target_ids_accuracy']:.4f} (n={p['target_ids_evaluated_count']})")
        else:
            lines.append(f"- **Accuracy**: N/A (no expected_target_ids)")
        lines.append(f"- **N/A count**: {p['target_ids_na_count']}")

        lines.extend(["", "### ID Safety"])
        if p.get('hallucinated_id_rate') is not None:
            lines.append(f"- **Hallucinated ID rate**: {p['hallucinated_id_rate']:.4f}")
        else:
            lines.append(f"- **Hallucinated ID rate**: N/A (no emitted IDs)")
        if p.get('ungrounded_id_rate') is not None:
            lines.append(f"- **Ungrounded ID rate**: {p['ungrounded_id_rate']:.4f}")
        if p.get('wrong_role_id_rate') is not None:
            lines.append(f"- **Wrong-role ID rate**: {p['wrong_role_id_rate']:.4f}")
        lines.extend([
            f"- **Total emitted IDs**: {p['total_emitted_ids']}",
            f"- **Total hallucinated IDs**: {p['total_hallucinated_ids']}",
            f"- **Total ungrounded IDs**: {p['total_ungrounded_ids']}",
            f"- **Total wrong-role IDs**: {p['total_wrong_role_ids']}",
            "",
            "### Other",
            f"- **Error rate**: {p['error_rate']:.4f}",
            f"- **Avg latency**: {p['avg_latency_ms']:.1f}ms",
            f"- **Avg planner latency**: {p['avg_planner_latency_ms']:.1f}ms",
            "",
        ])

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Pipeline Evaluation Runner")
    parser.add_argument("--db", required=True, help="Path to vector DB")
    parser.add_argument("--grounding-cases", help="Path to grounding cases JSONL")
    parser.add_argument("--planning-cases", help="Path to planning cases JSONL")
    parser.add_argument("--model", default="mistral", help="LLM model name")
    parser.add_argument("--mode", default="structured",
                       choices=["structured", "oracle_intent", "oracle_grounding"],
                       help="Pipeline mode")
    parser.add_argument("--repeats", type=int, default=1, help="Number of repeats")
    parser.add_argument("--output", help="Output directory for reports")
    parser.add_argument("--allow-blocked", action="store_true",
                       help="Allow running on blocked benchmarks (development only)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    results = run_evaluation(
        db_dir=args.db,
        grounding_cases_path=args.grounding_cases,
        planning_cases_path=args.planning_cases,
        model=args.model,
        mode=args.mode,
        repeats=args.repeats,
        output_dir=args.output,
        allow_blocked=args.allow_blocked,
    )

    # Print summary
    print("\n=== Evaluation Summary ===")
    print(json.dumps(results["summary"], indent=2))


if __name__ == "__main__":
    main()
