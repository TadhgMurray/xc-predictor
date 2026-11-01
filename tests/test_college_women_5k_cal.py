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
    """2026-11-01: slower through the body of both college pools (the
    stopwatch, diag_hs_to_college part E), the 2026-10-31 top kept."""
    assert C.xcTrackCal("college_m", 125.5) == -0.020          # the top: faster
    assert C.xcTrackCal("college_m", 140.0) == -0.020          # held beyond the end
    assert C.xcTrackCal("college_m", 101.9) == 0.0082          # the body: slower
    assert C.xcTrackCal("college_m", 80.0) == 0.0096           # held below the start
    assert abs(C.xcTrackCal("college_m", 118.8) - (0.0157 - 0.0357 / 2)) < 1e-9   # between
    assert C.xcTrackCal("college_f", 133.5) == 0.0 and C.xcTrackCal("college_f", 145.0) == 0.0
    assert C.xcTrackCal("college_f", 107.8) == 0.0117
    assert C.xcTrackCal("hs_m", 140.0) == 0.0 and C.xcTrackCal("hs_f", 100.0) == 0.0


def test_only_college_xc_moves(monkeypatch):
    monkeypatch.setattr(C, "ratingSeconds", lambda r, pool, sport="XC", d=None, track=False: 800.0)
    monkeypatch.setattr(C, "readerDistance", lambda pool, sport: 5000.0)
    top_m = C.ratingClock(126.0, "college_m", "XC")
    hs = C.ratingClock(126.0, "hs_m", "XC")
    tf = C.ratingClock(126.0, "college_m", "TF", 5000.0)
    assert hs["time"] == tf["time"] == "13:20"
    assert top_m["time"] == "13:04"                             # 800 * exp(-0.020) = 784.2 s
