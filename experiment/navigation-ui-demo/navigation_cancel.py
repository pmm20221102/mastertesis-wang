"""Cancel only recorded GUI NavigateToPose UUIDs and confirm their terminal states."""
import argparse
import json
import time
import uuid


def normalized_goal_id(value):
    goal = uuid.UUID(value)
    if not goal.int:
        raise ValueError("A nonzero owned goal UUID is required; cancel-all is forbidden")
    return goal.hex


def make_cancel_request(goal_id, request_type):
    request = request_type()
    request.goal_info.goal_id.uuid = list(bytes.fromhex(normalized_goal_id(goal_id)))
    # A nonzero goal UUID with zero timestamp selects exactly this UUID.
    request.goal_info.stamp.sec = request.goal_info.stamp.nanosec = 0
    return request


def cancel_owned_goals(goal_ids, request_cancel, get_status, poll, timeout=10):
    goals = list(dict.fromkeys(normalized_goal_id(goal) for goal in goal_ids))
    if not goals:
        return False
    requested_states = {}
    for goal in goals:
        state = get_status(goal)
        if state not in (4, 5, 6):
            request_cancel(goal)
        requested_states[goal] = state
    deadline = time.monotonic() + timeout
    while True:
        if all(get_status(goal) in (4, 5, 6) for goal in goals):
            return True
        if time.monotonic() >= deadline:
            return False
        for goal in goals:
            state = get_status(goal)
            if state in (1, 2) and state != requested_states[goal]:
                # A send already in flight at Stop may only now be accepted.
                request_cancel(goal)
                requested_states[goal] = state
        poll()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal-id", action="append", required=True)
    args = parser.parse_args()
    goals = [normalized_goal_id(goal) for goal in args.goal_id]
    import rclpy
    from action_msgs.msg import GoalStatusArray
    from action_msgs.srv import CancelGoal
    from rclpy.qos import DurabilityPolicy, QoSProfile
    rclpy.init()
    node = rclpy.create_node("hospital_ui_cancel_navigation")
    latest = {}
    def status(message):
        for item in message.status_list:
            latest[bytes(item.goal_info.goal_id.uuid).hex()] = item.status
    qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
    sub = node.create_subscription(GoalStatusArray, "/navigate_to_pose/_action/status", status, qos)
    client = node.create_client(CancelGoal, "/navigate_to_pose/_action/cancel_goal")
    result = {"confirmed": False}
    try:
        if not client.wait_for_service(timeout_sec=3.0):
            raise RuntimeError("Navigation cancellation service is unavailable.")
        def cancel_one(goal):
            future = client.call_async(make_cancel_request(goal, CancelGoal.Request))
            rclpy.spin_until_future_complete(node, future, timeout_sec=3.0)
            return future.done() and future.result() is not None
        result["confirmed"] = cancel_owned_goals(
            goals, cancel_one, latest.get, lambda: rclpy.spin_once(node, timeout_sec=0.1))
    except Exception as error:
        result["error"] = str(error)
    finally:
        node.destroy_subscription(sub)
        node.destroy_node()
        rclpy.shutdown()
    print(json.dumps(result))
    return 0 if result["confirmed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
