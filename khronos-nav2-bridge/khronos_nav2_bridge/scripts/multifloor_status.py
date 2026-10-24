"""Stable JSON status contract shared by the Hospital runner and RViz viewer."""


LIVE_STATUS_SCHEMA = "hospital_multifloor_live_status_v1"
LIVE_STATUS_STATES = frozenset({
    "idle",
    "navigating_to_stop",
    "navigating_to_entry",
    "switching_map",
    "resetting_pose",
    "floor_ready",
    "succeeded",
    "failed",
})


def _floor(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer or null")
    return value


def build_live_status(task_id, state, active_floor=None, from_floor=None,
                      to_floor=None, transition_id=None, error=None):
    """Build a validated, JSON-serializable status message for live consumers."""
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-empty string")
    if state not in LIVE_STATUS_STATES:
        raise ValueError(f"unsupported live status state: {state!r}")
    if transition_id is not None and (
            not isinstance(transition_id, str) or not transition_id.strip()):
        raise ValueError("transition_id must be a non-empty string or null")
    if error is not None and not isinstance(error, str):
        raise ValueError("error must be a string or null")
    return {
        "schema": LIVE_STATUS_SCHEMA,
        "task_id": task_id,
        "state": state,
        "active_floor": _floor(active_floor, "active_floor"),
        "from_floor": _floor(from_floor, "from_floor"),
        "to_floor": _floor(to_floor, "to_floor"),
        "transition_id": transition_id,
        "error": error,
    }
