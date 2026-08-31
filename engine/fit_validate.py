"""
Purpose:   Validation and uncertainty for the ruler fitters -- the pieces
           fit_distance_exponent / fit_era_corrections / fit_weather_correction
           / fit_geometry_correction have never had.

           Three tools, none of which touch a fitter's own logic:

             coreMask()        -- fit on the well-identified core, not on
                                  everything (fitter problem 3)
             crossValidate()   -- held-out error, split BY ATHLETE
                                  (fitter problem 4)
             bootstrapBands()  -- confidence bands by cluster bootstrap
                                  (fitter problem 4)

           Fitter problems 1 (sequential fitting) and 2 (circularity) are NOT
           addressed here and cannot be: they are the architecture, and the fix
           is the joint global solve. This module is what tells you whether that
           solve is an improvement, so it is worth having first.

Input:     a fitter expressed as fit(idx) -> params and evaluate(params, grid),
           plus the athlete id behind every observation.
Output:    held-out scores, percentile bands, and a core mask.

★ WHY EVERY SPLIT IS BY ATHLETE, NEVER BY ROW. The fitters consume PAIRS: one
  athlete who raced d1 and d2. An athlete contributes many pairs, and those
  pairs share that athlete's ability, their season, their injuries. Split at
  random by row and the same athlete lands in train and test, the model is
  scored on a person it has already seen, and held-out error reads far better
  than it is. Every function here takes athlete ids and splits on them.
"""

import numpy as np


# ------------------------------------------------------------------ #
# Core selection -- fitter problem 3                                  #
# ------------------------------------------------------------------ #

# Purpose:   Which cells are connected well enough to be worth fitting ON.
# Input:     cell_a, cell_b -- the two cells each pair/edge links.
#            athlete       -- who supplied that link.
#            min_athletes  -- an edge needs this many DISTINCT athletes.
# Output:    (mask over the input rows, info dict)
#
# ★ THE RULER MUST NOT BE FIT ON THE DATA IT IS MEANT TO JUDGE. A cell that is
#   pinned to the rest of the world by one athlete cannot correct anything --
#   it can only import its own noise into the curve. Worse, a cell whose label
#   is wrong contributes a systematic offset to the very curve that is supposed
#   to detect the wrongness. So: keep the edges carried by several distinct
#   athletes, take the largest connected component of what survives, and fit
#   the ruler there. The periphery is what the ruler is later USED on, never
#   what it is BUILT from.
def coreMask(cell_a, cell_b, athlete, min_athletes=3):
    cell_a = np.asarray(cell_a)
    cell_b = np.asarray(cell_b)
    athlete = np.asarray(athlete)

    # 1. An edge survives only with enough DISTINCT athletes behind it. Ten
    #    pairs from one person is one person's opinion, not ten measurements.
    lo = np.minimum(cell_a, cell_b)
    hi = np.maximum(cell_a, cell_b)
    edge_key = np.stack([lo, hi], axis=1)
    uniq_edges, edge_idx = np.unique(edge_key, axis=0, return_inverse=True)

    distinct = np.zeros(len(uniq_edges), dtype=np.int64)
    seen = set()
    for e, a in zip(edge_idx, athlete):
        k = (int(e), int(a))
        if k not in seen:
            seen.add(k)
            distinct[e] += 1
    strong_edge = distinct >= min_athletes

    # 2. Largest connected component over the surviving edges. Difficulties are
    #    only mutually comparable INSIDE a component; across components the
    #    gauge is a convention, not a measurement.
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:            # path compression, iterative
            parent[x], x = root, parent[x]
        return root

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for (a, b), ok in zip(uniq_edges, strong_edge):
        if ok:
            union(int(a), int(b))

    if not parent:
        return np.zeros(len(cell_a), dtype=bool), {
            "n_components": 0, "core_cells": 0, "core_rows": 0}

    roots = {}
    for node in list(parent):
        roots.setdefault(find(node), []).append(node)
    biggest = max(roots.values(), key=len)
    core_cells = set(biggest)

    keep = strong_edge[edge_idx]
    keep &= np.array([int(a) in core_cells and int(b) in core_cells
                      for a, b in zip(cell_a, cell_b)])

    return keep, {
        "n_components": len(roots),
        "core_cells": len(core_cells),
        "core_rows": int(keep.sum()),
        "dropped_rows": int((~keep).sum()),
    }


# ------------------------------------------------------------------ #
# Cross-validation -- fitter problem 4                                #
# ------------------------------------------------------------------ #

# Purpose:   K-fold held-out error with every fold split BY ATHLETE.
# Input:     athlete   -- athlete id per row.
#            fit_fn    -- fit_fn(train_idx) -> params
#            score_fn  -- score_fn(params, test_idx) -> float (lower is better)
# Output:    (per-fold scores ndarray, info dict)
def crossValidate(athlete, fit_fn, score_fn, k=5, seed=0):
    athlete = np.asarray(athlete)
    uniq = np.unique(athlete)

    rng = np.random.default_rng(seed)
    fold_of_athlete = rng.integers(0, k, size=len(uniq))
    lookup = {int(a): int(f) for a, f in zip(uniq, fold_of_athlete)}
    fold = np.array([lookup[int(a)] for a in athlete])

    scores, sizes = [], []
    for f in range(k):
        test = np.flatnonzero(fold == f)
        train = np.flatnonzero(fold != f)
        if len(test) == 0 or len(train) == 0:
            continue
        # ★ THE GUARANTEE THIS BUYS: no athlete is in both halves, so the score
        #   measures transfer to an unseen person, not memory of a seen one.
        scores.append(float(score_fn(fit_fn(train), test)))
        sizes.append(len(test))

    scores = np.asarray(scores, dtype=float)
    sizes = np.asarray(sizes, dtype=float)
    return scores, {
        "mean": float(np.average(scores, weights=sizes)) if len(scores) else float("nan"),
        "std": float(scores.std(ddof=1)) if len(scores) > 1 else float("nan"),
        "folds": len(scores),
        "n_athletes": len(uniq),
    }


# ------------------------------------------------------------------ #
# Confidence bands -- fitter problem 4                                #
# ------------------------------------------------------------------ #

# Purpose:   Percentile confidence bands for a fitted curve.
# Input:     athlete    -- athlete id per row.
#            fit_fn     -- fit_fn(idx) -> params
#            eval_fn    -- eval_fn(params) -> ndarray of curve values on a grid
# Output:    (lo, hi, point) ndarrays over the grid.
#
# ★ RESAMPLE ATHLETES, NOT ROWS. The rows are not independent -- one athlete's
#   pairs move together. A row bootstrap treats each pair as fresh evidence and
#   reports bands far too tight, which is the failure mode that lets a curve
#   fitted on thin data at the extremes look authoritative.
def bootstrapBands(athlete, fit_fn, eval_fn, n_boot=200, alpha=0.05, seed=0):
    athlete = np.asarray(athlete)
    uniq = np.unique(athlete)
    by_athlete = {int(a): np.flatnonzero(athlete == a) for a in uniq}

    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(n_boot):
        picked = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([by_athlete[int(a)] for a in picked])
        try:
            draws.append(np.asarray(eval_fn(fit_fn(idx)), dtype=float))
        except Exception:
            continue            # a degenerate resample is dropped, not fatal

    if not draws:
        raise RuntimeError("every bootstrap resample failed to fit")

    D = np.vstack(draws)
    lo = np.percentile(D, 100 * alpha / 2, axis=0)
    hi = np.percentile(D, 100 * (1 - alpha / 2), axis=0)
    point = np.asarray(eval_fn(fit_fn(np.arange(len(athlete)))), dtype=float)
    return lo, hi, point
