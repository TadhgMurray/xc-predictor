# A shared-name school page's Races tab lists its own institution's meets
# (owner, 2026-10-28: the University of Washington's tab listed the middle
# school's time trials). school.schoolMeets, level=.
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

import school as S                                             # noqa: E402


class _Cur:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return []

    def fetchmany(self, n):
        return []


def test_the_level_scopes_the_meets(monkeypatch):
    monkeypatch.setattr(S, "fetchCapped", lambda cur, limit: [])
    cur = _Cur()
    S.schoolMeets(cur, "Washington", "XC", state="WA", level="college")
    sql, params = cur.calls[-1]
    assert "split_part(split_part(rr.pool, '|', 1), '_', 1)" in sql
    assert params["level"] == "college"
    cur = _Cur()
    S.schoolMeets(cur, "Washington", "XC", state="WA")
    assert "split_part" not in cur.calls[-1][0]
