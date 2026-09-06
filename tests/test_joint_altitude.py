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
    # free: the prior-side split, about half the truth
    free = js.solveJoint(y, design=D_on, n_outer=6, tilt=False, n_probe=1,
                         alt_prior_pen=0.0)
    assert 0.005 < float(free["altitude_coef"][0]) < k_true, free["altitude_coef"]
    # with the physiology as a loose prior (--altitude-fit), the bridge
    # athletes keep it near the truth; held (the default) it IS the prior
    on = js.solveJoint(y, design=D_on, n_outer=6, tilt=False, n_probe=1,
                       alt_prior_pen=js.ALT_PRIOR_PEN_FIT)
    off = js.solveJoint(y, design=D_off, n_outer=6, tilt=False, n_probe=1)
    k = float(on["altitude_coef"][0])
    assert abs(k - k_true) < 0.005, k
    held = js.solveJoint(y, design=D_on, n_outer=6, tilt=False, n_probe=1)
    assert abs(float(held["altitude_coef"][0]) - js.ALT_PRIOR_MEAN) < 1e-3
    res = slice(n_low, n_low + n_high)
    low = slice(0, n_low)

    def gap(o):
        a = o["ability"]
        return float((a[res] - a_true[res]).mean() - (a[low] - a_true[low]).mean())
    assert abs(gap(on)) < 0.01 and abs(gap(off)) < 0.01, (gap(on), gap(off))
    hi = np.arange(10, 20)
    a_hi = np.linspace(0.2, 1.6, 10)
    lo = np.arange(10)
    # relative to the sea-level cells: the term moves a constant between
    # the cells' mean and the level (a gauge), never a cell's standing
    tot_on = on["delta"][hi] + k * a_hi - on["delta"][lo].mean()
    tot_off = off["delta"][hi] - off["delta"][lo].mean()
    assert np.abs(tot_on - tot_off).max() < 0.01, "the split changed the total"
    print(f"  altitude: k {k:.4f} of {k_true} (the prior's split); abilities "
          f"unchanged ({gap(on):+.3f} vs {gap(off):+.3f}) ... OK")


def test_residents_are_acclimatised_and_a_race_credits_its_field():
    """Issue 192: residents lose only (1 - ALT_ACCLIM) of the altitude
    penalty at home; with home altitude in the exposure their abilities
    line up with the sea-level truth, and the rating's credit at a race
    is the venue less the field's acclimatisation, the same for everyone
    in it."""
    rng = np.random.default_rng(3)
    n_low, n_high, n_bridge = 300, 200, 80
    n_ath = n_low + n_high + n_bridge
    a_true = rng.normal(0, 0.12, n_ath)
    n_cell = 20
    d_true = rng.normal(0, 0.03, n_cell)
    alt_cell = np.r_[np.zeros(10), np.full(10, 1.5)]      # a 2,100 m town
    k_true = js.ALT_PRIOR_MEAN
    home_true = np.r_[np.zeros(n_low), np.full(n_high, 1.5), np.zeros(n_bridge)]
    rows = []
    for i in range(n_ath):
        if i < n_low:
            cells = rng.integers(0, 10, 8)
        elif i < n_low + n_high:
            cells = np.r_[rng.integers(10, 20, 7), rng.integers(0, 10, 1)]
        else:
            cells = np.r_[rng.integers(0, 10, 4), rng.integers(10, 20, 4)]
        for c in cells:
            rows.append((i, int(c)))
    ath = np.array([r[0] for r in rows]); cel = np.array([r[1] for r in rows])
    # a race is a day at one cell: rows grouped by cell, six per day
    order = np.argsort(cel, kind="stable")
    ath, cel = ath[order], cel[order]
    rac = np.unique(cel * 100000 + np.arange(len(rows)) // 6, return_inverse=True)[1]
    exposure = alt_cell[cel] - js.ALT_ACCLIM * home_true[ath]
    y = a_true[ath] + d_true[cel] + k_true * exposure + rng.normal(0, 0.02, len(rows))
    # home from the races, as run_joint derives it
    home = js.homeAltitude(alt_cell[cel], np.ones(len(rows), dtype=bool), ath, n_ath)
    assert np.allclose(home, home_true)
    D_home = js.Design(ath, cel, rac, alt=alt_cell[cel], alt_home=home[ath])
    D_flat = js.Design(ath, cel, rac, alt=alt_cell[cel])
    on = js.solveJoint(y, design=D_home, n_outer=6, tilt=False, n_probe=1)
    off = js.solveJoint(y, design=D_flat, n_outer=6, tilt=False, n_probe=1)
    res, low = slice(n_low, n_low + n_high), slice(0, n_low)

    def gap(o):
        a = o["ability"]
        return float((a[res] - a_true[res]).mean() - (a[low] - a_true[low]).mean())
    assert abs(gap(on)) < 0.01, gap(on)
    assert gap(off) < -0.015, gap(off)          # residents read too fast
    # the rating's credit: per race, venue less half the field's home
    credit = js.altitudeCredit(D_home)
    at_alt = alt_cell[cel] > 0
    assert (credit[~at_alt] == 0).all()
    for r in np.unique(rac[at_alt])[:20]:
        m = rac == r
        assert np.allclose(credit[m], credit[m][0]), "one credit per race"
        field = home[ath[m]].mean()
        assert abs(credit[m][0] - (1.5 - js.ALT_ACCLIM * field)) < 1e-9
    # a resident-only race earns half, a visitor-only race all of it
    assert credit[at_alt].min() >= 1.5 * (1 - js.ALT_ACCLIM) - 1e-9
    assert credit[at_alt].max() <= 1.5 + 1e-9
    # no home known: the old behaviour, byte for byte
    assert (js.altitudeCredit(D_flat) == D_flat.alt).all()
    print(f"  residents' ability gap with home {gap(on):+.4f}, without "
          f"{gap(off):+.4f}; credit range {credit[at_alt].min():.2f}-"
          f"{credit[at_alt].max():.2f} km of 1.5 ... OK")
