"""Record books (racecast/record_books.py, build_record_books.py,
record_pages.py; owner 2026-10-10: "history and record books").

Pinned, with stub data (no database):
  * ordering: one line per athlete, best first; grade bests in grade
    order; most improved is same-pool year-on-year; the course record
    progression and decade bests; "on this day" picks a past year;
  * the filters are the BOARDS' -- rankings._whereClauses /
    season_floor.floorSql / rankings.gradeKeySql / teams.parseFilters are
    called and their text is what the SQL carries, not a retyped copy;
  * incremental: a school rebuilds only when its fingerprint moved;
  * the templates render from a stored ctx.
"""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401  -- sets XCP_DB_PASSWORD, must precede config

import pytest  # noqa: E402

import record_books as RB  # noqa: E402
import build_record_books as B  # noqa: E402
import record_pages as P  # noqa: E402


# ------------------------------------------------------------------ #
#  ordering
# ------------------------------------------------------------------ #

def test_best_per_person_one_line_each_best_first():
    rows = [{"person_id": 1, "rating": 110.0}, {"person_id": 2, "rating": 115.0},
            {"person_id": 1, "rating": 118.0}, {"person_id": 3, "rating": None},
            {"person_id": None, "rating": 130.0}]
    got = RB.bestPerPerson(rows, "rating")
    assert [(r["person_id"], r["rating"]) for r in got] == [(1, 118.0), (2, 115.0)]
    # lower is better for a clock
    t = RB.bestPerPerson([{"person_id": 1, "t": 900}, {"person_id": 1, "t": 880},
                          {"person_id": 2, "t": 890}], "t", higher=False)
    assert [(r["person_id"], r["t"]) for r in t] == [(1, 880), (2, 890)]


def test_grade_bests_in_grade_order_one_per_athlete_and_capped():
    rows = [
        {"person_id": 1, "grade_key": "12", "rating": 120.0},
        {"person_id": 1, "grade_key": "12", "rating": 123.0},   # same senior, two seasons
        {"person_id": 2, "grade_key": "12", "rating": 121.0},
        {"person_id": 3, "grade_key": "9", "rating": 108.0},
        {"person_id": 4, "grade_key": "9", "rating": 111.0},
        {"person_id": 5, "grade_key": "9", "rating": 104.0},
        {"person_id": 6, "grade_key": "sr", "rating": 140.0},   # a college key: not a HS grade
    ]
    got = RB.gradeBests(rows, RB.LEVELS["hs"][2], n=2)
    assert [g for g, _w, _r in got] == ["9", "12"]            # 10 and 11 empty: left out
    assert [w for _g, w, _r in got] == ["Freshman", "Senior"]
    nine = got[0][2]
    assert [r["person_id"] for r in nine] == [4, 3]           # capped at 2, best first
    senior = got[1][2]
    assert [(r["person_id"], r["rating"]) for r in senior] == [(1, 123.0), (2, 121.0)]


def test_most_improved_same_pool_consecutive_years_best_jump_each():
    s = [
        {"person_id": 1, "pool": "hs_m", "year": 2020, "rating": 100.0},
        {"person_id": 1, "pool": "hs_m", "year": 2021, "rating": 108.0},
        {"person_id": 1, "pool": "hs_m", "year": 2022, "rating": 111.0},
        # a two-year gap is not "one year to the next"
        {"person_id": 2, "pool": "hs_m", "year": 2019, "rating": 90.0},
        {"person_id": 2, "pool": "hs_m", "year": 2021, "rating": 112.0},
        # a pool change is a scale change, not an improvement
        {"person_id": 3, "pool": "ms_m", "year": 2020, "rating": 95.0},
        {"person_id": 3, "pool": "hs_m", "year": 2021, "rating": 115.0},
        # a fall is never listed
        {"person_id": 4, "pool": "hs_m", "year": 2020, "rating": 110.0},
        {"person_id": 4, "pool": "hs_m", "year": 2021, "rating": 104.0},
        {"person_id": 5, "pool": "hs_m", "year": 2020, "rating": 99.0},
        {"person_id": 5, "pool": "hs_m", "year": 2021, "rating": 101.0},
    ]
    got = RB.mostImproved(s, n=5)
    assert [(r["person_id"], r["gain"], r["year"], r["prev_year"]) for r in got] == \
        [(1, 8.0, 2021, 2020), (5, 2.0, 2021, 2020)]
    assert got[0]["prev_rating"] == 100.0


def test_progression_every_record_oldest_first_ties_do_not_break():
    rows = [
        {"result_id": 1, "race_date": "2004-09-10", "time_seconds": 960.0},
        {"result_id": 2, "race_date": "2006-10-01", "time_seconds": 970.0},  # slower
        {"result_id": 3, "race_date": "2008-09-20", "time_seconds": 951.5},
        {"result_id": 4, "race_date": "2009-09-20", "time_seconds": 951.5},  # a tie
        {"result_id": 5, "race_date": "2015-10-03", "time_seconds": 940.0},
        {"result_id": 6, "race_date": None, "time_seconds": 900.0},          # undated
    ]
    got = RB.progression(rows)
    assert [r["result_id"] for r in got] == [1, 3, 5]
    assert [r["until"] for r in got] == ["2008-09-20", "2015-10-03", None]
    assert [r["by"] for r in got] == [None, 8.5, 11.5]


def test_decade_bests_newest_first_one_per_athlete():
    rows = [
        {"person_id": 1, "race_date": "2012-09-01", "time_seconds": 950},
        {"person_id": 1, "race_date": "2014-09-01", "time_seconds": 945},
        {"person_id": 2, "race_date": "2019-09-01", "time_seconds": 948},
        {"person_id": 3, "race_date": "2005-09-01", "time_seconds": 955},
        {"person_id": 4, "race_date": "2021-09-01", "time_seconds": 960},
    ]
    got = RB.decadeBests(rows, n=3)
    assert [d for d, _r in got] == [2020, 2010, 2000]
    tens = dict(got)[2010]
    assert [(r["person_id"], r["time_seconds"]) for r in tens] == [(1, 945), (2, 948)]


def test_on_this_day_picks_a_past_year_and_keeps_one_per_year():
    rows = [{"person_id": 1, "race_date": "2026-10-10", "rating": 130.0},
            {"person_id": 2, "race_date": "2014-10-10", "rating": 125.0}]
    pick = RB.otdPick(rows, 2026)
    assert pick["person_id"] == 2 and pick["years_ago"] == 12
    assert RB.otdPick(rows[:1], 2026) is None
    kept = B.otdKeep([
        {"person_id": 1, "race_date": "2014-10-10", "rating": 130},
        {"person_id": 2, "race_date": "2014-10-10", "rating": 129},   # same year
        {"person_id": 1, "race_date": "2019-10-10", "rating": 128},   # same athlete
        {"person_id": 3, "race_date": "2009-10-10", "rating": 120},
    ])
    assert [(r["person_id"], r["race_date"][:4]) for r in kept] == [(1, "2014"), (3, "2009")]


def test_course_ctx_keeps_the_most_raced_distances_with_both_parts():
    def row(part, dist, n, g, rid, date, t, pid):
        return {"part": part, "dist": dist, "n": n, "gender": g, "result_id": rid,
                "race_date": date, "time_seconds": t, "person_id": pid}
    rows = [row("p", 5000, 900, "M", 1, "2005-09-01", 960, 1),
            row("p", 5000, 900, "M", 2, "2011-09-01", 950, 2),
            row("d", 5000, 900, "M", 2, "2011-09-01", 950, 2),
            row("d", 5000, 900, "M", 1, "2005-09-01", 960, 1),
            row("p", 4000, 5, "F", 3, "2012-09-01", 900, 3),        # too few results
            row("p", 4828, 40, "F", 4, "2015-09-01", 1100, 4)]
    ctx = B.courseCtx("Lane CC", rows)
    assert [d["dist"] for d in ctx["dists"]] == [5000, 4828]
    m = ctx["dists"][0]["genders"]["M"]
    assert [r["result_id"] for r in m["progression"]] == [1, 2]
    assert m["progression"][0]["until"] == "2011-09-01"
    assert [d for d, _r in m["decades"]] == [2010, 2000]
    assert ctx["dists"][1]["genders"].keys() == {"F"}


# ------------------------------------------------------------------ #
#  the boards' filters, reused
# ------------------------------------------------------------------ #

def test_sql_carries_the_boards_where_clause(monkeypatch):
    import rankings
    seen = []

    def fake(f, params, with_dates):
        seen.append((f["board"], f["pool"], with_dates))
        params["sentinel"] = 1
        return " AND <<BOARD-WHERE>>"
    monkeypatch.setattr(rankings, "_whereClauses", fake)
    f = B.boardFilters("performance", "XC", "hs_m", ["CA", "OR"])
    for sql, params in (B.stateRaceSql(f), B.otdSql(f)):
        assert "<<BOARD-WHERE>>" in sql and params["sentinel"] == 1
    fa = B.boardFilters("ability", "XC", "hs_m", ["CA"])
    sql, _p = B.seasonSql(fa, 100, grades=("9", "10"))
    assert "<<BOARD-WHERE>>" in sql
    fc = B.boardFilters("performance", "XC", "all", scope="all", gender="f")
    assert "<<BOARD-WHERE>>" in B.courseSql(fc)[0]
    assert ("ability", "hs_m", False) in seen and ("performance", "all", True) in seen


def test_the_real_clauses_state_scope_floor_and_grade_key():
    from rankings import US_STATES, gradeKeySql
    from season_floor import floorSql
    f = B.boardFilters("performance", "XC", "hs_m", list(US_STATES))
    sql, params = B.stateRaceSql(f)
    assert "state = ANY(%(state)s)" in sql and params["state"] == list(US_STATES)
    assert "pool = %(pool)s" in sql and params["pool"] == "hs_m"
    assert params["sport"] == "XC"
    fa = B.boardFilters("ability", "TF", "hs_f", ["CA"])
    sql, params = B.seasonSql(fa, 10, grades=("9", "10", "11", "12"))
    assert floorSql(True) in sql and params["min_races"] == 3
    assert gradeKeySql("s") in sql and params["grades"] == ["9", "10", "11", "12"]
    # the US-only scope the boards use keeps foreign meets off "on this day"
    fo = B.boardFilters("performance", "XC", "hs_m")
    sql, params = B.otdSql(fo)
    assert "us_states" in params and "result_id < 0" in sql
    # a course record list is one gender of every level
    fc = B.boardFilters("performance", "XC", "all", scope="all", gender="m")
    sql, params = B.courseSql(fc)
    assert "pool LIKE %(gender)s" in sql and params["gender"].endswith("_m")
    assert "us_states" not in params


def test_team_lists_come_from_the_team_board(monkeypatch):
    import teams as T
    got = {}

    def fake_rank(cur, f):
        got.update(f)
        return [{"school": "Jesuit", "state": "OR", "top5_mean": 120.0}]
    monkeypatch.setattr(T, "getTeamRankings", fake_rank)
    monkeypatch.setattr(T, "fillBestRunner", lambda cur, rows: rows)

    class Cur:
        def execute(self, *a, **k):
            pass
    rows = B.teamRows(Cur(), "XC", "hs_m", "OR", school="Jesuit", n=5)
    assert rows[0]["school"] == "Jesuit"
    assert got["span"] == "alltime" and got["board_scope"] == "OR"
    assert got["school"] == ["Jesuit"] and got["limit"] == 5


# ------------------------------------------------------------------ #
#  incremental
# ------------------------------------------------------------------ #

def test_changed_schools_only_moved_fingerprints_biggest_first():
    fps = {("Jesuit", "XC"): ("fp1", 500), ("Jesuit", "TF"): ("fp2", 900),
           ("Summit", "XC"): ("fp3", 300), ("Unattached", "XC"): ("fp4", 9999),
           ("Kingston", "XC"): ("fp5", 100)}
    clusters = {"Jesuit": [("OR", True), ("CA", False)],
                "Kingston": [("WA", True), ("MO", False)]}
    stored = {RB.schoolKey("Jesuit", "OR", "XC"): "fp1",     # unchanged
              RB.schoolKey("Jesuit", "CA", "XC"): "old",
              RB.schoolKey("Kingston", "WA", "XC"): "fp5",
              RB.schoolKey("Kingston", "MO", "XC"): "fp5"}

    def is_team(s):
        return s != "Unattached"
    todo = B.changedSchools(fps, stored, clusters, is_team, cap=0)
    assert [(s, sp, st) for s, sp, st, _pr, _fp in todo] == [
        ("Jesuit", "TF", "CA"), ("Jesuit", "TF", "OR"),
        ("Jesuit", "XC", "CA"), ("Summit", "XC", None)]
    assert todo[0][3] == "OR"                                 # the primary rides along
    assert len(B.changedSchools(fps, stored, clusters, is_team, cap=2)) == 2
    assert B.changedSchools(fps, {RB.schoolKey("Summit", None, "XC"): "fp3"},
                            {}, lambda s: s == "Summit", cap=0) == []


def test_keys_and_paths():
    assert RB.stateKey("xc", "hs_m", "ca") == "XC|hs_m|CA"
    assert RB.schoolKey("Jesuit", None, "tf") == "Jesuit||TF"
    assert RB.otdKey("10-10") == "10-10|US" and RB.otdKey("10-10", "or") == "10-10|OR"
    assert P.recordsPath("CA") == "/records/ca?sport=xc"
    assert P.recordsPath("CA", "tf", "ms", "f") == "/records/ca?sport=tf&level=ms&gender=girls"
    assert P.parseArgs({"sport": "TF", "level": "nope", "gender": "Girls"}) == ("tf", "hs", "f")
    assert RB.poolFor("college", "f") == "college_f" and RB.poolFor("x", "m") is None


def test_load_book_never_raises():
    class Broken:
        connection = None

        def execute(self, sql, *a):
            if "SELECT" in sql:
                raise RuntimeError("no table")
    assert RB.loadBook(Broken(), "state", "XC|hs_m|CA") is None

    class Ok:
        def execute(self, *a):
            pass

        def fetchone(self):
            return {"ctx": {"races": []}, "built_at": datetime.datetime(2026, 10, 10)}
    assert RB.loadBook(Ok(), "state", "k") == {"races": [], "built_at": "2026-10-10"}


def test_stamp_book_reaches_every_rating_in_both_shapes():
    calls = []

    def stamp(rows, rating_keys=("rating",), sport=None, pool=None):
        calls.append((rating_keys, len(rows)))
        for r in rows:
            r["hs_" + rating_keys[0]] = 1.0
        return False
    state = {"races": [{"rating": 1}], "seasons": [{"rating": 1}],
             "grades": [["9", "Freshman", [{"rating": 1}, {"rating": 2}]]],
             "teams": [{"top5_mean": 1}]}
    P.stampBook(state, stamp)
    assert (("rating",), 1) in calls and (("rating",), 3) in calls
    assert (("top5_mean",), 1) in calls
    calls.clear()
    school = {"sport": "XC",
              "events": [{"kind": "running", "tables": {"M": [{"speed_rating": 1}], "F": []}}],
              "grades": [{"pool": "hs_m", "grades": [["12", "Senior", [{"rating": 1}]]]}],
              "improved": [{"pool": "hs_m", "rows": [{"rating": 1}]}],
              "teams": [{"pool": "hs_m", "rows": [{"top5_mean": 1}]}]}
    P.stampBook(school, stamp)
    assert (("speed_rating",), 1) in calls and (("rating",), 2) in calls
    assert school["teams"][0]["rows"][0]["hs_top5_mean"] == 1.0


# ------------------------------------------------------------------ #
#  the pages render from a stored ctx
# ------------------------------------------------------------------ #

def _race(pid, rating, sport="XC"):
    return {"person_id": pid, "name": f"Runner {pid}", "school": "Jesuit", "state": "OR",
            "grade": "12", "pool": "hs_m", "sport": sport, "year": 2014, "rating": rating,
            "result_id": 900 + pid, "meet_id": 41, "div_id": 2, "event_id": 7,
            "race_date": "2014-10-10", "time_seconds": 900.0 + pid, "distance": 5000,
            "meet_name": "Nike Portland XC"}


def test_templates_render():
    flask = pytest.importorskip("flask")  # noqa: F841
    import app as A
    import landing as L
    import record_books as RBm
    book = {"races": [_race(1, 125.0), _race(2, 122.0)],
            "seasons": [dict(_race(3, 120.0), n_races=6, best=_race(3, 121.0))],
            "grades": [["9", "Freshman", [dict(_race(4, 110.0), grade="9")]]],
            "teams": [{"school": "Jesuit", "state": "OR", "pool": "hs_m", "sport": "XC",
                       "year": 2014, "rank": 1, "top5_mean": 118.2,
                       "best_person_id": 1, "best_name": "Runner 1"}],
            "built_at": "2026-10-10"}
    orig = RBm.loadBook
    RBm.loadBook = lambda cur, kind, key: book if kind == "state" else None
    try:
        with A.app.test_request_context("/records/or?sport=xc"):
            ctx = P.statePage(None, "OR", {"sport": "xc"}, A.stampBoardRows, L)
            html = A.render_template("records.html", **ctx)
    finally:
        RBm.loadBook = orig
    assert "Oregon High School Boys Cross Country All-Time Records" in html
    assert "/race/xc/41/2?r=901" in html and "Runner 1" in html
    assert "era-adjusted" in html and "Freshman" in html
    school_book = {"sport": "XC", "state": "OR", "events": [
        {"label": "5000m", "kind": "running", "distance": 5000, "tables": {
            "M": [dict(_race(1, 125.0), speed_rating=125.0)], "F": []}}],
        "grades": [{"pool": "hs_m", "grades": [["12", "Senior", [_race(5, 119.0)]]]}],
        "improved": [{"pool": "hs_m", "rows": [dict(_race(6, 115.0), gain=7.5,
                                                     prev_rating=107.5)]}],
        "teams": [{"pool": "hs_m", "rows": book["teams"]}]}
    P.stampBook(school_book, A.stampBoardRows)       # as the route does
    with A.app.test_request_context("/school/Jesuit/records"):
        html = A.render_template("school_records.html", school="Jesuit", state="OR",
                                 sport="XC", record_book=school_book, has_hs_view=False)
    assert "Most improved" in html and "+7.5" in html and "Best by grade" in html
    eras = {"dist": 5000, "label": "5000m", "genders": {"M": {
        "progression": [dict(_race(1, 120.0), until=None, by=3.2)],
        "decades": [[2010, [_race(1, 120.0)]]]}}}
    with A.app.test_request_context("/course/x"):
        html = A.render_template("_course_eras.html", course_eras_dist=eras)
    assert "still standing" in html and "2010s" in html
