"""As it ran is that season's squad, and grades are aged to the year we are
in (owner, 2026-10-05: "as it ran includes new freshmen", "predicting a race
this year from last year keeps the same grades")."""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "engine", "scripts", "model"):
    sys.path.insert(0, os.path.join(_ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

import pytest  # noqa: E402

P = pytest.importorskip("predict")
from season_year import academicYear  # noqa: E402

NOW = academicYear(datetime.date.today())


@pytest.fixture
def squads(monkeypatch):
    calls = {"entrants": [], "carry": 0}

    def forYear(cur, schools, sport, year, exclude_terminal=False, active_year=None,
                gender=None, levels=None):
        return {"Amherst": [{"person_id": 1, "grade": "11", "pool": "hs_m", "rating": 120.0,
                             "name": "Old Runner"}]}

    def entrants(cur, schools, sport, year, gender=None, levels=None):
        calls["entrants"].append(year)
        return {"Amherst": [{"person_id": 2, "grade": "9", "pool": "hs_m", "rating": 100.0,
                             "name": "New Freshman"}]} if year == NOW else {}

    def carrying(cur, schools, sport, year):
        calls["carry"] += 1
        return []

    import roster
    monkeypatch.setattr(P, "_squadsForYear", forYear)
    monkeypatch.setattr(P, "_raceEntrants", entrants)
    monkeypatch.setattr(P, "_fillNames", lambda cur, rows: None)
    monkeypatch.setattr(roster, "carryingSchools", carrying)
    return calls


def test_as_ran_reads_that_season_and_adds_no_current_freshman(squads):
    out = P._currentSquads(None, ["Amherst"], "XC", NOW - 1, as_ran=True)
    assert [r["person_id"] for r in out["Amherst"]] == [1]
    assert out["Amherst"][0]["grade"] == "11"          # as it was, not aged
    assert squads["entrants"] == [NOW - 1]             # that season's racers only


def test_this_year_still_ages_and_adds_the_freshman(squads):
    out = P._currentSquads(None, ["Amherst"], "XC", NOW - 1)
    ids = [r["person_id"] for r in out["Amherst"]]
    assert 2 in ids and squads["entrants"] == [NOW]
    old = next(r for r in out["Amherst"] if r["person_id"] == 1)
    assert old["grade"] != "11"                        # a year older


class _Cur:
    def __init__(self, rows):
        self.rows, self.n = rows, 0

    def execute(self, sql, params=None):
        self.n += 1

    def fetchall(self):
        return self.rows if self.n == 1 else []


def test_a_finished_seasons_grade_is_aged_on_the_sent_field():
    cur = _Cur([{"person_id": 5, "school": "X", "grade": "11", "pool": "hs_m",
                 "rating": 110.0, "name": "A Runner"}])
    got = P._athleteEntries(cur, [5], "TF", NOW - 1)
    assert got[0]["grade"] != "11"
    cur = _Cur([{"person_id": 5, "school": "X", "grade": "11", "pool": "hs_m",
                 "rating": 110.0, "name": "A Runner"}])
    assert P._athleteEntries(cur, [5], "XC", NOW)[0]["grade"] == "11"


def test_switching_when_rereads_every_race():
    """! loadField only fetches races with no field yet; switching between
    "as it ran" and "this year" must clear the edits first, or the other
    mode's cards (and its Everyone additions) carry over (2026-10-05)."""
    js = open(os.path.join(_ROOT, "racecast", "static", "predictions.js"),
              encoding="utf-8").read()
    i = js.index("The WHEN decides WHO")
    handler = js[i:i + 1500]
    assert handler.index("resetEdits()") < handler.index("loadField()")
