"""Where the average outdoor track sits on the cross country course scale
(owner, 2026-10-28: Portage West MS "+8.4% slower than an average outdoor
track", while the runners' own spring track times put the stored XC zero
4-6% too fast for high school -- scripts/diag_xc_track_gap.py).

conversions.xcTrackZero measures it in seconds from the model's own two
scales; difficulty_view reads a cross country % against it, the rating
breakdown starts there, the course page's bars follow. Nothing stored and
no rating moves. The database client is stubbed; the distance pickles on
disk do the factors.

    python -m pytest -q tests/test_xc_track_zero.py
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: F401,E402  -- sets XCP_DB_PASSWORD, must precede config
for _p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, _p))
import conversions as cv                                        # noqa: E402
import course_history as ch                                     # noqa: E402
import difficulty_view as dv                                    # noqa: E402
import explain_rating as ex                                     # noqa: E402

POOLS = (("hs_m", 1247.6), ("hs_f", 1390.0), ("college_m", 1180.0),
         ("college_f", 1330.0))


def _stub(rows=None, gain=None):
    # the server's shape: one anchor shift for both sports, XC's median
    # race well above it
    cv._scale["map"] = {(p, s): (pm, -0.0048 if s == "XC" else -0.06, -0.0583)
                        for p, pm in POOLS for s in ("XC", "TF")}
    cv._scale["rows"] = dict(rows or {})
    cv._scale["at"] = 1e18                           # never reload
    cv._offsets["map"], cv._offsets["at"] = {}, 1e18
    cv._gain["map"], cv._gain["at"] = dict(gain or {}), 1e18
    cv._clock_tables.clear()
    cv._xc_zero["at"] = None


def _xc0(pool, d, r=100.0):
    norm = cv._norm_from_rating(r, pool, 0.0, "XC")
    return cv.normalized_to_time(norm, {"distance": d, "pool": pool,
                                        "sport": "XC", "difficulty": 0.0})


def test_the_zero_puts_a_100_on_the_track_events_time():
    # a winter gain on high school track, as the server writes
    _stub(gain={("hs_m", "TF"): [(100.0, 0.008)], ("hs_f", "TF"): [(100.0, 0.008)]})
    for pool, _pm in POOLS:
        d = cv.XC_TRACK_M[pool.split("_")[0]]
        z = cv.xcTrackZero(pool)
        tf = cv.ratingSeconds(100.0, pool, "TF", d)
        xc = cv.ratingSeconds(100.0, pool, "XC", d, track=True)
        assert abs(xc - tf) < 0.05, (pool, xc, tf)
        # and it is exactly the distance between the two zeros, in seconds
        assert abs(z - math.log(tf / _xc0(pool, d))) < 1e-9, pool
    # the gain is a track-only term: it moves the track, so it moves z
    assert cv.xcTrackZero("hs_m") > 0.0


def test_the_course_pages_number_is_the_pools_weighted_by_xc_rows():
    rows = {("hs_m", "XC"): 300, ("hs_f", "XC"): 100, ("college_m", "TF"): 999}
    _stub(rows=rows, gain={("hs_m", "TF"): [(100.0, 0.02)]})
    want = (300 * cv.xcTrackZero("hs_m") + 100 * cv.xcTrackZero("hs_f")) / 400
    assert abs(cv.xcTrackZero() - want) < 1e-12
    assert cv.trackDifficulty("hs_m", "TF") == 0.0
    assert abs(cv.trackDifficulty("hs_m", "XC") - math.expm1(cv.xcTrackZero("hs_m"))) < 1e-12


def test_no_scale_is_the_old_reading():
    cv._scale["map"], cv._scale["rows"], cv._scale["at"] = {}, {}, 2e18
    cv._xc_zero["at"] = None
    assert cv.xcTrackZero() == 0.0 and cv.xcTrackZero("hs_m") == 0.0
    assert cv.trackDifficulty("hs_m", "XC") == 0.0


def test_a_named_course_converts_exactly_as_before():
    """The zero is display: a time on a named XC course still carries
    tilt * (log1p(d) + shift), so a rating there is what it was."""
    _stub(gain={("hs_m", "TF"): [(100.0, 0.02)]})
    ctx = {"distance": 5000.0, "pool": "hs_m", "sport": "XC", "difficulty": 0.084}
    norm = cv._norm_from_rating(147.2, "hs_m", 0.0, "XC")
    t = cv.normalized_to_time(norm, ctx)
    want = norm * math.exp(cv._tilt(147.2, "hs_m") * (math.log1p(0.084) - 0.0583)) \
        / cv._forward_factor(5000.0, "hs_m", None, None, None, "XC", None)
    assert abs(t - want) < 1e-6


def _pin_zero(z):
    dv._zero["XC"], dv._zero["at"] = z, float("inf")


def test_a_cross_country_percent_reads_against_the_track():
    _pin_zero(math.log(1.04))
    try:
        # Portage: +8.4% stored, the track 4% up the XC scale
        assert abs(dv.relativePct(0.084, "XC") - 100 * (1.084 / 1.04 - 1)) < 1e-9
        assert dv.diffPct(0.084, "XC") == "+4.2%"
        assert dv.diffPct(0.04, "XC") == "0.0%"           # runs as a track does
        # track is untouched, and so is the course word (median-relative)
        assert dv.diffPct(0.04, "TF") == "+4.0%"
        assert dv.sportMeanLog("TF") == dv.sportMeanLog(None) == 0.0
        cuts = dv.cutoffsFrom([("XC", 0.01 * i) for i in range(21)])   # median 0.10
        pct = 100 * math.expm1(math.log1p(0.084) - 0.10)
        assert dv.courseWord(0.084, "XC", cuts) == ("Typical", f"{pct:+.1f}%")
    finally:
        _pin_zero(0.0)


def test_the_course_history_bars_follow_the_printed_percent():
    rows = [{"race_date": "2025-10-04", "day_effect": 0.0, "n_rows": 50,
             "course_effect": 0.03},
            {"race_date": "2024-10-05", "day_effect": 0.0, "n_rows": 50,
             "course_effect": 0.084}]
    out = ch.summarize(rows, None, 5000, None, zero=math.log(1.04))
    s = {x["season"]: x for x in out["seasons"]}
    # the stored together is kept for diffpct; the bar is what it prints
    assert abs(s[2025]["together"] - 0.03) < 1e-12
    assert abs(s[2025]["bar"] - (1.03 / 1.04 - 1)) < 1e-12 and s[2025]["bar"] < 0
    assert abs(s[2024]["bar"] - (1.084 / 1.04 - 1)) < 1e-12
    # no zero given: the bar is the together, as before
    out0 = ch.summarize(rows, None, 5000, None)
    assert all(abs(x["bar"] - x["together"]) < 1e-12 for x in out0["seasons"])


def _xc_terms(**kw):
    t = dict(sport="XC", time=863.3, distance_m=5000.0, ref_distance=5000.0,
             pool="hs_m", rating=147.2, pm=1247.6, f_dist=1.0, era=0.0,
             w_stored=0.0, w_live=0.0, w_detail={}, difficulty=0.084,
             course_source="era", venue="Portage West MS", tilt=0.854,
             shift=-0.0583, day_u=0.0, day_n=200, day_mode="all")
    t.update(kw)
    return t


def test_the_breakdown_starts_on_a_track_and_still_lands():
    z = math.log(1.04)
    old = ex.buildChain(_xc_terms())
    new = ex.buildChain(_xc_terms(track_zero=z))
    c_old = next(s for s in old["steps"] if s["key"] == "course")
    c_new = next(s for s in new["steps"] if s["key"] == "course")
    # the course step reads against the track the course page names
    assert "4.2% slower than an average outdoor track" in c_new["text"]
    assert "8.4% slower" in c_old["text"]
    assert abs(c_new["pct"] - 100 * math.expm1(0.854 * math.log(1.084 / 1.04))) < 0.01
    # the start moved by exactly what the course step gave back
    assert abs(math.log(new["start_rating"] / old["start_rating"]) - 0.854 * z) < 1e-3
    # both land on the stored rating
    for body in (old, new):
        run = body["start_rating"] + sum(s["pts"] for s in body["steps"] if s.get("pts"))
        assert abs(run - 147.2) < 0.02, run


def test_no_course_step_no_zero_in_the_start():
    """A hand-corrected distance carries no course term: the start stays
    where it was, or 'Other' would take the track's place for it."""
    z = math.log(1.04)
    a = ex.buildChain(_xc_terms(course_applied=False))
    b = ex.buildChain(_xc_terms(course_applied=False, track_zero=z))
    assert a["start_rating"] == b["start_rating"]
