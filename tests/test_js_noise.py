"""ResizeObserver's "loop completed" is browser noise, not a site error
(owner's error mail, 2026-10-18): recordJs drops it, keeps real errors.

    python -m pytest -q tests/test_js_noise.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))

import app_errors as AE  # noqa: E402


class _Buf:
    def __init__(self):
        self.got = []

    def add(self, key, kind, title, path, body):
        self.got.append(title)
        return True


def test_resize_observer_dropped():
    b = _Buf()
    msg = "ResizeObserver loop completed with undelivered notifications."
    assert AE.recordJs(msg, "(inline)", 0, "/report", buf=b) is False
    assert AE.recordJs("ResizeObserver loop limit exceeded", "(inline)", 0, "/", buf=b) is False
    assert b.got == []


def test_real_error_kept():
    b = _Buf()
    AE.recordJs("TypeError: x is undefined", "/static/app.js", 12, "/", buf=b)
    assert len(b.got) == 1
