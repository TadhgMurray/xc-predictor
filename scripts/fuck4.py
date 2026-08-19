# Project: xc-predictor
# File:    scripts/diag_convergence.py
# Purpose: Find why buildResultRatings emits 1,009,281 rows out of ~34.6M.
#
# ============================================================================
# WHERE WE ARE
# ============================================================================
# diag_speed_funnel.py proved the loss is NOT in the loader and NOT in
# packResults:
#     loader output            34,632,323 rows
#     packResults keeps        100.0% of a 176,589-row sample (0 unknown_pool)
#     engine emitted            1,009,281 ratings   (2.9%)
#
# So 97% dies in runConvergence -> buildResultRatings, whose final gate is
#     ok = (pm > 0) & (adjusted > 0)
#     adjusted = cols["norm"] / (1.0 + difficulty[course])
#
# `norm` is always in [600, 3600]. So `adjusted <= 0` requires difficulty <= -1.
# And `pm <= 0` requires the athlete's pool to be absent from `means`.
#
# This script runs the REAL packResults and runConvergence, then decomposes `ok`
# into its two causes and reports the state of `difficulty`, `valid` and `wsum`.
# It writes NOTHING.
#
# ============================================================================
# THE HYPOTHESIS
# ============================================================================
# _weightedMeanBy does
#
#     means = np.where(wsum > 0, vsum / np.maximum(wsum, 1e-12), 0.0)
#
# np.where evaluates BOTH branches, so the np.maximum() is there to silence a
# divide-by-zero. But it does not only fire at wsum == 0. It fires for any
# wsum < 1e-12 -- and wsum is a sum of DECAY_K**days_ago with DECAY_K = 0.998.
#
#     a 2015 race weighs 0.998^4000  = 3.4e-4
#     a 2005 race weighs 0.998^7700  = 2.0e-7
#     a 1995 race weighs 0.998^11300 = 1.5e-10
#     a 1985 race weighs 0.998^15000 = 9.2e-14   <-- below the floor
#
# An athlete whose races are all pre-~1990 has a legitimately tiny wsum. The
# clamp replaces their denominator with 1e-12, so their ability comes out ~4
# SECONDS instead of ~1300. `valid` requires only `mean > 0`, so they pass.
#
# Then in computeCourseDifficulties:
#     dev = norm / ability - 1   ->  1300/4 - 1  =  324
# for every course they raced. That inflates `sums`, inflates `centre`, and
#     new[solved] -= centre
# pushes most course difficulties far below -1. adjusted goes negative, and
# every row with a venue is discarded. Rows WITHOUT a venue use d = 0.0 and
# survive -- which is why the survivors (1,009,281) sit near the venueless
# share (2.3% of 34.6M = 796,543).
#
# THE FIX, if this is confirmed:
#     np.divide(vsum, wsum, out=np.zeros_like(vsum), where=wsum > 0)
# `where=` skips the elements instead of clamping them, so a small-but-real
# denominator divides correctly and only a TRUE zero is skipped.
#
# SAFETY: read-only. Runs the engine's math, saves nothing.
#
# USAGE
#   python scripts/diag_convergence.py --sport XC
#   python scripts/diag_convergence.py --sport XC --limit-iters 3   # faster

import argparse
import sys
from datetime import date

import numpy as np

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")

import speed_ratings as sr
from speed_ratings_db import streamResults


# ================================================================== #
# REPORT HELPERS
# ================================================================== #

# _q
# Purpose : a compact quantile line for an array. Quantiles, not mean/std --
#           a single ability of 4 seconds destroys a mean and leaves the median
#           untouched, and it is precisely that row we are hunting.
def _q(name, a, fmt="{:12.4g}"):
    if a.size == 0:
        print(f"  {name:<28} (empty)")
        return
    qs = np.nanpercentile(a, [0, 1, 25, 50, 75, 99, 100])
    cells = "".join(fmt.format(v) for v in qs)
    print(f"  {name:<28}{cells}")


def _header():
    print(f"  {'':<28}{'min':>12}{'p1':>12}{'p25':>12}{'p50':>12}"
          f"{'p75':>12}{'p99':>12}{'max':>12}")


# ================================================================== #
# THE PROBE
# ================================================================== #

# _probeWeights
# Purpose : how many athlete groups have a weight sum below the 1e-12 clamp?
# Those are the groups whose ability is silently rescaled by
# `vsum / np.maximum(wsum, 1e-12)`.
def _probeWeights(cols, n_athletes):
    wsum = np.bincount(cols["athlete"], weights=cols["weight"].astype(np.float64),
                       minlength=n_athletes)
    below = int((wsum < 1e-12).sum())
    zero = int((wsum == 0).sum())
    print(f"\n  athlete groups                 {n_athletes:,}")
    print(f"  wsum == 0 (truly no weight)   {zero:,}")
    print(f"  0 < wsum < 1e-12 (CLAMPED)    {below:,}  "
          f"({100.0 * below / max(n_athletes, 1):.2f}%)")
    if below:
        print(f"  -> those {below:,} athletes get ability = vsum / 1e-12,")
        print(f"     not vsum / wsum. Their ability is inflated by "
              f"1e-12 / wsum, which is >= 1x and unbounded.")
    _header()
    _q("wsum", wsum)
    return wsum


# _probeAbilities
# Purpose : the abilities the engine actually solved, and how many are absurd.
# A distance-race ability is a 5K-equivalent time: it lives in [600, 3600].
# Anything outside that is a solver artefact, not an athlete.
def _probeAbilities(ability, valid):
    print(f"\n  valid athletes                {int(valid.sum()):,} "
          f"of {valid.size:,} ({100.0 * valid.sum() / valid.size:.1f}%)")
    a = ability[valid]
    _header()
    _q("ability (valid only)", a)
    absurd = int(((a < 300) | (a > 5000)).sum())
    print(f"  abilities outside [300, 5000]  {absurd:,}  "
          f"({100.0 * absurd / max(a.size, 1):.3f}%)")
    if absurd:
        print(f"  !! an ability is a 5K-equivalent time. These are solver")
        print(f"     artefacts and they poison every course they touch.")


# _probeDifficulty
# Purpose : the state of `difficulty`, and how many courses are past the cliff.
# `adjusted = norm / (1 + d)`. At d <= -1 the denominator is <= 0, so `adjusted`
# is negative or infinite and `ok` rejects every row on that course.
def _probeDifficulty(difficulty, cols, n_courses):
    print(f"\n  courses                       {n_courses:,}")
    _header()
    _q("difficulty", difficulty)
    lethal = difficulty <= -1.0
    n_lethal = int(lethal.sum())
    print(f"  difficulty <= -1 (LETHAL)     {n_lethal:,}  "
          f"({100.0 * n_lethal / max(n_courses, 1):.1f}% of courses)")

    course = cols["course"]
    on_lethal = int(np.isin(course, np.nonzero(lethal)[0]).sum()) if n_lethal else 0
    venueless = int((course < 0).sum())
    print(f"  rows on a lethal course       {on_lethal:,}")
    print(f"  rows with NO course (d = 0)   {venueless:,}  <- these always survive")
    return lethal


# _probeOk
# Purpose : decompose `ok = (pm > 0) & (adjusted > 0)` into its two causes.
# This is the whole point: the engine reports one number, and the number has two
# possible parents. Reporting the parents is the difference between a diagnosis
# and a guess.
def _probeOk(cols, difficulty, ability, valid, athlete_keys):
    means = sr.poolMeans(ability, valid, athlete_keys)
    print(f"\n  pools with a mean             {len(means):,}")
    for p in sorted(means):
        print(f"    {p:<28}{means[p]:10.1f}s")

    pm_by_athlete = np.array([means.get(k[1], 0.0) for k in athlete_keys],
                             dtype=np.float64)
    pm = pm_by_athlete[cols["athlete"]]

    course = cols["course"]
    d = np.where(course >= 0, difficulty[np.maximum(course, 0)], 0.0)
    adjusted = cols["norm"] / (1.0 + d)

    n = cols["norm"].size
    pm_ok = pm > 0
    adj_ok = adjusted > 0
    ok = pm_ok & adj_ok

    print(f"\n  {'gate':<34}{'passing':>14}{'%':>9}")
    print("  " + "-" * 57)
    print(f"  {'rows entering buildResultRatings':<34}{n:>14,}{100.0:>8.1f}%")
    print(f"  {'pm > 0  (pool has a mean)':<34}{int(pm_ok.sum()):>14,}"
          f"{100.0*pm_ok.sum()/n:>8.1f}%")
    print(f"  {'adjusted > 0  (difficulty > -1)':<34}{int(adj_ok.sum()):>14,}"
          f"{100.0*adj_ok.sum()/n:>8.1f}%")
    print(f"  {'both  = RATINGS WRITTEN':<34}{int(ok.sum()):>14,}"
          f"{100.0*ok.sum()/n:>8.1f}%")

    print(f"\n  failing ONLY pm > 0            {int((~pm_ok & adj_ok).sum()):,}")
    print(f"  failing ONLY adjusted > 0      {int((pm_ok & ~adj_ok).sum()):,}")
    print(f"  failing both                   {int((~pm_ok & ~adj_ok).sum()):,}")
    return ok


def main():
    ap = argparse.ArgumentParser(description="Localize the engine's 97% loss.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--limit-iters", type=int, default=0,
                    help="cap MAX_ITERATIONS (the corruption appears on iter 1)")
    args = ap.parse_args()

    if args.limit_iters:
        sr.MAX_ITERATIONS = args.limit_iters
        print(f"[probe] MAX_ITERATIONS capped at {args.limit_iters}")

    today = date.today()
    print("=" * 92)
    print(f"CONVERGENCE PROBE -- {args.sport}")
    print("=" * 92)

    cols = sr.packResults(streamResults(args.sport), today)
    if cols is None:
        print("no rows")
        return
    n_athletes = len(cols["athlete_keys"])
    n_courses = cols["n_courses"]
    print(f"\n  packResults census: {cols['census']}")
    print(f"  {cols['norm'].size:,} rows   {n_athletes:,} athlete-pools   "
          f"{n_courses:,} venues")

    print(f"\n{'-' * 92}\nWEIGHTS  (the 1e-12 clamp in _weightedMeanBy)\n{'-' * 92}")
    _probeWeights(cols, n_athletes)

    print(f"\n{'-' * 92}\nCONVERGENCE\n{'-' * 92}")
    difficulty, ability, valid = sr.runConvergence(cols, n_athletes, n_courses)

    print(f"\n{'-' * 92}\nABILITIES\n{'-' * 92}")
    _probeAbilities(ability, valid)

    print(f"\n{'-' * 92}\nDIFFICULTIES\n{'-' * 92}")
    _probeDifficulty(difficulty, cols, n_courses)

    print(f"\n{'-' * 92}\nTHE FINAL GATE\n{'-' * 92}")
    _probeOk(cols, difficulty, ability, valid, cols["athlete_keys"])

    print(f"\n{'=' * 92}")
    print("HOW TO READ THIS")
    print("=" * 92)
    print("  If 'failing ONLY adjusted > 0' is the bulk, the difficulties are")
    print("  poisoned and the cause is upstream: absurd abilities, which come")
    print("  from the 1e-12 clamp on wsum. Fix _weightedMeanBy:")
    print("      np.divide(vsum, wsum, out=np.zeros_like(vsum), where=wsum > 0)")
    print("\n  If 'failing ONLY pm > 0' is the bulk, most pools have no valid")
    print("  athlete, and MIN_RACES / the validity rule is the cause instead.")


if __name__ == "__main__":
    main()