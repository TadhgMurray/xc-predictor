# Project: xc-predictor
# Subset:  Pair Engine -- validation and measured shrinkage
#
# TWO THINGS, BOTH REPLACING A GUESS WITH A MEASUREMENT
#
# 1. SPLIT-HALF SHRINKAGE, replacing the variance model that failed.
#
#    The first attempt computed a weight from tau (signal sd) and v (per-cell
#    sampling variance). Both were wrong, in the same direction:
#      - tau came from method of moments, mean(delta^2) - mean(v), and mean(delta^2)
#        was inflated by the very outliers shrinkage exists to remove. Circular.
#        It read 0.0554 against 0.0314 from the best-measured cells.
#      - v used the Jacobi bound sigma^2 / A_cc, which understates the true
#        sigma^2 * (A^-1)_cc most for poorly-connected cells.
#    Result: weights of 0.82-1.00, almost nothing shrunk, and the sd-by-degree
#    table still sloped 0.0852 -> 0.0348.
#
#    ★ THE FIX USES NO VARIANCE MODEL AT ALL. Split the athlete-seasons in two,
#      solve delta independently on each half, and correlate the halves. Two
#      independent measurements of the same true difficulty correlate at
#      c = tau^2 / (tau^2 + v_half). Spearman-Brown converts that to the
#      reliability of the FULL estimate:
#
#          w = 2c / (1 + c)
#
#      and reliability IS the shrinkage weight. Nothing to contaminate: the
#      outlier cell appears in both halves, so it inflates both variances and
#      the correlation is unaffected.
#
# 2. HELD-OUT PREDICTION, replacing difficulty sd as the objective.
#
#    In-sample fit CANNOT rank two estimators. Least squares is by construction
#    the best in-sample fit, so any move toward truth -- shrinkage included --
#    must make in-sample fit worse. Demonstrated on synthetic data where truth
#    was known: raw beat shrunk in-sample (0.028243 vs 0.028528) while shrunk
#    beat raw against truth (0.019012 vs 0.021419). Held-out error ranked them
#    the same way truth did.
#
#    ⚠ AND THE OLD TARGET IS PROBABLY WRONG. reference_fit's 0.0445 comes from
#      the same kind of short-panel two-way comparison, so it carries the same
#      limited-mobility inflation. The 9,138 cells at degree >= 1000 -- 42M rows,
#      the least-biased estimate in this project -- put the true difficulty
#      spread near 0.030-0.035. Held-out error does not care about any of this,
#      which is the point of using it.

import os
import sys

import numpy as np

import pair_engine as pe


# ------------------------------------------------------------------ #
# CHUNK 1 -- SPLITTING
# ------------------------------------------------------------------ #

# splitByGroup
# Purpose:   assign whole athlete-seasons to half A or half B.
# Arguments: group -- athlete-season code per row; n_groups; seed.
# Output:    boolean mask, True = half A.
#
# ★ SPLIT BY ATHLETE-SEASON, NOT BY ROW. Two rows from one athlete-season share
#   an alpha, so splitting rows would put correlated information on both sides
#   and the halves would not be independent -- the correlation would come out
#   too high and the shrinkage too weak.
def splitByGroup(group, n_groups, seed=0):
    rng = np.random.default_rng(seed)
    side = rng.random(n_groups) < 0.5
    return side[group]


# splitByRow
# Purpose:   hold out a random fraction of ROWS for prediction testing.
# Arguments: n_rows; frac; seed.
# Output:    boolean mask, True = held out.
#
# Rows, not athlete-seasons, because the question is "given what we know about
# this athlete and this course, can we predict this race" -- which needs the
# athlete present in training.
def splitByRow(n_rows, frac=0.10, seed=1):
    rng = np.random.default_rng(seed)
    return rng.random(n_rows) < frac


# ------------------------------------------------------------------ #
# CHUNK 2 -- SOLVING A SUBSET
# ------------------------------------------------------------------ #

# ------------------------------------------------------------------ #
#  SOLVE CACHE
# ------------------------------------------------------------------ #

_CACHE_PATH = None          # set on first use, next to the pack


def _cachePath():
    global _CACHE_PATH
    if _CACHE_PATH is None:
        here = os.path.dirname(os.path.abspath(__file__))
        _CACHE_PATH = os.path.join(here, "data", "pair_solve_cache.npz")
    return _CACHE_PATH


def _fingerprint(course, group, y, n_cells, n_groups, min_degree):
    """
    A cheap identity for one solve.

    ★ NOT A HASH OF THE ARRAYS. Hashing 55M float64s costs more than the
      bincounts we are trying to avoid. Row count, cell count, group count and
      the three column sums identify a subset well enough: two different row
      subsets of the same corpus differing only in rows we happened to pick
      would have to match on all six by coincidence.

    ⚠ IT WILL NOT NOTICE A CHANGED FORM CORRECTION that leaves sum(y) intact.
      Delete the cache after any change to rust_fitness or the pack.
    """
    return (f"n{y.size}_c{n_cells}_g{n_groups}_d{min_degree}"
            f"_y{float(y.sum()):.6f}_x{int(course.sum())}_p{int(group.sum())}")


def _loadCached(key):
    path = _cachePath()
    if not os.path.exists(path):
        return None
    try:
        with np.load(path, allow_pickle=False) as f:
            if f"{key}__delta" not in f.files:
                return None
            return {"delta": f[f"{key}__delta"],
                    "degree": f[f"{key}__degree"],
                    "diag": f[f"{key}__diag"],
                    "sigma2": float(f[f"{key}__sigma2"][0])}
    except Exception:
        return None


def _saveCached(key, out):
    """Merge into the existing cache rather than replacing it."""
    path = _cachePath()
    payload = {}
    if os.path.exists(path):
        try:
            with np.load(path, allow_pickle=False) as f:
                payload = {k: f[k] for k in f.files}
        except Exception:
            payload = {}
    payload[f"{key}__delta"] = out["delta"]
    payload[f"{key}__degree"] = out["degree"]
    payload[f"{key}__diag"] = out["diag"]
    payload[f"{key}__sigma2"] = np.array([out["sigma2"]])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez(path, **payload)


# solveSubset
# Purpose:   run the full Stage-1/2 pipeline on a row subset.
# Arguments: course, group, y -- already restricted; n_cells, n_groups.
# Output:    dict with delta, degree, diag, sigma2.
#
# Re-runs informativeMask on the subset: a cell that was identified on the full
# data may not be on half of it, and pretending otherwise is how a split-half
# correlation gets quietly computed on cells one half never saw.
def solveSubset(course, group, y, n_cells, n_groups, min_degree=2, quiet=True,
                cache=True, h=None, sc=None, ridge=0.0):
    """
    ★ CACHED. The chain runs eight of these -- pair_engine, pair_ratings,
      pair_write_results and pair_golive each solve the FULL corpus, and
      pair_validate solves four subsets -- and four of the eight are byte-for-
      byte the same solve. Each costs ~296 CG iterations over 55M rows plus up
      to 20 informativeMask passes.

      Results are keyed on a fingerprint of the inputs, so a subset solve and a
      full solve never collide. Delete engine/data/pair_solve_cache.npz after
      changing rust_fitness, the pack, or anything upstream of y.
    """
    key = _fingerprint(course, group, y, n_cells, n_groups, min_degree)
    if h is not None:
        # a tilted solve is a DIFFERENT solve; never share a cache entry
        key += f"_h{float(h.sum()):.4f}"
    if sc is not None:
        # ⚠ v2 IN THE KEY, BECAUSE THE OPERATOR CHANGED. Entries written before
        #   sc was re-centred on the subset came from an ASYMMETRIC operator --
        #   see below -- and a cache hit would hand one straight back.
        key += f"_sc2{float(np.abs(sc).sum()):.2f}_k{ridge:g}"
    if cache:
        hit = _loadCached(key)
        if hit is not None:
            if not quiet:
                print(f"    cache hit ({y.size:,} rows) -- solve skipped")
            return hit

    mask, degree = pe.informativeMask(course=course, group=group,
                                      n_groups=n_groups, n_cells=n_cells,
                                      min_degree=min_degree)
    c, g, yy = course[mask], group[mask], y[mask]
    hh = None if h is None else h[mask]
    # ⚠ sc MUST BE RE-CENTRED ON THE SUBSET, AND MASKING ALONE DOES NOT DO IT.
    #   sportCentered subtracts each group's MEAN sport indicator, computed
    #   over every row. informativeMask then drops rows -- 6.4% of the corpus
    #   on the run that exposed this -- and what is left no longer has group
    #   mean zero.
    #
    # ★ AND THAT BREAKS THE ONE PROPERTY CG NEEDS. The projector is
    #   (I - Q)M with Q = sc sc'/(sc'sc), and it is symmetric only when
    #   M sc == sc, which is exactly "sc is centred within group". Off-centre,
    #   QM != MQ and the operator is not symmetric -- so conjugate gradient is
    #   not solving anything. Measured on a synthetic corpus thinned to the
    #   same 93.6%: relative asymmetry 8.8e-02 against 5.6e-15 once re-centred.
    #
    #   demeanWithin IS the centring operation, so this is one line and it is
    #   a no-op whenever the mask keeps every row.
    ss = None if sc is None else pe.demeanWithin(sc[mask], g, n_groups)
    delta, iters, rel = pe.solveDelta(yy, c, g, n_cells, n_groups,
                                      max_iter=800, h=hh, sc=ss, ridge=ridge)
    if not quiet:
        print(f"    solved on {yy.size:,} rows in {iters} CG iterations "
              f"(rel {rel:.1e})")
    diag = pe.operatorDiagonal(c, g, n_cells, n_groups, h=hh,
                               sc=ss, ridge=ridge)
    out = {"delta": delta, "degree": degree, "diag": diag,
           "sigma2": pe.residualVariance(yy, delta, c, g, n_cells, n_groups)}
    if cache:
        _saveCached(key, out)
    return out


# refitAlpha
# Purpose:   ability per athlete-season, given delta.
# Arguments: y, delta, course, group, n_groups.
# Output:    per-group array; 0.0 for groups with no rows.
#
# Needed only for prediction: the solve itself never forms alpha, because
# demeanWithin absorbs it. Here we want an actual predicted time, so alpha has
# to be materialised -- it is the group mean of (y - delta), which is its
# least-squares value given delta.
def refitAlphaBeta(y, delta, course, group, n_groups, sc, ridge):
    """
    Ability AND the shrunk sport offset, given delta.

    Returns (alpha, beta, count). Prediction is alpha[g] + beta[g]*sc.

    ★ beta USES THE SAME RIDGE AS THE SOLVE. Fitting it unpenalised here would
      hand a two-race athlete a full offset that the solve never granted, and
      the held-out prediction would then use a beta the difficulties were never
      fitted against.
    """
    resid = y - delta[course]
    total = np.bincount(group, weights=resid, minlength=n_groups)
    count = np.bincount(group, minlength=n_groups)
    alpha = np.where(count > 0, total / np.maximum(count, 1), 0.0)

    rc = resid - alpha[group]
    num = np.bincount(group, weights=sc * rc, minlength=n_groups)
    den = np.bincount(group, weights=sc * sc, minlength=n_groups) + ridge
    beta = num / np.maximum(den, 1e-12)
    return alpha, beta, count


def refitAlpha(y, delta, course, group, n_groups, h=None):
    resid = y - (delta[course] if h is None else h * delta[course])
    total = np.bincount(group, weights=resid, minlength=n_groups)
    count = np.bincount(group, minlength=n_groups)
    return np.where(count > 0, total / np.maximum(count, 1), 0.0), count


# ------------------------------------------------------------------ #
# CHUNK 3 -- MEASURED SHRINKAGE
# ------------------------------------------------------------------ #

# _bucketEdges
# Purpose:   the degree bands used for every report in this project, so the
#            tables line up.
def _bucketEdges():
    return ((2, 4), (5, 9), (10, 49), (50, 199), (200, 999), (1000, 10 ** 9))


# spearmanBrown
# Purpose:   reliability of the full estimate from a half-vs-half correlation.
# Arguments: c -- observed split-half correlation.
# Output:    w in [0, 1].
#
# Derivation: two independent half estimates of one truth correlate at
# c = tau^2 / (tau^2 + v_half), and a half sample has roughly twice the variance
# of the full, so v_full = v_half / 2. Substituting,
#     w = tau^2 / (tau^2 + v_full) = 2c / (1 + c).
# Negative correlations mean the halves disagree entirely -- no recoverable
# signal -- so they floor at zero rather than producing a negative weight.
def spearmanBrown(c):
    if not np.isfinite(c) or c <= 0.0:
        return 0.0
    return float(min(2.0 * c / (1.0 + c), 1.0))


# reliabilityByDegree
# Purpose:   ★ THE MEASUREMENT. Observed half-vs-half agreement per degree band.
# Arguments: dA, dB -- delta from each half; okA, okB -- solved masks;
#            degree -- from the FULL data, so bands are stable.
# Output:    list of (label, lo, hi, n_cells, corr, weight)
#
# A cell must be identified in BOTH halves to contribute: if only one half saw
# it, there is nothing to correlate.
def reliabilityByDegree(dA, dB, okA, okB, degree):
    both = okA & okB
    rows = []
    for lo, hi in _bucketEdges():
        m = both & (degree >= lo) & (degree <= hi)
        n = int(m.sum())
        label = f"{lo}+" if hi > 10 ** 8 else f"{lo}-{hi}"
        if n < 30 or dA[m].std() == 0 or dB[m].std() == 0:
            rows.append((label, lo, hi, n, float("nan"), float("nan")))
            continue
        c = float(np.corrcoef(dA[m], dB[m])[0, 1])
        rows.append((label, lo, hi, n, c, spearmanBrown(c)))
    return rows


# fitInformationConstant
# Purpose:   one constant K such that w = I / (I + K) reproduces the measured
#            reliabilities, giving a SMOOTH per-cell weight instead of a step
#            function at bucket boundaries.
# Arguments: rows -- from reliabilityByDegree; diag -- per-cell information;
#            degree.
# Output:    K (float).
#
# Per bucket, invert w = I/(I+K) to K = I * (1 - w) / w using that bucket's
# median information. The median across buckets is then taken, weighted by cell
# count -- median rather than mean because a single badly-estimated bucket
# should not move the answer.
def fitInformationConstant(rows, diag, degree):
    ks, wts = [], []
    for _label, lo, hi, n, _c, w in rows:
        if not np.isfinite(w) or w <= 0.0 or w >= 1.0 or n < 30:
            continue
        m = (degree >= lo) & (degree <= hi) & (diag > 0)
        if not m.any():
            continue
        info = float(np.median(diag[m]))
        ks.append(info * (1.0 - w) / w)
        wts.append(n)

    if not ks:
        return 0.0
    ks = np.array(ks, dtype=np.float64)
    wts = np.array(wts, dtype=np.float64)
    order = np.argsort(ks)
    ks, wts = ks[order], wts[order]
    cum = np.cumsum(wts) / wts.sum()
    return float(ks[np.searchsorted(cum, 0.5)])


# reportReliability
# Purpose:   print the measurement and the fitted constant.
def reportReliability(rows, K, diag, solved):
    print("\n[valid] ---- split-half reliability ----")
    print("    degree        cells    corr    weight")
    for label, _lo, _hi, n, c, w in rows:
        cs = "   n/a" if not np.isfinite(c) else f"{c:6.3f}"
        ws = "   n/a" if not np.isfinite(w) else f"{w:6.3f}"
        print(f"    {label:>9} {n:>12,}  {cs}    {ws}")
    print(f"    fitted K (information units): {K:,.1f}")
    if solved.any():
        w = diag / (diag + K)
        print(f"    implied weight: median {np.median(w[solved]):.3f}, "
              f"min {w[solved].min():.3f}, max {w[solved].max():.3f}")
    print("[valid] --------------------------------")


# ------------------------------------------------------------------ #
# CHUNK 4 -- HELD-OUT PREDICTION
# ------------------------------------------------------------------ #

# evaluateHoldout
# Purpose:   ★ THE OBJECTIVE. Prediction error on rows the fit never saw.
# Arguments: delta -- fitted on train; alpha, gcount -- from refitAlpha on
#            train; y_te, course_te, group_te; solved -- cells identified in
#            train; label.
# Output:    (coverage, error_sd)
#
# COVERAGE MATTERS AS MUCH AS ERROR. A held-out row is unpredictable if its cell
# was not identified in training, or its athlete-season has no training rows.
# Error is reported on the covered subset ONLY -- averaging in unpredictable rows
# would mix two different quantities -- so the coverage figure has to be printed
# beside it or the error is meaningless.
def evaluateHoldout(delta, alpha, gcount, y_te, course_te, group_te,
                    solved, label, beta=None, sc_te=None):
    """Held-out error. Pass beta and sc_te to score a SPORT-SPLIT fit.

    ⚠ WITHOUT THEM THIS SCORES THE SHARED MODEL, whatever produced `delta`.
      The prediction is alpha[g] + delta[c]; a split fit predicts
      alpha[g] + beta[g]*sc + delta[c], and leaving beta out does not merely
      lose the improvement -- it measures a model nobody fitted. That is how
      pair_all's --validate reported a split run at exactly the shared
      number (0.047039, 2026-08-31), and it is the same blind spot that got
      --tilt wrongly rejected: see apply_tilt.py, "the validation had read an
      untilted cached solve".
    """
    cov = solved[course_te] & (gcount[group_te] > 0)
    if not cov.any():
        print(f"    {label:>8}: no predictable held-out rows")
        return 0.0, float("nan")

    err = y_te[cov] - alpha[group_te[cov]] - delta[course_te[cov]]
    if beta is not None and sc_te is not None:
        err = err - beta[group_te[cov]] * sc_te[cov]
    sd = float(err.std())
    mae = float(np.abs(err).mean())
    print(f"    {label:>8}: error sd {sd:.6f}   MAE {mae:.6f}   "
          f"covered {int(cov.sum()):,} ({100.0 * cov.mean():.1f}%)")
    return float(cov.mean()), sd


# ------------------------------------------------------------------ #
# CHUNK 5 -- ENTRY POINT
# ------------------------------------------------------------------ #

def prepare(path):
    """Load the pack and build the model inputs, once."""
    cols = pe.loadPack(path)
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    y_all, _ = pe.buildResponse(cols)
    course = cols["course"][keep].astype(np.int64)
    y = y_all[keep]
    group, n_groups = pe.athleteSeasonCodes(cols["athlete"][keep],
                                            cols["year"][keep])
    return cols, course, group, y, n_groups, len(cols["course_keys"])


def main(path, holdout_frac=0.10):
    cols, course, group, y, n_groups, n_cells = prepare(path)
    print(f"[valid] {y.size:,} rows, {n_groups:,} athlete-seasons, "
          f"{n_cells:,} cells")

    # ---- 1. split-half reliability -> measured shrinkage ------------
    print("\n[valid] solving two independent halves...")
    inA = splitByGroup(group, n_groups, seed=0)
    A = solveSubset(course[inA], group[inA], y[inA], n_cells, n_groups)
    B = solveSubset(course[~inA], group[~inA], y[~inA], n_cells, n_groups)

    full = solveSubset(course, group, y, n_cells, n_groups)
    degree, diag = full["degree"], full["diag"]
    solved = degree >= 2

    rows = reliabilityByDegree(A["delta"], B["delta"],
                               A["degree"] >= 2, B["degree"] >= 2, degree)
    K = fitInformationConstant(rows, diag, degree)
    reportReliability(rows, K, diag, solved)

    weight = np.where(solved & (diag > 0), diag / (diag + K), 0.0)
    delta_sh = full["delta"] * weight

    for name, d in (("raw", full["delta"]), ("shrunk", delta_sh)):
        vals = np.expm1(d)[solved]
        print(f"    {name:>6}: difficulty sd {vals.std():.4f}   "
              f"range [{vals.min():+.4f}, {vals.max():+.4f}]")

    # ---- 2. held-out prediction ------------------------------------
    print(f"\n[valid] held-out prediction ({holdout_frac:.0%} of rows)...")
    te = splitByRow(y.size, frac=holdout_frac, seed=1)
    tr = ~te
    T = solveSubset(course[tr], group[tr], y[tr], n_cells, n_groups)
    t_solved = T["degree"] >= 2
    t_weight = np.where(t_solved & (T["diag"] > 0),
                        T["diag"] / (T["diag"] + K), 0.0)

    print("    (lower error sd is better; this is the objective)")
    for name, d in (("raw", T["delta"]), ("shrunk", T["delta"] * t_weight),
                    ("zero", np.zeros(n_cells))):
        alpha, gcount = refitAlpha(y[tr], d, course[tr], group[tr], n_groups)
        evaluateHoldout(d, alpha, gcount, y[te], course[te], group[te],
                        t_solved, name)
    print("    'zero' ignores course difficulty entirely -- the baseline any")
    print("    difficulty model must beat.")

    out = os.path.join(os.path.dirname(os.path.abspath(path)),
                       "pair_validated.npz")
    np.savez(out, difficulty=np.expm1(delta_sh), difficulty_raw=np.expm1(full["delta"]),
             weight=weight, solved=solved, degree=degree, diag=diag,
             K=np.array([K]), course_keys=np.array(cols["course_keys"], dtype=str))
    print(f"\n[valid] wrote {out}")


if __name__ == "__main__":
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "packed_XC_TF.npz")
    main(sys.argv[1] if len(sys.argv) > 1 else default)