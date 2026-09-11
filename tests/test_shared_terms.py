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


# ------------------------------------------------------------------ #
# the taper as a CONTINUOUS covariate (2026-09-11): the race's season-end
# share, not a label. Planted: rows run faster by taper * share.
# ------------------------------------------------------------------ #

def test_a_continuous_share_recovers_the_taper_and_frees_the_venues():
    # Ten finals venues (every race a full peak, share 1), twenty mixed
    # venues (shares 0 .. 1, the identifying variation), forty ordinary
    # venues (share 0.05). The coefficient's standard error on this world is
    # about race-day sd / (sd of the share * sqrt(mixed races)) = 0.02 /
    # (0.37 * sqrt(400)) = 0.003; four seeds recovered -0.021 .. -0.024.
    rng = np.random.default_rng(21)
    n_ath, n_cell, per_athlete, taper = 1200, 70, 14, -0.025
    true_d = rng.normal(0, 0.04, n_cell); true_d -= true_d.mean()
    true_a = rng.normal(0, 0.2, n_ath)
    race_cell, race_share = [], []
    for c in range(n_cell):
        if c < 10:                       # finals venues: every race a full peak
            for _ in range(3):
                race_cell.append(c); race_share.append(1.0)
        elif c < 30:                     # mixed venues: shares all over
            for _ in range(4):
                for s in (0.0, 0.2, 0.5, 0.8, 1.0):
                    race_cell.append(c); race_share.append(s)
        else:                            # ordinary venues
            for _ in range(4):
                race_cell.append(c); race_share.append(0.05)
    race_cell = np.array(race_cell); race_share = np.array(race_share)
    race_u = rng.normal(0, 0.02, race_cell.size)
    ath, cel, rac, y = [], [], [], []
    for i in range(n_ath):
        for _ in range(per_athlete):
            j = rng.integers(0, race_cell.size)
            c = race_cell[j]
            ath.append(i); cel.append(c); rac.append(j)
            y.append(true_a[i] + true_d[c] + race_u[j] + taper * race_share[j]
                     + rng.normal(0, 0.02))
    ath, cel, rac, y = map(np.array, (ath, cel, rac, y))
    w = race_share[rac]
    idx = np.where(w > 0, 0, -1)
    with_t = js.solveJoint(y, design=js.Design(ath, cel, rac, imp=idx, n_imp=1,
                                               imp_prior=[0.0], imp_w=w),
                           n_outer=5, tilt=False, tau_max=None, n_probe=0)
    without = js.solveJoint(y, design=js.Design(ath, cel, rac),
                            n_outer=5, tilt=False, tau_max=None, n_probe=0)
    got = float(with_t["importance"][0])
    print(f"  season-end taper per unit share: planted {taper:+.4f}, recovered {got:+.4f}")
    assert abs(got - taper) < 0.006, (got, taper)
    finals = np.arange(n_cell) < 10

    def err(o):
        d = o["delta"] - o["delta"][~finals].mean() + true_d[~finals].mean()
        return _rmse(d[finals], true_d[finals])

    e_with, e_without = err(with_t), err(without)
    print(f"  finals-only venues: rmse {e_with:.4f} with the share, {e_without:.4f} without")
    assert e_with < 0.7 * e_without, (e_with, e_without)
    print("  continuous share recovers the taper; finals venues honest ..... OK")


# ---------------------------------------------------------------------- #
# 2026-09-11, second cut: the FIELD-STRENGTH term (owner: "if there is a
# race that is very top-heavy, where people will run fast because there's
# more competition, those races should get some refund to their
# difficulty") and the ASSERTED indoor level (owner: "I think indoor might
# be off").
# ---------------------------------------------------------------------- #

def _field_world(seed=5, effect=-0.006, n_ath=1500, n_cell=60, n_rows=30):
    """Ten finals venues host only stacked fields (drawn from the fastest
    15%); fifty mixed venues cycle through weak, ordinary, strong and
    stacked fields. y carries `effect` per unit of the race's front
    (top-5 mean rating above the median race, per 10 points), computed
    from the TRUE abilities exactly as the solve computes it from its own."""
    rng = np.random.default_rng(seed)
    true_a = rng.normal(0, 0.15, n_ath)
    true_d = rng.normal(0, 0.04, n_cell); true_d -= true_d.mean()
    order = np.argsort(true_a)                       # fastest first (low log-time)
    pools = {-1: order[int(0.3 * n_ath):], 0: order, 1: order[:int(0.4 * n_ath)],
             2: order[:int(0.15 * n_ath)]}
    race_cell, race_kind = [], []
    for c in range(n_cell):
        kinds = [2] * 6 if c < 10 else [-1, 0, 0, 1, 2, 0]
        for k in kinds:
            race_cell.append(c); race_kind.append(k)
    race_cell = np.array(race_cell); race_kind = np.array(race_kind)
    n_race = race_cell.size
    race_u = rng.normal(0, 0.02, n_race)
    ath, cel, rac = [], [], []
    for j in range(n_race):
        picks = rng.choice(pools[int(race_kind[j])], n_rows, replace=False)
        ath.extend(picks.tolist()); cel.extend([race_cell[j]] * n_rows)
        rac.extend([j] * n_rows)
    ath, cel, rac = map(np.array, (ath, cel, rac))
    # the true front, as fieldStrength computes it from ratings
    true_r = 100.0 * np.exp(true_a.mean()) / np.exp(true_a)
    mask = np.ones(ath.size, dtype=bool)
    w_true, s_true, _ = js.fieldStrength(true_r[ath], rac, n_race,
                                         np.zeros(ath.size, dtype=np.int64),
                                         mask, 1)
    y = (true_a[ath] + true_d[cel] + race_u[rac] + effect * w_true
         + rng.normal(0, 0.02, ath.size))
    return y, ath, cel, rac, true_d, s_true, effect


def test_the_field_strength_term_recovers_a_planted_front_effect():
    y, ath, cel, rac, true_d, s_true, effect = _field_world()
    n_ath = int(ath.max()) + 1
    idx = np.zeros(ath.size, dtype=np.int64)
    pool = np.zeros(n_ath, dtype=np.int64)
    D = js.Design(ath, cel, rac, imp=idx, n_imp=1, imp_prior=[0.0],
                  imp_kind="field")
    assert D.imp_kind == "field" and float(np.abs(D.imp_w).max()) == 0.0
    with_t = js.solveJoint(y, design=D, athlete_pool=pool, n_outer=6,
                           tilt=False, tau_max=None, n_probe=0)
    got = float(with_t["importance"][0])
    print(f"  field strength per unit: planted {effect:+.4f}, recovered {got:+.4f}")
    assert abs(got - effect) < 0.002, (got, effect)
    # the covariate the solve built matches the planted one closely
    assert with_t["field_strength"] is not None
    corr = np.corrcoef(with_t["field_strength"], s_true)[0, 1]
    assert corr > 0.97, corr
    # and the stacked-only venues are freed: without the term they read easy
    without = js.solveJoint(y, design=js.Design(ath, cel, rac),
                            athlete_pool=pool, n_outer=6, tilt=False,
                            tau_max=None, n_probe=0)
    finals = np.arange(true_d.size) < 10

    def err(o):
        d = o["delta"] - o["delta"][~finals].mean() + true_d[~finals].mean()
        return _rmse(d[finals], true_d[finals])

    e_with, e_without = err(with_t), err(without)
    # the planted effect is -1.8% on a stacked field; ten cells at six races
    # each keep an estimation floor near 0.008, so the ratio is bounded
    print(f"  stacked-only venues: rmse {e_with:.4f} with the term, {e_without:.4f} without")
    assert e_with < 0.75 * e_without, (e_with, e_without)

    def bias(o):
        return float((o["delta"][finals] - o["delta"][~finals].mean()
                      - (true_d[finals] - true_d[~finals].mean())).mean())

    print(f"  stacked-only venues: mean bias {bias(with_t):+.4f} with, {bias(without):+.4f} without")
    assert bias(without) < -0.008, bias(without)       # they read EASY without it
    # the fitted covariate is the true one seen through fitted ratings, so
    # the slope attenuates a little (errors in variables); the bias halves
    assert abs(bias(with_t)) < 0.6 * abs(bias(without))
    print("  field strength recovers the front effect; stacked venues honest ..... OK")


def test_the_field_covariate_is_the_front_of_the_race():
    race = np.array([0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 2, 2])
    r = np.array([150, 140, 130, 120, 110, 100, 90, 105, 100, 95, 130, 70.0])
    idx = np.zeros(race.size, dtype=np.int64)
    w, s, cen = js.fieldStrength(r, race, 3, idx, np.ones(race.size, bool), 1,
                                 k=5, unit=10.0, clip=(-4.0, 6.0))
    front = np.array([130.0, 100.0, 100.0])          # top-5 mean, top-3, top-2
    assert np.allclose(cen, [100.0])                  # the median race
    assert np.allclose(s, (front - 100.0) / 10.0)
    assert np.allclose(w, s[race])
    # a fixed centre is honoured (a held-out design uses the training one)
    w2, s2, cen2 = js.fieldStrength(r, race, 3, idx, np.ones(race.size, bool), 1,
                                    centre=[110.0])
    assert np.allclose(cen2, [110.0]) and np.allclose(s2, (front - 110.0) / 10.0)
    # rows without a cell carry no weight, and a NaN rating counts as 100
    m = np.ones(race.size, bool); m[:7] = False
    w3, s3, _ = js.fieldStrength(np.where(race == 2, np.nan, r), race, 3, idx, m, 1)
    assert (w3[:7] == 0).all() and np.isfinite(s3).all()


def test_an_asserted_indoor_level_is_off_theta_and_the_ovals_keep_only_their_deviation():
    rng = np.random.default_rng(3)
    n_ath, n_xc, n_out, n_in, per_athlete = 800, 20, 20, 8, 10
    n_cell = n_xc + n_out + n_in
    group = np.r_[np.zeros(n_xc, int), np.ones(n_out + n_in, int)]
    is_in = np.r_[np.zeros(n_xc + n_out, bool), np.ones(n_in, bool)]
    level, mu_tf = 0.012, -0.05
    true_d = rng.normal(0, 0.03, n_cell)
    for g, m in ((0, group == 0), (1, (group == 1) & ~is_in), (1, is_in)):
        true_d[m] -= true_d[m].mean()                  # each block centred
    true_a = rng.normal(0, 0.2, n_ath)
    race_cell = np.repeat(np.arange(n_cell), 4)
    race_u = rng.normal(0, 0.015, race_cell.size)
    ath = np.repeat(np.arange(n_ath), per_athlete)
    rac = rng.integers(0, race_cell.size, ath.size)
    cel = race_cell[rac]
    y = (true_a[ath] + np.where(group[cel] == 1, mu_tf, 0.0) + true_d[cel]
         + level * is_in[cel] + race_u[rac] + rng.normal(0, 0.02, ath.size))
    pool_row = np.zeros(ath.size, dtype=np.int64)
    D = js.Design(ath, cel, rac, group_of_cell=group, pool_row=pool_row,
                  n_pool=1, mu_fixed=[0.0, mu_tf], ind=is_in, ind_fixed=[level])
    assert D.n_ind == 0 and D.ind_fixed is not None and D.n_mu == 0
    off = D.fixedOffset(np.ones(D.n))
    assert np.allclose(off, np.where(group[cel] == 1, mu_tf, 0.0) + level * is_in[cel])
    out = js.solveJoint(y, design=D, n_outer=4, tilt=False, tau_max=None,
                        n_probe=0)
    assert out["indoor_fixed"] and np.allclose(out["indoor"], [level])
    d = out["d"]
    # the outdoor tracks and the ovals are centred SEPARATELY: the level is
    # the whole of the indoor offset, and delta carries it
    assert abs(d[(group == 1) & ~is_in].mean()) < 1e-9
    assert abs(d[is_in].mean()) < 1e-9
    assert np.allclose(out["delta"][is_in], mu_tf + d[is_in] + level)
    assert np.allclose(out["delta"][(group == 1) & ~is_in], mu_tf + d[(group == 1) & ~is_in])
    assert _rmse(d[is_in], true_d[is_in]) < 0.012
    assert _rmse(d[~is_in], true_d[~is_in]) < 0.012
    # the fit's own residual and the held-out predictor both carry the level
    pred, covered = js.predictHeldOut(out, D, D)
    resid = y - pred
    assert covered.all() and abs(float(resid.mean())) < 2e-3
    assert float(np.sqrt(np.mean(resid ** 2))) < 0.03
    print("  asserted indoor level: off theta, ovals centred, in delta and in the prediction ..... OK")
