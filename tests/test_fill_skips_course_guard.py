"""fill_ratings._sqlFor (engine review 2026-10-24): an XC row the go-live's
course guard left unrated (course_guard_unrated) is not priced by the fill."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import fill_ratings as F                                         # noqa: E402


def _sql(monkeypatch, have):
    monkeypatch.setattr(F.B, "_sourceSql",
                        lambda conn, sport: F.B._collegeFirstSql(None, F.B._SQL[sport])
                        .replace("__IMPOSSIBLE__", "").replace("__RATING_POOL__", "r.rating_pool"))
    monkeypatch.setattr(F, "_teamColumns", lambda table: "NULL::bigint AS team_id, NULL::text AS team_slug")
    monkeypatch.setattr(F, "_hasTable", lambda conn, name: have)
    return F._sqlFor("XC", conn=object())


def test_the_guarded_rows_are_left_out(monkeypatch):
    sql = _sql(monkeypatch, True)
    assert "course_guard_unrated g WHERE g.result_id = r.result_id" in sql
    assert "r.speed_rating IS NULL AND r.normalized_time > 0" in sql


def test_no_table_no_clause(monkeypatch):
    assert "course_guard_unrated" not in _sql(monkeypatch, False)
