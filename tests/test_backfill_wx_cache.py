"""The backfill's weather-grid aggregate is reused only while its key holds.

★ 2026-09-26: _loadWxDictCached pickles the aggregate per sport, keyed by
  the query text (race window + temperature aggregate) and weather_grid's
  write counters. A changed counter, window or aggregate must rebuild; no
  signal (track_counts off) must never cache. No database: the signal and
  the aggregate are stubbed.

    python -m pytest -q tests/test_backfill_wx_cache.py
"""
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("backfill", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
if "corrections" not in sys.modules:            # 165 MB, not in git
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: {}
    sys.modules["corrections"] = _c

import backfill_normalize as B                                  # noqa: E402

_AVG = "avg(apparent_temperature)"


def _setup(monkeypatch, tmp_path, signal):
    monkeypatch.setattr(B, "_WX_CACHE", str(tmp_path / "wx_{sport}.pkl"))
    state = {"sig": signal, "built": 0, "grid": {(30.0, 200.0, "2024-01-01"):
                                                 (50.0, 1.0, 0.0, 0.2, 0.0)}}
    monkeypatch.setattr(B, "_wxGridSignal", lambda cur: state["sig"])

    def load(cur, hours, agg):
        state["built"] += 1
        return dict(state["grid"])
    monkeypatch.setattr(B, "_loadWxDict", load)
    return state


def test_reused_until_the_grid_moves(monkeypatch, tmp_path):
    st = _setup(monkeypatch, tmp_path, (1, 2, 100, 0, 0, None, "t0"))
    a = B._loadWxDictCached(None, "XC", (8, 12), _AVG)
    b = B._loadWxDictCached(None, "XC", (8, 12), _AVG)
    assert a == b and st["built"] == 1
    st["sig"] = (1, 2, 100, 1, 0, None, "t0")      # one UPDATE
    st["grid"][(30.0, 200.0, "2024-01-01")] = (51.0, 1.0, 0.0, 0.2, 0.0)
    c = B._loadWxDictCached(None, "XC", (8, 12), _AVG)
    assert st["built"] == 2 and c == st["grid"]


def test_the_window_and_the_aggregate_are_part_of_the_key(monkeypatch, tmp_path):
    st = _setup(monkeypatch, tmp_path, (1, 2, 100, 0, 0, None, "t0"))
    B._loadWxDictCached(None, "XC", (8, 12), _AVG)
    B._loadWxDictCached(None, "XC", (8, 13), _AVG)
    B._loadWxDictCached(None, "XC", (8, 13), "max(apparent_temperature)")
    assert st["built"] == 3
    B._loadWxDictCached(None, "TF", (8, 13), "max(apparent_temperature)")
    assert st["built"] == 4                         # one file per sport


def test_no_signal_never_caches(monkeypatch, tmp_path):
    st = _setup(monkeypatch, tmp_path, None)
    B._loadWxDictCached(None, "XC", (8, 12), _AVG)
    B._loadWxDictCached(None, "XC", (8, 12), _AVG)
    assert st["built"] == 2
    assert not os.path.exists(tmp_path / "wx_XC.pkl")


def test_a_broken_cache_file_is_rebuilt_not_fatal(monkeypatch, tmp_path):
    st = _setup(monkeypatch, tmp_path, (1, 2, 100, 0, 0, None, "t0"))
    (tmp_path / "wx_XC.pkl").write_bytes(b"not a pickle")
    assert B._loadWxDictCached(None, "XC", (8, 12), _AVG) == st["grid"]
    assert B._loadWxDictCached(None, "XC", (8, 12), _AVG) == st["grid"]
    assert st["built"] == 1
