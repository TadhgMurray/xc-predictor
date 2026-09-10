#!/usr/bin/env python3
"""
shrinkage_audit.py -- is course difficulty actually shrunk toward zero when
there is almost no evidence for it, and does track difficulty average zero?

    scripts/shrinkage_audit.py                 # both sports
    scripts/shrinkage_audit.py --sport XC
    scripts/shrinkage_audit.py --worst 60      # more offenders listed

Run from the PROJECT ROOT. READ-ONLY -- every statement is a SELECT.

★ THE COMPLAINTS THIS SETTLES, all of which are the same question (owner,
  2026-09-10, on a list of eleven: "yes" to each of these):

    "courses with 10 results not shrunk"        -> section 2
    "Run the Grove is not shrunk"               -> section 3
    "the difficulty is crazy and only ten
     results are there (no shrinkage)"          -> sections 2 and 3
    "track difficulty not default 0.0"          -> section 1
    "TF difficulty variance too high"           -> section 1
    "Mt. SAC and Crystal Springs keep ping
     ponging between 9% and 5%"                 -> section 2's thin end

★ WHY n_races AND NOT n_results, WHICH IS THE WHOLE POINT. The solver
  shrinks a cell by the information behind it, and the information it
  counts is ROWS: pen_cell = sigma2/tau2 sits next to sum(w*h^2) over the
  cell's rows, so the shrink factor is about n/(n + sigma2/tau2) and the
  half-shrink point is sigma2/tau2 RESULTS. With sigma and tau both near
  5% that point is UNDER ONE RESULT, so ten results keep ~93% of their raw
  deviation and "shrunk" means nothing.

  But ten results at a course are usually ONE RACE -- ten finishers, one
  day, one weather, one field. They are not ten independent readings of
  the course, they are one reading with ten witnesses. So the honest x
  axis is RACES, and that is what this bins on.

  If difficulty spread is FLAT across the race buckets, a course seen once
  is being trusted as much as a course seen fifty times, and that is the
  bug. If it RISES with races, shrinkage is working and the thin end is
  merely uncertain rather than wrong.

! HOW RACES ARE COUNTED, and what the count is and is not. One race is
  one division at one meet, counted PER VENUE -- a course_name for XC, a
  location_id plus the indoor flag for TF, because those are what the two
  sports' cell keys are actually made of. A venue's races are split across
  its distance cells, so this is an UPPER BOUND on any one cell's races:
  if a venue reads thin, every cell in it is thin.

  It is deliberately not joined on distance. The distance in an XC key is
  SNAPPED (engine/distance_pin.snapStandard) while meets.distance is the
  raw scraped number, so an equality join misses whenever snapping moved
  it -- and the first version of this reported those misses as "0 races",
  which then sat in the 1-race bucket and made the thin end look thinner
  and wilder than it is. Unmatched cells now get their own row.

! DIFFICULTY IS A FRACTION and printed as a percent, matching the athlete
  page (which shows difficulty * 100).
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

# ★ SECTION 1. The two numbers the owner asked for by name: is the mean
#   difficulty zero, and how wide is each sport's spread? A sport whose
#   mean is not ~0 has its anchor somewhere else, and the site's per-sport
#   view is then re-expressing an offset nobody chose.
_LEVEL = """
    SELECT substring(course_name FROM 1 FOR 2)              AS sport,
           count(*)                                         AS cells,
           sum(n_results)                                   AS results,
           round((100 * avg(difficulty))::numeric, 3)       AS mean_pct,
           round((100 * stddev_samp(difficulty))::numeric, 3) AS sd_pct,
           round((100 * percentile_cont(0.5) WITHIN GROUP
                  (ORDER BY difficulty))::numeric, 3)       AS p50_pct,
           round((100 * percentile_cont(0.99) WITHIN GROUP
                  (ORDER BY difficulty))::numeric, 3)       AS p99_pct
    FROM   course_difficulties
    WHERE  difficulty IS NOT NULL
    GROUP  BY 1
    ORDER  BY 1
"""

# The same, weighted by results -- the anchor the solver actually set is a
# results-weighted mean, so an unweighted mean being off zero is not by
# itself a bug. If BOTH are off zero, the anchor is wrong.
_LEVEL_W = """
    SELECT substring(course_name FROM 1 FOR 2)              AS sport,
           round((100 * sum(difficulty * n_results)
                  / NULLIF(sum(n_results), 0))::numeric, 3) AS wmean_pct
    FROM   course_difficulties
    WHERE  difficulty IS NOT NULL AND n_results > 0
    GROUP  BY 1
    ORDER  BY 1
"""

# ★ SECTION 2. THE TEST. Spread by how many RACES back the cell.
_BUCKETS = """
    WITH races AS (
        {races}
    ), joined AS (
        SELECT cd.course_name, cd.difficulty, cd.n_results,
               COALESCE(r.n_races, 0)                       AS n_races
        FROM   course_difficulties cd
        LEFT   JOIN races r
               ON r.venue_key = substring(cd.course_name FROM 4)
        WHERE  cd.difficulty IS NOT NULL
          AND  cd.course_name LIKE %(pfx)s
    )
    SELECT CASE WHEN n_races = 0   THEN '0 (UNMATCHED)'
                WHEN n_races = 1   THEN '1'
                WHEN n_races = 2   THEN '2'
                WHEN n_races <= 5  THEN '3-5'
                WHEN n_races <= 10 THEN '6-10'
                WHEN n_races <= 25 THEN '11-25'
                WHEN n_races <= 60 THEN '26-60'
                ELSE                    '61+' END           AS races,
           count(*)                                         AS cells,
           round(avg(n_results)::numeric, 0)                AS avg_results,
           round((100 * stddev_samp(difficulty))::numeric, 2) AS sd_pct,
           round((100 * avg(abs(difficulty)))::numeric, 2)  AS mean_abs_pct,
           round((100 * percentile_cont(0.9) WITHIN GROUP
                  (ORDER BY abs(difficulty)))::numeric, 2)  AS p90_abs_pct
    FROM   joined
    GROUP  BY 1
    ORDER  BY min(n_races)
"""

# ★ SECTION 3. The offenders by name -- a big difficulty on almost no
#   evidence. "Run the Grove" should be in here.
_WORST = """
    WITH races AS (
        {races}
    )
    SELECT substring(cd.course_name FROM 4)                 AS course,
           cd.distance_m::int                               AS dist,
           cd.n_results, cd.n_athletes,
           COALESCE(r.n_races, 0)                           AS n_races,
           round((100 * cd.difficulty)::numeric, 2)         AS pct
    FROM   course_difficulties cd
    LEFT   JOIN races r
           ON r.venue_key = substring(cd.course_name FROM 4)
    WHERE  cd.difficulty IS NOT NULL
      AND  cd.course_name LIKE %(pfx)s
      AND  abs(cd.difficulty) >= %(big)s
      AND  COALESCE(r.n_races, 0) <= %(thin)s
    ORDER  BY abs(cd.difficulty) DESC
    LIMIT  %(worst)s
"""

# ★ HOW A CELL IS KEYED, WHICH IS NOT THE SAME FOR THE TWO SPORTS AND IS
#   WHY THE FIRST VERSION OF THIS CRASHED ON TF AND SILENTLY REPORTED
#   ZERO RACES ON HALF OF XC.
#
#     XC   'XC:<venue>:d<distance>'   venue is a course_name (or an id)
#     TF   'TF:loc:<location_id>:<in|out>'
#
#   meets_tf has no course_name and no distance column at all -- a track
#   venue is a location_id -- so the TF query had to be written against a
#   different table shape, not the same one with a different filter.
#
# ⚠ AND THE XC DISTANCE IN THE KEY IS SNAPPED, NOT SCRAPED. The ':d' part
#   comes off the engine key, where the backfill had already pinned it to
#   a standard distance (engine/distance_pin.snapStandard); meets.distance
#   is the raw scraped number. Joining the two on equality therefore MISSES
#   whenever snapping moved the distance, and the miss looked like "this
#   cell has 0 races" -- which then landed in the '1' bucket and made the
#   thin end look thinner and wilder than it is.
#
#   So races are now counted PER VENUE, not per venue-distance cell. That
#   is an UPPER BOUND on the cell's own races (a venue's races are split
#   across its distance cells) and it cannot silently read zero. Cells that
#   match no venue at all are reported as a count, not bucketed.


def _cols(cur, table):
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s""",
                (table,))
    return {r[0] for r in cur.fetchall()}


def _racesXc(cur):
    """One race is one division at one meet, counted per course_name."""
    have = _cols(cur, "meets")
    if "course_name" not in have:
        return None, "meets has no course_name column"
    key = [c for c in ("meet_id", "div_id", "source") if c in have]
    return ("""SELECT course_name AS venue_key, count(*) AS n_races
               FROM (SELECT DISTINCT {k}, course_name
                     FROM meets WHERE course_name IS NOT NULL) d
               GROUP BY 1""".format(k=", ".join(key)), None)


def _racesTf(cur):
    """A track venue is a location_id, and the key carries the indoor flag.
    Rebuilt to match 'loc:<id>:<in|out>' exactly."""
    have = _cols(cur, "meets_tf")
    if "location_id" not in have:
        return None, ("meets_tf has no location_id column "
                      f"(it has: {', '.join(sorted(have)) or 'nothing'})")
    key = [c for c in ("meet_id", "div_id") if c in have]
    indoor = ("CASE WHEN COALESCE(is_indoor, 0) = 1 THEN 'in' ELSE 'out' END"
              if "is_indoor" in have else "'out'")
    return ("""SELECT 'loc:' || location_id::text || ':' || {ind} AS venue_key,
                      count(*) AS n_races
               FROM (SELECT DISTINCT {k}, location_id{ic}
                     FROM meets_tf WHERE location_id IS NOT NULL) d
               GROUP BY 1""".format(
        k=", ".join(key), ind=indoor,
        ic=", is_indoor" if "is_indoor" in have else ""), None)


# ★ SECTION 4. DIFFICULTY BY THE DISTANCE THE CELL CLAIMS. No join, so it
#   cannot fail, and it is where the worst rows in section 3 come from: an
#   XC "course" at 1600m with +94% difficulty is not a hard course, it is a
#   5k whose distance was scraped as 1600m. Normalising a 5k time as though
#   it were 1600m makes it absurdly slow, and the cell absorbs the whole
#   error as difficulty.
_BY_DIST = """
    SELECT CASE WHEN distance_m IS NULL      THEN 'null'
                WHEN distance_m <  1500      THEN '<1500'
                WHEN distance_m <  2500      THEN '1500-2499'
                WHEN distance_m <  3500      THEN '2500-3499'
                WHEN distance_m <  4500      THEN '3500-4499'
                WHEN distance_m <  5500      THEN '4500-5499'
                WHEN distance_m <  7000      THEN '5500-6999'
                ELSE                              '7000+' END  AS band,
           count(*)                                         AS cells,
           sum(n_results)                                   AS results,
           round((100 * avg(difficulty))::numeric, 2)       AS mean_pct,
           round((100 * stddev_samp(difficulty))::numeric, 2) AS sd_pct,
           round((100 * percentile_cont(0.99) WITHIN GROUP
                  (ORDER BY difficulty))::numeric, 2)       AS p99_pct,
           count(*) FILTER (WHERE difficulty > 0.25)        AS over_25pct
    FROM   course_difficulties
    WHERE  difficulty IS NOT NULL AND course_name LIKE %(pfx)s
    GROUP  BY 1
    ORDER  BY min(COALESCE(distance_m, -1))
"""


def _rows(cur, sql, args=None):
    cur.execute(sql, args or {})
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


def _verdict(rows):
    """Flat spread across the race buckets means a course seen once is
    trusted like a course seen fifty times."""
    if len(rows) < 3:
        return "  (not enough buckets to judge)"
    rows = [r for r in rows if not str(r[0]).startswith("0 ")]
    thin = [r for r in rows if r[0] in ("1", "2")]
    thick = [r for r in rows if r[0] in ("26-60", "61+")]
    if not thin or not thick:
        return "  (need both a thin and a thick bucket to judge)"
    sd_thin = max(float(r[3] or 0) for r in thin)
    sd_thick = max(float(r[3] or 0) for r in thick)
    if sd_thick <= 0:
        return "  (no spread at the thick end -- look at the table)"
    ratio = sd_thin / sd_thick
    out = [f"  thin (1-2 races) sd = {sd_thin:.2f}%,  "
           f"thick (26+ races) sd = {sd_thick:.2f}%,  ratio = {ratio:.2f}"]
    if ratio > 1.5:
        out.append("  => SHRINKAGE IS INVERTED. The thin end is WIDER than")
        out.append(f"     the thick end ({ratio:.1f}x). A course seen once is")
        out.append("     not merely untrusted, it is the loudest thing on")
        out.append("     the board. Nothing is pulling it in at all.")
    elif ratio > 0.9:
        out.append("  => SHRINKAGE IS NOT WORKING. A course seen once is as")
        out.append("     spread as a course seen fifty times, which is what")
        out.append("     a prior that never bites looks like from outside.")
    elif ratio > 0.6:
        out.append("  => WEAK. Some shrinkage, not enough to trust the thin")
        out.append("     end; the ping-pong between runs lives here.")
    else:
        out.append("  => shrinkage is biting: the thin end is pulled in.")
        out.append("     A big difficulty on one race is then a real")
        out.append("     outlier, not the prior failing.")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(
        description="Is course difficulty shrunk when the evidence is thin, "
                    "and does track difficulty average zero?")
    ap.add_argument("--sport", choices=["XC", "TF", "both"], default="both")
    ap.add_argument("--worst", type=int, default=30)
    ap.add_argument("--big", type=float, default=0.05,
                    help="an offender's |difficulty| floor (default 0.05 "
                         "= 5%%)")
    ap.add_argument("--thin", type=int, default=3,
                    help="an offender's race ceiling (default 3)")
    ap.add_argument("--work-mem", default="256MB")
    ap.add_argument("--timeout", default="20min")
    args = ap.parse_args()

    # ⚠ getConn IS A CONTEXT MANAGER OVER A POOLED CONNECTION, not a
    #   connection. It must be used as `with getConn() as conn`, and it
    #   dies at the FIRST cursor if you forget, which no offline check can
    #   see. tests/test_lint_getconn.py greps for it now.
    #
    # ! SET LOCAL, not SET. The connection goes back to this process's pool
    #   and a later query here would inherit the setting. (It cannot reach
    #   the site -- gunicorn is a different process with its own pool.)
    #   LOCAL keeps the tuning scoped to the work that asked for it.
    from database import getConn
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(f"SET LOCAL work_mem = '{args.work_mem}'")
        cur.execute(f"SET LOCAL statement_timeout = '{args.timeout}'")
        cur.execute("SET LOCAL max_parallel_workers_per_gather = 2")
        try:
            print("\n" + "=" * 68)
            print("1. THE LEVEL AND SPREAD PER SPORT")
            print("   mean_pct is the question 'is track difficulty 0.0?'.")
            print("=" * 68)
            cols, rows = _rows(cur, _LEVEL)
            _table(cols, rows)
            cols, rows = _rows(cur, _LEVEL_W)
            print("\n  results-weighted mean (the anchor the solver actually set):")
            _table(cols, rows)
            print("\n  A sport whose weighted mean is not within ~0.1 of zero is")
            print("  anchored somewhere nobody chose. Unweighted can differ --")
            print("  it counts a one-race course the same as Mt. SAC.")

            sports = ["XC", "TF"] if args.sport == "both" else [args.sport]
            builders = {"XC": _racesXc, "TF": _racesTf}
            for sp in sports:
                print("\n" + "=" * 68)
                print(f"4. {sp}: DIFFICULTY BY THE DISTANCE THE CELL CLAIMS")
                print("   A band whose mean and p99 run away from the rest is")
                print("   not full of hard courses -- it is full of wrong")
                print("   distances. over_25pct counts cells above +25%.")
                print("=" * 68)
                cols, rows = _rows(cur, _BY_DIST, {"pfx": sp + ":%"})
                _table(cols, rows)

                races_sql, why = builders[sp](cur)
                if races_sql is None:
                    print(f"\n  ! cannot count {sp} races: {why}")
                    print("    sections 2 and 3 skipped for this sport.")
                    continue
                t0 = time.time()
                print("\n" + "=" * 68)
                print(f"2. {sp}: DIFFICULTY SPREAD BY NUMBER OF RACES")
                print("   sd_pct should RISE from left to right if the prior is")
                print("   doing anything. Flat means it is not.")
                print("=" * 68)
                cols, rows = _rows(cur, _BUCKETS.format(races=races_sql),
                                   {"pfx": sp + ":%"})
                _table(cols, rows)
                print(_verdict(rows))
                print(f"  ({time.time() - t0:.0f}s)")

                print("\n" + "=" * 68)
                print(f"3. {sp}: BIG DIFFICULTY ON ALMOST NO EVIDENCE")
                print(f"   |difficulty| >= {100 * args.big:.0f}% on "
                      f"<= {args.thin} races.")
                print("=" * 68)
                cols, rows = _rows(cur, _WORST.format(races=races_sql),
                                   {"pfx": sp + ":%", "big": args.big,
                                    "thin": args.thin, "worst": args.worst})
                _table(cols, rows)
                print("  Every row here is a course the site is confidently")
                print("  wrong about. n_results is large only because a race has")
                print("  many finishers -- n_races is the evidence.")
        finally:
            # read-only: nothing to keep, and the rollback leaves the
            # pooled connection exactly as it was found
            conn.rollback()
            cur.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
