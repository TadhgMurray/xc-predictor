#!/usr/bin/env python3
"""
distance_between_venues.py -- is the distance curve wrong ACROSS venues?

    scripts/distance_between_venues.py                  # 5% of athletes
    scripts/distance_between_venues.py --pct 20         # more, slower
    scripts/distance_between_venues.py --pool hs_m      # one pool
    scripts/distance_between_venues.py --cells 40       # worst venue cells

Run from the PROJECT ROOT.

★ WHY THIS ONE EXISTS, AND WHY THE LAST ONE DID NOT SETTLE IT (owner,
  2026-09-10: "I am uncertain that vs own venue is a good thing ... Vs
  other venues might work tho").

  The WITHIN-VENUE test asked whether a distance's ratings deviate from
  that venue's own mean. Weighted by the band that holds 10.9M of 15M
  results it came back at -0.02, i.e. nothing, and the "short races are
  overrated" story died with it. But that test cannot see the failure it
  was aimed at: a venue's own mean is BUILT from the same rows, so a
  distance curve that is wrong everywhere by the same amount is absorbed
  into every venue's mean and reads as zero. Within-venue can only find a
  venue whose distances disagree WITH EACH OTHER.

  This test uses a benchmark the venue cannot move: THE SAME ATHLETES'
  RACES AT OTHER VENUES.

★ THE ESTIMATOR, in one line:

      residual(row) = log(nt) - mean over that athlete-season's rows
                                AT OTHER VENUES of log(nt)

  and then the slope of residual on log(raw distance).

  normalized_time is the time already normalised to the pool's anchor
  distance, so IF THE CURVE IS RIGHT it carries no memory of how far the
  race actually was. Any slope is the curve being wrong.

  Leaving the row's OWN venue out of its own benchmark is what makes this
  between-venue: an athlete's 4828m at Mt. SAC is judged against their
  races everywhere else, never against Mt. SAC.

⚠ READ THE SIGN OFF THIS, DO NOT GUESS IT. normalized_time is a TIME, so
  smaller is better and an OVERRATED row has a normalized_time that is too
  SMALL -- a NEGATIVE residual.

      slope > 0   short races are overrated  (their nt comes out too small)
      slope < 0   long races are overrated
      slope ~ 0   the curve is fine between venues too

  The printout says which in words so nobody has to re-derive it.

⚠⚠ THE RAW SLOPE IS CONFOUNDED AND IS NOT THE ANSWER. READ slope_adj.

  A venue's difficulty and its distance are ONE OBSERVATION EACH, so
  whatever correlation they happen to have lands in the slope. This is
  measured, not argued: tests/test_distance_between_venues.py plants a
  corpus with NO distance bias at all and the raw slope comes back at
  +0.065, an order of magnitude larger than anything worth chasing.
  Subtracting the venue's own course_difficulties entry takes the same
  fixture to +0.005, and recovers a planted +/-0.03 correctly.

  So: slope_adj is the estimate, slope is only there to show how much of
  it was difficulty. If the two are far apart -- and on the real corpus
  they will be -- that gap IS the confound, not a finding.

⚠ AND slope_adj IS AN UPPER BOUND, NOT A CLEAN NUMBER. The difficulties
  it subtracts came out of a solve that ALREADY USED THIS CURVE, so a
  curve error is partly baked into the very control being applied.
  Between venues, the distance curve and course difficulty are not
  separately identified: a course that is 3% slow and a curve that is 3%
  slow at that distance fit the data identically. The only test that
  separates them is a venue that races SEVERAL DISTANCES -- the
  within-venue test, which came back at -0.02 in the dominant band.

  Read this script as: "how much room is left for a curve error once
  difficulty has had its say", not as "the curve is wrong by X".

! RESOURCE GUARDS, because these have eaten the box before. Athlete
  sampling (not TABLESAMPLE on results, which cannot give a whole
  athlete's season), two sequential passes over one UNLOGGED scratch
  table, no LATERAL, work_mem capped, statement_timeout set, and the
  scratch table dropped on the way out however this ends.
"""

import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

_SCRATCH = "bv_rows"

# ★ A CONSTANT, NOT A LITERAL IN THE SQL, AND THAT IS THE POINT. `date` is
#   TEXT on results_tf, so EXTRACT() cannot be used and a malformed row
#   would kill the cast -- hence the ISO guard. But the guard contains
#   {4} and {2}, and these queries are variously f-strings, .format()
#   templates and plain strings: doubling the braces is right in two of
#   those and WRONG in the third, where it renders a literal {{4}} that
#   matches nothing and silently returns zero rows. Substituting a
#   constant is correct in all three, because the value's braces are never
#   re-scanned.
_ISO_DATE = "r.date::text ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'"

# ★ ONE PASS OVER results, ATHLETE-SAMPLED. The sample is on person_id's
#   hash so an athlete is in or out WHOLE -- a half-sampled season has a
#   benchmark built from the rows that happened to survive, which is a
#   different (and biased) estimator.
#
# ! season is the ACADEMIC year (engine/season_year.py's rule, inlined so
#   this script needs no python round trip per row): August opens the year.
_PASS_A = f"""
CREATE UNLOGGED TABLE {_SCRATCH} AS
SELECT r.person_id,
       (CASE WHEN substr(r.date::text, 6, 2)::int >= 8
             THEN substr(r.date::text, 1, 4)::int
             ELSE substr(r.date::text, 1, 4)::int - 1 END)  AS season,
       m.course_name                                        AS venue,
       COALESCE(dov.distance, m.distance)::float            AS distance,
       ln(r.normalized_time)                                AS lnt,
       r.rating_pool                                        AS pool,
       cd.difficulty::float                                 AS diff
FROM   results r
LEFT   JOIN dist_override dov ON dov.meet_id = r.meet_id
                             AND dov.div_id  = r.div_id
JOIN   meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
               AND m.source = r.source
-- ★ THE VENUE'S OWN DIFFICULTY, WHICH THIS TEST CANNOT DO WITHOUT.
--   See _SLOPE: the raw slope is confounded by cov(difficulty, ln
--   distance) and a fixture with NO planted bias reads +0.065 without
--   this column. LEFT, so a venue with no difficulty row still counts
--   toward the raw slope and simply drops out of the adjusted one; the
--   printout reports what share matched.
LEFT   JOIN course_difficulties cd
       ON cd.course_name = 'XC:' || m.course_name
      AND round(cd.distance_m::numeric)
          = round(COALESCE(dov.distance, m.distance)::numeric)
WHERE  r.normalized_time IS NOT NULL AND r.normalized_time > 0
  -- ⚠ date IS TEXT on results_tf; only ISO-shaped rows parse
  AND  {_ISO_DATE}
  AND  r.rating_pool IS NOT NULL
  AND  m.course_name IS NOT NULL
  AND  COALESCE(dov.distance, m.distance) BETWEEN 800 AND 12000
  AND  abs(mod(hashint8(r.person_id::bigint), 10000)) < %(cut)s
  {{pool}}
"""

# ★ THE LEAVE-OWN-VENUE-OUT BENCHMARK, as plain aggregates. For each
#   athlete-season: the total over ALL its rows, and the total over the
#   rows at THIS venue; the difference is the benchmark this row is judged
#   against. n_all > n_own is the condition that the athlete actually
#   raced somewhere else -- without it there is no between-venue evidence
#   in this athlete at all and the row is dropped, not silently zeroed.
_PASS_B = f"""
WITH per_season AS (
    SELECT person_id, season, count(*) AS n_all, sum(lnt) AS s_all
    FROM   {_SCRATCH} GROUP BY 1, 2
), per_venue AS (
    SELECT person_id, season, venue, count(*) AS n_own, sum(lnt) AS s_own
    FROM   {_SCRATCH} GROUP BY 1, 2, 3
), resid AS (
    SELECT b.pool, b.distance, b.venue,
           b.lnt - (p.s_all - v.s_own) / (p.n_all - v.n_own) AS res
    FROM   {_SCRATCH} b
    JOIN   per_season p ON p.person_id = b.person_id AND p.season = b.season
    JOIN   per_venue  v ON v.person_id = b.person_id AND v.season = b.season
                       AND v.venue = b.venue
    WHERE  p.n_all > v.n_own
)
SELECT {{group}}                                    AS grp,
       count(*)                                     AS n,
       round(avg(res)::numeric, 5)                  AS mean_res,
       round(stddev_samp(res)::numeric, 4)          AS sd,
       round(avg(ln(distance))::numeric, 4)         AS mean_lnd
FROM   resid
GROUP  BY 1
HAVING count(*) >= %(min_n)s
ORDER  BY 1
"""

# The regression slope, done in SQL so no result set larger than a handful
# of rows ever crosses the wire. regr_slope(y, x) is y on x.
_SLOPE = f"""
WITH per_season AS (
    SELECT person_id, season, count(*) AS n_all, sum(lnt) AS s_all
    FROM   {_SCRATCH} GROUP BY 1, 2
), per_venue AS (
    SELECT person_id, season, venue, count(*) AS n_own, sum(lnt) AS s_own
    FROM   {_SCRATCH} GROUP BY 1, 2, 3
), resid AS (
    SELECT b.pool, b.distance, b.diff,
           b.lnt - (p.s_all - v.s_own) / (p.n_all - v.n_own) AS res
    FROM   {_SCRATCH} b
    JOIN   per_season p ON p.person_id = b.person_id AND p.season = b.season
    JOIN   per_venue  v ON v.person_id = b.person_id AND v.season = b.season
                       AND v.venue = b.venue
    WHERE  p.n_all > v.n_own
)
SELECT COALESCE(pool, '(all)')                      AS pool,
       count(*)                                     AS n,
       round(regr_slope(res, ln(distance))::numeric, 5)  AS slope,
       round(avg(res)::numeric, 5)                  AS mean_res,
       count(diff)                                  AS n_with_diff,
       round(regr_slope(res - diff,
                        ln(distance))::numeric, 5)  AS slope_adj
FROM   resid
GROUP  BY ROLLUP (pool)
HAVING count(*) >= %(min_n)s
ORDER  BY 2 DESC
"""

# ★ THE VENUE CELLS THEMSELVES, which is the Mt. SAC / Morley question in
#   its own right: a cell whose athletes run systematically slower here
#   than they do elsewhere is a HARD cell, and that number owes nothing to
#   the cell's own mean.
_CELLS = f"""
WITH per_season AS (
    SELECT person_id, season, count(*) AS n_all, sum(lnt) AS s_all
    FROM   {_SCRATCH} GROUP BY 1, 2
), per_venue AS (
    SELECT person_id, season, venue, count(*) AS n_own, sum(lnt) AS s_own
    FROM   {_SCRATCH} GROUP BY 1, 2, 3
), resid AS (
    SELECT b.venue, round(b.distance / 100.0) * 100 AS dist_cell,
           b.lnt - (p.s_all - v.s_own) / (p.n_all - v.n_own) AS res
    FROM   {_SCRATCH} b
    JOIN   per_season p ON p.person_id = b.person_id AND p.season = b.season
    JOIN   per_venue  v ON v.person_id = b.person_id AND v.season = b.season
                       AND v.venue = b.venue
    WHERE  p.n_all > v.n_own
)
SELECT venue, dist_cell::int AS dist,
       count(*)                                     AS n,
       round((100 * avg(res))::numeric, 2)          AS pct_vs_elsewhere,
       round((100 * stddev_samp(res)
              / sqrt(count(*)))::numeric, 2)        AS se_pct
FROM   resid
GROUP  BY 1, 2
HAVING count(*) >= %(cell_n)s
ORDER  BY abs(avg(res)) DESC
LIMIT  %(cells)s
"""


def _rows(cur, sql, args):
    cur.execute(sql, args)
    return [d[0] for d in cur.description], cur.fetchall()


def _table(cols, rows, indent="    "):
    if not rows:
        print(f"{indent}(no rows)")
        return
    body = [[("" if v is None else str(v)) for v in r] for r in rows]
    w = [max(len(c), *(len(b[i]) for b in body)) for i, c in enumerate(cols)]
    print(indent + "  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    print(indent + "  ".join("-" * w[i] for i in range(len(cols))))
    for b in body:
        print(indent + "  ".join(b[i].ljust(w[i]) for i in range(len(b))))


def _verdict(slope):
    """The sign, in words. See this file's header -- normalized_time is a
    TIME, so an overrated row is a NEGATIVE residual."""
    if slope is None:
        return "no slope (not enough rows)"
    s = float(slope)
    if abs(s) < 0.002:
        return ("the curve is FINE between venues too "
                "(under 0.2% per e-fold of distance)")
    if s > 0:
        return (f"SHORT races are overrated: {100 * s:+.2f}% of "
                "normalized_time per e-fold of distance")
    return (f"LONG races are overrated: {100 * s:+.2f}% of "
            "normalized_time per e-fold of distance")


def main():
    ap = argparse.ArgumentParser(
        description="Is the XC distance curve wrong when a venue is judged "
                    "against the same athletes' races at OTHER venues?")
    ap.add_argument("--pct", type=float, default=5.0,
                    help="percent of ATHLETES to sample (default 5)")
    ap.add_argument("--pool", help="restrict to one rating pool, e.g. hs_m")
    ap.add_argument("--min-n", type=int, default=500,
                    help="minimum rows for a printed group (default 500)")
    ap.add_argument("--cells", type=int, default=25,
                    help="how many venue-distance cells to print")
    ap.add_argument("--cell-n", type=int, default=200,
                    help="minimum rows for a printed cell (default 200)")
    ap.add_argument("--work-mem", default="256MB")
    ap.add_argument("--timeout", default="20min")
    args = ap.parse_args()

    cut = int(round(args.pct * 100))          # hash mod 10000 -> 0.01% steps
    if cut <= 0:
        print("--pct must be above 0")
        return 2

    # ⚠ getConn IS A CONTEXT MANAGER OVER A POOLED CONNECTION, not a
    #   connection: `with getConn() as conn`. It dies at the FIRST cursor
    #   if you forget, which no offline check can see;
    #   tests/test_lint_getconn.py greps for it now.
    #
    # ! SET LOCAL, not SET. The connection goes back to this process's pool
    #   and a later query here would inherit the setting. (It cannot reach
    #   the site -- gunicorn is a different process with its own pool.)
    #
    # ! AND THE SCRATCH TABLE LIVES INSIDE THE TRANSACTION. Rolling back at
    #   the end undoes the CREATE completely, so nothing is left behind on
    #   a pooled connection however this ends -- including a Ctrl-C.
    from database import getConn
    with getConn() as conn:
        cur = conn.cursor()
        # ! MODEST, ON PURPOSE. The site shares this box; a diagnostic that
        #   takes it down is worse than no diagnostic.
        cur.execute(f"SET LOCAL work_mem = '{args.work_mem}'")
        cur.execute(f"SET LOCAL statement_timeout = '{args.timeout}'")
        cur.execute("SET LOCAL max_parallel_workers_per_gather = 2")
        try:
            cur.execute(f"DROP TABLE IF EXISTS {_SCRATCH}")
            pool = "AND r.rating_pool = %(pool)s" if args.pool else ""
            t0 = time.time()
            print(f"\npass A: reading results for {args.pct}% of athletes"
                  f"{' in ' + args.pool if args.pool else ''} ...", flush=True)
            cur.execute(_PASS_A.format(pool=pool), {"cut": cut, "pool": args.pool})
            cur.execute(f"SELECT count(*) FROM {_SCRATCH}")
            n = cur.fetchone()[0]
            print(f"  {n:,} rows in {time.time() - t0:.0f}s", flush=True)
            if n == 0:
                print("  nothing to measure")
                return 1
            cur.execute(f"CREATE INDEX ON {_SCRATCH} (person_id, season)")

            print("\nSLOPE OF RESIDUAL ON ln(distance), by pool")
            print("  residual = this row's log normalized_time MINUS the same")
            print("  athlete-season's mean AT OTHER VENUES.")
            print("  slope     RAW -- CONFOUNDED by cov(difficulty, distance);")
            print("            a corpus with no bias at all reads +0.065 here.")
            print("  slope_adj the same after subtracting the venue's own")
            print("            course_difficulties entry. THIS IS THE ESTIMATE.")
            cols, rows = _rows(cur, _SLOPE, {"min_n": args.min_n})
            _table(cols, rows)
            overall = [r for r in rows if r[0] == "(all)"]
            if overall:
                n, adj, have = overall[0][1], overall[0][5], overall[0][4]
                print(f"\n  => {_verdict(adj)}")
                print(f"     (difficulty-adjusted, on {have:,} of {n:,} rows "
                      f"that matched a course_difficulties cell)")
                print("     UPPER BOUND: those difficulties came from a solve")
                print("     that already used this curve. See the header.")

            print("\nRESIDUAL BY DISTANCE BAND (500m bands, all pools)")
            band = "(round(distance / 500.0) * 500)::int"
            cols, rows = _rows(cur, _PASS_B.format(group=band),
                               {"min_n": args.min_n})
            _table(cols, rows)
            print("  mean_res is in LOG units: -0.01 means those races come out")
            print("  1% faster than the same athletes run elsewhere.")

            print(f"\nWORST {args.cells} VENUE-DISTANCE CELLS vs ELSEWHERE")
            print("  pct_vs_elsewhere > 0: athletes run SLOWER here than they do")
            print("  at other venues, i.e. a HARD cell. This number is built")
            print("  entirely from other venues, so the cell cannot move it.")
            cols, rows = _rows(cur, _CELLS,
                               {"cells": args.cells, "cell_n": args.cell_n})
            _table(cols, rows)
        finally:
            conn.rollback()          # takes the scratch table with it
            cur.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
