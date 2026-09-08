# Project: xc-predictor
# Subset:  Pair Engine -- per-RESULT speed ratings
#
# Produces one rating per result row, the number a user actually sees on a race
# page, using the pair engine's difficulties and per-athlete-season abilities.
#
#     adjusted = normalized_time / (1 + difficulty[cell])
#     rating   = 100 * pool_mean / adjusted
#
# The formula is imported from pair_ratings.perRaceRatings rather than rewritten,
# so there is ONE definition of what a result rating is.
#
# ★ SAFE BY DEFAULT. Writes a NEW table, pair_result_rating (result_id, sport,
#   rating_career, rating_seasonal). It does NOT touch results.speed_rating.
#   That matters because saveResultSpeedRatings' own docstring says of its
#   rebuild path: "⚠ THIS REMOVES THE ONLY ROLLBACK." Pass --merge when you have
#   compared the two and want the real column overwritten.
#
# TWO ANCHORS, AND THE CHOICE IS NOT COSMETIC
#   rating_career    pool_mean over all athlete-seasons in the pool. A result's
#                    rating is then comparable across years -- which is what a
#                    race page usually wants -- and inherits whatever
#                    era_curve.pkl gets wrong.
#   rating_seasonal  pool_mean within (pool, season). Era cancels exactly,
#                    because it sits in numerator and denominator. But a 2010
#                    rating and a 2025 rating are no longer on one scale, so an
#                    all-time list built from this column is meaningless.
#   Both are written. --merge uses career, matching the existing column's
#   semantics; pass --merge-seasonal to use the other.
#
# ⚠ UNRATED ROWS ARE OMITTED, NOT ZEROED. A row with no venue, an out-of-band
#   normalized_time, or a pool with no mean gets no row here. The engine's merge
#   uses preserve_unmatched=False for the same reason: a stale value from an
#   older run is worse than a NULL. The docstring in speed_ratings_db records
#   4,216 fossils with speed_rating 7528 on a 20.6-second normalized_time.

import os
import sys

import numpy as np

import pair_engine as pe
import pair_validate as pv
import pair_ratings as pr
from pair_write import copyRows

_SPORT_NAME = {0: "XC", 1: "TF"}


# ------------------------------------------------------------------ #
# CHUNK 1 -- POOL MEANS
# ------------------------------------------------------------------ #

# poolMeanPerGroup
# Purpose:   the 100-point anchor for each athlete-season, both ways.
# Arguments: ability -- per athlete-season; attrs -- from groupAttributes;
#            valid.
# Output:    (career ndarray, seasonal ndarray), both indexed by athlete-season.
#
# ★ THE ANCHOR IS A MEAN OF ABILITIES IN SECONDS, not a mean of ratings.
#   Averaging ratings would anchor on a ratio of ratios and the 100 point would
#   drift with the shape of the distribution instead of sitting at the pool's
#   middle.
# ★ A PRO IS RATED ON THE COLLEGE SCALE (owner, 2026-09-08: "pro athletes
#   still get crazy low speed ratings. They should honestly be incorporated
#   to college speed pool mechanically but not ranked as such").
#
#   A rating is 100 * pool_mean / exp(a): 100 is the MEAN OF YOUR OWN POOL.
#   The pro pool's mean is a professional, so an elite professional reads
#   just over 100 -- Graham Blanks' 29:41 at the USATF trials came out 96.4
#   beside his college rows at 146. Nothing is broken in the solve; the two
#   numbers are simply on different scales, and only one of them is the
#   scale a reader has in their head.
#
# ! SO THE ANCHOR MOVES, NOT THE POOL. The obvious alternative -- repool
#   pros as college -- would fold professional abilities INTO the college
#   mean and shift every college rating down. This takes the college pool's
#   mean and APPLIES it to pro rows, leaving the college mean computed over
#   collegians alone. Pros keep pool 'pro_m'/'pro_f', so rankings.POOLS
#   still excludes them from every board: rated on the college scale, not
#   ranked as college, which is exactly what was asked.
_PRO_SCALE_PREFIX = "pro_"
_PRO_SCALE_ONTO = "college_"


def _proScaleMap(pool_names):
    """Index -> the pool index whose MEAN each pool should be rated against.
    Identity everywhere except pro_x -> college_x."""
    names = [str(n) for n in pool_names]
    out = np.arange(max(len(names), 1), dtype=np.int64)
    by_name = {n: i for i, n in enumerate(names)}
    for i, n in enumerate(names):
        if not n.startswith(_PRO_SCALE_PREFIX):
            continue
        onto = by_name.get(_PRO_SCALE_ONTO + n[len(_PRO_SCALE_PREFIX):])
        # ! ONLY WHEN THE COLLEGE POOL IS REALLY THERE. A corpus with pros
        #   and no collegians of that gender keeps its own anchor rather
        #   than silently rating against nothing.
        if onto is not None:
            out[i] = onto
    return out


def poolMeanPerGroup(ability, attrs, valid, anchor=None):
    # ★ THE ANCHOR SET, NOT THE RATED SET. buildRatings now rates 2-race
    #   athlete-seasons but keeps them out of pool_mean; the same distinction has
    #   to hold here or the 100 point would differ between the athlete table and
    #   the result table.
    a = valid if anchor is None else anchor
    pool = attrs["pool"].clip(0)
    n_pools = max(len(attrs["pool_names"]), 1)
    career, _ = pr._meanBy(ability, pool, n_pools, a)

    # the means stay per REAL pool (so college's is collegians only); only
    # the LOOKUP is redirected for pro rows
    scale_of = _proScaleMap(attrs["pool_names"])
    career = career[scale_of]

    combo = pool.astype(np.int64) * 10000 + attrs["season"]
    _u, code = np.unique(combo, return_inverse=True)
    seasonal, _ = pr._meanBy(ability, code, code.max() + 1, a)

    # ! THE SEASONAL HALF NEEDS THE SAME REDIRECT, and it is a lookup rather
    #   than an index remap: a pro row of season s must read the code for
    #   (college pool, s). searchsorted works because np.unique returns _u
    #   sorted. A season with no collegians has no such code, so that row
    #   keeps its own -- better a pro-anchored season than a rating against
    #   a pool that did not race.
    want = scale_of[pool].astype(np.int64) * 10000 + attrs["season"]
    moved = want != combo
    if moved.any():
        idx = np.searchsorted(_u, want)
        found = (idx < _u.size)
        idx_safe = np.clip(idx, 0, max(_u.size - 1, 0))
        found &= _u[idx_safe] == want
        code = np.where(moved & found, idx_safe, code)

    # ★ A POOL MEAN IS RETURNED FOR EVERY GROUP, rated or not. A one-race
    #   athlete-season has no usable ability -- alpha IS that row's residual, so
    #   the model fits it exactly and learns nothing -- but the RACE still has a
    #   meaningful rating, because delta is known from everybody else. The pool
    #   mean it needs does not depend on that athlete at all.
    return career[pool], seasonal[code]


# ------------------------------------------------------------------ #
# CHUNK 2 -- ROWS
# ------------------------------------------------------------------ #

# ratedMask
# Purpose:   the rows that get a rating at all.
# Arguments: r_career, r_seasonal -- per-row ratings; group; group_valid.
# Output:    boolean mask over rows.
#
# A rating is emitted only when it is finite, positive, and its athlete-season
# was itself rated. Anything else is left absent so the column reads NULL.
def ratedMask(r_career, r_seasonal, group, group_valid, cell_solved=None,
              course=None):
    """
    Which ROWS get a rating.

    ★ NO LONGER REQUIRES THE ATHLETE-SEASON TO BE RATED. A result rating answers
      "what is this performance worth on this course" --

          rating = 100 * pool_mean / (norm / (1 + delta))

      -- and every term comes from the field, not from the athlete. delta is
      pinned by everyone else who raced the venue, pool_mean by the whole pool.
      An athlete's single race is perfectly rateable even though their ABILITY
      is not estimable from it.

      Requiring group_valid conflated the two and blanked ~10.6M XC rows,
      including every one-race season -- which informativeMask had already
      dropped from the solve, so they were never going to be rated at all.

    ⚠ THE CELL STILL HAS TO BE SOLVED. Without a delta there is no correction to
      apply, and rating an unknown course would just publish raw time dressed up
      as a rating.
    """
    ok = (np.isfinite(r_career) & (r_career > 0)
          & np.isfinite(r_seasonal) & (r_seasonal > 0))
    if cell_solved is not None and course is not None:
        ok &= cell_solved[course]
    return ok


# resultRows
# Purpose:   the tuples to COPY.
# Arguments: result_id, sport, r_career, r_seasonal -- all masked already.
#
# Yields rather than materialising: 55M tuples at once would be several GB of
# Python objects, and copyRows only needs a batch at a time.
def resultRows(result_id, sport, r_career, r_seasonal):
    for rid, sc, rc, rs in zip(result_id, sport, r_career, r_seasonal):
        yield (int(rid), _SPORT_NAME.get(int(sc), "?"),
               round(float(rc), 2), round(float(rs), 2))


_DDL = """
    CREATE TABLE pair_result_rating (
        result_id        bigint  NOT NULL,
        sport            text    NOT NULL,
        rating_career    double precision,
        rating_seasonal  double precision,
        PRIMARY KEY (result_id, sport)
    )"""


# ------------------------------------------------------------------ #
# CHUNK 3 -- OPTIONAL MERGE INTO results.speed_rating
# ------------------------------------------------------------------ #

# mergeIntoResults
# Purpose:   overwrite the real column, using the engine's own writer.
# Arguments: result_id, sport, rating -- masked arrays; label.
#
# ★ CALLS saveResultSpeedRatings, which is the engine's existing path: staging
#   table, then mergeColumn with preserve_unmatched=False, then the heap rebuild.
#   Reimplementing that here would be a second source of truth for the most
#   destructive operation in the project.
#
# ⚠ THIS IS THE IRREVERSIBLE ONE. It rebuilds results (34M rows) and results_tf
#   (27M rows), drops the _old copies on success, and the previous
#   speed_rating values are then gone. Budget ~35 minutes.
def mergeIntoResults(result_id, sport, rating, label):
    from speed_ratings_db import saveResultSpeedRatings

    print(f"\n[write] MERGING {label} into results.speed_rating "
          f"-- this rebuilds both heaps and is not reversible")
    for code, name in _SPORT_NAME.items():
        m = sport == code
        if not m.any():
            continue
        saveResultSpeedRatings(name, (result_id[m], np.round(rating[m], 2)))
        print(f"[write] {name}: {int(m.sum()):,} result ratings written")


# ------------------------------------------------------------------ #
# CHUNK 4 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(pack_path, merge=None):
    from database import getConn

    cols = pe.loadPack(pack_path)
    for needed in ("result_id", "sport"):
        if needed not in cols:
            print(f"[write] pack has no '{needed}' column -- cannot key result "
                  f"ratings. Re-pack with a current speed_ratings.py.")
            return

    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    y_all, _form = pe.buildResponse(cols)

    course = cols["course"][keep].astype(np.int64)
    y = y_all[keep]
    norm = cols["norm"][keep]
    athlete = cols["athlete"][keep]
    year = cols["year"][keep]
    result_id = cols["result_id"][keep]
    sport = cols["sport"][keep]
    group, n_groups = pe.athleteSeasonCodes(athlete, year)
    n_cells = len(cols["course_keys"])

    print(f"[write] {y.size:,} rows, {n_groups:,} athlete-seasons")
    T = pv.solveSubset(course, group, y, n_cells, n_groups, quiet=False)
    delta = T["delta"]

    alpha, _cnt = pv.refitAlpha(y, delta, course, group, n_groups)
    attrs = pr.groupAttributes(group, athlete, year, n_groups,
                               pr.poolPerAthlete(cols["athlete_keys"]))
    rat = pr.buildRatings(alpha, attrs)

    pm_career, pm_seasonal = poolMeanPerGroup(rat["ability"], attrs,
                                              rat["valid"])
    r_career = pr.perRaceRatings(norm, delta, course, group, pm_career)
    r_seasonal = pr.perRaceRatings(norm, delta, course, group, pm_seasonal)

    ok = ratedMask(r_career, r_seasonal, group, rat["valid"])
    print(f"[write] {int(ok.sum()):,}/{ok.size:,} rows rated "
          f"({100.0 * ok.mean():.1f}%)")
    for code, name in _SPORT_NAME.items():
        m = ok & (sport == code)
        if m.any():
            print(f"      {name}: {int(m.sum()):,} rows, "
                  f"mean rating {r_career[m].mean():.1f}")

    rid, sp = result_id[ok], sport[ok]
    rc, rs = r_career[ok], r_seasonal[ok]

    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS pair_result_rating")
            cur.execute(_DDL)
            print("    copying pair_result_rating...")
            n = copyRows(cur, "pair_result_rating",
                         ("result_id", "sport", "rating_career",
                          "rating_seasonal"),
                         resultRows(rid, sp, rc, rs))
            cur.execute("CREATE INDEX ON pair_result_rating (sport, "
                        "rating_career DESC)")
            cur.execute("ANALYZE pair_result_rating")
        conn.commit()
    print(f"[write] wrote {n:,} rows to pair_result_rating")

    if merge:
        rating = rs if merge == "seasonal" else rc
        mergeIntoResults(rid, sp, rating, f"rating_{merge}")
    else:
        print("\n[write] results.speed_rating NOT touched. Compare first:")
        print("""
    SELECT p.sport, count(*),
           round(corr(p.rating_career, r.speed_rating)::numeric, 4) AS corr,
           round(avg(p.rating_career - r.speed_rating)::numeric, 3) AS mean_diff,
           round(stddev(p.rating_career - r.speed_rating)::numeric, 3) AS sd_diff
    FROM pair_result_rating p
    JOIN results r ON r.result_id = p.result_id AND p.sport = 'XC'
    WHERE r.speed_rating IS NOT NULL
    GROUP BY 1;

  then, to overwrite for real:
    python engine\\pair_write_results.py --merge""")


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    merge = ("seasonal" if "--merge-seasonal" in sys.argv
             else "career" if "--merge" in sys.argv else None)
    main(args[0] if args else os.path.join(here, "packed_XC_TF.npz"),
         merge=merge)