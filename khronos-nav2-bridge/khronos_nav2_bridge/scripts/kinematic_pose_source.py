#!/usr/bin/env python3
"""Bounded planar pose source for visualization; not legged locomotion."""

import math
import time


class PoseState:
    """Planar kinematic state with bounded velocity and acceleration."""

    def __init__(self, max_v: float = 0.7, max_w: float = 1.0,
                 max_accel: float = 1.0, timeout: float = 0.5):
        self.x = 0.0
        self.y = 0.0
        self.yaw = 0.0
        self.v = 0.0
        self.w = 0.0
        self.target_v = 0.0
        self.target_w = 0.0
        self.last_cmd = 0.0
        self.max_v = max_v
        self.max_w = max_w
        self.max_accel = max_accel
        self.timeout = timeout

    def reset(self, x: float, y: float, yaw: float, now: float | None = None) -> None:
        """Reset pose with validation."""
        values = (float(x), float(y), float(yaw))
        if not all(math.isfinite(v) for v in values):
            raise ValueError("nonfinite reset pose")
        self.x = values[0]
        self.y = values[1]
        self.yaw = math.atan2(math.sin(values[2]), math.cos(values[2]))
        self.v = 0.0
        self.w = 0.0
        self.target_v = 0.0
        self.target_w = 0.0
        self.last_cmd = time.monotonic() if now is None else now

    def command(self, v: float, w: float, now: float | None = None) -> None:
        """Set velocity command with clamping."""
        if not all(math.isfinite(float(x)) for x in (v, w)):
            raise ValueError("nonfinite cmd_vel")
        self.target_v = max(-self.max_v, min(self.max_v, float(v)))
        self.target_w = max(-self.max_w, min(self.max_w, float(w)))
        self.last_cmd = time.monotonic() if now is None else now

    def step(self, dt: float, now: float | None = None) -> tuple:
        """Integrate one time step. Returns (x, y, yaw, v, w)."""
        if dt < 0 or not math.isfinite(dt):
            raise ValueError("invalid dt")
        now = time.monotonic() if now is None else now

        timed_out = (now - self.last_cmd) > self.timeout
        tv = 0.0 if timed_out else self.target_v
        tw = 0.0 if timed_out else self.target_w

        dv = self.max_accel * dt
        self.v += max(-dv, min(dv, tv - self.v))
        self.w += max(-dv, min(dv, tw - self.w))

        self.x += self.v * math.cos(self.yaw) * dt
        self.y += self.v * math.sin(self.yaw) * dt
        self.yaw = math.atan2(
            math.sin(self.yaw + self.w * dt),
            math.cos(self.yaw + self.w * dt),
        )

        return self.x, self.y, self.yaw, self.v, self.w


def yaw_quaternion(yaw: float) -> tuple:
    """Convert yaw angle to quaternion (x, y, z, w)."""
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


def main():
    import rclpy
    from geometry_msgs.msg import PoseWithCovarianceStamped, Twist, TransformStamped
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import JointState
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy
    from tf2_ros import TransformBroadcaster, StaticTransformBroadcaster

    class Source(Node):
        def __init__(self):
            super().__init__("kinematic_pose_source")
            self.declare_parameter("initial_x", 0.0)
            self.declare_parameter("initial_y", 0.0)
            self.declare_parameter("initial_yaw", 0.0)

            self.state = PoseState()
            self.state.x = float(self.get_parameter("initial_x").value)
            self.state.y = float(self.get_parameter("initial_y").value)
            self.state.yaw = float(self.get_parameter("initial_yaw").value)
            self.last = time.monotonic()

            qos = QoSProfile(depth=10)
            qos.reliability = ReliabilityPolicy.RELIABLE
            qos.durability = DurabilityPolicy.VOLATILE
            qos.history = HistoryPolicy.KEEP_LAST

            self.odom_pub = self.create_publisher(Odometry, "/odom", qos)
            self.joint_pub = self.create_publisher(JointState, "/joint_states", qos)
            self.cmd_sub = self.create_subscription(Twist, "/cmd_vel", self.cmd, qos)
            self.initialpose_sub = self.create_subscription(
                PoseWithCovarianceStamped, "/initialpose", self.reset_pose, qos
            )

            self.tf = TransformBroadcaster(self)
            self.static_tf = StaticTransformBroadcaster(self)
            self.timer = self.create_timer(1 / 30, self.tick)

            t = TransformStamped()
            t.header.frame_id = "map"
            t.child_frame_id = "odom"
            t.transform.rotation.w = 1.0
            self.static_tf.sendTransform(t)

            self.joints = [
                "LF_HAA", "LF_HFE", "LF_KFE",
                "RF_HAA", "RF_HFE", "RF_KFE",
                "LH_HAA", "LH_HFE", "LH_KFE",
                "RH_HAA", "RH_HFE", "RH_KFE",
            ]
            self.nominal = [0.0, 0.4, -0.8, 0.0, 0.4, -0.8,
                            0.0, -0.4, 0.8, 0.0, -0.4, 0.8]

        def cmd(self, msg):
            self.state.command(msg.linear.x, msg.angular.z)

        def reset_pose(self, msg):
            q = msg.pose.pose.orientation
            yaw = math.atan2(
                2.0 * (q.w * q.z + q.x * q.y),
                1.0 - 2.0 * (q.y * q.y + q.z * q.z),
            )
            now = time.monotonic()
            self.state.reset(msg.pose.pose.position.x, msg.pose.pose.position.y, yaw, now)
            self.last = now

        def tick(self):
            now = time.monotonic()
            dt = min(0.1, now - self.last)
            self.last = now
            x, y, yaw, v, w = self.state.step(dt, now)

            stamp = self.get_clock().now().to_msg()
            q = yaw_quaternion(yaw)

            o = Odometry()
            o.header.stamp = stamp
            o.header.frame_id = "odom"
            o.child_frame_id = "base_link"
            o.pose.pose.position.x = x
            o.pose.pose.position.y = y
            o.pose.pose.orientation.z = q[2]
            o.pose.pose.orientation.w = q[3]
            o.twist.twist.linear.x = v
            o.twist.twist.angular.z = w
            self.odom_pub.publish(o)

            t = TransformStamped()
            t.header.stamp = stamp
            t.header.frame_id = "odom"
            t.child_frame_id = "base_link"
            t.transform.translation.x = x
            t.transform.translation.y = y
            t.transform.rotation.z = q[2]
            t.transform.rotation.w = q[3]
            self.tf.sendTransform(t)

            j = JointState()
            j.header.stamp = stamp
            j.name = self.joints
            j.position = self.nominal
            self.joint_pub.publish(j)

    rclpy.init()
    n = Source()
    rclpy.spin(n)
    n.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
