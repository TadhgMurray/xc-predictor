"""The altitude term (issue 172): a closed community at altitude plus a
few bridge athletes; without the term the altitude cell reads between
the residents and the visitors and residents' abilities sit low; with it
k is recovered, the cell is terrain, and residents' abilities line up
with the sea-level truth.

    python -m pytest -q tests/test_joint_altitude.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import joint_solve as js                                        # noqa: E402


def _world(seed=0):
    rng = np.random.default_rng(seed)
    n_low, n_high, n_bridge = 300, 200, 60
    n_ath = n_low + n_high + n_bridge
    a_true = rng.normal(0, 0.12, n_ath)              # sea-level ability
    # cells 0-9 at sea level, 10-19 spread from 800 m to 2200 m: k is the
    # slope of the cell effect on elevation ACROSS venues, so the world
    # needs elevations that vary, as the corpus's do (Boise, Salt Lake,
    # Denver, Flagstaff, Laramie)
    n_cell = 20
    d_true = rng.normal(0, 0.04, n_cell)             # terrain only
    alt_cell = np.r_[np.zeros(10), np.linspace(0.2, 1.6, 10)]
    k_true = 0.035
    rows = []
    for i in range(n_ath):
        if i < n_low:
            cells = rng.integers(0, 10, 8)
        elif i < n_low + n_high:
            cells = rng.integers(10, 20, 8)
        else:
            cells = np.r_[rng.integers(0, 10, 4), rng.integers(10, 20, 4)]
        for c in cells:
            rows.append((i, int(c)))
    ath = np.array([r[0] for r in rows]); cel = np.array([r[1] for r in rows])
    rac = np.arange(len(rows)) // 6
    y = a_true[ath] + d_true[cel] + k_true * alt_cell[cel] + rng.normal(0, 0.02, len(rows))
    return y, ath, cel, rac, alt_cell[cel], a_true, d_true, k_true, n_low, n_high


def test_altitude_term_is_a_split_of_the_cell_effect():
    """What the term can and cannot do (the finding of 2026-09-04): with
    bridge athletes present, the cells already absorb the altitude penalty
    and residents' abilities are right WITHOUT the term; the term splits
    the cell effect into k * elevation plus terrain, and that split is the
    cell prior's, so k comes back at roughly half the truth here. Its
    value is the direction it gives the prior for a poorly bridged
    community. The total effect per cell, and every ability, must be
    unchanged by it."""
    y, ath, cel, rac, alt, a_true, d_true, k_true, n_low, n_high = _world()
    D_on = js.Design(ath, cel, rac, alt=alt)
    D_off = js.Design(ath, cel, rac)
    on = js.solveJoint(y, design=D_on, n_outer=6, tilt=False, n_probe=1)
    off = js.solveJoint(y, design=D_off, n_outer=6, tilt=False, n_probe=1)
    k = float(on["altitude_coef"][0])
    assert 0.005 < k < k_true + 0.01, k        # the right sign and order
    res = slice(n_low, n_low + n_high)
    low = slice(0, n_low)

    def gap(o):
        a = o["ability"]
        return float((a[res] - a_true[res]).mean() - (a[low] - a_true[low]).mean())
    assert abs(gap(on)) < 0.01 and abs(gap(off)) < 0.01, (gap(on), gap(off))
    hi = np.arange(10, 20)
    a_hi = np.linspace(0.2, 1.6, 10)
    tot_on = on["delta"][hi] + k * a_hi
    tot_off = off["delta"][hi]
    assert np.abs(tot_on - tot_off).max() < 0.01, "the split changed the total"
    print(f"  altitude: k {k:.4f} of {k_true} (the prior's split); abilities "
          f"unchanged ({gap(on):+.3f} vs {gap(off):+.3f}) ... OK")
