# Project: xc-predictor / tests
# File:    test_bracket.py
# Purpose: engine/bracket.py is the one same-athlete window comparison. It
#          must equal a brute-force loop row for row, take the curve out
#          exactly as the Design puts it in, and scale like a sort.
#
#   python -m pytest -q tests/test_bracket.py
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket as bk                                           # noqa: E402
import joint_solve as js                                       # noqa: E402


def _brute(z, season, sport, cell, days, window):
    out = np.full(z.size, np.nan)
    for i in range(z.size):
        if cell[i] < 0:
            continue
        m = ((season == season[i]) & (sport == sport[i]) & (cell != cell[i])
             & (cell >= 0) & (np.abs(days - days[i]) <= window))
        if m.any():
            out[i] = z[i] - z[m].mean()
    return out


def test_the_window_bracket_equals_the_brute_force_loop():
    rng = np.random.default_rng(4)
    n = 6000
    season = rng.integers(0, 400, n)
    sport = rng.integers(0, 2, n)
    cell = rng.integers(-1, 25, n)                 # some rows without a cell
    days = rng.integers(0, 120, n).astype(np.float64)
    z = rng.normal(0, 0.05, n)
    got, n_other = bk.windowBracket(z, season, sport, cell, days, 21)
    want = _brute(z, season, sport, cell, days, 21)
    assert np.array_equal(np.isnan(got), np.isnan(want))
    ok = ~np.isnan(want)
    assert np.allclose(got[ok], want[ok], atol=1e-12)
    assert (n_other[cell < 0] == 0).all() and (n_other[ok] > 0).all()
    # the window is inclusive at both ends, and a row never brackets itself
    assert (n_other[ok] <= np.bincount(season * 2 + sport)[(season * 2 + sport)[ok]] - 1).all()


def test_the_curve_on_rows_matches_the_design():
    rng = np.random.default_rng(7)
    n_pool, n, n_knot = 2, 500, js.CURVE_N_KNOTS
    pool = rng.integers(0, n_pool, n)
    doy = rng.integers(1, 366, n)
    c = rng.normal(0, 0.03, (n_pool, n_knot))
    c[:, js.CURVE_REF_KNOT] = 0.0
    npz = {"curve": c, "curve_knot_days": np.array([js.CURVE_KNOT_DAYS])}
    ath = np.arange(n); cell = np.zeros(n, int); race = np.zeros(n, int)
    D = js.Design(ath, cell, race, pool_row=pool, day=doy, n_pool=n_pool)
    grid = c.reshape(-1)
    want = D.w0 * grid[D.k0] + D.w1 * grid[D.k1]
    got = bk.curveOnRows(npz, pool, doy)
    assert np.allclose(got, want, atol=1e-12)
    rating = rng.uniform(80, 150, n)
    got_a = bk.curveOnRows(npz, pool, doy, rating)
    assert np.allclose(got_a, want * js.amplitudeFromRating(rating), atol=1e-12)
    # a pool the file does not know contributes nothing
    assert bk.curveOnRows(npz, np.full(n, 5), doy).sum() == 0.0


def test_bracket_rows_takes_the_curve_out_and_scales_like_a_sort():
    rng = np.random.default_rng(11)
    n_ath, per, n_cell = 250_000, 8, 3000
    ath = np.repeat(np.arange(n_ath), per)
    n = ath.size
    year = np.full(n, 2024)
    doy = rng.integers(230, 330, n)                 # an autumn
    days = (330 - doy).astype(np.float64)
    # ! COURSES BELONG TO A PART OF THE SEASON, as they do in life: the
    #   first thousand host early races, the last thousand late ones. That
    #   is what makes the raw bracket carry the fitness curve into the
    #   course and the corrected one not.
    band = np.digitize(doy, [263, 297])             # early / mid / late
    course = rng.integers(0, n_cell // 3, n) + (n_cell // 3) * band
    # knots are 30 academic days apart from 1 August: knot 3 is 30 October,
    # inside this autumn, so the ramp from knot 2 to 3 is what the rows see
    c = np.zeros((1, js.CURVE_N_KNOTS)); c[0, 3:] = -0.03   # fitter after late October
    curve_row = bk.curveOnRows({"curve": c}, np.zeros(n, int), doy)
    true_d = rng.normal(0, 0.04, n_cell)
    norm = np.exp(rng.normal(0, 0.2, n_ath)[ath] + true_d[course] + curve_row
                  + rng.normal(0, 0.02, n))
    cols = {"athlete": ath, "year": year, "course": course, "days": days, "doy": doy,
            "sport": np.zeros(n, int), "norm": norm,
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)],
            "course_keys": [f"XC:{i}:d5000" for i in range(n_cell)]}
    t0 = time.time()
    with_c = bk.bracketRows(cols, {"curve": c}, window=45, use_curve=True)
    without = bk.bracketRows(cols, None, window=45)
    took = time.time() - t0
    assert took < 60, took
    b1, b0 = with_c["bracket"], without["bracket"]
    ok = np.isfinite(b1) & np.isfinite(b0)
    assert ok.mean() > 0.9
    # per cell, the curve-corrected bracket recovers the planted difficulty
    # (relative), and does so better than the raw one, which carries the
    # fitness gained inside the window
    def per_cell(b):
        s = np.bincount(course[ok], weights=b[ok], minlength=n_cell)
        k = np.bincount(course[ok], minlength=n_cell)
        return s / np.maximum(k, 1)
    e1 = per_cell(b1) - true_d; e0 = per_cell(b0) - true_d
    e1 -= e1.mean(); e0 -= e0.mean()
    assert np.sqrt(np.mean(e1 ** 2)) < np.sqrt(np.mean(e0 ** 2))
    assert np.corrcoef(per_cell(b1), true_d)[0, 1] > 0.9
    # the raw bracket reads the late-season courses as easy (their runners
    # were fitter than in the races they are compared with); the corrected
    # one does not
    late = np.arange(n_cell) >= 2 * (n_cell // 3)
    assert e0[late].mean() < -0.004, e0[late].mean()
    assert abs(e1[late].mean()) < 0.003, e1[late].mean()
