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


# ---------------------------------------------------------------------- #
# issue #21 (2026-09-11): the round trip closes at every distance, with an
# incomplete offset table, and at a rating that straddles a band edge --
# the exact case that used to come back -3.34% on a 3200.
# ---------------------------------------------------------------------- #

def _stub_straddle():
    cv._scale["map"] = {("hs_m", "XC"): (1247.6, -0.025, -0.0253),
                        ("hs_m", "TF"): (1247.6, -0.055, -0.0253)}
    cv._scale["at"] = 1e18
    # band 0 and band 3 absent, bands 1/2 far apart: the old hard step
    cv._offsets["map"] = {("hs_m", "TF", 3200, 1): 0.014,
                          ("hs_m", "TF", 3200, 2): -0.020,
                          ("hs_m", "TF", 800, 2): 0.030,
                          ("hs_m", "TF", 1600, 1): 0.0,
                          ("hs_m", "TF", 5000, 1): -0.012,
                          ("hs_m", "TF", 5000, 3): 0.006,
                          ("hs_m", "TF", 10000, 2): -0.030}
    cv._offsets["at"] = 1e18


def test_the_offset_is_continuous_with_missing_bands():
    _stub_straddle()
    # a missing band borrows its nearest neighbour; no step anywhere
    prev = None
    for r in [x / 4.0 for x in range(280, 640)]:
        v = cv.distance_offset("hs_m", "TF", 3200, rating=r)
        if prev is not None:
            assert abs(v - prev) < 0.001, (r, v, prev)
        prev = v
    assert cv.distance_offset("hs_m", "TF", 3200, rating=80.0) == 0.014
    assert cv.distance_offset("hs_m", "TF", 3200, rating=150.0) == -0.020
    assert cv.distance_offset("hs_m", "TF", 800, rating=100.0) == 0.030


def test_round_trip_closes_at_every_distance_and_a_straddling_rating():
    _stub_straddle()
    for dist, t in ((800.0, 118.0), (1600.0, 262.0), (3200.0, 598.0),
                    (3200.0, 541.1), (5000.0, 930.0), (10000.0, 1900.0)):
        ctx = {"distance": dist, "pool": "hs_m", "sport": "TF"}
        for chosen in (None, -0.041, 0.03):
            norm = cv._norm_from_time(t, dist, "hs_m", sport="TF", chosen=chosen)
            c2 = dict(ctx) if chosen is None else dict(ctx, difficulty=chosen)
            back = cv.normalized_to_time(norm, c2)
            # normalizeTime rounds to 0.01 s on the forward leg, which is
            # up to 6e-6 relative -- 0.003 s on a 3200; the model closes exactly
            assert abs(back - t) < 0.01, (dist, t, chosen, back)
            # and the rating the page reports is the one the effect was
            # evaluated at: a second forward pass from `back` agrees
            n2 = cv._norm_from_time(back, dist, "hs_m", sport="TF", chosen=chosen)
            assert abs(n2 - norm) < 1e-6, (dist, t, chosen)


def test_a_rating_converts_to_a_time_and_back_across_events():
    _stub_straddle()
    for r in (95.0, 104.9, 105.1, 119.9, 120.1, 134.9, 135.1, 150.0):
        norm = cv._norm_from_rating(r, "hs_m", sport="TF")
        for dist in (800.0, 1600.0, 3200.0, 5000.0, 10000.0):
            t = cv.normalized_to_time(norm, {"distance": dist, "pool": "hs_m",
                                             "sport": "TF"})
            n2 = cv._norm_from_time(t, dist, "hs_m", sport="TF", chosen=None)
            assert abs(cv.normalized_to_rating(n2, "hs_m", sport="TF") - r) < 0.01, (r, dist)


def test_the_bands_and_anchors_match_the_solver():
    import re, io
    js = io.open(os.path.join(ROOT, "engine", "joint_solve.py"), encoding="utf-8").read()
    bands = re.search(r"^DIST_BANDS = \(([^)]*)\)", js, re.M).group(1)
    anchors = re.search(r"^DIST_BAND_ANCHORS = \(([^)]*)\)", js, re.M).group(1)
    assert tuple(float(v) for v in bands.split(",") if v.strip()) == cv._DIST_BANDS
    assert tuple(float(v) for v in anchors.split(",") if v.strip()) == cv._BAND_ANCHORS


def test_the_tilt_rails_match_the_solver():
    import re, io
    js = io.open(os.path.join(ROOT, "engine", "joint_solve.py"), encoding="utf-8").read()
    lo = float(re.search(r"^TILT_RATING_LO = ([0-9.]+)", js, re.M).group(1))
    hi = float(re.search(r"^TILT_RATING_HI = ([0-9.]+)", js, re.M).group(1))
    assert (lo, hi) == (cv._TILT_LO, cv._TILT_HI)
    # the line runs on past 140 (2026-09-11): a 160 is charged less than a 140
    assert cv._tilt(160.0) < cv._tilt(140.0) < cv._tilt(100.0) == 1.0


def test_an_unnamed_track_is_the_zero_not_the_median_row():
    """A track conversion with no venue uses difficulty 0.0 (the average
    outdoor track, the display zero), not the median applied effect over
    track rows; an XC conversion with no venue still uses its median."""
    _stub()
    sc = cv.engineScale("hs_m", "TF")
    assert sc[1] != 0.0 and sc[2] != 0.0            # the stub tells them apart
    e_none = cv.venueEffect("hs_m", "TF", 120.0, None, 3200.0)
    e_zero = cv.venueEffect("hs_m", "TF", 120.0, 0.0, 3200.0)
    assert abs(e_none - e_zero) < 1e-12
    # and the time it produces is the 0.0% track's time
    norm = cv._norm_from_rating(130.0, "hs_m", sport="TF")
    t_none = cv.normalized_to_time(norm, {"distance": 3200.0, "pool": "hs_m",
                                          "sport": "TF"})
    t_zero = cv.normalized_to_time(norm, {"distance": 3200.0, "pool": "hs_m",
                                          "sport": "TF", "difficulty": 0.0})
    assert abs(t_none - t_zero) < 1e-9
    xc_none = cv.venueEffect("hs_m", "XC", 120.0, None, 5000.0)
    xc_sc = cv.engineScale("hs_m", "XC")
    assert abs(xc_none - (xc_sc[1] + cv.distance_offset("hs_m", "XC", 5000.0, rating=120.0)
                          + cv.sport_gain("hs_m", "XC", 120.0))) < 1e-12
