"""The pure pieces of scripts/diag_winter_gain.py on made-up rows."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import diag_winter_gain as W                                    # noqa: E402


def _row(pid, pool, sport, season, lr, first, last):
    return {"person_id": pid, "pool": pool, "sport": sport, "season": season,
            "lr": lr, "n": 3, "first_date": first, "last_date": last}


def test_annual_gain_is_consecutive_same_sport_same_person():
    rows = [_row(1, "hs_m", "XC", 2023, 4.60, "2023-09-01", "2023-11-10"),
            _row(1, "hs_m", "XC", 2024, 4.63, "2024-09-01", "2024-11-10"),
            _row(1, "hs_m", "TF", 2023, 4.62, "2024-03-01", "2024-05-20"),
            _row(2, "hs_m", "XC", 2022, 4.50, "2022-09-01", "2022-11-01"),
            _row(2, "hs_m", "XC", 2024, 4.70, "2024-09-01", "2024-11-01")]
    g = W.annualGains(rows)
    assert np.allclose(g[("hs_m", "XC")], [0.03])       # person 2 skips a year
    assert ("hs_m", "TF") not in g


def test_gap_days_winter_and_summer():
    rows = [_row(1, "hs_m", "XC", 2023, 4.6, "2023-09-01", "2023-11-10"),
            _row(1, "hs_m", "TF", 2023, 4.6, "2024-03-01", "2024-05-20"),
            _row(1, "hs_m", "XC", 2024, 4.6, "2024-09-01", "2024-11-10")]
    g = W.gapDays(rows)["hs_m"]
    assert g["winter_days"] == 112 and g["summer_days"] == 104
    assert g["n_winter"] == 1 and g["n_summer"] == 1


def test_in_season_gain_is_minus_the_curve_move():
    knots = np.arange(13) * 30.0
    curve = -0.001 * knots                        # fitter by 0.03 per month
    assert abs(W.inSeasonGain(curve, knots, 30.0, 90.0) - 0.06) < 1e-9


def test_split_rules():
    s = W.splitOffSeason(0.04, 112, 104)
    assert abs(s["half"] - 0.02) < 1e-9
    assert abs(s["by duration"] - 0.04 * 112 / 216) < 1e-9
    assert abs(s["summer-heavy (40/60)"] - 0.016) < 1e-9
