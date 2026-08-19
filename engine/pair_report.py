# Project: xc-predictor
# Subset:  Pair Engine -- scoring
#
# Grades pair_difficulty.npz on EXACTLY the metrics the ALS engine reports, by
# calling speed_ratings.reportSolve itself rather than reimplementing it. Any
# reimplementation would be a second source of truth for "is this good", and the
# two would eventually disagree.
#
# Also reports the two things reportSolve does not, both of which the first real
# run made necessary:
#
#   1. TRIMMED sd. The run produced a cell at +2.1183 -- ln(1+d) = 1.14, i.e.
#      athletes running 3.1x slower there than everywhere else. That is bad data,
#      not terrain, and plain least squares has no defence against it. The
#      untrimmed sd of 0.0609 cannot be compared against reference_fit's 0.0445
#      until we know how much of it is a handful of cells.
#
#   2. sd BY DEGREE. ★ The decisive test for limited-mobility bias. Two-way
#      fixed effects with a short panel -- 4.5 races per athlete-season here --
#      estimates each alpha from very few rows, and that noise propagates into
#      delta, inflating its apparent spread. This is the AKM incidental-
#      parameter problem. If sd falls as degree rises, the excess over 0.0445 is
#      estimation noise rather than real difficulty spread, and the fix is a
#      leave-out or shrinkage estimator. If sd is FLAT in degree, the spread is
#      real and the model is genuinely fitting something the reference does not.

import os
import sys

import numpy as np


# ------------------------------------------------------------------ #
# CHUNK 1 -- inputs
# ------------------------------------------------------------------ #

# loadPairOutput
# Purpose:   the arrays pair_engine wrote.
# Output:    dict with difficulty, solved, degree, course_keys.
def loadPairOutput(path):
    with np.load(path, allow_pickle=False) as d:
        return {k: d[k] for k in d.files}


# rowCounts
# Purpose:   results per cell, the weight reportSolve expects.
# Arguments: cols -- the packed dict; n_cells.
#
# Counted on the FULL pack, not the informative subset, so the weights match
# what the ALS engine used and the two reports are comparable.
def rowCounts(cols, n_cells):
    course = cols["course"]
    real = course >= 0
    return np.bincount(course[real], minlength=n_cells).astype(np.float64)


# ------------------------------------------------------------------ #
# CHUNK 2 -- extra diagnostics
# ------------------------------------------------------------------ #

# trimmedSd
# Purpose:   sd after discarding the tails, to separate "wide" from "has
#            outliers".
# Arguments: values; pct -- percent removed from EACH end.
# Syntax:    np.percentile then a boolean mask; clip would keep the outliers at
#            the boundary and still inflate the sd.
def trimmedSd(values, pct):
    # Needs enough points that removing the tails leaves >= 2, or std() is
    # undefined and numpy warns. Small bins fall back to the untrimmed sd.
    if values.size < 3:
        return float(values.std()) if values.size >= 2 else float("nan")
    lo, hi = np.percentile(values, [pct, 100.0 - pct])
    keep = (values >= lo) & (values <= hi)
    return float(values[keep].std())


# reportTrim
# Purpose:   how much of the spread is a handful of cells.
def reportTrim(difficulty, solved):
    vals = difficulty[solved]
    print("\n[score] ---- spread vs outliers ----")
    print(f"    untrimmed sd      {vals.std():.4f}   "
          f"(reference_fit target 0.0445)")
    for pct in (0.1, 0.5, 1.0, 2.5):
        print(f"    trim {pct:>4}% each end  {trimmedSd(vals, pct):.4f}")
    print(f"    range             [{vals.min():+.4f}, {vals.max():+.4f}]")
    print("[score] --------------------------------")


# reportByDegree
# Purpose:   ★ the limited-mobility-bias test. See the module docstring.
# Arguments: difficulty, solved, degree, counts.
def reportByDegree(difficulty, solved, degree, counts):
    print("\n[score] ---- sd by cell degree ----")
    print("    degree        cells        sd     trim1%      rows")
    for lo, hi in ((2, 4), (5, 9), (10, 49), (50, 199), (200, 999),
                   (1000, 10 ** 9)):
        m = solved & (degree >= lo) & (degree <= hi)
        if not m.any():
            continue
        vals = difficulty[m]
        label = f"{lo}+" if hi > 10 ** 8 else f"{lo}-{hi}"
        print(f"    {label:>9} {int(m.sum()):>12,} {vals.std():>9.4f} "
              f"{trimmedSd(vals, 1.0):>10.4f} {int(counts[m].sum()):>9,}")
    print("    falling sd => limited-mobility bias (estimation noise)")
    print("    flat sd    => the spread is real")
    print("[score] -------------------------------")


# reportWorst
# Purpose:   name the cells driving the tail, so bad data can be found.
# Arguments: difficulty, solved, degree, counts, course_keys, n.
def reportWorst(difficulty, solved, degree, counts, course_keys, n=15):
    idx = np.nonzero(solved)[0]
    order = idx[np.argsort(-np.abs(difficulty[idx]))][:n]
    print(f"\n[score] ---- {n} largest |difficulty| ----")
    print("    difficulty   degree      rows   key")
    for i in order:
        print(f"    {difficulty[i]:+10.4f} {int(degree[i]):>8,} "
              f"{int(counts[i]):>9,}   {course_keys[i]}")
    print("[score] -------------------------------")


# ------------------------------------------------------------------ #
# CHUNK 3 -- entry point
# ------------------------------------------------------------------ #

def main(pack_path, pair_path):
    sys.path.insert(0, os.path.dirname(os.path.abspath(pack_path)) or ".")
    from speed_ratings import loadCols, loadCourseRegions, reportSolve

    cols = loadCols(pack_path)
    out = loadPairOutput(pair_path)

    difficulty = out["difficulty"]
    solved = out["solved"].astype(bool)
    degree = out["degree"]
    course_keys = list(cols["course_keys"])
    n_cells = len(course_keys)
    counts = rowCounts(cols, n_cells)

    print(f"[score] {int(solved.sum()):,}/{n_cells:,} cells solved")

    # The engine's own scorer: anchors, difficulty sd, region-mean sd.
    region = loadCourseRegions(course_keys)
    reportSolve(difficulty, course_keys, region, counts)

    reportTrim(difficulty, solved)
    reportByDegree(difficulty, solved, degree, counts)
    reportWorst(difficulty, solved, degree, counts, course_keys)


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    main(sys.argv[1] if len(sys.argv) > 1
         else os.path.join(here, "packed_XC_TF.npz"),
         sys.argv[2] if len(sys.argv) > 2
         else os.path.join(here, "pair_difficulty.npz"))