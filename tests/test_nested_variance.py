# Project: xc-predictor / tests
# File:    test_nested_variance.py
# Purpose: The variance E-step must use the nested (cell + its races) block,
#          not the information diagonal. Within a cell, d and every u of its
#          races are collinear; the diagonal pretends more rows of ONE race
#          resolve the split, and so under-states Var(d) and Var(u) in exactly
#          the thin cells the priors exist for. That is the mechanism behind
#          the sevenfold under-recovery of sigma_u the module recorded on a
#          few-races world, and behind "a course seen once keeps too much".
#
#   python -m pytest -q tests/test_nested_variance.py
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_solve as js                                        # noqa: E402


def _mixed_world(n_ath=700, n_thick=40, n_thin=260, thick_races=6,
                 per_athlete=3, delta_sd=0.05, u_sd=0.03, noise_sd=0.03,
                 seed=0):
    """Generate from the model: a few well-raced cells and many one-race
    cells, few races per athlete (the corpus' thin end). Returns arrays
    plus the truth."""
    rng = np.random.default_rng(seed)
    n_cell = n_thick + n_thin
    true_delta = rng.normal(0, delta_sd, n_cell)
    true_delta -= true_delta.mean()
    true_a = rng.normal(0, 0.20, n_ath)
    race_cell, race_u = [], []
    for c in range(n_cell):
        for _ in range(thick_races if c < n_thick else 1):
            race_cell.append(c)
            race_u.append(rng.normal(0, u_sd))
    race_cell = np.array(race_cell)
    race_u = np.array(race_u)
    ath, cel, rac, y = [], [], [], []
    for i in range(n_ath):
        for _ in range(per_athlete):
            j = rng.integers(0, len(race_cell))
            c = race_cell[j]
            ath.append(i); cel.append(c); rac.append(j)
            y.append(true_a[i] + true_delta[c] + race_u[j]
                     + rng.normal(0, noise_sd))
    return (np.array(ath), np.array(cel), np.array(rac), np.array(y),
            true_delta, true_a, race_u, race_cell)


def test_nested_variance_is_the_exact_inverse_of_the_cell_race_block():
    """Conditioning on the abilities, the (d, u) block of the operator is
    block-diagonal by cell with an arrowhead per cell. nestedPosteriorVar
    must reproduce diag(inv(A_du)) to machine precision -- including a
    tilt h that varies by row and a per-race pen_race."""
    ath, cel, rac, y, *_ = _mixed_world(n_ath=80, n_thick=6, n_thin=10,
                                        thick_races=3, per_athlete=4, seed=2)
    n_ath, n_cell, n_race = ath.max() + 1, cel.max() + 1, rac.max() + 1
    rng = np.random.default_rng(1)
    w = rng.uniform(0.3, 1.0, y.size)
    h = rng.uniform(0.85, 1.15, y.size)
    pen_cell = rng.uniform(0.3, 2.0, n_cell)
    pen_race = 1.7
    D = js.Design(ath, cel, rac)

    def matvec(t):
        return js.applyOperator(t, ath, cel, rac, w, h, n_ath, n_cell,
                                n_race, pen_cell, pen_race)

    # ! the legacy operator carries u UNTILTED; the model's u is tilted like
    #   d (issue 156), so build the tilted block by hand instead.
    n = n_ath + n_cell + n_race
    A = np.zeros((n, n))
    for i in range(y.size):
        cols = [ath[i], n_ath + cel[i], n_ath + n_cell + rac[i]]
        coef = [1.0, h[i], h[i]]
        for p, cp in zip(cols, coef):
            for q, cq in zip(cols, coef):
                A[p, q] += w[i] * cp * cq
    A[np.arange(n_ath, n_ath + n_cell), np.arange(n_ath, n_ath + n_cell)] += pen_cell
    A[np.arange(n_ath + n_cell, n), np.arange(n_ath + n_cell, n)] += pen_race
    A_du = A[n_ath:, n_ath:]
    exact = np.diag(np.linalg.inv(A_du))
    var_d, var_u = js.nestedPosteriorVar(D, w, h, pen_cell,
                                         np.full(n_race, pen_race), 1.0)
    got = np.concatenate([var_d, var_u])
    assert np.allclose(got, exact, rtol=1e-9, atol=1e-12), \
        np.max(np.abs(got - exact) / exact)
    # and it is NOT what the diagonal says: a one-race cell's Var(d) stays
    # at sigma2/(P_c + P_u) however many rows the race has
    diag = js._operatorDiag(ath, cel, rac, w, h, n_ath, n_cell, n_race,
                            pen_cell, pen_race)
    lower = 1.0 / diag[n_ath:n_ath + n_cell]
    assert (var_d >= lower - 1e-12).all()
    thin = np.bincount(cel, minlength=n_cell) > 0
    thin &= np.array([len(set(rac[cel == c])) == 1 for c in range(n_cell)])
    assert (var_d[thin] > 1.5 * lower[thin]).all(), \
        "the diagonal was not materially below the nested block on one-race cells"
    print("  nested block == exact inverse of the (d, u) block ........ OK")


def test_one_race_cell_variance_does_not_vanish_with_more_rows():
    """The whole point: sigma2/A_dd -> 0 as rows grow, the truth does not."""
    for n_rows in (5, 50, 500):
        ath = np.arange(n_rows)
        cel = np.zeros(n_rows, dtype=int)
        rac = np.zeros(n_rows, dtype=int)
        D = js.Design(ath, cel, rac)
        pc, pu = 2.0, 3.0
        var_d, var_u = js.nestedPosteriorVar(D, np.ones(n_rows),
                                             np.ones(n_rows), np.array([pc]),
                                             np.array([pu]), 1.0)
        limit = 1.0 / (pc + pu)
        assert var_d[0] > limit and var_d[0] < limit * (1.0 + 2.0 / n_rows + 1e-9), \
            (n_rows, var_d[0], limit)
        assert abs(var_u[0] - var_d[0]) < 1.0 / n_rows + 1e-9
    print("  one-race cell: Var(d) -> sigma2/(P_c+P_u), not 0 ......... OK")


def test_sigma_u_is_recovered_on_the_corpus_shaped_world():
    """Many rows per race, few races per athlete -- the corpus' shape (about
    100 rows per race, 4-5 per athlete-season). Measured against the EXACT
    posterior (a dense inverse of this world, scratch experiment 2026-09-11):
    exact and nested both land on the planted race-day sd, the diagonal
    sits 8-10% under it, and the gap widens as athletes get thinner. On a
    world with 3 races per athlete and 4 rows per race NOT EVEN THE EXACT
    E-STEP recovers it (0.006 against 0.030) -- that world is unidentified,
    not mis-estimated, and is deliberately not the test."""
    ath, cel, rac, y, true_delta, _, race_u, race_cell = _mixed_world(
        n_ath=3000, per_athlete=4, seed=4)
    u_true = float(np.std(race_u))
    nested = js.solveJoint(y, ath, cel, rac, n_outer=8, tilt=False,
                           tau_max=None, n_probe=0, nested_var=True)
    diag = js.solveJoint(y, ath, cel, rac, n_outer=8, tilt=False,
                         tau_max=None, n_probe=0, nested_var=False)
    su_n = float(np.sqrt(nested["sigma_u2"][0]))
    su_d = float(np.sqrt(diag["sigma_u2"][0]))
    print(f"  race-day sd: planted {u_true:.4f}, nested {su_n:.4f}, "
          f"diagonal {su_d:.4f}")
    assert abs(su_n - u_true) < abs(su_d - u_true), (su_n, su_d, u_true)
    assert abs(su_n - u_true) < 0.20 * u_true, (su_n, u_true)

    # and the thin cells shrink correctly as a consequence: the one-race
    # cells' published difficulty is no worse than under the diagonal, and
    # they no longer keep more of one day's noise than the prior allows
    thin = np.array([len(set(rac[cel == c])) == 1 for c in range(cel.max() + 1)])

    def rmse_thin(o):
        d = o["delta"] - o["delta"].mean()
        return float(np.sqrt(np.mean((d[thin] - true_delta[thin]) ** 2)))

    e_n, e_d = rmse_thin(nested), rmse_thin(diag)
    print(f"  thin-cell rmse: nested {e_n:.4f}, diagonal {e_d:.4f}")
    assert e_n <= e_d * 1.02, (e_n, e_d)
    print("  sigma_u recovered on the thin world .................... OK")


def test_solve_exposes_the_nested_variances():
    ath, cel, rac, y, *_ = _mixed_world(n_ath=120, n_thick=6, n_thin=20,
                                        per_athlete=4, seed=7)
    out = js.solveJoint(y, ath, cel, rac, n_outer=3, tilt=False,
                        tau_max=None, n_probe=0)
    assert out["cell_var_nested"].shape == (cel.max() + 1,)
    assert out["race_var"].shape == (rac.max() + 1,)
    # with the probes off, cell_var IS the nested block
    assert np.allclose(out["cell_var"], out["cell_var_nested"])
    assert (out["race_var"] > 0).all() and (out["cell_var"] > 0).all()
    print("  nested variances on the output dict ...................... OK")
