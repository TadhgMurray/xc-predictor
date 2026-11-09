"""The site-audit gates of 2026-11-09: the course page's rating lists hold
only rows the boards admit and rows with an HS-equivalent (Hereford HS's
elem_m Sherwood 1986-88 rows at 168), and "On this day" never picks a race
from a one-race season (Amanda White, 1990-91, state meets only)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app as A                                               # noqa: E402
import record_books as RB                                     # noqa: E402


class _Cur:
    def __init__(self, answers):
        self.answers, self.sql, self._last = answers, [], ""

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self._last = sql

    def fetchall(self):
        for marker, rows in self.answers:
            if marker in self._last:
                return rows
        return []


def _ctx():
    return {"rating_bests": {
                "M": [{"result_id": 1, "speed_rating": 168.2, "hs_rating": None},
                      {"result_id": 2, "speed_rating": 152.4, "hs_rating": 152.4},
                      {"result_id": 3, "speed_rating": 150.0, "hs_rating": 150.0}],
                "F": []},
            "team_rating": {"M": [], "F": []}}


def test_a_course_list_drops_rows_with_no_hs_value_and_rows_off_the_boards():
    cur = _Cur([("FROM ranking_results WHERE result_id", [{"result_id": 2},
                                                          {"result_id": 1}])])
    out = A._gateCourseRatings(cur, _ctx())
    assert [r["result_id"] for r in out["rating_bests"]["M"]] == [2]


def test_a_list_with_no_hs_values_at_all_is_left_alone():
    ctx = _ctx()
    for r in ctx["rating_bests"]["M"]:
        r["hs_rating"] = None
    cur = _Cur([("FROM ranking_results WHERE result_id",
                 [{"result_id": 1}, {"result_id": 2}, {"result_id": 3}])])
    out = A._gateCourseRatings(cur, ctx)
    assert len(out["rating_bests"]["M"]) == 3


def test_the_gate_never_breaks_the_page():
    class Boom(_Cur):
        def execute(self, sql, params=None):
            if "ranking_results" in sql:
                raise RuntimeError("no table")

    class Conn:
        def rollback(self):
            pass
    cur = Boom([])
    cur.connection = Conn()
    ctx = _ctx()
    assert A._gateCourseRatings(cur, ctx) is ctx


def test_on_this_day_skips_a_one_race_season(monkeypatch):
    rows = [{"person_id": 9, "pool": "hs_f", "sport": "XC", "year": 1991,
             "race_date": "1991-11-09", "rating": 152.3},
            {"person_id": 8, "pool": "hs_m", "sport": "XC", "year": 2015,
             "race_date": "2015-11-09", "rating": 147.0}]
    monkeypatch.setattr(RB, "loadBook", lambda cur, kind, key: {"rows": rows})
    cur = _Cur([("athlete_season", [{"person_id": 8, "pool": "hs_m",
                                     "sport": "XC", "year": 2015}])])
    import datetime
    pick = RB.onThisDay(cur, datetime.date(2026, 11, 9))
    assert pick["person_id"] == 8


def test_a_forecast_finds_the_result_race_its_runners_ran():
    import meet_recap as MR
    stored = [{"div_id": 1, "runners": [{"person_id": p} for p in range(10)]},
              {"div_id": 2, "runners": [{"person_id": p} for p in range(10, 20)]},
              {"div_id": 3, "runners": [{"person_id": p} for p in range(40, 50)]},
              {"div_id": 7, "runners": [{"person_id": 1}]}]
    actual = {101: [{"person_id": p} for p in range(12, 30)],
              102: [{"person_id": p} for p in range(0, 8)],
              7: [{"person_id": 99}]}
    got = MR.matchDivisions(stored, actual)
    assert got == {1: 102, 2: 101, 7: 7}          # 3 ran nowhere: unmatched


def test_an_unservable_sort_falls_back_to_rating_not_an_error():
    from werkzeug.datastructures import MultiDict
    import rankings as R

    class C:
        def execute(self, sql, params=None):
            self.last = params

        def fetchone(self):
            return {"ok": False}             # no as_sort_* index built yet
    f, err = R.parseFilters(MultiDict({"board": "ability", "pool": "hs_m",
                                       "sport": "XC", "sort": "name"}))
    assert err is None
    assert not R.sortServed(C(), f)
    assert R.servedSorts(C(), f) == ["rating", "best"]
    f2, _ = R.parseFilters(MultiDict({"board": "ability", "pool": "hs_m",
                                      "sport": "XC", "sort": "name", "state": "CA"}))
    assert R.sortServed(C(), f2)

    class Built(C):
        def fetchone(self):
            return {"ok": True}
    R._IDX_SEEN.clear()
    f3, _ = R.parseFilters(MultiDict({"board": "ability", "pool": "hs_m",
                                      "sport": "XC", "sort": "grade"}))
    assert R.sortServed(Built(), f3)
    R._IDX_SEEN.clear()
