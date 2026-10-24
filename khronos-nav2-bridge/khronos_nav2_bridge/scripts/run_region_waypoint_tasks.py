#!/usr/bin/env python3
"""Run 12 selected region tasks with Navfn preflight and FollowWaypoints."""

import argparse
import json
import math
import pathlib
import time


def validate_navigation_start(payload):
    start = payload.get("navigation_start") or {}
    try:
        result = {name: float(start[name]) for name in ("x", "y", "yaw")}
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid navigation_start") from error
    if not all(math.isfinite(value) for value in result.values()):
        raise ValueError("navigation_start must be finite")
    return result


def validate_missions(payload):
    tasks = payload.get("tasks") or []
    is_trial = payload.get("schema_version") == "region-approach-trials-v1"
    if is_trial:
        if payload.get("experiment_gate", {}).get("trials") != len(tasks):
            raise ValueError("experiment gate does not cover every trial")
    elif payload.get("semantic_gate", {}).get("passes") != len(tasks):
        raise ValueError("semantic gate does not cover every task")
    if not payload.get("geometry_gate", {}).get("eligible"):
        raise ValueError("geometry gate is not eligible")
    if not is_trial and len(tasks) != 12:
        raise ValueError(f"expected 12 tasks, got {len(tasks)}")
    if is_trial and not tasks:
        raise ValueError("expected at least one approach trial")
    total = 0
    for task in tasks:
        if not task.get("route"):
            raise ValueError(f"empty route: {task.get('case_id')}")
        for target in task["route"]:
            if not target.get("footprint_safe"):
                raise ValueError(f"unsafe target: {target.get('waypoint_id')}")
        total += len(task["route"])
    expected = payload.get("geometry_gate", {}).get("total_waypoints")
    if total != expected:
        raise ValueError(f"waypoint total mismatch: {total} != {expected}")
    return tasks


def atomic_write(path, payload):
    output = pathlib.Path(path)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(output)


def count_successful_waypoints(total_waypoints, missed_waypoints, task_success):
    """Count reached waypoints when Nav2 stops at the first failed waypoint."""
    if task_success:
        return total_waypoints
    missed_indices = [
        int(item.get("index", -1))
        for item in missed_waypoints
        if 0 <= int(item.get("index", -1)) < total_waypoints
    ]
    return min(missed_indices) if missed_indices else 0


def summarize_state(state):
    for row in state["tasks"]:
        navigation = row["navigation"]
        navigation["successful_waypoints"] = count_successful_waypoints(
            len(row["waypoint_ids"]),
            navigation.get("missed_waypoints", []),
            bool(navigation.get("task_success")),
        )
    successful_waypoints = sum(
        row["navigation"]["successful_waypoints"] for row in state["tasks"]
    )
    successful_tasks = sum(
        row["navigation"]["task_success"] for row in state["tasks"]
    )
    state.update({
        "status": "complete",
        "successful_waypoints": successful_waypoints,
        "waypoint_success_rate": successful_waypoints / state["expected_waypoints"],
        "successful_tasks": successful_tasks,
        "task_success_rate": successful_tasks / state["expected_tasks"],
        "statistics_method": "stop_on_failure_first_missed_index",
        "all_navfn_preflights_complete": all(
            len(row["preflight"]) == len(row["waypoint_ids"])
            for row in state["tasks"]
        ),
    })
    return state


def ros_main(missions_path, output_path, preflight_timeout, task_timeout):
    import rclpy
    import tf2_ros
    from action_msgs.msg import GoalStatus
    from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
    from nav2_msgs.action import ComputePathToPose, FollowWaypoints
    from rclpy.action import ActionClient
    from rclpy.duration import Duration
    from rclpy.node import Node

    payload = json.loads(pathlib.Path(missions_path).read_text())
    tasks = validate_missions(payload)
    navigation_start = validate_navigation_start(payload)

    class Runner(Node):
        def __init__(self):
            super().__init__("region_waypoint_task_runner")
            self.tf_buffer = tf2_ros.Buffer()
            self.tf_listener = tf2_ros.TransformListener(
                self.tf_buffer, self, spin_thread=True
            )
            self.path_client = ActionClient(
                self, ComputePathToPose, "/compute_path_to_pose"
            )
            self.follow_client = ActionClient(
                self, FollowWaypoints, "/follow_waypoints"
            )
            self.reset_publisher = self.create_publisher(
                PoseWithCovarianceStamped, "/initialpose", 10
            )

        def reset_to_start(self, start):
            deadline = time.monotonic() + 15.0
            while self.reset_publisher.get_subscription_count() < 1:
                if time.monotonic() >= deadline:
                    raise RuntimeError("initialpose subscriber unavailable")
                rclpy.spin_once(self, timeout_sec=0.1)
            message = PoseWithCovarianceStamped()
            message.header.frame_id = "map"
            message.pose.pose.position.x = start["x"]
            message.pose.pose.position.y = start["y"]
            message.pose.pose.orientation.z = math.sin(start["yaw"] / 2.0)
            message.pose.pose.orientation.w = math.cos(start["yaw"] / 2.0)
            while time.monotonic() < deadline:
                message.header.stamp = self.get_clock().now().to_msg()
                self.reset_publisher.publish(message)
                rclpy.spin_once(self, timeout_sec=0.1)
                pose = self.current_pose()
                dx = pose.pose.position.x - start["x"]
                dy = pose.pose.position.y - start["y"]
                q = pose.pose.orientation
                yaw = math.atan2(
                    2.0 * (q.w * q.z + q.x * q.y),
                    1.0 - 2.0 * (q.y * q.y + q.z * q.z),
                )
                yaw_error = math.atan2(
                    math.sin(yaw - start["yaw"]),
                    math.cos(yaw - start["yaw"]),
                )
                if math.hypot(dx, dy) <= 0.05 and abs(yaw_error) <= 0.05:
                    return {
                        "x": pose.pose.position.x,
                        "y": pose.pose.position.y,
                        "yaw": yaw,
                    }
            raise RuntimeError("timed out resetting navigation_start")

        def current_pose(self):
            deadline = time.monotonic() + 30.0
            while not self.tf_buffer.can_transform(
                "map", "base_link", rclpy.time.Time(), timeout=Duration(seconds=0.1)
            ):
                if time.monotonic() >= deadline:
                    raise RuntimeError("timed out waiting for map->base_link")
                rclpy.spin_once(self, timeout_sec=0.1)
            transform = self.tf_buffer.lookup_transform(
                "map", "base_link", rclpy.time.Time(), timeout=Duration(seconds=5.0)
            )
            pose = PoseStamped()
            pose.header.frame_id = "map"
            pose.header.stamp = transform.header.stamp
            pose.pose.position.x = transform.transform.translation.x
            pose.pose.position.y = transform.transform.translation.y
            pose.pose.orientation = transform.transform.rotation
            return pose

        def make_pose(self, target):
            data = target["nav2_pose"]
            pose = PoseStamped()
            pose.header.frame_id = "map"
            pose.header.stamp = self.get_clock().now().to_msg()
            pose.pose.position.x = float(data["x"])
            pose.pose.position.y = float(data["y"])
            pose.pose.orientation.z = math.sin(float(data.get("yaw", 0.0)) / 2.0)
            pose.pose.orientation.w = math.cos(float(data.get("yaw", 0.0)) / 2.0)
            return pose

        def wait_future(self, future, timeout, label):
            rclpy.spin_until_future_complete(self, future, timeout_sec=timeout)
            if not future.done():
                raise RuntimeError(f"timeout waiting for {label} after {timeout}s")
            return future.result()

        def preflight(self, task, start):
            rows = []
            poses = [self.make_pose(target) for target in task["route"]]
            for index, (target, goal_pose) in enumerate(zip(task["route"], poses)):
                goal = ComputePathToPose.Goal()
                goal.start = start
                goal.use_start = True
                goal.goal = goal_pose
                goal.planner_id = "GridBased"
                handle = self.wait_future(
                    self.path_client.send_goal_async(goal),
                    30.0,
                    f"Navfn goal acceptance {task['case_id']} leg {index}",
                )
                if not handle or not handle.accepted:
                    raise RuntimeError(f"Navfn rejected {task['case_id']} leg {index}")
                wrapped = self.wait_future(
                    handle.get_result_async(),
                    preflight_timeout,
                    f"Navfn result {task['case_id']} leg {index}",
                )
                path = wrapped.result.path
                if not path.poses:
                    raise RuntimeError(f"Navfn empty path {task['case_id']} leg {index}")
                rows.append({
                    "leg": index,
                    "waypoint_id": target["waypoint_id"],
                    "planner_id": "GridBased",
                    "allow_unknown": False,
                    "path_poses": len(path.poses),
                    "start": {
                        "x": start.pose.position.x,
                        "y": start.pose.position.y,
                    },
                    "goal": {
                        "x": goal_pose.pose.position.x,
                        "y": goal_pose.pose.position.y,
                    },
                })
                start = goal_pose
            return rows, poses

        def follow(self, task, poses):
            feedback = []

            def feedback_callback(message):
                current = int(message.feedback.current_waypoint)
                feedback.append(current)
                self.get_logger().info(
                    f"TASK_FEEDBACK case={task['case_id']} waypoint={current}"
                )

            goal = FollowWaypoints.Goal()
            goal.poses = poses
            handle = self.wait_future(
                self.follow_client.send_goal_async(
                    goal, feedback_callback=feedback_callback
                ),
                30.0,
                f"FollowWaypoints acceptance {task['case_id']}",
            )
            if not handle or not handle.accepted:
                raise RuntimeError(f"FollowWaypoints rejected {task['case_id']}")
            wrapped = self.wait_future(
                handle.get_result_async(),
                task_timeout,
                f"FollowWaypoints result {task['case_id']}",
            )
            missed = []
            for item in wrapped.result.missed_waypoints:
                missed.append({
                    "index": int(getattr(item, "index", -1)),
                    "error_code": int(getattr(item, "error_code", 0)),
                    "error_msg": str(getattr(item, "error_msg", "")),
                })
            succeeded = (
                wrapped.status == GoalStatus.STATUS_SUCCEEDED and not missed
            )
            return {
                "action_status": int(wrapped.status),
                "feedback_waypoints": feedback,
                "missed_waypoints": missed,
                "successful_waypoints": count_successful_waypoints(
                    len(poses), missed, succeeded
                ),
                "task_success": succeeded,
            }

    state = {
        "schema_version": "region-nav2-results-v1",
        "missions": str(missions_path),
        "semantic_gate": payload.get("semantic_gate"),
        "experiment_gate": payload.get("experiment_gate"),
        "geometry_gate": payload["geometry_gate"],
        "expected_tasks": len(tasks),
        "expected_waypoints": sum(len(task["route"]) for task in tasks),
        "tasks": [],
        "status": "running",
    }
    atomic_write(output_path, state)
    rclpy.init()
    node = Runner()
    try:
        if not node.path_client.wait_for_server(timeout_sec=30.0):
            raise RuntimeError("compute_path_to_pose action unavailable")
        if not node.follow_client.wait_for_server(timeout_sec=30.0):
            raise RuntimeError("follow_waypoints action unavailable")
        for task in tasks:
            row = {
                "case_id": task["case_id"],
                "selected_model": task["selected_model"],
                "waypoint_ids": task["waypoint_ids"],
                "preflight": [],
                "navigation": None,
                "error": None,
                "reset_pose": None,
            }
            started = time.monotonic()
            try:
                row["reset_pose"] = node.reset_to_start(navigation_start)
                preflight, poses = node.preflight(task, node.current_pose())
                row["preflight"] = preflight
                row["navigation"] = node.follow(task, poses)
            except Exception as error:
                row["error"] = str(error)
                row["navigation"] = {
                    "successful_waypoints": 0,
                    "task_success": False,
                    "missed_waypoints": [],
                    "feedback_waypoints": [],
                }
            row["duration_s"] = round(time.monotonic() - started, 3)
            state["tasks"].append(row)
            atomic_write(output_path, state)
            node.get_logger().info(
                "TASK_RESULT " + json.dumps(row, ensure_ascii=False)
            )
        summarize_state(state)
        atomic_write(output_path, state)
        return 0 if state["successful_tasks"] == state["expected_tasks"] else 2
    finally:
        node.destroy_node()
        rclpy.shutdown()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--missions", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--preflight-timeout", type=float, default=120.0)
    parser.add_argument("--task-timeout", type=float, default=900.0)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        tasks = validate_missions(json.loads(pathlib.Path(args.missions).read_text()))
        print(json.dumps({"tasks": len(tasks), "status": "valid"}))
        return 0
    return ros_main(
        args.missions, args.output, args.preflight_timeout, args.task_timeout
    )


if __name__ == "__main__":
    raise SystemExit(main())
