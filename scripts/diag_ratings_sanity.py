# Project: xc-predictor
# File:    scripts/diag_ratings_sanity.py
# Purpose: After engine/speed_ratings.py, answer two questions:
#            1. Are the TOTALS right?   (reconciliation, from identities)
#            2. Do the TOP results and athletes look like real runners?
#
# ============================================================================
# THE THREE IDENTITIES  (read these; they are what "right" means here)
# ============================================================================
# The engine writes three things, and each has a DIFFERENT population. Confusing
# them is how a broken run looks fine.
#
#   results.speed_rating
#       written for every row passing `ok = (pm > 0) & (adjusted > 0)` in
#       buildResultRatings. `pm` is a POOL mean, so a row belonging to an
#       athlete with only 1 race STILL gets a rating.
#       => rated results INCLUDE results of athletes who are never rated.
#
#   athlete_ratings
#       one row per VALID (person_id, pool): n_races >= MIN_RACES (3), ability
#       inside [ABILITY_MIN, ABILITY_MAX], pool has a mean.
#       => strictly fewer athletes than appear in rated results.
#
#   course_difficulties
#       one row per venue with >= 1 result -- INCLUDING thin venues, which are
#       saved with difficulty = 0.0 (see buildDifficultiesToSave: it skips only
#       counts[code] == 0).
#       => count should equal the engine's reported venue count exactly.
#
# The identity that ties the first two together:
#
#       sum(athlete_ratings.n_races)  +  (rated results of unrated athletes)
#         ==  count(results.speed_rating)
#
# because n_races counts ALL of a valid athlete's rows, and every one of those
# rows gets a rating (same pool, same pm). If that does not balance, something
# is double-counting -- a fan-out join, or a twin that survived dedup.
#
# ============================================================================
# WHAT "LOOKS RIGHT" MEANS FOR THE NUMBERS
# ============================================================================
#   speed_rating = pool_mean / adjusted_time * 100.
#   Faster (fewer seconds) -> HIGHER. An athlete exactly at their pool's mean
#   ability scores 100. So:
#     * the MEDIAN rating in a pool should sit near 100 (it is the anchor),
#     * elite high schoolers land maybe 125-145,
#     * anything above ~200 or below ~40 is a 2x error, not an athlete.
#
# SAFETY: read-only, rolls back immediately.
#
# USAGE
#   python scripts/diag_ratings_sanity.py --sport XC
#   python scripts/diag_ratings_sanity.py --sport TF --top 15
#   python scripts/diag_ratings_sanity.py --sport XC --skip-results   # faster

import argparse
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool


_TABLE = {"XC": "results", "TF": "results_tf"}
_MIN_TIME, _MAX_TIME = 600.0, 3600.0

# A rating outside this band is arithmetic, not athletics.
_SANE_LO, _SANE_HI = 40.0, 200.0


# ================================================================== #
# SCHEMA PROBE  —  ask, do not assume
# ================================================================== #

# _columns
# Purpose : the column names of a table. `athletes` stores names in a way this
#           script must discover: the master doc says first_name + last_name,
#           but results.athlete_name also exists and is ~90% null. Rather than
#           guess, we look, and build a name expression from what is there.
def _columns(cur, table):
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s
    """, (table,))
    return {r[0] for r in cur.fetchall()}


# _nameExpr
# Purpose : a SQL expression yielding a readable athlete name, from whatever
#           columns exist. Falls back to the id, which is always available.
# Syntax  : the expression is spliced into a LATERAL subquery keyed on
#           athlete_id. A LATERAL with LIMIT 1 is used instead of a plain join
#           because `athletes` has a COMPOSITE primary key (athlete_id, school),
#           so joining on athlete_id alone would DUPLICATE every result of an
#           athlete who changed schools -- 863,443 athletes have >1 school row.
def _nameExpr(cols):
    if {"first_name", "last_name"} <= cols:
        return "trim(coalesce(a.first_name,'') || ' ' || coalesce(a.last_name,''))"
    if "athlete_name" in cols:
        return "a.athlete_name"
    return "NULL"


# ================================================================== #
# 1. RECONCILIATION
# ================================================================== #

def _scalar(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchone()[0]


# _loaderOutput
# Purpose : the number of rows the engine's loader WOULD have emitted. This is
#           the denominator for "did we rate everything we could".
def _loaderOutput(cur, table):
    return _scalar(cur, f"""
        SELECT count(*) FROM {table}
        WHERE normalized_time IS NOT NULL
          AND normalized_time BETWEEN {_MIN_TIME} AND {_MAX_TIME}
          AND date IS NOT NULL AND person_id IS NOT NULL
    """)


# _reconcile
# Purpose : the three populations and the identity that binds them.
def _reconcile(cur, sport, table):
    like_pool = f"%|{sport}"
    like_venue = f"{sport}:%"

    total = _scalar(cur, f"SELECT count(*) FROM {table}")
    normed = _scalar(cur, f"SELECT count(normalized_time) FROM {table}")
    loader = _loaderOutput(cur, table)
    rated = _scalar(cur, f"SELECT count(speed_rating) FROM {table}")

    n_ath = _scalar(cur, "SELECT count(*) FROM athlete_ratings WHERE pool LIKE %s",
                    (like_pool,))
    sum_races = _scalar(cur, "SELECT coalesce(sum(n_races),0) FROM athlete_ratings "
                             "WHERE pool LIKE %s", (like_pool,))
    n_venue = _scalar(cur, "SELECT count(*) FROM course_difficulties "
                           "WHERE course_name LIKE %s", (like_venue,))
    n_thin = _scalar(cur, "SELECT count(*) FROM course_difficulties "
                          "WHERE course_name LIKE %s AND difficulty = 0", (like_venue,))

    # rated rows whose athlete never earned a rating (fewer than MIN_RACES races)
    unrated_ath_rows = _scalar(cur, f"""
        SELECT count(*) FROM {table} r
        WHERE r.speed_rating IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM athlete_ratings ar
                          WHERE ar.athlete_id = r.person_id AND ar.pool LIKE %s)
    """, (like_pool,))

    # FOSSILS: rows carrying a speed_rating that the loader could not have
    # emitted. buildResultRatings cannot rate a row that never entered `cols`,
    # so any such row's rating was written by an EARLIER run and preserved by
    # mergeColumn's COALESCE. A stale value is worse than a missing one: NULL
    # says "we do not know"; 7528 says "this athlete ran a 20-second 5K".
    fossils = _scalar(cur, f"""
        SELECT count(*) FROM {table}
        WHERE speed_rating IS NOT NULL
          AND NOT (normalized_time IS NOT NULL
                   AND normalized_time BETWEEN {_MIN_TIME} AND {_MAX_TIME}
                   AND date IS NOT NULL AND person_id IS NOT NULL)
    """)

    print("=" * 78)
    print(f"1. RECONCILIATION  --  {sport} ({table})")
    print("=" * 78)
    print(f"  rows in {table:<14}{total:>16,}")
    print(f"  with normalized_time      {normed:>16,}")
    print(f"  loader would emit         {loader:>16,}")
    print(f"  with speed_rating         {rated:>16,}"
          f"   ({100.0*rated/loader if loader else 0:5.1f}% of loader output)")
    print(f"  ...of which FOSSILS       {fossils:>16,}   "
          f"{'(stale, from an older run)' if fossils else '(clean)'}")
    if fossils:
        print(f"     A row the loader cannot emit cannot be rated by this run.")
        print(f"     These ratings survived mergeColumn's COALESCE. Rerun with")
        print(f"     preserve_unmatched=False (speed_ratings_db already does).")
        cur.execute(f"""
            SELECT result_id, speed_rating, normalized_time, date
            FROM {table}
            WHERE speed_rating IS NOT NULL
              AND NOT (normalized_time IS NOT NULL
                       AND normalized_time BETWEEN {_MIN_TIME} AND {_MAX_TIME}
                       AND date IS NOT NULL AND person_id IS NOT NULL)
            ORDER BY speed_rating DESC LIMIT 5
        """)
        for rid, sr, nt, dt in cur.fetchall():
            print(f"       {rid:>12}  rating={sr:>9.1f}  norm={nt}  {dt}")
    print()
    print(f"  athlete_ratings rows      {n_ath:>16,}   (VALID athletes only)")
    print(f"  sum(n_races)              {sum_races:>16,}")
    print(f"  rated rows, unrated ath.  {unrated_ath_rows:>16,}   (< MIN_RACES)")
    print(f"  {'-' * 44}")
    lhs = sum_races + unrated_ath_rows
    delta = rated - lhs                      # POSITIVE = more rated rows than accounted for
    ok = (delta == 0)
    print(f"  sum(n_races) + unrated    {lhs:>16,}")
    print(f"  should equal rated rows   {rated:>16,}   "
          f"{'BALANCED' if ok else f'!! rated is {delta:+,}'}")
    if delta > 0:
        print(f"     {delta:,} MORE rated rows than any athlete accounts for.")
        print(f"     That is DOUBLE COUNTING: a fan-out join (athletes has a")
        print(f"     composite PK -- 863,443 athlete_ids have >1 school row), or")
        print(f"     a cross-source twin the dedup missed.")
    elif delta < 0:
        print(f"     {-delta:,} FEWER rated rows than athletes account for.")
        print(f"     Some valid athlete's rows failed `adjusted > 0`, i.e. their")
        print(f"     venue's difficulty is <= -1. Check section 5.")
    print()
    print(f"  course_difficulties rows  {n_venue:>16,}")
    print(f"    of which difficulty = 0 {n_thin:>16,}   "
          f"(thin: < MIN_COURSE_ATHLETES, or venueless)")
    return rated


# ================================================================== #
# 2. DISTRIBUTION PER POOL
# ================================================================== #

# _poolDistribution
# Purpose : per pool, the shape of athlete_ratings.speed_rating.
# The MEDIAN is the check: the engine anchors 100 = pool mean ability, so a
#   pool's median rating must sit near 100. A median of 60 or 300 means the pool
#   mean is being dragged by degenerate abilities -- exactly the failure that
#   printed `pool mean hs_m|XC 134.4s` when a 5K equivalent should be ~1300s.
# athlete_ratings is small (millions), so this is exact, not sampled.
def _poolDistribution(cur, sport):
    cur.execute("""
        SELECT pool, count(*), 
               percentile_cont(0.01) WITHIN GROUP (ORDER BY speed_rating::numeric),
               percentile_cont(0.50) WITHIN GROUP (ORDER BY speed_rating::numeric),
               percentile_cont(0.99) WITHIN GROUP (ORDER BY speed_rating::numeric),
               max(speed_rating),
               avg(n_races)
        FROM athlete_ratings WHERE pool LIKE %s
        GROUP BY pool ORDER BY 2 DESC
    """, (f"%|{sport}",))
    rows = cur.fetchall()
    print(f"\n{'=' * 78}")
    print("2. ATHLETE RATING DISTRIBUTION PER POOL  (median should be ~100)")
    print("=" * 78)
    print(f"{'pool':<28}{'athletes':>11}{'p1':>9}{'p50':>9}{'p99':>9}"
          f"{'max':>10}{'races':>8}")
    print("-" * 78)
    for pool, n, p1, p50, p99, mx, races in rows:
        flag = "" if 85 <= float(p50) <= 115 else "  <-- median far from 100"
        print(f"{pool:<28}{n:>11,}{float(p1):>9.1f}{float(p50):>9.1f}"
              f"{float(p99):>9.1f}{mx:>10.1f}{float(races):>8.1f}{flag}")
    return [r[0] for r in rows]


# ================================================================== #
# 3. TOP ATHLETES
# ================================================================== #

# _topAthletes
# Purpose : the highest-rated athletes in a pool, with a name.
# Cheap: athlete_ratings is small and indexed by nothing, but a per-pool ORDER BY
#   over a few hundred thousand rows is a top-N heap, not a sort of the corpus.
def _topAthletes(cur, pool, name_expr, n):
    cur.execute(f"""
        SELECT ar.athlete_id, ar.speed_rating, ar.n_races,
               COALESCE(nm.nm, '(no name)') AS name
        FROM athlete_ratings ar
        LEFT JOIN LATERAL (
            SELECT {name_expr} AS nm FROM athletes a
            WHERE a.athlete_id = ar.athlete_id LIMIT 1) nm ON true
        WHERE ar.pool = %s
        ORDER BY ar.speed_rating DESC
        LIMIT {n}
    """, (pool,))
    return cur.fetchall()


# ================================================================== #
# 4. TOP RESULTS
# ================================================================== #

# _topResults
# Purpose : the fastest individual performances, per pool.
# Syntax  : one pass over the results table, hash-joined to athlete_ratings
#   (filtered to this sport, so an athlete who runs both sports cannot fan out),
#   then a window function partitioned by pool. `row_number() OVER (PARTITION BY
#   ... ORDER BY ... DESC)` ranks within each pool in ONE sort rather than one
#   sort per pool.
# SET LOCAL work_mem: the sort is ~34M x 40 bytes. At the 4MB default it spills
#   into hundreds of disk batches. LOCAL means it expires at commit and cannot
#   ride back into the connection pool.
def _topResults(cur, sport, table, name_expr, n):
    cur.execute("SET LOCAL work_mem = '1GB'")
    cur.execute(f"""
        SELECT pool, result_id, speed_rating, normalized_time, date,
               meet_id, div_id, school, name
        FROM (
            SELECT ar.pool, r.result_id, r.speed_rating, r.normalized_time,
                   r.date, r.meet_id, r.div_id, r.school,
                   COALESCE(nm.nm, '(no name)') AS name,
                   row_number() OVER (PARTITION BY ar.pool
                                      ORDER BY r.speed_rating DESC) AS rn
            FROM {table} r
            JOIN athlete_ratings ar
              ON ar.athlete_id = r.person_id AND ar.pool LIKE %s
            LEFT JOIN LATERAL (
                SELECT {name_expr} AS nm FROM athletes a
                WHERE a.athlete_id = r.athlete_id LIMIT 1) nm ON true
            WHERE r.speed_rating IS NOT NULL
        ) x WHERE rn <= {n}
        ORDER BY pool, speed_rating DESC
    """, (f"%|{sport}",))
    return cur.fetchall()


# ================================================================== #
# 5. RED FLAGS
# ================================================================== #

# _redFlags
# Purpose : the two things that are wrong even if the totals balance.
#   (a) ratings outside [40, 200] -- a 2x arithmetic error, not an athlete.
#   (b) venues with an extreme fitted difficulty and FEW results. These are
#       venues where the engine BELIEVED a corrupt division: the difficulty
#       absorbed the corruption, so the residual is ~0 and no residual method
#       can ever see it. This is the second arm of the outlier detector, and it
#       is one query.
def _redFlags(cur, sport, table):
    print(f"\n{'=' * 78}")
    print("5. RED FLAGS")
    print("=" * 78)

    lo = _scalar(cur, f"SELECT count(*) FROM {table} WHERE speed_rating < {_SANE_LO}")
    hi = _scalar(cur, f"SELECT count(*) FROM {table} WHERE speed_rating > {_SANE_HI}")
    print(f"  results with speed_rating < {_SANE_LO:g}   {lo:>12,}")
    print(f"  results with speed_rating > {_SANE_HI:g}  {hi:>12,}")
    if lo or hi:
        cur.execute(f"""
            SELECT result_id, speed_rating, normalized_time, meet_id, div_id, date
            FROM {table}
            WHERE speed_rating IS NOT NULL
              AND (speed_rating < {_SANE_LO} OR speed_rating > {_SANE_HI})
            ORDER BY speed_rating DESC LIMIT 8
        """)
        print(f"    {'result_id':>14}{'rating':>10}{'norm':>10}{'meet':>10}"
              f"{'div':>10}  date")
        for rid, sr, nt, mid, did, dt in cur.fetchall():
            print(f"    {rid:>14}{sr:>10.1f}{nt:>10.1f}{mid or 0:>10}"
                  f"{did or 0:>10}  {dt}")

    print(f"\n  venues the engine may have BELIEVED (extreme difficulty, thin data):")
    cur.execute("""
        SELECT course_name, difficulty, n_results, n_athletes
        FROM course_difficulties
        WHERE course_name LIKE %s AND abs(difficulty) > 0.25
        ORDER BY n_results ASC, abs(difficulty) DESC
        LIMIT 15
    """, (f"{sport}:%",))
    rows = cur.fetchall()
    if not rows:
        print("    (none with |difficulty| > 0.25)")
    else:
        print(f"    {'difficulty':>11}{'n_results':>11}{'n_athletes':>12}  venue")
        for name, d, nr, na in rows:
            print(f"    {d:>+11.3f}{nr:>11,}{na:>12,}  {name}")
        print("\n    Low n_results + large |difficulty| = the corruption was")
        print("    ABSORBED into the venue, not exposed as a residual. No")
        print("    residual method can see these. This list is how you do.")


def main():
    ap = argparse.ArgumentParser(description="Sanity-check the speed ratings.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--skip-results", action="store_true",
                    help="skip section 4 (a full sort of the results table)")
    args = ap.parse_args()

    table = _TABLE[args.sport]
    initPool()
    with getConn() as conn:
        with conn.cursor() as cur:
            name_expr = _nameExpr(_columns(cur, "athletes"))
            _reconcile(cur, args.sport, table)
            pools = _poolDistribution(cur, args.sport)

            print(f"\n{'=' * 78}")
            print(f"3. TOP {args.top} ATHLETES PER POOL")
            print("=" * 78)
            for pool in pools:
                print(f"\n  {pool}")
                print(f"    {'rating':>8}{'races':>7}  {'person_id':>12}  name")
                for pid, sr, nr, nm in _topAthletes(cur, pool, name_expr, args.top):
                    print(f"    {sr:>8.1f}{nr:>7}  {pid:>12}  {nm}")

            if not args.skip_results:
                print(f"\n{'=' * 78}")
                print(f"4. TOP {args.top} RESULTS PER POOL "
                      f"(one sort of {table}; a few minutes)")
                print("=" * 78)
                cur_pool = None
                for (pool, rid, sr, nt, dt, mid, did, sch, nm) in _topResults(
                        cur, args.sport, table, name_expr, args.top):
                    if pool != cur_pool:
                        cur_pool = pool
                        print(f"\n  {pool}")
                        print(f"    {'rating':>8}{'norm':>9}  {'date':<12}"
                              f"{'meet':>9}{'div':>9}  name (school)")
                    print(f"    {sr:>8.1f}{nt:>9.1f}  {str(dt):<12}"
                          f"{mid or 0:>9}{did or 0:>9}  {nm} ({sch})")

            _redFlags(cur, args.sport, table)
        conn.rollback()          # release ACCESS SHARE; discard SET LOCAL


if __name__ == "__main__":
    main()