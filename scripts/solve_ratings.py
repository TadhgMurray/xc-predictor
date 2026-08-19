#!/usr/bin/env python3
# Project: xc-predictor
# File:    scripts/solve_ratings.py
# Purpose: fit athlete ability and course difficulty JOINTLY, by minimising a
#          real objective, and SCORE the result on held-out races.
#
#     python scripts/solve_ratings.py --sport XC                  # sweep lambda, fit, score
#     python scripts/solve_ratings.py --sport XC --ablate         # which heuristic causes the drift?
#     python scripts/solve_ratings.py --sport TF --season-start 2026-01-01
#
# WHY THIS EXISTS
# ---------------
# The engine is a FIXED-POINT ITERATION with heuristics layered on it. Nothing
# is being minimised, so "converged" means "stopped moving", which is a weaker
# claim than "fits best" -- and along a badly-conditioned direction, damping
# makes stalling indistinguishable from arriving. Weakly-connected subgraphs
# live on exactly those directions, which is where the apparent state offsets
# come from: XC and TF drift by state at corr +0.94 despite being fit from
# separate tables with separate parameters. Geography cannot do that. A solver
# can.
#
# In logs the model is LINEAR:
#
#     ln(norm[i,j]) = alpha[i] + delta[j] + sum_k gamma_k * x_k[i,j] + eps
#
# Linear least squares is CONVEX: one global optimum, no path dependence, no
# drift. Regularisation stops being a hand-tuned shrink applied mid-iteration
# and becomes a penalty term in the objective -- so it can be TUNED BY HELD-OUT
# ERROR instead of by eye.
#
# WHAT THIS DOES NOT DO
#   - It does not replace the engine. It writes to its own table/CSV so you can
#     score it against the current output before committing to anything.
#   - It consumes `normalized_time`, so L1-L4 (distance, geometry, era, weather)
#     are already baked in. This is the L5 step only. Folding those in as
#     covariates is the NEXT phase, and the design matrix is built to take them.
#   - No season term. No state term. Deliberately. See --ablate.

import argparse
import os
import sys

sys.path.insert(0, "scripts")

import numpy as np
import pandas as pd

# Reuse the loader/keying from the reference fit rather than duplicating SQL --
# if the cell key drifts between the two scripts the comparison is meaningless.
from reference_fit import (_loadResults, _cellKey, _trimToCore, HS_GRADES,
                           TERRAIN_ANCHORS, MIN_ROWS_PER_CELL,
                           MIN_RACES_PER_ATHLETE)


# ------------------------------------------------------------------ #
# CHUNK 0 — CONSTANTS
# ------------------------------------------------------------------ #

LAMBDA_GRID  = [0.3, 1.0, 3.0, 10.0, 30.0, 100.0]  # ridge on delta, swept
HOLDOUT_FRAC = 0.10
LSQR_ITERS   = 400
RANDOM_SEED  = 17


# ------------------------------------------------------------------ #
# CHUNK 1 — THE DESIGN MATRIX
# ------------------------------------------------------------------ #

# ★ WHY NOT scipy lsqr. The obvious move is to build the sparse design matrix
# and call lsqr. It was tried: on 1.6M rows x 304k columns it had not converged
# after 2,000 Krylov iterations -- the athlete/cell system is badly conditioned
# precisely along the gauge direction, which is the thing we care about. The
# ridge-penalised ALS below minimises the IDENTICAL objective
#
#     ||y - alpha[i] - delta[j] - gamma.x||^2  +  lam_d*||delta||^2 + lam_a*||alpha||^2
#
# by exact coordinate descent (each half-step is the closed-form minimiser for
# its block), converges in seconds, and needs no matrix at all. Convexity is
# what matters here, not the particular algorithm: one global optimum, no path
# dependence.


# _ridgeMeanBy
# Purpose:   the closed-form minimiser for one parameter block.
# Arguments: codes -- group index per row; resid -- y minus the OTHER blocks;
#            n -- number of groups; lam -- ridge strength.
# Output:    ndarray[float], one value per group.
# Syntax:    a plain group mean is sum/count. The ridge solution is
#            sum/(count + lam) -- the penalty acts exactly like `lam` extra
#            observations pinned at zero. That is the whole of empirical Bayes
#            shrinkage in one line, and it degrades smoothly: a cell with 3 rows
#            is pulled most of the way to the prior, one with 30,000 barely
#            moves. No cliff at n=20, because nothing is special about 20.
def _ridgeMeanBy(codes, resid, n, lam):
    total = np.bincount(codes, weights=resid, minlength=n)
    count = np.bincount(codes, minlength=n)
    return total / (count + lam)


# solveRidge
# Purpose:   the convex fit.
# Arguments: y -- CENTRED log times; ai, ci -- codes; na, nc -- level counts;
#            lam_d, lam_a -- ridge on difficulty / ability;
#            covars -- optional (n, k) dense array.
# Output:    (alpha, delta, gamma).
#
# ★ y MUST BE CENTRED. Penalising alpha toward zero is only meaningful if zero
# is the corpus mean log-time; otherwise the penalty drags every athlete toward
# a one-second 5K. lam_a is kept tiny -- it exists to make the problem strictly
# convex, not to shrink anybody.
#
# ★ THE GAUGE FALLS OUT. alpha+delta is unchanged by adding k to every alpha and
# subtracting it from every delta, so the unpenalised problem has no unique
# solution. Penalising delta picks the member of that family with the smallest
# difficulties -- which is what the hand-rolled mean-zero anchor was for, except
# now it is part of the objective instead of being re-imposed each iteration on
# top of a shrink that already moved the target.
def solveRidge(y, ai, ci, na, nc, lam_d, lam_a=1e-6, covars=None, iters=200):
    alpha = np.zeros(na)
    delta = np.zeros(nc)
    gamma = np.zeros(covars.shape[1]) if covars is not None else np.zeros(0)

    for _ in range(iters):
        fixed = delta[ci] + (covars @ gamma if covars is not None else 0.0)
        alpha = _ridgeMeanBy(ai, y - fixed, na, lam_a)

        fixed = alpha[ai] + (covars @ gamma if covars is not None else 0.0)
        delta = _ridgeMeanBy(ci, y - fixed, nc, lam_d)

        if covars is not None:
            # ordinary least squares on what the two factors could not explain
            resid = y - alpha[ai] - delta[ci]
            gamma = np.linalg.lstsq(covars, resid, rcond=None)[0]

    return alpha, delta, gamma


# solveALS
# Purpose:   the ENGINE's algorithm, with each heuristic switchable.
# Arguments: shrink -- LINK_SHRINK_K, or None to disable;
#            damping -- DAMPING, or None for a full step;
#            gate    -- minimum rows for a cell to be fit at all, or None.
# Output:    (alpha, delta) in LOG units.
#
# ★ THIS EXISTS ONLY FOR --ablate. It is the engine's update rule reproduced
# exactly -- unpenalised group means, then shrink, then gate, then damp, then
# re-centre -- so each heuristic can be switched on alone and scored on the
# same holdout as every other config. Do not use it for production fits;
# solveRidge is the one with an objective behind it.
#
# Note the ORDER: shrink pulls toward zero, and the anchor then MOVES zero.
# Composing those two is not the same operation as shrinking a centred
# quantity, which is why it is worth being able to isolate.
def solveALS(y, ai, ci, na, nc, iters=100, shrink=None, damping=None, gate=None):
    cnt_a = np.bincount(ai, minlength=na)
    cnt_c = np.bincount(ci, minlength=nc)
    linked = _linkedCounts(ai, ci, nc)
    alpha = np.zeros(na)
    delta = np.zeros(nc)

    for _ in range(iters):
        alpha = np.bincount(ai, weights=y - delta[ci], minlength=na) / np.maximum(cnt_a, 1)
        raw = np.bincount(ci, weights=y - alpha[ai], minlength=nc) / np.maximum(cnt_c, 1)

        if shrink is not None:
            raw *= linked / (linked + shrink)
        if gate is not None:
            raw[cnt_c < gate] = 0.0

        delta = raw if damping is None else (1 - damping) * delta + damping * raw
        delta -= np.average(delta, weights=np.maximum(cnt_c, 1e-9))

    return alpha, delta


# _linkedCounts
# Purpose:   per cell, how many of its athletes also race SOMEWHERE ELSE.
# Output:    ndarray[float], one per cell.
# Note:      this is the quantity LINK_SHRINK_K divides by. An athlete confined
#            to one cell carries no information separating that cell's
#            difficulty from their own ability.
def _linkedCounts(ai, ci, nc):
    df = pd.DataFrame({"a": ai, "c": ci}).drop_duplicates()
    n_cells_per_ath = df.groupby("a").size()
    df["multi"] = df.a.map(n_cells_per_ath) >= 2
    return df.groupby("c").multi.sum().reindex(range(nc), fill_value=0).to_numpy(float)


# ------------------------------------------------------------------ #
# CHUNK 3 — SCORING
# ------------------------------------------------------------------ #

# _splitHoldout
# Purpose:   hold out a random slice of ROWS (not athletes, not cells).
# Output:    boolean mask, True = training row.
# ★ WHY ROWS. Holding out whole athletes would leave their alpha unestimated
#   and score nothing but the intercept. Holding out rows asks the question
#   that matters: given everything else, how well is THIS race predicted?
def _splitHoldout(n, frac=HOLDOUT_FRAC, seed=RANDOM_SEED):
    rng = np.random.default_rng(seed)
    return rng.random(n) >= frac


# _scorePredictions
# Purpose:   held-out residual sd, the number that decides everything.
# Arguments: y_true, y_pred over the TEST rows only.
# Output:    float, in log units (0.03 ~ 3% typical error).
def _scorePredictions(y_true, y_pred):
    return float(np.std(y_true - y_pred))


# sweepLambda
# Purpose:   ★ pick the ridge strength by HELD-OUT ERROR rather than by eye.
# Output:    (best_lambda, DataFrame of the sweep).
#
# This is the capability the current engine cannot have. LINK_SHRINK_K = 10 and
# DAMPING = 0.3 are numbers with no test attached; here every candidate gets a
# score and the winner is whichever predicts unseen races best. Note the
# holdout curve is FLAT over a wide range -- so lambda can be chosen on a second
# criterion (state spread, anchor signs) at almost no cost in prediction, which
# is a legitimate choice only because the cost is measured.
def sweepLambda(y, ai, ci, na, nc, train, test, covars, df, cells):
    rows = []
    for lam in LAMBDA_GRID:
        cv = covars[train] if covars is not None else None
        alpha, delta, _ = solveRidge(y[train], ai[train], ci[train], na, nc, lam,
                                     covars=cv)
        pred = alpha[ai[test]] + delta[ci[test]]
        d = np.expm1(delta)
        rows.append({"lambda": lam,
                     "holdout_sd": _scorePredictions(y[test], pred),
                     "delta_sd": float(np.std(d)),
                     "state_sd": _stateSpread(df, cells, d)})
        print(f"  lambda={lam:>6.1f}   holdout={rows[-1]['holdout_sd']:.5f}"
              f"   delta_sd={rows[-1]['delta_sd']:.5f}"
              f"   state_sd={rows[-1]['state_sd']:.5f}")
    t = pd.DataFrame(rows)
    return float(t.loc[t.holdout_sd.idxmin(), "lambda"]), t


# _usableTest
# Purpose:   holdout rows whose athlete AND cell were both seen in training.
# Output:    boolean mask.
# Note:      an athlete with no training rows has an unestimated alpha; scoring
#            against it measures nothing. Typically <1% of the holdout is lost.
def _usableTest(train, ai, ci, na, nc):
    seen_a = np.zeros(na, bool); seen_a[ai[train]] = True
    seen_c = np.zeros(nc, bool); seen_c[ci[train]] = True
    return (~train) & seen_a[ai] & seen_c[ci]


# ------------------------------------------------------------------ #
# CHUNK 4 — REPORTS
# ------------------------------------------------------------------ #

# _anchorSigns
# Purpose:   how many known-terrain courses come out the right sign?
# Output:    (correct, total).
def _anchorSigns(cells, delta, sport):
    if sport != "XC":
        return 0, 0
    lookup = pd.Series(delta, index=cells)
    ok = total = 0
    for venue, sign in TERRAIN_ANCHORS.items():
        hit = lookup[lookup.index.str.startswith(venue + "|")]
        if hit.empty:
            continue
        total += 1
        val = hit.iloc[np.argmax(np.abs(hit.to_numpy()))]
        ok += (np.sign(val) == sign)
        print(f"  {venue:<26} {val:+.4f}   expect {'hard' if sign > 0 else 'fast'}"
              f"{'' if np.sign(val) == sign else '   <-- WRONG SIGN'}")
    return ok, total


# _stateSpread
# Purpose:   sd of the mean difficulty per state. A drift-free fit is near zero.
# Output:    float.
def _stateSpread(df, cells, delta):
    per_cell = pd.Series(delta, index=cells)
    st = df.groupby("cellk").state.agg(
        lambda s: s.mode().iloc[0] if len(s.mode()) else None)
    g = pd.DataFrame({"d": per_cell, "state": st}).dropna()
    means = g.groupby("state").d.agg(["mean", "size"])
    return float(means[means["size"] >= 20]["mean"].std())


# ------------------------------------------------------------------ #
# CHUNK 5 — ABLATION
# ------------------------------------------------------------------ #

# runAblation
# Purpose:   ★ WHICH HEURISTIC CAUSES THE DRIFT?
#            Turn shrink / damping / gate on and off independently, score each
#            on the same holdout, and report state spread for each.
# Output:    DataFrame.
#
# Reading it: `state_sd` is the diagnostic. If one row's state spread jumps
# while the others stay flat, that heuristic is the one manufacturing the
# apparent geography -- and you can fix that rather than rebuilding anything.
def runAblation(df, y, ai, ci, na, nc, cells, train, sport):
    combos = [
        ("none (reference)",      dict()),
        ("shrink only",           dict(shrink=10.0)),
        ("damping only",          dict(damping=0.3)),
        ("gate only",             dict(gate=20)),
        ("shrink + damping",      dict(shrink=10.0, damping=0.3)),
        ("all three (engine)",    dict(shrink=10.0, damping=0.3, gate=20)),
        ("ridge lam=10 (new)",    dict(lam=10.0)),
    ]
    rows = []
    test = _usableTest(train, ai, ci, na, nc)
    yc = y - y.mean()                      # centred: solveRidge needs it, ALS is indifferent
    for name, kw in combos:
        if name.startswith("ridge"):
            alpha, delta, _ = solveRidge(yc[train], ai[train], ci[train], na, nc,
                                         kw["lam"])
        else:
            alpha, delta = solveALS(yc[train], ai[train], ci[train], na, nc, **kw)
        pred = alpha[ai[test]] + delta[ci[test]]
        d = np.expm1(delta)
        rows.append({"config": name,
                     "holdout_sd": _scorePredictions(yc[test], pred),
                     "delta_sd": float(np.std(d)),
                     "state_sd": _stateSpread(df, cells, d)})
        print(f"  {name:<20} holdout={rows[-1]['holdout_sd']:.5f}"
              f"   delta_sd={rows[-1]['delta_sd']:.5f}"
              f"   state_sd={rows[-1]['state_sd']:.5f}")
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ #
# CHUNK 6 — ENTRY POINT
# ------------------------------------------------------------------ #

# _prepare
# Purpose:   load -> filter -> key -> trim -> factorise. Shared by both modes.
# Output:    (df, y, ai, ci, na, nc, cells, covars)
def _prepare(sport, start, end, use_altitude):
    df = _loadResults(sport, start, end)
    df = df[df.grade.isin(HS_GRADES)]
    df = df[df.normalized_time.notna() & df.person_id.notna()]
    if sport == "XC":
        df = df[df.distance.notna()]

    df = df.assign(cellk=_cellKey(df, sport))
    df = _trimToCore(df)

    ai, _ = pd.factorize(df.person_id)
    ci, cells = pd.factorize(df.cellk)
    y = np.log(df.normalized_time.to_numpy(np.float64))

    covars = None
    if use_altitude and "altitude_meters" in df:
        alt = df.altitude_meters.fillna(df.altitude_meters.median()).to_numpy()
        covars = ((alt - alt.mean()) / 1000.0).reshape(-1, 1)   # per 1000 m

    print(f"[prep] {len(df):,} rows   {ai.max()+1:,} athletes   {len(cells):,} cells")
    return df, y, ai, ci, ai.max() + 1, len(cells), cells, covars


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sport", choices=["XC", "TF"], default="XC")
    p.add_argument("--season-start", default="2025-07-01")
    p.add_argument("--season-end", default="2026-07-01")
    p.add_argument("--ablate", action="store_true",
                   help="score the engine's heuristics one at a time instead of fitting")
    p.add_argument("--altitude", action="store_true",
                   help="include altitude as a covariate (a real physical effect)")
    p.add_argument("--out", default="exports/solved_difficulty.csv")
    args = p.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    df, y, ai, ci, na, nc, cells, covars = _prepare(
        args.sport, args.season_start, args.season_end, args.altitude)
    train = _splitHoldout(len(y))

    if args.ablate:
        print("\n=== ABLATION: which heuristic manufactures the drift? ===")
        res = runAblation(df, y, ai, ci, na, nc, cells, train, args.sport)
        res.to_csv(f"exports/ablation_{args.sport}.csv", index=False)
        return

    test = _usableTest(train, ai, ci, na, nc)
    ybar = y.mean()
    yc = y - ybar                       # centring: see solveRidge
    print(f"[fit]  train {train.sum():,}   holdout {test.sum():,}")

    print("\n=== ridge sweep, scored on held-out races ===")
    best, sweep = sweepLambda(yc, ai, ci, na, nc, train, test, covars, df, cells)
    print(f"  best lambda = {best}")

    alpha, delta, gamma = solveRidge(yc, ai, ci, na, nc, best, covars=covars)
    delta = np.expm1(delta)

    if covars is not None:
        print(f"\n  altitude coefficient: {gamma[0]:+.5f} per 1000 m"
              f"   (physics expects roughly +0.015 to +0.020)")

    print(f"\n=== result ===")
    print(f"  delta sd    {np.std(delta):.5f}")
    print(f"  state sd    {_stateSpread(df, cells, delta):.5f}")
    print(f"  holdout sd  {sweep.holdout_sd.min():.5f}")

    print("\n=== terrain anchors ===")
    ok, total = _anchorSigns(cells, delta, args.sport)
    if total:
        print(f"\n  correct signs: {ok}/{total}")

    counts = np.bincount(ci, minlength=nc)
    pd.DataFrame({"cellk": cells, "difficulty": delta, "n": counts}) \
      .to_csv(args.out, index=False)
    print(f"\n[out]  {args.out}  ({nc:,} cells)")


if __name__ == "__main__":
    main()