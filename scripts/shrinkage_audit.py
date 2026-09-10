#!/usr/bin/env python3
"""
shrinkage_audit.py -- is course difficulty actually shrunk toward zero when
there is almost no evidence for it, and does track difficulty average zero?

    scripts/shrinkage_audit.py                 # both sports
    scripts/shrinkage_audit.py --sport XC
    scripts/shrinkage_audit.py --worst 60      # more offenders listed
    scripts/shrinkage_audit.py --exact         # count races from results

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

! HOW RACES ARE COUNTED. By default from `meets` alone -- one race is one
  (meet_id, div_id, source) listed at that course and distance -- which is
  an index-sized read. --exact counts only divisions that actually have
  results, which is a full pass over the results table; it moves the
  numbers very little and costs minutes, so it is not the default.

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
               ON r.course_name = substring(cd.course_name FROM 4)
              AND round(r.distance::numeric) = round(cd.distance_m::numeric)
        WHERE  cd.difficulty IS NOT NULL
          AND  cd.course_name LIKE %(pfx)s
    )
    SELECT CASE WHEN n_races <= 1  THEN '1'
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
           ON r.course_name = substring(cd.course_name FROM 4)
          AND round(r.distance::numeric) = round(cd.distance_m::numeric)
    WHERE  cd.difficulty IS NOT NULL
      AND  cd.course_name LIKE %(pfx)s
      AND  abs(cd.difficulty) >= %(big)s
      AND  COALESCE(r.n_races, 0) <= %(thin)s
    ORDER  BY abs(cd.difficulty) DESC
    LIMIT  %(worst)s
"""

# One race is one division at one meet. --exact keeps only divisions that
# actually produced results.
_RACES_FAST = {
    "XC": """SELECT course_name, distance, count(*) AS n_races
             FROM (SELECT DISTINCT meet_id, div_id, source,
                          course_name, distance
                   FROM meets WHERE course_name IS NOT NULL) d
             GROUP BY 1, 2""",
    "TF": """SELECT course_name, distance, count(*) AS n_races
             FROM (SELECT DISTINCT meet_id, div_id, source,
                          course_name, distance
                   FROM meets_tf WHERE course_name IS NOT NULL) d
             GROUP BY 1, 2""",
}
_RACES_EXACT = {
    "XC": """SELECT m.course_name, m.distance, count(*) AS n_races
             FROM (SELECT DISTINCT r.meet_id, r.div_id, r.source
                   FROM results r WHERE r.normalized_time > 0) x
             JOIN meets m ON m.meet_id = x.meet_id AND m.div_id = x.div_id
                          AND m.source = x.source
             WHERE m.course_name IS NOT NULL
             GROUP BY 1, 2""",
    "TF": """SELECT m.course_name, m.distance, count(*) AS n_races
             FROM (SELECT DISTINCT r.meet_id, r.div_id, r.source
                   FROM results_tf r WHERE r.normalized_time > 0) x
             JOIN meets_tf m ON m.meet_id = x.meet_id AND m.div_id = x.div_id
                             AND m.source = x.source
             WHERE m.course_name IS NOT NULL
             GROUP BY 1, 2""",
}


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
    ap.add_argument("--exact", action="store_true",
                    help="count races from results rather than meets "
                         "(a full pass; minutes, not seconds)")
    ap.add_argument("--worst", type=int, default=30)
    ap.add_argument("--big", type=float, default=0.05,
                    help="an offender's |difficulty| floor (default 0.05 "
                         "= 5%%)")
    ap.add_argument("--thin", type=int, default=3,
                    help="an offender's race ceiling (default 3)")
    ap.add_argument("--work-mem", default="256MB")
    ap.add_argument("--timeout", default="20min")
    args = ap.parse_args()

    from database import getConn
    conn = getConn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"SET work_mem = '{args.work_mem}'")
    cur.execute(f"SET statement_timeout = '{args.timeout}'")
    cur.execute("SET max_parallel_workers_per_gather = 2")

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
        src = _RACES_EXACT if args.exact else _RACES_FAST
        for sp in sports:
            t0 = time.time()
            print("\n" + "=" * 68)
            print(f"2. {sp}: DIFFICULTY SPREAD BY NUMBER OF RACES")
            print("   sd_pct should RISE from left to right if the prior is")
            print("   doing anything. Flat means it is not.")
            print("=" * 68)
            cols, rows = _rows(cur, _BUCKETS.format(races=src[sp]),
                               {"pfx": sp + ":%"})
            _table(cols, rows)
            print(_verdict(rows))
            print(f"  ({time.time() - t0:.0f}s)")

            print("\n" + "=" * 68)
            print(f"3. {sp}: BIG DIFFICULTY ON ALMOST NO EVIDENCE")
            print(f"   |difficulty| >= {100 * args.big:.0f}% on "
                  f"<= {args.thin} races.")
            print("=" * 68)
            cols, rows = _rows(cur, _WORST.format(races=src[sp]),
                               {"pfx": sp + ":%", "big": args.big,
                                "thin": args.thin, "worst": args.worst})
            _table(cols, rows)
            print("  Every row here is a course the site is confidently")
            print("  wrong about. n_results is large only because a race has")
            print("  many finishers -- n_races is the evidence.")
    finally:
        cur.close()
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
