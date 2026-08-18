"""Depth 解码测试（指南 §38 test_depth）。"""
import numpy as np
import pytest


def test_depth_decode_shape(dataset):
    depth = dataset.depth(0)
    assert depth.shape == (640, 640), f"shape {depth.shape}"


def test_depth_dtype(dataset):
    assert dataset.depth(0).dtype == np.float32


def test_depth_finite_ratio(dataset):
    d = dataset.depth(100)
    finite = np.isfinite(d)
    ratio = finite.mean()
    assert ratio > 0.3, f"finite ratio too low: {ratio:.3f}"
    assert np.isfinite(d[finite]).all()


def test_depth_median_positive(dataset):
    d = dataset.depth(100)
    finite = d[np.isfinite(d) & (d > 0)]
    assert finite.size > 0
    assert np.median(finite) > 0.0
