"""A race page borrows an unlinked, unrated row's person and rating from the
other feed's copy of the same race (owner, 2026-09-25: "on this race page ppl
are unlinked but when you look up their athlete page it has that race with
ratings"). No database: a fake cursor answers the lookups.

    python -m pytest -q tests/test_twin_borrow.py
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                # noqa: E402

pytest.importorskip("flask")
import app as A                                              # noqa: E402


class Cur:
    def __init__(self, twins, canon=555):
        self.twins, self.canon, self.log = twins, canon, []
        self.last = None

    def execute(self, sql, params=None):
        self.log.append(" ".join(sql.split())[:40])
        self.last = sql

    def fetchone(self):
        if "SHOW statement_timeout" in self.last:
            return {"statement_timeout": "0"}
        if "canon_meet_id FROM" in self.last:
            return {"canon_meet_id": self.canon} if self.canon else None
        return None

    def fetchall(self):
        return self.twins


def test_borrows_by_name_and_time_or_by_person(monkeypatch):
    monkeypatch.setattr(A, "_ratingPoolCol", lambda cur, t: "r.rating_pool")
    twins = [{"person_id": 11, "time_seconds": 1540.3, "speed_rating": 114.5,
              "rating_pool": "college_m", "name": "Eli Welch"},
             {"person_id": 12, "time_seconds": 1556.5, "speed_rating": 111.7,
              "rating_pool": "college_m", "name": "Harris Gulbransen"},
             {"person_id": 13, "time_seconds": 1600.0, "speed_rating": 105.0,
              "rating_pool": "college_m", "name": "Far Off"}]
    rows = [{"person_id": None, "time_seconds": 1540.4, "speed_rating": None,
             "name": "Eli Welch"},
            {"person_id": 12, "time_seconds": 1556.5, "speed_rating": None,
             "name": "H. Gulbransen"},
            {"person_id": None, "time_seconds": 1590.0, "speed_rating": None,
             "name": "Far Off"},                      # 10 s away: not the same
            {"person_id": 9, "time_seconds": 1700.0, "speed_rating": 99.0,
             "name": "Already Rated"}]
    cur = Cur(twins)
    assert A._borrowTwins(cur, rows, "results", 1, "tfrrs") == 2
    assert rows[0]["person_id"] == 11 and rows[0]["speed_rating"] == 114.5
    assert rows[1]["speed_rating"] == 111.7            # matched by person
    assert rows[2]["speed_rating"] is None and rows[2]["person_id"] is None
    assert any("SAVEPOINT twin_borrow" in l for l in cur.log)


def test_no_twin_meet_changes_nothing(monkeypatch):
    monkeypatch.setattr(A, "_ratingPoolCol", lambda cur, t: "r.rating_pool")
    rows = [{"person_id": None, "time_seconds": 1540.4, "speed_rating": None,
             "name": "Eli Welch"}]
    assert A._borrowTwins(Cur([], canon=None), rows, "results", 1, "tfrrs") == 0
    assert rows[0]["speed_rating"] is None


def test_the_compiled_link_survives_an_unknown_gender():
    src = open(os.path.join(_ROOT, "racecast", "templates", "meet.html")).read()
    assert "/compiled/{{ g.distance }}/{{ g.gender|urlencode }}" in src
    mc = open(os.path.join(_ROOT, "racecast", "meet_compile.py")).read()
    assert "->> 'div_name') ~* '(women|girls|female)'" in mc
