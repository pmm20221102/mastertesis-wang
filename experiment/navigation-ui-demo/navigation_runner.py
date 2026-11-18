"""Run the existing mission runner while recording this GUI client's motion UUIDs."""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys
import uuid


class GoalLedger:
    def __init__(self, path, execution_id):
        self.path = Path(path)
        self.data = {"execution_id": execution_id, "client_pid": os.getpid(),
                     "action_name": "/navigate_to_pose", "goal_ids": []}
        self._write()

    def _write(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def record(self, goal_uuid):
        goal_id = bytes(goal_uuid.uuid).hex()
        if not uuid.UUID(hex=goal_id).int:
            raise ValueError("A motion goal must have a nonzero UUID")
        if goal_id not in self.data["goal_ids"]:
            self.data["goal_ids"].append(goal_id)
        self._write()  # Before transport: failure here must prevent sending the goal.


def recording_action_client(base, ledger, uuid_factory):
    class RecordingClient(base):
        def __init__(self, node, action_type, action_name, *args, **kwargs):
            super().__init__(node, action_type, action_name, *args, **kwargs)
            self._gui_motion_action = action_name == "/navigate_to_pose"

        def send_goal_async(self, goal, feedback_callback=None, goal_uuid=None):
            if self._gui_motion_action:
                if goal_uuid is None:
                    goal_uuid = uuid_factory()
                ledger.record(goal_uuid)
            return super().send_goal_async(goal, feedback_callback=feedback_callback,
                                           goal_uuid=goal_uuid)
    return RecordingClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--execution-id", required=True)
    args, forwarded = parser.parse_known_args()
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    ledger = GoalLedger(args.ledger, args.execution_id)
    import rclpy.action
    from unique_identifier_msgs.msg import UUID
    original = rclpy.action.ActionClient
    rclpy.action.ActionClient = recording_action_client(
        original, ledger, lambda: UUID(uuid=list(uuid.uuid4().bytes)))
    sys.argv = [str(args.runner), *forwarded]
    # Match direct script execution so the runner can import sibling modules.
    sys.path.insert(0, str(args.runner.resolve().parent))
    try:
        runpy.run_path(str(args.runner), run_name="__main__")
    finally:
        sys.path.pop(0)
        rclpy.action.ActionClient = original


if __name__ == "__main__":
    main()
