"""A season's shared level is not a day (owner, 2026-10-05): the day terms
are centred per (sport, season), runner-weighted, and the shift is uniform
within a race."""
import os
import sys
import types

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

import pytest  # noqa: E402

rj = pytest.importorskip("run_joint")


def test_each_sport_season_is_centred_and_days_keep_their_order():
    rng = np.random.default_rng(3)
    n_race = 40
    race_season = np.repeat([2024, 2025], 20)
    race_sport = np.tile([0, 1], 20)                     # XC, TF alternating
    rows_per = rng.integers(5, 60, n_race)
    race = np.repeat(np.arange(n_race), rows_per)
    D = types.SimpleNamespace(race=race, n_race=n_race)
    season_row, sport_row = race_season[race], race_sport[race]
    level = {(2024, 0): -0.01, (2025, 0): -0.045, (2024, 1): 0.012, (2025, 1): 0.015}
    day = rng.normal(0, 0.01, n_race)
    u = day + np.array([level[(race_season[r], race_sport[r])] for r in range(n_race)])
    uc, shift_row = rj.centreDaysBySeason(u, D, season_row, sport_row)
    for key in level:
        m = (season_row == key[0]) & (sport_row == key[1])
        assert abs(uc[race][m].mean()) < 1e-12           # runner-weighted zero
        assert shift_row[m].std() < 1e-12                 # one shift per group
    # within a group the days keep their differences
    g = (race_season == 2025) & (race_sport == 0)
    assert np.allclose(np.diff(uc[g]), np.diff(u[g]))


def test_the_refit_moves_the_shift_into_the_abilities():
    src = open(os.path.join(_ROOT, "engine", "run_joint.py")).read()
    i = src.index("centreDaysBySeason(u_new, D, season_row, sport_row)")
    block = src[i:i + 900]
    assert "race_effect_row_bracket" in block and "a_new = a_new +" in block
    assert 'os.environ.get("XCP_DAY_CENTRE", "1") != "0"' in src[i - 600:i]
