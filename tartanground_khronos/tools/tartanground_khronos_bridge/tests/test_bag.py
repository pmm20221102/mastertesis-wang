"""Bag 静态审计测试（指南 §38 test_bag）：topic/type/count 正确。"""
import os

import pytest

from tartanground_khronos_bridge.audit_bag import audit_bag

BAG_DIR = os.environ.get(
    "TARTANGROUND_BAG",
    "/home/ulmmt/Masterthesis-wang/tartanground_khronos/bags/hospital_p2000_smoke",
)

REQUIRED = {
    "/tartanground/front/rgb/image_raw": "sensor_msgs/msg/Image",
    "/tartanground/front/rgb/camera_info": "sensor_msgs/msg/CameraInfo",
    "/tartanground/front/depth/image_raw": "sensor_msgs/msg/Image",
    "/tartanground/front/semantic/image_raw": "sensor_msgs/msg/Image",
    "/tf": "tf2_msgs/msg/TFMessage",
    "/tf_static": "tf2_msgs/msg/TFMessage",
}


@pytest.fixture(scope="module")
def bag():
    if not os.path.isdir(BAG_DIR):
        pytest.skip(f"bag not built yet: {BAG_DIR}")
    return audit_bag(BAG_DIR)


def test_topics_and_types(bag):
    for topic, typ in REQUIRED.items():
        assert topic in bag, f"missing topic {topic}"
        assert bag[topic]["type"] == typ, f"{topic} type wrong"


def test_counts_equal(bag):
    counts = {bag[t]["count"] for t in REQUIRED if t not in ("/tf", "/tf_static")}
    assert len(counts) == 1, f"RGB/D/S/CameraInfo 数量应一致: {counts}"
    assert bag["/tf"]["count"] >= 1
    assert bag["/tf_static"]["count"] >= 1
