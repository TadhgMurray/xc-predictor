"""The island check (owner, 2026-10-18: Colorado State and Wyoming's 2020
COVID season on top of the all-time board): a race whose field rated above
both its runners' previous and next seasons is pulled down by the smaller
gap. Improvers and lone peaks are left alone.

    python -m pytest -q tests/test_island_check.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))

import numpy as np                                               # noqa: E402

from joint_golive import islandCheck                             # noqa: E402


def _careers(levels, n=12):
    """n runners, seasons 2019-2021, two races a season (race ids 2k and
    2k+1 for season k), one row per runner per race. levels: the
    log level per season, plus a per-runner constant."""
    x, person, season, race = [], [], [], []
    for a in range(n):
        for k, (y, lv) in enumerate(zip((2019, 2020, 2021), levels)):
            for _r in range(2):
                x.append(lv + 0.2 * a)
                person.append(a)
                season.append(y)
                race.append(2 * k + _r)
    return (np.array(x), np.array(person), np.array(season), np.array(race))


def test_an_island_season_is_pulled_down():
    x, p, y, r = _careers((0.0, 0.07, 0.02))
    corr, gp, gn, n = islandCheck(x, p, y, r, 6, np.ones(x.size, bool))
    assert n[2] == 12 and n[3] == 12
    assert abs(corr[2] - 0.05) < 1e-9 and abs(corr[3] - 0.05) < 1e-9   # the smaller gap
    assert not corr[[0, 1, 4, 5]].any()          # no neighbours either side


def test_improvers_are_left_alone():
    x, p, y, r = _careers((0.0, 0.04, 0.08))
    corr, *_ = islandCheck(x, p, y, r, 6, np.ones(x.size, bool))
    assert not corr.any()


def test_a_small_peak_and_a_thin_field_are_left_alone():
    x, p, y, r = _careers((0.0, 0.025, 0.0))     # under the 3% floor
    assert not islandCheck(x, p, y, r, 6, np.ones(x.size, bool))[0].any()
    x, p, y, r = _careers((0.0, 0.07, 0.02), n=6)
    assert not islandCheck(x, p, y, r, 6, np.ones(x.size, bool))[0].any()


def test_capped():
    x, p, y, r = _careers((0.0, 0.2, 0.0))
    assert islandCheck(x, p, y, r, 6, np.ones(x.size, bool))[0][2] == 0.06
