"""A runner who has stopped racing sits out the predicted lineup (owner,
2026-10-18: Everett Mosher "is no longer running"): raced this season, then
missed CARRY_RACES (3) of the team's varsity meets since -- the rule that
ends a returner's carry-forward, applied mid-season. A JV-only meet is not
a varsity meet missed.

    python -m pytest -q tests/test_stopped_racing.py
"""
import datetime
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts", "model", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import predict as P                                              # noqa: E402

D = datetime.date
MEETS = [(1, D(2026, 9, 5)), (2, D(2026, 9, 12)), (3, D(2026, 9, 26)),
         (4, D(2026, 10, 3)), (5, D(2026, 10, 10))]
JV = [(90, D(2026, 9, 19)), (91, D(2026, 10, 1)), (92, D(2026, 10, 8))]


def squad(n=8):
    return {"S": [{"person_id": i} for i in range(1, n + 1)]}


def test_missed_the_last_three_varsity_meets():
    ran = {("S", i): set(MEETS) for i in range(2, 9)}
    ran[("S", 1)] = set(MEETS[:2])                 # stopped after meet 2
    assert P.stoppedFrom(squad(), ran, 3) == {1: 3}


def test_two_missed_is_still_on_the_team():
    ran = {("S", i): set(MEETS) for i in range(2, 9)}
    ran[("S", 1)] = set(MEETS[:3])                 # missed 4 and 5 only
    assert P.stoppedFrom(squad(), ran, 3) == {}


def test_jv_meets_are_not_varsity_meets_missed():
    ran = {("S", i): set(MEETS) for i in range(1, 7)}
    ran[("S", 7)] = set(MEETS[:1])                 # stopped after the opener
    ran[("S", 8)] = set(MEETS[:1]) | set(JV)       # JV since: last raced Oct 8
    out = P.stoppedFrom(squad(), ran, 3)
    assert out == {7: 4}                           # 8 missed one varsity meet (Oct 10)


def test_no_race_this_season_is_not_this_rule():
    ran = {("S", i): set(MEETS) for i in range(2, 9)}     # 1 never raced
    assert 1 not in P.stoppedFrom(squad(), ran, 3)


def test_a_finished_season_is_left_alone():
    class Cur:
        def execute(self, *a):
            raise AssertionError("no query for a finished season")
    assert P._stoppedRacing(Cur(), squad(), "XC", 2025, today=D(2026, 10, 18)) == {}


def test_skip_top_parsing():
    import app as A
    assert A._skipTop({"skip_top": "7"}) == 7
    assert A._skipTop({"skip_top": "x"}) == 0
    assert A._skipTop({"skip_top": "99"}) == 20
    assert A._skipTop({}) == 0


def test_a_graded_runner_who_skips_meets_is_still_on_the_team():
    """owner, 2026-11-05: "this shouldn't be a thing in general" -- only an
    ungraded runner (none stored, or inferred) counts as stopped."""
    sq = {"S": [{"person_id": i, "grade": "JR-3"} for i in range(1, 9)]}
    ran = {("S", i): set(MEETS) for i in range(2, 9)}
    ran[("S", 1)] = set(MEETS[:2])                 # rested since meet 2
    assert P.stoppedFrom(sq, ran, 3) == {}
    sq["S"][0] = {"person_id": 1, "grade": "SR-5", "grade_inferred": True}
    assert P.stoppedFrom(sq, ran, 3) == {1: 3}     # ungraded (inferred): stopped


def test_an_ungraded_race_off_the_boards_still_counts(monkeypatch):
    """owner, 2026-11-05: "I still see Isaac Bourne" -- his one ungraded race
    is not in ranking_results; its date comes from the results."""
    import season_year as SY
    sq = {"Loras": [{"person_id": 1, "grade": "SR-5", "grade_inferred": True}]
          + [{"person_id": i, "grade": "JR-3"} for i in range(2, 9)]}

    class Cur:
        def execute(self, sql, args=None):
            self.sql = sql

        def fetchall(self):
            if "FROM results" in self.sql:
                return [{"person_id": 1, "d": "2026-09-04"}]
            if "FROM athlete_season" in self.sql:          # graded this season
                return [{"person_id": i} for i in range(2, 9)]
            return [{"school": "Loras", "person_id": i, "meet_id": m, "day": d}
                    for i in range(2, 9) for m, d in MEETS]
    year = SY.academicYear(D(2026, 10, 18))
    out = P._stoppedRacing(Cur(), sq, "XC", year, today=D(2026, 10, 18))
    assert out.get(1) == 5                    # every varsity meet after Sep 4


def test_a_row_given_last_seasons_grade_is_still_ungraded():
    """owner, 2026-11-05: "Isaac Bourne is still there" -- his row carried
    SR-4 aged to SR-5; the season's record (athlete_season) has no grade."""
    import season_year as SY
    sq = {"Loras": [{"person_id": 1, "grade": "SR-5", "rating": 140.0}]
          + [{"person_id": i, "grade": "JR-3", "rating": 120.0} for i in range(2, 9)]}

    class Cur:
        def execute(self, sql, args=None):
            self.sql = sql

        def fetchall(self):
            if "FROM results" in self.sql:
                return [{"person_id": 1, "d": "2026-09-04"}]
            if "FROM athlete_season" in self.sql:
                return [{"person_id": i} for i in range(2, 9)]
            return [{"school": "Loras", "person_id": i, "meet_id": m, "day": d}
                    for i in range(2, 9) for m, d in MEETS]
    year = SY.academicYear(D(2026, 10, 18))
    assert 1 in P._stoppedRacing(Cur(), sq, "XC", year, today=D(2026, 10, 18))


def test_a_qualified_team_that_loses_a_stopped_runner_sends_its_next(monkeypatch):
    """owner, 2026-11-05: "now Loras only has 5" -- a team keeps its number."""
    entries = ([{"person_id": 1, "school": "Loras", "pool": "college_m", "rating": 140.0}]
               + [{"person_id": i, "school": "Loras", "pool": "college_m", "rating": 120.0 - i}
                  for i in range(2, 8)]
               + [{"person_id": 50, "school": "Solo U", "pool": "college_m", "rating": 130.0}])
    monkeypatch.setattr(P, "_stoppedRacing", lambda cur, by, sport, yr: {1: 5, 50: 4})
    monkeypatch.setattr(P, "_currentSquads", lambda cur, schools, sport, yr, gender=None,
                        levels=None: {"Loras": [{"person_id": i, "rating": 125.0 - i,
                                                 "pool": "college_m"} for i in range(1, 12)]})
    got = P.dropStopped(None, entries, "XC", 2026)
    loras = [e["person_id"] for e in got if e["school"] == "Loras"]
    assert 1 not in loras and len(loras) == 7 and 8 in loras     # next best steps in
    assert 50 not in [e["person_id"] for e in got]               # an individual is not refilled
