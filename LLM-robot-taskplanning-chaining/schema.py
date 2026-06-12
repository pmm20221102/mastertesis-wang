from typing import Literal, Optional
from pydantic import BaseModel, Field


AllowedStatus = Literal[
    "valid",
    "ambiguous",
    "target_not_found",
    "unsupported"
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


class TaskStep(BaseModel):
    """A single atomic step in the robot's task chain.

    Attributes:
        step: 1-based sequential step number.
        action: The high-level action the robot must perform.
        target_type: Category of the target entity.
        target_id: Exact scene graph node ID or workflow ID; ``None`` for
            actions that do not target a specific entity.
        description: Short human-readable description of this step.
        executor: The subsystem responsible for executing this step.
        constraints: Navigation and behavioral constraints for this step.
    """

    step: int
    action: AllowedAction
    target_type: TargetType
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
