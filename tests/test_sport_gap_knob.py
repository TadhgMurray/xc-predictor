"""The two claims that turned out to rest on broken diagnostics.

1. diagnose_rating Q8/Q9 called an anchor mismatch "THE CONFIRMED BUG" while
   selecting rows below the pool's OWN 0.1st percentile -- 0.1% of any pool,
   healthy or not. Q7 recomputed a CAREER anchor and compared it to
   rating_seasonal, so it reported "stale" on every athlete.
2. recenter() takes a scalar. Per-pool bbar cannot work: cells are keyed
   XC:<venue>:d<dist>, so pools share delta and the recentring identity stops
   cancelling. An array would broadcast instead of failing.

    python tests/test_sport_gap_knob.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

failed = []


def ok(cond, msg):
    if not cond:
        failed.append(msg)
    return cond


def read(*p):
    with io.open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- #
# 1. the self-confirming threshold is gone
# ---------------------------------------------------------------- #
diag = read("engine", "diagnose_rating.py")

ok("percentile_cont(0.001)" not in diag,
   "Q8/Q9 still select below the pool's own 0.1st percentile, which is 0.1% "
   "of the pool by definition and confirms itself on any data")

q8 = diag[diag.index('dict(n=8,'):diag.index('dict(n=9,')]
ok("0.62" in q8 and "under_62" in q8,
   "Q8 should bucket ability / pool median, with 0.62 (= 1/1.64, the "
   "3200-over-5000 seam) as the column that matters")
ok("under_70" in q8 and "under_80" in q8,
   "Q8 needs neighbouring buckets -- the signal is the tail REFUSING to thin, "
   "which a single count cannot show")

q9 = diag[diag.index('dict(n=9,'):]
ok("0.62 * b.median" in q9,
   "Q9 must name the same athlete-seasons Q8 counts, or the two disagree")

# ---------------------------------------------------------------- #
# 2. Q7 compares each stored rating against its OWN anchor
# ---------------------------------------------------------------- #
q7 = diag[diag.index('dict(n=7,'):diag.index('dict(n=8,')]
ok("GROUP BY pool, season" in q7,
   "rating_seasonal is anchored within (pool, season) -- pair_ratings "
   "buildRatings -- so a career recompute is a different quantity")
ok("races >= 3" in q7,
   "only athlete-seasons with races >= 3 enter the anchor "
   "(min_races_anchor), so the recompute must filter the same way")
ok("recomp_seasonal" in q7 and "recomp_career" in q7,
   "Q7 should recompute both anchors and sit each beside its own stored "
   "column")

# ---------------------------------------------------------------- #
# 3. recenter() refuses a non-scalar bbar
# ---------------------------------------------------------------- #
import numpy as np                                          # noqa: E402

import pair_recenter as prc                                 # noqa: E402

n_groups, n_cells, n_rows = 4, 3, 12
rng = np.random.default_rng(0)
group = np.arange(n_rows) % n_groups
course = np.arange(n_rows) % n_cells
sport = (np.arange(n_rows) % 2).astype(np.int64)
sc = sport.astype(np.float64) - 0.5
delta = rng.normal(size=n_cells)
alpha = rng.normal(size=n_groups)
beta = rng.normal(size=n_groups)

args = (delta, alpha, beta, sc, group, sport, course, n_cells, n_groups)

d2, a2, b2, bbar, n_ident = prc.recenter(*args, bbar=-0.039)
ok(isinstance(bbar, float) and abs(bbar + 0.039) < 1e-12,
   "a scalar bbar must still be applied verbatim")

try:
    prc.recenter(*args, bbar=np.full(n_groups, -0.039))
except ValueError as e:
    ok("scalar" in str(e).lower(),
       "the refusal should say scalar-only, not just fail")
    ok("delta" in str(e).lower() or "cell" in str(e).lower(),
       "the refusal should say WHY -- pools share delta -- so the next "
       "person does not try again")
else:
    failed.append("recenter() accepted a per-pool bbar array; it broadcasts "
                  "against s_cell instead of failing and silently produces "
                  "difficulties that reparameterise nothing")

# ---------------------------------------------------------------- #
# 4. the reason is written down where someone will look for it
# ---------------------------------------------------------------- #
rec = read("engine", "pair_recenter.py")
ok("PER-POOL" in rec.upper() and "distance_fix_by_pool" in rec,
   "pair_recenter should record why per-pool bbar is not the knob and which "
   "file the pool spread belongs in")


# ---------------------------------------------------------------- #
# 5. the anchor a caller is TOLD is the anchor the normaliser USED
# ---------------------------------------------------------------- #
#
# ★ THE INVARIANT, AND IT IS THE ONLY ONE THAT MATTERS HERE. targetFor exists
#   so a caller can reason about the SCALE of a normalized_time. That claim is
#   true exactly when a race run AT the returned distance normalises to its own
#   time -- normalizeTime is the identity at the anchor. Anything else means
#   the two stages write and read on different scales.
#
#   It was false for every TF pool: the artifact carries 'hs_m|TF' = 1600,
#   targetFor preferred the suffixed key, and _normalizeWithPotential strips
#   the suffix and reads 'hs_m' = 5000. poolBandFor then gated high-school
#   track at (192, 1152) -- a 1600m band over 5000m numbers -- so a 5:29 1600
#   was refused a place on every board and nothing at all could fail the floor.
import normalize_distance as nd                              # noqa: E402

if nd._SPLINES is None:
    print("  (no distance_spline.pkl -- anchor checks skipped)")
else:
    for pool in ("hs_m", "hs_f", "ms_m", "ms_f", "elem_m", "elem_f",
                 "college_m", "college_f"):
        for sport in ("XC", "TF"):
            a = nd.targetFor(pool, sport)
            got = nd.normalizeTime(600.0, a, pool, sport=sport)
            ok(abs(got - 600.0) < 0.5,
               f"targetFor({pool!r}, {sport!r}) = {a:.0f}m but normalizeTime "
               f"is not the identity there ({got:.1f}s for 600s) -- the band "
               f"and the stored normalized_time are on different scales")

        # and both sports must land on ONE anchor, or a merged athlete-season
        # holds an ability that is two different distances at once.
        ok(nd.targetFor(pool, "XC") == nd.targetFor(pool, "TF"),
           f"{pool}: XC anchors at {nd.targetFor(pool, 'XC'):.0f}m and TF at "
           f"{nd.targetFor(pool, 'TF'):.0f}m, but alpha is one ability per "
           f"athlete-season spanning both sports")

    # the band follows the anchor, so it must too
    for pool in ("hs_m", "hs_f"):
        lo, hi = nd.poolBandFor(pool, "TF")
        ok((lo, hi) == nd.poolBandFor(pool, "XC"),
           f"{pool}: the TF and XC sanity bands differ, so one sport's rows "
           f"are gated on the other's scale")
        # a 6:00 1600 is an ordinary high school time and belongs in the solve
        n = nd.normalizeTime(360.0, 1600, pool, sport="TF")
        ok(lo <= n <= hi,
           f"{pool}: a 6:00 1600 normalises to {n:.0f}s and falls outside the "
           f"band ({lo:.0f}, {hi:.0f}) -- dropped from the solve and every "
           f"board")

# ---------------------------------------------------------------- #
if failed:
    print("FAIL")
    for m in failed:
        print("  - " + m)
    sys.exit(1)
print("test_sport_gap_knob: ok")
