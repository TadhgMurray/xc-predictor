# Project: xc-predictor / tests
# File:    test_shared_terms.py
# Purpose: The three shared terms added on 2026-09-11 and the asserted sport
#          level, each on a planted world where the answer is known:
#
#            mu_fixed   the XC/TF level is a definition; the solve must take
#                       it off y and never re-estimate it
#            imp        the meet-importance (taper) term, identified off
#                       venues that host a mix, so a championship-only
#                       venue stops booking the taper as an easy course
#            ind        indoor as one coefficient per pool, so thin indoor
#                       cells stop being shrunk toward the outdoor mean
#            e_table    the published tables as the prior mean of an event
#                       offset the season-best pairs cannot calibrate
#
#   python -m pytest -q tests/test_shared_terms.py
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_solve as js                                        # noqa: E402
import distance_tables as dt                                    # noqa: E402


def _rmse(a, b):
    return float(np.sqrt(np.mean((np.asarray(a) - np.asarray(b)) ** 2)))


# ------------------------------------------------------------------ #
# the asserted sport level
# ------------------------------------------------------------------ #

def _two_sport_world(n_ath=600, n_cell=60, races_per_cell=4, per_athlete=8,
                     level=-0.05, seed=0):
    rng = np.random.default_rng(seed)
    group = (np.arange(n_cell) >= n_cell // 2).astype(int)      # 0 XC, 1 TF
    true_d = rng.normal(0, 0.04, n_cell)
    for g in (0, 1):
        true_d[group == g] -= true_d[group == g].mean()
    true_a = rng.normal(0, 0.2, n_ath)
    race_cell = np.repeat(np.arange(n_cell), races_per_cell)
    race_u = rng.normal(0, 0.02, race_cell.size)
    ath, cel, rac, y = [], [], [], []
    for i in range(n_ath):
        for _ in range(per_athlete):
            j = rng.integers(0, race_cell.size)
            c = race_cell[j]
            ath.append(i); cel.append(c); rac.append(j)
            y.append(true_a[i] + level * group[c] + true_d[c] + race_u[j]
                     + rng.normal(0, 0.02))
    return (np.array(ath), np.array(cel), np.array(rac), np.array(y),
            group, true_d, true_a)


def test_asserted_level_is_taken_off_y_and_never_estimated():
    ath, cel, rac, y, group, true_d, true_a = _two_sport_world()
    D = js.Design(ath, cel, rac, group_of_cell=group, mu_fixed=[0.0, -0.05])
    assert D.n_mu == 0 and D.n_total == D.n_ath + D.n_cell + D.n_race
    out = js.solveJoint(y, design=D, n_outer=5, tilt=False, tau_max=None,
                        n_probe=0)
    assert np.allclose(out["mu"], [0.0, -0.05])
    assert out["mu_fixed"] is not None
    # merge semantics: each sport's mean course difficulty is dropped
    for g in (0, 1):
        assert abs(float(out["d"][group == g].mean())) < 1e-6
    # relative difficulties recovered, and the level rides on delta
    d = out["d"]
    assert np.corrcoef(d, true_d)[0, 1] > 0.95      # 4 races/cell, u sd half of d sd
    assert np.allclose(out["delta"] - d, np.where(group == 1, -0.05, 0.0))
    # the level really came off y: predictions on the training rows have
    # no sport-shaped residual left
    pred, cov = js.predictHeldOut(out, D, D, tilt=False)
    resid = y - pred
    xc = group[cel] == 0
    assert abs(float(resid[xc].mean()) - float(resid[~xc].mean())) < 0.005
    print("  asserted level: off y, not in theta, on delta ............ OK")


def test_a_wrong_asserted_level_lands_in_the_abilities_not_the_courses():
    """Sport is season: the level cannot be checked by the fit. Assert it
    wrong by 3% and the courses must not move -- the abilities do."""
    ath, cel, rac, y, group, true_d, _ = _two_sport_world(seed=2)
    right = js.solveJoint(y, design=js.Design(ath, cel, rac, group_of_cell=group,
                                              mu_fixed=[0.0, -0.05]),
                          n_outer=4, tilt=False, tau_max=None, n_probe=0)
    wrong = js.solveJoint(y, design=js.Design(ath, cel, rac, group_of_cell=group,
                                              mu_fixed=[0.0, -0.02]),
                          n_outer=4, tilt=False, tau_max=None, n_probe=0)
    assert _rmse(right["d"], wrong["d"]) < 0.004
    print("  a wrong level moves abilities, not courses ................ OK")


# ------------------------------------------------------------------ #
# the meet-importance term
# ------------------------------------------------------------------ #

def _importance_world(n_ath=900, per_athlete=10, taper=-0.025, seed=0):
    """60 cells: 10 host ONLY championships, 10 host a mix, 40 host only
    ordinary meets. Championship rows run `taper` faster."""
    rng = np.random.default_rng(seed)
    n_cell = 60
    kind = np.array(["champ"] * 10 + ["mixed"] * 10 + ["plain"] * 40)
    true_d = rng.normal(0, 0.04, n_cell)
    true_d -= true_d.mean()
    true_a = rng.normal(0, 0.2, n_ath)
    race_cell, race_cls = [], []
    for c in range(n_cell):
        if kind[c] == "champ":
            for _ in range(3):
                race_cell.append(c); race_cls.append(1)
        elif kind[c] == "mixed":
            for k in (0, 0, 0, 1, 1, 1):
                race_cell.append(c); race_cls.append(k)
        else:
            for _ in range(4):
                race_cell.append(c); race_cls.append(0)
    race_cell = np.array(race_cell); race_cls = np.array(race_cls)
    race_u = rng.normal(0, 0.02, race_cell.size)
    ath, cel, rac, y = [], [], [], []
    for i in range(n_ath):
        for _ in range(per_athlete):
            j = rng.integers(0, race_cell.size)
            c = race_cell[j]
            ath.append(i); cel.append(c); rac.append(j)
            y.append(true_a[i] + true_d[c] + race_u[j] + taper * race_cls[j]
                     + rng.normal(0, 0.02))
    ath, cel, rac, y = map(np.array, (ath, cel, rac, y))
    imp = np.where(race_cls[rac] == 1, 0, -1)
    return ath, cel, rac, y, imp, true_d, kind, taper


def test_importance_term_is_recovered_and_frees_the_championship_venues():
    ath, cel, rac, y, imp, true_d, kind, taper = _importance_world()
    # prior at zero: the coefficient has to come from the data
    with_t = js.solveJoint(y, design=js.Design(ath, cel, rac, imp=imp, n_imp=1,
                                               imp_prior=[0.0]),
                           n_outer=5, tilt=False, tau_max=None, n_probe=0)
    without = js.solveJoint(y, design=js.Design(ath, cel, rac),
                            n_outer=5, tilt=False, tau_max=None, n_probe=0)
    got = float(with_t["importance"][0])
    print(f"  importance: planted {taper:+.4f}, recovered {got:+.4f}")
    assert abs(got - taper) < 0.006, (got, taper)
    champ = kind == "champ"

    def err(o):
        d = o["delta"] - o["delta"][~champ].mean() + true_d[~champ].mean()
        return _rmse(d[champ], true_d[champ])

    e_with, e_without = err(with_t), err(without)
    print(f"  championship-only venues: rmse {e_with:.4f} with the term, "
          f"{e_without:.4f} without")
    # the mixed venues' 30 championship races pin the term to about one
    # race-day sd / sqrt(30) = 0.004; the leftover sits in the pure venues
    assert e_with < 0.6 * e_without, (e_with, e_without)
    # the untermed fit booked the taper as an easy course
    d0 = without["delta"] - without["delta"][~champ].mean() + true_d[~champ].mean()
    assert float((d0[champ] - true_d[champ]).mean()) < 0.6 * taper
    print("  importance term recovered; championship venues honest ..... OK")


def test_importance_is_not_in_the_prediction_when_absent():
    """A design without the block is byte-for-byte the old one."""
    ath, cel, rac, y, imp, *_ = _importance_world(n_ath=200, per_athlete=6, seed=3)
    D0 = js.Design(ath, cel, rac)
    D1 = js.Design(ath, cel, rac, imp=np.full(ath.size, -1), n_imp=0)
    assert D0.n_total == D1.n_total and D1.n_imp == 0
    b = D1.unpack(np.zeros(D1.n_total))
    assert b["imp"] is None and b["ind"] is None


# ------------------------------------------------------------------ #
# the indoor term
# ------------------------------------------------------------------ #

def _indoor_world(n_ath=1200, per_athlete=8, shared=0.012, seed=0):
    """200 track cells, 40 indoor. Indoor cells carry a SHARED +shared and
    a small own deviation; most indoor cells host one or two races."""
    rng = np.random.default_rng(seed)
    n_cell = 200
    indoor = np.arange(n_cell) < 40
    own = np.where(indoor, rng.normal(0, 0.006, n_cell), rng.normal(0, 0.012, n_cell))
    own[~indoor] -= own[~indoor].mean()
    own[indoor] -= own[indoor].mean()
    true_delta = own + shared * indoor
    true_a = rng.normal(0, 0.2, n_ath)
    race_cell = []
    for c in range(n_cell):
        n_r = (1 if c % 2 else 2) if indoor[c] else 5
        race_cell += [c] * n_r
    race_cell = np.array(race_cell)
    race_u = rng.normal(0, 0.012, race_cell.size)
    ath, cel, rac, y = [], [], [], []
    for i in range(n_ath):
        for _ in range(per_athlete):
            j = rng.integers(0, race_cell.size)
            c = race_cell[j]
            ath.append(i); cel.append(c); rac.append(j)
            y.append(true_a[i] + true_delta[c] + race_u[j] + rng.normal(0, 0.02))
    return (np.array(ath), np.array(cel), np.array(rac), np.array(y),
            indoor, true_delta, shared)


def test_indoor_shared_term_is_recovered_and_reaches_delta():
    ath, cel, rac, y, indoor, true_delta, shared = _indoor_world()
    pool_row = np.zeros(ath.size, dtype=np.int64)
    # a narrow course prior, as track's is (TAU_MAX_DEFAULT[1] = 0.0161)
    with_t = js.solveJoint(y, design=js.Design(ath, cel, rac, pool_row=pool_row,
                                               ind=indoor),
                           n_outer=5, tilt=False, tau_max={0: 0.0161},
                           n_probe=0)
    without = js.solveJoint(y, design=js.Design(ath, cel, rac),
                            n_outer=5, tilt=False, tau_max={0: 0.0161},
                            n_probe=0)
    got = float(with_t["indoor"][0])
    print(f"  indoor: planted {shared:+.4f}, recovered {got:+.4f}")
    assert abs(got - shared) < 0.004, (got, shared)
    # delta carries it: the indoor cells' published level is the shared
    # term plus their own deviation
    assert np.allclose(with_t["delta"] - with_t["d"], with_t["indoor_cell"])
    assert abs(float(with_t["indoor_cell"][indoor].mean()) - got) < 1e-9
    assert (with_t["indoor_cell"][~indoor] == 0).all()

    def err(o):
        d = o["delta"] - o["delta"][~indoor].mean()
        return _rmse(d[indoor], true_delta[indoor])

    e_with, e_without = err(with_t), err(without)
    print(f"  indoor cells: rmse {e_with:.4f} with the term, {e_without:.4f} without")
    assert e_with < e_without
    print("  indoor term recovered and folded into delta ................ OK")


# ------------------------------------------------------------------ #
# the tables as a prior mean
# ------------------------------------------------------------------ #

def test_table_relation_is_antisymmetric_and_level_dependent():
    for sex in ("M", "F"):
        for band in range(4):
            a = dt.logTimeRatio(sex, band, 1600, 3200)
            b = dt.logTimeRatio(sex, band, 3200, 1600)
            assert abs(a + b) < 1e-12
            # 1600 -> 3200 is a 2.14-2.18x time ratio in every table
            assert 2.10 < np.exp(a) < 2.20, (sex, band, np.exp(a))
    # slower bands scale steeper, women steeper than men
    assert dt.logTimeRatio("M", 0, 800, 1600) > dt.logTimeRatio("M", 3, 800, 1600)
    assert dt.logTimeRatio("F", 1, 1600, 3200) > dt.logTimeRatio("M", 1, 1600, 3200)
    # crossing a knot integrates both segments
    both = dt.logTimeRatio("M", 1, 1000, 2000)
    assert abs(both - (dt.logTimeRatio("M", 1, 1000, 1600)
                       + dt.logTimeRatio("M", 1, 1600, 2000))) < 1e-12
    # the offset is zero when the curve already agrees with the table
    b = dt.EXPONENTS["M"][(1600.0, 3200.0)][1]
    assert abs(dt.tableOffset("p_m", 3200, 1600, 1,
                              lambda d: -b * np.log(d / 5000.0))) < 1e-12
    print("  tables: antisymmetric, level-dependent, zero on agreement ... OK")


def test_table_prior_holds_a_class_the_pairs_cannot_calibrate():
    """One TF class with a handful of rows and no reference pairs: solved
    e must sit near the table's value, not near zero."""
    rng = np.random.default_rng(5)
    n_ath, n_cell = 300, 20
    true_a = rng.normal(0, 0.2, n_ath)
    ath, cel, rac, y, dist = [], [], [], [], []
    race = 0
    for c in range(n_cell):
        for _ in range(4):
            for i in rng.integers(0, n_ath, 8):
                ath.append(i); cel.append(c); rac.append(race)
                dist.append(-1)
                y.append(true_a[i] + rng.normal(0, 0.02))
            race += 1
    # six rows of one free class, offset +0.03 in truth
    for i in rng.integers(0, n_ath, 6):
        ath.append(i); cel.append(0); rac.append(race); dist.append(0)
        y.append(true_a[i] + 0.03 + rng.normal(0, 0.02))
    ath, cel, rac, y, dist = map(np.array, (ath, cel, rac, y, dist))
    table = np.full(js.DIST_N_BAND, 0.03)
    D = js.Design(ath, cel, rac, dist=dist, n_e=1, dist_banded=True,
                  e_table=table)
    assert np.allclose(D.e_mean, 0.03)
    out = js.solveJoint(y, design=D, n_outer=3, tilt=False, tau_max=None,
                        n_probe=0, dist_cal=False)
    e = out["dist_offset"].reshape(1, js.DIST_N_BAND)
    # the middle band carries the six rows; every band sits on the table
    assert (np.abs(e - 0.03) < 0.012).all(), e
    D0 = js.Design(ath, cel, rac, dist=dist, n_e=1, dist_banded=True)
    out0 = js.solveJoint(y, design=D0, n_outer=3, tilt=False, tau_max=None,
                         n_probe=0, dist_cal=False)
    e0 = out0["dist_offset"].reshape(1, js.DIST_N_BAND)
    assert (np.abs(e0[0, [0, 2, 3]]) < 1e-6).all(), "no rows, zero prior -> zero"
    print("  table prior holds an uncalibrated class ..................... OK")


# ------------------------------------------------------------------ #
# altitude per event (2026-09-11): the 800 earns a fifth of the 5000's credit
# ------------------------------------------------------------------ #

def test_altitude_cost_scales_with_the_event():
    f = js.altDistanceFactor(np.array([0.0, 600.0, 800.0, 1600.0, 3000.0,
                                       5000.0, 8000.0, 10000.0, 12000.0]))
    assert f[0] == 1.0                                   # no distance: as before
    assert f[1] == f[2] == 0.21                          # held below the first knot
    assert abs(f[3] - 0.83) < 1e-12 and abs(f[5] - 1.0) < 1e-12
    assert 0.83 < f[4] < 1.0 and 1.0 < f[6] < 1.05 and f[7] == f[8] == 1.05
    assert (np.diff(f[1:]) >= 0).all()                   # monotone in distance
    # on a design: exposure and credit both carry it, home altitude too
    n = 6
    ath = np.arange(n); cel = np.zeros(n, int); rac = np.zeros(n, int)
    alt = np.full(n, 1.5)                                # 1.5 km above the floor
    dist = np.array([800.0, 1600.0, 5000.0, 10000.0, 0.0, 5000.0])
    D = js.Design(ath, cel, rac, alt=alt, alt_dist=js.altDistanceFactor(dist))
    assert np.allclose(D.alt, 1.5 * js.altDistanceFactor(dist))
    assert abs(D.alt[0] / D.alt[2] - 0.21) < 1e-12
    credit = js.altitudeCredit(D)
    assert np.allclose(credit, D.alt)
    # residents at the same race: the field's home altitude comes off first,
    # then the event's share applies
    home = np.full(n, 1.0)
    D2 = js.Design(ath, cel, rac, alt=alt, alt_home=home,
                   alt_dist=js.altDistanceFactor(dist))
    c2 = js.altitudeCredit(D2)
    assert np.allclose(c2, (1.5 - js.ALT_ACCLIM * 1.0) * js.altDistanceFactor(dist))
    # the old designs are untouched: no alt_dist means a factor of one
    D0 = js.Design(ath, cel, rac, alt=alt)
    assert np.allclose(D0.alt, alt) and np.allclose(js.altitudeCredit(D0), alt)
    print("  altitude cost scales with the event .......................... OK")
