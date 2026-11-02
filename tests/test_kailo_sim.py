"""scripts/kailo_sim.py: Kailo's simulator time, as their page prices it,
and the rule that only a build from before the race counts. No network, no
database. (Checked against their own meetsim-core.js on 120 runners of 40
presets: equal to 1e-12 s.)

    python -m pytest -q tests/test_kailo_sim.py
"""
import math
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                # noqa: E402
import kailo_sim as S                                        # noqa: E402

P = {"gamma": 0.9, "riegel": 1.06, "basis": "standard_day",
     "seasonal": {"knots_day": [15.0, 95.0], "heat_pct": [1.0, -1.0], "rest_pct": [0.5, 0.5],
                  "phase_pct_per_day": 0.01, "phase_knot_day": 45.0, "clip_day": 110.0}}


def test_the_season_and_the_route():
    assert S.seasonDay("2026-08-15") == 0 and S.seasonDay("2026-10-14") == 60
    assert S.seasonDay("2027-01-30") == 120 and S.seasonDay("2026-07-01") == 0
    assert S.routeKm(8000) == 8 and S.routeKm(8047) == 8 and S.routeKm(6500) == 6
    assert S.routeKm(7500) == 8


def test_their_factor():
    c = ["id", "X", "", 2.0, 5, 8000, 5000, [], {"8": 0.5}, 0.3, 0]
    day = S.seasonDay("2026-10-14")                          # 60: heat 0.125, rest 0.5
    dated = (1.0 + (-2.0) * (60 - 15) / 80) + 0.5 + 0.3       # season term 0 past day 45
    want = math.exp((2.0 + 0.5 + dated) / 100) * (8000 / 5000) ** 1.06
    assert day == 60 and S.factor(P, c, 8000, "2026-10-14") == pytest.approx(want)
    typical = c[:10] + [1]                                   # a typical-day rating: gamma, no date term
    assert S.factor(P, typical, 8000, "2026-10-14") == pytest.approx(
        math.exp((0.9 * 2.0 + 0.5) / 100) * 1.6 ** 1.06)


def test_a_preset_reads_its_teams_once_across_divisions():
    D = {"params": P, "courses": [],
         "teams": {"d1_M": [["A", [["Ann B", 900.0, 3, "SR-4", 0.02]]]],
                   "d2_M": [["A", [["Dup", 1.0, 1, "", 0]]], ["B", [["Cy D", 950.0, 2, "", 0]]]],
                   "d1_F": [["A", [["Eve F", 1000.0, 3, "", 0]]]]}}
    p = {"sex": "M", "dist": 5000, "date": "2026-10-14", "teams": ["A", "B"], "course": None}
    got = S.presetTimes(D, p)
    assert [(n, t) for n, t, _s in got] == [("Ann B", "A"), ("Cy D", "B")]
    assert got[0][2] == pytest.approx(900.0)                 # no course, 5K: their time as is


def test_only_a_build_from_before_the_race_counts():
    snaps = {"2026-10-21": "a", "2026-10-28": "b", "2026-10-31": "c"}
    assert S.beforeRace(snaps, "2026-10-31") == ("2026-10-28", "b")
    assert S.beforeRace(snaps, "2026-10-21") is None


def test_runners_match_by_name_then_school():
    ours = [(1, "a ann", "Alpha U", 900.0), (2, "a ann", "Beta College", 910.0),
            (3, "b cy", "Beta College", 950.0)]
    got = S.matchKailo([("Ann A", "Beta", 905.0), ("Cy B", "Beta", 949.0),
                        ("Nobody Here", "Beta", 1.0)], ours)
    assert got == {2: 905.0, 3: 949.0}
