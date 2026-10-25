"""CameraInfo 构造测试（指南 §38 test_camera_info）。"""
from tartanground_khronos_bridge.convert_to_bag import make_camera_info
from builtin_interfaces.msg import Time


def _info():
    return make_camera_info(Time(sec=1, nanosec=0), "camera_optical_frame",
                            [320.0, 0, 320.0, 0, 320.0, 320.0, 0, 0, 1])


def test_intrinsics():
    ci = _info()
    assert ci.k[0] == 320.0 and ci.k[4] == 320.0
    assert ci.k[2] == 320.0 and ci.k[5] == 320.0


def test_dims():
    ci = _info()
    assert ci.width == 640 and ci.height == 640


def test_distortion_zero():
    ci = _info()
    assert ci.distortion_model == "plumb_bob"
    assert all(v == 0.0 for v in ci.d)


def test_frame_id():
    assert _info().header.frame_id == "camera_optical_frame"
