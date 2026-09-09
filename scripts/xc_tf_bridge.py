#!/usr/bin/env python3
"""
xc_tf_bridge.py -- "a 145 in XC is a 4:00 mile, in November".

    scripts/xc_tf_bridge.py                     # the table, hs_m
    scripts/xc_tf_bridge.py --pool college_m
    scripts/xc_tf_bridge.py --sample 5          # more data, slower

★ THE QUESTION THE ENGINE HAS BEEN ASKING IS NOT THE QUESTION (2026-09-09).
  Owner: "there must just be some way to say that this is a 145 in xc, and it
  correlates to a 4:00 mile fitness wise at that moment."

  There is, and it is easier than what we have been doing -- because it is a
  DIFFERENT question.

    what the solve asks   one ability per athlete, plus a sport offset:
                          "how good are they, and how much do they
                          specialise?"

    what you are asking   a MAPPING: "an XC rating of R at this time of year
                          goes with what track performance?"

★ AND TODAY'S RESULT SAYS THE FIRST ONE HAS NO ANSWER. Three arms, measured:

      XC - indoor    -0.03189      (XC-out) - (XC-in)  +0.00633
      XC - outdoor   -0.02556      indoor  - outdoor   -0.00807
      in  - out      -0.00807      CLOSURE ERROR       +0.01440

  Those two lines are the same quantity written two ways. They disagree by
  more than the indoor/outdoor gap itself, at 20-90 SE. If each sport had ONE
  level they would be equal. So there is no single XC/TF offset to find, and
  every hour spent looking for one was spent looking for something that is
  not there.

★ BUT THE MAPPING EXISTS, AND IT IS DIRECTLY MEASURABLE. The closure error
  is not noise -- it is the mapping VARYING WITH THE CALENDAR, which is
  exactly what an athlete experiences. So stop trying to remove time by
  interpolation to recover a constant, and keep time as a variable:

      XC 145 in November  ->  a 4:01 mile
      XC 145 in March     ->  a 3:58 mile

  Two ratings and one conversion, rather than one blended scale. That is
  strictly MORE than the merged scale says, and it is honest about the part
  that moves.

! THIS IS A MEASUREMENT. It writes nothing and changes no rating. It exists
  to show the mapping is there and has a shape, so the engine can be pointed
  at it.

⚠ CHEAP BY CONSTRUCTION, after the last two were not. Two aggregate passes
  over ranking_results and one over results_tf, all sampled by person, all
  hash joins, no per-row lookups anywhere.
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

RATING_LO, RATING_HI = 40.0, 260.0

# ★ THE MONTH IS THE "MOMENT". Finer than a season, coarse enough that an
#   athlete has more than one race in it. Numbered from August so the
#   academic year is contiguous and the winter does not wrap.
_MONTH = ("(EXTRACT(month FROM {d})::int + 4) %% 12")

_XC = """
    DROP TABLE IF EXISTS br_xc;
    CREATE TEMP TABLE br_xc AS
        SELECT person_id, pool, year,
               {m}                              AS am,
               avg(ln(speed_rating))            AS lxc,
               count(*)                         AS n
        FROM   ranking_results
        WHERE  sport = 'XC' AND person_id IS NOT NULL
          AND  speed_rating BETWEEN %(lo)s AND %(hi)s
          AND  pool = %(pool)s
          AND  (person_id %% %(mod)s) = 0
        GROUP  BY 1, 2, 3, 4;
    CREATE INDEX ON br_xc (person_id, year);
    ANALYZE br_xc;
""".format(m=_MONTH.format(d="race_date"))

_TF = """
    DROP TABLE IF EXISTS br_tf;
    CREATE TEMP TABLE br_tf AS
        SELECT person_id, pool, year,
               {m}                              AS am,
               avg(ln(speed_rating))            AS ltf,
               count(*)                         AS n
        FROM   ranking_results
        WHERE  sport = 'TF' AND person_id IS NOT NULL
          AND  speed_rating BETWEEN %(lo)s AND %(hi)s
          AND  pool = %(pool)s
          AND  (person_id %% %(mod)s) = 0
        GROUP  BY 1, 2, 3, 4;
    CREATE INDEX ON br_tf (person_id, year);
    ANALYZE br_tf;
"""

# ! THE SAME ACADEMIC YEAR, AND THE PAIR IS (XC month, TF month). No
#   interpolation and no bracketing: the point is to KEEP the calendar, not
#   to cancel it.
_PAIRS = """
    DROP TABLE IF EXISTS br_pair;
    CREATE TEMP TABLE br_pair AS
        SELECT x.person_id, x.pool, x.year,
               x.am AS xc_month, t.am AS tf_month,
               exp(x.lxc)                       AS xc,
               t.ltf - x.lxc                    AS d,
               t.am - x.am                      AS months_apart
        FROM   br_xc x
        JOIN   br_tf t ON t.person_id = x.person_id
                      AND t.year = x.year AND t.pool = x.pool
        WHERE  t.am > x.am;
    ANALYZE br_pair;
"""

_BY_MONTH = """
    SELECT tf_month,
           count(*)                                                  AS pairs,
           round(percentile_cont(0.5) WITHIN GROUP (ORDER BY d)::numeric, 4)
                                                                     AS d_log,
           round((100 * (exp(percentile_cont(0.5) WITHIN GROUP (ORDER BY d))
                         - 1))::numeric, 2)                          AS pct,
           round(avg(months_apart)::numeric, 1)                      AS mo_apart
    FROM   br_pair
    GROUP  BY 1 HAVING count(*) >= 200 ORDER BY 1
"""

# ⚠ AND BY LEVEL, because a constant ratio is an assumption and this is the
#   place to find out. If d_log drifts down this table the conversion is not
#   one number even within a month.
_BY_LEVEL = """
    SELECT width_bucket(xc, 90, 170, 8) * 10 + 80                    AS xc_band,
           count(*)                                                  AS pairs,
           round(percentile_cont(0.5) WITHIN GROUP (ORDER BY d)::numeric, 4)
                                                                     AS d_log,
           round((100 * (exp(percentile_cont(0.5) WITHIN GROUP (ORDER BY d))
                         - 1))::numeric, 2)                          AS pct
    FROM   br_pair
    GROUP  BY 1 HAVING count(*) >= 200 ORDER BY 1
"""

# ★ THE OTHER HALF: what a TF rating actually IS, in seconds. Straight off
#   results_tf -- speed_rating, event_short and time_seconds are all on the
#   row, so this needs no join at all.
_MILE = """
    SELECT round(speed_rating / 5) * 5                               AS tf_band,
           count(*)                                                  AS races,
           round(percentile_cont(0.5) WITHIN GROUP
                 (ORDER BY time_seconds)::numeric, 1)                AS secs,
           to_char((percentile_cont(0.5) WITHIN GROUP
                    (ORDER BY time_seconds)) * interval '1 second',
                   'MI:SS.MS')                                       AS mile
    FROM   results_tf
    WHERE  speed_rating BETWEEN %(lo)s AND %(hi)s
      AND  time_seconds BETWEEN 200 AND 400
      AND  rating_pool = %(pool)s
      AND  (person_id %% %(mod)s) = 0
      -- ! SINGLE QUOTES, AND THE APOSTROPHE DOUBLED. The first version
      --   wrote "Men's Mile" with double quotes, which Postgres reads as a
      --   COLUMN NAME: 'column "Men's Mile" does not exist'.
      AND  event_short IN ('1mile', '1600m', 'Mile', '1 Mile',
                           'Men''s Mile', 'Men''s 1600 Meters',
                           'Mile Run', '1600 Meters')
    GROUP  BY 1 HAVING count(*) >= 50 ORDER BY 1
"""


def _rows(cur, sql, args=None):
    cur.execute(sql, args or {})
    return [d[0] for d in cur.description], cur.fetchall()


def _table(cols, rows):
    if not rows:
        print("  (none)")
        return
    txt = [[("" if v is None else str(v)) for v in r] for r in rows]
    w = [max(len(c), *(len(t[i]) for t in txt)) for i, c in enumerate(cols)]
    print("  " + "  ".join(c.rjust(w[i]) for i, c in enumerate(cols)))
    print("  " + "  ".join("-" * w[i] for i in range(len(cols))))
    for t in txt:
        print("  " + "  ".join(t[i].rjust(w[i]) for i in range(len(cols))))


_MONTHS = ("Aug", "Sep", "Oct", "Nov", "Dec", "Jan",
           "Feb", "Mar", "Apr", "May", "Jun", "Jul")


def main():
    ap = argparse.ArgumentParser(
        description="What an XC rating is worth on the track, by month.")
    ap.add_argument("--pool", default="hs_m")
    ap.add_argument("--sample", type=int, default=20, metavar="N",
                    help="every Nth person (default 20)")
    args = ap.parse_args()
    p = {"lo": RATING_LO, "hi": RATING_HI, "pool": args.pool,
         "mod": args.sample}

    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL work_mem = '256MB'")
        cur.execute("SET LOCAL max_parallel_workers_per_gather = 8")
        print(f"\n  pool {args.pool}, every {args.sample}th person", flush=True)
        t0 = time.time()
        cur.execute(_XC, p)
        cur.execute(_TF.format(m=_MONTH.format(d="race_date")), p)
        cur.execute(_PAIRS)
        cur.execute("SELECT count(*) FROM br_pair")
        n = cur.fetchone()[0]
        print(f"  {n:,} (XC month, TF month) pairs in the same season  "
              f"({time.time() - t0:.0f}s)\n", flush=True)
        if not n:
            print("  nothing to measure -- try --sample 1 or another pool")
            conn.rollback()
            return 1

        print("-- 1. TF rating minus XC rating, by the month of the track "
              "race ---")
        cols, rows = _rows(cur, _BY_MONTH)
        rows = [(_MONTHS[r[0]],) + tuple(r[1:]) for r in rows]
        _table(("month",) + tuple(cols[1:]), rows)
        print("     `pct` is how much HIGHER the same athlete rates on the "
              "track that\n     month than in cross country that season. It "
              "moving down the column IS\n     the thing no single offset "
              "could ever capture.")

        print("\n-- 2. and by level, because a constant ratio is an "
              "assumption ------")
        _table(*_rows(cur, _BY_LEVEL))

        print("\n-- 3. what a TF rating is, in seconds (mile / 1600) -------"
              "-------")
        _table(*_rows(cur, _MILE, p))
        print("     Compose 1 and 3: an XC rating R in month M is a TF "
              "rating of\n     R * (1 + pct/100), and that TF rating is the "
              "mile time on this table.")
        conn.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
