"""The 5K column (owner, 2026-10-10: "add [a 5K column] to all pages with
ratings"). Beside every rating, the TRACK 5K it is worth -- 3200 m in middle
school -- for a cross country and a track rating alike, off the per-pool
clock tables; one clock for both scale views. conversions.fiveK /
fiveKLabel / stampFiveK, the _scale.html macros, and the athlete bests'
average. The database client is stubbed, as in test_rating_clock.

    python -m pytest -q tests/test_five_k_column.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: F401,E402  -- sets XCP_DB_PASSWORD, must precede config
for _p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, _p))
import conversions as cv                                        # noqa: E402

POOLS = (("hs_m", 1247.6), ("hs_f", 1390.0), ("college_m", 1180.0),
         ("college_f", 1330.0), ("ms_m", 1420.0), ("ms_f", 1560.0))


def _stub():
    cv._scale["map"] = {(p, s): (pm, -0.025, -0.0253)
                        for p, pm in POOLS for s in ("XC", "TF")}
    cv._scale["at"] = 1e18                           # never reload
    cv._offsets["map"], cv._offsets["at"] = {}, 1e18
    cv._gain["map"], cv._gain["at"] = {}, 1e18
    cv._clock_tables.clear()


def test_a_cross_country_rating_is_ratingClocks_track_5k():
    _stub()
    c = cv.fiveK(124.6, "hs_m", "XC")
    assert c == {"time": cv.ratingClock(124.6, "hs_m", "XC")["time"], "dist": "5K"}
    # the bare and the sport-suffixed pool are one pool
    assert cv.fiveK(124.6, "hs_m|XC", "XC") == c


def test_a_track_rating_is_read_at_5000_m_not_its_own_event():
    _stub()
    c = cv.fiveK(124.6, "hs_m", "TF")
    assert c["dist"] == "5K"
    assert c["time"] == cv.ratingClock(124.6, "hs_m", "TF", 5000.0)["time"]
    # ! the same reference both sports: one column down a mixed page
    assert c == cv.fiveK(124.6, "hs_m", "XC")
    assert cv.fiveK(124.6, "college_f", "TF")["dist"] == "5K"


def test_middle_school_is_3200_m_and_says_so():
    _stub()
    for sport in ("XC", "TF"):
        c = cv.fiveK(110.0, "ms_f", sport)
        assert c["dist"] == "3200m"
        assert c["time"] == cv.ratingClock(110.0, "ms_f", "TF", 3200.0)["time"]


def test_faster_rating_faster_5k():
    _stub()
    secs = [cv.ratingSeconds(r, "hs_f", "TF", 5000.0) for r in (95, 110, 125, 140)]
    assert secs == sorted(secs, reverse=True)


def test_one_clock_for_both_views():
    """The own-pool number on its own pool is the clock; the HS twin is
    only for a pool no reader's level knows (pro, open)."""
    _stub()
    own = cv.fiveK(130.0, "college_m", "XC")
    assert cv.fiveK(130.0, "college_m", "XC", hs=150.0) == own
    # a pro row reads its HS-equivalent on the same-gender HS pool
    assert cv.fiveK(150.0, "pro_m", "XC") is None
    assert cv.fiveK(150.0, "pro_m", "XC", hs=140.0) == cv.fiveK(140.0, "hs_m", "XC")
    # nothing to say
    for r, p in ((None, "hs_m"), (0, "hs_m"), ("x", "hs_m"), (120.0, None),
                 (120.0, "hs_x"), (120.0, "open_m")):
        assert cv.fiveK(r, p, "XC") is None, (r, p)


def test_the_header_label():
    assert cv.fiveKLabel(["hs_m", "college_f|XC"]) == "5K"
    assert cv.fiveKLabel(["ms_m", "ms_f"]) == "3200m"
    assert cv.fiveKLabel(["hs_m", "ms_m"]) == "Track ≈"
    # unknown pools do not vote; an empty page says 5K
    assert cv.fiveKLabel(["hs_m", None, "pro_m"]) == "5K"
    assert cv.fiveKLabel([]) == "5K" and cv.fiveKLabel(None) == "5K"


def test_stamp_five_k():
    _stub()
    rows = [{"pool": "hs_m", "sport": "XC", "rating": 124.6},
            {"rating_pool": "ms_m|TF", "sport": "TF", "rating": 118.0},
            {"pool": "pro_m", "sport": "XC", "rating": 150.0, "hs_rating": 141.0},
            {"pool": "hs_m", "sport": "XC", "rating": None}]
    label = cv.stampFiveK(rows, hs_key="hs_rating")
    assert rows[0]["rating_5k"] == cv.fiveK(124.6, "hs_m")["time"]
    assert rows[0]["rating_5k_dist"] == "5K"
    assert rows[1]["rating_5k_dist"] == "3200m"
    assert rows[2]["rating_5k"] == cv.fiveK(141.0, "hs_m")["time"]
    assert rows[3]["rating_5k"] is None and rows[3]["rating_5k_dist"] is None
    assert label == "Track ≈"
    # a board's pool and sport, and another key (the teams' top-5 average)
    teams = [{"top5_mean": 120.0}]
    assert cv.stampFiveK(teams, "top5_mean", pool="hs_f", sport="XC") == "5K"
    assert teams[0]["top5_mean_5k"] == cv.fiveK(120.0, "hs_f")["time"]


def test_off_the_tables_not_a_conversion_per_row(monkeypatch):
    """! a board reads the per-pool table; it does not convert each row."""
    _stub()
    cv.fiveK(120.0, "hs_m", "XC")                       # builds the table
    calls = []
    real = cv.normalized_to_time
    monkeypatch.setattr(cv, "normalized_to_time",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    rows = [{"pool": "hs_m", "sport": "XC", "rating": 90.0 + i * 0.7}
            for i in range(60)]
    cv.stampFiveK(rows)
    assert not calls and all(r["rating_5k"] for r in rows)


def _env_jinja():
    os.environ.setdefault("XCP_DB_QUIET", "1")
    import app
    return app.app


def test_the_macros():
    _stub()
    app = _env_jinja()
    with app.test_request_context():
        t = app.jinja_env.from_string(
            '{% from "_scale.html" import fk_th, fk, fk_td, fk_inline %}'
            '{{ fk_th(five_k_label(["hs_m"])) }}|'
            '{{ fk(124.6, "hs_m", "XC", none, "5K") }}|'
            '{{ fk(110.0, "ms_m", "TF", none, "Track ≈", "fk-sm") }}|'
            '{{ fk(none, "hs_m") }}|'
            '{{ fk_inline(five_k(110.0, "ms_m", "XC")) }}')
        out = t.render()
    th, hs, ms, none_, inline = out.split("|")
    assert th == '<th class="n fk" title="The track 5K this rating is worth">5K</th>'
    want = cv.fiveK(124.6, "hs_m")["time"]
    assert hs == f'<td class="n fk">{want}</td>'
    assert 'class="n fk fk-sm"' in ms and '<span class="fk-d">3200m</span>' in ms
    assert 'fk-none' in none_
    assert inline.startswith('<span class="fk-i"') and "3200m" in inline


PAGES = ("race.html", "race_tf.html", "compiled.html", "compiled_tf.html", "meet_tf.html",
         "course.html", "venue_tf.html", "school.html", "school_prs.html", "embed_school.html",
         "athlete.html", "compare.html", "breakouts.html", "landing.html", "what_it_takes.html",
         "projections.html", "recruit.html", "recruiting_school.html", "my_page.html")


def test_every_page_with_a_5k_compiles_and_uses_it():
    app = _env_jinja()
    root = os.path.join(ROOT, "racecast", "templates")
    for name in PAGES:
        app.jinja_env.get_template(name)                 # a syntax error raises
        src = open(os.path.join(root, name), encoding="utf-8").read()
        assert "five_k" in src or "fk(" in src, name
    # ! home is another change's (its own 5K column): untouched here
    assert "five_k" not in open(os.path.join(root, "home.html"), encoding="utf-8").read()


def test_the_athlete_bests_average():
    _stub()
    import athlete_bests as ab
    races = [{"sport": "XC", "pool": "hs_m", "speed_rating": 120.0, "hs_rating": 120.0,
              "event": "5000", "time_raw": 960.0, "result_id": 1},
             {"sport": "XC", "pool": "hs_m", "speed_rating": 124.0, "hs_rating": 124.0,
              "event": "5000", "time_raw": 950.0, "result_id": 2}]
    out = ab.all_time_bests(races)
    assert out["XC"]["avg_5k"] == cv.fiveK(122.0, "hs_m", "XC")
    # across pools: the HS-equivalent average on the HS pool
    races.append({"sport": "XC", "pool": "college_m", "speed_rating": 118.0,
                  "hs_rating": 128.0, "event": "8000", "time_raw": 1500.0,
                  "result_id": 3})
    out = ab.all_time_bests(races)
    hs_avg = (120.0 + 124.0 + 128.0) / 3
    assert out["XC"]["avg_5k"] == cv.fiveK(hs_avg, "hs_m", "XC")
    assert out["TF"]["avg_5k"] is None
