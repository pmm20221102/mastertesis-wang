#!/usr/bin/env python3
"""Unified same-floor and abstract cross-floor Hospital Nav2 task runner."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import yaml

from floor_transition_core import compile_mission, validate_transition_bundle
from candidate_route_planner import CANDIDATE_MISSION_SCHEMA, resolve_candidate_mission
from multifloor_status import build_live_status


class TaskExecutionFailure(RuntimeError):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def _succeeded(result):
    if result is True or result == "succeeded":
        return True
    return isinstance(result, dict) and result.get("status") == "succeeded"


def plan_candidate_mission(candidate_mission, bundle, adapter):
    """Preflight ordered candidate routes without issuing motion goals."""
    if not isinstance(candidate_mission, dict):
        raise ValueError("candidate_mission must be an object")
    initial_state = {"floor_id": candidate_mission.get("initial_floor"),
                     "pose": candidate_mission.get("initial_pose")}
    try:
        restored = adapter.restore_start(initial_state)
    except Exception as error:
        restored = {"status": "failed", "reason": f"{type(error).__name__}: {error}"}
    if not _succeeded(restored):
        return {"status": "start_preflight_failed", "execution_performed": False,
                "reason": f"fixed start failed map/pose/cost preflight: {restored}"}

    try:
        resolution = resolve_candidate_mission(candidate_mission, bundle, adapter)
    except Exception as error:
        return {"status": "route_preflight_failed", "execution_performed": False,
                "reason": f"{type(error).__name__}: {error}"}
    route_selection = resolution.get("route_selection")
    if resolution.get("status") != "selected":
        return {"status": resolution.get("status", "no_feasible_candidate_route"),
                "execution_performed": False,
                "reason": (route_selection or {}).get("reason", "no feasible route"),
                "route_selection": route_selection}
    return {
        "status": "planned",
        "execution_performed": False,
        "task_id": candidate_mission["task_id"],
        "map_bundle_id": candidate_mission["map_bundle_id"],
        "initial_state": initial_state,
        "resolved_mission": resolution["resolved_mission"],
        "route_selection": route_selection,
    }


def normalize_nav2_pose(value, frame_id="map"):
    """Normalize a portal or planner goal into planar x/y/yaw coordinates."""
    if not isinstance(value, dict):
        raise ValueError("Nav2 pose must be an object")
    if value.get("frame_id", frame_id) != frame_id:
        raise ValueError(f"Nav2 pose frame_id must be {frame_id}")
    coordinates = {}
    for key in ("x", "y"):
        raw = value.get(key)
        if isinstance(raw, bool):
            raise ValueError(f"Nav2 pose {key} must be finite")
        try:
            coordinates[key] = float(raw)
        except (OverflowError, TypeError, ValueError) as error:
            raise ValueError(f"Nav2 pose {key} must be finite") from error
        if not math.isfinite(coordinates[key]):
            raise ValueError(f"Nav2 pose {key} must be finite")
    if "yaw" in value:
        if isinstance(value["yaw"], bool):
            raise ValueError("Nav2 pose yaw must be finite")
        try:
            yaw = float(value["yaw"])
        except (OverflowError, TypeError, ValueError) as error:
            raise ValueError("Nav2 pose yaw must be finite") from error
        if not math.isfinite(yaw):
            raise ValueError("Nav2 pose yaw must be finite")
    else:
        orientation = value.get("orientation")
        if isinstance(orientation, dict):
            component = lambda key: orientation[key]
        else:
            component = lambda key: getattr(orientation, key)
        if orientation is None:
            raise ValueError("Nav2 pose requires yaw or quaternion orientation")
        try:
            qx, qy, qz, qw = (float(component(key)) for key in ("x", "y", "z", "w"))
        except (KeyError, OverflowError, TypeError, ValueError) as error:
            raise ValueError("Nav2 pose quaternion must be finite") from error
        if not all(math.isfinite(item) for item in (qx, qy, qz, qw)):
            raise ValueError("Nav2 pose quaternion must be finite")
        norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
        if norm <= 1e-9:
            raise ValueError("Nav2 pose quaternion must be non-zero")
        qx, qy, qz, qw = (item / norm for item in (qx, qy, qz, qw))
        yaw = math.atan2(2.0 * (qw * qz + qx * qy),
                         1.0 - 2.0 * (qy * qy + qz * qz))
    coordinates["yaw"] = math.atan2(math.sin(yaw), math.cos(yaw))
    coordinates["frame_id"] = frame_id
    return coordinates


def costmap_yaw(pose, frame_id="map"):
    """Return the normalized yaw for GetCost from either dict or ROS quaternion."""
    return normalize_nav2_pose(pose, frame_id)["yaw"]


def normalize_compute_path_result(succeeded, path):
    """Normalize a Nav2 ComputePathToPose result for the existing planner API."""
    if not succeeded or not isinstance(path, list) or not path:
        return {"status": "failed", "path": []}
    return {"status": "succeeded", "path": path}


def costmap_uses_footprint(robot_model):
    """Point mode queries the cost at the center cell, not a polygon footprint."""
    if robot_model not in ("footprint", "point"):
        raise ValueError("robot_model must be 'footprint' or 'point'")
    return robot_model == "footprint"


def costmap_cost_is_safe(cost):
    """Return whether a Nav2 cost byte is below the lethal/inscribed range."""
    if isinstance(cost, bool) or not isinstance(cost, int) or not 0 <= cost <= 255:
        raise ValueError("cost must be an integer in the range 0..255")
    return cost <= 252


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def grid_geometry_matches(grid, expected, frame_id="map", tolerance=1e-5):
    """Compare a LoadMap/OccupancyGrid result with frozen map geometry."""
    header = _field(grid, "header")
    info = _field(grid, "info")
    origin = _field(info, "origin")
    origin_position = _field(origin, "position")
    actual = {
        "frame_id": _field(header, "frame_id"),
        "width": _field(info, "width"),
        "height": _field(info, "height"),
        "resolution": _field(info, "resolution"),
        "origin": [_field(origin_position, "x"), _field(origin_position, "y")],
    }
    if actual["frame_id"] != frame_id:
        return False
    if actual["width"] != expected.get("width") or actual["height"] != expected.get("height"):
        return False
    expected_origin = expected.get("origin")
    if not isinstance(expected_origin, (list, tuple)) or len(expected_origin) < 2:
        return False
    try:
        values = [float(actual["resolution"]), float(expected["resolution"]),
                  float(actual["origin"][0]), float(actual["origin"][1]),
                  float(expected_origin[0]), float(expected_origin[1])]
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(value) for value in values):
        return False
    return (math.isclose(values[0], values[1], rel_tol=0.0, abs_tol=tolerance)
            and math.isclose(values[2], values[4], rel_tol=0.0, abs_tol=tolerance)
            and math.isclose(values[3], values[5], rel_tol=0.0, abs_tol=tolerance))


def costmap_metadata_matches(costmap, expected, frame_id="map", tolerance=1e-5):
    """Check that Nav2's live costmap service has loaded the requested floor."""
    header = _field(costmap, "header")
    metadata = _field(costmap, "metadata")
    origin = _field(metadata, "origin")
    position = _field(origin, "position")
    actual = {
        "frame_id": _field(header, "frame_id"),
        "width": _field(metadata, "size_x"),
        "height": _field(metadata, "size_y"),
        "resolution": _field(metadata, "resolution"),
        "origin": [_field(position, "x"), _field(position, "y")],
    }
    if actual["frame_id"] != frame_id:
        return False
    if actual["width"] != expected.get("width") or actual["height"] != expected.get("height"):
        return False
    expected_origin = expected.get("origin")
    if not isinstance(expected_origin, (list, tuple)) or len(expected_origin) < 2:
        return False
    try:
        values = [float(actual["resolution"]), float(expected["resolution"]),
                  float(actual["origin"][0]), float(actual["origin"][1]),
                  float(expected_origin[0]), float(expected_origin[1])]
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(value) for value in values):
        return False
    return (math.isclose(values[0], values[1], rel_tol=0.0, abs_tol=tolerance)
            and math.isclose(values[2], values[4], rel_tol=0.0, abs_tol=tolerance)
            and math.isclose(values[3], values[5], rel_tol=0.0, abs_tol=tolerance))


def load_floor_navigation_map(floor_config):
    """Load the already hash-validated PGM as the existing planner's grid tuple."""
    from plan_candidate_task_chain import load_floor_map
    required = ("map_yaml_path", "map_pgm_path", "map_pgm_sha256")
    if any(not isinstance(floor_config.get(key), str) for key in required):
        raise ValueError("floor map config is missing verified map paths/hashes")
    return load_floor_map({key: floor_config[key] for key in required})


def build_single_step_pipeline(stop):
    """Adapt one normalized stop to the existing PipelineResult planner API."""
    if not isinstance(stop, dict):
        raise ValueError("stop must be an object")
    floor_id = stop.get("floor_id")
    if isinstance(floor_id, bool) or not isinstance(floor_id, int) or floor_id < 0:
        raise ValueError("stop floor_id must be a non-negative integer")
    assignment_source = stop.get("floor_assignment_source")
    if not isinstance(assignment_source, str) or not assignment_source.strip():
        raise ValueError("stop floor_assignment_source is required")
    candidates = stop.get("candidate_targets")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("stop candidate_targets must be a non-empty list")
    candidate_ids = []
    target_entries = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate {index} must be an object")
        node_id = candidate.get("target_id")
        if not isinstance(node_id, str) or not node_id.strip() or node_id in candidate_ids:
            raise ValueError(f"candidate {index} has a missing or duplicate target_id")
        if candidate.get("floor_id") != floor_id:
            raise ValueError(f"candidate {node_id} floor_id does not match stop")
        if candidate.get("floor_assignment_source") != assignment_source:
            raise ValueError(f"candidate {node_id} floor provenance does not match stop")
        navigation_target = candidate.get("navigation_target")
        if not isinstance(navigation_target, dict):
            raise ValueError(f"candidate {node_id} navigation_target must be an object")
        if (navigation_target.get("floor_id") != floor_id
                or navigation_target.get("floor_assignment_source") != assignment_source):
            raise ValueError(f"candidate {node_id} navigation target floor metadata mismatch")
        candidate_ids.append(node_id)
        target_entries.append({"node_id": node_id,
                               "navigation_target": navigation_target})

    step_index = stop.get("stop_index")
    if isinstance(step_index, bool) or not isinstance(step_index, int) or step_index < 0:
        raise ValueError("stop_index must be a non-negative integer")
    return {
        "status": "valid",
        "task_plan": {
            "status": "valid",
            "task_chain": [{
                "step": step_index + 1,
                "action": "navigate_to",
                "executor": "nav2",
                "target_ref": "target",
                "candidate_node_ids": candidate_ids,
            }],
        },
        "grounded_task": {
            "references": {
                "target": {"candidate_navigation_targets": target_entries},
            },
            "steps": [],
        },
    }


def execute_compiled_mission(compiled, adapter, event_callback=None,
                             status_callback=None):
    """Execute a compiled mission through an injected Nav2/map adapter."""
    if not isinstance(compiled, dict):
        raise ValueError("compiled mission must be an object")
    task_id = compiled.get("task_id")
    initial = compiled.get("initial_state")
    actions = compiled.get("actions")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("compiled mission task_id must be non-empty")
    if not isinstance(initial, dict) or not isinstance(actions, list):
        raise ValueError("compiled mission requires initial_state and actions")
    def report_status(state, active_floor=None, from_floor=None, to_floor=None,
                      transition_id=None, error=None):
        if status_callback is not None:
            status_callback(build_live_status(
                task_id, state, active_floor, from_floor, to_floor,
                transition_id, error))

    try:
        initial_floor = int(initial["floor_id"])
        initial_pose = initial["pose"]
        initial_map = initial["map_yaml_path"]
        report_status("switching_map", to_floor=initial_floor)
        if not adapter.load_map(initial_floor, initial_map):
            raise TaskExecutionFailure("initialize_map", "initial map load failed")
        report_status("resetting_pose", active_floor=initial_floor,
                      from_floor=initial_floor, to_floor=initial_floor)
        if not adapter.reset_pose(initial_floor, initial_pose):
            raise TaskExecutionFailure("initialize_pose", "initial pose reset failed")
        report_status("floor_ready", active_floor=initial_floor,
                      from_floor=initial_floor, to_floor=initial_floor)
    except TaskExecutionFailure as error:
        report_status("failed", error=str(error))
        return {"task_id": task_id, "status": "failed", "failure_stage": error.stage,
                "error": str(error), "events": [], "not_run": True}

    active_floor = initial_floor
    pending_transition = None
    pending_target_floor = None
    events = []
    reached_stops = []

    def emit(event):
        event = dict(event)
        events.append(event)
        if event_callback is not None:
            event_callback(event)

    for action_index, action in enumerate(actions):
        if not isinstance(action, dict):
            return {"task_id": task_id, "status": "failed", "failure_stage": "invalid_action",
                    "error": f"action {action_index} must be an object", "events": events,
                    "reached_stops": reached_stops, "not_run": True}
        kind = action.get("kind")
        transition_id = action.get("transition_id")
        try:
            if kind == "navigate_to_entry":
                if pending_transition is not None:
                    raise TaskExecutionFailure(kind, "nested transition entry")
                if action.get("floor_id") != active_floor or not transition_id:
                    raise TaskExecutionFailure(kind, "entry floor or transition ID mismatch")
                target_floor = None
                if (action_index + 1 < len(actions)
                        and actions[action_index + 1].get("kind") == "load_map"):
                    target_floor = int(actions[action_index + 1]["floor_id"])
                report_status("navigating_to_entry", active_floor=active_floor,
                              from_floor=active_floor, to_floor=target_floor,
                              transition_id=transition_id)
                result = adapter.navigate_to_pose(action["pose"])
                emit({"kind": kind, "transition_id": transition_id,
                      "floor_id": active_floor, "result": result})
                if not _succeeded(result):
                    raise TaskExecutionFailure(kind, "Nav2 did not reach the configured portal")
                pending_transition = transition_id
            elif kind == "load_map":
                if transition_id != pending_transition:
                    raise TaskExecutionFailure(kind, "map load has no matching reached portal")
                target_floor = int(action["floor_id"])
                if target_floor == active_floor:
                    raise TaskExecutionFailure(kind, "map load does not change floor")
                report_status("switching_map", from_floor=active_floor,
                              to_floor=target_floor, transition_id=transition_id)
                result = adapter.load_map(target_floor, action["map_yaml_path"])
                emit({"kind": kind, "transition_id": transition_id,
                      "floor_id": target_floor, "result": result})
                if not _succeeded(result):
                    raise TaskExecutionFailure(kind, "target floor map failed to load")
                pending_target_floor = target_floor
                report_status("resetting_pose", active_floor=target_floor,
                              from_floor=active_floor, to_floor=target_floor,
                              transition_id=transition_id)
            elif kind == "set_pose":
                if transition_id != pending_transition or pending_target_floor is None:
                    raise TaskExecutionFailure(kind, "pose reset has no matching map load")
                target_floor = int(action["floor_id"])
                if target_floor != pending_target_floor:
                    raise TaskExecutionFailure(kind, "pose reset floor does not match loaded map")
                result = adapter.reset_pose(target_floor, action["pose"])
                emit({"kind": kind, "transition_id": transition_id,
                      "floor_id": target_floor, "result": result})
                if not _succeeded(result):
                    report_status("failed", from_floor=active_floor,
                                  to_floor=target_floor, transition_id=transition_id,
                                  error="exit pose failed TF/costmap verification")
                    raise TaskExecutionFailure(kind, "exit pose failed TF/costmap verification")
                active_floor = target_floor
                pending_transition = None
                pending_target_floor = None
                report_status("floor_ready", active_floor=active_floor,
                              from_floor=active_floor, to_floor=active_floor,
                              transition_id=transition_id)
            elif kind == "navigate_to_stop":
                if pending_transition is not None:
                    raise TaskExecutionFailure(kind, "task target reached before transition completed")
                if int(action.get("floor_id", -1)) != active_floor:
                    raise TaskExecutionFailure(kind, "target floor does not match active map")
                report_status("navigating_to_stop", active_floor=active_floor,
                              from_floor=active_floor, to_floor=active_floor)
                plan = adapter.plan_stop(active_floor, action["candidate_targets"])
                emit({"kind": "plan_stop", "stop_index": action["stop_index"],
                      "floor_id": active_floor, "result": plan})
                if not isinstance(plan, dict) or plan.get("status") != "path_found":
                    raise TaskExecutionFailure("plan_stop", "no feasible same-floor candidate path")
                result = adapter.navigate_to_pose(plan["goal"])
                emit({"kind": kind, "stop_index": action["stop_index"],
                      "floor_id": active_floor,
                      "selected_node_id": plan.get("selected_node_id"),
                      "result": result})
                if not _succeeded(result):
                    raise TaskExecutionFailure(kind, "Nav2 did not reach the selected stop")
                reached_stops.append(action["stop_index"])
                report_status("floor_ready", active_floor=active_floor,
                              from_floor=active_floor, to_floor=active_floor)
            else:
                raise TaskExecutionFailure("invalid_action", f"unknown action kind: {kind}")
        except TaskExecutionFailure as error:
            report_status("failed",
                          active_floor=None if pending_target_floor is not None else active_floor,
                          from_floor=active_floor, to_floor=pending_target_floor,
                          error=str(error),
                          transition_id=transition_id)
            return {"task_id": task_id, "status": "failed", "failure_stage": error.stage,
                    "error": str(error), "active_floor": active_floor, "events": events,
                    "reached_stops": reached_stops, "not_run": True}
        except (KeyError, TypeError, ValueError, RuntimeError) as error:
            report_status("failed",
                          active_floor=None if pending_target_floor is not None else active_floor,
                          from_floor=active_floor, to_floor=pending_target_floor,
                          error=str(error),
                          transition_id=transition_id)
            return {"task_id": task_id, "status": "failed", "failure_stage": kind or "invalid_action",
                    "error": str(error), "active_floor": active_floor, "events": events,
                    "reached_stops": reached_stops, "not_run": True}

    if pending_transition is not None:
        report_status("failed", active_floor=active_floor,
                      from_floor=active_floor,
                      error="mission ended before the transition exit pose was verified",
                      transition_id=pending_transition)
        return {"task_id": task_id, "status": "failed", "failure_stage": "incomplete_transition",
                "error": "mission ended before the transition exit pose was verified",
                "active_floor": active_floor, "events": events,
                "reached_stops": reached_stops, "not_run": True}
    report_status("succeeded", active_floor=active_floor, from_floor=active_floor,
                  to_floor=active_floor)
    return {"task_id": task_id, "status": "succeeded", "active_floor": active_floor,
            "events": events, "reached_stops": reached_stops, "not_run": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--plan-only", action="store_true",
                        help="compute a Nav2 route without sending NavigateToPose goals")
    parser.add_argument("--action-timeout", type=float, default=600.0)
    parser.add_argument("--robot-model", choices=("footprint", "point"),
                        default="footprint",
                        help="must match the Hospital Nav2 launch parameter profile")
    args = parser.parse_args(argv)
    if not math.isfinite(args.action_timeout) or args.action_timeout <= 0:
        parser.error("--action-timeout must be positive and finite")
    mission = json.loads(args.mission.read_text(encoding="utf-8"))
    bundle = yaml.safe_load(args.bundle.read_text(encoding="utf-8"))
    normalized_bundle = validate_transition_bundle(bundle, verify_files=True)
    if mission.get("schema_version") == CANDIDATE_MISSION_SCHEMA:
        if args.validate_only:
            parser.error("cross-floor candidate missions require Nav2 route preflight; "
                         "--validate-only is not applicable")
        return ros_main(mission, normalized_bundle, args.output,
                        args.action_timeout, args.robot_model,
                        plan_only=args.plan_only)
    if args.plan_only:
        parser.error("--plan-only requires a candidate mission with explicit RAG targets")
    compiled = compile_mission(mission, normalized_bundle, verify_files=False)
    if args.validate_only:
        print(json.dumps({"status": "valid", "task_id": compiled["task_id"],
                          "actions": len(compiled["actions"])}, ensure_ascii=False))
        return 0
    return ros_main(compiled, normalized_bundle, args.output, args.action_timeout,
                    args.robot_model)


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def _pgm_dimensions(path):
    """Read a P2/P5 PGM header without loading the complete map image."""
    with Path(path).open("rb") as stream:
        tokens = []
        while len(tokens) < 4:
            byte = stream.read(1)
            if not byte:
                raise ValueError(f"invalid PGM header: {path}")
            if byte == b"#":
                stream.readline()
                continue
            if byte.isspace():
                continue
            token = byte
            while True:
                byte = stream.read(1)
                if not byte or byte.isspace():
                    break
                token += byte
            tokens.append(token)
    if tokens[0] not in (b"P2", b"P5"):
        raise ValueError(f"unsupported map image format in {path}")
    return int(tokens[1]), int(tokens[2])


def ros_main(compiled, bundle, output_path, action_timeout, robot_model="footprint",
             plan_only=False):
    """Run a mixed-floor mission against an already-running Nav2 stack."""
    import rclpy
    import tf2_ros
    from action_msgs.msg import GoalStatus
    from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
    from nav2_msgs.action import ComputePathToPose, NavigateToPose
    from nav2_msgs.srv import GetCost, GetCostmap, LoadMap
    from nav_msgs.msg import OccupancyGrid
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    from rclpy.duration import Duration
    from rclpy.action import ActionClient
    from rclpy.node import Node
    from std_msgs.msg import String

    class Nav2Adapter(Node):
        def __init__(self):
            super().__init__("hospital_task_chain_nav2_runner")
            self.expected_maps = {}
            self.floor_map_data = {}
            for key, config in bundle["floors"].items():
                yaml_path = Path(config["map_yaml_path"]).resolve()
                metadata = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                image = (yaml_path.parent / metadata["image"]).resolve()
                self.expected_maps[int(key)] = {
                    "width": _pgm_dimensions(image)[0],
                    "height": _pgm_dimensions(image)[1],
                    "resolution": float(metadata["resolution"]),
                    "origin": [float(v) for v in metadata["origin"][:2]],
                    "yaml_path": str(yaml_path),
                    "floor_z_m": float(config["floor_z_m"]),
                }
                self.floor_map_data[int(key)] = load_floor_navigation_map(config)
            self.active_floor = None
            self.latest_grid = None
            self.create_subscription(OccupancyGrid, "/map", self._on_grid, 10)
            self.initialpose_pub = self.create_publisher(
                PoseWithCovarianceStamped, "/initialpose", 10)
            status_qos = QoSProfile(depth=1)
            status_qos.reliability = ReliabilityPolicy.RELIABLE
            status_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
            self.status_pub = self.create_publisher(
                String, "/hospital/multifloor/status", status_qos)
            self.map_client = self.create_client(LoadMap, "/map_server/load_map")
            self.costmap_get_client = self.create_client(
                GetCostmap, "/global_costmap/get_costmap")
            self.cost_client = self.create_client(
                GetCost, "/global_costmap/get_cost_global_costmap")
            self.path_client = ActionClient(self, ComputePathToPose, "/compute_path_to_pose")
            self.nav_client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
            self.tf_buffer = tf2_ros.Buffer()
            self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self,
                                                         spin_thread=True)

        def publish_live_status(self, payload):
            message = String()
            message.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            self.status_pub.publish(message)
            if payload["state"] in ("succeeded", "failed"):
                self.status_pub.wait_for_all_acked(Duration(seconds=1.0))

        def _on_grid(self, message):
            self.latest_grid = message

        def _wait(self, future, timeout, label):
            rclpy.spin_until_future_complete(self, future, timeout_sec=timeout)
            if not future.done() or future.result() is None:
                raise TimeoutError(f"timeout/empty result waiting for {label}")
            return future.result()

        def load_map(self, floor_id, map_yaml_path):
            expected = self.expected_maps.get(int(floor_id))
            if expected is None or str(Path(map_yaml_path).resolve()) != expected["yaml_path"]:
                return {"status": "failed", "reason": "map/floor contract mismatch"}
            if not self.map_client.wait_for_service(timeout_sec=15.0):
                return {"status": "failed", "reason": "LoadMap service unavailable"}
            if not self.costmap_get_client.wait_for_service(timeout_sec=10.0):
                return {"status": "failed", "reason": "global GetCostmap service unavailable"}
            request = LoadMap.Request()
            request.map_url = expected["yaml_path"]
            response = self._wait(self.map_client.call_async(request), 30.0, "LoadMap")
            response_ok = grid_geometry_matches(response.map, expected, bundle["frame_id"])
            response_data = tuple(response.map.data)
            deadline = time.monotonic() + 20.0
            topic_ok = False
            costmap_ok = False
            costmap_geometry = None
            while time.monotonic() < deadline:
                if self.latest_grid is not None and grid_geometry_matches(
                        self.latest_grid, expected, bundle["frame_id"]) and tuple(
                            self.latest_grid.data) == response_data:
                    topic_ok = True
                try:
                    costmap_response = self._wait(
                        self.costmap_get_client.call_async(GetCostmap.Request()),
                        5.0, "global GetCostmap")
                    costmap_geometry = costmap_metadata_matches(
                        costmap_response.map, expected, bundle["frame_id"])
                    costmap_ok = costmap_geometry
                except (RuntimeError, TimeoutError):
                    costmap_ok = False
                if topic_ok and costmap_ok:
                    break
                rclpy.spin_once(self, timeout_sec=0.1)
            ok = response.result == 0 and response_ok and topic_ok and costmap_ok
            self.active_floor = int(floor_id) if ok else None
            return {"status": "succeeded" if ok else "failed",
                    "result_code": int(response.result),
                    "response_geometry_matches": response_ok,
                    "published_map_matches": topic_ok,
                    "global_costmap_matches_active_map": costmap_ok,
                    "costmap_geometry_matches": costmap_geometry}

        def _pose(self, value):
            pose = normalize_nav2_pose(value, bundle["frame_id"])
            message = PoseStamped()
            message.header.frame_id = bundle["frame_id"]
            message.header.stamp = self.get_clock().now().to_msg()
            message.pose.position.x = pose["x"]
            message.pose.position.y = pose["y"]
            message.pose.orientation.z = math.sin(pose["yaw"] / 2)
            message.pose.orientation.w = math.cos(pose["yaw"] / 2)
            return pose, message

        def reset_pose(self, floor_id, value):
            if self.active_floor != int(floor_id):
                return {"status": "failed", "reason": "floor map is not active"}
            pose, _ = self._pose(value)
            deadline = time.monotonic() + 20.0
            while self.initialpose_pub.get_subscription_count() < 1:
                if time.monotonic() >= deadline:
                    return {"status": "failed", "reason": "initialpose subscriber unavailable"}
                rclpy.spin_once(self, timeout_sec=0.1)
            message = PoseWithCovarianceStamped()
            message.header.frame_id = bundle["frame_id"]
            message.pose.pose.position.x = pose["x"]
            message.pose.pose.position.y = pose["y"]
            message.pose.pose.orientation.z = math.sin(pose["yaw"] / 2)
            message.pose.pose.orientation.w = math.cos(pose["yaw"] / 2)
            while time.monotonic() < deadline:
                message.header.stamp = self.get_clock().now().to_msg()
                self.initialpose_pub.publish(message)
                rclpy.spin_once(self, timeout_sec=0.1)
                try:
                    tf = self.tf_buffer.lookup_transform(
                        bundle["frame_id"], "base_link", rclpy.time.Time())
                except Exception:
                    continue
                dx = tf.transform.translation.x - pose["x"]
                dy = tf.transform.translation.y - pose["y"]
                q = tf.transform.rotation
                yaw = math.atan2(2 * (q.w*q.z + q.x*q.y),
                                 1 - 2*(q.y*q.y + q.z*q.z))
                dyaw = math.atan2(math.sin(yaw-pose["yaw"]),
                                  math.cos(yaw-pose["yaw"]))
                if math.hypot(dx, dy) <= 0.08 and abs(dyaw) <= 0.08:
                    return {"status": "succeeded", "tf_error_m": math.hypot(dx, dy)}
            return {"status": "failed", "reason": "TF did not confirm reset pose"}

        def _current_pose(self):
            tf = self.tf_buffer.lookup_transform(bundle["frame_id"], "base_link",
                                                 rclpy.time.Time())
            return {"x": tf.transform.translation.x, "y": tf.transform.translation.y,
                    "orientation": tf.transform.rotation}

        def _get_cost(self, pose):
            if not self.cost_client.wait_for_service(timeout_sec=5.0):
                raise RuntimeError("global costmap GetCost service unavailable")
            normalized = normalize_nav2_pose(pose, bundle["frame_id"])
            request = GetCost.Request()
            request.use_footprint = costmap_uses_footprint(robot_model)
            request.x, request.y = normalized["x"], normalized["y"]
            request.theta = normalized["yaw"]
            response = self._wait(self.cost_client.call_async(request), 10.0, "GetCost")
            return int(response.cost)

        def verify_start_cost(self, pose):
            cost = self._get_cost(pose)
            if not costmap_cost_is_safe(cost):
                return {"status": "failed", "reason": "fixed start is unsafe on live costmap",
                        "cost": cost}
            return {"status": "succeeded", "cost": cost}

        def _compute(self, start, goal):
            try:
                cost = self._get_cost(goal)
            except (RuntimeError, TimeoutError) as error:
                return {"status": "costmap_unavailable", "path": [], "reason": str(error)}
            if not costmap_cost_is_safe(cost):
                return {"status": "unsafe_goal_costmap", "path": []}
            request = ComputePathToPose.Goal()
            request.start = self._pose(start)[1]
            request.goal = self._pose(goal)[1]
            request.use_start = True
            request.planner_id = "GridBased"
            handle = self._wait(self.path_client.send_goal_async(request), 20.0,
                                "ComputePathToPose acceptance")
            if not handle.accepted:
                return {"status": "rejected", "path": []}
            wrapped = self._wait(handle.get_result_async(), 60.0,
                                 "ComputePathToPose result")
            poses = [{"x": item.pose.position.x, "y": item.pose.position.y}
                for item in wrapped.result.path.poses]
            return normalize_compute_path_result(
                wrapped.status == GoalStatus.STATUS_SUCCEEDED, poses)

        @staticmethod
        def _path_length(path):
            if not isinstance(path, list) or not path:
                return None
            points = []
            for pose in path:
                if not isinstance(pose, dict):
                    return None
                try:
                    x, y = float(pose["x"]), float(pose["y"])
                except (KeyError, OverflowError, TypeError, ValueError):
                    return None
                if not math.isfinite(x) or not math.isfinite(y):
                    return None
                points.append((x, y))
            length = sum(math.hypot(bx - ax, by - ay)
                         for (ax, ay), (bx, by) in zip(points, points[1:]))
            return length if math.isfinite(length) else None

        def plan_to_pose(self, floor_id, start_pose, goal_pose):
            if self.active_floor != int(floor_id):
                return {"status": "wrong_floor", "reason": "planner map is not active"}
            result = self._compute(start_pose, goal_pose)
            if result.get("status") != "succeeded":
                return {"status": result.get("status", "no_path"),
                        "reason": "Nav2 ComputePathToPose did not find a portal path"}
            length = self._path_length(result.get("path"))
            if length is None:
                return {"status": "invalid_path", "reason": "Nav2 returned invalid path geometry"}
            return {"status": "path_found", "path_length_m": length,
                    "path": result["path"]}

        def switch_floor(self, edge, map_yaml_path):
            target_floor = int(edge["to_floor"])
            loaded = self.load_map(target_floor, map_yaml_path)
            if loaded.get("status") != "succeeded":
                return {"status": "failed", "reason": f"LoadMap failed: {loaded}"}
            reset = self.reset_pose(target_floor, edge["exit_pose"])
            if reset.get("status") != "succeeded":
                return {"status": "failed", "reason": f"exit pose reset failed: {reset}"}
            return {"status": "succeeded", "map_load": loaded, "pose_reset": reset}

        def plan_candidate(self, floor_id, candidate):
            if candidate.get("floor_id") != int(floor_id):
                return {"status": "wrong_floor"}
            return self.plan_stop(int(floor_id), [candidate])

        def set_virtual_pose(self, floor_id, pose):
            """Teleport only the kinematic preflight state; no Nav2 motion goal is sent."""
            return self.reset_pose(int(floor_id), pose)

        def restore_start(self, initial_state):
            floor_id = int(initial_state["floor_id"])
            map_path = bundle["floors"][str(floor_id)]["map_yaml_path"]
            loaded = self.load_map(floor_id, map_path)
            if loaded.get("status") != "succeeded":
                return {"status": "failed", "reason": f"start map restoration failed: {loaded}"}
            reset = self.reset_pose(floor_id, initial_state["pose"])
            if reset.get("status") != "succeeded":
                return {"status": "failed", "reason": f"start pose restoration failed: {reset}"}
            safe = self.verify_start_cost(initial_state["pose"])
            if safe.get("status") != "succeeded":
                return {"status": "failed", "reason": safe.get("reason"),
                        "map_load": loaded, "pose_reset": reset, "cost_check": safe}
            return {"status": "succeeded", "map_load": loaded,
                    "pose_reset": reset, "cost_check": safe}

        def plan_stop(self, floor_id, candidates):
            if self.active_floor != int(floor_id):
                return {"status": "wrong_floor"}
            if not self.path_client.wait_for_server(timeout_sec=10.0):
                return {"status": "planner_unavailable"}
            pipeline = build_single_step_pipeline({
                "stop_index": 0, "floor_id": int(floor_id),
                "floor_assignment_source": candidates[0]["floor_assignment_source"],
                "candidate_targets": candidates})
            from plan_candidate_task_chain import plan_task_chain
            result = plan_task_chain(pipeline, self._current_pose(), self._compute,
                                     map_floor=int(floor_id),
                                     map_floor_z=self.expected_maps[int(floor_id)]["floor_z_m"],
                                     map_data=self.floor_map_data[int(floor_id)],
                                     allow_costmap_fallbacks=True,
                                     point_robot=robot_model == "point")
            steps = result.get("steps", [])
            if result.get("status") != "planning_only_complete" or not steps:
                return {"status": "no_path", "planner_result": result}
            step = steps[0]
            return {"status": "path_found", "selected_node_id": step["selected_node_id"],
                    "goal": step["goal"], "path_length_m": step.get("path_length_m"),
                    "path": step.get("path", []),
                    "projection": step.get("projection")}

        def navigate_to_pose(self, value):
            if not self.nav_client.wait_for_server(timeout_sec=15.0):
                return {"status": "failed", "reason": "NavigateToPose unavailable"}
            _, pose = self._pose(value)
            goal = NavigateToPose.Goal()
            goal.pose = pose
            handle = self._wait(self.nav_client.send_goal_async(goal), 20.0,
                                "NavigateToPose acceptance")
            if not handle.accepted:
                return {"status": "failed", "reason": "goal rejected"}
            result = self._wait(handle.get_result_async(), action_timeout,
                                "NavigateToPose result")
            final_pose = None
            try:
                final_pose = normalize_nav2_pose(self._current_pose(), bundle["frame_id"])
            except Exception:
                pass
            return {"status": "succeeded" if result.status == GoalStatus.STATUS_SUCCEEDED
                    else "failed", "action_status": int(result.status),
                    "final_pose": final_pose}

    rclpy.init()
    node = Nav2Adapter()
    started = time.monotonic()
    candidate_mode = compiled.get("schema_version") == CANDIDATE_MISSION_SCHEMA
    task_id = compiled["task_id"]
    map_bundle_id = compiled["map_bundle_id"]
    state = {"schema_version": "hospital-task-chain-nav2-results-v1",
             "task_id": task_id, "map_bundle_id": map_bundle_id,
             "status": "running", "events": [], "reached_stops": [],
             "robot_model": robot_model,
             "execution_scope": ("point-robot planar Nav2; abstract floor transitions"
                                 if robot_model == "point" else
                                 "footprint planar Nav2; abstract floor transitions"),
             "input_mission_sha256": hashlib.sha256(
                 json.dumps(compiled, sort_keys=True, ensure_ascii=False).encode("utf-8")
             ).hexdigest()}
    _atomic_json(output_path, state)
    try:
        route_selection = None
        if candidate_mode:
            if plan_only:
                plan_result = plan_candidate_mission(compiled, bundle, node)
                state.update(plan_result)
                state["duration_s"] = round(time.monotonic() - started, 3)
                _atomic_json(output_path, state)
                node.get_logger().info("NAV2_PLAN_RESULT " + json.dumps(state, ensure_ascii=False))
                return 0 if plan_result.get("status") == "planned" else 2

            initial_state = {"floor_id": int(compiled["initial_floor"]),
                             "pose": dict(compiled["initial_pose"])}
            restored = node.restore_start(initial_state)
            if restored.get("status") != "succeeded":
                state.update(status="failed", failure_stage="candidate_route_start_preflight",
                             error=f"fixed F0 start failed costmap/TF preflight: {restored}")
                _atomic_json(output_path, state)
                return 2
            route_resolution = resolve_candidate_mission(compiled, bundle, node)
            route_selection = route_resolution.get("route_selection")
            state["candidate_route_selection"] = route_selection
            if route_resolution.get("status") != "selected":
                state.update(status="failed",
                             failure_stage=route_resolution.get("status", "candidate_route_selection"),
                             error=(route_selection or {}).get("reason", "no feasible candidate sequence"))
                state["duration_s"] = round(time.monotonic() - started, 3)
                _atomic_json(output_path, state)
                node.publish_live_status(build_live_status(
                    task_id, "failed", active_floor=initial_state["floor_id"],
                    error=state["error"]))
                node.get_logger().error("CANDIDATE_ROUTE_RESULT " +
                                        json.dumps(state, ensure_ascii=False))
                return 2
            compiled = compile_mission(route_resolution["resolved_mission"], bundle,
                                       verify_files=False)
            state["mission_sha256"] = hashlib.sha256(
                json.dumps(compiled, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            state["status"] = "running"
            state["active_floor"] = initial_state["floor_id"]
            _atomic_json(output_path, state)
        else:
            state["mission_sha256"] = state.pop("input_mission_sha256")
            _atomic_json(output_path, state)

        def persist_event(event):
            state["events"].append(event)
            state["active_floor"] = event.get("floor_id", state.get("active_floor"))
            if event.get("kind") == "navigate_to_stop" and _succeeded(event.get("result")):
                stop_index = event.get("stop_index")
                if stop_index not in state["reached_stops"]:
                    state["reached_stops"].append(stop_index)
            state["duration_s"] = round(time.monotonic() - started, 3)
            _atomic_json(output_path, state)

        result = execute_compiled_mission(
            compiled, node, event_callback=persist_event,
            status_callback=node.publish_live_status)
        state.update(result)
        state["duration_s"] = round(time.monotonic() - started, 3)
        _atomic_json(output_path, state)
        node.get_logger().info("MISSION_RESULT " + json.dumps(state, ensure_ascii=False))
        return 0 if result["status"] == "succeeded" else 2
    except Exception as error:
        state.update(status="failed", failure_stage="ros_adapter",
                     error=f"{type(error).__name__}: {error}")
        state["duration_s"] = round(time.monotonic() - started, 3)
        _atomic_json(output_path, state)
        node.publish_live_status(build_live_status(
            task_id, "failed", error=state["error"]))
        return 2
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
