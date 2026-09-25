"""The XC saver's athletes row carries the result's name and gender (owner,
2026-09-25: freshmen "Unknown" and rated ~25 points high -- no name, no
gender, the unknown-gender pool). No database: execute_values is captured.

    python -m pytest -q tests/test_athlete_placeholder.py
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


def test_the_placeholder_is_a_real_athlete(monkeypatch):
    calls = []

    class Cur:
        def execute(self, *a, **k):
            pass

    class Conn:
        def cursor(self):
            return Cur()

    monkeypatch.setattr(D, "_ensureResultsStatus", lambda conn, *a: None)
    monkeypatch.setattr(D.psycopg2.extras, "execute_values",
                        lambda cur, sql, rows, **k: calls.append((sql, rows)))
    result = {"IDResult": 1, "AthleteID": 77, "FirstName": "Jane",
              "LastName": "Frosh", "Gender": "F", "SortValue": 1100.0}
    meet = {"ID": 5, "MeetDate": "2026-08-21T00:00:00"}
    D.saveResultsBulk(Conn(), [(result, meet, "Monte Vista", 9)])
    ath = [c for c in calls if "INSERT INTO athletes" in c[0]]
    assert ath, "an athletes row is written"
    sql, rows = ath[0]
    assert rows[0][:4] == (77, "Jane", "Frosh", "F")
    assert "DO UPDATE" in sql and "DO NOTHING" not in sql
    # a real name is never overwritten: only blanks are filled
    assert "WHEN COALESCE(btrim(athletes.first_name), '') = ''" in sql


def test_the_bulk_athlete_save_fills_a_blank_placeholder_too():
    src = open(os.path.join(_ROOT, "scripts", "database.py")).read()
    body = src[src.index("def saveAthletesBulk"):src.index("def saveResultsBulk")]
    assert "ON CONFLICT (athlete_id, school) DO UPDATE SET" in body
