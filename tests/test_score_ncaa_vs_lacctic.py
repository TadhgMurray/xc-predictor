"""The LACCTiC-style scores (owner, 2026-11-23)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    sys.path.insert(0, _p)

import score_ncaa_vs_lacctic as S  # noqa: E402


def test_perfect_order_scores_one():
    actual = {i: 900.0 + i for i in range(20)}
    rho, off, n = S.runnerScore({i: 800.0 + i for i in range(20)}, actual)
    assert abs(rho - 1.0) < 1e-9 and off == 0 and n == 20


def test_places_off_are_reranked_among_the_scored():
    actual = {i: 900.0 + i for i in range(20)}
    pred = {i: 900.0 + (19 - i) for i in range(20)}       # exactly reversed
    rho, off, _n = S.runnerScore(pred, actual)
    assert rho < -0.99 and off == 10.0


def test_team_order():
    school = {i: "ABCD"[i % 4] + " College" for i in range(40)}
    actual = {i: 900.0 + i for i in range(40)}           # A beats B beats C beats D
    rho, off, n = S.teamScore(actual, actual, school)
    assert n == 4 and abs(rho - 1.0) < 1e-9 and off == 0


def test_report_prints(capsys):
    row = {"finishers": 250, "entrants": (0.85, 31.0, 248), "entrant_teams": (0.9, 2.5, 31),
           "frozen": (0.8, 35.0, 200), "frozen_teams": (0.85, 3.0, 30), "frozen_made": "2026-11-12"}
    S.report({"DI men": row, "DI women": row, "DIII men": dict(row, leak=True)})
    out = capsys.readouterr().out
    assert "0.850" in out and "no results here" in out and "seen this race" in out
