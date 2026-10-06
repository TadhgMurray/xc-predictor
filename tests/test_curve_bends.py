"""The year curve bends when the races say it does (2026-10-06).

The rows-scaled smoothness priced a bend at a knot's worth of ROWS while the
same shift spread over the races' day terms cost only their per-race prior,
so with many runners per race (the real corpus) the curve came out a
straight line and the bend went into the day terms: XC day terms drifted
-0.30% a week through the season. curve_smooth="fit" puts the curvature on
the same footing as the other priors and estimates its sd.

The world: one pool, XC only, a season that improves fast in August-
September and flattens in October-November (a bend), 80 runners per race,
race-day effects with no trend. The test: under "fit" the fitted curve
follows the bend and the day terms carry no season shape; under the old
stated weight the curve is a straight line and the day terms carry the bend.

    python -m pytest -q tests/test_curve_bends.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import joint_solve as js                                        # noqa: E402


def trueCurve(aday):
    """Fast gains to 1 Oct (academic day 60), flat after: a bend."""
    t = np.asarray(aday, dtype=np.float64)
    return np.where(t < 60, 0.04 * (60 - t) / 60.0, 0.0) - 0.005 * t / 120.0


def world(seed=0, n_ath=3000, n_cell=60, per_race=80):
    rng = np.random.default_rng(seed)
    d_true = rng.normal(0, 0.04, n_cell)
    race_cell, race_doy, race_u = [], [], []
    for c in range(n_cell):
        for dd in rng.integers(213, 335, 4):                     # Aug-Nov
            race_cell.append(c); race_doy.append(int(dd))
            race_u.append(rng.normal(0, 0.015))
    race_cell = np.array(race_cell); race_doy = np.array(race_doy)
    race_u = np.array(race_u)
    n_race = len(race_cell)
    a_true = rng.normal(0, 0.1, n_ath)
    # each race draws per_race runners; each runner races several times
    ath = np.concatenate([rng.choice(n_ath, per_race, replace=False)
                          for _ in range(n_race)])
    rac = np.repeat(np.arange(n_race), per_race)
    cel = race_cell[rac]
    doy = race_doy[rac]
    aday = js.academicDay(doy)
    # the curve scaled by ability, as the model has it (amp(a)); see
    # test_joint_year.world
    rating = 100.0 * np.exp(a_true).mean() / np.exp(a_true)
    amp = js.amplitudeFromRating(rating)
    y = (a_true[ath] + d_true[cel] + race_u[rac] + amp[ath] * trueCurve(aday)
         + rng.normal(0, 0.02, len(ath)))
    o = np.argsort(ath, kind="stable")
    D = js.Design(ath[o], cel[o], rac[o], group_of_cell=np.zeros(n_cell, int),
                  pool_row=np.zeros(len(ath), int)[o], day=doy[o],
                  first=np.zeros(len(ath), dtype=bool))
    return y[o], D, race_doy, race_u


def fit(y, D, **kw):
    return js.solveJoint(y, design=D, athlete_pool=np.zeros(D.n_ath, int),
                         n_outer=5, tilt=False, n_probe=8, robust=False,
                         tau_max=None, sigma_u_floor=0.0, curve_gap=0.0, **kw)


def dayBend(out, D, race_doy, race_u):
    """Spread (max - min) of the fitted day terms' error by 20-day bin of
    the season: the shape the curve failed to take, left in the days."""
    u = np.asarray(D.unpack(out["theta"])["u"], dtype=np.float64)
    ad = js.academicDay(race_doy)
    bins = [float(np.mean(u[m] - race_u[m]))
            for lo in range(0, 120, 20)
            for m in [(ad >= lo) & (ad < lo + 20)] if m.any()]
    return max(bins) - min(bins)


def test_fit_bends_and_the_days_stay_flat():
    y, D, race_doy, race_u = world()
    old = fit(y, D, curve_smooth=1.0)
    new = fit(y, D, curve_smooth="fit")
    b_old = dayBend(old, D, race_doy, race_u)
    b_new = dayBend(new, D, race_doy, race_u)
    # the old weight leaves the bend in the day terms; "fit" takes it out
    assert b_old > 2 * b_new, (b_old, b_new)
    assert b_new < 0.004, b_new
    # the curve follows the bend: Aug->Oct gain well above Oct->Dec
    knots = new["curve_knot_days"]
    c = new["curve_anchored"][0]
    gain_early = float(np.interp(0, knots, c) - np.interp(60, knots, c))
    gain_late = float(np.interp(60, knots, c) - np.interp(120, knots, c))
    true_early = float(trueCurve(0) - trueCurve(60))
    assert abs(gain_early - true_early) < 0.006, (gain_early, true_early)
    assert gain_early > 3 * gain_late, (gain_early, gain_late)
    # the old weight: a straight line, the same gain in both halves
    co = old["curve_anchored"][0]
    assert abs((co[0] - co[2]) - (co[2] - co[4])) < 0.004
    assert new["curve_sd"] is not None and old["curve_sd"] is None


def test_a_number_still_means_the_old_weight():
    y, D, _, _ = world(n_ath=800, n_cell=20, per_race=30)
    out = fit(y, D, curve_smooth=1.0)
    rows = np.bincount(D.pool_row, minlength=D.n_pool)
    np.testing.assert_allclose(out["curve_lambda"],
                               np.maximum(rows / float(D.n_knot), 1.0))


def test_curvature_var_keeps_an_empty_pool_at_its_start():
    class _D:
        n_pool, n_knot = 2, 5
        free_grid = np.array([0, 1, 3, 4, 5, 6, 8, 9])
    c = np.array([0.04, 0.02, 0.0, 0.0, 0.0,   0, 0, 0, 0, 0], float)
    got = js.curveCurvatureVar(c, _D, np.zeros(8), np.array([10, 0]),
                               np.array([0.01, 0.01]))
    # pool 0: second differences 0, 0.02, 0 -> mean square 0.02^2 / 3
    np.testing.assert_allclose(got[0], 0.02 ** 2 / 3)
    assert got[1] == 0.01
