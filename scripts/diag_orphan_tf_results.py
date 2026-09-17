#!/usr/bin/env python3
# Project: xc-predictor
# File:    scripts/diag_orphan_tf_results.py
# Purpose: Why do 3.4M anet track results have no meets_tf row? (owner,
#          2026-09-17). Those rows have no meet name, no distance and no
#          venue, so the athlete page prints them as "Race Results" -- the
#          same symptom as the tfrrs duplicate and ~7,800 times the size.
#
#     python scripts/diag_orphan_tf_results.py
#     python scripts/diag_orphan_tf_results.py --meet 631547
#
# ★ IT NARROWS THE KEY, ONE COLUMN AT A TIME. _saveTFMeet writes the
#   meets_tf rows and the results_tf rows in ONE transaction, so they cannot
#   have diverged at save time -- a result with no geometry row means the
#   four-part key (meet_id, div_id, event_id, source) disagrees between the
#   two writers, not that a write was lost. Joining on meet_id alone, then
#   +div_id, then +event_id, then +source says WHICH column.
#
# ! NO CONCLUSION IS DRAWN HERE ON PURPOSE. Three guesses at the tfrrs
#   duplicate cost a day and nearly cost 5.3M real rows; this prints numbers
#   and the actual disagreeing rows side by side, and the reading happens
#   after.

import sys
import argparse

sys.path.insert(0, "scripts")

from database import getConn                            # noqa: E402

# Sampled, because the point is the SHAPE of the failure and results_tf is
# 191M rows. TABLESAMPLE is a page sample, so it is representative in a way
# LIMIT is not -- the LIMIT 2000000 in the last diagnostic matched 99.93 per
# cent and told us nothing, because it read one physical region of the table.
_LADDER = """
    WITH s AS (
        SELECT meet_id, div_id, event_id, source, result_id, date
        FROM   results_tf TABLESAMPLE SYSTEM (%(pct)s)
        WHERE  source = 'anet'
    )
    SELECT count(*)                                           AS sampled,
           count(*) FILTER (WHERE m1.meet_id IS NOT NULL)      AS by_meet,
           count(*) FILTER (WHERE m2.meet_id IS NOT NULL)      AS by_meet_div,
           count(*) FILTER (WHERE m3.meet_id IS NOT NULL)      AS by_meet_div_event,
           count(*) FILTER (WHERE m4.meet_id IS NOT NULL)      AS all_four
    FROM   s
    LEFT   JOIN LATERAL (SELECT 1 AS meet_id FROM meets_tf m
            WHERE m.meet_id = s.meet_id LIMIT 1) m1 ON true
    LEFT   JOIN LATERAL (SELECT 1 AS meet_id FROM meets_tf m
            WHERE m.meet_id = s.meet_id AND m.div_id = s.div_id LIMIT 1) m2 ON true
    LEFT   JOIN LATERAL (SELECT 1 AS meet_id FROM meets_tf m
            WHERE m.meet_id = s.meet_id AND m.div_id = s.div_id
              AND m.event_id = s.event_id LIMIT 1) m3 ON true
    LEFT   JOIN LATERAL (SELECT 1 AS meet_id FROM meets_tf m
            WHERE m.meet_id = s.meet_id AND m.div_id = s.div_id
              AND m.event_id = s.event_id AND m.source = s.source LIMIT 1) m4 ON true
"""

# Is a whole meet missing, or only some of its event-divs? Different bugs.
_WHOLE_OR_PART = """
    WITH orph AS (
        SELECT DISTINCT t.meet_id
        FROM   results_tf t
        WHERE  t.source = 'anet'
          AND  NOT EXISTS (SELECT 1 FROM meets_tf m
                           WHERE m.meet_id = t.meet_id AND m.div_id = t.div_id
                             AND m.event_id = t.event_id AND m.source = t.source)
    )
    SELECT (EXISTS (SELECT 1 FROM meets_tf m
                    WHERE m.meet_id = o.meet_id)) AS meet_has_any_geometry,
           count(*)                               AS meets
    FROM   orph o
    GROUP  BY 1
"""

# When the meet HAS geometry, what do its two sides actually look like?
_SIDE_BY_SIDE = """
    SELECT 'results_tf' AS side, div_id, event_id, event_short, source,
           count(*) AS n
    FROM   results_tf WHERE meet_id = %(meet)s
    GROUP  BY 1, 2, 3, 4, 5
    UNION ALL
    SELECT 'meets_tf', div_id, event_id, event_short, source, count(*)
    FROM   meets_tf WHERE meet_id = %(meet)s
    GROUP  BY 1, 2, 3, 4, 5
    ORDER  BY 2, 3, 1
"""

# A meet with orphans, to feed --meet.
_AN_EXAMPLE = """
    SELECT t.meet_id, count(*) AS orphan_rows
    FROM   results_tf t
    WHERE  t.source = 'anet'
      AND  EXISTS (SELECT 1 FROM meets_tf m WHERE m.meet_id = t.meet_id)
      AND  NOT EXISTS (SELECT 1 FROM meets_tf m
                       WHERE m.meet_id = t.meet_id AND m.div_id = t.div_id
                         AND m.event_id = t.event_id AND m.source = t.source)
    GROUP  BY 1 ORDER BY 2 DESC LIMIT 10
"""

# Are they old, or is the cause still live? This decides whether the
# rescrape reproduces it.
_BY_YEAR = """
    SELECT substring(t.date, 1, 4) AS yr, count(*) AS orphan_rows
    FROM   results_tf t
    WHERE  t.source = 'anet'
      AND  NOT EXISTS (SELECT 1 FROM meets_tf m
                       WHERE m.meet_id = t.meet_id AND m.div_id = t.div_id
                         AND m.event_id = t.event_id AND m.source = t.source)
    GROUP  BY 1 ORDER BY 1
"""


def _table(cur):
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    txt = [[("" if v is None else str(v)) for v in r] for r in rows]
    if not txt:
        print("    (none)")
        return
    w = [max(len(c), *(len(t[i]) for t in txt)) for i, c in enumerate(cols)]
    print("    " + "  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    print("    " + "  ".join("-" * w[i] for i in range(len(cols))))
    for t in txt:
        print("    " + "  ".join(t[i].ljust(w[i]) for i in range(len(cols))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pct", type=float, default=0.5,
                    help="TABLESAMPLE percent for the key ladder (default 0.5)")
    ap.add_argument("--meet", type=int,
                    help="print both sides of this meet, row for row")
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '900s'")
            if args.meet:
                print(f"  both sides of meet {args.meet}:")
                cur.execute(_SIDE_BY_SIDE, {"meet": args.meet})
                _table(cur)
                return

            print(f"  which key column fails, over a {args.pct}% page sample "
                  f"of anet track results:", flush=True)
            cur.execute(_LADDER, {"pct": args.pct})
            _table(cur)

            print()
            print("  is the whole meet missing its geometry, or only some "
                  "event-divs?", flush=True)
            cur.execute(_WHOLE_OR_PART)
            _table(cur)

            print()
            print("  orphans by year -- is the cause still live?", flush=True)
            cur.execute(_BY_YEAR)
            _table(cur)

            print()
            print("  meets that HAVE geometry and still have orphans "
                  "(feed one to --meet):", flush=True)
            cur.execute(_AN_EXAMPLE)
            _table(cur)
        conn.rollback()


if __name__ == "__main__":
    main()
