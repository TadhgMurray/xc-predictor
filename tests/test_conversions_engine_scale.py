"""On the engine's scale (issue 177) a conversion closes on itself: a time
in one context, to the neutral time, back to the same context is the
same time; a rating to a 3200 and back is the same rating; and the
engine_scale table is what the page reads. The database client is
stubbed; the distance pickles on disk do the factors.

    python -m pytest -q tests/test_conversions_engine_scale.py
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.modules.setdefault("database", types.SimpleNamespace(getConn=None))
import conversions as cv                                        # noqa: E402


def _stub():
    cv._scale["map"] = {("hs_m", "XC"): (1247.6, -0.025, -0.0253),
                        ("hs_m", "TF"): (1247.6, -0.055, -0.0253)}
    cv._scale["at"] = 1e18                           # never reload
    cv._offsets["map"] = {("hs_m", "TF", 3200, 1): 0.014,
                          ("hs_m", "TF", 3200, 2): 0.010,
                          ("hs_m", "TF", 800, 1): 0.0}
    cv._offsets["at"] = 1e18


def test_a_3200_converts_to_itself():
    _stub()
    ctx = {"distance": 3200.0, "pool": "hs_m", "sport": "TF"}
    norm = cv._norm_from_time(541.1, 3200.0, "hs_m", sport="TF", chosen=None)
    back = cv.normalized_to_time(norm, ctx)
    assert abs(back - 541.1) < 0.05, back
    # and with a chosen venue on both sides
    norm2 = cv._norm_from_time(541.1, 3200.0, "hs_m", sport="TF", chosen=-0.041)
    back2 = cv.normalized_to_time(norm2, dict(ctx, difficulty=-0.041))
    assert abs(back2 - 541.1) < 0.05, back2
    # a fast venue makes the same time worth less than a typical one
    assert norm2 > norm


def test_a_rating_round_trips_through_a_time():
    _stub()
    norm = cv._norm_from_rating(132.8, "hs_m", sport="TF")
    assert abs(cv.normalized_to_rating(norm, "hs_m", sport="TF") - 132.8) < 1e-9
    t = cv.normalized_to_time(norm, {"distance": 3200.0, "pool": "hs_m",
                                     "sport": "TF"})
    n2 = cv._norm_from_time(t, 3200.0, "hs_m", sport="TF", chosen=None)
    assert abs(cv.normalized_to_rating(n2, "hs_m", sport="TF") - 132.8) < 0.05


def test_tilt_matches_the_solver():
    import re, io
    js = io.open(os.path.join(ROOT, "engine", "joint_solve.py"), encoding="utf-8").read()
    k = float(re.search(r"^TILT_K = (-?[0-9.]+)", js, re.M).group(1))
    assert k == cv._TILT_K
