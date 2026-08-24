#!/usr/bin/env python3
"""Mock ANYmal robot：task-plan 驱动 + checkpoint 状态机 + 静态/动态发布拆分。

MD Fix D / Part 4：
- --task-plan <json> 直接读 LLM+RAG 输出，经 NavigationGoalCompiler 转 waypoint
- checkpoint 状态机：5 markers（pending 黄 / current 红 / done 绿）+ checkpoint_pause
- 静态发布（TRANSIENT_LOCAL, 一次）：start / checkpoints / route Path / route LINE_STRIP
- 动态发布：robot pose+arrow 10Hz、state/demo_state 10Hz、goal 仅在 checkpoint 切换时、
  trail 低频(<=5Hz)并抽稀(>0.15m)

Topics:
  /anymald/mock_robot       Marker   CUBE+ARROW（10Hz）
  /anymald/start_marker     Marker   蓝色起点（一次）
  /anymald/checkpoints      MarkerArray  5 个 checkpoint（黄/红/绿 + 编号）
  /anymald/route            nav_msgs/Path  witness 展开完整 route（一次）
  /anymald/route_marker     Marker   LINE_STRIP route（一次）
  /anymald/goal_marker      Marker   当前 goal（checkpoint 切换时）
  /anymald/robot_trail      Marker   走过的轨迹（<=5Hz 抽稀）
  /anymald/state            String
  /anymald/demo_state       String   checkpoint i/5 + target + waypoint + state
"""
import argparse
import bisect
import json
import math
import sys
import time
import xml.etree.ElementTree as ET

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from geometry_msgs.msg import PoseStamped, TransformStamped
from nav_msgs.msg import Path
from sensor_msgs.msg import JointState
from std_msgs.msg import Int32, String
from tf2_ros import TransformBroadcaster
from visualization_msgs.msg import Marker, MarkerArray

from .data_loader import (DISPLAY_Z, BridgeData, color, dist, header, point,
                          quaternion_from_yaw)

# checkpoint 颜色：pending 黄 / current 红 / done 绿
_CHECKPOINT_COLOR = {
    "pending": (1.0, 0.1, 0.85, 0.9),
    "current": (1.0, 0.45, 0.15, 0.95),
    "done": (0.2, 0.85, 1.0, 0.95),
}


def _latch():
    return QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                      durability=DurabilityPolicy.TRANSIENT_LOCAL)


class MockRobotNode(Node):
    def __init__(self, data: BridgeData, graph, shortest_path_fn, compiler,
                 start_ref, goal_refs: list, rate: float, speed: float,
                 checkpoint_pause: float = 1.0, frame: str = "map",
                 start_delay: float = 0.0, start_waypoint: int | None = None,
                 robot_frame: str = "base", anymal_urdf: str | None = None,
                 show_marker_robot: bool = True, robot_z_offset: float = 0.0,
                 demo_duration: float = 0.0, camera_follow_yaw: bool = True,
                 focus_floors: bool = False):
        super().__init__("anymald_mock_robot")
        self.data = data
        self.frame = frame
        self.graph = graph
        self.shortest_path = shortest_path_fn
        self.compiler = compiler
        self.rate = rate
        self.speed = speed
        self.checkpoint_pause = checkpoint_pause
        self.robot_frame = robot_frame
        self.show_marker_robot = show_marker_robot
        self.robot_z_offset = robot_z_offset
        self.demo_duration = demo_duration
        self.camera_follow_yaw = camera_follow_yaw
        self.focus_floors = focus_floors
        self._anim_delay = start_delay
        self._begin = time.monotonic()
        self._anim_ready = start_delay <= 0
        self._last_cp = 0.0
        self.state = "IDLE"

        # 解析 start + goal waypoints（经 NavigationGoalCompiler）
        self.start_ref = start_ref
        self.goal_refs = goal_refs
        self.start_wp = (int(start_waypoint) if start_waypoint is not None
                         and int(start_waypoint) >= 0
                         else compiler.compile(start_ref))
        self.goal_wps = [compiler.compile(ref) for ref in goal_refs]
        reachable = data.reachable_waypoint_ids(self.start_wp)
        displayed_by_floor = {}
        for wid in reachable:
            p = data.spars_pos(wid)
            if p is not None:
                displayed_by_floor.setdefault(data.floor_of_waypoint(wid), []).append(p)
        displayed = [p for points in displayed_by_floor.values() for p in points]
        self.floor_centers = {
            floor: tuple((min(p[i] for p in points) + max(p[i] for p in points)) / 2
                         for i in range(3))
            for floor, points in displayed_by_floor.items()
        }
        self.active_floor = data.floor_of_waypoint(self.start_wp)
        self._camera_xyz = list(self.floor_centers.get(
            self.active_floor, data.spars_pos(self.start_wp) or (0.0, 0.0, 0.0)))
        if data.floor_spacing and displayed:
            center = [(min(p[i] for p in displayed) + max(p[i] for p in displayed)) / 2
                      for i in range(3)]
            # ponytail: ThirdPersonFollower projects exploded X columns toward
            # the lower screen edge; this small calibrated bias keeps both
            # columns visible during the orbit.
            center[0] += data.floor_spacing * 0.2
            self.camera_center = tuple(center)
        else:
            self.camera_center = None
        if not self.goal_wps:
            self.get_logger().error("无有效 goal")
            self.state = "NO_PATH"

        # ---- publishers ----
        # 静态（TRANSIENT_LOCAL）
        self.pub_route = self.create_publisher(Path, "/anymald/route", _latch())
        self.pub_route_marker = self.create_publisher(Marker, "/anymald/route_marker", _latch())
        self.pub_start = self.create_publisher(Marker, "/anymald/start_marker", _latch())
        self.pub_checkpoints = self.create_publisher(MarkerArray, "/anymald/checkpoints", _latch())
        # 动态
        self.pub_robot = self.create_publisher(Marker, "/anymald/mock_robot", 10)
        self.pub_goal = self.create_publisher(Marker, "/anymald/goal_marker", 10)
        self.pub_state = self.create_publisher(String, "/anymald/state", 10)
        self.pub_demo = self.create_publisher(String, "/anymald/demo_state", 10)
        self.pub_trail = self.create_publisher(Marker, "/anymald/robot_trail", 10)
        self.pub_joints = self.create_publisher(JointState, "/joint_states", 10)
        self.pub_active_floor = self.create_publisher(
            Int32, "/anymald/active_floor", _latch())
        self.tf_broadcaster = TransformBroadcaster(self)
        self.joint_names = self._load_joint_names(anymal_urdf)

        # 规划完整 route + checkpoint 位置
        self._build_route()
        if self.demo_duration > 0 and self._cum[-1] > 0:
            self.speed = self._cum[-1] / self.demo_duration

        # checkpoint 状态
        self.checkpoint_state = ["pending"] * len(self.goal_wps)
        self.cur_checkpoint = 0
        self.pause_remaining = 0.0
        if self.goal_wps:
            self.checkpoint_state[0] = "current"
        if self.state != "NO_PATH":
            self.state = ("MOVING" if self._cum and self._cum[-1] > 1e-6 else "REACHED") \
                if self._anim_ready else "IDLE"
        self.trail: list[tuple] = []

        # 静态发布一次
        self._publish_start()
        self._publish_checkpoints()
        self._publish_route()
        self._publish_active_floor()

        # 动态 timer
        self.timer = self.create_timer(1.0 / rate, self._tick_guard)
        trail_rate = min(rate, 5.0)
        self.trail_timer = self.create_timer(1.0 / trail_rate, self._trail_guard)
        self.get_logger().info(
            f"start={start_ref}(wp{self.start_wp}) goals={goal_refs} "
            f"wps={self.goal_wps} state={self.state}")

    def _tick_guard(self):
        """start_delay 延迟动画；静态立即发布。TRANSIENT_LOCAL 对 late
        subscriber 不可靠，延迟期间每 2s 重发 checkpoint/start 保证 RViz 显示。"""
        if not self._anim_ready:
            if time.monotonic() - self._begin < self._anim_delay:
                now = time.monotonic()
                if now - self._last_cp >= 2.0:
                    self._last_cp = now
                    self._publish_checkpoints()
                    self._publish_start()
                    self._publish_route()
                stamp = self.get_clock().now().to_msg()
                self._publish_robot(stamp)
                self._publish_state(stamp)
                return
            self._anim_ready = True
            self.state = "MOVING" if self._cum[-1] > 1e-6 else "REACHED"
            self.get_logger().info(
                f"robot animation started after {self._anim_delay:.0f}s static delay")
        self.tick()

    def _trail_guard(self):
        if self._anim_ready:
            self._update_trail()

    # ---- route 构建 ----
    def _build_route(self):
        self.route_wps = [self.start_wp]
        start_pos = self.data.spars_pos(self.start_wp)
        self.route_pts = [start_pos] if start_pos is not None else []
        self._cum = [0.0]
        self._checkpoint_cum = []
        if self.state == "NO_PATH":
            return
        seq = [self.start_wp]
        cur = self.start_wp
        for cwp in self.goal_wps:
            path, _ = self.shortest_path(self.graph, cur, cwp)
            if path is None:
                self.state = "NO_PATH"
                self.get_logger().error(f"NO_PATH: {cur} -> {cwp}")
                return
            seq.extend(path[1:])
            cur = cwp
        self.route_wps = seq
        self.route_pts = [(px, py, pz) for px, py, pz in self.data.route_positions(seq)]
        self._cum = [0.0]
        for i in range(len(self.route_pts) - 1):
            self._cum.append(self._cum[-1] + dist(self.route_pts[i], self.route_pts[i + 1]))
        self._s_cur = 0.0
        # 每个 checkpoint 到达时的累计距离
        self._checkpoint_cum = []
        search_from = 0
        for wp in self.goal_wps:
            gp = self.data.spars_pos(wp)
            found = None
            for i in range(search_from, len(self.route_pts)):
                p = self.route_pts[i]
                if gp is not None and dist(p, gp) < 0.05:
                    found = i
                    break
            self._checkpoint_cum.append(self._cum[found] if found is not None
                                        else self._cum[-1])
            if found is not None:
                search_from = found

    @staticmethod
    def _load_joint_names(urdf_path: str | None) -> list[str]:
        if not urdf_path:
            return []
        root = ET.parse(urdf_path).getroot()
        return [j.get("name") for j in root.findall("joint")
                if j.get("type") != "fixed"]

    # ---- 静态发布 ----
    def _publish_active_floor(self):
        self.pub_active_floor.publish(Int32(data=self.active_floor))

    def _set_active_floor(self, floor: int):
        if not self.focus_floors or floor == self.active_floor:
            return
        self.active_floor = floor
        self._publish_active_floor()
        self._publish_start()
        self._publish_checkpoints()
        self._publish_route()
        self._publish_goal()
        self._publish_trail_marker()
        self.get_logger().info(f"ACTIVE_FLOOR {floor}")

    def _publish_start(self):
        sm = Marker()
        sm.header = header(self.frame)
        sm.ns = "start"
        sm.id = 0
        sm.type = Marker.CYLINDER
        sm.action = Marker.ADD
        sp = self.data.spars_pos(self.start_wp)
        if (self.focus_floors
                and self.data.floor_of_waypoint(self.start_wp) != self.active_floor):
            sm.action = Marker.DELETE
        if sp is not None:
            sm.pose.position.x, sm.pose.position.y, sm.pose.position.z = sp[0], sp[1], sp[2]
        sm.pose.orientation = quaternion_from_yaw(0.0)
        scale = 1.35 if self.data.floor_spacing else 0.9
        sm.scale.x, sm.scale.y, sm.scale.z = scale, scale, 0.3
        sm.color = color(0.2, 0.4, 1.0, 1.0)   # 蓝色起点
        self.pub_start.publish(sm)

    def _publish_checkpoints(self):
        ma = MarkerArray()
        delete_all = Marker()
        delete_all.action = Marker.DELETEALL
        ma.markers.append(delete_all)
        sphere_scale = 1.8 if self.data.floor_spacing else (1.0 if self.focus_floors else 0.65)
        sphere_height = 1.0 if self.data.floor_spacing else (0.7 if self.focus_floors else 0.55)
        text_height = 2.2 if self.data.floor_spacing else (1.45 if self.focus_floors else 1.0)
        text_scale = 1.3 if self.data.floor_spacing else (0.65 if self.focus_floors else 0.42)
        for i, st in enumerate(self.checkpoint_state):
            wp = self.goal_wps[i]
            if (self.focus_floors
                    and self.data.floor_of_waypoint(wp) != self.active_floor):
                continue
            p = self.data.spars_pos(wp)
            if p is None:
                continue
            m = Marker()
            m.header = header(self.frame)
            m.ns = "checkpoints"
            m.id = i
            m.type = Marker.SPHERE
            m.action = Marker.ADD
            m.pose.position.x, m.pose.position.y, m.pose.position.z = (
                p[0], p[1], p[2] + sphere_height)
            m.pose.orientation = quaternion_from_yaw(0.0)
            m.scale.x = m.scale.y = m.scale.z = sphere_scale
            m.color = color(*_CHECKPOINT_COLOR[st])
            ma.markers.append(m)
            tm = Marker()
            tm.header = header(self.frame)
            tm.ns = "checkpoint_text"
            tm.id = 100 + i
            tm.type = Marker.TEXT_VIEW_FACING
            tm.action = Marker.ADD
            tm.pose.position.x, tm.pose.position.y, tm.pose.position.z = (
                p[0], p[1], p[2] + text_height)
            tm.text = f"{i + 1}/{len(self.goal_wps)}"
            tm.scale.z = text_scale
            tm.color = color(1.0, 1.0, 1.0, 1.0)
            ma.markers.append(tm)
        self.pub_checkpoints.publish(ma)

    def _publish_route(self):
        path_msg = Path()
        path_msg.header = header(self.frame)
        for px, py, pz in self.route_pts:
            ps = PoseStamped()
            ps.header = header(self.frame)
            ps.pose.position = point(px, py, pz)
            ps.pose.orientation = quaternion_from_yaw(0.0)
            path_msg.poses.append(ps)
        self.pub_route.publish(path_msg)
        rm = Marker()
        rm.header = header(self.frame)
        rm.ns = "route"
        rm.id = 0
        rm.type = Marker.LINE_LIST if self.focus_floors else Marker.LINE_STRIP
        rm.action = Marker.ADD
        rm.pose.orientation = quaternion_from_yaw(0.0)
        rm.scale.x = 0.18 if self.data.floor_spacing else 0.08
        rm.color = color(0.0, 1.0, 0.4, 0.9)
        if self.focus_floors:
            for p0, p1 in zip(self.route_pts, self.route_pts[1:]):
                if (self.data.floor_of_z(p0[2] - self.data.z_offset) == self.active_floor
                        and self.data.floor_of_z(p1[2] - self.data.z_offset) == self.active_floor):
                    rm.points.extend((point(*p0), point(*p1)))
        else:
            for px, py, pz in self.route_pts:
                rm.points.append(point(px, py, pz))
        self.pub_route_marker.publish(rm)

    # ---- 动态 ----
    def _pos_at(self, s):
        if not self.route_pts:
            return 0.0, 0.0, 0.0, 0.0
        if len(self.route_pts) == 1:
            return (*self.route_pts[0], 0.0)
        i = max(0, min(bisect.bisect_right(self._cum, s) - 1, len(self.route_pts) - 2))
        p0, p1 = self.route_pts[i], self.route_pts[i + 1]
        seg = self._cum[i + 1] - self._cum[i]
        t = 0.0 if seg <= 1e-9 else (s - self._cum[i]) / seg
        t = max(0.0, min(1.0, t))
        x = p0[0] + (p1[0] - p0[0]) * t
        y = p0[1] + (p1[1] - p0[1]) * t
        z = p0[2] + (p1[2] - p0[2]) * t
        yaw = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        return x, y, z, yaw

    def tick(self):
        stamp = self.get_clock().now().to_msg()
        if self.state == "MOVING":
            dt = 1.0 / self.rate
            if self.pause_remaining > 0:
                self.pause_remaining -= dt
            else:
                self._s_cur = min(self._s_cur + self.speed * dt, self._cum[-1])
                while (self.cur_checkpoint < len(self.goal_wps) and
                       self._s_cur >= self._checkpoint_cum[self.cur_checkpoint] - 1e-9):
                    self.checkpoint_state[self.cur_checkpoint] = "done"
                    self.cur_checkpoint += 1
                    if self.cur_checkpoint < len(self.goal_wps):
                        self.checkpoint_state[self.cur_checkpoint] = "current"
                    else:
                        self.state = "REACHED"
                    self.pause_remaining = self.checkpoint_pause
                    self._publish_checkpoints()
                    self._publish_goal()
                    self.get_logger().info(
                        f"CHECKPOINT_REACHED {self.cur_checkpoint}/{len(self.goal_wps)}")
                if self._s_cur >= self._cum[-1] - 1e-9:
                    self.state = "REACHED"
        self._publish_robot(stamp)
        self._publish_state(stamp)

    def _publish_goal(self):
        g = Marker()
        g.header = header(self.frame)
        g.ns = "goal"
        g.id = 0
        g.type = Marker.SPHERE
        g.action = Marker.ADD
        if self.cur_checkpoint < len(self.goal_wps):
            goal_wp = self.goal_wps[self.cur_checkpoint]
            if (self.focus_floors
                    and self.data.floor_of_waypoint(goal_wp) != self.active_floor):
                g.action = Marker.DELETE
            gp = self.data.spars_pos(goal_wp)
            if gp is not None:
                g.pose.position.x, g.pose.position.y, g.pose.position.z = gp
        else:
            g.action = Marker.DELETE
        g.pose.orientation = quaternion_from_yaw(0.0)
        goal_scale = 0.9 if self.data.floor_spacing else (0.7 if self.focus_floors else 0.45)
        g.scale.x = g.scale.y = g.scale.z = goal_scale
        g.color = color(1.0, 0.15, 0.15, 0.9)
        self.pub_goal.publish(g)

    def _publish_robot(self, stamp):
        x, y, z, yaw = self._pos_at(self._s_cur)
        self._set_active_floor(self.data.floor_of_z(z - self.data.z_offset))
        q = quaternion_from_yaw(yaw)
        robot_tf = TransformStamped()
        robot_tf.header = header(self.frame, stamp)
        robot_tf.child_frame_id = self.robot_frame
        robot_tf.transform.translation.x = x
        robot_tf.transform.translation.y = y
        robot_tf.transform.translation.z = z + self.robot_z_offset
        robot_tf.transform.rotation = q

        # ThirdPersonFollower tracks this frame. The exploded-floor overview
        # uses map-relative yaw; close follow mode uses the robot heading.
        camera_tf = TransformStamped()
        camera_tf.header = header(self.frame, stamp)
        camera_tf.child_frame_id = "demo_camera_target"
        if self.focus_floors:
            target = self.floor_centers.get(self.active_floor, (x, y, z))
            # ponytail: one-second easing exposes the 3-D floor transition;
            # measured camera poses can replace this for a hardware replay.
            alpha = min(1.0, 1.0 / self.rate)
            self._camera_xyz = [a + (b - a) * alpha
                                for a, b in zip(self._camera_xyz, target)]
            camera_target = self._camera_xyz
        else:
            camera_target = self.camera_center or (x, y, z + 0.5)
        camera_tf.transform.translation.x = camera_target[0]
        camera_tf.transform.translation.y = camera_target[1]
        camera_tf.transform.translation.z = camera_target[2]
        base_yaw = yaw if self.camera_follow_yaw else 0.0
        orbit = (0.55 * math.sin(self._s_cur / 7.0) if self.camera_follow_yaw
                 else ((0.38 if self.focus_floors else 0.28)
                       * math.sin(self._s_cur / 18.0)))
        camera_tf.transform.rotation = quaternion_from_yaw(
            base_yaw + orbit)
        self.tf_broadcaster.sendTransform([robot_tf, camera_tf])

        if self.joint_names:
            joints = JointState()
            joints.header.stamp = stamp
            joints.name = self.joint_names
            # ponytail: fixed nominal stance is sufficient for navigation
            # playback; replace with measured joint states for gait animation.
            nominal = {
                "LF_HFE": 0.55, "LF_KFE": -1.1,
                "RF_HFE": 0.55, "RF_KFE": -1.1,
                "LH_HFE": -0.55, "LH_KFE": 1.1,
                "RH_HFE": -0.55, "RH_KFE": 1.1,
            }
            joints.position = [nominal.get(name, 0.0) for name in self.joint_names]
            self.pub_joints.publish(joints)

        if not self.show_marker_robot:
            return
        halo = Marker()
        halo.header = header(self.frame, stamp)
        halo.ns = "mock_robot"
        halo.id = 0
        halo.type = Marker.CYLINDER
        halo.action = Marker.ADD
        halo.pose.position.x, halo.pose.position.y, halo.pose.position.z = x, y, z + 0.05
        halo.pose.orientation = q
        halo.scale.x, halo.scale.y, halo.scale.z = 2.2, 2.2, 0.12
        halo.color = color(0.1, 0.95, 0.1, 0.78)
        arrow = Marker()
        arrow.header = header(self.frame, stamp)
        arrow.ns = "mock_robot_arrow"
        arrow.id = 1
        arrow.type = Marker.ARROW
        arrow.action = Marker.ADD
        arrow.pose.position.x, arrow.pose.position.y, arrow.pose.position.z = x, y, z + 0.5
        arrow.pose.orientation = q
        arrow.scale.x, arrow.scale.y, arrow.scale.z = 1.4, 0.18, 0.18
        arrow.color = color(0.0, 0.4, 0.0, 1.0)
        self.pub_robot.publish(halo)
        self.pub_robot.publish(arrow)

    def _update_trail(self):
        x, y, z, _ = self._pos_at(self._s_cur)
        if (not self.trail or dist(self.trail[-1], (x, y, z)) > 0.15):
            self.trail.append((x, y, z))
        self._publish_trail_marker()

    def _publish_trail_marker(self):
        tr = Marker()
        tr.header = header(self.frame)
        tr.ns = "robot_trail"
        tr.id = 0
        tr.type = Marker.LINE_LIST if self.focus_floors else Marker.LINE_STRIP
        tr.action = Marker.ADD
        tr.pose.orientation = quaternion_from_yaw(0.0)
        tr.scale.x = 0.2 if self.data.floor_spacing else 0.08
        tr.color = color(1.0, 0.55, 0.1, 0.95)   # 橙色轨迹
        if self.focus_floors:
            for p0, p1 in zip(self.trail, self.trail[1:]):
                if (self.data.floor_of_z(p0[2] - self.data.z_offset) == self.active_floor
                        and self.data.floor_of_z(p1[2] - self.data.z_offset) == self.active_floor):
                    tr.points.extend((point(*p0), point(*p1)))
        else:
            for px, py, pz in self.trail:
                tr.points.append(point(px, py, pz))
        self.pub_trail.publish(tr)

    def _publish_state(self, stamp):
        x, y, z, yaw = self._pos_at(self._s_cur)
        cur_target = (self.goal_refs[self.cur_checkpoint]
                      if self.cur_checkpoint < len(self.goal_refs) else None)
        cur_wp = (self.goal_wps[self.cur_checkpoint]
                  if self.cur_checkpoint < len(self.goal_wps) else None)
        progress = f"{self.cur_checkpoint}/{len(self.goal_wps)}"
        self.pub_state.publish(String(
            data=f"state={self.state} pos=({x:.2f},{y:.2f},{z:.2f}) "
                 f"yaw={math.degrees(yaw):.0f} s={self._s_cur:.1f}/{self._cum[-1]:.1f}m "
                 f"checkpoint={progress}"))
        self.pub_demo.publish(String(
            data=f"checkpoint={progress} target={cur_target} waypoint={cur_wp} "
                 f"state={self.state} progress={self._s_cur:.1f}/{self._cum[-1]:.1f}m"))


def _load_refs(task_plan_path, targets_str):
    if task_plan_path:
        with open(task_plan_path, encoding="utf-8") as f:
            plan = json.load(f)
        task_plan = plan.get("task_plan") or plan
        refs = []
        for step in task_plan.get("task_chain", []):
            if step.get("action") == "navigate_to":
                target = step.get("target_id") or step.get("target_ref")
                if target:
                    refs.append(target)
        if not refs:
            raise ValueError("task_plan 中无 navigate_to 步骤")
        return refs
    if targets_str:
        return [t.strip() for t in targets_str.split(",") if t.strip()]
    raise ValueError("需 --task-plan 或 --targets")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", required=True)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--canonical-graph", default=None)
    ap.add_argument("--vertical-transitions", default=None)
    ap.add_argument("--floor-model", default=None)
    ap.add_argument("--floor-spacing", type=float, default=0.0)
    ap.add_argument("--floor-columns", type=int, default=1)
    ap.add_argument("--z-offset", type=float, default=0.0)
    ap.add_argument("--native-z", default="false")
    ap.add_argument("--r", type=float, default=3.0)
    ap.add_argument("--ground-z", type=float, default=0.0)
    ap.add_argument("--frame", default="map")
    ap.add_argument("--start-room", type=int, default=0)
    ap.add_argument("--start-waypoint", type=int, default=None)
    ap.add_argument("--task-plan", default=None, help="LLM+RAG task plan json")
    ap.add_argument("--targets", default=None, help="逗号分隔目标（诊断用）")
    ap.add_argument("--rate", type=float, default=10.0, help="Hz")
    ap.add_argument("--mock-speed", type=float, default=3.0, help="m/s")
    ap.add_argument("--demo-duration", type=float, default=0.0,
                    help="移动总时长；>0 时覆盖 mock-speed")
    ap.add_argument("--checkpoint-pause", type=float, default=1.0, help="s")
    ap.add_argument("--start-delay", type=float, default=0.0,
                    help="启动后延迟秒数（先静态确认 mesh/waypoint/edges 再动画）")
    ap.add_argument("--anymal-urdf", default=None,
                    help="ANYmal-D URDF（发布 nominal joint states）")
    ap.add_argument("--robot-frame", default="base")
    ap.add_argument("--robot-z-offset", type=float, default=0.0)
    ap.add_argument("--show-marker-robot", default="true",
                    help="true=在 ANYmal 下显示宽视角定位光环")
    ap.add_argument("--camera-follow-yaw", default="true",
                    help="false=楼层总览使用地图相对镜头")
    ap.add_argument("--focus-floors", default="false",
                    help="仅显示机器人当前楼层，并随跨层路线切换")
    args, _ = ap.parse_known_args()

    # 复用 bridge planner + compiler（witness cost）
    sys.path.insert(0, args.base_dir)
    from waypoint_graph.planner import WaypointGraph, shortest_path  # noqa: E402
    from navigation_goal_compiler import NavigationGoalCompiler  # noqa: E402

    rclpy.init()
    try:
        native_z = args.native_z.lower() in ("true", "1", "yes")
        data = BridgeData(
            args.base_dir, r=args.r, ground_z=args.ground_z, frame=args.frame,
            data_dir=args.data_dir, canonical_graph=args.canonical_graph,
            vertical_transitions=args.vertical_transitions,
            project_z=None if native_z else DISPLAY_Z,
            floor_model=args.floor_model, floor_spacing=args.floor_spacing,
            floor_columns=args.floor_columns, z_offset=args.z_offset)
        graph = WaypointGraph.load(
            str(data.gen / f"waypoints_sparsified_R{args.r}.json"),
            str(data.gen / "witness_paths.json"),
            str(data.vertical_transitions_path) if data.vertical_transitions_path else None)
        compiler = NavigationGoalCompiler(str(data.gen / "semantic_to_waypoint.json"))
        refs = _load_refs(args.task_plan, args.targets)
        node = MockRobotNode(data, graph, shortest_path, compiler,
                             start_ref=f"R{args.start_room}", goal_refs=refs,
                             rate=args.rate, speed=args.mock_speed,
                             checkpoint_pause=args.checkpoint_pause, frame=args.frame,
                             start_delay=args.start_delay,
                             start_waypoint=args.start_waypoint,
                             robot_frame=args.robot_frame,
                             anymal_urdf=args.anymal_urdf,
                             show_marker_robot=args.show_marker_robot.lower()
                             in ("true", "1", "yes"),
                             robot_z_offset=args.robot_z_offset,
                             demo_duration=args.demo_duration,
                             camera_follow_yaw=args.camera_follow_yaw.lower()
                             in ("true", "1", "yes"),
                             focus_floors=args.focus_floors.lower()
                             in ("true", "1", "yes"))
        try:
            rclpy.spin(node)
        except (KeyboardInterrupt, ExternalShutdownException):
            pass
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
