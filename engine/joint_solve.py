"""
joint_solve.py -- the joint estimator: ability, difficulty, race-day effect and
the ability tilt fitted in ONE objective instead of four sequential stages.

    ln(t) = a[athlete_season] + delta[cell] * h(a) + u[race] + eps

  a      nuisance ability per athlete-season, unpenalised
  delta  cell difficulty, hierarchically shrunk: delta_c ~ N(0, tau2[group])
  u      race-day effect,  u_j ~ N(0, sigma_u2)  -- the term the current
         engine does not have, and the reason day-level noise becomes
         permanent venue difficulty today
  eps    ASYMMETRIC robust noise: a bad day is slow and unbounded, a good day
         is bounded by physiology, so the two tails get different thresholds

WHAT THIS REPLACES, and why each is a deletion rather than an addition:

  pair_engine.solveDelta + linkage_check.shrink   -> the penalised solve here
  shrinkByLinkage / bridgeFraction / shortLabelDead
        Those gates detect weakly-identified cells and overwrite them, because
        cellVariance uses sigma2/A_ii -- the diagonal of the INFORMATION, not
        the diagonal of its INVERSE. A_ii counts a cell's own races; it cannot
        see whether the cells it is anchored against are themselves solid.
        cellPosteriorVar() below estimates diag(A^-1) by Hutchinson probing,
        so precision shrinkage subsumes identification shrinkage and the gates
        have nothing left to do.
  apply_tilt (post-pass)
        The tilt is circular as a post-pass: delta_eff = delta*h(rating) while
        rating depends on delta_eff, and apply_tilt resolves it in ONE step off
        the pre-tilt rating. Here h is evaluated at the model's own ability, so
        there is no implicit equation to approximate.
  rowguard (diag/triage/apply)
        A hand-built robust loss that DELETES rows. Deleting a row removes it
        from the alibi pool, which condemns the next row -- that feedback is
        issue #23's 1.4M-drop spiral. A robust WEIGHT cannot spiral: the row
        stays in with weight 0.05 instead of vanishing.

Everything is implicit-operator + conjugate gradient, as pair_engine already
does, so nothing here materialises a matrix.
"""

import numpy as np


# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

# ★ ASYMMETRIC BY DESIGN. Residual is in log-time, so POSITIVE = ran slow.
#   A cramp, a fall, going out too hard: unbounded and common, so tolerate a
#   wide slow tail before down-weighting. Running far FASTER than predicted is
#   bounded by physiology, so a large negative residual is more likely a bad
#   row (mis-keyed time, short course) and is clipped sooner.
HUBER_SLOW = 2.5          # positive residuals, in robust scales
HUBER_FAST = 1.5          # negative residuals

TILT_K = -0.031           # per 10 rating points; matches racecast/tilt.py
TILT_RATING_LO = 70.0     # h is clamped to the band the tilt was fitted over
TILT_RATING_HI = 140.0

CG_TOL = 1e-8
CG_MAX_ITER = 400


# ------------------------------------------------------------------ #
# THE OPERATOR
# ------------------------------------------------------------------ #

# Purpose:   apply (Z' W Z + P) to a parameter vector, implicitly.
# Input:     theta packed as (a | delta | u); design given by the index arrays.
# Output:    the same packing.
#
# ★ SYMMETRIC BY CONSTRUCTION, which is what keeps conjugate gradient valid:
#   every block is a weighted incidence product, and the penalty is diagonal.
def applyOperator(theta, athlete, cell, race, w, h,
                  n_ath, n_cell, n_race, pen_cell, pen_race):
    a = theta[:n_ath]
    d = theta[n_ath:n_ath + n_cell]
    u = theta[n_ath + n_cell:]

    # row-wise prediction from the current parameters
    row = a[athlete] + d[cell] * h + u[race]
    wr = w * row

    out_a = np.bincount(athlete, weights=wr, minlength=n_ath)
    out_d = np.bincount(cell, weights=wr * h, minlength=n_cell) + pen_cell * d
    out_u = np.bincount(race, weights=wr, minlength=n_race) + pen_race * u
    return np.concatenate([out_a, out_d, out_u])


def _rhs(y, athlete, cell, race, w, h, n_ath, n_cell, n_race):
    wy = w * y
    return np.concatenate([
        np.bincount(athlete, weights=wy, minlength=n_ath),
        np.bincount(cell, weights=wy * h, minlength=n_cell),
        np.bincount(race, weights=wy, minlength=n_race)])


# Purpose:   preconditioned conjugate gradient on the normal equations.
# Detail:    Jacobi preconditioner -- the operator's own diagonal, which is
#            available in closed form and costs one bincount per block.
def conjugateGradient(rhs, matvec, diag, tol=CG_TOL, max_iter=CG_MAX_ITER):
    x = np.zeros_like(rhs)
    r = rhs - matvec(x)
    inv_diag = 1.0 / np.maximum(diag, 1e-12)
    z = inv_diag * r
    p = z.copy()
    rz = float(r @ z)
    rhs_norm = max(float(np.linalg.norm(rhs)), 1e-30)

    for it in range(max_iter):
        Ap = matvec(p)
        pAp = float(p @ Ap)
        if pAp <= 0:
            break                       # not positive definite; stop clean
        alpha = rz / pAp
        x += alpha * p
        r -= alpha * Ap
        if float(np.linalg.norm(r)) / rhs_norm < tol:
            return x, it + 1
        z = inv_diag * r
        rz_new = float(r @ z)
        p = z + (rz_new / rz) * p
        rz = rz_new
    return x, max_iter


def _operatorDiag(athlete, cell, race, w, h, n_ath, n_cell, n_race,
                  pen_cell, pen_race):
    return np.concatenate([
        np.bincount(athlete, weights=w, minlength=n_ath),
        np.bincount(cell, weights=w * h * h, minlength=n_cell) + pen_cell,
        np.bincount(race, weights=w, minlength=n_race) + pen_race])


# ------------------------------------------------------------------ #
# ROBUST WEIGHTS -- replaces rowguard
# ------------------------------------------------------------------ #

# Purpose:   asymmetric Huber weights from the current residuals.
# Output:    (weights, robust scale)
#
# ! NOTHING IS EVER DROPPED. A row that would have been condemned gets a small
#   weight and stays in the design, so it can still anchor its athlete and can
#   recover on the next iteration if the fit moves. That is the property
#   rowguard lacked.
def robustWeights(resid, scale=None):
    if scale is None:
        mad = float(np.median(np.abs(resid - np.median(resid))))
        scale = max(1.4826 * mad, 1e-9)
    z = resid / scale
    cut = np.where(z >= 0, HUBER_SLOW, HUBER_FAST)
    w = np.ones_like(z)
    big = np.abs(z) > cut
    w[big] = cut[big] / np.abs(z[big])
    return w, scale


# ------------------------------------------------------------------ #
# THE TILT -- evaluated at the model's own ability, not at a rating
# ------------------------------------------------------------------ #

# ★ NO CIRCULARITY. apply_tilt must evaluate h at the PRE-tilt rating because
#   the tilted rating is not known until after the tilt. Here ability is a
#   parameter, so h is exact at every iteration.
def tiltFromAbility(a_row, pool_mean_row):
    with np.errstate(over="ignore", invalid="ignore"):
        rating = 100.0 * pool_mean_row / np.exp(a_row)
    rating = np.clip(np.nan_to_num(rating, nan=100.0), TILT_RATING_LO,
                     TILT_RATING_HI)
    return 1.0 + TILT_K * (rating - 100.0) / 10.0


# ------------------------------------------------------------------ #
# POSTERIOR VARIANCE -- the number the gate stack exists to approximate
# ------------------------------------------------------------------ #

# Purpose:   diag(A^-1) for the cell block, by Hutchinson probing.
# Input:     matvec/diag as used by the solve; n_probe independent probes.
# Output:    per-cell posterior variance, in units of sigma2.
#
# ★ THIS IS THE WHOLE POINT. cellVariance() in pair_engine returns
#   sigma2 / A_ii -- the inverse of the DIAGONAL. The correct sampling variance
#   is sigma2 * (A^-1)_ii, and (A^-1)_ii >= 1/A_ii always, with the gap widest
#   exactly where a cell's neighbours are themselves weakly determined. Using
#   the diagonal understates the variance of precisely the cells that need
#   shrinking most, which is why a separate "identification" shrinkage had to
#   be bolted on. Estimate it properly and one mechanism does both jobs.
#
#   E[z * A^-1 z] = diag(A^-1) for z with independent +-1 entries.
#
# ⚠ EACH PROBE IS ONE CG SOLVE, and the error falls as 1/sqrt(n_probe), NOT
#   faster. Measured against an exactly-inverted operator on a small problem:
#
#       probes    400   1600   6400  25600
#       max err  21.1%  15.7%   5.5%   2.8%
#
#   64 is a usable default for shrinkage weights, which are forgiving --
#   tau2/(tau2+v) barely moves for a 20% error in v. Use several hundred
#   before PUBLISHING a per-cell standard error. The probes are independent,
#   so this parallelises perfectly.
def cellPosteriorVar(matvec, diag, n_total, n_ath, n_cell, sigma2,
                     n_probe=64, seed=0):
    rng = np.random.default_rng(seed)
    acc = np.zeros(n_cell)
    for _ in range(n_probe):
        z = rng.integers(0, 2, size=n_total).astype(np.float64) * 2.0 - 1.0
        x, _ = conjugateGradient(z, matvec, diag)
        acc += z[n_ath:n_ath + n_cell] * x[n_ath:n_ath + n_cell]
    return sigma2 * np.maximum(acc / n_probe, 1e-12)


# ------------------------------------------------------------------ #
# THE SOLVE
# ------------------------------------------------------------------ #

# Purpose:   fit the joint model by block coordinate descent.
# Input:     y        -- log response, one per row
#            athlete  -- athlete-season code per row, 0..n_ath-1
#            cell     -- course cell per row, 0..n_cell-1
#            race     -- race (meet x division x day) per row, 0..n_race-1
#            group    -- shrinkage group per CELL (level/sport/distance band)
#            pool_mean_row -- pool mean per row, for the tilt. None disables.
# Output:    dict of fitted blocks, variance components and diagnostics.
def solveJoint(y, athlete, cell, race, group=None, pool_mean_row=None,
               n_outer=6, robust=True, tilt=True, n_probe=64, seed=0,
               verbose=False):
    y = np.asarray(y, dtype=np.float64)
    athlete = np.asarray(athlete, dtype=np.int64)
    cell = np.asarray(cell, dtype=np.int64)
    race = np.asarray(race, dtype=np.int64)

    n = y.size
    n_ath = int(athlete.max()) + 1
    n_cell = int(cell.max()) + 1
    n_race = int(race.max()) + 1
    group = (np.zeros(n_cell, dtype=np.int64) if group is None
             else np.asarray(group, dtype=np.int64))
    n_group = int(group.max()) + 1

    w = np.ones(n)
    h = np.ones(n)
    # ★ START WEAK, NOT AT ZERO. tau2 = inf would be a flat prior and a
    #   singular first solve; these are loosened by the updates below.
    tau2 = np.full(n_group, 0.05)
    sigma_u2 = 0.01
    sigma2 = 1.0
    scale = None
    delta = np.zeros(n_cell)
    a = np.zeros(n_ath)
    u = np.zeros(n_race)

    for outer in range(n_outer):
        pen_cell = sigma2 / np.maximum(tau2[group], 1e-12)
        pen_race = sigma2 / max(sigma_u2, 1e-12)

        def matvec(t, _w=w, _h=h, _pc=pen_cell, _pr=pen_race):
            return applyOperator(t, athlete, cell, race, _w, _h,
                                 n_ath, n_cell, n_race, _pc, _pr)

        diag = _operatorDiag(athlete, cell, race, w, h, n_ath, n_cell, n_race,
                             pen_cell, pen_race)
        rhs = _rhs(y, athlete, cell, race, w, h, n_ath, n_cell, n_race)
        theta, iters = conjugateGradient(rhs, matvec, diag)

        a = theta[:n_ath]
        delta = theta[n_ath:n_ath + n_cell]
        u = theta[n_ath + n_cell:]

        resid = y - (a[athlete] + delta[cell] * h + u[race])

        # --- variance components ------------------------------------ #
        sigma2 = float(np.average(resid ** 2, weights=w))
        sigma_u2 = max(float(np.mean(u ** 2)), 1e-9)
        for g in range(n_group):
            m = group == g
            if m.any():
                tau2[g] = max(float(np.mean(delta[m] ** 2)), 1e-9)

        # --- robust reweighting (replaces rowguard) ------------------ #
        if robust:
            w, scale = robustWeights(resid)

        # --- the tilt, at the model's own ability -------------------- #
        if tilt and pool_mean_row is not None:
            h = tiltFromAbility(a[athlete], np.asarray(pool_mean_row))

        if verbose:
            print(f"  [joint] outer {outer + 1}/{n_outer}: cg {iters} iters, "
                  f"sigma {np.sqrt(sigma2):.5f}, sigma_u "
                  f"{np.sqrt(sigma_u2):.5f}, tau {np.sqrt(tau2).mean():.5f}, "
                  f"mean w {w.mean():.3f}")

    # --- posterior variance, and the shrinkage it licenses ----------- #
    pen_cell = sigma2 / np.maximum(tau2[group], 1e-12)
    pen_race = sigma2 / max(sigma_u2, 1e-12)

    def matvec_final(t):
        return applyOperator(t, athlete, cell, race, w, h,
                             n_ath, n_cell, n_race, pen_cell, pen_race)

    diag_final = _operatorDiag(athlete, cell, race, w, h, n_ath, n_cell,
                               n_race, pen_cell, pen_race)
    cell_var = cellPosteriorVar(matvec_final, diag_final, n_ath + n_cell +
                                n_race, n_ath, n_cell, sigma2,
                                n_probe=n_probe, seed=seed)

    return {
        "ability": a, "delta": delta, "race_effect": u,
        "cell_var": cell_var, "cell_se": np.sqrt(cell_var),
        "sigma2": sigma2, "sigma_u2": sigma_u2, "tau2": tau2,
        "weights": w, "robust_scale": scale, "h": h,
        "n_downweighted": int((w < 0.999).sum()),
    }
