"""数据同步测试（指南 §38 test_sync）。"""
def test_sync_counts_equal(dataset):
    n_img, n_depth, n_seg, n_pose = dataset.check_sync()
    assert n_img == n_depth == n_seg == n_pose, \
        f"RGB/D/S/P 不一致: {n_img}/{n_depth}/{n_seg}/{n_pose}"
