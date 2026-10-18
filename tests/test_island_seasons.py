"""diag_island_seasons: a roster high against both of its own neighbouring
seasons stands out; steady improvers do not.

    python -m pytest -q tests/test_island_seasons.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import diag_island_seasons as D                                  # noqa: E402


def test_an_island_season_stands_out():
    rows = []
    for i in range(40):                      # steady improvers, +2 a year
        for y, r in ((2019, 100 + i), (2020, 102 + i), (2021, 104 + i)):
            rows.append((i, "college_m", y, "Steady", r))
    for i in range(8):                       # a 2020 roster 6% above both sides
        for y, r in ((2019, 110), (2020, 117.7), (2021, 112)):
            rows.append((100 + i, "college_m", y, "Island", r))
    team, pool_all = D.jumps(rows)
    got = D.excess(team, pool_all, min_runners=5)
    assert got[0][3] == "Island" and got[0][4] == 2020 and got[0][0] > 2.5
    assert all(g[3] != "Steady" or abs(g[0]) < 0.5 for g in got)
