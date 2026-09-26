# -*- coding: utf-8 -*-
"""单元测试。注意：scikit-learn 仅在此处使用，不随产品分发。"""
from sklearn.metrics import accuracy_score

from app import build_frame


def test_build_frame():
    df = build_frame([{"a": 1}, {"a": 2}])
    assert len(df) == 2


def test_accuracy():
    assert accuracy_score([1, 0], [1, 0]) == 1.0
