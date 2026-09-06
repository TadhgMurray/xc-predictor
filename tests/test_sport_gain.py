"""The winter gain per ability (issue 194): the go-live's shift on track
rows makes the dual-sport page gap in each band the stated one, the
band shifts interpolate by rating, thin bands borrow, and the
conversions page applies the same shift.

    python -m pytest -q tests/test_sport_gain.py
"""
import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.modules.setdefault("database", types.SimpleNamespace(getConn=None))
import joint_solve as js                                        # noqa: E402
import conversions as cv                                        # noqa: E402


def _world(seed=0, n_ath=900):
    """Athlete-seasons in three bands, two pools; every one races both
    sports; the track rows read 0.7% SLOWER than XC (the 2024+ reading),
    plus a per-athlete sport preference."""
    rng = np.random.default_rng(seed)
    rating = np.r_[rng.uniform(80, 100, n_ath // 3),
                   rng.uniform(106, 119, n_ath // 3),
                   rng.uniform(121, 140, n_ath - 2 * (n_ath // 3))]
    pool = rng.integers(0, 2, n_ath)
    rows = []
    for i in range(n_ath):
        pref = rng.normal(0, 0.01)
        for _ in range(5):
            rows.append((i, 0, rng.normal(0, 0.02)))
        for _ in range(4):
            rows.append((i, 1, 0.007 + pref + rng.normal(0, 0.02)))
    ath = np.array([r[0] for r in rows])
    sport = np.array([r[1] for r in rows])
    log_adj = np.array([r[2] for r in rows]) - 0.3 * (rating[ath] - 100) / 100
    return log_adj, sport, ath, rating, pool


def _gapByBand(log_adj, sport, ath, rating, pool, n_pool=2):
    _s, gap, n = js.sportGainShift(log_adj, sport, ath, rating, pool, n_pool,
                                   (0.0, 0.0, 0.0))
    return gap, n


def test_shift_makes_each_band_read_the_stated_gain():
    log_adj, sport, ath, rating, pool = _world()
    gains = (0.03, 0.02, 0.03)
    shift, gap, n = js.sportGainShift(log_adj, sport, ath, rating, pool, 2, gains)
    assert (n >= 100).all(), n
    assert np.allclose(gap, 0.007, atol=0.004), gap           # what it read
    assert np.allclose(shift, gap + np.array(gains)[None, :]), shift
    # apply it as the go-live does: the band means land on -gain exactly
    # when banded at the anchors (the interpolation only smooths between)
    row_shift = js.sportGainRow(rating[ath], pool[ath], shift)
    after = log_adj - np.where(sport == 1, row_shift, 0.0)
    gap2, _n = _gapByBand(after, sport, ath, rating, pool)
    for b, g in enumerate(gains):
        assert np.allclose(gap2[:, b], -g, atol=0.006), (b, gap2[:, b])
    # and it is continuous: a 119.9 and a 120.1 get shifts a hair apart
    r = np.array([119.9, 120.1])
    s2 = js.sportGainRow(r, np.zeros(2, dtype=int), shift)
    assert abs(s2[0] - s2[1]) < 0.001
    # XC rows are untouched
    assert (after[sport == 0] == log_adj[sport == 0]).all()


def test_thin_band_borrows_and_no_gain_is_no_shift():
    log_adj, sport, ath, rating, pool = _world()
    # nobody above 120 in pool 1
    rating = rating.copy()
    rating[(pool == 1) & (rating > 120)] = 110.0
    shift, gap, n = js.sportGainShift(log_adj, sport, ath, rating, pool, 2,
                                      (0.03, 0.02, 0.03))
    assert n[1, 2] == 0 and n[1, 1] > 0
    assert shift[1, 2] == shift[1, 1], "the top borrows the middle"
    assert shift[0, 2] != shift[0, 1]
    # stated gains of exactly the realised gap leave zero shift
    z, _g, _n = js.sportGainShift(log_adj, sport, ath, rating, pool, 2,
                                  tuple(-gap[0]))
    assert np.allclose(z[0], 0.0)


def test_conversions_apply_the_same_shift():
    cv._gain["map"] = {("hs_m", "TF"): [(90.0, 0.01), (112.0, 0.02), (130.0, 0.04)]}
    cv._gain["at"] = 1e18
    assert cv.sport_gain("hs_m", "TF", 80) == 0.01
    assert cv.sport_gain("hs_m", "TF", 140) == 0.04
    assert abs(cv.sport_gain("hs_m", "TF", 121) - 0.03) < 1e-9
    assert cv.sport_gain("hs_m", "XC", 121) == 0.0
    assert cv.sport_gain("hs_f", "TF", 121) == 0.0          # no row: none applied
    cv._scale["map"] = {("hs_m", "XC"): (1247.6, -0.025, -0.0253),
                        ("hs_m", "TF"): (1247.6, -0.055, -0.0253)}
    cv._scale["at"] = 1e18
    cv._offsets["map"] = {}
    cv._offsets["at"] = 1e18
    # a track time still converts to itself on the shifted scale
    ctx = {"distance": 3200.0, "pool": "hs_m", "sport": "TF"}
    norm = cv._norm_from_time(541.1, 3200.0, "hs_m", sport="TF", chosen=None)
    back = cv.normalized_to_time(norm, ctx)
    assert abs(back - 541.1) < 0.05, back
    # and rates HIGHER than with no shift, by the shift at its rating
    cv._gain["map"] = {}
    norm0 = cv._norm_from_time(541.1, 3200.0, "hs_m", sport="TF", chosen=None)
    assert norm < norm0
