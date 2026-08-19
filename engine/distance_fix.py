# Project: xc-predictor
# Subset:  Distance correction, solved from within-venue gaps
#
# WHAT THIS MEASURES
# ------------------
# The pair engine reported, for venues hosting two distances (same ground, same
# runners, so terrain cancels EXACTLY):
#
#     2400 vs 5000   +0.0315      5000 vs 8000   -0.0001
#     1600 vs 5000   +0.0254      6000 vs 8000   -0.0024
#     4800 vs 5000   +0.0117      3200 vs 5000   +0.0106
#
# Short races read systematically HARDER at the same venue. Above 5000m the gaps
# vanish. That is a normalization error, not terrain: the distance potential's
# slope is too steep below 5000m, so a short race is over-scaled, its normalized
# time comes out too slow, and the excess lands in the venue's difficulty.
#
# ★ WHY A GRAPH SOLVE AND NOT THE TABLE. Those pairwise numbers are mutually
#   INCONSISTENT. 3000-vs-5000 = +0.0055 and 3200-vs-5000 = +0.0106 imply 3000
#   sits 0.005 BELOW 3200, while the direct 3000-vs-3200 = +0.0142 says it sits
#   above. Each pair rests on a different set of venues, so they are not
#   comparable. Treating every gap as one noisy observation of phi(d1) - phi(d2)
#   and solving all of them at once gives ONE curve that cannot contradict
#   itself. Same reasoning as the engine: differences are identified, levels are
#   not, so anchor one and solve the rest.
#
# THE MODEL
#     delta_observed(venue, d)  =  delta_true(venue)  +  phi(d)
#
# so the correction to apply to normalized_time at distance d is exp(-phi(d)).
# phi(5000) = 0 by choice of anchor: 5000m is the reference the potential
# already targets, and it has by far the most cells.
#
# Reads pair_validated.npz only. Changes nothing.

import os
import sys
from collections import defaultdict

import numpy as np

_REFERENCE = 5000          # phi(5000) := 0

# ★ GENUINE RACE CONVENTIONS. Everything else in the corpus is dominated by
#   sloppy distance recording: a venue showing both a 2000 and a 2100 cell is
#   almost never hosting two races, it is ONE race written down differently
#   across years, split into two cells whose gap is pure noise. The evidence is
#   physical impossibility -- the full run produced observed gaps of +0.0981
#   between 1600 and 1800, and +0.0923 between 2300 and 2500. A 5% change in
#   distance cannot move difficulty 9%.
#
#   These are the mile / 1.5mi / 2mi / 3000 / 4000 / 3mi / 5k / 6k / 8k / 10k
#   conventions, plus 4700 (Mt. SAC's distance) because it carries 417 cells.
_COMMON = (1600, 2400, 3000, 3200, 4000, 4700, 4800, 5000, 6000, 8000, 10000)


# ------------------------------------------------------------------ #
# CHUNK 1 -- PARSING
# ------------------------------------------------------------------ #

# splitKey
# Purpose:   (canonical_id, distance) from an engine XC course key.
# Arguments: key -- 'XC:<id>:d<dist>' or 'XC:name:<name>:d<dist>'.
# Output:    (id_or_name, distance) or (None, None).
#
# rpartition(":d") scans from the RIGHT, so a course name containing a colon
# cannot break the distance parse. Both key shapes are accepted -- the name form
# still identifies a venue, which is all this needs.
def splitKey(key):
    if not key or not key.startswith("XC:"):
        return (None, None)
    venue, tag, dist = key[3:].rpartition(":d")
    if not tag or not dist.isdigit():
        return (None, None)
    return (venue, int(dist))


# ------------------------------------------------------------------ #
# CHUNK 2 -- EDGES
# ------------------------------------------------------------------ #

# extractEdges
# Purpose:   one observation of phi(d1) - phi(d2) per venue hosting both.
# Arguments: difficulty, solved, degree, keys; min_degree.
# Output:    dict (d1, d2) -> list of (gap, weight), d1 < d2.
#
# ★ WEIGHT IS THE PRECISION OF A DIFFERENCE, not of a cell. A gap is only as
#   trustworthy as its weaker side, so the weight is the harmonic combination
#   deg1*deg2/(deg1+deg2) -- which is what the variance of a difference of two
#   independent means gives. Using deg1+deg2 would let one huge cell rescue a
#   3-athlete partner.
def extractEdges(difficulty, solved, degree, keys, min_degree=25):
    by_venue = defaultdict(dict)
    for i, key in enumerate(keys):
        venue, dist = splitKey(key)
        if venue and dist and solved[i] and degree[i] >= min_degree:
            by_venue[venue][dist] = (float(difficulty[i]), float(degree[i]))

    edges = defaultdict(list)
    for per_dist in by_venue.values():
        if len(per_dist) < 2:
            continue
        items = sorted(per_dist.items())
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                d1, (v1, n1) = items[a]
                d2, (v2, n2) = items[b]
                edges[(d1, d2)].append((v1 - v2, n1 * n2 / (n1 + n2)))
    return edges


# aggregateEdges
# Purpose:   collapse each distance pair to ONE robust gap and total weight.
# Arguments: edges -- from extractEdges; min_venues.
# Output:    list of (d1, d2, gap, weight).
#
# ★ WEIGHTED MEDIAN, NOT MEAN. Merged canonical ids exist -- one id in this
#   corpus carries cells at 1300 through 8000 plus a dNA, so it is a catch-all
#   rather than a venue -- and its gaps are meaningless. A mean would let those
#   move the curve; a median cannot.
def aggregateEdges(edges, min_venues=15):
    out = []
    for (d1, d2), obs in edges.items():
        if len(obs) < min_venues:
            continue
        gaps = np.array([g for g, _w in obs], dtype=np.float64)
        wts = np.array([w for _g, w in obs], dtype=np.float64)
        order = np.argsort(gaps)
        gaps, wts = gaps[order], wts[order]
        cum = np.cumsum(wts) / wts.sum()
        median = float(gaps[int(np.searchsorted(cum, 0.5))])
        out.append((d1, d2, median, float(wts.sum())))
    return out


# ------------------------------------------------------------------ #
# CHUNK 3 -- THE SOLVE
# ------------------------------------------------------------------ #

# _buildLaplacian
# Purpose:   the weighted least-squares normal equations for
#            min sum_e w_e (phi_i - phi_j - g_e)^2.
# Arguments: agg -- aggregated edges; index -- distance -> node number; n.
# Output:    (L, b) with L the weighted Laplacian and b the gap moments.
#
# Syntax: each edge adds w to L[i,i] and L[j,j] and -w to both off-diagonals;
# b gets +w*g at i and -w*g at j. Standard, and identical in form to the
# operator the cell solve uses -- one connected component, one free constant.
def _buildLaplacian(agg, index, n):
    L = np.zeros((n, n), dtype=np.float64)
    b = np.zeros(n, dtype=np.float64)
    for d1, d2, gap, w in agg:
        i, j = index[d1], index[d2]
        L[i, i] += w
        L[j, j] += w
        L[i, j] -= w
        L[j, i] -= w
        b[i] += w * gap
        b[j] -= w * gap
    return L, b


# solvePotential
# Purpose:   phi per distance, anchored at phi(reference) = 0.
# Arguments: agg; reference distance.
# Output:    (dict distance -> phi, list of distances in solve order)
#
# The anchor is imposed by DELETING the reference row and column rather than by
# adding a penalty: deletion is exact and leaves the remaining system positive
# definite, so np.linalg.solve is enough and no regulariser can bias the curve.
def solvePotential(agg, reference=_REFERENCE):
    dists = sorted({d for d1, d2, _g, _w in agg for d in (d1, d2)})
    if reference not in dists:
        raise ValueError(f"reference distance {reference} has no edges")

    index = {d: i for i, d in enumerate(dists)}
    L, b = _buildLaplacian(agg, index, len(dists))

    keep = [i for d, i in index.items() if d != reference]
    phi_reduced = np.linalg.solve(L[np.ix_(keep, keep)], b[keep])

    phi = {reference: 0.0}
    for pos, i in enumerate(keep):
        phi[dists[i]] = float(phi_reduced[pos])
    return phi, dists


# residualCheck
# Purpose:   ★ THE VALIDATION. Does one curve reproduce every observed gap?
# Arguments: agg; phi.
# Output:    (weighted rms before, weighted rms after)
#
# "Before" treats phi as zero, i.e. the current normalization. If "after" is not
# much smaller, a single per-distance curve does not explain the gaps and
# something distance-dependent is being missed.
def residualCheck(agg, phi):
    num_b = num_a = den = 0.0
    for d1, d2, gap, w in agg:
        pred = phi[d1] - phi[d2]
        num_b += w * gap ** 2
        num_a += w * (gap - pred) ** 2
        den += w
    return np.sqrt(num_b / den), np.sqrt(num_a / den)


# ------------------------------------------------------------------ #
# CHUNK 4 -- REPORT
# ------------------------------------------------------------------ #

# reportCurve
# Purpose:   the correction, in the form it will be applied.
# Arguments: phi; agg; counts -- cells per distance.
#
# `factor` is what normalized_time should be MULTIPLIED by:
# delta_observed = delta_true + phi, so removing phi means exp(-phi).
def reportCurve(phi, agg, counts):
    weight_by_dist = defaultdict(float)
    for d1, d2, _g, w in agg:
        weight_by_dist[d1] += w
        weight_by_dist[d2] += w

    print("\n[dfix] ---- solved distance correction ----")
    print("    dist      phi     factor    cells    edge_wt")
    for d in sorted(phi):
        print(f"    {d:>5} {phi[d]:>+8.4f} {np.exp(-phi[d]):>9.4f} "
              f"{counts.get(d, 0):>8,} {weight_by_dist[d]:>10,.0f}")
    print("    factor = multiply normalized_time by this")
    print("[dfix] ---------------------------------------")


# reportEdges
# Purpose:   observed vs predicted gap per distance pair, worst first.
def reportEdges(agg, phi, n=14):
    rows = [(d1, d2, gap, w, phi[d1] - phi[d2]) for d1, d2, gap, w in agg]
    rows.sort(key=lambda r: -abs(r[2] - r[4]))
    print("\n[dfix] ---- gaps: observed vs one-curve fit ----")
    print("      pair          weight    observed   predicted   resid")
    for d1, d2, gap, w, pred in rows[:n]:
        print(f"    {d1:>5} vs {d2:<5} {w:>10,.0f}  {gap:>+10.4f} "
              f"{pred:>+11.4f} {gap - pred:>+8.4f}")
    print("[dfix] -------------------------------------------")


# ------------------------------------------------------------------ #
# CHUNK 5 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(path, min_degree=25, min_venues=15, common_only=True):
    """common_only: restrict to _COMMON. Pass --all to include every distance
    and reproduce the contaminated run."""
    with np.load(path, allow_pickle=False) as f:
        difficulty = f["difficulty"]
        solved = f["solved"].astype(bool)
        degree = f["degree"]
        keys = [str(k) for k in f["course_keys"]]

    if common_only:
        # Drop cells at non-convention distances entirely, rather than solving
        # for them and letting their noise leak into the shared nodes.
        allow = set(_COMMON)
        for i, k in enumerate(keys):
            _v, d = splitKey(k)
            if d not in allow:
                solved[i] = False
        print(f"[dfix] restricted to {len(_COMMON)} convention distances "
              f"(pass --all to include every distance)")

    counts = defaultdict(int)
    for i, k in enumerate(keys):
        _v, d = splitKey(k)
        if d and solved[i] and degree[i] >= min_degree:
            counts[d] += 1

    edges = extractEdges(difficulty, solved, degree, keys, min_degree)
    agg = aggregateEdges(edges, min_venues)
    print(f"[dfix] {len(edges):,} distance pairs seen, {len(agg):,} with "
          f">= {min_venues} venues")

    phi, dists = solvePotential(agg)
    before, after = residualCheck(agg, phi)

    reportCurve(phi, agg, counts)
    reportEdges(agg, phi)

    print(f"\n[dfix] weighted rms gap: {before:.5f} -> {after:.5f} "
          f"({100.0 * (1 - after / before):.1f}% explained by one curve)")
    if after > 0.6 * before:
        print("    ⚠ one per-distance curve does NOT explain the gaps --")
        print("      something distance-dependent is being missed")

    out = os.path.join(os.path.dirname(os.path.abspath(path)),
                       "distance_fix.npz")
    np.savez(out,
             distances=np.array(sorted(phi), dtype=np.int64),
             phi=np.array([phi[d] for d in sorted(phi)], dtype=np.float64),
             factor=np.array([np.exp(-phi[d]) for d in sorted(phi)],
                             dtype=np.float64))
    print(f"[dfix] wrote {out}")


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else os.path.join(here, "pair_validated.npz"),
         common_only="--all" not in sys.argv)