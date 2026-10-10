"""The reader tools of 2026-10-10 (owner-approved items 4, 5, 10, 13, 14):
the rating -> time sheet (/scale), the grade rank under the athlete header,
the head-to-head prediction on /compare, the model's record at a meet on
/predictions, and the goal calculator (/goal).

Pinned here, with stub cursors (no database): the sheet's rows span the
boards' own ends rounded outward and convert on the pool's own scale; the
rating "i" links to /scale; the grade line reads one season_rank row and
says nothing for a grade the pool does not have; a winner the model gave no
time is a miss; a track meet has no record; a goal is the published mark or
recruiting median at the chosen course.

    python -m pytest -q tests/test_reader_tools.py
"""
import contextlib
import datetime
import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), _ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401
os.environ.setdefault("XCP_DB_QUIET", "1")

import pytest  # noqa: E402

import app as A  # noqa: E402
import scale as S  # noqa: E402
import grade_rank as GR  # noqa: E402
import meet_track as MT  # noqa: E402
import goal as G  # noqa: E402
import h2h as H  # noqa: E402
import accuracy as ACC  # noqa: E402
import ttlcache  # noqa: E402


def read(*p):
    with open(os.path.join(_ROOT, *p), encoding="utf-8") as f:
        return f.read()


class Cur:
    """A cursor that answers each execute from a queue of row lists."""

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.sql = []
        self.rows = []
        self.connection = self

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))
        self.rows = self.answers.pop(0) if self.answers else []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def rollback(self):
        pass

    def cursor(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ------------------------------------------------------------------ #
#  5. /scale
# ------------------------------------------------------------------ #

def test_rows_span_the_board_rounded_outward():
    assert S.roundedSpan(38.4, 171.2, 5) == (35, 175)
    assert S.roundedSpan(40.0, 170.0, 5) == (40, 170)
    assert S.roundedSpan(171.2, 38.4, 10) == (30, 180)
    assert S.roundedSpan(None, 10, 5) is None


def test_college_gets_its_championship_distance():
    keys = lambda p: [c[0] for c in S.columnsFor(p)]   # noqa: E731
    assert keys("hs_m") == ["1600", "3200", "xc5k"]
    assert keys("college_m")[-1] == "xc8k" and keys("college_f")[-1] == "xc6k"
    assert keys("ms_f") == ["1600", "3200", "xc5k"]


def test_rows_are_hs_numbers_converted_on_the_own_scale():
    seen = []

    def conv(own, pool, d, sport):
        seen.append((own, pool, d, sport))
        return 1000.0 * d / own          # faster rating -> shorter time

    t = S.buildTable("college_m", (61.0, 158.9, {"XC": 2026}), 1.2, 5, convert=conv)
    hs = [r["hs"] for r in t["rows"]]
    assert hs[0] == 195 and hs[-1] == 70 and hs == sorted(hs, reverse=True)
    assert all(b - a == -5 for a, b in zip(hs, hs[1:]))
    top = t["rows"][0]
    assert top["own"] == round(195 / 1.2, 1)
    # converted on the pool's own number, not the HS one
    assert seen[0][0] == pytest.approx(195 / 1.2) and seen[0][1] == "college_m"
    assert set(top["times"]) == {"1600", "3200", "xc5k", "xc8k"}
    assert t["lo"] == round(61.0 * 1.2, 1) and t["hi"] == round(158.9 * 1.2, 1)


def test_no_board_no_rows():
    assert S.buildTable("hs_m", None, 1.0)["rows"] == []


def test_board_ends_read_the_boards_filters():
    cur = Cur([[{"y": 2026}], [{"r": 171.2}], [{"r": 38.4}],
               [{"y": 2025}], [{"r": 168.0}], [{"r": 41.0}]])
    lo, hi, years = S.boardEnds(cur, "hs_m")
    assert (lo, hi) == (38.4, 171.2) and years == {"XC": 2026, "TF": 2025}
    ends = [s for s, _ in cur.sql if "ORDER BY mean_rating" in s]
    assert len(ends) == 4 and all("LIMIT 1" in s for s in ends)
    assert all("(n_races >= %(floor)s OR year >= %(open)s)" in s
               and "state = ANY(%(us)s)" in s for s in ends)


@pytest.fixture
def client(monkeypatch):
    import database

    @contextlib.contextmanager
    def conn():
        yield Cur()
    monkeypatch.setattr(database, "getConn", conn)
    return A.app.test_client()


def test_scale_page_renders_and_prints(client, monkeypatch):
    monkeypatch.setattr(S, "tableFor", lambda cur, p, step=5: dict(
        S.buildTable(p, (60.0, 150.0, {}), 1.0, step, convert=lambda *a: 300.0),
        computed_at=None))
    r = client.get("/scale?pool=hs_f")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Rating to time" in body and "tools.css" in body and "window.print()" in body
    assert client.get("/scale?pool=all").status_code == 200
    assert client.get("/scale?pool=nope").status_code == 404


def test_rating_i_opens_the_sheet_and_about_names_it():
    exp = read("racecast", "templates", "_explain.html")
    assert '<a class="flag info-i" href="/scale"' in exp
    about = read("racecast", "templates", "about.html")
    assert "<dt>Rating to time</dt>" in about and 'href="/scale"' in about
    assert "a.info-i" in read("racecast", "static", "style.css")


# ------------------------------------------------------------------ #
#  13. the grade rank
# ------------------------------------------------------------------ #

SEASON = {"pool": "hs_m", "sport": "XC", "year": 2025, "state": "CA"}


def test_grade_words_only_for_the_pools_grades():
    assert GR.gradeWords("hs_m", "10") == "HS sophomore boys"
    assert GR.gradeWords("hs_f", "12") == "HS senior girls"
    assert GR.gradeWords("college_m", "so") == "college sophomore men"
    assert GR.gradeWords("ms_f", "7") == "7th-grade girls"
    assert GR.gradeWords("hs_m", "201") is None        # "Class of 2011" keys as junk
    assert GR.gradeWords("hs_m", "so") is None          # a college word in a school pool
    assert GR.gradeWords("pro_m", "12") is None


def test_grade_line_from_one_row():
    row = {"grade_key": "10", "grade_nation": 212, "grade_nation_total": 7064,
           "grade_state": 14, "grade_state_total": 402}
    out = GR.lineFrom(row, SEASON)
    assert out["rank"] == 212 and out["pct"] == "top 3.1%"
    assert out["state"] == "CA" and out["state_rank"] == 14
    assert "grade=10" in out["href"] and "state=CA" in out["state_href"]
    assert "min_races=3" in out["href"]                 # the season's own floor
    # a state of one is a count, not a place
    alone = GR.lineFrom(dict(row, grade_state=1, grade_state_total=1), SEASON)
    assert "state" not in alone
    assert GR.lineFrom(dict(row, grade_nation=None), SEASON) is None


def test_grade_rank_needs_the_columns(monkeypatch):
    monkeypatch.setitem(GR._COLS, "checked", False)
    cur = Cur([[{"n": 0}]])
    assert GR.gradeRank(cur, 1, SEASON) is None
    monkeypatch.setitem(GR._COLS, "checked", False)
    cur = Cur([[{"n": 5}], [{"grade_key": "10", "grade_nation": 3, "grade_nation_total": 50,
                              "grade_state": None, "grade_state_total": None}]])
    got = GR.gradeRank(cur, 1, SEASON)
    assert got["rank"] == 3 and "WHERE person_id = %s AND pool = %s" in cur.sql[-1][0]


def test_the_build_ranks_the_grade_inside_the_board():
    import build_season_ranks as B
    sql = " ".join(B.rankSql(True).split())
    assert "PARTITION BY pool, sport, year, ranked, us, gk ORDER BY mean_rating DESC, person_id" in sql
    assert "PARTITION BY pool, sport, year, ranked, us, state, gk ORDER BY" in sql
    assert "AS grade_nation_total" in sql and "AS grade_state_total" in sql
    assert "NULL::text AS gk" in " ".join(B.rankSql(True, grade_col=False).split())
    tpl = read("racecast", "templates", "athlete.html")
    assert '{% include "_grade_rank.html" %}' in tpl


# ------------------------------------------------------------------ #
#  10. head to head
# ------------------------------------------------------------------ #

def test_accuracy_facts_from_the_scorecard(tmp_path):
    hist = tmp_path / "h.jsonl"
    hist.write_text(json.dumps({"run": "20261008_040000",
                                "metrics": {"lacctic_order_ours": 81.2,
                                            "holdout_race_sd": 0.029}}) + "\n")
    got = ACC.latestByKey(str(hist))
    assert got["lacctic_order_ours"] == "81%" and got["holdout_race_sd"] == "±2.9%"
    assert got["as_of"] == datetime.date(2026, 10, 8)
    facts = H.accuracyFacts(load=lambda: got)
    assert facts["order"] == "81%" and facts["miss"] == "±2.9%"
    assert H.accuracyFacts(load=lambda: None) is None


def test_compare_has_the_predicted_tab(monkeypatch):
    monkeypatch.setitem(A.app.jinja_env.globals, "h2h_accuracy", lambda: {
        "order": "81%", "miss": "±2.9%", "as_of": datetime.date(2026, 10, 8)})
    card = lambda pid, n: {"person_id": pid, "name": n, "short": n, "school": None,  # noqa: E731
                           "state": None, "pool": "college_f", "grade": None,
                           "season_rating": None, "season_rating_hs": None,
                           "best_rating": None, "best_rating_hs": None}
    with A.app.test_request_context("/compare?a=1&b=2"):
        html = A.app.jinja_env.get_template("compare.html").render(
            card_a=card(1, "A"), card_b=card(2, "B"), same=False, missing=[], wins_a=0,
            wins_b=0, ties=0, meetings=[], avg_margin=0, seasons=[], bests=[], brate=None,
            chart=None, has_hs_view=False)
    assert 'data-tab="predict"' in html and 'id="predict"' in html
    assert 'data-dist="6000"' in html                  # a college woman's race
    assert "81%" in html and "h2h.js" in html
    js = read("racecast", "static", "h2h.js")
    assert 'mode: "manual"' in js and "/api/predict/individual" in js


# ------------------------------------------------------------------ #
#  14. the record at a meet
# ------------------------------------------------------------------ #

def test_score_race_winner_is_the_fields():
    actual = {1: 900.0, 2: 905.0, 3: 930.0}
    got = MT.scoreRace(actual, {1: 902.0, 2: 899.0, 3: None})
    assert got["winner_actual"] == 1 and got["winner_pred"] == 2 and not got["picked"]
    assert got["n_predicted"] == 2 and got["median_err"] == pytest.approx(4.0)
    # the winner had no time: a miss, not a race left out
    got = MT.scoreRace(actual, {2: 904.0, 3: 931.0})
    assert got["picked"] is False
    assert MT.scoreRace(actual, {}) is None


def test_summary_counts_runners_not_race_medians():
    rows = [{"meet_id": 10, "race_date": datetime.date(2023, 9, 1), "picked": True,
             "abs_err": [1.0, 2.0, 3.0, 4.0, 50.0], "median_err_pct": 0.4},
            {"meet_id": 10, "race_date": datetime.date(2023, 9, 1), "picked": False,
             "abs_err": [10.0], "median_err_pct": 1.1},
            {"meet_id": 11, "race_date": datetime.date(2024, 9, 1), "picked": True,
             "abs_err": [5.0], "median_err_pct": 0.6}]
    s = MT.summarize(rows)
    assert (s["editions"], s["races"], s["picked"]) == (2, 3, 2)
    assert s["median_err"] == 4.0 and (s["first"], s["last"]) == ("2023", "2024")
    text = MT.sentence(s)
    assert text.startswith("On 2 past editions of this meet (2023 to 2024, 3 races)")
    assert "picked the winner 2 of 3 times; median miss 4.0 s" in text
    assert MT.summarize([]) is None


def test_editions_are_one_name_in_one_state():
    assert MT.editionKey("2025 38th Annual Nike Portland XC!", "or") == "annual nike portland xc|OR"
    assert MT.editionKey("Conference Championship 2024", "TX") != \
        MT.editionKey("Conference Championship 2024", "OH")


def test_track_record_api(client, monkeypatch):
    assert client.get("/api/predict/track_record?meet_id=5&sport=TF").get_json()["available"] is False
    assert client.get("/api/predict/track_record").status_code == 400
    monkeypatch.setattr(MT, "_resolveSource", lambda cur, m, a, s: "anet")
    monkeypatch.setattr(MT, "trackRecord", lambda cur, m, s: {"available": True, "text": "x"})
    ttlcache._store.pop(("track-record", 77, None, None), None)
    r = client.get("/api/predict/track_record?meet_id=77&sport=XC")
    assert r.get_json() == {"available": True, "text": "x"}


def test_build_uses_the_backtests_floor_and_served_path():
    import build_meet_track as BT
    assert BT.MIN_FIELD == 20
    src = read("racecast", "build_meet_track.py")
    assert "P._servedTimes(cur, ids, target)" in src and '"rerun_exact"' in src
    tpl = read("racecast", "templates", "predictions.html")
    assert 'id="meet-record"' in tpl and "meet-record.js" in tpl


# ------------------------------------------------------------------ #
#  4. the goal calculator
# ------------------------------------------------------------------ #

DATA = {"state": "OR", "genders": {"boys": {
    "pool": "hs_m", "time_distance": 5000,
    "venue": {"course_name": "Lane CC", "distance": 5000, "difficulty": 0.008, "canonical_id": 9},
    "divisions": [{"slug": "6a", "short": "6A", "label": "OSAA 6A", "thin": False,
                   "latest": {"year": 2025, "lines": {
                       "qualify": {"mark": 118.6, "n": 96},
                       "podium": {"mark": 131.4, "n": 8}}}}]}}}


def test_state_goal_is_the_published_mark():
    g = G.stateGoal(DATA, "boys", "6a", "podium")
    assert g["rating"] == 131.4 and g["year"] == 2025 and g["whose"] == "its top 8 finishers"
    assert g["href"] == "/what-it-takes/or/6a"
    assert G.stateGoal(DATA, "boys", "6a", "top") is None      # no line on record
    assert G.stateGoal(DATA, "girls", "6a", "qualify") is None
    assert G.goalLabel("podium") == "Top 8 at state"


def test_find_college():
    rows = [{"school": "Portland", "label": "Portland (OR)"},
            {"school": "Portland State", "label": "Portland State (OR)"}]
    assert G.findCollege(rows, "portland (or)")["school"] == "Portland"
    assert G.findCollege(rows, "Portland State")["school"] == "Portland State"
    assert G.findCollege(rows, "portland") ["school"] == "Portland"   # the exact name
    assert G.findCollege(rows, "land") is None                        # two match
    assert G.recruitGoal({"median": 132.7, "school": "Portland", "state": "OR",
                          "label": "Portland (OR)"})["rating"] == 132.7


def test_clocks_at_the_course_and_typical():
    calls = []

    def conv(r, pool, d, diff=None, cid=None, name=None):
        calls.append((r, pool, d, diff))
        return 900.0 * (1 + (diff or 0))
    out = G.clocks(120.0, "hs_m", 5000.0, {"difficulty": 0.02, "canonical_id": 9, "name": "X"},
                   convert=conv, track=lambda r, p, d, s: d / 4.0)
    assert out["course"] == pytest.approx(918.0) and out["typical"] == 900.0
    assert out["t1600"] == 400.0 and out["t3200"] == 800.0
    assert G.clocks(120.0, "hs_m", 5000.0, None, convert=conv,
                    track=lambda *a: None).get("course") is None
    assert G._goalDist(5000) == "5K" and G._goalDist(4800) == "4,800 m"


def test_goal_page_renders(client, monkeypatch):
    import cuts
    monkeypatch.setattr(cuts, "marksFor", lambda cur, st: dict(DATA, computed_at=0))
    monkeypatch.setattr(cuts, "toTime", lambda *a, **k: 1000.0)
    monkeypatch.setattr(G, "clocks", lambda r, p, d, c=None: {"typical": 1000.0, "course": 1010.0,
                                                               "t1600": 290.0, "t3200": 630.0})
    r = client.get("/goal?goal=podium&state=OR&div=6a&g=boys")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "16:50" in body and "Lane CC" in body and "131.4" in body
    assert client.get("/goal").status_code == 200
    assert client.get("/goal?state=ZZ").status_code == 404


def test_new_scripts_parse():
    for f in ("h2h.js", "meet-record.js", "goal.js"):
        r = subprocess.run(["node", "--check", os.path.join(_ROOT, "racecast", "static", f)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
