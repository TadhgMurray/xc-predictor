# Project: xc-predictor
# Subset:  Distance correction, PER POOL
#
# WHY PER POOL
# ------------
# A single distance curve explained only 47.3% of the within-venue gaps, and the
# residuals said why: the highest-weight pairs were systematically underfit
# (2400-vs-3000 residual +0.0181 on weight 36,775; 3000-vs-3200 +0.0142 on
# 93,201). One curve was being asked to satisfy two different things at once,
# because a 2400m race at a venue is MIDDLE SCHOOL and the 5000m at the same
# venue is HIGH SCHOOL. Within-venue cancels terrain. It does not cancel pool.
#
# ★ THE FIX IS TO HOLD POOL FIXED, which means solving delta separately for each
#   pool. Then a within-venue distance gap is measured on ONE population, and
#   the only thing left in it is that pool's distance normalization error.
#
#   This is not a new method -- it is the same estimator run on six row subsets.
#   normalize_distance already keys the potential "pool|SPORT" and falls back to
#   a per-sport global, so per-pool curves are the shape the artifact wants.
#
# ⚠ THE ASSUMPTION THIS RESTS ON. Reading a distance effect out of within-venue
#   variation requires that a venue's TERRAIN be the same at both distances. It
#   often is not exactly -- a 3k loop and a 5k loop at one park are different
#   ground. That deviation is noise here, not bias, provided it is not
#   systematically tied to distance across venues. It is the price of making the
#   correction identifiable at all: with a free difficulty per (venue, distance)
#   AND a free distance term, the two are collinear and neither is identified.
#
# Reads the packed cache. Writes distance_fix_by_pool.npz. Changes nothing else.

import os
import sys
from collections import defaultdict

import numpy as np

import pair_engine as pe
from distance_fix import (splitKey, extractEdges, aggregateEdges,
                          solvePotential, residualCheck, _COMMON)

_MIN_ROWS = 200_000        # a pool below this cannot support 45 distance nodes


# ------------------------------------------------------------------ #
# CHUNK 1 -- POOL PER ROW
# ------------------------------------------------------------------ #

# poolCodes
# Purpose:   bare pool name per row, and the list of pools present.
# Arguments: cols -- packed dict; keep -- the usable-row mask.
# Output:    (codes ndarray[int32] per row, pools list[str])
#
# athlete_keys is indexed by athlete CODE and holds (person_id, pool), so the
# pool of a row is a double lookup: row -> athlete code -> key -> pool. Merged
# runs carry bare pools ("hs_m"); per-sport runs append a sport ("hs_m|XC"), so
# split on "|" exactly as rust_fitness does.
def poolCodes(cols, keep):
    keys = cols["athlete_keys"]
    per_athlete = np.empty(len(keys), dtype=np.int32)
    pools, index = [], {}
    for code, key in enumerate(keys):
        raw = key[1] if (key is not None and len(key) > 1) else ""
        pool = str(raw).split("|", 1)[0] if raw else "unknown"
        if pool not in index:
            index[pool] = len(pools)
            pools.append(pool)
        per_athlete[code] = index[pool]
    return per_athlete[cols["athlete"][keep]], pools


# ------------------------------------------------------------------ #
# CHUNK 2 -- ONE POOL
# ------------------------------------------------------------------ #

# solvePoolDifficulty
# Purpose:   delta per (venue, distance) using ONLY this pool's rows.
# Arguments: course, group, y -- restricted to the pool; n_cells, n_groups.
# Output:    (difficulty, solved, degree) or None if too thin.
#
# Re-runs informativeMask on the subset: a cell identified on the full corpus may
# not be identified on one pool's slice, and using the full-corpus degree here
# would silently include cells this pool barely raced.
def solvePoolDifficulty(course, group, y, n_cells, n_groups, min_degree=2):
    if y.size < _MIN_ROWS:
        return None
    # ⚠ KEYWORDS, NOT POSITIONAL. informativeMask's signature is
    #   (group, course, ...) -- passing (course, group) silently computes
    #   "distinct cells per athlete-season" instead of degree, returns an array
    #   of the wrong length, and every downstream gate then passes or fails at
    #   random. Cost an hour the first time.
    mask, degree = pe.informativeMask(group=group, course=course,
                                      n_groups=n_groups, n_cells=n_cells,
                                      min_degree=min_degree)
    if mask.sum() < _MIN_ROWS:
        return None
    c, g, yy = course[mask], group[mask], y[mask]
    delta, iters, rel = pe.solveDelta(yy, c, g, n_cells, n_groups, max_iter=600)
    print(f"    solved on {yy.size:,} rows, {iters} CG iterations "
          f"(rel {rel:.1e})")
    return np.expm1(delta), degree >= min_degree, degree


# poolCurve
# Purpose:   this pool's distance correction, and how well one curve fits it.
# Arguments: difficulty, solved, degree, keys; min_degree, min_venues.
# Output:    (phi dict, explained fraction, n_edges) or None.
#
# Distances are restricted to _COMMON. The full-distance run showed observed
# gaps of +0.098 between 1600 and 1800 at the same venue, which is physically
# impossible and marks those cells as one race recorded two ways rather than two
# races.
def poolCurve(difficulty, solved, degree, keys, min_degree=25, min_venues=10):
    allow = set(_COMMON)
    gated = solved.copy()
    for i, k in enumerate(keys):
        _v, dm = splitKey(k)
        if dm not in allow:
            gated[i] = False

    edges = extractEdges(difficulty, gated, degree, keys, min_degree)
    agg = aggregateEdges(edges, min_venues)
    if not agg or _COMMON[0] is None:
        return None
    try:
        phi, _dists = solvePotential(agg)
    except (ValueError, np.linalg.LinAlgError):
        return None

    before, after = residualCheck(agg, phi)
    explained = 1.0 - after / before if before > 0 else 0.0
    return phi, explained, len(agg)


# ------------------------------------------------------------------ #
# CHUNK 3 -- REPORT
# ------------------------------------------------------------------ #

# reportPoolCurves
# Purpose:   ★ THE COMPARISON. One column per pool, so a pool whose potential is
#            badly fitted stands out against the others at the same distance.
# Arguments: curves -- {pool: (phi, explained, n_edges)}.
def reportPoolCurves(curves):
    if not curves:
        print("[dpool] no pool had enough data")
        return

    pools = sorted(curves)
    dists = sorted({d for p in pools for d in curves[p][0]})

    print("\n[dpool] ---- phi by pool and distance ----")
    header = "    dist " + "".join(f"{p:>12}" for p in pools)
    print(header)
    for d in dists:
        row = f"    {d:>5}"
        for p in pools:
            phi = curves[p][0].get(d)
            row += "".join(f"{phi:>+12.4f}" if phi is not None else f"{'-':>12}")
        print(row)

    print("\n    explained by one curve, per pool:")
    for p in pools:
        _phi, explained, n_edges = curves[p]
        flag = "" if explained >= 0.75 else "   <-- still unexplained"
        print(f"      {p:>12}: {100.0 * explained:5.1f}%  "
              f"({n_edges} distance pairs){flag}")
    print("    factor to apply = exp(-phi)")
    print("[dpool] ------------------------------------")


# ------------------------------------------------------------------ #
# CHUNK 4 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(path):
    cols = pe.loadPack(path)
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    y_all, _form = pe.buildResponse(cols)

    course = cols["course"][keep].astype(np.int64)
    y = y_all[keep]
    group, n_groups = pe.athleteSeasonCodes(cols["athlete"][keep],
                                            cols["year"][keep])
    keys = [str(k) for k in cols["course_keys"]]
    n_cells = len(keys)

    pool_of_row, pools = poolCodes(cols, keep)
    sizes = np.bincount(pool_of_row, minlength=len(pools))
    order = np.argsort(-sizes)

    print(f"[dpool] {y.size:,} rows across {len(pools)} pools")
    for i in order:
        print(f"    {pools[i]:>14}: {int(sizes[i]):>12,} rows"
              f"{'' if sizes[i] >= _MIN_ROWS else '   (too thin, skipped)'}")

    curves = {}
    for i in order:
        if sizes[i] < _MIN_ROWS:
            continue
        pool = pools[i]
        print(f"\n[dpool] === {pool} ===")
        sel = pool_of_row == i
        got = solvePoolDifficulty(course[sel], group[sel], y[sel],
                                  n_cells, n_groups)
        if got is None:
            print("    too thin after gating; skipped")
            continue
        difficulty, solved, degree = got
        curve = poolCurve(difficulty, solved, degree, keys)
        if curve is None:
            print("    no identifiable distance pairs; skipped")
            continue
        curves[pool] = curve
        print(f"    one-curve fit explains {100.0 * curve[1]:.1f}% "
              f"of {curve[2]} within-venue gaps")

    reportPoolCurves(curves)

    if curves:
        out = os.path.join(os.path.dirname(os.path.abspath(path)),
                           "distance_fix_by_pool.npz")
        payload = {}
        for pool, (phi, explained, n_edges) in curves.items():
            ds = np.array(sorted(phi), dtype=np.int64)
            payload[f"{pool}__distances"] = ds
            payload[f"{pool}__phi"] = np.array([phi[d] for d in ds])
            payload[f"{pool}__explained"] = np.array([explained])
        np.savez(out, **payload)
        print(f"\n[dpool] wrote {out}")


if __name__ == "__main__":
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "packed_XC_TF.npz")
    main(sys.argv[1] if len(sys.argv) > 1 else default)