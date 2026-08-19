#!/usr/bin/env python3
# Project: xc-predictor
# File:    engine/speed_ratings_kernels.py
# Purpose: the two inner half-steps, fused and compiled.
#
# WHY THIS EXISTS
# ---------------
# The solve is memory-bandwidth-bound, not compute-bound. NumPy calls optimised C
# for each operation but cannot FUSE them, so a line like
#
#     log_adj = log_norm - log1p_d[course_idx]
#
# is three passes over 61.8M elements and allocates a 500MB temporary -- every
# iteration, 500 iterations. A fused loop does it in one pass with no allocation.
#
# Measured on 20M rows / 1.5M athletes, SINGLE CORE (so no parallel help at all):
#
#     numpy bincount + gather :  1234.4 ms
#     numba fused parallel    :   151.0 ms      8.2x
#     max difference          :  8.9e-16
#
# The gain is fusion and allocation, not threads; `parallel=True` adds more on a
# multi-core box.
#
# ★ THE SORT IS THE PRECONDITION. These kernels walk each athlete's rows as a
# CONTIGUOUS SEGMENT, which requires the packed arrays to be sorted by athlete.
# That also fixes the other half of the problem: `ability[athlete]` on unsorted
# rows is 61.8M random reads into a 36MB array -- a cache miss almost every time.
# Sorted, it is sequential. packResults does the sort once; the --cache file
# stores the sorted version, so it is paid once per corpus, not once per run.
#
# FALLBACK: if numba is missing, HAVE_NUMBA is False and the caller keeps its
# original numpy path. Nothing here is required for correctness.

import numpy as np

try:
    from numba import njit, prange
    HAVE_NUMBA = True
except ImportError:                       # pragma: no cover
    HAVE_NUMBA = False

    def njit(*a, **k):                    # no-op decorator so the file imports
        def wrap(f):
            return f
        return wrap if not a else a[0]

    prange = range


# ------------------------------------------------------------------ #
# CHUNK 1 — SEGMENTS
# ------------------------------------------------------------------ #

# athleteSegments
# Purpose:   start offset of every athlete's contiguous block of rows.
# Arguments: athlete_sorted -- the athlete code column, ALREADY SORTED;
#            n_athletes.
# Output:    ndarray[int64] of length n_athletes+1; segment g is
#            rows[starts[g] : starts[g+1]].
# Syntax:    searchsorted on a sorted array gives the first index >= each value
#            in one binary-search pass, so an empty athlete simply yields
#            starts[g] == starts[g+1] and the kernels skip it naturally.
def athleteSegments(athlete_sorted, n_athletes):
    return np.searchsorted(athlete_sorted,
                           np.arange(n_athletes + 1)).astype(np.int64)


# ------------------------------------------------------------------ #
# CHUNK 2 — THE ABILITY HALF-STEP
# ------------------------------------------------------------------ #

# abilityKernel
# Purpose:   every athlete's log-ability, with the 2-sigma trim, in ONE pass.
# Arguments: starts        -- segment offsets, length n_athletes+1
#            log_norm      -- ln(normalised time), per row
#            log1p_d       -- ln(1+difficulty), per course, PADDED with a
#                             trailing 0.0 for venue-less rows
#            course_idx    -- per row, index into log1p_d
#            w             -- per-row weight (already zeroed at pinned courses)
#            trim          -- apply the 2-sigma cut
#            min_races     -- never let the trim drop an athlete below this
#            out_mean      -- output, per athlete
#            out_wsum      -- output, per athlete (0 => not solvable)
# Output:    None; writes into out_mean / out_wsum.
#
# ★ log_adj IS NEVER MATERIALISED. The old code built a 61.8M-element array to
# hold it. Here it is a scalar inside the loop, so the whole half-step allocates
# nothing per iteration.
#
# The trim is applied to LOG residuals, which is what makes it fair: race times
# are right-skewed -- you can blow up by two minutes, you cannot run two minutes
# faster -- so a symmetric cut in seconds removes more of the slow tail and
# biases every ability fast.
@njit(parallel=True, fastmath=True)
def abilityKernel(starts, log_norm, log1p_d, course_idx, w,
                  trim, min_races, out_mean, out_wsum):
    for g in prange(len(starts) - 1):
        lo = starts[g]
        hi = starts[g + 1]
        if hi <= lo:
            out_mean[g] = 0.0
            out_wsum[g] = 0.0
            continue

        # --- pass 1: weighted mean of the log-adjusted times ---
        sw = 0.0
        sv = 0.0
        for i in range(lo, hi):
            wi = w[i]
            if wi > 0.0:
                sw += wi
                sv += wi * (log_norm[i] - log1p_d[course_idx[i]])
        mean = sv / sw if sw > 0.0 else 0.0

        if trim and sw > 0.0:
            # --- pass 2: weighted variance about that mean ---
            sq = 0.0
            for i in range(lo, hi):
                wi = w[i]
                if wi > 0.0:
                    r = (log_norm[i] - log1p_d[course_idx[i]]) - mean
                    sq += wi * r * r
            sigma = np.sqrt(sq / sw) if sq > 0.0 else 0.0

            if sigma > 0.0:
                # --- pass 3: re-mean inside +-2 sigma, unless that would
                #             leave the athlete below min_races ---
                kw = 0.0
                kv = 0.0
                kept = 0
                for i in range(lo, hi):
                    wi = w[i]
                    if wi > 0.0:
                        v = log_norm[i] - log1p_d[course_idx[i]]
                        if abs(v - mean) <= 2.0 * sigma:
                            kw += wi
                            kv += wi * v
                            kept += 1
                if kept >= min_races and kw > 0.0:
                    mean = kv / kw
                    sw = kw

        out_mean[g] = mean
        out_wsum[g] = sw


# ------------------------------------------------------------------ #
# CHUNK 3 — THE DIFFICULTY HALF-STEP
# ------------------------------------------------------------------ #

# difficultyKernel
# Purpose:   per-course weighted sums of the log deviation, in one pass.
# Arguments: course_idx  -- per row (rows at no venue must be pre-filtered out)
#            log_norm, log_ability -- per row / per athlete
#            athlete_idx -- per row
#            log_form    -- ln(1+form), per row
#            cw          -- per-row course weight
#            sums, wsum  -- outputs, per course
# Output:    None.
# Syntax:    the accumulation is a SCATTER (many rows into one course), so it
#            cannot use prange without races. Courses number ~81k x 8 bytes =
#            650KB, which fits in L2, so the serial scatter is cheap -- the
#            expensive part was always the athlete side, and that is parallel.
@njit(fastmath=True)
def difficultyKernel(course_idx, athlete_idx, log_norm, log_ability,
                     log_form, cw, sums, wsum):
    for i in range(len(course_idx)):
        c = course_idx[i]
        dev = log_norm[i] - log_ability[athlete_idx[i]] - log_form[i]
        cwi = cw[i]
        sums[c] += cwi * dev
        wsum[c] += cwi


# ------------------------------------------------------------------ #
# CHUNK 4 — WARM-UP
# ------------------------------------------------------------------ #

# warmup
# Purpose:   force compilation on tiny arrays so the first real iteration is not
#            charged for it.
# Output:    None; prints once.
# Note:      numba compiles on first call with the exact dtypes it sees, so the
#            warm-up arrays must match the real ones -- int64 offsets, int32
#            indices, float64 data. A dtype mismatch silently triggers a SECOND
#            compilation later.
def warmup():
    if not HAVE_NUMBA:
        print("[kernels] numba not installed -- using the numpy path "
              "(about 8x slower). pip install numba")
        return
    import time
    t0 = time.time()
    st = np.array([0, 2], dtype=np.int64)
    ln = np.zeros(2)
    l1 = np.zeros(2)
    ci = np.zeros(2, dtype=np.int32)
    w = np.ones(2)
    om = np.zeros(1)
    ow = np.zeros(1)
    abilityKernel(st, ln, l1, ci, w, True, 1, om, ow)
    difficultyKernel(ci, ci, ln, np.zeros(2), np.zeros(2), w,
                     np.zeros(2), np.zeros(2))
    print(f"[kernels] numba compiled in {time.time() - t0:.1f}s")