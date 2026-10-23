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


def test_slow_names_running_pipeline_steps(tmp_path):
    import time
    run = tmp_path / "20261020_011700"
    run.mkdir()
    (run / "summary.log").write_text("x")
    (run / "10_rankings_finish.log").write_text("x")
    old = run / "04_grade_sanity.log"
    old.write_text("x")
    t = time.time()
    os.utime(old, (t - 3600, t - 3600))
    ctx = AE.serverContext(now=t, log_root=str(tmp_path))
    assert "10_rankings_finish running" in ctx and "04_grade_sanity" not in ctx
    assert "load" in ctx
    b = _Buf()
    AE.recordSlow("/race/tf/:m", 28600, [], buf=b)
    assert b.got == ["Slow request /race/tf/:m"]


def test_extension_and_in_app_browser_noise_is_dropped():
    b = _Buf()
    for msg in ("Unhandled rejection: Invalid call to runtime.sendMessage(). Tab not found.",
                "ReferenceError: Can't find variable: _AutofillCallbackHandler",
                "Unhandled rejection: Extension context invalidated."):
        assert AE.recordJs(msg, "(inline)", 1, "/", buf=b) is False, msg
    assert b.got == []
    assert AE.recordJs("TypeError: x is undefined", "/static/a.js", 3, "/", buf=b) is True
