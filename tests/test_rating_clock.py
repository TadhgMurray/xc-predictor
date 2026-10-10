"""The time next to the rating (owner, 2026-10-10: "124.6 means nothing to
parents"). conversions.ratingClock says a rating as a clock at the distance
a reader of the pool expects; the boards read it off a per-pool table, which
must agree with the direct conversion; the athlete header names a track
athlete's own main event. The database client is stubbed; the distance
pickles on disk do the factors.

    python -m pytest -q tests/test_rating_clock.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: F401,E402  -- sets XCP_DB_PASSWORD, must precede config
for _p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, _p))
import conversions as cv                                        # noqa: E402


def _stub():
    cv._scale["map"] = {(p, s): (pm, -0.025, -0.0253)
                        for p, pm in (("hs_m", 1247.6), ("college_m", 1180.0),
                                      ("college_f", 1330.0), ("hs_f", 1390.0))
                        for s in ("XC", "TF")}
    cv._scale["at"] = 1e18                           # never reload
    cv._offsets["map"], cv._offsets["at"] = {}, 1e18
    cv._gain["map"], cv._gain["at"] = {}, 1e18
    cv._clock_tables.clear()


def _direct(r, pool, sport, d):
    norm = cv._norm_from_rating(r, pool, 0.0, sport)
    return cv.normalized_to_time(norm, {"distance": d, "pool": pool, "sport": sport})


def test_reader_distances():
    assert cv.readerDistance("hs_m", "XC") == 5000.0
    assert cv.readerDistance("hs_f|XC", "XC") == 5000.0
    assert cv.readerDistance("college_m", "XC") == 8000.0
    assert cv.readerDistance("college_f", "XC") == 6000.0
    assert cv.readerDistance("ms_f", "XC") == 3200.0
    assert cv.readerDistance("hs_m", "TF") == 1600.0
    assert cv.readerDistance("college_f", "TF") == 1500.0
    # no custom to follow: no sentence
    assert cv.readerDistance("pro_m", "XC") is None
    assert cv.readerDistance("hs_x", "XC") is None
    assert cv.readerDistance(None, "XC") is None


def test_distance_words():
    assert cv.distanceWords(5000) == "5K"
    assert cv.distanceWords(8000.0) == "8K"
    assert cv.distanceWords(1600) == "1600m"
    assert cv.distanceWords(3200) == "3200m"
    assert cv.distanceWords(1609.34) == "mile"
    assert cv.distanceWords(3218.69) == "2 mile"
    assert cv.distanceWords(None) is None


def test_the_table_agrees_with_the_direct_conversion():
    _stub()
    for pool, sport in (("hs_m", "XC"), ("college_m", "XC"), ("hs_m", "TF")):
        d = cv.readerDistance(pool, sport)
        for r in (88.0, 100.3, 112.75, 124.6, 139.95, 150.5):
            got = cv.ratingSeconds(r, pool, sport)
            want = _direct(r, pool, sport, d)
            assert abs(got - want) < 0.5, (pool, sport, r, got, want)
    # off the table's ends it converts directly rather than refusing
    assert abs(cv.ratingSeconds(30.0, "hs_m") - _direct(30.0, "hs_m", "XC", 5000.0)) < 1e-6
    assert cv.ratingSeconds(None, "hs_m") is None
    assert cv.ratingSeconds(0, "hs_m") is None
    assert cv.ratingSeconds(120, "pro_m") is None


def test_a_higher_rating_is_a_faster_time():
    _stub()
    ts = [cv.ratingSeconds(r, "hs_m", "XC") for r in (95, 105, 115, 125, 135)]
    assert ts == sorted(ts, reverse=True)


def test_the_phrase():
    _stub()
    c = cv.ratingClock(124.6, "hs_m", "XC")
    secs = int(round(_direct(124.6, "hs_m", "XC", 5000.0)))
    assert c["text"] == f"≈ {secs // 60}:{secs % 60:02d} 5K on a typical course"
    t = cv.ratingClock(124.6, "hs_m", "TF", 3200.0)
    assert t["dist"] == "3200m" and t["where"] == "on a typical track"
    assert cv.ratingClock(124.6, "college_m", "XC")["dist"] == "8K"
    assert cv.ratingClock(None, "hs_m", "XC") is None


def test_board_rows_get_a_title():
    _stub()
    rows = [{"pool": "hs_m", "sport": "XC", "rating": 124.6},
            {"pool": "hs_m", "sport": "TF", "rating": 124.6, "distance": 3200.0},
            {"pool": "hs_m", "sport": "TF", "rating": 124.6, "distance": 400.0},
            {"pool": "pro_m", "sport": "XC", "rating": 150.0},
            {"pool": "hs_m", "sport": "XC", "rating": None}]
    cv.stampBoardClocks(rows)
    assert rows[0]["rating_clock"].endswith("5K on a typical course")
    # one track race says itself at its own distance; a 400 does not
    assert rows[1]["rating_clock"].endswith("3200m on a typical track")
    assert rows[2]["rating_clock"].endswith("1600m on a typical track")
    assert rows[3]["rating_clock"] is None and rows[4]["rating_clock"] is None


def test_main_track_distance():
    os.environ.setdefault("XCP_DB_QUIET", "1")
    import app
    R = lambda ev, lab="2026", sp="TF", r=110.0: {"sport": sp, "season_label": lab,
                                                  "event": ev, "speed_rating": r}
    races = [R("1600m"), R("3200m", r=120.0), R("3200m"), R("1600m"),
             R("400m"), R("400m"), R("400m"), R("4x800m"), R("3000m SC"),
             R("1600m", lab="2025"), R("5000", sp="XC")]
    # two 1600s, two 3200s (sprints, relays, steeple, other seasons and
    # sports do not count): a tie goes to the event rated highest
    assert app._mainTrackDistance(races, 2026) == 3200.0
    races.append(R("1600m"))
    assert app._mainTrackDistance(races, 2026) == 1600.0
    assert abs(app._mainTrackDistance([R("1 Mile")], 2026) - 1609.344) < 0.01
    # sprints only, or another season only: no event of its own
    assert app._mainTrackDistance([R("400m"), R("200m")], 2026) is None
    assert app._mainTrackDistance(races, 2024) is None
