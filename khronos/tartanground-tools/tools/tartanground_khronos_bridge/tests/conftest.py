import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

DATASET_ROOT = os.environ.get(
    "TARTANGROUND_ROOT",
    "/home/ulmmt/Masterthesis-wang/tartanground_khronos/dataset",
)
CONFIG_DIR = os.environ.get(
    "TARTANGROUND_CONFIG",
    "/home/ulmmt/Masterthesis-wang/tartanground_khronos/tools/tartanground_khronos_bridge/config",
)
BAG_DIR = os.environ.get(
    "TARTANGROUND_BAG",
    "/home/ulmmt/Masterthesis-wang/tartanground_khronos/bags/hospital_p2000_smoke",
)


@pytest.fixture(scope="module")
def dataset():
    from tartanground_khronos_bridge.dataset_reader import TartanGroundDataset
    return TartanGroundDataset(DATASET_ROOT)


@pytest.fixture(scope="module")
def seg_label_map():
    from tartanground_khronos_bridge.semantic_utils import load_seg_label_map
    import os.path
    p = os.path.join(DATASET_ROOT, "seg_label_map.json")
    if not os.path.isfile(p):
        pytest.skip(f"seg_label_map.json missing: {p}")
    return load_seg_label_map(p)
