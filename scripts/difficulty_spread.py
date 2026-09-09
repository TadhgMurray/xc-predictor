#!/usr/bin/env python3
"""
difficulty_spread.py -- is the XC course difficulty compressed, and is it a
California artefact?

    scripts/difficulty_spread.py                  # the whole picture
    scripts/difficulty_spread.py --state CA       # one state's courses
    scripts/difficulty_spread.py --top 40         # the biggest courses

Run from the PROJECT ROOT. READ-ONLY: every statement is a SELECT, and the
one temp table is dropped with the transaction. It reads
course_difficulties (thousands of rows) plus ONE pass over meets to attach a
state. Seconds, not minutes.

★ THE SYMPTOM. Lex Young's dump, all six of his California courses:

      Woodbridge 0.1%   Clovis 0.2%   CIF State 0.2%
      Marmonte 1.7%     CIF-SS 5.8%   NXN 5.9%

  A flat 5k in September and a hilly one in November do not differ by a
  fifth of one percent. The three with the LARGEST fields read as almost
  exactly average, which is what a compressed scale looks like from inside.

★ THE TWO EXPLANATIONS THIS SEPARATES, and they need different fixes:

    SHRINKAGE   a course with few results is pulled toward zero by the
                prior, so the spread should GROW with n_results. Section 2
                is that test: if the sd of difficulty rises steadily across
                the n_results buckets, small courses are being shrunk and
                the big ones are fine.

    A FLAT SCALE  every course is near zero however much data it has. Then
                the spread does NOT grow with n_results, and the problem is
                the scale itself rather than the evidence behind any one
                course.

★ AND THE CALIFORNIA QUESTION (owner, 2026-09-09: "look at if it's a CA
  thing as well"). California is the densest, most inter-raced state in the
  corpus, so its courses are the best connected -- which cuts both ways:

    - if CA's spread is WIDER than everyone's, the estimator works where the
      data is dense and the rest of the country is being shrunk;
    - if CA's spread is NARROWER, the big CA courses are effectively
      defining zero and the compression starts there.

  Sections 3 and 4 measure it either way rather than assuming one.

! DIFFICULTY IS A FRACTION HERE and printed as a percent: the athlete page
  shows difficulty * 100, so a 0.058 in the table is the 5.8% above.
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

from database import getConn                                    # noqa: E402


def _have(cur, table, wanted):
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s""",
                (table,))
    got = {r[0] if not isinstance(r, dict) else next(iter(r.values()))
           for r in cur.fetchall()}
    return [c for c in wanted if c in got]


def _rows(cur, sql, args=None):
    cur.execute(sql, args or {})
    return [d[0] for d in cur.description], cur.fetchall()


def _table(cols, rows):
    if not rows:
        print("  (none)")
        return
    txt = [[("" if v is None else
             (f"{v:,.2f}" if isinstance(v, float) else str(v)))
            for v in r] for r in rows]
    w = [max(len(c), *(len(t[i]) for t in txt)) for i, c in enumerate(cols)]
    print("  " + "  ".join(c.rjust(w[i]) for i, c in enumerate(cols)))
    print("  " + "  ".join("-" * w[i] for i in range(len(cols))))
    for t in txt:
        print("  " + "  ".join(t[i].rjust(w[i]) for i in range(len(cols))))


def _section(name, fn):
    print(f"\n-- {name} " + "-" * max(0, 64 - len(name)), flush=True)
    t0 = time.time()
    try:
        fn()
    except Exception as e:                                       # noqa: BLE001
        print(f"  !! {type(e).__name__}: {e}")
    print(f"  ({time.time() - t0:.1f}s)", flush=True)


# ★ ONE PASS OVER meets, AND THE ONLY EXPENSIVE THING HERE. meets is one row
#   per DIVISION, so a course appears once per division of every meet it has
#   hosted; the aggregate collapses that to one row per course. mode() rather
#   than max() because a course whose state was mis-scraped once should not
#   be relabelled by the mistake.
_STATE_MAP = """
    DROP TABLE IF EXISTS cd_state;
    CREATE TEMP TABLE cd_state AS
        SELECT course_name,
               mode() WITHIN GROUP (ORDER BY upper(btrim(state))) AS state,
               count(*) AS n_div
        FROM   meets
        WHERE  course_name IS NOT NULL AND btrim(coalesce(state,'')) <> ''
        GROUP  BY course_name;
    CREATE INDEX ON cd_state (course_name);
    ANALYZE cd_state;
"""

# ! THE 'XC:' PREFIX. course_difficulties is keyed "<SPORT>:<course>", so the
#   join has to strip it -- athlete_dump builds the same key from the other
#   side. split_part is exact where a regexp would have to guess at colons
#   inside a course name.
_JOINED = """
    DROP TABLE IF EXISTS cd_all;
    CREATE TEMP TABLE cd_all AS
        SELECT d.course_name                                   AS key,
               CASE WHEN d.course_name LIKE '%%:%%'
                    THEN substring(d.course_name from position(':' in d.course_name) + 1)
                    ELSE d.course_name END                     AS course,
               CASE WHEN d.course_name LIKE '%%:%%'
                    THEN split_part(d.course_name, ':', 1)
                    ELSE '?' END                               AS sport,
               d.difficulty::float8 * 100.0                    AS pct,
               COALESCE(d.n_results, 0)                        AS n_results,
               s.state
        FROM   course_difficulties d
        LEFT   JOIN cd_state s
               ON s.course_name = CASE WHEN d.course_name LIKE '%%:%%'
                    THEN substring(d.course_name from position(':' in d.course_name) + 1)
                    ELSE d.course_name END
        WHERE  d.difficulty IS NOT NULL;
    ANALYZE cd_all;
"""

_SHAPE = """
    SELECT sport,
           count(*)                                                 AS courses,
           sum(n_results)                                           AS results,
           round(avg(pct)::numeric, 2)                              AS mean,
           round(stddev_samp(pct)::numeric, 2)                      AS sd,
           round(percentile_cont(0.05) WITHIN GROUP (ORDER BY pct)::numeric, 2) AS p05,
           round(percentile_cont(0.50) WITHIN GROUP (ORDER BY pct)::numeric, 2) AS p50,
           round(percentile_cont(0.95) WITHIN GROUP (ORDER BY pct)::numeric, 2) AS p95,
           round(min(pct)::numeric, 2)                              AS min,
           round(max(pct)::numeric, 2)                              AS max,
           count(*) FILTER (WHERE abs(pct) < 1.0)                   AS within_1pc,
           count(*) FILTER (WHERE state IS NULL)                    AS no_state
    FROM   cd_all
    GROUP  BY sport
    ORDER  BY 3 DESC NULLS LAST
"""

# ★ THE SHRINKAGE TEST. If a prior is pulling thin courses to zero, sd rises
#   monotonically down this table and the last bucket is the honest one. If
#   sd is flat, the scale itself is compressed and more data will not fix it.
_BY_N = """
    SELECT CASE WHEN n_results <      50 THEN '1. <50'
                WHEN n_results <     200 THEN '2. 50-199'
                WHEN n_results <   1000 THEN '3. 200-999'
                WHEN n_results <   5000 THEN '4. 1k-5k'
                WHEN n_results <  20000 THEN '5. 5k-20k'
                ELSE                          '6. 20k+' END          AS bucket,
           count(*)                                                  AS courses,
           round(avg(pct)::numeric, 2)                               AS mean,
           round(stddev_samp(pct)::numeric, 2)                       AS sd,
           round(percentile_cont(0.05) WITHIN GROUP (ORDER BY pct)::numeric, 2) AS p05,
           round(percentile_cont(0.95) WITHIN GROUP (ORDER BY pct)::numeric, 2) AS p95
    FROM   cd_all
    WHERE  sport = %(sport)s
    GROUP  BY 1 ORDER BY 1
"""

_BY_STATE = """
    SELECT state,
           count(*)                                                  AS courses,
           sum(n_results)                                            AS results,
           round(avg(pct)::numeric, 2)                               AS mean,
           round(stddev_samp(pct)::numeric, 2)                       AS sd,
           round(percentile_cont(0.05) WITHIN GROUP (ORDER BY pct)::numeric, 2) AS p05,
           round(percentile_cont(0.95) WITHIN GROUP (ORDER BY pct)::numeric, 2) AS p95,
           round((percentile_cont(0.95) WITHIN GROUP (ORDER BY pct)
                - percentile_cont(0.05) WITHIN GROUP (ORDER BY pct))::numeric,
                 2)                                                  AS span
    FROM   cd_all
    WHERE  sport = %(sport)s AND state IS NOT NULL
    GROUP  BY state
    HAVING count(*) >= %(min_courses)s
    ORDER  BY 3 DESC NULLS LAST
    LIMIT  %(lim)s
"""

# ⚠ WEIGHTED BY RESULTS, NOT BY COURSE. California has many small courses and
#   a handful of enormous ones; an unweighted sd answers "what does a random
#   COURSE look like" when the question is "what does a random RACE look
#   like".
_CA = """
    SELECT CASE WHEN state = 'CA' THEN 'California' ELSE 'everywhere else' END
                                                                     AS side,
           count(*)                                                  AS courses,
           sum(n_results)                                            AS results,
           round(avg(pct)::numeric, 2)                               AS mean,
           round(stddev_samp(pct)::numeric, 2)                       AS sd,
           round((sum(n_results * pct) / NULLIF(sum(n_results), 0))::numeric, 2)
                                                                     AS mean_wtd,
           round(percentile_cont(0.05) WITHIN GROUP (ORDER BY pct)::numeric, 2) AS p05,
           round(percentile_cont(0.95) WITHIN GROUP (ORDER BY pct)::numeric, 2) AS p95
    FROM   cd_all
    WHERE  sport = %(sport)s AND state IS NOT NULL
    GROUP  BY 1 ORDER BY 3 DESC
"""

# ★ THE SYMPTOM ITSELF: the courses with the MOST evidence. If these are the
#   ones sitting closest to zero, the scale is anchored on them.
_BIGGEST = """
    SELECT left(course, 42) AS course, state, n_results,
           round(pct::numeric, 2) AS pct
    FROM   cd_all
    WHERE  sport = %(sport)s AND (%(state)s::text IS NULL OR state = %(state)s)
    ORDER  BY n_results DESC NULLS LAST
    LIMIT  %(lim)s
"""


def main():
    ap = argparse.ArgumentParser(
        description="Is the XC course difficulty compressed, and is it CA?")
    ap.add_argument("--sport", default="XC")
    ap.add_argument("--state", default=None, help="restrict section 5")
    ap.add_argument("--top", type=int, default=25, metavar="N")
    ap.add_argument("--min-courses", type=int, default=15, metavar="N",
                    help="ignore states with fewer courses than this")
    ap.add_argument("--states", type=int, default=25, metavar="N")
    args = ap.parse_args()
    p = {"sport": args.sport, "state": args.state, "lim": args.top,
         "min_courses": args.min_courses}

    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL work_mem = '128MB'")
        cur.execute("SET LOCAL max_parallel_workers_per_gather = 8")
        need = _have(cur, "course_difficulties",
                     ["course_name", "difficulty", "n_results"])
        if "difficulty" not in need:
            print("course_difficulties has no difficulty column.")
            return 1
        t0 = time.time()
        print("  attaching a state to each course (one pass over meets)...",
              flush=True)
        cur.execute(_STATE_MAP)
        cur.execute(_JOINED)
        cur.execute("SELECT count(*) FROM cd_all")
        print(f"    {cur.fetchone()[0]:,} courses  "
              f"({time.time() - t0:.1f}s)", flush=True)

        _section("1. the shape of the table, per sport",
                 lambda: _table(*_rows(cur, _SHAPE)))
        print("     `within_1pc` is how many courses sit inside +-1%. A big "
              "share of that\n     is what compression looks like.")

        _section("2. does the spread grow with evidence? (the shrinkage test)",
                 lambda: _table(*_rows(cur, _BY_N, p)))
        print("     sd RISING down the table -> thin courses are shrunk to "
              "zero and the\n     big ones are honest. sd FLAT -> the scale "
              "itself is compressed.")

        _section(f"3. by state ({args.sport})",
                 lambda: _table(*_rows(cur, _BY_STATE,
                                       {**p, "lim": args.states})))
        print("     `span` is p95 - p05, the honest width of that state's "
              "difficulty scale.")

        _section("4. California against everywhere else",
                 lambda: _table(*_rows(cur, _CA, p)))
        print("     WIDER in CA -> the estimator works where the data is "
              "dense and the rest\n     of the country is being shrunk. "
              "NARROWER -> the big CA courses are\n     defining zero.")

        _section(f"5. the {args.top} courses with the most results",
                 lambda: _table(*_rows(cur, _BIGGEST, p)))
        print("     If these cluster near 0.00 the scale is anchored on "
              "them.")
        conn.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
