"""The college 5K column is calibrated by rating level to what runners at
that level ran on the track (owner, 2026-10-31: "the collegiate 5k
underrating is insane"); high school, and a track rating's own 5K, are
untouched.

    python -m pytest -q tests/test_college_women_5k_cal.py
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))

import conversions as C                                          # noqa: E402


def test_the_correction_moves_with_the_rating():
    assert C.xcTrackCal("college_m", 125.5) == -0.020          # the top: faster
    assert C.xcTrackCal("college_m", 140.0) == -0.020          # held beyond the end
    assert C.xcTrackCal("college_m", 100.0) == 0.016           # deep: slower
    assert abs(C.xcTrackCal("college_m", 116.2) - 0.0035) < 1e-9   # between points
    assert C.xcTrackCal("hs_m", 140.0) == 0.0 and C.xcTrackCal("college_f", None) == 0.0


def test_only_college_xc_moves(monkeypatch):
    monkeypatch.setattr(C, "ratingSeconds", lambda r, pool, sport="XC", d=None, track=False: 800.0)
    monkeypatch.setattr(C, "readerDistance", lambda pool, sport: 5000.0)
    top_m = C.ratingClock(126.0, "college_m", "XC")
    hs = C.ratingClock(126.0, "hs_m", "XC")
    tf = C.ratingClock(126.0, "college_m", "TF", 5000.0)
    assert hs["time"] == tf["time"] == "13:20"
    assert top_m["time"] == "13:04"                             # 800 * exp(-0.020) = 784.2 s
