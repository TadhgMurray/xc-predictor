"""
Tests for engine/joint_solve.py. Synthetic worlds with a KNOWN answer, so the
claims the module makes about itself are checked rather than asserted.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import joint_solve as js                                        # noqa: E402


def _world(n_ath=400, n_cell=60, races_per_cell=4, per_athlete=8,
           delta_sd=0.15, u_sd=0.03, noise_sd=0.02, seed=0):
    """Generate from the model exactly. Returns arrays plus the truth."""
    rng = np.random.default_rng(seed)
    true_delta = rng.normal(0, delta_sd, n_cell)
    true_delta -= true_delta.mean()                 # the model's own gauge
    true_a = rng.normal(0, 0.20, n_ath)

    race_cell, race_u = [], []
    for c in range(n_cell):
        for _ in range(races_per_cell):
            race_cell.append(c)
            race_u.append(rng.normal(0, u_sd))
    race_cell = np.array(race_cell)
    race_u = np.array(race_u)

    ath, cel, rac, y = [], [], [], []
    for i in range(n_ath):
        for _ in range(per_athlete):
            j = rng.integers(0, len(race_cell))     # a race, hence its cell
            c = race_cell[j]
            ath.append(i); cel.append(c); rac.append(j)
            y.append(true_a[i] + true_delta[c] + race_u[j]
                     + rng.normal(0, noise_sd))
    return (np.array(ath), np.array(cel), np.array(rac), np.array(y),
            true_delta, true_a, race_u)


def test_recovers_known_difficulty():
    ath, cel, rac, y, true_delta, _, _ = _world()
    out = js.solveJoint(y, ath, cel, rac, n_outer=6, tilt=False)
    d = out["delta"] - out["delta"].mean()
    r = float(np.corrcoef(d, true_delta)[0, 1])
    rmse = float(np.sqrt(np.mean((d - true_delta) ** 2)))
    assert r > 0.97, f"correlation with truth only {r:.3f}"
    assert rmse < 0.03, f"rmse {rmse:.4f}"
    print(f"  difficulty recovery: corr {r:.4f}, rmse {rmse:.4f} ......... OK")


def test_race_day_effect_protects_the_venue():
    """The claim: without u, day-level noise becomes permanent difficulty."""
    ath, cel, rac, y, true_delta, _, _ = _world(u_sd=0.10, noise_sd=0.02,
                                                seed=3)
    with_u = js.solveJoint(y, ath, cel, rac, n_outer=6, tilt=False)
    # race=0 everywhere collapses u to a single intercept, i.e. no race term.
    without_u = js.solveJoint(y, ath, cel, np.zeros_like(rac), n_outer=6,
                              tilt=False)

    def err(o):
        d = o["delta"] - o["delta"].mean()
        return float(np.sqrt(np.mean((d - true_delta) ** 2)))

    e_with, e_without = err(with_u), err(without_u)
    assert e_with < e_without, (e_with, e_without)
    print(f"  race-day effect: rmse {e_with:.4f} with u vs {e_without:.4f} "
          f"without ... OK")


def test_robust_weights_downweight_but_never_drop():
    ath, cel, rac, y, true_delta, _, _ = _world(seed=5)
    y_bad = y.copy()
    rng = np.random.default_rng(11)
    hit = rng.choice(len(y), size=len(y) // 50, replace=False)
    y_bad[hit] += 0.60                                # 60% slow: blowups

    rob = js.solveJoint(y_bad, ath, cel, rac, n_outer=6, robust=True,
                        tilt=False)
    plain = js.solveJoint(y_bad, ath, cel, rac, n_outer=6, robust=False,
                          tilt=False)

    def err(o):
        d = o["delta"] - o["delta"].mean()
        return float(np.sqrt(np.mean((d - true_delta) ** 2)))

    assert err(rob) < err(plain), (err(rob), err(plain))
    assert (rob["weights"] > 0).all(), "a row was zeroed -- nothing may drop"
    assert rob["weights"][hit].mean() < rob["weights"].mean(), \
        "the injected outliers were not down-weighted"
    print(f"  robust: rmse {err(rob):.4f} vs {err(plain):.4f} plain; "
          f"{rob['n_downweighted']:,} rows down-weighted, 0 dropped ... OK")


def test_the_loss_is_asymmetric():
    """A slow outlier is tolerated further than an equally large fast one."""
    resid = np.array([-0.5, 0.5] + list(np.random.default_rng(0)
                                        .normal(0, 0.1, 400)))
    w, _ = js.robustWeights(resid)
    assert w[1] > w[0], (w[0], w[1])          # +0.5 (slow) kept more than -0.5
    print(f"  asymmetry: w(slow +0.5)={w[1]:.3f} > w(fast -0.5)={w[0]:.3f} "
          f".. OK")


def test_posterior_variance_matches_the_exact_inverse():
    """cellPosteriorVar must estimate diag(A^-1), not 1/diag(A)."""
    ath, cel, rac, y, _, _, _ = _world(n_ath=60, n_cell=10, races_per_cell=2,
                                       per_athlete=5, seed=9)
    n_ath = ath.max() + 1
    n_cell = cel.max() + 1
    n_race = rac.max() + 1
    n = n_ath + n_cell + n_race
    w = np.ones(len(y))
    h = np.ones(len(y))
    pen_cell = np.full(n_cell, 0.5)
    pen_race = 1.0

    def matvec(t):
        return js.applyOperator(t, ath, cel, rac, w, h, n_ath, n_cell,
                                n_race, pen_cell, pen_race)

    diag = js._operatorDiag(ath, cel, rac, w, h, n_ath, n_cell, n_race,
                            pen_cell, pen_race)

    # Exact: build the dense operator column by column, invert it.
    A = np.column_stack([matvec(np.eye(n)[:, k]) for k in range(n)])
    exact = np.diag(np.linalg.inv(A))[n_ath:n_ath + n_cell]

    # The operator must be exactly symmetric or CG is invalid.
    assert np.abs(A - A.T).max() == 0.0, "operator is not symmetric"

    # Hutchinson is unbiased but noisy: error must FALL as 1/sqrt(n_probe).
    # That is what separates estimator noise from a wrong estimator.
    err = {}
    for p in (400, 6400):
        est = js.cellPosteriorVar(matvec, diag, n, n_ath, n_cell, sigma2=1.0,
                                  n_probe=p, seed=1)
        err[p] = float(np.max(np.abs(est - exact) / exact))
    assert err[6400] < err[400], err
    assert err[6400] < 0.10, f"still {err[6400]:.1%} off at 6400 probes"

    # And the claim that motivates the whole module.
    naive = 1.0 / diag[n_ath:n_ath + n_cell]
    assert (exact >= naive - 1e-12).all(), "(A^-1)_ii < 1/A_ii is impossible"
    ratio = float(np.mean(exact / naive))
    assert ratio > 1.0
    print(f"  posterior var: {err[400]:.1%} err at 400 probes -> "
          f"{err[6400]:.1%} at 6400; true variance {ratio:.1f}x the 1/A_ii "
          f"the gates patch ... OK")


def test_thin_cells_get_larger_standard_errors():
    ath, cel, rac, y, _, _, _ = _world(seed=13)
    out = js.solveJoint(y, ath, cel, rac, n_outer=5, tilt=False, n_probe=60)
    rows = np.bincount(cel, minlength=len(out["cell_se"]))
    thin = rows <= np.percentile(rows, 25)
    thick = rows >= np.percentile(rows, 75)
    assert out["cell_se"][thin].mean() > out["cell_se"][thick].mean()
    print(f"  thin cells SE {out['cell_se'][thin].mean():.4f} > thick "
          f"{out['cell_se'][thick].mean():.4f} ......... OK")


def test_tilt_is_evaluated_without_circularity():
    a = np.log(np.array([1000.0, 1200.0, 900.0]))
    pool = np.array([1100.0, 1100.0, 1100.0])
    h = js.tiltFromAbility(a, pool)
    assert h[1] > h[0] > h[2], h        # slower athlete -> larger multiplier
    assert np.isfinite(h).all()
    print(f"  tilt from ability: h = {np.round(h, 4)} ............... OK")


# ------------------------------------------------------------------ #
# run_joint helpers -- pure, so testable without a pack or a database
# ------------------------------------------------------------------ #

import run_joint as rj                                          # noqa: E402


def test_race_codes_group_one_cell_one_day():
    course = np.array([5, 5, 5, 5, 7, 7])
    date = np.array([100, 100, 101, 101, 100, 100])
    race, n = rj.raceCodes(course, date)
    assert n == 3, n
    assert race[0] == race[1], "same cell, same day must be one race"
    assert race[0] != race[2], "same cell, different day must differ"
    assert race[0] != race[4], "different cell, same day must differ"
    print(f"  raceCodes: {n} races from 6 rows ................... OK")


def test_cell_groups_split_by_sport():
    course = np.array([0, 0, 1, 1, 2])
    sport = np.array([1, 1, 0, 0, 1])          # cell 0 TF, cell 1 XC, cell 2 TF
    grp, n = rj.cellGroups(course, sport, n_cells=3)
    assert n == 2
    assert grp[0] == grp[2] != grp[1], grp
    solo, n1 = rj.cellGroups(course, None, n_cells=3)
    assert n1 == 1 and (solo == 0).all()
    print(f"  cellGroups: sport split {grp.tolist()} ............. OK")


if __name__ == "__main__":
    for fn in [test_recovers_known_difficulty,
               test_race_day_effect_protects_the_venue,
               test_robust_weights_downweight_but_never_drop,
               test_the_loss_is_asymmetric,
               test_posterior_variance_matches_the_exact_inverse,
               test_thin_cells_get_larger_standard_errors,
               test_tilt_is_evaluated_without_circularity,
               test_race_codes_group_one_cell_one_day,
               test_cell_groups_split_by_sport]:
        fn()
    print("\nall joint_solve tests passed")
