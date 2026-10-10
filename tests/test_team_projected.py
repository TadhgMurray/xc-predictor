"""The projected team board: the live season with unraced returners counted.

    python -m pytest -q tests/test_team_projected.py

Owner, 2026-10-10: NYU "heavily underrated" on the team boards because their
A team had not raced -- the meet they were entered in was cancelled and only
the B team had run. The board counts athlete_season rows, which exist only
once someone has raced, so the varsity was simply not on it.

★ THE ROSTER RULE IS roster.py's (the one predictions use), and the ranking
  is the board's own (boards -> rankTeams). These pin both reuses, the
  stored marks, the toggle on the API, and that the default board is
  untouched.
"""
import os
import re
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))

import build_team_season as B                                   # noqa: E402
import roster                                                   # noqa: E402
import teams                                                    # noqa: E402
from team_rank import SQUAD, SCORERS                            # noqa: E402

YEAR = 2026
_NP = len(B.PROJECTED_COLS)
_COL = {c: i for i, c in enumerate(B._COLUMNS)}


def _ath(school, pid, rating, year=YEAR, pool="college_m", state="NY"):
    row = {"sport": "XC", "year": year, "pool": pool, "state": state,
           "school": school, "person_id": pid, "rating": rating}
    for u in B.UNIT_COLS:
        row[u] = None
    return row


def _field():
    """NYU: its B team raced (five at 100); its A team, last season's
    varsity, has not (seven at 130). Rival: five raced at 115."""
    current = ([_ath("NYU", 100 + i, 100.0) for i in range(5)]
               + [_ath("Rival", 200 + i, 115.0) for i in range(5)])
    carried = [dict(_ath("NYU", 300 + i, 130.0 - i, year=YEAR - 1),
                    active_year=YEAR) for i in range(7)]
    return current, carried


def _usa(rows):
    return [r for r in rows if r[_COL["scope"]] == "usa"]


# ---- the merge ------------------------------------------------------- #
def test_merge_flags_carried_rows_and_files_them_under_this_season():
    current, carried = _field()
    merged = B.mergeCarried(current, carried)
    got = [r for r in merged if r.get("carried")]
    assert len(got) == 7
    assert {r["year"] for r in merged} == {YEAR}
    assert not any("active_year" in r for r in merged)
    assert not any(r.get("carried") for r in merged if r["person_id"] < 300)


def test_merge_never_enters_a_runner_twice():
    current, carried = _field()
    # one of the B team also has a carried row: the raced one wins
    carried.append(dict(_ath("NYU", 100, 140.0, year=YEAR - 1),
                        active_year=YEAR))
    merged = B.mergeCarried(current, carried)
    rows100 = [r for r in merged if r["person_id"] == 100]
    assert len(rows100) == 1 and rows100[0]["rating"] == 100.0
    assert not rows100[0].get("carried")


def test_merge_is_ordered_for_boards():
    current, carried = _field()
    current.append(_ath("Smith", 900, 90.0, pool="college_f"))
    merged = B.mergeCarried(current, carried)
    keys = [(r["sport"], r["year"], r["pool"]) for r in merged]
    assert keys == sorted(keys)


# ---- the ranking ----------------------------------------------------- #
def test_unraced_varsity_moves_the_team_up():
    current, carried = _field()
    raced = _usa(r for b in B.boards(sorted(current, key=lambda r: r["pool"]))
                 for r in B.toRows(b))
    proj = _usa(B.projectedRows(current, carried))
    rank = lambda rows, s: next(r[_COL["rank"]] for r in rows
                                if r[_COL["school"]] == s)
    assert rank(raced, "NYU") > rank(raced, "Rival")
    assert rank(proj, "NYU") < rank(proj, "Rival")
    assert all(r[_COL["span"]] == "projected" for r in proj)


def test_projected_board_is_the_season_board_of_the_merged_field():
    """Same code path: rank, points and ratings equal boards() on the merged
    rows -- the projection changes who is in the field, nothing else."""
    current, carried = _field()
    merged = B.mergeCarried(current, carried)
    plain = [r for b in B.boards(merged) for r in B.toRows(b)]
    proj = list(B.projectedRows(current, carried))
    strip = lambda r: r[1:len(r) - _NP]
    assert [strip(r) for r in proj] == [strip(r) for r in plain]


def test_marks_count_flag_and_best_runner():
    current, carried = _field()
    nyu = next(r for r in _usa(B.projectedRows(current, carried))
               if r[_COL["school"]] == "NYU")
    # the seven entered are the seven carried returners (130..124)
    assert nyu[_COL["n_projected"]] == SQUAD
    assert nyu[_COL["projected_flags"]] == "{" + ",".join(["t"] * SQUAD) + "}"
    assert nyu[_COL["best_projected"]] == "t"
    rival = next(r for r in _usa(B.projectedRows(current, carried))
                 if r[_COL["school"]] == "Rival")
    assert rival[_COL["n_projected"]] == 0
    assert rival[_COL["best_projected"]] == "f"


def test_flags_line_up_with_ratings_when_mixed():
    current = [_ath("NYU", 100 + i, 120.0 - i) for i in range(SCORERS)]
    carried = [dict(_ath("NYU", 300, 119.5, year=YEAR - 1), active_year=YEAR)]
    nyu = next(r for r in _usa(B.projectedRows(current, carried))
               if r[_COL["school"]] == "NYU")
    ratings = [float(x) for x in nyu[_COL["ratings"]].strip("{}").split(",")]
    flags = nyu[_COL["projected_flags"]].strip("{}").split(",")
    assert ratings[flags.index("t")] == 119.5
    assert flags.count("t") == 1 == nyu[_COL["n_projected"]]
    assert nyu[_COL["best_projected"]] == "f"


# ---- the default board is untouched ---------------------------------- #
def test_season_and_alltime_rows_carry_no_projection():
    current, _ = _field()
    rows = [r for b in B.boards(current) for r in B.toRows(b)]
    assert rows and all(len(r) == len(B._COLUMNS) for r in rows)
    assert all(r[_COL["span"]] == "season" for r in rows)
    assert all(r[-_NP:] == (None,) * _NP for r in rows)
    at = list(B.toAlltimeRows(("usa", "college_m", "XC", [
        {"school": "NYU", "state": "NY", "year": YEAR, "rank": 1,
         "points": 15, "n_athletes": 5, "top5_mean": 100.0,
         "fifth_rating": 100.0, "best_rating": 100.0,
         "ratings": [100.0] * 5}])))
    assert at[0][-_NP:] == (None,) * _NP and len(at[0]) == len(B._COLUMNS)


def test_alltime_pass_reads_season_rows_only():
    assert "span = 'season'" in B._ALLTIME_SQL


def test_ddl_declares_every_written_column():
    ddl = B._DDL.format(name="team_season_new")
    for c in B._COLUMNS:
        assert re.search(rf'^\s*"?{c}"?\s', ddl, re.M), c


# ---- the roster rule is roster.py's ---------------------------------- #
class _Cur:
    """Scripted: last season's schools, the carry window, the returners."""

    def __init__(self, carrying_meets):
        self.meets = carrying_meets
        self.queries = []
        self.rows = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.queries.append((flat, params))
        if flat.startswith("SELECT DISTINCT school"):
            self.rows = [{"school": "NYU"}, {"school": "Rival"}]
        elif "count(DISTINCT rr.meet_id)" in flat:
            self.rows = [{"school": s, "n": n, "n_top": n, "has_top": True}
                         for s, n in self.meets.items()]
        else:
            self.rows = [dict(_ath(s, i, 120.0, year=YEAR - 1))
                         for i, s in enumerate(params["carrying"])]

    def fetchall(self):
        return self.rows


def test_carried_returners_use_the_carry_window_and_both_exits():
    # NYU's A team has raced once; Rival's has raced CARRY_RACES times
    cur = _Cur({"NYU": 1, "Rival": roster.CARRY_RACES})
    stats = {"carrying": {}}
    params = {"min_races": B.MIN_RACES, "open_from": B.OPEN_FROM,
              "pools": ["college_m"], "states": ["NY"]}
    out = B._carriedFor(cur, "XC", YEAR, params, stats)
    assert {r["school"] for r in out} == {"NYU"}
    assert all(r["active_year"] == YEAR for r in out)
    window = [q for q in cur.queries if "count(DISTINCT rr.meet_id)" in q[0]]
    assert window and window[0][1]["top_n"] == roster.VARSITY_N
    assert window[0][1]["gender"] == "M"
    sql, p = cur.queries[-1]
    assert " ".join(roster.graduatedClause("s").split()) in sql
    assert " ".join(roster.transferredClause("s").split()) in sql
    assert p["carrying"] == ["NYU"] and p["prev"] == YEAR - 1
    assert p["term_keys"] == list(roster.TERMINAL_KEYS)


# ---- the API --------------------------------------------------------- #
class _Args(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)

    def getlist(self, k):
        v = dict.get(self, k)
        return [v] if v is not None else []


def test_default_filters_have_no_projection():
    f, err = teams.parseFilters(_Args(sport="XC", pool="college_m",
                                      year="2026"), default_year=YEAR)
    assert err is None and "projected" not in f and f["span"] == "season"
    assert teams._projCols(f) == ""


def test_projected_defaults_to_the_current_season():
    f, err = teams.parseFilters(_Args(sport="XC", pool="college_m",
                                      projected="1"), default_year=YEAR)
    assert err is None and f["projected"] is True
    assert f["year"] == [YEAR] and f.get("year_defaulted")


def test_projected_refuses_several_seasons():
    f, err = teams.parseFilters(_Args(sport="XC", pool="college_m",
                                      projected="1", year="2025,2026"),
                                default_year=YEAR)
    assert f is None and "one season" in err


class _BoardCur:
    """Answers the existence probe, the count, the column probe and the
    field; records every query."""

    def __init__(self, projected_exists):
        self.exists = projected_exists
        self.queries = []
        self.row = None
        self.rows = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.queries.append((flat, dict(params or {})))
        if "t.span = 'projected'" in flat:
            self.row = {"ok": self.exists}
        elif "count(*)" in flat:
            self.row = {"n": 2}
        elif "information_schema" in flat:
            self.row = {"x": 1}
        else:
            proj = "t.n_projected" in flat
            self.rows = [
                {"school": s, "state": "NY", "pool": "college_m",
                 "sport": "XC", "year": YEAR, "scope": "usa", "rank": i + 1,
                 "points": 15, "n_athletes": 7, "top5_mean": 120.0 - i,
                 "fifth_rating": 120.0 - i, "best_rating": 120.0 - i,
                 "ratings": [120.0 - i] * 7, "best_person_id": None,
                 **({"n_projected": 3 - 3 * i, "projected_flags": [False] * 7,
                     "best_projected": False} if proj else {})}
                for i, s in enumerate(["NYU", "Rival"])]

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


def _parsed(**kw):
    f, err = teams.parseFilters(_Args(sport="XC", pool="college_m", **kw),
                                default_year=YEAR)
    assert err is None
    return f


def test_projected_board_serves_the_projected_span():
    cur = _BoardCur(projected_exists=True)
    rows, info = teams.serveBoard(cur, _parsed(projected="1"))
    assert info["projected"] is True and info["span"] == "projected"
    field = [q for q in cur.queries if "t.ratings" in q[0]][0]
    assert field[1]["span"] == "projected" and "t.n_projected" in field[0]
    assert rows[0]["n_projected"] == 3


def test_projected_falls_back_and_says_so():
    cur = _BoardCur(projected_exists=False)
    rows, info = teams.serveBoard(cur, _parsed(projected="1"))
    assert info["projected"] is False and info["projected_reason"] == "season"
    assert info["span"] == "season"
    assert not any("t.n_projected" in q[0] for q in cur.queries)


def test_default_board_is_unchanged():
    cur = _BoardCur(projected_exists=True)
    rows, info = teams.serveBoard(cur, _parsed(year=str(YEAR)))
    assert "projected" not in info and "projected_reason" not in info
    assert info["span"] == "season"
    assert not any("projected" in q[0] for q in cur.queries)
    assert all("n_projected" not in r for r in rows)


# ---- the page --------------------------------------------------------- #
def test_page_toggle_banner_and_disclaimer():
    js = open(os.path.join(_ROOT, "racecast", "static", "rankings.js"),
              encoding="utf-8").read()
    html = open(os.path.join(_ROOT, "racecast", "templates", "rankings.html"),
                encoding="utf-8").read()
    assert 'id="projected"' in html and 'id="projected-banner"' in html
    assert "Include returners who haven't raced" in html
    assert 'q.set("projected", "1")' in js
    assert 'params.get("projected")' in js
    assert "Projected - not results." in js
    # the disclaimer spells the window out in words; keep it honest
    words = {3: "three"}
    assert f"raced {words[roster.CARRY_RACES]} meets with its top" in js
