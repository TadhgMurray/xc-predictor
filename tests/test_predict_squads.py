"""Who is on a squad when a past race is run this year (owner, 2026-09-25):
freshmen who have raced for the school are added from the results, and a
runner who has not raced yet this season shows last season's grade, a year
on. No database: a fake cursor answers the one query.

    python -m pytest -q tests/test_predict_squads.py
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import predict as P                                          # noqa: E402
from grade_label import advanceGrade                         # noqa: E402


def test_grades_advance_on_their_own_level():
    assert advanceGrade("10", 1, "hs_m") == "11"
    assert advanceGrade("So", 1, "hs_f") == "11"
    assert advanceGrade("8", 1, "ms_m") == "9"
    assert advanceGrade("JR-3", 1, "college_m") == "SR-4"
    assert advanceGrade("SR-4", 1, "college_m") == "SR-5"
    assert advanceGrade("FR-1", 2, "college_f") == "JR-3"
    assert advanceGrade("12", 1, "hs_m") is None          # graduated
    assert advanceGrade("11", 0, "hs_m") == "11"
    assert advanceGrade(None, 1) is None


class _Cur:
    def __init__(self, rows):
        self.rows, self.sql, self.params = rows, None, None

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.rows


ROWS = [
    {"school": "Campolindo", "person_id": 1, "grade": "9", "pool": "hs_m",
     "rating": 118.44, "n_races": 3, "gender": "M", "name": "New Frosh"},
    {"school": "Campolindo", "person_id": 2, "grade": "9", "pool": None,
     "rating": None, "n_races": 1, "gender": "M", "name": "Unrated Frosh"},
    {"school": "Campolindo", "person_id": 3, "grade": "10", "pool": "hs_f",
     "rating": 120.0, "n_races": 4, "gender": "F", "name": "A Girl"},
    {"school": "Campolindo", "person_id": 4, "grade": "8", "pool": "ms_m",
     "rating": 125.0, "n_races": 2, "gender": "M", "name": "Middle Schooler"},
]


def test_race_entrants_come_off_the_results(monkeypatch):
    import pool_view
    monkeypatch.setattr(pool_view, "stampBoardRows", lambda rows, **k: None)
    cur = _Cur(ROWS)
    got = P._raceEntrants(cur, ["Campolindo"], "XC", 2026, gender="M",
                          levels={"hs"})
    ids = [e["person_id"] for e in got["Campolindo"]]
    assert ids == [1, 2]                    # the girl and the ms boy filtered
    assert got["Campolindo"][0]["rating"] == 118.4
    assert got["Campolindo"][1]["rating"] is None
    assert cur.params["lo"] == "2026-08-01" and cur.params["hi"] == "2027-08-01"
    assert "FROM   results r" in cur.sql and "< 19999" in cur.sql


def test_track_entrants_skip_field_and_relays(monkeypatch):
    import pool_view
    monkeypatch.setattr(pool_view, "stampBoardRows", lambda rows, **k: None)
    cur = _Cur([])
    P._raceEntrants(cur, ["Campolindo"], "TF", 2025)
    assert "results_tf" in cur.sql and "is_relay" in cur.sql and "is_field" in cur.sql


def test_the_level_of_a_grade():
    assert P._levelOfGrade("9") == "hs" and P._levelOfGrade("7") == "ms"
    assert P._levelOfGrade("SO-2") == "college" and P._levelOfGrade("") is None
