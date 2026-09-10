"""The joint solver on a two-sport academic year with a KNOWN answer.

The world: two pools, XC cells raced August-November, outdoor track cells
March-June, indoor track cells December-February; one ability per
athlete-season; a fifth of athletes with a real sport preference (beta);
a smooth year-long form curve per pool scaled by ability; a track-versus-XC
surface level; race-day effects; opener rust; asymmetric noise. Everything
the model claims to separate is generated separately, so recovery is a test
and not a tautology.

    python tests/test_joint_year.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import joint_solve as js
# ! tau_max=None: solveJoint now defaults to js.TAU_MAX_DEFAULT, the
#   MEASURED difficulty spread of this corpus (XC 0.035, TF 0.0122). This
#   world plants its own, wider spread, so the corpus cap would crush it
#   and the test would be measuring the cap.
#
# ! sigma_u_floor=0.0 for the same reason. The default (0.045) is a CLAIM
#   that race days vary by at least that much -- true of this corpus, not
#   of a world that plants its own race-day sd. Forcing one here costs
#   difficulty recovery, which is the documented cost of the floor and not
#   a bug in it. See engine/joint_solve.SIGMA_U_FLOOR.                                        # noqa: E402

TRUE_LEVEL = -0.03          # mu[TF] - mu[XC]: track surface reads 3% easier
TRUE_RUST = np.array([0.012, 0.014])
NOISE = 0.02


def trueCurve(day_academic, pool):
    """Log-time form offset by academic day: fitter through the year, more
    so in pool 1. Smooth across the winter by construction."""
    t = np.asarray(day_academic, dtype=np.float64)
    return CURVE_SCALE[pool] * np.cos(np.pi * t / 340.0)



CURVE_SCALE = [0.02, 0.028]


def world(indoor=True, seed=0, n_ath=700, indoor_frac=0.4, indoor_races=2):

    rng = np.random.default_rng(seed)
    # cells: [XC x 40 | TF outdoor x 30 | TF indoor x 10]
    n_xc, n_tfo, n_tfi = 40, 30, (10 if indoor else 0)
    n_cell = n_xc + n_tfo + n_tfi
    sport_of_cell = np.r_[np.zeros(n_xc), np.ones(n_tfo + n_tfi)].astype(int)
    d_true = np.r_[rng.normal(0, 0.08, n_xc), rng.normal(0, 0.03, n_tfo),
                   rng.normal(0, 0.03, n_tfi)]
    mu_true = np.array([0.0, TRUE_LEVEL])
    delta_true = mu_true[sport_of_cell] + d_true

    # race days per cell (doy), several per cell
    race_cell, race_doy, race_u = [], [], []
    for c in range(n_cell):
        if c < n_xc:
            days = rng.integers(225, 335, 5)                      # Aug-Nov
        elif c < n_xc + n_tfo:
            days = rng.integers(65, 160, 5)                       # Mar-Jun
        else:
            days = rng.choice(np.r_[np.arange(340, 366),
                                    np.arange(1, 55)], 4)         # Dec-Feb
        for dd in days:
            race_cell.append(c); race_doy.append(int(dd))
            race_u.append(rng.normal(0, 0.02))
    race_cell = np.array(race_cell); race_doy = np.array(race_doy)
    race_u = np.array(race_u)
    xc_races = np.flatnonzero(race_cell < n_xc)
    tfo_races = np.flatnonzero((race_cell >= n_xc) & (race_cell < n_xc + n_tfo))
    tfi_races = np.flatnonzero(race_cell >= n_xc + n_tfo)

    pool_of_ath = rng.integers(0, 2, n_ath)
    a_true = rng.normal(0, 0.15, n_ath)
    beta_true = np.where(rng.random(n_ath) < 0.2, rng.normal(0, 0.04, n_ath),
                         0.0)
    # rating from the true abilities, for the amplitude tilt
    rating = np.empty(n_ath)
    for p in (0, 1):
        m = pool_of_ath == p
        rating[m] = 100.0 * np.exp(a_true[m]).mean() / np.exp(a_true[m])
    amp_true = js.amplitudeFromRating(rating)

    rows = []
    for i in range(n_ath):
        picks = list(rng.choice(xc_races, 6)) + list(rng.choice(tfo_races, 6))
        if indoor and rng.random() < indoor_frac:
            picks += list(rng.choice(tfi_races, indoor_races))

        for j in picks:
            rows.append((i, j))
    ath = np.array([r[0] for r in rows]); rac = np.array([r[1] for r in rows])
    cel = race_cell[rac]; doy = race_doy[rac]
    sport_row = sport_of_cell[cel]
    pool_row = pool_of_ath[ath]
    aday = js.academicDay(doy)

    # centred sport per athlete-season (the design's sc)
    s = sport_row - 0.5
    sbar = np.bincount(ath, weights=s, minlength=n_ath) / np.bincount(
        ath, minlength=n_ath)
    sc = s - sbar[ath]

    # opener per (athlete, sport): the earliest academic day
    key = ath * 2 + sport_row
    order = np.lexsort((aday, key))
    first = np.zeros(len(ath), dtype=bool)
    k_s, d_s = key[order], aday[order]
    starts = np.r_[True, k_s[1:] != k_s[:-1]]
    grp = np.cumsum(starts) - 1
    first[order] = d_s == d_s[starts][grp]

    f_row = np.where(pool_row == 0, trueCurve(aday, 0), trueCurve(aday, 1))
    eps = rng.normal(0, NOISE, len(ath))
    y = (a_true[ath] + beta_true[ath] * sc + delta_true[cel] + race_u[rac]
         + amp_true[ath] * f_row + TRUE_RUST[pool_row] * first + eps)

    D = js.Design(ath, cel, rac, group_of_cell=sport_of_cell, sc=sc,
                  pool_row=pool_row, day=doy, first=first)
    # ★ THE LEVEL THE MODEL ESTIMATES IS THE GROUP MEAN OF THE DIFFICULTIES,
    #   and the cells' random parts have a non-zero realised mean (40 XC
    #   cells at sd 0.08 give the mean a sd of 0.013). Compare against
    #   what was actually generated, not the nominal constant.
    level_true = float(delta_true[sport_of_cell == 1].mean()
                       - delta_true[sport_of_cell == 0].mean())
    truth = dict(delta=delta_true, d=d_true, mu=mu_true, a=a_true,
                 level=level_true,

                 beta=beta_true, u=race_u, pool_of_ath=pool_of_ath,
                 sport_of_cell=sport_of_cell)
    return y, D, truth, dict(ath=ath, cel=cel, rac=rac, sc=sc,
                             pool_row=pool_row, doy=doy, first=first,
                             sport_of_cell=sport_of_cell)


def fit(y, D, truth, **kw):
    return js.solveJoint(y, design=D, athlete_pool=truth["pool_of_ath"],
                         n_outer=6, tilt=False, n_probe=8, **kw,
                         tau_max=None, sigma_u_floor=0.0)


def report_curve(out, D, doy):
    """Anchored fitted curve vs the anchored true curve at the knots."""
    knots = out["curve_knot_days"]
    errs, corrs = [], []
    for p in range(D.n_pool):
        true_k = trueCurve(knots, p)
        # anchor both the same way: to the row-weighted mean over the pool
        m = D.pool_row == p
        w = out["weights"][m]
        f_true_rows = trueCurve(js.academicDay(np.asarray(doy[m])), p)

        true_anch = true_k - np.average(f_true_rows, weights=w)
        got = out["curve_anchored"][p]
        # knots past June carry no rows; judge Aug..Jun only
        keep = knots <= 330
        errs.append(float(np.max(np.abs(got[keep] - true_anch[keep]))))
        corrs.append(float(np.corrcoef(got[keep], true_anch[keep])[0, 1]))
    return errs, corrs


def test_full_recovery():
    """With the window-balance penalty OFF: the smoothness prior and the
    December bridge separate the level from a curve that this world
    generates smooth across the winter. This is the pure-recovery check;
    the default split is test_window_balance_pins_the_level."""
    y, D, truth, raw = world(indoor=True)
    out = fit(y, D, truth, curve_gap=0.0)


    # the difficulty, level included, centred
    got = out["delta"] - out["delta"].mean()
    exp = truth["delta"] - truth["delta"].mean()
    r = float(np.corrcoef(got, exp)[0, 1])
    rmse = float(np.sqrt(np.mean((got - exp) ** 2)))
    assert r > 0.97 and rmse < 0.02, (r, rmse)
    print(f"  difficulty: corr {r:.4f}, rmse {rmse:.4f} .................. OK")

    # the surface level, separated from the seasonal curve. The curve's
    # linear trend is unpenalised and is pinned only by within-sport time
    # trends plus the December bridge, so the level is the least precise
    # number here; 0.006 is about three of its standard errors on this world.
    level = float(out["mu"][1] - out["mu"][0])
    assert abs(level - truth["level"]) < 0.006, (level, truth["level"])
    print(f"  TF-XC level {level:+.4f} (generated {truth['level']:+.4f}, "
          f"nominal {TRUE_LEVEL:+.3f}) ... OK")

    # the curve shape
    errs, corrs = report_curve(out, D, raw["doy"])

    assert min(corrs) > 0.95 and max(errs) < 0.01, (corrs, errs)
    print(f"  year curve: corr {np.round(corrs, 3)}, max err "
          f"{np.round(errs, 4)} ... OK")

    # specialists found, the rest near zero
    spec = truth["beta"] != 0
    rb = float(np.corrcoef(out["beta"][spec], truth["beta"][spec])[0, 1])
    rest = float(np.abs(out["beta"][~spec]).mean())
    assert rb > 0.8 and rest < 0.012, (rb, rest)
    print(f"  sport offset: specialists corr {rb:.3f}, others |beta| "
          f"{rest:.4f} ... OK")

    # rust per pool
    assert np.all(np.abs(out["rust"] - TRUE_RUST) < 0.005), out["rust"]
    print(f"  opener rust {np.round(out['rust'], 4)} (true {TRUE_RUST}) .. OK")

    # race-day effects
    ru = float(np.corrcoef(out["race_effect"], truth["u"])[0, 1])
    assert ru > 0.7, ru
    print(f"  race-day effects corr {ru:.3f} ......................... OK")
    return out


def test_window_balance_pins_the_level():
    """The DEFAULT split (issue 143): the curve's track-window mean equals
    its XC-window mean per pool, so the level mu carries the generated
    surface level PLUS the curve's net fall-to-spring step. Within a sport
    the difficulties are as good as before; the indoor cells, which this
    world generates on a curve that is smooth through the winter, pay for
    the constraint -- on real data that smoothness is the assumption the
    penalty exists to stop deciding the level."""
    y, D, truth, raw = world(indoor=True)
    out = fit(y, D, truth)
    gaps = out["curve_window_gap"]
    assert np.all(np.abs(gaps) < 1e-4), gaps

    aday = js.academicDay(raw["doy"])
    f_true = np.where(raw["pool_row"] == 0, trueCurve(aday, 0),
                      trueCurve(aday, 1))
    ref = D.group_row == 0
    step = float((out["amp"][~ref] * f_true[~ref]).mean()
                 - (out["amp"][ref] * f_true[ref]).mean())
    level = float(out["mu"][1] - out["mu"][0])
    assert abs(level - (truth["level"] + step)) < 0.008, \
        (level, truth["level"], step)

    got, exp = out["delta"], truth["delta"]
    idx = np.arange(got.size)
    # cells: [XC x 40 | TF outdoor x 30 | TF indoor x 10], as world() lays
    # them out. The indoor ten sit on the winter of a curve this world
    # generates smooth, which the constraint forbids; they pay, the rest
    # do not.
    for name, m, floor in (("XC", idx < 40, 0.96),
                           ("TF outdoor", (idx >= 40) & (idx < 70), 0.96),
                           ("TF indoor", idx >= 70, 0.85)):
        g = got[m] - got[m].mean()
        e = exp[m] - exp[m].mean()
        r = float(np.corrcoef(g, e)[0, 1])
        assert r > floor, (name, r)
    print(f"  window balance: gaps {np.round(gaps, 5)}, level {level:+.4f} "
          f"= surface {truth['level']:+.4f} + curve step {step:+.4f} ... OK")


def test_stated_winter_gain_moves_the_level_by_that_much():
    """--winter-gain G: the curve's track window sits G below its XC
    window and the level rises by G against the zero-gain fit."""
    y, D, truth, raw = world(indoor=True)
    base = fit(y, D, truth)
    gain = fit(y, D, truth, winter_gain=0.03)
    gaps = gain["curve_window_gap"]
    assert np.all(np.abs(gaps + 0.03) < 1e-3), gaps
    d_level = float((gain["mu"][1] - gain["mu"][0])
                    - (base["mu"][1] - base["mu"][0]))
    assert abs(d_level - 0.03) < 0.004, d_level
    print(f"  stated winter gain 0.03: window gaps {np.round(gaps, 4)}, "
          f"level moved {d_level:+.4f} ... OK")


def test_without_indoor_the_level_is_the_penalty():
    """No December overlap: the level and the curve's winter step are
    separated by the smoothness prior alone. The fit still predicts, but the
    level is no longer a measurement -- print how far it drifts."""
    y, D, truth, raw = world(indoor=False, seed=2)
    out = fit(y, D, truth, curve_gap=0.0)
    level = float(out["mu"][1] - out["mu"][0])
    print(f"  without indoor: TF-XC level {level:+.4f} (generated "
          f"{truth['level']:+.4f}); the winter is carried by the penalty")



def test_held_out_prediction():
    y, D, truth, raw = world(indoor=True, seed=4)
    rng = np.random.default_rng(1)
    te = rng.random(y.size) < 0.10
    tr = ~te

    def sub(m):
        return js.Design(raw["ath"][m], raw["cel"][m], raw["rac"][m],
                         group_of_cell=raw["sport_of_cell"], sc=raw["sc"][m],
                         pool_row=raw["pool_row"][m], day=raw["doy"][m],
                         first=raw["first"][m], n_ath=D.n_ath,
                         n_cell=D.n_cell, n_race=D.n_race, n_pool=D.n_pool)
    D_tr, D_te = sub(tr), sub(te)
    out = fit(y[tr], D_tr, truth)
    pred, cov = js.predictHeldOut(out, D_tr, D_te,
                                  athlete_pool=truth["pool_of_ath"],
                                  tilt=False)
    err = y[te][cov] - pred[cov]
    sd = float(err.std())
    # the world's irreducible noise is 0.02; unseen race days add ~0.02
    assert sd < 0.035 and cov.mean() > 0.9, (sd, cov.mean())
    print(f"  held-out: error sd {sd:.4f} over {cov.mean():.0%} covered "
          f"(noise {NOISE}) ... OK")


if __name__ == "__main__":
    test_full_recovery()
    test_without_indoor_the_level_is_the_penalty()
    test_held_out_prediction()
    print("\nall joint_year tests passed")
