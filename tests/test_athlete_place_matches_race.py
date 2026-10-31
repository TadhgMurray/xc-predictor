"""An athlete page's place is the race page's (owner, 2026-10-31: "The place
on an athlete's page is not the same as their place on the race page"):
placed finishers only (a DQ keeps its time, not its place), ties in
result_id order, the same feed.

    python -m pytest -q tests/test_athlete_place_matches_race.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from result_status import placedSql, finishedSql, kind      # noqa: E402


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_placed_sql_is_kind_ok():
    sql = placedSql("x")
    assert finishedSql("x") in sql and "('DQ','DSQ','FS')" in sql
    # the tokens it leaves out are exactly the ones kind() does not call 'ok'
    for tok in ("DQ", "DSQ", "FS", "DNF", "DNS", "SCR", "WD", "NT", "NH", "ND", "NM", "DNP"):
        assert kind(tok, 900.0) != "ok" and tok in sql, tok


def test_both_pages_use_the_rule():
    app = read("racecast", "app.py")
    assert "OR (r2.time_seconds = r.time_seconds AND r2.result_id < r.result_id)" in app
    assert "AND r2.source = r.source" in app
    assert "ORDER BY r.time_seconds ASC, r.result_id ASC" in app
