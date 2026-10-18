"""race_day_effect is ONE row per course-day (owner, 2026-10-18,
/athlete/29751562: a race listed 16 times). Under XCP_RACE_KEY=division the
go-live has a race per division; writeRaceDays combines a day's divisions,
weighted by their rows, because every reader joins on (course, distance,
date).

    python -m pytest -q tests/test_race_days_one_per_day.py
"""
import contextlib
import datetime
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("engine", "scripts", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")


def test_divisions_of_one_day_are_one_row(monkeypatch):
    import joint_golive as G
    import speed_ratings_db as S
    import database
    got = {}

    class Cur:
        def execute(self, *a):
            pass

    @contextlib.contextmanager
    def conn():
        class C:
            def cursor(self):
                return contextlib.nullcontext(Cur())
        yield C()
    monkeypatch.setattr(database, "getConn", conn)
    monkeypatch.setattr(database, "swapTable", lambda *a, **k: 1)
    monkeypatch.setattr(S, "loadCanonicalNames", lambda: {})
    monkeypatch.setattr(S, "_splitVenueKey", lambda key, names: (key, 7, 5000))
    monkeypatch.setattr(S, "_escape", lambda s: s)

    def copy(cur, table, cols, rows):
        got["rows"] = list(rows)
        return len(got["rows"])
    monkeypatch.setattr(S, "_copyInto", copy)
    keys = ["Showcase"]
    cell = np.array([0, 0, 0, 0])
    days = np.array([10, 10, 10, 3])               # three divisions one day, one later
    u = np.array([0.02, 0.04, -0.01, 0.05])
    n = np.array([100, 50, 50, 80])
    ce = np.array([0.03, 0.03, np.nan, 0.01])
    G.writeRaceDays((keys, cell, days, u, n, datetime.date(2026, 10, 18), ce))
    rows = sorted(got["rows"], key=lambda r: r[3])
    assert len(rows) == 2                            # one per course-day
    day = rows[0]
    assert day[3] == "2026-10-08" and day[5] == 200
    assert abs(day[4] - (0.02 * 100 + 0.04 * 50 - 0.01 * 50) / 200) < 1e-5
    assert abs(day[6] - 0.03) < 1e-5                 # the course number of the rows that had one
