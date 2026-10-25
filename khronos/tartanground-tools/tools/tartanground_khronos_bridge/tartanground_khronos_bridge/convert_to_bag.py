"""TartanGround -> Khronos ROS2 bag 转换器（指南 §14-18, §37）。

生成 topics：
  /tartanground/front/rgb/image_raw     sensor_msgs/Image     rgb8
  /tartanground/front/rgb/camera_info   sensor_msgs/CameraInfo
  /tartanground/front/depth/image_raw   sensor_msgs/Image     32FC1, meter
  /tartanground/front/semantic/image_raw sensor_msgs/Image    mono8 (raw ID)
  /tf                                    tf2_msgs/TFMessage    odom->base_link
  /tf_static                             tf2_msgs/TFMessage    base_link->camera_optical_frame

时间戳合成：t0=1.0s，dt=1/fps，RGB/Depth/Semantic/CameraInfo/TF 完全一致（指南 §15）。

用法：
  python -m tartanground_khronos_bridge.convert_to_bag \
    --dataset-root <dir> --start-frame 0 --num-frames 300 \
    --output <bag_dir>
"""
import argparse
import math
import sys
from pathlib import Path

import numpy as np

from .dataset_reader import TartanGroundDataset
from .pose_utils import R_BASE_OPT, T_BASE_OPT, ned_to_ros_pose

# 640x640 pinhole 官方内参（指南 §10）
INTRINSICS = dict(fx=320.0, fy=320.0, cx=320.0, cy=320.0, width=640, height=640)

RGB_TOPIC = "/tartanground/front/rgb/image_raw"
INFO_TOPIC = "/tartanground/front/rgb/camera_info"
DEPTH_TOPIC = "/tartanground/front/depth/image_raw"
SEMANTIC_TOPIC = "/tartanground/front/semantic/image_raw"
TF_TOPIC = "/tf"
TF_STATIC_TOPIC = "/tf_static"

CAMERA_FRAME = "camera_optical_frame"
BASE_FRAME = "base_link"
ODOM_FRAME = "odom"


def make_image(h, w, encoding, data, stamp, frame_id, step):
    from builtin_interfaces.msg import Time
    from sensor_msgs.msg import Image
    img = Image()
    img.header.stamp = Time(sec=stamp.sec, nanosec=stamp.nanosec)
    img.header.frame_id = frame_id
    img.height = h
    img.width = w
    img.encoding = encoding
    img.is_bigendian = 0
    img.step = step
    img.data = data.tobytes()
    return img


def make_camera_info(stamp, frame_id, k):
    from builtin_interfaces.msg import Time
    from sensor_msgs.msg import CameraInfo
    ci = CameraInfo()
    ci.header.stamp = Time(sec=stamp.sec, nanosec=stamp.nanosec)
    ci.header.frame_id = frame_id
    ci.height = ci.width = 640
    ci.distortion_model = "plumb_bob"
    ci.d = [0.0] * 5
    ci.k = k
    ci.r = [1.0, 0, 0, 0, 1, 0, 0, 0, 1]
    ci.p = [k[0], 0, k[2], 0,
            0, k[4], k[5], 0,
            0, 0, 1, 0]
    return ci


def make_tf_msg(stamp, transforms):
    from tf2_msgs.msg import TFMessage
    return TFMessage(transforms=transforms)


def make_transform_stamped(stamp, child, parent, trans, quat):
    from builtin_interfaces.msg import Time
    from geometry_msgs.msg import Quaternion, Transform, TransformStamped, Vector3
    ts = TransformStamped()
    ts.header.stamp = Time(sec=stamp.sec, nanosec=stamp.nanosec)
    ts.header.frame_id = parent
    ts.child_frame_id = child
    ts.transform.translation = Vector3(x=float(trans[0]), y=float(trans[1]), z=float(trans[2]))
    ts.transform.rotation = Quaternion(x=float(quat[0]), y=float(quat[1]),
                                       z=float(quat[2]), w=float(quat[3]))
    return ts


def _stamp(sec_float):
    from builtin_interfaces.msg import Time
    sec = int(math.floor(sec_float))
    nsec = int(round((sec_float - sec) * 1e9))
    return Time(sec=sec, nanosec=nsec)


def build_topics(writer):
    from rosbag2_py import TopicMetadata
    specs = [
        (RGB_TOPIC, "sensor_msgs/msg/Image"),
        (INFO_TOPIC, "sensor_msgs/msg/CameraInfo"),
        (DEPTH_TOPIC, "sensor_msgs/msg/Image"),
        (SEMANTIC_TOPIC, "sensor_msgs/msg/Image"),
        (TF_TOPIC, "tf2_msgs/msg/TFMessage"),
        (TF_STATIC_TOPIC, "tf2_msgs/msg/TFMessage"),
    ]
    for tid, (name, t) in enumerate(specs):
        writer.create_topic(TopicMetadata(id=tid, name=name, type=t,
                                          serialization_format="cdr"))


def serialize(msg):
    from rclpy.serialization import serialize_message
    return serialize_message(msg)


def convert(args, dataset, writer, static_tf):
    """写 bag。返回 (n_frames, depth_stats)。"""
    from rclpy.serialization import serialize_message

    k = [INTRINSICS["fx"], 0, INTRINSICS["cx"],
         0, INTRINSICS["fy"], INTRINSICS["cy"],
         0, 0, 1]
    n = min(args.num_frames, len(dataset) - args.start_frame)
    if n <= 0:
        raise ValueError("num_frames must be >= 1 and within dataset")

    start = args.start_frame
    dt = 1.0 / args.fps
    t0 = 1.0
    depth_min = args.depth_min
    depth_max = args.depth_max
    valid_depth_ratios = []

    # 静态 TF 先写一次（base_link -> camera_optical_frame）
    writer.write(TF_STATIC_TOPIC, serialize_message(static_tf), int(t0 * 1e9))

    for i in range(n):
        fi = start + i
        stamp_f = t0 + i * dt
        stamp = _stamp(stamp_f)

        rgb = dataset.rgb(fi)
        depth = dataset.depth(fi)
        seg = dataset.seg(fi)

        # 可选 depth 裁剪：超范围置 NaN（默认关闭，由 Khronos sensor range 处理）
        if depth_min is not None or depth_max is not None:
            mask = ~np.isfinite(depth)
            if depth_min is not None:
                mask |= depth < depth_min
            if depth_max is not None:
                mask |= depth > depth_max
            depth = depth.copy()
            depth[mask] = np.nan
        finite = np.isfinite(depth)
        valid_depth_ratios.append(float(finite.mean()) if depth.size else 0.0)

        # RGB
        img = make_image(640, 640, "rgb8", rgb, stamp, CAMERA_FRAME, 640 * 3)
        writer.write(RGB_TOPIC, serialize_message(img), int(stamp_f * 1e9))

        # CameraInfo
        ci = make_camera_info(stamp, CAMERA_FRAME, k)
        writer.write(INFO_TOPIC, serialize_message(ci), int(stamp_f * 1e9))

        # Depth 32FC1 meter
        d32 = np.ascontiguousarray(depth, dtype=np.float32)
        dimg = make_image(640, 640, "32FC1", d32, stamp, CAMERA_FRAME, 640 * 4)
        writer.write(DEPTH_TOPIC, serialize_message(dimg), int(stamp_f * 1e9))

        # Semantic mono8 (raw ID)
        s8 = np.ascontiguousarray(seg, dtype=np.uint8)
        simg = make_image(640, 640, "mono8", s8, stamp, CAMERA_FRAME, 640)
        writer.write(SEMANTIC_TOPIC, serialize_message(simg), int(stamp_f * 1e9))

        # TF odom -> base_link
        pos_ned = dataset.pose(fi)[:3]
        quat_ned = dataset.pose(fi)[3:]
        pos_enu, quat_ros = ned_to_ros_pose(pos_ned, quat_ned)
        tf_msg = make_tf_msg(stamp, [make_transform_stamped(
            stamp, BASE_FRAME, ODOM_FRAME, pos_enu, quat_ros)])
        writer.write(TF_TOPIC, serialize_message(tf_msg), int(stamp_f * 1e9))

        if (i + 1) % 50 == 0 or i + 1 == n:
            print(f"  [{i+1}/{n}] t={stamp_f:.1f}s  pose={pos_enu.round(2)}", flush=True)

    return n, valid_depth_ratios


def build_static_tf():
    """base_link -> camera_optical_frame（固定，指南 §13.4）。"""
    from builtin_interfaces.msg import Time
    from geometry_msgs.msg import Quaternion, Transform, TransformStamped, Vector3
    ts = TransformStamped()
    ts.header.stamp = Time(sec=0, nanosec=0)
    ts.header.frame_id = BASE_FRAME
    ts.child_frame_id = CAMERA_FRAME
    ts.transform.translation = Vector3(x=float(T_BASE_OPT[0]), y=float(T_BASE_OPT[1]), z=float(T_BASE_OPT[2]))
    q = _rotmat_to_quat(R_BASE_OPT)
    ts.transform.rotation = Quaternion(x=q[0], y=q[1], z=q[2], w=q[3])
    return ts


def _rotmat_to_quat(Rm):
    from .pose_utils import matrix_to_quat
    return matrix_to_quat(Rm)


def main(argv=None):
    ap = argparse.ArgumentParser(description="TartanGround -> Khronos ROS2 bag")
    ap.add_argument("--dataset-root", required=True, help="解压后的数据根目录")
    ap.add_argument("--env", default="Hospital")
    ap.add_argument("--version", default="diff")
    ap.add_argument("--traj", default="P1000")
    ap.add_argument("--camera", default="lcam_front")
    ap.add_argument("--start-frame", type=int, default=0)
    ap.add_argument("--num-frames", type=int, default=300)
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--depth-min", type=float, default=None, help="可选裁剪下限(m)，默认关闭由 Khronos 处理")
    ap.add_argument("--depth-max", type=float, default=None, help="可选裁剪上限(m)")
    ap.add_argument("--output", required=True, help="bag 输出目录")
    ap.add_argument("--audit-only", action="store_true", help="只审计数据，不写 bag")
    ap.add_argument("--overwrite", action="store_true", help="覆盖已存在的 bag 目录")
    ap.add_argument("--keep-raw-semantic", action="store_true", default=True, help="bag 保存 raw ID（Khronos remap 转 compact）")
    ap.add_argument("--write-debug-pointcloud", action="store_true", help="额外写 debug 点云（可选）")
    ap.add_argument("--world-frame-convention", default="ros_enu", choices=["ros_enu"])
    args = ap.parse_args(argv)

    dataset = TartanGroundDataset(args.dataset_root, camera=args.camera)

    n_img, n_depth, n_seg, n_pose = dataset.check_sync()
    print(f"data sync: image={n_img} depth={n_depth} seg={n_seg} pose={n_pose}")
    if not (n_img == n_depth == n_seg == n_pose):
        raise RuntimeError("RGB/Depth/Seg/Pose 数量不一致，禁止继续")

    if args.audit_only:
        print("audit-only: 数据同步校验通过")
        return 0

    out = Path(args.output)
    if out.exists():
        if not args.overwrite:
            raise FileExistsError(f"output exists (use --overwrite): {out}")
        import shutil
        shutil.rmtree(out)

    from rosbag2_py import ConverterOptions, StorageOptions, SequentialWriter
    storage = StorageOptions(uri=str(out), storage_id="sqlite3")
    converter = ConverterOptions(input_serialization_format="cdr",
                                 output_serialization_format="cdr")
    writer = SequentialWriter()
    writer.open(storage, converter)
    build_topics(writer)

    static_tf = build_static_tf()
    n_written, ratios = convert(args, dataset, writer, static_tf)
    writer.close()

    print(f"\nwrote {n_written} frames -> {out}")
    if ratios:
        print(f"valid depth ratio mean={np.mean(ratios):.3f} (min {np.min(ratios):.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
