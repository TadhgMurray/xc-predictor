#!/usr/bin/env python3
"""
rating_scale_probe.py -- recreate the impossible ratings, from the columns
that produced them.

    scripts/rating_scale_probe.py                     # since 2024-08-01
    scripts/rating_scale_probe.py --since 1900-01-01  # the whole corpus
    scripts/rating_scale_probe.py --pool college_m --event 800m
    scripts/rating_scale_probe.py --rows 12345 67890  # named rows, side by side

★ WHY THIS EXISTS (owner, 2026-09-09: "I think its prolly just rating them
  and normalizing on diff scales/anchors. we should try to recreate it tho").
  It recreates it, and it needs nothing from the engine: every number below
  comes out of results_tf, so it says what the stored rows ACTUALLY did
  rather than what the code is supposed to do.

★ THE ARITHMETIC, MEASURED BEFORE THIS WAS WRITTEN. Over 38 rows spanning
  1995 to 2026, from four separate athletes and eight different events:

      rating x normalized_time = 165,645 +/- 25   (a spread of 0.03%)

  So for a pool, `rating` is EXACTLY 100 x anchor / normalized_time and
  nothing else -- no per-row term, no seasonal component, no course
  difficulty. Two things follow, and they are the reason to look here first:

    - the solve is not implicated. An impossible rating is an impossible
      normalized_time, arriving at a correct division.
    - normalized_time is therefore the only thing worth measuring, and the
      only thing this prints.

⚠ WHAT A CORRECT normalized_time LOOKS LIKE. It is a distance-NORMALISED
  time: every event mapped onto one reference distance so that a pool's
  800m runners and its 10k runners land on the same scale. So within one
  (pool, event) cell the multiplier

      x = normalized_time / time_seconds

  should be ONE number, moved only by the small weather/altitude/era
  corrections. Section 2 is a test of exactly that, and the corpus fails it:
  college_m 800m rows carry x = 3.37, x = 4.72 AND x = 14.2, which are three
  different reference distances (~2,500m, ~3,400m and ~10,000m) sharing one
  anchor. The 14.2 rows rate ~106. The 4.72 rows rate ~320.

! READ-ONLY. Every statement is a SELECT.
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

from database import getConn                                # noqa: E402


# ! ASK THE CATALOG, DO NOT GUESS. Same rule as athlete_dump: a diagnostic
#   that dies on a missing column is useless exactly when it is needed.
def _have(cur, table, wanted):
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s""",
                (table,))
    got = set()
    for r in cur.fetchall():
        got.add(r[0] if not isinstance(r, dict) else next(iter(r.values())))
    return [c for c in wanted if c in got]


def _rows(cur, sql, args=None):
    cur.execute(sql, args or {})
    return [d[0] for d in cur.description], cur.fetchall()


def _table(cols, rows, limit=None):
    if not rows:
        print("  (none)")
        return
    shown = rows[:limit] if limit else rows
    txt = [[("" if v is None else str(v)) for v in
            (r if not isinstance(r, dict) else [r[c] for c in cols])]
           for r in shown]
    w = [max(len(c), *(len(t[i]) for t in txt)) for i, c in enumerate(cols)]
    print("  " + "  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    print("  " + "  ".join("-" * w[i] for i in range(len(cols))))
    for t in txt:
        print("  " + "  ".join(t[i].ljust(w[i]) for i in range(len(cols))))
    if limit and len(rows) > limit:
        print(f"    ... {len(rows) - limit:,} more rows")


def _section(name, fn):
    print(f"\n-- {name} " + "-" * max(0, 62 - len(name)), flush=True)
    t0 = time.time()
    try:
        fn()
    except Exception as e:                                   # noqa: BLE001
        # ⚠ ONE SECTION FAILING MUST NOT TAKE THE REPORT. The whole point is
        #   to hand over output; a half report beats a traceback.
        print(f"  !! {type(e).__name__}: {e}")
    print(f"  ({time.time() - t0:.0f}s)", flush=True)


# ★ THE ANCHOR, RECOVERED FROM THE ROWS THEMSELVES rather than read out of
#   the engine. If the spread column is ~0 the rating really is 100 x anchor
#   / normalized_time for that pool, which is the claim the rest rests on.
_ANCHOR = """
    SELECT rating_pool                                            AS pool,
           count(*)                                               AS rows,
           round(percentile_disc(0.5) WITHIN GROUP
                 (ORDER BY (speed_rating * normalized_time))::numeric, 0) AS k,
           round(percentile_disc(0.02) WITHIN GROUP
                 (ORDER BY (speed_rating * normalized_time))::numeric, 0) AS k_p02,
           round(percentile_disc(0.98) WITHIN GROUP
                 (ORDER BY (speed_rating * normalized_time))::numeric, 0) AS k_p98
    FROM   results_tf
    WHERE  speed_rating IS NOT NULL AND normalized_time > 0
      AND  date >= %(since)s
    GROUP  BY 1
    ORDER  BY 2 DESC
"""

# ★ ONE CELL, ONE SCALE -- OR NOT. `off` is the population that matters: the
#   rows whose multiplier is more than 20% from their own cell's median.
#   Weather, altitude and the era curve move x by a few percent; they do not
#   move it by 200%.
_CELLS = """
    WITH x AS (
        SELECT rating_pool AS pool, {ev} AS event, result_id,
               normalized_time / time_seconds AS mult
        FROM   results_tf
        WHERE  speed_rating IS NOT NULL
          AND  normalized_time > 0 AND time_seconds > 0
          AND  date >= %(since)s
          AND  (%(pool)s::text IS NULL OR rating_pool = %(pool)s)
          AND  (%(event)s::text IS NULL OR {ev} = %(event)s)
    ),
    med AS (
        SELECT pool, event, count(*) AS n,
               percentile_disc(0.5) WITHIN GROUP (ORDER BY mult) AS m
        FROM   x GROUP BY 1, 2
    )
    SELECT m.pool, m.event, m.n AS rows,
           round(m.m::numeric, 3)                                 AS med_x,
           count(*) FILTER (WHERE abs(x.mult - m.m) > 0.20 * m.m) AS off,
           round((100.0 * count(*) FILTER (WHERE abs(x.mult - m.m) > 0.20 * m.m)
                  / m.n)::numeric, 2)                             AS off_pct,
           round(min(x.mult)::numeric, 3)                         AS min_x,
           round(max(x.mult)::numeric, 3)                         AS max_x
    FROM   med m JOIN x ON x.pool = m.pool AND x.event = m.event
    WHERE  m.n >= %(min_rows)s
    GROUP  BY 1, 2, 3, 4
    ORDER  BY 5 DESC
    LIMIT  %(lim)s
"""

# The named rows, beside their own cell's median, so the divergence is a
# number rather than an impression.
_ROWS = """
    WITH me AS (
        SELECT result_id, person_id, date, rating_pool AS pool, {ev} AS event,
               time_seconds, normalized_time, speed_rating,
               normalized_time / time_seconds AS mult
        FROM   results_tf
        WHERE  result_id = ANY(%(ids)s)
    )
    SELECT me.result_id, me.person_id, me.date, me.pool, me.event,
           round(me.time_seconds::numeric, 2)                  AS secs,
           round(me.normalized_time::numeric, 2)               AS normalized,
           round(me.speed_rating::numeric, 1)                  AS rating,
           round(me.mult::numeric, 3)                          AS x,
           round(cell.m::numeric, 3)                           AS cell_x,
           round((me.mult / cell.m)::numeric, 2)               AS x_over_cell,
           round((me.speed_rating * me.normalized_time)::numeric, 0) AS k
    FROM   me
    LEFT   JOIN LATERAL (
        SELECT percentile_disc(0.5) WITHIN GROUP
               (ORDER BY r.normalized_time / r.time_seconds) AS m
        FROM   results_tf r
        WHERE  r.rating_pool = me.pool AND {rev} = me.event
          AND  r.normalized_time > 0 AND r.time_seconds > 0
          AND  r.speed_rating IS NOT NULL
    ) cell ON TRUE
    ORDER  BY me.person_id, me.date
"""


def main():
    ap = argparse.ArgumentParser(
        description="Recreate an impossible rating from its own columns.")
    ap.add_argument("--since", default="2024-08-01",
                    help="only rows on or after this date (default the last "
                         "two academic years; 1900-01-01 for everything)")
    ap.add_argument("--pool", default=None, help="one rating_pool only")
    ap.add_argument("--event", default=None, help="one event only")
    ap.add_argument("--min-rows", type=int, default=200, metavar="N",
                    help="ignore (pool, event) cells thinner than this")
    ap.add_argument("--limit", type=int, default=30, metavar="N")
    ap.add_argument("--rows", type=int, nargs="+", default=None,
                    metavar="RESULT_ID",
                    help="show these rows beside their own cell's median")
    # ! results_tf HAS NO INDEX ON date OR rating_pool, so every section here
    #   is a full scan whatever the filters say. Same trick as the chair
    #   scan: the scan is the cost and it parallelises.
    ap.add_argument("--workers", type=int,
                    default=int(os.environ.get("XCP_PG_WORKERS", "8")),
                    metavar="N")
    args = ap.parse_args()

    with getConn() as conn, conn.cursor() as cur:
        cur.execute(f"SET max_parallel_workers_per_gather = {int(args.workers)}")
        cur.execute("SET work_mem = '256MB'")
        need = _have(cur, "results_tf",
                     ["result_id", "person_id", "date", "speed_rating",
                      "normalized_time", "time_seconds", "rating_pool"])
        missing = {"result_id", "speed_rating", "normalized_time",
                   "time_seconds", "rating_pool"} - set(need)
        if missing:
            print(f"results_tf is missing {sorted(missing)} -- nothing to "
                  f"probe")
            return
        # ! THE EVENT COLUMN IS NOT GUARANTEED. Some feeds carry it on the
        #   result, some only on the meet; without one, every row falls into
        #   a single cell and section 2 says nothing rather than lying.
        ev = "event_short" if _have(cur, "results_tf", ["event_short"]) \
            else "'(no event column)'"
        p = {"since": args.since, "pool": args.pool, "event": args.event,
             "min_rows": args.min_rows, "lim": args.limit,
             "ids": args.rows or []}

        print(f"rows on or after {args.since}"
              + (f", pool {args.pool}" if args.pool else "")
              + (f", event {args.event}" if args.event else ""))

        _section("1. the anchor: is rating just K / normalized_time?",
                 lambda: _table(*_rows(cur, _ANCHOR, p)))
        print("     k_p02 == k_p98 means the rating carries NOTHING but the "
              "anchor and\n     the normalized time -- so a wrong rating is a "
              "wrong normalized time.")

        if args.rows:
            _section("2. the named rows, beside their own cell",
                     lambda: _table(*_rows(
                         cur, _ROWS.format(ev=ev, rev=ev.replace(
                             "event_short", "r.event_short")), p)))
            return

        _section("2. one pool, one event -- how many scales?",
                 lambda: _table(*_rows(cur, _CELLS.format(ev=ev), p)))
        print("     `off` counts rows whose multiplier is >20% from their own\n"
              "     cell's median. Weather and altitude move it a few percent.\n"
              "     A cell with a nonzero `off` holds more than one reference\n"
              "     distance, which is the bug: one anchor, several scales.")


if __name__ == "__main__":
    main()
