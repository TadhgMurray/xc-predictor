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
    row = {"finishers": 250, "entrants": (0.85, 31.0, 248), "entrants_teams": (0.9, 2.5, 31),
           "rating": (0.83, 33.0, 248), "rating_teams": (0.88, 2.9, 31),
           "frozen": (0.8, 35.0, 200), "frozen_teams": (0.85, 3.0, 30), "frozen_made": "2026-11-12"}
    S.report({"DI men": row, "DI women": row, "DIII men": dict(row, leak=True)})
    out = capsys.readouterr().out
    assert "0.850" in out and "no results here" in out and "seen this race" in out


def test_only_meets_parse():
    sys.path.insert(0, os.path.join(_ROOT, "tfrrs", "driver"))
    import importlib
    src = open(os.path.join(_ROOT, "tfrrs", "driver", "run_tfrrs.py")).read()
    ns = {}
    # the parser alone: run_tfrrs imports the scraper stack at the top
    start = src.index("def parseOnlyMeets")
    end = src.index("async def runOnly")
    exec("import re\nSPORT_XC, SPORT_TF = 'XC', 'TF'\n" + src[start:end], ns)
    p = ns["parseOnlyMeets"]
    assert p("12345, 67890") == [(12345, "XC"), (67890, "XC")]
    assert p("1:tf 2:XC 1:TF") == [(1, "TF"), (2, "XC")]
    import pytest
    with pytest.raises(ValueError):
        p("abc")


def test_ncaa_meet_ids_classify():
    import ncaa_meet_ids as N
    assert N.classify("NCAA Division III Midwest Region Cross Country Championships") == ("DIII", "regional")
    assert N.classify("NCAA Division I Cross Country Championships") == ("DI", "national")
    assert N.classify("NCAA DI Mountain Region XC Championships") == ("DI", "regional")
    assert N.classify("NCAA Division II Cross Country Championships") is None
    assert N.classify("NCAA Cross Country Championships") == ("DI", "national")
    assert N.classify("2026 NCAA Cross Country Championships") == ("DI", "national")
    assert N.classify("NAIA Cross Country National Championships") is None
    assert N.classify("NCAA D-III Pre-Nationals Championship") is None
    assert N.classify("Hope Invitational") is None
    rows = [{"meet_id": "5", "sport": "XC", "date": "2026-11-14",
             "name": "NCAA Division III East Region Cross Country Championships"},
            {"meet_id": "6", "sport": "XC", "date": "2025-11-15",
             "name": "NCAA Division III East Region Cross Country Championships"},
            {"meet_id": "7", "sport": "TF", "date": "2026-11-14",
             "name": "NCAA Division I Championships"}]
    assert N.pick(rows, 2026) == {("DIII", "regional"): [(5, "2026-11-14",
                                   "NCAA Division III East Region Cross Country Championships")]}
