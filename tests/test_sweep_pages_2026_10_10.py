"""Page bugs from the read-only review of sweep 2026-10-10.

No Postgres: the SQL is read off stub cursors and the templates are rendered
with the site's own Jinja environment.

    python -m pytest -q tests/test_sweep_pages_2026_10_10.py
"""
import os
import re
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                  # noqa: E402

TEMPLATES = os.path.join(_ROOT, "racecast", "templates")


def _tpl(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as fh:
        return fh.read()


class StubCursor:
    """Records every statement; answers from a list of canned results, each
    either a list of rows (fetchall/fetchone take from it) or a callable
    (sql, params) -> rows."""

    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.sql = []
        self._rows = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        a = self.answers.pop(0) if self.answers else []
        self._rows = list(a(sql, params) if callable(a) else a)

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def fetchmany(self, n):
        rows, self._rows = self._rows[:n], self._rows[n:]
        return rows


@pytest.fixture(scope="module")
def A():
    import app
    return app


# ---- 1. links pin the feed --------------------------------------------- #

def test_school_meets_pin_the_feed_and_name_it():
    import school
    cur = StubCursor([[{"meet_id": 7, "pin_rid": -301, "date": "2025-10-01",
                        "runners": 7, "divisions": 1, "avg_rating": None,
                        "best_rating": None, "distances": [8000],
                        "meet_name": "tfrrs meet"}]])
    rows = school.schoolMeets(cur, "Dana Hills", "XC")
    sql = cur.sql[-1][0]
    assert "min(rr.result_id)" in sql and "pin_rid" in sql
    assert "GROUP  BY rr.meet_id, (rr.result_id < 0)" in sql
    assert "WHEN g.pin_rid < 0" in sql            # the pin picks the name table
    assert rows[0]["pin_rid"] == -301


@pytest.mark.parametrize("tpl", ["school.html", "embed_school.html"])
def test_school_meet_links_carry_the_pin(tpl):
    assert "r={{ m.pin_rid }}" in _tpl(tpl)


def test_course_meet_links_carry_the_pin(A):
    src = _tpl("course.html")
    assert src.count("?r={{ m.pin_rid }}") == 2
    cur = StubCursor([[]])
    A.get_course_meets(cur, "Woodward Park")
    assert "min(r.result_id) AS pin_rid" in cur.sql[-1][0]


def test_meet_tf_event_links_carry_alt_and_school(A):
    src = _tpl("meet_tf.html")
    macro = re.search(r"{% macro race_q\(\) %}.*?{% endmacro %}", src).group(0)
    t = A.app.jinja_env.from_string(macro + "{{ race_q() }}")
    assert t.render(other_sources=[{"alt": 0, "n": 3}], alt_idx=1,
                    school="A B") == "?alt=1&amp;school=A%20B"
    assert t.render(other_sources=[], alt_idx=0, school=None) == ""
    assert "{{ e.div_id }}{{ race_q() }}" in src


def test_race_tf_links_back_with_alt():
    src = _tpl("race_tf.html")
    assert ('/meet/tf/{{ header.meet_id }}{% if other_sources %}'
            '?alt={{ alt_idx }}{% endif %}') in src


def _srcRows(*names):
    return [{"source": n, "n": 10 - i} for i, n in enumerate(names)]


def test_race_tf_honours_alt_when_that_feed_ran_it(A):
    # meet feeds: anet (alt 0), tfrrs (alt 1); both ran the triple
    cur = StubCursor([_srcRows("anet"), _srcRows("tfrrs"),
                      [{"source": "anet"}, {"source": "tfrrs"}]])
    src, alt, others = A._tfRaceSource(cur, 9, 1, 2, {"alt": "1"})
    assert (src, alt) == ("tfrrs", 1) and others == [{"alt": 0, "n": 10}]


def test_race_tf_alt_falls_back_when_that_feed_did_not_run_it(A):
    cur = StubCursor([_srcRows("anet"), _srcRows("tfrrs"),
                      [{"source": "anet"}]])
    src, alt, _ = A._tfRaceSource(cur, 9, 1, 2, {"alt": "1"})
    assert (src, alt) == ("anet", 0)


def test_race_tf_pin_wins(A):
    cur = StubCursor([[{"source": "tfrrs"}], _srcRows("anet"), _srcRows("tfrrs")])
    src, alt, _ = A._tfRaceSource(cur, 9, 1, 2, {"r": "-5", "alt": "0"})
    assert (src, alt) == ("tfrrs", 1)


# ---- 3. distance overrides --------------------------------------------- #

def test_course_rows_take_the_override(A):
    cte = A._courseRowsCte()
    assert cte.count("LEFT JOIN dist_override dov") == 2
    assert cte.count("COALESCE(dov.distance::real") == 2
    assert "dist_drop" not in cte
    assert cte.count("dist_drop") == 0
    assert A._courseRowsCte(drop=True).count("FROM dist_drop dd") == 2


@pytest.mark.parametrize("has_drop", [True, False])
def test_course_time_records_leave_out_dropped_divisions(A, has_drop):
    for fn in (A.get_course_records, A.get_course_team_records):
        cur = StubCursor([[{"d": "dist_drop" if has_drop else None}], []])
        fn(cur, "Woodward Park", 5000)
        assert ("FROM dist_drop dd" in cur.sql[-1][0]) is has_drop


def test_compiled_races_take_the_override():
    import meet_compile as M
    cur = StubCursor([[{"1": 1}], []])
    M.compiledResults(cur, 9)
    sql = cur.sql[-1][0]
    assert "LEFT JOIN dist_override dov" in sql
    assert sql.count("COALESCE(\n                   dov.distance::real") + \
        sql.count("COALESCE(\n                 dov.distance::real") == 2
    cur = StubCursor([[]])
    M.compiledIndex(cur, 9)
    sql = cur.sql[-1][0]
    assert "LEFT JOIN dist_override dov" in sql
    assert sql.count("dov.distance::real, m.distance") == 2


# ---- 4. search folds accents and apostrophes, both sides ---------------- #

@pytest.mark.parametrize("raw,want", [
    ("O'Brien", "obrien"),
    ("O’Brien", "obrien"),
    ("Peña", "pena"),
    ("Zoë  Smith-Jones", "zoe smith jones"),
    ("Mt. SAC", "mt sac"),
    ("Saint-Étienne (FR)", "saint etienne fr"),
])
def test_search_fold(raw, want):
    import search_index
    assert search_index.searchFold(raw) == want


def test_query_and_index_fold_alike(A):
    where, params, _ = A._searchTerms("o'brien peña")
    assert params["t0"] == "%obrien%" and params["t1"] == "%pena%"
    import search_index
    seen = []

    class _C:
        def execute(self, *a, **k):
            pass
    import psycopg2.extras
    orig = psycopg2.extras.execute_values
    psycopg2.extras.execute_values = lambda cur, sql, batch, **k: seen.extend(batch)
    try:
        search_index._flush(_C(), [("athlete", "Liam O'Brien", "Peña HS",
                                    "/athlete/1", "liam o'brien peña hs",
                                    "o'brien", 2025, 3)])
    finally:
        psycopg2.extras.execute_values = orig
    assert seen[0][4] == "liam obrien pena hs" and seen[0][5] == "obrien"


# ---- 20. a lone typed year ---------------------------------------------- #

class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self, *a, **k):
        return self

    # the cursor context manager is the conn itself
    def execute(self, *a, **k):
        return self.cur.execute(*a, **k)

    def fetchall(self):
        return self.cur.fetchall()

    def fetchone(self):
        return self.cur.fetchone()


def test_a_lone_year_searches_as_the_dropdown_does(A, monkeypatch):
    cur = StubCursor()
    monkeypatch.setattr(A, "getConn", lambda *a, **k: _Conn(cur))
    A._run_search("2025", "all", None, 0)
    sql, params = cur.sql[0]
    assert params["t0"] == "%2025%"
    assert "sort_year = %(y)s" not in sql          # no filter on nothing
    cur.sql.clear()
    A._run_search("2025 arcadia", "all", None, 0)  # a year beside words filters
    sql, params = cur.sql[0]
    assert params["t0"] == "%arcadia%" and params["y"] == 2025


# ---- 5. TF meet page Winner column -------------------------------------- #

def test_tf_meet_winner_reads_marks_finals_and_skips_dq(A):
    rows = [
        # high jump in feet-inches: not a float, used to have no winner
        {"div_id": 1, "event_id": 5, "is_field": 1, "mark": "5-10", "athlete_name": "A"},
        {"div_id": 1, "event_id": 5, "is_field": 1, "mark": "6-02", "athlete_name": "B"},
        # 100m: a faster prelim and a DQ'd final runner never win
        {"div_id": 1, "event_id": 6, "time_seconds": 10.9, "round": "P", "athlete_name": "Heat"},
        {"div_id": 1, "event_id": 6, "time_seconds": 10.8, "round": "F", "mark": "DQ",
         "athlete_name": "Dq"},
        {"div_id": 1, "event_id": 6, "time_seconds": 11.1, "round": "F", "athlete_name": "Champ"},
        # the anet TF sentinel is no time
        {"div_id": 1, "event_id": 7, "time_seconds": 20000.002, "athlete_name": "Dns"},
        {"div_id": 1, "event_id": 7, "time_seconds": 250.0, "athlete_name": "Miler"},
    ]
    w = A._tfEventWinners(rows)
    assert w[(1, 5)]["athlete_name"] == "B"
    assert w[(1, 6)]["athlete_name"] == "Champ"
    assert w[(1, 7)]["athlete_name"] == "Miler"


# ---- 6 / 8. school bests: names filled, the pin selected ---------------- #

def _namesFrom(name_by_pid):
    def answer(sql, params):
        ids = params[0]
        return [{"person_id": p, "name": name_by_pid[p]} for p in ids
                if p in name_by_pid]
    return answer


def test_school_best_selects_the_pin_and_fills_names():
    import school
    best = [{"person_id": 5, "name": " ", "rating": 120, "pool": "college_m",
             "result_id": -77}]
    cur = StubCursor([best, _namesFrom({5: "Ann Lee"}), []])
    rows = school.schoolBest(cur, "Amherst", "XC")
    assert "rr.result_id" in cur.sql[0][0]
    assert rows[0]["name"] == "Ann Lee"


def test_school_top_athletes_fill_names():
    import school
    top = [{"person_id": 6, "name": " ", "best": 120, "pool": "college_f"}]
    cur = StubCursor([top, _namesFrom({6: "Bo Diaz"}), []])
    assert school.schoolTopAthletes(cur, "Amherst", "XC")[0]["name"] == "Bo Diaz"


def test_school_prs_fall_back_to_the_feed_name():
    import school_prs
    src = open(school_prs.__file__, encoding="utf-8").read()
    assert src.count("_fillRowNames(cur,") == 2


# ---- 7. school PRs field links ------------------------------------------ #

def test_school_prs_field_rows_without_ids_link_the_meet(A):
    src = _tpl("school_prs.html")
    i = src.index("{% elif r.event_id and r.div_id is not none %}")
    tail = src[i:src.index("</td>", i)]
    assert '/meet/tf/{{ r.meet_id }}{% if r.result_id %}?r={{ r.result_id }}' in tail


# ---- 15. header, year bar and current season follow the state chip ------ #

@pytest.mark.parametrize("fn,args", [
    ("schoolYears", ()), ("currentSeason", ("XC",)), ("schoolHeader", ())])
def test_school_scalars_take_the_state_chip(fn, args):
    import school
    cur = StubCursor([[{"athletes": 3, "y": 2025}]])
    getattr(school, fn)(cur, "Oregon", *args, "IL", "OR")
    sql, params = cur.sql[0]
    assert "person_home_state" in sql and params["sf_state"] == "IL"
    cur = StubCursor([[{"athletes": 3, "y": 2025}]])
    getattr(school, fn)(cur, "Oregon", *args)
    assert "person_home_state" not in cur.sql[0][0]


# ---- 9. course team records: no Unattached ------------------------------ #

def test_course_team_records_leave_out_non_teams(A):
    table = ([{"school": "Unattached", "gender": "M", "total": 1}]
             + [{"school": f"School {i}", "gender": "M", "total": 2 + i}
                for i in range(5)])
    caps = []

    def answer(sql, params):
        caps.append(params["limit"])
        return table[:params["limit"]]
    cur = StubCursor([[{"d": None}], answer, answer])
    rows = A.get_course_team_records(cur, "Woodward Park", 5000, limit=3)
    assert [r["school"] for r in rows] == ["School 0", "School 1", "School 2"]
    assert caps == [3, 4]                      # refetched one deeper, once


def test_course_team_rating_bests_leave_out_non_teams(A, monkeypatch):
    monkeypatch.setattr(A, "_ratingPoolCol", lambda cur, t: None)
    rows = [{"school": "Unattached", "gender": "F", "pool": "hs_f", "avg5": 140.0},
            {"school": "Dublin", "gender": "F", "pool": "hs_f", "avg5": 130.0}]
    cur = StubCursor([lambda sql, p: rows[:p["limit"]]] * 3)
    got = A.get_course_team_rating_bests(cur, "Hidden Valley", limit=1)
    assert [r["school"] for r in got] == ["Dublin"]


# ---- 10. /compare ---------------------------------------------------------- #

def test_compare_bests_are_flat_races_only(monkeypatch):
    import compare as C
    import rankings
    monkeypatch.setattr(rankings, "_hasEventKind", lambda cur=None: True)
    cur = StubCursor([[], []])
    C.bestRows(cur, 1, 2)
    assert all("event_kind IS NULL" in sql for sql, _p in cur.sql)
    monkeypatch.setattr(rankings, "_hasEventKind", lambda cur=None: False)
    cur = StubCursor([[], []])
    C.bestRows(cur, 1, 2)
    assert not any("event_kind" in sql for sql, _p in cur.sql)


@pytest.mark.parametrize("sec,want", [
    (0.04, "0.04s"), (12.3, "12.30s"), (59.994, "59.99s"),
    (59.996, "1:00"), (75.5, "1:15.5")])
def test_compare_margins(sec, want):
    import compare as C
    assert C.fmtMargin(sec) == want


@pytest.mark.parametrize("a,b,want", [
    ("Jane Smith", "Kate Smith", ("J. Smith", "K. Smith")),
    ("Jane Smith", "Kate Jones", ("Smith", "Jones")),
    ("Jane Smith", "June Smith", ("Jane Smith", "June Smith")),
])
def test_compare_short_names(a, b, want):
    import compare as C
    assert C.shortNames(a, b) == want


def test_compare_edges_follow_the_default_hs_scale():
    import compare as C
    # own pools: A leads 120 vs 118; HS: B leads 104 vs 110
    rows = [{"a": {"rating": 120.0, "hs_rating": 104.0},
             "b": {"rating": 118.0, "hs_rating": 110.0},
             "edge": "a", "edge_by": 2.0}]
    C.stampEdgeHs(rows)
    assert rows[0]["edge"] == "b" and rows[0]["hs_edge_by"] == 6.0
    assert rows[0]["edge_by"] == 6.0          # never a negative "+X"
    brate = {"a": {"speed_rating": 120.0, "hs_speed_rating": 104.0},
             "b": {"speed_rating": 118.0, "hs_speed_rating": 110.0}}
    assert C.bestRatingEdge(brate) == "b"
    brate["b"]["hs_speed_rating"] = None      # no HS on one side: own scale
    assert C.bestRatingEdge(brate) == "a"
    src = _tpl("compare.html")
    assert 'brate_edge == "a"' in src and "m.margin_label" in src
    assert '"%.1f"|format(m.margin)' not in src


# ---- 11. TF heats: semis are their own round, ties share a place -------- #

def test_tf_sections_keep_semis_apart_and_tie_places(A):
    rows = [
        {"result_id": 1, "round": "F", "time_seconds": 10.50, "heat": None},
        {"result_id": 2, "round": "F", "time_seconds": 10.60, "heat": None},
        {"result_id": 3, "round": "F", "time_seconds": 10.60, "heat": None},
        {"result_id": 4, "round": "F", "time_seconds": 10.70, "heat": None},
        {"result_id": 5, "round": "S", "time_seconds": 10.65, "heat": None},
        {"result_id": 6, "round": "P", "time_seconds": 10.80, "heat": None},
    ]
    secs = A._tf_heat_sections(rows, False)
    assert [s["label"] for s in secs] == ["Finals", "Semifinals", "Prelims"]
    assert [r["sec_place"] for r in secs[0]["rows"]] == [1, 2, 2, 4]


def test_scoring_still_reads_a_semi_as_not_the_final():
    import tf_points
    assert tf_points.rowRound({"round": "S"}) == "prelim"
    assert tf_points.rowRound({"round": "S"}, fine=True) == "semi"
    assert tf_points.rowRound({"round": "Q"}, fine=True) == "quarter"
    assert tf_points.rowRound({"round": "F"}, fine=True) == "final"


# ---- 12. PR/SR flags: same event kind, same-day earlier rounds ---------- #

def test_record_flags_match_the_event_kind(A, monkeypatch):
    import rankings
    monkeypatch.setattr(rankings, "_hasEventKind", lambda cur=None: True)
    rows = [{"person_id": 1, "result_id": 10, "time_seconds": 40.0, "round": "F"}]
    cur = StubCursor([[], []])
    A.stampRecordFlags(cur, "TF", rows, 300, "2026-05-01", event_kind="hurdles")
    assert all("rr.event_kind = %(kind)s" in sql for sql, _p in cur.sql)
    assert cur.sql[0][1]["kind"] == "hurdles"
    cur = StubCursor([[], []])
    A.stampRecordFlags(cur, "TF", rows, 400, "2026-05-01")
    assert all("rr.event_kind IS NULL" in sql for sql, _p in cur.sql)


def test_a_final_slower_than_that_mornings_prelim_is_no_pr(A, monkeypatch):
    import rankings
    monkeypatch.setattr(rankings, "_hasEventKind", lambda cur=None: False)
    rows = [{"person_id": 1, "result_id": 20, "time_seconds": 50.5, "round": "F"},
            {"person_id": 1, "result_id": 30, "time_seconds": 50.1, "round": "P"}]
    same_day = [{"person_id": 1, "result_id": 20, "time_seconds": 50.5, "round": "F"},
                {"person_id": 1, "result_id": 30, "time_seconds": 50.1, "round": "P"}]
    prior = [{"person_id": 1, "best_before": 51.0, "season_best_before": 51.0}]
    cur = StubCursor([prior, same_day])
    A.stampRecordFlags(cur, "TF", rows, 400, "2026-05-01")
    final, prelim = rows
    assert prelim["is_pr"] is True             # the morning's run was the PR
    assert final["is_pr"] is False and final["is_sr"] is False


# ---- 13 / 14 / 16. XC DQ/DNF places, the meet page's winners ------------ #

def test_xc_places_skip_dq_and_non_finishers(A):
    rows = [{"time_seconds": 900.0, "status": None},
            {"time_seconds": 901.0, "status": "DQ"},
            {"time_seconds": 902.0, "status": "FS"},
            {"time_seconds": 903.0, "status": None},
            {"time_seconds": 999999, "status": "DNF"},
            {"time_seconds": 999999, "status": None}]
    A._stampXcPlaces(rows)
    assert [r["pl"] for r in rows] == [1, None, None, 2, None, None]
    assert [r["pl_status"] for r in rows] == [None, "DQ", "FS", None, "DNF", " - "]


def test_race_template_places_from_pl_and_links_only_people():
    src = _tpl("race.html")
    assert "{{ row.pl if row.pl else row.pl_status }}" in src
    assert "<span>{{ loop.index }}</span></td>\n        <td class=\"nm\">" not in src
    for t in ("race.html", "compiled.html"):
        assert "{% if r and r.person_id %}<a href=\"/athlete/{{ r.person_id }}" in _tpl(t)


def test_meet_winner_is_never_a_dq_and_carries_its_state(A, monkeypatch):
    import meet_compile
    monkeypatch.setattr(A, "_hasResultsStatus", lambda cur, table="results": True)

    def stamp(cur, rows):
        for r in rows:
            r["school_state"] = "AZ"
    monkeypatch.setattr(meet_compile, "stampSchoolStates", stamp)
    split = []
    monkeypatch.setattr(meet_compile, "splitCollisionTeams",
                        lambda cur, rows, **k: split.append(k))
    rows = [{"div_id": 1, "person_id": 9, "time_seconds": 880.0, "school": "Dq HS",
             "status": "DQ", "name": "Dq Runner", "team_id": None}]
    rows += [{"div_id": 1, "person_id": i, "time_seconds": 900.0 + i,
              "school": "Hamilton", "status": None, "name": f"R{i}", "team_id": None}
             for i in range(1, 6)]
    cur = StubCursor([rows])
    got = A.meetWinners(cur, 7, [{"div_id": 1, "gender": "M"}], meet_state="CA")
    w = got[1]["winner"]
    assert w["name"] == "R1" and w["school_state"] == "AZ"
    assert got[1]["team"]["school"] == "Hamilton"
    assert split and split[0]["meet_state"] == "CA"
    src = _tpl("meet.html")
    assert "w.winner.school_state or header.state" in src
    assert "d.n_finishers" in src


def test_meet_divisions_count_finishers(A, monkeypatch):
    monkeypatch.setattr(A, "_hasResultsStatus", lambda cur, table="results": True)
    cur = StubCursor([[]])
    A.get_meet_divisions(cur, 7)
    assert "AS n_finishers" in cur.sql[-1][0] and "'DNF'" in cur.sql[-1][0]
