"""The college women's 5K column is calibrated to the same runners' spring
5000s (owner, 2026-10-29); every other pool, and a track rating's own 5K,
are untouched.

    python -m pytest -q tests/test_college_women_5k_cal.py
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))

import conversions as C                                          # noqa: E402


def test_only_college_women_xc_moves(monkeypatch):
    monkeypatch.setattr(C, "ratingSeconds", lambda r, pool, sport="XC", d=None, track=False: 1000.0)
    monkeypatch.setattr(C, "readerDistance", lambda pool, sport: 5000.0)
    f = C.ratingClock(120.0, "college_f", "XC")
    m = C.ratingClock(120.0, "college_m", "XC")
    h = C.ratingClock(120.0, "hs_f", "XC")
    tf = C.ratingClock(120.0, "college_f", "TF", 5000.0)
    assert m["time"] == h["time"] == tf["time"] == "16:40"
    want = 1000.0 * math.exp(C.XC_TRACK_CAL["college_f"])          # 1026.3 s
    assert f["time"] == "17:06" and abs(want - 1026.3) < 0.1
    assert set(C.XC_TRACK_CAL) == {"college_f"}
