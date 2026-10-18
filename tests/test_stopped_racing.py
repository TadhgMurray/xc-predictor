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
