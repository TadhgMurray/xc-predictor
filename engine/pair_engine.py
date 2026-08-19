# Project: xc-predictor
# Subset:  Pair Engine -- Stages 1 and 2
#
# THE MODEL
# ---------
#     ln(norm) - ln(1 + form)  =  alpha[athlete, season]  +  delta[cell]  +  eps
#
# alpha is a nuisance parameter. delta = ln(1 + difficulty) is what we want.
#
# ★ WHY THIS IS THE PAIR ESTIMATOR. Least squares on this model is algebraically
#   identical to least squares over every same-athlete-same-season venue PAIR:
#   in a pair difference alpha appears on both sides and cancels EXACTLY. It is
#   never estimated against difficulty, so the two cannot trade off. The FE form
#   is used instead of literal pairs only because ~132M pairs would have to be
#   materialised and the FE form does not.
#
# ★ WHY THIS CANNOT RUN AWAY. The system is linear and its matrix is positive
#   semi-definite, so there is a unique minimiser up to the null space -- and
#   the null space is exactly one constant per connected component of the
#   bipartite graph (Stage 0 measured 798 components, 99.95% of rows in one).
#   Conjugate gradient from zero converges to the minimum-norm solution, which
#   is orthogonal to that null space. There is no fixed point to drift away
#   from: the 323-iteration walk to -0.674 has no analogue here.
#
# ★ PER-SEASON ABILITY. The ALS engine holds one ability per athlete for their
#   whole career. A high schooler improving 4%/year for four years has ~15% of
#   variation forced into one scalar, and the remainder lands in whichever
#   venues they raced -- which correlates with WHEN. alpha[athlete, season]
#   absorbs that by construction.
#
# WHAT THIS DOES NOT FIX, and must not be credited with fixing:
#   - the +0.02 to 0.03 offset at 4800m, which lives in normalized_time
#   - weather, still a no-op pickle
#   - a taper common to a whole championship field, which is collinear with
#     that venue's difficulty in any design
#
# Reads the packed cache, so cell definitions are the engine's own.

import os
import sys

import numpy as np


# ------------------------------------------------------------------ #
# CHUNK 1 -- INPUTS
# ------------------------------------------------------------------ #

# loadPack
# Purpose:   the packed arrays speed_ratings already builds.
# Detail:    imports loadCols so there is ONE reader for the .npz layout.
def loadPack(path):
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)) or ".")
    from speed_ratings import loadCols
    return loadCols(path)


# buildResponse
# Purpose:   y, the left-hand side, with season form removed.
# Arguments: cols -- packed dict.
# Output:    (y ndarray[float64], form ndarray[float64])
#
# Sign: rust_fitness returns POSITIVE for "slower than true level because of
# season form". ln(norm) = alpha + delta + ln(1+form), so form is SUBTRACTED in
# logs to stop the venue being charged for it. This is the same convention as
# computeCourseDifficulties, which does log_norm - log_ability - log_form.
def buildResponse(cols):
    from rust_fitness import buildCorrection, reportCorrection
    from speed_ratings_db import loadPriorRatings

    form = buildCorrection(cols, cols["athlete_keys"], loadPriorRatings())
    reportCorrection(form, cols)
    return np.log(cols["norm"]) - np.log1p(form), form


# athleteSeasonCodes
# Purpose:   dense 0..M-1 id per (athlete, season).
# Syntax:    one int64 key beats np.unique(axis=0) at 59M rows; multiplying the
#            athlete code by 10000 makes the combination unique for any 4-digit
#            season.
def athleteSeasonCodes(athlete, year):
    key = athlete.astype(np.int64) * 10000 + year.astype(np.int64)
    _, codes = np.unique(key, return_inverse=True)
    return codes.astype(np.int64), int(codes.max()) + 1


# ------------------------------------------------------------------ #
# CHUNK 2 -- THE INFORMATIVE SUBSET
# ------------------------------------------------------------------ #

# _groupSizes
# Purpose:   rows per athlete-season.
def _groupSizes(group, n_groups):
    return np.bincount(group, minlength=n_groups)


# _cellDegree
# Purpose:   distinct athlete-seasons per cell.
# Syntax:    encode (cell, group) as one int64, take unique, count by cell.
#            Cheaper than np.unique(axis=0) and avoids a 2-column copy.
def _cellDegree(group, course, n_cells, n_groups):
    key = course.astype(np.int64) * n_groups + group
    uniq = np.unique(key)
    return np.bincount((uniq // n_groups).astype(np.int64), minlength=n_cells)


# informativeMask
# Purpose:   ★ drop rows that cannot constrain any delta, iterating to a fixed
#            point because each drop can strand others.
# Arguments: group, course, n_groups, n_cells; min_degree.
# Output:    (mask, degree)
#
# TWO RULES, and both are about identifiability rather than sample size:
#   1. An athlete-season with ONE row carries no information. alpha absorbs that
#      row exactly, the residual is zero by construction, and the cell learns
#      nothing. This is why Stage 0's degree histogram was optimistic.
#   2. A cell whose degree is below min_degree is pinned by too few
#      athlete-seasons for its level to be separable from their abilities.
#      Degree -- not row count -- is what determines identifiability, which is
#      why this replaces RIDGE_LAMBDA rather than sitting beside it.
#
# Removing rows under rule 2 can drop an athlete-season below two rows, which
# can drop another cell's degree, so it loops until nothing changes.
def informativeMask(group, course, n_groups, n_cells, min_degree=2):
    mask = np.ones(group.size, dtype=bool)

    for it in range(20):
        sizes = _groupSizes(group[mask], n_groups)
        keep_g = sizes[group] >= 2

        degree = _cellDegree(group[mask], course[mask], n_cells, n_groups)
        keep_c = degree[course] >= min_degree

        new = mask & keep_g & keep_c
        if new.sum() == mask.sum():
            break
        mask = new

    degree = _cellDegree(group[mask], course[mask], n_cells, n_groups)
    print(f"[pair] informative subset after {it + 1} passes: "
          f"{int(mask.sum()):,}/{mask.size:,} rows "
          f"({100.0 * mask.sum() / mask.size:.1f}%), "
          f"{int((degree >= min_degree).sum()):,} cells with degree "
          f">= {min_degree}")
    return mask, degree


# ------------------------------------------------------------------ #
# CHUNK 3 -- THE OPERATOR
# ------------------------------------------------------------------ #

# demeanWithin
# Purpose:   subtract each athlete-season's mean -- the projector M_A that
#            absorbs alpha.
# Arguments: v -- per-row values; group; n_groups.
# Output:    per-row residuals.
#
# ★ THIS IS WHERE ABILITY CANCELS. Everything downstream sees only within-
#   athlete-season variation, which is exactly what a pair difference sees.
def demeanWithin(v, group, n_groups):
    total = np.bincount(group, weights=v, minlength=n_groups)
    count = np.bincount(group, minlength=n_groups)
    np.maximum(count, 1, out=count)
    return v - (total / count)[group]


# ------------------------------------------------------------------ #
#  PARTIAL POOLING: one ability plus a SHRUNK per-athlete sport offset
# ------------------------------------------------------------------ #
#
#     y = alpha[athlete, season] + beta[athlete, season] * s + delta[cell]
#
#   with s = +-0.5 for TF/XC and beta penalised by RIDGE.
#
# ★ WHY NOT JUST SPLIT THE SPORTS. Fitting alpha per (athlete, season, sport)
#   predicts 5.56% better held-out -- TF alone improves 10.01% -- so the single
#   shared ability really is costing accuracy. But shared athletes are the ONLY
#   edges linking XC venues to TF venues: split them and the bipartite graph
#   falls into two components with independent gauges, and the 0.043 offset
#   between TF and XC difficulty stops being measurable. Stage 0 found 99.95% of
#   rows in one component precisely BECAUSE ability is shared.
#
#   Partial pooling keeps alpha spanning both sports -- so the graph stays
#   connected and one anchor still fixes everything -- while letting a genuine
#   specialist differ. An athlete with twenty races in each sport gets nearly
#   their full offset; one with three XC races and a single TF race gets almost
#   none, because the ridge dominates when the within-group spread of s is
#   small.
#
# ★ THE OPERATOR STAYS SYMMETRIC, so conjugate gradient remains valid. Removing
#   the group mean is a projection; removing the ridge-shrunk slope is
#   s(s's + K)^-1 s', also symmetric and PSD. Their difference from the identity
#   is symmetric, which is the only property CG needs.


def sportCentered(sport, group, n_groups):
    """
    s minus its group mean, per row.

    Centering inside the group is what makes the slope orthogonal to the mean,
    so alpha and beta do not fight. A group racing ONE sport has every s equal,
    so the centred value is zero everywhere -- beta is then unidentified for
    that athlete and the ridge sends it to exactly zero, which is correct.
    """
    s = sport.astype(np.float64) - 0.5
    total = np.bincount(group, weights=s, minlength=n_groups)
    count = np.maximum(np.bincount(group, minlength=n_groups), 1)
    return s - (total / count)[group]


def demeanWithinSport(v, group, sc, n_groups, ridge):
    """
    Remove the group mean AND the ridge-shrunk sport slope.

    Arguments: v -- per-row values; group; sc -- centred sport from
               sportCentered; ridge -- K.
    Output:    per-row residual.

    The slope is  beta = sum(sc*vc) / (sum(sc^2) + K), the standard ridge
    solution. K = 0 recovers a full split; K -> infinity recovers the shared
    ability.
    """
    total = np.bincount(group, weights=v, minlength=n_groups)
    count = np.maximum(np.bincount(group, minlength=n_groups), 1)
    vc = v - (total / count)[group]

    num = np.bincount(group, weights=sc * vc, minlength=n_groups)
    den = np.bincount(group, weights=sc * sc, minlength=n_groups) + ridge
    beta = num / np.maximum(den, 1e-12)
    return vc - beta[group] * sc


# applyOperator
# Purpose:   D' M_A D v, the normal-equations matrix applied to a vector.
# Arguments: v -- per-cell vector; course, group, n_cells, n_groups.
# Output:    per-cell vector.
#
# Never forms the 81k x 81k matrix. Three passes over the rows: expand cells to
# rows, demean within athlete-season, aggregate back to cells. O(rows), which is
# what makes conjugate gradient affordable at 59M rows.
def applyOperator(v, course, group, n_cells, n_groups, h=None,
                  sc=None, ridge=0.0):
    rows = v[course] if h is None else h * v[course]
    rows = (demeanWithin(rows, group, n_groups) if sc is None
            else demeanWithinSport(rows, group, sc, n_groups, ridge))
    if h is not None:
        rows = h * rows
    return np.bincount(course, weights=rows, minlength=n_cells)


# operatorDiagonal
# Purpose:   diag(D' M_A D), for Jacobi preconditioning.
# Output:    per-cell vector.
#
# Entry c is sum over rows in cell c of (1 - 1/n_g) for that row's group. Cells
# whose athlete-seasons are short contribute little, and preconditioning by this
# is what keeps CG to tens of iterations instead of thousands.
def operatorDiagonal(course, group, n_cells, n_groups, h=None,
                     sc=None, ridge=0.0, exact=True):
    """
    diag(D' M D) for Jacobi preconditioning.

    ★ THE APPROXIMATE FORM WAS WRONG BY A FACTOR OF ~12. (D'MD)_cc is the sum
      of M[r,r'] over ALL PAIRS of rows in cell c, not just the diagonal terms
      M[r,r]. The cross terms are what an athlete racing the same venue twice
      contributes, and they dominate at any well-attended course.
      Measured on a synthetic corpus: true diagonal vs the approximation
      differed by up to 12.7 in absolute terms, which leaves the preconditioner
      unable to damp the small-eigenvalue directions -- exactly the ones that
      made the split solve diverge.

      exact=True computes the cross terms properly. It costs one extra pass over
      the rows per call, and the call happens once per solve.
    """
    count = np.bincount(group, minlength=n_groups).astype(np.float64)
    np.maximum(count, 1.0, out=count)
    per_row = 1.0 - 1.0 / count[group]
    if sc is not None:
        # the slope removes a further sc^2 / (sum sc^2 + K) from each row
        den = np.bincount(group, weights=sc * sc, minlength=n_groups) + ridge
        per_row = per_row - sc * sc / np.maximum(den, 1e-12)[group]
        np.maximum(per_row, 1e-9, out=per_row)

    if exact:
        # ★ THE EXACT DIAGONAL. Verified against an explicitly constructed
        #   operator to 1e-14:
        #
        #       (D'MD)_cc = sum over groups g of
        #                   [ n_cg  -  n_cg^2 / n_g  -  s_cg^2 / den_g ]
        #
        #   where n_cg is the number of rows in cell c AND group g, and s_cg is
        #   the sum of the centred sport indicator over those rows.
        #
        #   The middle and last terms are the CROSS TERMS -- what two rows in
        #   the same cell and the same group contribute to each other. The old
        #   approximation summed only the r == r' terms and was wrong by up to
        #   12.7 in absolute value, which left the Jacobi preconditioner unable
        #   to damp the small-eigenvalue directions. That is what let the split
        #   solve diverge.
        key = course.astype(np.int64) * n_groups + group
        uniq, inv = np.unique(key, return_inverse=True)
        cell_of = (uniq // n_groups).astype(np.int64)
        grp_of = (uniq % n_groups).astype(np.int64)

        w = h if h is not None else np.ones(course.size)
        n_cg = np.bincount(inv, weights=w * w, minlength=uniq.size)
        contrib = n_cg - (n_cg * n_cg) / count[grp_of]
        if sc is not None:
            s_cg = np.bincount(inv, weights=w * w * sc, minlength=uniq.size)
            contrib = contrib - (s_cg * s_cg) / np.maximum(den, 1e-12)[grp_of]
        out = np.bincount(cell_of, weights=contrib, minlength=n_cells)
        return np.maximum(out, 1e-9)

    return np.bincount(course, weights=per_row, minlength=n_cells)


# ------------------------------------------------------------------ #
# CHUNK 4 -- THE SOLVE
# ------------------------------------------------------------------ #

# _dot
# Purpose:   float64 inner product, guarded against the empty case.
def _dot(a, b):
    return float(np.dot(a, b))


# solveDelta
# Purpose:   preconditioned conjugate gradient on D' M_A D delta = D' M_A y.
# Arguments: y, course, group, n_cells, n_groups; tol, max_iter.
# Output:    (delta ndarray, iterations, relative residual)
#
# ★ WHY THE GAUGE NEEDS NO PRIOR. The null space of the operator is the
#   indicator of each connected component. The right-hand side is orthogonal to
#   it automatically: summing D' M_A y over a component sums demeaned residuals
#   over whole athlete-seasons, and each of those sums to zero. So CG started at
#   zero never enters the null space and lands on the minimum-norm solution --
#   per-component mean zero, no region prior, no global re-centring, nothing for
#   the two to fight over.
#
# Syntax: the CG recurrence is standard; `z` is the preconditioned residual and
# `rz` the residual-preconditioner inner product that replaces r'r.
def solveDelta(y, course, group, n_cells, n_groups, tol=1e-10, max_iter=500,
               h=None, sc=None, ridge=0.0):
    """
    ★ h IS THE ABILITY TILT. With h=None this is the plain two-way model, where
      a course multiplies every runner's time by the same factor.

      Measured, that is false. Mean residual by (cell difficulty x athlete
      rating) over 51M rows tilts systematically, and the tilt tracks difficulty
      at corr = -0.993 across five quintiles:

          delta_eff = delta * (1 - 0.031 * (rating - 100) / 10)

      A hard course costs a slow runner MORE than delta says and an elite runner
      LESS. Checked against a single venue held out by hand: Steens Mountain,
      delta 0.45, rating band 140 -- predicted error 7.8 rating points, measured
      7.87.

      Passing h makes the design entry for a row h_r instead of 1, so delta is
      the effect at h = 1 (rating 100) and every row is fitted at its own scale.
    """
    dm = (demeanWithin(y, group, n_groups) if sc is None
          else demeanWithinSport(y, group, sc, n_groups, ridge))
    rhs = np.bincount(course, weights=dm if h is None else h * dm,
                      minlength=n_cells)

    diag = operatorDiagonal(course, group, n_cells, n_groups, h=h,
                            sc=sc, ridge=ridge)
    inv = np.where(diag > 0, 1.0 / np.maximum(diag, 1e-12), 0.0)

    delta = np.zeros(n_cells, dtype=np.float64)
    r = rhs.copy()
    z = inv * r
    p = z.copy()
    rz = _dot(r, z)
    rhs_norm = np.sqrt(_dot(rhs, rhs)) or 1.0
    rel, best_rel = 1.0, np.inf

    for it in range(max_iter):
        Ap = applyOperator(p, course, group, n_cells, n_groups, h=h,
                           sc=sc, ridge=ridge)
        pAp = _dot(p, Ap)
        # ⚠ DIVERGENCE, NOT SLOW CONVERGENCE. On 55M real rows the split
        #   operator sent the relative residual from 1.8e-03 at iteration 26 to
        #   4.6e-01 at 39; delta blew up, exp(alpha) overflowed, and ratings
        #   came out as 10^92. Only a later crash stopped that reaching the
        #   database. A residual that GROWS is never recoverable -- stop.
        if rel > 10.0 * best_rel:
            raise RuntimeError(
                f"CG DIVERGED at iteration {it + 1}: relative residual "
                f"{rel:.2e} against a best of {best_rel:.2e}. The operator is "
                f"ill-conditioned -- with sc set this means the sport "
                f"direction is near-null. Re-run without --split, or raise the "
                f"ridge.")
        if pAp <= 0:
            # ⚠ NOT A CLEAN CONVERGENCE. The operator has run out of range
            #   space, which with sc != None means the sport direction is
            #   near-null and delta is only partly determined. Say so loudly:
            #   an unconverged delta silently poisons signalVariance downstream.
            print(f"    ⚠ CG STOPPED AT ITERATION {it + 1} with relative "
                  f"residual {rel:.2e} -- the operator is numerically "
                  f"exhausted, NOT converged.")
            break
        a = rz / pAp
        delta += a * p
        r -= a * Ap

        rel = np.sqrt(_dot(r, r)) / rhs_norm
        best_rel = min(best_rel, rel)
        if it % 25 == 0 or rel < tol:
            print(f"  cg {it + 1:>4}: relative residual {rel:.3e}")
        if rel < tol:
            break

        z = inv * r
        rz_new = _dot(r, z)
        p = z + (rz_new / rz) * p
        rz = rz_new

    return delta, it + 1, rel


# anchorToWeightedMean
# Purpose:   fix the remaining per-component constant the engine's way --
#            result-weighted mean zero over the solved cells.
# Arguments: delta; weights -- rows per cell; solved -- boolean mask.
#
# Only the LEVEL moves. Every difference between cells inside a component is
# already determined by the data and is untouched by this.
def anchorToWeightedMean(delta, weights, solved):
    if not solved.any() or weights[solved].sum() <= 0:
        return delta
    level = np.average(delta[solved], weights=weights[solved])
    out = delta.copy()
    out[solved] -= level
    return out


# ------------------------------------------------------------------ #
# CHUNK 4b -- PRECISION SHRINKAGE
# ------------------------------------------------------------------ #
#
# WHY THIS IS NEEDED, from the first real run
#   Untrimmed difficulty sd was 0.0609 against reference_fit's 0.0445, but
#   trimming 1% off each end gave 0.0446 -- so the excess lived entirely in the
#   tails. And sd fell monotonically with cell degree:
#
#       degree    2-4   0.1099        degree  50-199   0.0551
#       degree    5-9   0.0940        degree 200-999   0.0447
#       degree  10-49   0.0722        degree   1000+   0.0348
#
#   A real spread would be FLAT in degree. A 3.2x decline is the signature of
#   estimation noise: every one of the 15 worst cells had degree <= 26 with
#   rows == degree, i.e. each athlete raced there exactly once.
#
#   This is limited-mobility bias -- the known weakness of two-way fixed effects
#   on a short panel. Each alpha is estimated from ~4.5 races, that noise
#   propagates into delta, and it is SYMMETRIC, which is why both the positive
#   and negative tails came out inflated.
#
# ★ COMPUTED, NOT SWEPT. Each cell's sampling variance comes from the operator
#   diagonal that conjugate gradient already needed for preconditioning, and the
#   signal variance comes from method of moments. There is no constant to tune,
#   which is the difference between this and LINK_SHRINK_K.


# residualVariance
# Purpose:   sigma^2 of eps, with the right degrees of freedom.
# Arguments: y, delta, course, group, n_cells, n_groups.
# Output:    float.
#
# The residual is M_A(y - D delta): alpha is already absorbed by the demeaning,
# so the df cost of the athlete-season effects has to be subtracted explicitly.
# n_cells - 1 because one cell level is the gauge and is not estimated.
def residualVariance(y, delta, course, group, n_cells, n_groups):
    e = demeanWithin(y - delta[course], group, n_groups)
    ssr = float(np.dot(e, e))
    n_used = int((np.bincount(group, minlength=n_groups) > 0).sum())
    df = max(y.size - n_used - (n_cells - 1), 1)
    return ssr / df


# cellVariance
# Purpose:   per-cell sampling variance of delta-hat.
# Arguments: sigma2; diag -- diag(D' M_A D).
# Output:    per-cell variance; inf where the cell carries no information.
#
# ⚠ THIS IS A LOWER BOUND. The exact variance is sigma^2 * (A^-1)_cc, and
#   (A^-1)_cc >= 1/A_cc always, with the gap largest for poorly-connected cells
#   -- exactly the ones needing the most shrinkage. So this UNDER-shrinks the
#   worst cells. Deliberately conservative: under-shrinking leaves a visible
#   residual slope in the sd-by-degree table, which is diagnosable. Over-
#   shrinking would flatten the table while quietly destroying real signal.
#
#   If the degree table does not go flat, that is the evidence this bound is too
#   loose, and the fix is a Hutchinson estimate of (A^-1)_cc -- k CG solves
#   against random probes.
def cellVariance(sigma2, diag):
    return np.where(diag > 0, sigma2 / np.maximum(diag, 1e-12), np.inf)


# signalVariance
# Purpose:   tau^2, the variance of TRUE difficulty, by method of moments.
# Arguments: delta, v, solved.
# Output:    (tau2, tau2_high_degree) -- the MoM estimate and an independent
#            check from the best-measured cells.
#
# E[delta_hat^2] = tau^2 + E[v], so tau^2 = mean(delta^2) - mean(v). The second
# return value repeats it on cells whose v is negligible; if the two disagree
# badly, the variance model is wrong and shrinkage should not be trusted.
def signalVariance(delta, v, solved, diag):
    """
    tau^2, the variance of TRUE difficulty.

    ⚠ THE METHOD-OF-MOMENTS ESTIMATE CAN GO NEGATIVE AND MUST NOT BE TRUSTED
      ALONE. tau^2 = mean(delta^2) - mean(v), so if the solve did not converge
      the residual is large, mean(v) swamps mean(delta^2), and tau^2 <= 0. The
      weight tau^2/(tau^2+v) is then ZERO for every cell and the entire
      difficulty array collapses to zeros -- silently, because a weight of zero
      is a legal number.

      Observed: a split solve that stopped at 35 CG iterations with relative
      residual 2.1e-03 gave tau = 0.00000 while the best-measured cells gave
      0.02635. Every cell was zeroed.

      So the high-degree estimate is the fallback. Cells with the most
      information have negligible v, which makes mean(delta^2) - mean(v) a
      direct read of tau^2 there and immune to a bad global residual.
    """
    mom = float((delta[solved] ** 2).mean() - v[solved].mean())

    # Best-measured decile by information content, where v is smallest.
    if solved.sum() >= 10:
        cut = np.percentile(diag[solved], 90.0)
        best = solved & (diag >= cut)
        high = float((delta[best] ** 2).mean() - v[best].mean())
    else:
        high = mom

    # ★ THREE ESTIMATES, IN ORDER OF TRUST, because tau^2 must never be zero:
    #   a zero weight silently zeroes every difficulty in the array.
    #     1. method of moments -- correct when the solve converged
    #     2. the best-measured cells -- immune to a bad global residual
    #     3. the raw variance of delta among high-information cells -- an
    #        UPPER bound, since it still contains their (small) sampling noise,
    #        but always positive and never absurd.
    if mom <= 0 or (high > 0 and mom < 0.25 * high):
        if high > 0:
            print(f"    ⚠ method-of-moments tau^2 = {mom:.2e} unusable; "
                  f"using best-measured cells (tau {high ** 0.5:.5f})")
            mom = high
        else:
            cut = np.percentile(diag[solved], 90.0) if solved.sum() >= 10 else 0
            best = solved & (diag >= cut)
            fallback = float(delta[best].var()) if best.any() else 1e-12
            print(f"    ⚠ BOTH tau estimates non-positive -- the solve did not "
                  f"converge. Falling back to var(delta) on the best-measured "
                  f"cells (tau {fallback ** 0.5:.5f}); shrinkage will be weak.")
            mom = high = fallback

    return max(mom, 1e-12), max(high, 1e-12)


# shrinkDelta
# Purpose:   ★ THE SHRINKAGE. Scale each cell by its own precision.
# Arguments: delta, v, tau2, solved.
# Output:    (shrunk delta, weight per cell)
#
#     weight = tau^2 / (tau^2 + v_c)
#
# A cell measured by a thousand athlete-seasons keeps essentially all of its
# estimate; a cell measured by three keeps almost none. This is the empirical
# Bayes posterior mean under a normal prior, so it minimises expected squared
# error -- it is not a fudge toward zero.
def shrinkDelta(delta, v, tau2, solved):
    weight = np.where(solved, tau2 / (tau2 + v), 0.0)
    return delta * weight, weight


# reportShrinkage
# Purpose:   show what the shrinkage did, and to which cells.
def reportShrinkage(sigma2, tau2, tau2_high, weight, degree, solved):
    print("\n[pair] ---- precision shrinkage ----")
    print(f"    sigma (residual)  {np.sqrt(sigma2):.5f}")
    print(f"    tau   (signal)    {np.sqrt(tau2):.5f}"
          f"   [best-measured cells: {np.sqrt(tau2_high):.5f}]")
    if abs(np.sqrt(tau2) - np.sqrt(tau2_high)) > 0.5 * np.sqrt(tau2):
        print("    ⚠ the two tau estimates disagree -- variance model suspect")
    print("    kept fraction by degree:")
    for lo, hi in ((2, 4), (5, 9), (10, 49), (50, 199), (200, 999),
                   (1000, 10 ** 9)):
        m = solved & (degree >= lo) & (degree <= hi)
        if m.any():
            label = f"{lo}+" if hi > 10 ** 8 else f"{lo}-{hi}"
            print(f"      degree {label:>9}: {weight[m].mean():.3f}")
    print("[pair] --------------------------------")


# ------------------------------------------------------------------ #
# CHUNK 5 -- REPORT
# ------------------------------------------------------------------ #

# reportFit
# Purpose:   the numbers that decide whether this beats the ALS engine.
# Arguments: delta, difficulty; solved; weights; y, course, group, n_groups.
#
# `resid sd` is the honest goodness-of-fit: the within-athlete-season residual
# after removing delta. The ALS engine has no comparable number, so this is the
# baseline for the next iteration of THIS method rather than a cross-comparison.
def reportFit(delta, difficulty, solved, weights, y, course, group, n_groups):
    fitted = demeanWithin(y - delta[course], group, n_groups)
    print("\n[pair] ---- fit ----")
    print(f"    cells solved      {int(solved.sum()):,}")
    print(f"    difficulty range  [{difficulty[solved].min():+.4f}, "
          f"{difficulty[solved].max():+.4f}]")
    print(f"    difficulty sd     {difficulty[solved].std():.4f}"
          f"   (reference_fit target 0.0445)")
    print(f"    weighted mean     "
          f"{np.average(difficulty[solved], weights=weights[solved]):+.6f}")
    print(f"    residual sd       {fitted.std():.5f}")
    print("[pair] ------------------")


# ------------------------------------------------------------------ #
# CHUNK 6 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(path, min_degree=2):
    cols = loadPack(path)

    keep0 = (cols["course"] >= 0) & (cols["norm"] > 0)
    y_all, _form = buildResponse(cols)

    course = cols["course"][keep0].astype(np.int64)
    y = y_all[keep0]
    group, n_groups = athleteSeasonCodes(cols["athlete"][keep0],
                                         cols["year"][keep0])
    n_cells = len(cols["course_keys"])
    print(f"[pair] {y.size:,} usable rows, {n_groups:,} athlete-seasons, "
          f"{n_cells:,} cells")


    # ★ Through the cached solver, so pair_ratings / pair_write_results /
    #   pair_golive downstream reuse THIS solve instead of repeating it.
    import pair_validate as _pv
    _res = _pv.solveSubset(course, group, y, n_cells, n_groups,
                           min_degree=min_degree, quiet=False)
    delta = _res["delta"]
    degree = _res["degree"]
    diag = _res["diag"]
    sigma2 = _res["sigma2"]
    print(f"[pair] delta ready ({n_cells:,} cells)")

    # informativeMask runs inside solveSubset, so re-apply its row mask here for
    # the reporting arrays rather than recomputing it -- it is up to 20 passes
    # over 55M rows.
    mask, _deg = informativeMask(group, course, n_groups, n_cells,
                                 min_degree=min_degree)
    course, group, y = course[mask], group[mask], y[mask]

    solved = degree >= min_degree
    weights = np.bincount(course, minlength=n_cells).astype(np.float64)

    # ---- precision shrinkage (CHUNK 4b) -------------------------------
    v = cellVariance(sigma2, diag)
    tau2, tau2_high = signalVariance(delta, v, solved, diag)
    delta_s, weight = shrinkDelta(delta, v, tau2, solved)
    reportShrinkage(sigma2, tau2, tau2_high, weight, degree, solved)

    # Anchor AFTER converting to difficulty, so the weighted mean is zero on the
    # same quantity the ALS engine anchors. Anchoring delta then applying expm1
    # leaves a small positive bias by Jensen's inequality.
    raw = np.expm1(anchorToWeightedMean(delta, weights, solved))
    raw[~solved] = 0.0
    difficulty = np.expm1(delta_s)
    difficulty = anchorToWeightedMean(difficulty, weights, solved)
    difficulty[~solved] = 0.0

    print("\n[pair] === RAW (no shrinkage) ===")
    reportFit(delta, raw, solved, weights, y, course, group, n_groups)
    print("[pair] === SHRUNK ===")
    reportFit(delta_s, difficulty, solved, weights, y, course, group, n_groups)

    out = os.path.join(os.path.dirname(os.path.abspath(path)),
                       "pair_difficulty.npz")
    np.savez(out, difficulty=difficulty, difficulty_raw=raw, solved=solved,
             degree=degree, weight=weight, cell_var=v,
             course_keys=np.array(cols["course_keys"], dtype=str))
    print(f"[pair] wrote {out}")


if __name__ == "__main__":
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "packed_XC_TF.npz")
    main(sys.argv[1] if len(sys.argv) > 1 else default)