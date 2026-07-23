from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


AllowedStatus = Literal[
    "valid",
    "ambiguous",
    "target_not_found",
    "unsupported",
    "system_error",
]

AllowedAction = Literal[
    "locate_entity",
    "navigate_to",
    "escort_to",
    "guide_to",
    "avoid_area",
    "wait",
    "ask_clarification",
    "handover_to_staff",
    "trigger_workflow",
    "perform_screening",
    "return_to_base"
]

TargetType = Literal[
    "person",
    "room",
    "object",
    "area",
    "workflow",
    "base",
    "unknown"
]

ExecutorType = Literal[
    "nav2",
    "scene_graph",
    "llm",
    "external_workflow",
    "human_staff",
    "none"
]

# Valid target_ref values for role-specific validation
VALID_TARGET_REFS = {"actor", "target", "source", "destination"}
# Valid constraint_ref pattern
CONSTRAINT_REF_PATTERN = r"^constraint_(avoid|prefer)_(\d+)$"

# Action -> required executor mapping
ACTION_EXECUTOR_MAP = {
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

# Action -> valid target_ref types
ACTION_TARGET_REFS = {
    "locate_entity": {"target", "actor"},
    "navigate_to": {"target", "destination", "source"},
    "escort_to": {"actor", "destination"},
    "guide_to": {"actor", "destination"},
    "avoid_area": set(),  # uses constraint refs only
    "wait": set(),
    "ask_clarification": set(),
    "handover_to_staff": {"actor"},
    "trigger_workflow": set(),
    "perform_screening": {"target"},
    "return_to_base": set(),
}


class Constraints(BaseModel):
    """Per-step constraints controlling robot navigation and behavior.

    Attributes:
        avoid: Scene graph node IDs the robot must not enter during this step.
        prefer: Preferred routes or behaviors (e.g. ``"shortest_safe_route"``).
        conditions: Preconditions that must hold before this step can execute
            (e.g. ``"patient_located"``).
    """

    avoid: list[str] = Field(default_factory=list)
    prefer: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)


class PlanDraftStep(BaseModel):
    """LLM output step - no target_id allowed.

    This is what the LLM should output. The system deterministically
    compiles target_ref to actual target_id.

    Valid target_ref values:
    - Top-level roles: "actor", "target", "source", "destination"
    - Step-specific: "step_N_target", "step_N_source", "step_N_destination"
    - Constraints: "constraint_avoid_X", "constraint_prefer_X"
    - No-ref actions: "" (only for wait, ask_clarification, return_to_base)
    """

    step: int
    action: AllowedAction
    target_type: TargetType
    target_ref: str = ""  # Required for executable actions; "" only for no-ref actions
    description: str
    executor: ExecutorType
    constraints: Constraints = Field(default_factory=Constraints)

    def model_post_init(self, __context: Any) -> None:
        """Validate target_ref after initialization.

        Rules:
        - No-ref actions (wait, ask_clarification, return_to_base): target_ref must be ""
        - All other actions: target_ref is preferred but not strictly required
          (LLM often omits it; _resolve_target_refs will infer from action type)
        - Valid refs: actor/target/source/destination, step_N_*, constraint_*
        """
        import re
        import logging
        valid_refs = {"actor", "target", "source", "destination"}
        step_pattern = r"^step_(\d+)_(target|source|destination|actor)$"
        constraint_pattern = r"^constraint_(avoid|prefer)_\d+$"

        # Actions that don't need target_ref
        no_ref_actions = {"wait", "ask_clarification", "return_to_base"}

        if self.action in no_ref_actions:
            # These actions must NOT have a target_ref
            if self.target_ref and self.target_ref != "":
                self.target_ref = ""  # Force empty for no-ref actions
            return

        # Allow empty target_ref — will be inferred by _resolve_target_refs
        if not self.target_ref:
            logging.getLogger(__name__).debug(
                f"Step {self.step}: target_ref empty for '{self.action}', will infer from action type"
            )
            return

        # Validate target_ref format
        is_valid = (
            self.target_ref in valid_refs
            or bool(re.match(step_pattern, self.target_ref))
            or bool(re.match(constraint_pattern, self.target_ref))
        )
        if not is_valid:
            logging.getLogger(__name__).warning(
                f"Step {self.step}: invalid target_ref '{self.target_ref}' for '{self.action}'"
            )


class PlanDraft(BaseModel):
    """LLM output plan - no target_id allowed."""

    status: AllowedStatus
    task_chain: list[PlanDraftStep]
    clarification_request: Optional[str] = None
    target_not_found: Optional[str] = None
    notes: list[str] = Field(default_factory=list)


class TaskStep(BaseModel):
    """A single atomic step in the robot's task chain.

    Attributes:
        step: 1-based sequential step number.
        action: The high-level action the robot must perform.
        target_type: Category of the target entity.
        target_ref: Role reference from grounded task (e.g., "actor", "target",
            "destination", "constraint_avoid_0"). The system deterministically
            resolves this to the actual node_id.
        target_id: Exact scene graph node ID or workflow ID; ``None`` for
            actions that do not target a specific entity. Populated by the
            system from target_ref, not by the LLM directly.
        description: Short human-readable description of this step.
        executor: The subsystem responsible for executing this step.
        constraints: Navigation and behavioral constraints for this step.
    """

    step: int
    action: AllowedAction
    target_type: TargetType
    target_ref: Optional[str] = None
    target_id: Optional[str] = None
    description: str
    executor: ExecutorType
    constraints: Constraints = Field(default_factory=Constraints)


class TaskPlan(BaseModel):
    """Top-level task plan produced by the LLM or rule-based fallback.

    Attributes:
        status: Overall plan validity status.
        task_chain: Ordered list of steps the robot must execute.
        clarification_request: When ``status`` is ``"ambiguous"``, a message
            describing what clarification is needed.
        target_not_found: When ``status`` is ``"target_not_found"``, a message
            describing which target could not be located.
        notes: Free-text notes (e.g. restricted-area avoidance guidance).
    """

    status: AllowedStatus
    task_chain: list[TaskStep]
    clarification_request: Optional[str] = None
    target_not_found: Optional[str] = None
    notes: list[str] = Field(default_factory=list)


class PipelineResult(BaseModel):
    """Unified result from structured pipeline - success and failure.

    This is the canonical return type for run_structured_task_planner().
    """

    status: AllowedStatus
    intent: Optional[dict] = None
    grounding: Optional[dict] = None
    grounded_task: Optional[dict] = None
    task_plan: Optional[dict] = None
    validation_errors: list[str] = Field(default_factory=list)
    latency_ms: dict[str, float] = Field(default_factory=dict)
    error: Optional[str] = None
