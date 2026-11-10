"""scripts/coverage_report.py: the season, the week, the tallies (the SQL
was run against a scratch Postgres with the production tables' DDL).

    python -m pytest -q tests/test_coverage_report.py
"""
import datetime as dt
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import coverage_report as C                                     # noqa: E402


def test_the_season_starts_in_august_and_spring_belongs_to_last_fall():
    assert C.seasonStart(dt.date(2026, 11, 10)) == dt.date(2026, 8, 1)
    assert C.seasonStart(dt.date(2027, 3, 1)) == dt.date(2026, 8, 1)


def test_a_week_is_named_by_its_monday():
    assert C.week("2026-10-25") == "2026-10-19"           # a Sunday
    assert C.week("2026-10-19") == "2026-10-19"
    assert C.week(None) == "?"


def test_tallies_split_have_elsewhere_and_missing():
    rows = [("anet", 1, "a", "2026-10-03", "OR", "have", None),
            ("anet", 2, "b", "2026-10-04", "OR", "missing", "failed"),
            ("anet", 3, "c", "2026-10-04", "WA", "elsewhere", None)]
    assert C.summarise(rows, lambda r: r[4]) == {"OR": [2, 1, 0, 1], "WA": [1, 0, 1, 0]}


def test_the_nightly_step_runs_it():
    with open(os.path.join(_ROOT, "deploy", "nightly_update.sh"), encoding="utf-8") as fh:
        assert "scripts/coverage_report.py --nightly" in fh.read()


class _Cur:
    def __init__(self):
        self.sql, self.args = None, None

    def execute(self, sql, args=()):
        self.sql, self.args = sql, args

    def fetchall(self):
        return []


def test_alive_xc_rests_a_meet_it_tried_and_could_not_land():
    # 2026-11-10: held meets were never marked and took the night's slots again
    sys.path.insert(0, _ROOT)
    from sources import athleticlive_xc as X
    cur = _Cur()
    X.candidates(cur, 100)
    assert "tried_at IS NULL OR am.tried_at < now() - make_interval(days => %s)" in cur.sql
    assert "u.fresh DESC" in cur.sql and cur.args[0] == X.TRY_REST_DAYS
