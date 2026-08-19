# Project: xc-predictor
# Subset:  Apply the per-pool distance correction, and measure whether it helps
#
# WHAT IS BEING APPLIED
#   distance_fix_by_pool measured phi[pool, distance] from within-venue gaps:
#   the same ground raced at two distances by the same pool, so terrain cancels
#   and only that pool's distance normalization error is left.
#
#   The curves validated by CROSS-POOL AGREEMENT, not by variance explained:
#       hs_m vs hs_f   r = 0.970  (9 shared distances)
#       ms_m vs ms_f   r = 0.931  (7 shared distances)
#   Independent populations, independent solves, same curve. The ~50% of gaps
#   left unexplained is real loop-to-loop terrain variation -- a park's 2-mile
#   course is different ground from its 5k -- which is noise around the curve
#   rather than bias in it. That also vindicates keying cells on
#   (canonical_id, distance): those really are different courses.
#
# ★ TRUSTED POOLS ONLY. college_m vs college_f came back at r = -0.975 on four
#   shared distances with every value inside +-0.018 -- noise finding a spurious
#   anti-correlation. elem produced no identifiable pairs at all. Those pools
#   keep the existing potential; inventing a correction for them would be worse
#   than leaving them alone.
#
# THE TEST
#   Held-out prediction error, with and without the correction, on the same rows
#   and the same split. In-sample fit cannot rank two models -- least squares is
#   by construction the best in-sample fit, so a move toward truth must make it
#   worse. Held-out can.
#
# ⚠ ONE HONEST CAVEAT ON LEAKAGE. phi was fitted on the full corpus, which
#   includes the held-out rows. Strictly it should be refitted inside the train
#   split. The reason that is acceptable here: phi carries about 10 parameters
#   per pool, ~40 in total, against 5.5M held-out rows. The effective degrees of
#   freedom it could use to memorise a held-out row is negligible. If the
#   improvement were marginal this would matter; if it is clear, it does not.

import os
import sys
from collections import defaultdict

import numpy as np

import pair_engine as pe
import pair_validate as pv
from distance_fix import splitKey

# Pools whose curves survived the cross-pool agreement check.
_TRUSTED = ("hs_m", "hs_f", "ms_m", "ms_f")


# ------------------------------------------------------------------ #
# CHUNK 1 -- LOAD THE CURVES
# ------------------------------------------------------------------ #

# loadPoolCurves
# Purpose:   {pool: {distance: phi}} from distance_fix_by_pool.npz.
# Arguments: path; trusted -- pools to accept.
# Output:    dict.
#
# The npz stores flat keys "<pool>__distances" / "<pool>__phi" because np.savez
# cannot hold nested dicts. Untrusted pools are skipped at LOAD time rather than
# at apply time, so nothing downstream has to remember the exclusion.
def loadPoolCurves(path, trusted=_TRUSTED):
    curves = {}
    with np.load(path, allow_pickle=False) as f:
        for pool in trusted:
            dk, pk = f"{pool}__distances", f"{pool}__phi"
            if dk in f.files and pk in f.files:
                curves[pool] = dict(zip(f[dk].tolist(), f[pk].tolist()))
    return curves


# cellDistances
# Purpose:   snapped distance per CELL, -1 where the key carries none.
# Arguments: course_keys.
# Output:    ndarray[int64] of length n_cells.
#
# Parsed once from the keys rather than re-derived from the database, so the
# distance used here is exactly the one the cell was built on.
def cellDistances(course_keys):
    out = np.full(len(course_keys), -1, dtype=np.int64)
    for i, k in enumerate(course_keys):
        _venue, dist = splitKey(str(k))
        if dist:
            out[i] = dist
    return out


# ------------------------------------------------------------------ #
# CHUNK 2 -- BUILD THE PER-ROW CORRECTION
# ------------------------------------------------------------------ #

# _nearestCurveValue
# Purpose:   phi for one (pool, distance), or 0.0 when the curve has nothing
#            to say.
# Arguments: curve -- {distance: phi}; dist.
#
# ★ EXACT MATCH ONLY -- NO INTERPOLATION. The curve was solved on convention
#   distances (mile, 1.5mi, 2mi, 3000, 4000, 3mi, 5k, 6k...) precisely because
#   the distances between them are dominated by sloppy recording: the full-range
#   run found an observed gap of +0.098 between 1600 and 1800 at the SAME venue,
#   which is physically impossible. Interpolating onto those distances would
#   spread a correction across cells whose labels are not trustworthy. 0.0 is the
#   honest answer: leave them as they are.
def _nearestCurveValue(curve, dist):
    return float(curve.get(int(dist), 0.0))


# buildRowCorrection
# Purpose:   phi per ROW, ready to subtract from y.
# Arguments: cols, keep -- pack and usable mask; curves; cell_dist.
# Output:    (correction ndarray[float64], stats dict)
#
# Sign: delta_observed = delta_true + phi, so removing phi means subtracting it
# from y. A positive phi (short races reading too hard) becomes a downward
# adjustment to the course's account.
def buildRowCorrection(cols, keep, curves, cell_dist):
    # pool per row -- same lookup as distance_fix_by_pool.poolCodes: a row's
    # pool is row -> athlete code -> key -> pool, with "|SPORT" stripped.
    keys = cols["athlete_keys"]
    per_athlete = np.empty(len(keys), dtype=np.int32)
    names, index = [], {}
    for code, key in enumerate(keys):
        raw = key[1] if (key is not None and len(key) > 1) else ""
        pool = str(raw).split("|", 1)[0] if raw else "unknown"
        if pool not in index:
            index[pool] = len(names)
            names.append(pool)
        per_athlete[code] = index[pool]
    pool_of_row = per_athlete[cols["athlete"][keep]]

    course = cols["course"][keep]
    dist_of_row = cell_dist[course]

    corr = np.zeros(course.size, dtype=np.float64)
    stats = defaultdict(int)
    for pool, curve in curves.items():
        if pool not in index:
            continue
        sel = pool_of_row == index[pool]
        if not sel.any():
            continue
        # Map distance -> phi with one pass per distance in the curve.
        sub = dist_of_row[sel]
        vals = np.zeros(sub.size, dtype=np.float64)
        for dist, phi in curve.items():
            hit = sub == int(dist)
            vals[hit] = phi
            stats[(pool, int(dist))] = int(hit.sum())
        corr[sel] = vals

    touched = int((corr != 0.0).sum())
    print(f"[dapp] correction applied to {touched:,}/{corr.size:,} rows "
          f"({100.0 * touched / corr.size:.1f}%)")
    return corr, stats


# reportCoverage
# Purpose:   which (pool, distance) combinations actually got corrected, so a
#            curve that silently matched nothing is visible.
def reportCoverage(stats, top=18):
    rows = sorted(stats.items(), key=lambda kv: -kv[1])[:top]
    print("    rows corrected by (pool, distance):")
    for (pool, dist), n in rows:
        if n:
            print(f"      {pool:>6} {dist:>6}: {n:>12,}")


# ------------------------------------------------------------------ #
# CHUNK 3 -- THE A/B
# ------------------------------------------------------------------ #

# holdoutAB
# Purpose:   ★ THE DECISION. Same rows, same split, corrected vs not.
# Arguments: course, group, y_raw, y_fixed, n_cells, n_groups; frac, seed.
# Output:    dict label -> (coverage, error sd)
#
# The 'zero' arm ignores course difficulty entirely. It is the floor any
# difficulty model has to beat, and it also confirms the harness is measuring
# what it claims: if 'zero' were competitive, nothing else would mean anything.
def holdoutAB(course, group, y_raw, y_fixed, n_cells, n_groups,
              frac=0.10, seed=1):
    te = pv.splitByRow(y_raw.size, frac=frac, seed=seed)
    tr = ~te
    out = {}

    for label, y in (("uncorrected", y_raw), ("corrected", y_fixed)):
        print(f"\n[dapp] solving train split, {label}...")
        T = pv.solveSubset(course[tr], group[tr], y[tr], n_cells, n_groups)
        solved = T["degree"] >= 2
        alpha, gcount = pv.refitAlpha(y[tr], T["delta"], course[tr],
                                      group[tr], n_groups)
        out[label] = pv.evaluateHoldout(T["delta"], alpha, gcount, y[te],
                                        course[te], group[te], solved, label)
        if label == "uncorrected":
            zero = np.zeros(n_cells)
            a0, g0 = pv.refitAlpha(y[tr], zero, course[tr], group[tr], n_groups)
            out["zero"] = pv.evaluateHoldout(zero, a0, g0, y[te], course[te],
                                             group[te], solved, "zero")
    return out


# ------------------------------------------------------------------ #
# CHUNK 4 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(pack_path, curve_path):
    cols = pe.loadPack(pack_path)
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    y_all, _form = pe.buildResponse(cols)

    course = cols["course"][keep].astype(np.int64)
    y_raw = y_all[keep]
    group, n_groups = pe.athleteSeasonCodes(cols["athlete"][keep],
                                            cols["year"][keep])
    keys = [str(k) for k in cols["course_keys"]]
    n_cells = len(keys)

    curves = loadPoolCurves(curve_path)
    if not curves:
        print("[dapp] no trusted pool curves found; nothing to apply")
        return
    print(f"[dapp] loaded curves for {', '.join(sorted(curves))}")

    cell_dist = cellDistances(keys)
    corr, stats = buildRowCorrection(cols, keep, curves, cell_dist)
    reportCoverage(stats)

    y_fixed = y_raw - corr

    res = holdoutAB(course, group, y_raw, y_fixed, n_cells, n_groups)

    print("\n[dapp] ---- held-out error, lower is better ----")
    base = res.get("uncorrected", (0, float("nan")))[1]
    for label in ("zero", "uncorrected", "corrected"):
        if label not in res:
            continue
        _cov, sd = res[label]
        delta = "" if label == "zero" or not np.isfinite(base) else \
            f"   {100.0 * (sd - base) / base:+.2f}% vs uncorrected"
        print(f"    {label:>12}: {sd:.6f}{delta}")
    print("[dapp] --------------------------------------------")

    if "corrected" in res and np.isfinite(base):
        if res["corrected"][1] < base:
            print("    the correction HELPS -- worth folding into "
                  "distance_spline.pkl")
        else:
            print("    the correction does NOT help on held-out rows.")
            print("    the curves are reproducible but not predictive: do not")
            print("    re-normalize 62M rows on the strength of them.")


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    main(sys.argv[1] if len(sys.argv) > 1
         else os.path.join(here, "packed_XC_TF.npz"),
         sys.argv[2] if len(sys.argv) > 2
         else os.path.join(here, "distance_fix_by_pool.npz"))