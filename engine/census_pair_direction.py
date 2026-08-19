# Project: xc-predictor
# File:    scripts/census_pair_direction.py
# Purpose: Decide what the XC distance curves' sub-1.0 exponents ARE:
#          nominal-distance truth (keep) or calendar-effort leak (cancel).
#
#          The fitter's symmetrization cancels calendar-tied confounds
#          (early-season softness, within-window fitness gains) ONLY when
#          each bin holds both time-orders in balance. This census measures
#          (1) that balance per pool, and (2) the DIRECTIONAL GAP — the
#          implied exponent from short-first pairs vs long-first pairs.
#          If calendar effects are real, short-first fits shallower; the
#          gap between the two directions IS the leak's size, in exponent
#          units, directly comparable to the XC-vs-TF 0.95-vs-1.07 question.
#
#          Reads ONLY the pair cache (engine/data/distance_pairs_cache.pkl)
#          — no database. The cache preserves time order by construction:
#          _makePair stores race 1 = the earlier race. Minutes, not hours.
#
# READ ORDER on the output:
#   1. BALANCE per pool — TF near 50/50 is the control; XC college heavily
#      short-first is the precondition for a leak (no imbalance, no leak).
#   2. DIRECTIONAL GAP table — per transition, exponent by direction.
#      Directions AGREE  -> the shallow law is real (nominal-distance);
#      short-first SHALLOWER -> calendar leak confirmed; long-first-only
#      is the less-contaminated estimate of the clean law.
#   3. DIRECTION-SPLIT REFIT — whole-curve health per direction, the same
#      _fitOnePotential the shipped artifact came from.
#
# Usage:   python scripts/census_pair_direction.py            (both sports)
#          python scripts/census_pair_direction.py --sport XC
#          python scripts/census_pair_direction.py --min-cell 100

import sys
import math
import argparse

import numpy as np

sys.path.insert(0, "scripts")   # database.py (pulled in by the fitter import)
sys.path.insert(0, "engine")    # normalize_distance.py, fit_distance_exponent.py

# The fitter is imported as a LIBRARY: we reuse its cache reader, its edge
# admission constant, and its whole fit stack, so this census diagnoses the
# exact pairs and machinery that produced the shipped artifact — a census
# with its own private mirrors would be diagnosing a different fitter.
import fit_distance_exponent as fde

# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

MIN_CELL = 200        # a direction needs this many pairs in a transition
                      # cell before its median exponent is printed; below
                      # it the cell shows "thin" instead of a noisy number
TOP_TRANSITIONS = 8   # transitions shown per pool, largest first
BUCKET_M = fde.TRANSITION_BUCKET_M   # 100m — the fitter's own bucketing

# ------------------------------------------------------------------ #
# CACHE -> DIRECTED PAIRS
# ------------------------------------------------------------------ #

# _loadDirectedPairs
# Purpose:   The cache, regrouped by (sport, pool), each pair reduced to
#            the four numbers this census needs. Direction is read off the
#            stored order: distance1 belongs to the EARLIER race.
# Arguments: none (path comes from the fitter's own CACHE_FILE constant —
#            one truth for where the cache lives).
# Output:    {("XC"|"TF", pool): [(d_lo, d_hi, y, short_first), ...]}
#            where y = log(T_at_d_hi / T_at_d_lo)  (canonical delta) and
#            short_first = True when the shorter race was run FIRST.
#            Pairs under the fitter's MIN_EDGE_SPAN_LOG are dropped here
#            too — we diagnose the pairs the fit actually admitted.
def _loadDirectedPairs():
    cached = fde._loadCache()
    if cached is None:
        print("No pair cache found — run fit_distance_exponent.py first "
              "(or point CACHE_FILE at the right box).")
        sys.exit(1)
    xc_pairs, tf_pairs = cached
    out = {}
    for sport, pairs in (("XC", xc_pairs), ("TF", tf_pairs)):
        for p in pairs:
            rec = _directedRecord(p)
            if rec is not None:
                out.setdefault((sport, p["pool"]), []).append(rec)
    return out


# _directedRecord
# Purpose:   One cached pair -> (d_lo, d_hi, canonical delta, short_first),
#            or None when degenerate or under the fitter's span floor.
#            The canonicalization mirrors _aggregatePairEdges exactly:
#            low->high distance, delta negated when the pair ran high-first
#            — EXCEPT we remember which case it was instead of folding it.
# Arguments: p — a pair dict {distance1, distance2, time1, time2, pool}
#            (index 1 = the earlier race, by _makePair's contract).
# Output:    the tuple, or None.
def _directedRecord(p):
    d1, d2, t1, t2 = p["distance1"], p["distance2"], p["time1"], p["time2"]
    if min(d1, d2, t1, t2) <= 0:                     # degenerate guard
        return None
    ld1, ld2 = math.log(d1), math.log(d2)
    if abs(ld2 - ld1) < fde.MIN_EDGE_SPAN_LOG:       # the fit never saw it
        return None
    if d1 < d2:                                      # earlier race shorter
        return (d1, d2, math.log(t2 / t1), True)
    return (d2, d1, math.log(t1 / t2), False)        # earlier race longer

# ------------------------------------------------------------------ #
# PART 1 — THE BALANCE CENSUS
# ------------------------------------------------------------------ #

# _printBalance
# Purpose:   The precondition check: per pool, how lopsided are the
#            time-orders? 50/50 -> symmetrization's cancellation holds and
#            calendar effects are already dead; 90/10 -> the pooled median
#            IS the majority direction's median and the leak passes through
#            nearly undamped.
# Arguments: recs — one pool's directed records.
# Output:    (n_short_first, n_long_first) so callers can reuse the split.
def _printBalance(recs):
    n_sf = sum(1 for r in recs if r[3])
    n_lf = len(recs) - n_sf
    share = 100.0 * n_sf / len(recs)
    print(f"    balance: {n_sf:,} short-first / {n_lf:,} long-first "
          f"({share:.1f}% short-first)")
    return n_sf, n_lf

# ------------------------------------------------------------------ #
# PART 2 — THE DIRECTIONAL GAP, PER TRANSITION
# ------------------------------------------------------------------ #

# _groupByTransition
# Purpose:   Records -> {(lo_bucket, hi_bucket): [records]}, the fitter's
#            own 100m bucketing so a "transition" here means what it meant
#            in the fit's dedup key.
# Arguments: recs — directed records.
# Output:    the grouped dict.
def _groupByTransition(recs):
    grouped = {}
    for d_lo, d_hi, y, sf in recs:
        key = (round(d_lo / BUCKET_M), round(d_hi / BUCKET_M))
        grouped.setdefault(key, []).append((d_lo, d_hi, y, sf))
    return grouped


# _directionExponent
# Purpose:   One direction's implied local exponent inside one transition
#            cell: median canonical delta over the cell's own mean log
#            distance ratio. Median (not mean) for the same reason the
#            fitter uses it — two-sided noise dies, structure survives.
# Arguments: cell_recs — records of ONE direction in ONE transition.
# Output:    (exponent, n), or (None, n) when the side is under MIN_CELL.
def _directionExponent(cell_recs, min_cell):
    n = len(cell_recs)
    if n < min_cell:
        return None, n
    ys = np.array([r[2] for r in cell_recs])
    ratios = np.array([math.log(r[1] / r[0]) for r in cell_recs])
    return float(np.median(ys)) / float(np.mean(ratios)), n


# _printGapTable
# Purpose:   The main readout: for each of the pool's biggest transitions,
#            the exponent by direction, the pooled exponent, and the gap.
#            HOW TO READ: gap ~ 0 -> no calendar leak, the law is what the
#            data says; gap > 0 (long-first steeper) -> calendar effects
#            are deflating short-first pairs, and the pooled column shows
#            how far the imbalance let them drag the fit.
# Arguments: recs — one pool's directed records; min_cell — print floor.
def _printGapTable(recs, min_cell):
    grouped = _groupByTransition(recs)
    biggest = sorted(grouped.items(), key=lambda kv: -len(kv[1]))
    print(f"    {'transition':>16} {'n(sf)':>8} {'n(lf)':>8}"
          f" {'exp sf':>8} {'exp lf':>8} {'pooled':>8} {'gap':>7}")
    for (b_lo, b_hi), cell in biggest[:TOP_TRANSITIONS]:
        sf = [r for r in cell if r[3]]
        lf = [r for r in cell if not r[3]]
        e_sf, n_sf = _directionExponent(sf, min_cell)
        e_lf, n_lf = _directionExponent(lf, min_cell)
        e_all, _ = _directionExponent(cell, min_cell)
        label = f"{b_lo * BUCKET_M}->{b_hi * BUCKET_M}m"
        print(f"    {label:>16} {n_sf:>8,} {n_lf:>8,}"
              f" {_fmt(e_sf):>8} {_fmt(e_lf):>8} {_fmt(e_all):>8}"
              f" {_fmtGap(e_sf, e_lf):>7}")


# _fmt / _fmtGap
# Purpose:   Table cells: a number, or "thin" when a side lacked support.
def _fmt(e):
    return f"{e:.3f}" if e is not None else "thin"


def _fmtGap(e_sf, e_lf):
    if e_sf is None or e_lf is None:
        return "-"
    return f"{e_lf - e_sf:+.3f}"

# ------------------------------------------------------------------ #
# PART 3 — DIRECTION-SPLIT REFIT (whole curve per direction)
# ------------------------------------------------------------------ #

# _refitOneDirection
# Purpose:   Rebuild fitter-shaped pair dicts from one direction's records
#            and run the REAL fit stack on them (_fitOnePotential ->
#            _healthNote), so the per-direction verdict is a whole-curve
#            health line, not just per-transition medians. Distances are
#            emitted low-first; the fitter re-canonicalizes internally,
#            which is a no-op on an already-canonical pair.
# Arguments: label — report tag; recs — one direction's records.
def _refitOneDirection(label, recs):
    pairs = [{"pool": "census", "distance1": d_lo, "distance2": d_hi,
              "time1": 1000.0, "time2": 1000.0 * math.exp(y)}
             for d_lo, d_hi, y, _sf in recs]
    # times reconstructed FROM the delta: the fit only ever consumes
    # log(time2/time1), so (1000, 1000*e^y) reproduces each pair's evidence
    # exactly without carrying the original absolute times around.
    fitted = fde._fitOnePotential(pairs)
    if fitted is None:
        print(f"      {label}: too few edges to fit.")
        return
    print(f"      {label}: {fitted['n_edges']:,} edges, degree "
          f"{fitted['degree']}.  {fde._healthNote(fitted)}")

# ------------------------------------------------------------------ #
# MAIN
# ------------------------------------------------------------------ #

def main():
    parser = argparse.ArgumentParser(
        description="Time-order balance + directional-gap census over the "
                    "distance pair cache")
    parser.add_argument("--sport", choices=["XC", "TF"],
                        help="restrict to one sport")
    parser.add_argument("--min-cell", type=int, default=MIN_CELL,
                        help="min pairs per direction per transition cell")
    args = parser.parse_args()

    print("=== census_pair_direction.py ===\n")
    directed = _loadDirectedPairs()

    for (sport, pool) in sorted(directed):
        if args.sport and sport != args.sport:
            continue
        recs = directed[(sport, pool)]
        print(f"\n  {pool}|{sport} — {len(recs):,} admitted pairs")
        n_sf, n_lf = _printBalance(recs)
        _printGapTable(recs, args.min_cell)
        print("    direction-split refit:")
        _refitOneDirection("short-first only", [r for r in recs if r[3]])
        _refitOneDirection("long-first  only", [r for r in recs if not r[3]])

    print("\nRead order: balance -> gap column -> split refits. "
          "TF pools are the control (expect ~50/50, gap ~0).")


if __name__ == "__main__":
    main()