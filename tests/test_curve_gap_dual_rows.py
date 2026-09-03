"""The curve's window pin is taken over the rows that identify the level
(2026-09-03): athlete-seasons raced in both sports, sc != 0. A pool with
no dual-sport rows on one side falls back to every row it has; a design
without a sport offset block pins over every row, as before.

    python -m pytest -q tests/test_curve_gap_dual_rows.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import joint_solve as js                                        # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from explain_joint_row import rowTerms                          # noqa: E402


def _design(with_sc=True, single_pool_dual=True):
    # cells 0-1 XC (group 0), 2-3 TF (group 1); one pool (0) unless asked
    group = np.array([0, 0, 1, 1])
    # athlete 0: both sports (Sept XC, May TF); athlete 1: XC only (Nov);
    # athlete 2: TF only, indoor January
    ath = np.array([0, 0, 0, 0, 1, 1, 2, 2])
    cel = np.array([0, 1, 2, 3, 0, 1, 2, 3])
    doy = np.array([250, 260, 130, 140, 320, 325, 10, 20])
    rac = np.arange(8)
    pool = np.zeros(8, dtype=int)
    if not single_pool_dual:
        pool[:] = np.array([0, 0, 0, 0, 1, 1, 1, 1])    # pool 1: no dual rows
    s = np.where(cel >= 2, 0.5, -0.5)
    sbar = np.bincount(ath, weights=s) / np.bincount(ath)
    sc = s - sbar[ath]
    return js.Design(ath, cel, rac, group_of_cell=group,
                     sc=sc if with_sc else None, pool_row=pool, day=doy,
                     first=np.zeros(8, dtype=bool)), sc


def _gapByHand(D, w, amp, rows):
    """mean over track rows of amp*f minus mean over XC rows, on `rows`,
    as a vector over the free curve block."""
    g = np.zeros(D.n_c)
    for sign, side in ((-1.0, D.group_row == 0), (1.0, D.group_row == 1)):
        m = rows & side
        tot = w[m].sum()
        g += sign * (np.bincount(D.c0[m], weights=w[m] * amp[m] * D.w0[m],
                                 minlength=D.n_c)
                     + np.bincount(D.c1[m], weights=w[m] * amp[m] * D.w1[m],
                                   minlength=D.n_c)) / tot
    return g


def test_pin_uses_dual_sport_rows_only():
    D, sc = _design()
    w = np.linspace(0.5, 1.0, 8)
    amp = np.linspace(0.6, 1.4, 8)
    got = js.curveGapVectors(D, w, amp)
    assert len(got) == 1 and got[0] is not None
    dual = np.abs(sc) > 1e-9
    assert dual.tolist() == [True] * 4 + [False] * 4
    np.testing.assert_allclose(got[0], _gapByHand(D, w, amp, dual))
    # and NOT the every-row pin the first live run used
    every = _gapByHand(D, w, amp, np.ones(8, dtype=bool))
    assert not np.allclose(got[0], every)


def test_no_sport_offset_block_pins_over_every_row():
    D, _ = _design(with_sc=False)
    w = np.ones(8)
    amp = np.ones(8)
    got = js.curveGapVectors(D, w, amp)
    np.testing.assert_allclose(got[0], _gapByHand(D, w, amp,
                                                  np.ones(8, dtype=bool)))


def test_pool_without_dual_rows_falls_back_to_all_its_rows():
    D, sc = _design(single_pool_dual=False)
    w = np.ones(8)
    amp = np.ones(8)
    got = js.curveGapVectors(D, w, amp)
    assert len(got) == 2
    p1 = D.pool_row == 1
    np.testing.assert_allclose(got[1], _gapByHand(D, w, amp, p1))


def test_row_terms_reproduce_the_solver_prediction():
    """explain_joint_row.rowTerms: effect + left_out + ability is exactly
    rowPrediction, so the script's split of a rating is the model's."""
    D, sc = _design()
    rng = np.random.default_rng(3)
    b = {"a": rng.normal(0, 0.1, D.n_ath), "d": rng.normal(0, 0.05, D.n_cell),
         "u": rng.normal(0, 0.02, D.n_race), "mu": np.array([0.0, -0.04]),
         "beta": rng.normal(0, 0.03, D.n_ath),
         "c": rng.normal(0, 0.02, D.n_pool * D.n_knot), "r": np.array([0.01])}
    b["c"][js.CURVE_REF_KNOT] = 0.0
    rating = np.array([120.0, 95.0, 140.0])
    h = 1.0 + js.TILT_K * (np.clip(rating, 70, 140)[D.athlete] - 100.0) / 10.0
    amp = js.amplitudeFromRating(rating)[D.athlete]
    pred = js.rowPrediction(b, D, h, amp)
    c = b["c"].reshape(D.n_pool, D.n_knot)
    f_row = D.w0 * b["c"][D.k0] + D.w1 * b["c"][D.k1]
    mean_p = np.array([f_row.mean()])
    npz = {"delta": b["mu"][D.group_of_cell] + b["d"],
           "delta_anchored": b["d"], "race_effect": b["u"], "rating": rating,
           "beta": b["beta"], "rust": b["r"], "curve": c,
           "curve_anchored": c - mean_p[:, None]}
    amp_a = js.amplitudeFromRating(rating)        # per athlete-season
    ability = b["a"] + amp_a * mean_p[0]         # solveJoint's shifted a
    for j in range(D.n):
        t = rowTerms(D, npz, j)
        assert abs((ability[D.athlete[j]] + t["effect"] + t["left_out"])
                   - pred[j]) < 1e-12, j
