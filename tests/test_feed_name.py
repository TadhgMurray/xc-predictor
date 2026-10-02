"""The feed's name is kept on the result row (owner, 2026-10-02: "I can
promise you athletic net has the names"): a row with no registered athlete
lost its name at save time. No database: execute_values is captured.

    python -m pytest -q tests/test_feed_name.py
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import pytest                                                # noqa: E402

psycopg2 = pytest.importorskip("psycopg2")
import database as D                                         # noqa: E402


def test_feed_name():
    assert D._feedName({"FirstName": "Jane", "LastName": "Frosh"}) == "Jane Frosh"
    assert D._feedName({"FirstName": " Jane ", "LastName": None}) == "Jane"
    assert D._feedName({}) is None
    relay = {"FirstName": "Ann Lee<BR>Bo Ray<br/>Cy Ono<BR>Di Fox", "LastName": ""}
    assert D._feedName(relay, True) == "Ann Lee, Bo Ray, Cy Ono, Di Fox"


class _Cur:
    def execute(self, *a, **k):
        pass


class _Conn:
    def cursor(self):
        return _Cur()


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(D, "_ensureResultsStatus", lambda conn, *a: None)
    monkeypatch.setattr(D.psycopg2.extras, "execute_values",
                        lambda cur, sql, rows, **k: calls.append((sql, rows)))
    return calls


def _cols(sql):
    return [c.strip() for c in sql[sql.index("(") + 1:sql.index(")")].split(",")]


def test_track_row_without_an_athlete_keeps_its_name(monkeypatch):
    calls = _capture(monkeypatch)
    res = {"IDResult": 1, "AthleteID": None, "FirstName": "Jane", "LastName": "Frosh",
           "SortInt": 600000}
    D.saveResultsTFBulk(_Conn(), [(res, {"ID": 5, "MeetDate": "2025-04-01T00:00:00"},
                                   9, 3, "3200m", 0, "Monte Vista", 0)])
    sql, rows = [c for c in calls if "INSERT INTO results_tf" in c[0]][0]
    cols = _cols(sql)
    assert len(cols) == len(rows[0])
    assert rows[0][cols.index("athlete_name")] == "Jane Frosh"
    # a fill, never an overwrite
    assert "COALESCE(NULLIF(btrim(results_tf.athlete_name), '')" in sql


def test_xc_row_keeps_its_name(monkeypatch):
    calls = _capture(monkeypatch)
    res = {"IDResult": 1, "AthleteID": 77, "FirstName": "Jane", "LastName": "Frosh",
           "Gender": "F", "SortValue": 1100.0}
    D.saveResultsBulk(_Conn(), [(res, {"ID": 5, "MeetDate": "2026-08-21T00:00:00"},
                                 "Monte Vista", 9)])
    sql, rows = [c for c in calls if "INSERT INTO results (" in c[0]][0]
    cols = _cols(sql)
    assert len(cols) == len(rows[0])
    assert rows[0][cols.index("athlete_name")] == "Jane Frosh"
    assert "COALESCE(NULLIF(btrim(results.athlete_name), '')" in sql
