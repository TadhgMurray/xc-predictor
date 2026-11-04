"""A race dated in a later season: this year's seniors gone, grades older
(owner, 2026-11-04: "If I set a race for 2027 next season, it should not
include current seniors. It needs to take into account the time better!").
No database.

    python -m pytest -q tests/test_race_season_aging.py
"""
import datetime
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import predict as P                                          # noqa: E402


def test_seasons_ahead_by_the_aug_15_seam():
    assert P.seasonsAhead("2026-11-21", now=2026) == 0
    assert P.seasonsAhead("2027-08-14", now=2026) == 0        # still this season
    assert P.seasonsAhead("2027-09-12", now=2026) == 1
    assert P.seasonsAhead("2028-10-01", now=2026) == 2
    assert P.seasonsAhead("2025-10-01", now=2026) == 0        # a past date: no aging
    assert P.seasonsAhead(None, now=2026) == 0 and P.seasonsAhead("junk", now=2026) == 0


def test_who_will_have_left():
    assert P.goneBy("12", 1, "hs_m") and not P.goneBy("11", 1, "hs_m")
    assert P.goneBy("11", 2, "hs_m") and not P.goneBy("10", 2, "hs_m")
    assert P.goneBy("SR-4", 1, "college_m") and not P.goneBy("JR-3", 1, "college_m")
    assert P.goneBy("JR-3", 2, "college_f")
    assert not P.goneBy("12", 0, "hs_m")                      # this season: nobody
    assert not P.goneBy(None, 1, "hs_m") and not P.goneBy("", 1, "hs_m")   # blank stays


def test_a_squad_a_season_on():
    squads = {"Jesuit": [{"person_id": 1, "grade": "12", "pool": "hs_m"},
                         {"person_id": 2, "grade": "11", "pool": "hs_m"},
                         {"person_id": 3, "grade": None, "pool": "hs_m"}],
              "Tufts": [{"person_id": 4, "grade": "SR-4", "pool": "college_m"},
                        {"person_id": 5, "grade": "SO-2", "pool": "college_m"}]}
    P._ageForward(squads, 1, 2027)
    assert [(r["person_id"], r["grade"]) for r in squads["Jesuit"]] == [(2, "12"), (3, None)]
    assert [(r["person_id"], r["grade"]) for r in squads["Tufts"]] == [(5, "JR-3")]


def test_the_race_date_reaches_the_squads(monkeypatch):
    seen = []
    monkeypatch.setattr(P, "_ageForward", lambda squads, years, season: seen.append((years, season)))
    from season_year import academicYear
    now = academicYear(datetime.date.today())
    with P.raceDate(f"{now + 1}-10-01"):
        assert P.seasonsAhead(P._RACE_DATE.get(), now) == 1
    assert P._RACE_DATE.get() is None                          # and is let go after
