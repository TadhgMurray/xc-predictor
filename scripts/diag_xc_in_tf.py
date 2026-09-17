#!/usr/bin/env python3
# Project: xc-predictor
# File:    scripts/diag_xc_in_tf.py
# Purpose: Find out WHY a cross country race shows up a second time in the
#          track half of an athlete page, nameless -- "Race Results" is not
#          scraped data, it is racecast/templates/athlete.html's fallback
#          for a race whose meet has no name.
#
#     python scripts/diag_xc_in_tf.py                 # corpus-wide counts
#     python scripts/diag_xc_in_tf.py --person 12345  # one athlete, in full
#
# ! IT MEASURES BEFORE IT ACCUSES. The first guess at this bug -- that the
#   TFRRS driver was parsing cross country pages as track -- was wrong, and
#   the purge written for it found nothing, because no TFRRS meet_id is in
#   both meets and meets_tf. This asks the database what the duplicate rows
#   actually ARE instead of assuming.

import sys
import argparse

sys.path.insert(0, "scripts")

from database import getConn                            # noqa: E402


# A track row and a cross country row for the same person, the same day and
# the same time to a hundredth, is the same race stored twice.
_DUPES = """
    SELECT t.source          AS tf_source,
           x.source          AS xc_source,
           (m.meet_id IS NULL)        AS no_meets_tf_row,
           (NULLIF(btrim(COALESCE(m.meet_name, '')), '') IS NULL) AS no_meet_name,
           t.event_short,
           count(*)          AS n
    FROM   results_tf t
    JOIN   results    x
           ON  x.person_id = t.person_id
           AND x.date      = t.date
           AND abs(x.time_seconds - t.time_seconds) < 0.01
    LEFT   JOIN meets_tf m
           ON  m.meet_id  = t.meet_id
           AND m.div_id   = t.div_id
           AND m.event_id = t.event_id
           AND m.source   = t.source
    WHERE  t.time_seconds IS NOT NULL
      AND  x.time_seconds IS NOT NULL
      AND  t.person_id IS NOT NULL
    GROUP  BY 1, 2, 3, 4, 5
    ORDER  BY n DESC
    LIMIT  40
"""

# The nameless track rows on their own, whether or not they duplicate
# anything: this is what the page prints as "Race Results".
_NAMELESS = """
    SELECT t.source,
           (m.meet_id IS NULL) AS no_meets_tf_row,
           count(*)            AS n,
           count(DISTINCT t.meet_id) AS meets
    FROM   results_tf t
    LEFT   JOIN meets_tf m
           ON  m.meet_id  = t.meet_id
           AND m.div_id   = t.div_id
           AND m.event_id = t.event_id
           AND m.source   = t.source
    LEFT   JOIN meets_tf_meta mt ON mt.meet_id = t.meet_id
    WHERE  NULLIF(btrim(COALESCE(m.meet_name, '')), '') IS NULL
      AND  NULLIF(btrim(COALESCE(mt.meet_name, '')), '') IS NULL
    GROUP  BY 1, 2
    ORDER  BY n DESC
    LIMIT  20
"""

# ⚠ meets_tfrrs ON THE XC SIDE TOO, and leaving it out made this
#   diagnostic lie: every tfrrs cross country row came back with a blank
#   meet name, which looked like the site's bug and was this query's. `meets`
#   is anet-only; app.py joins meets_tfrrs for a tfrrs row (_tfrrs_join) and
#   so must anything claiming to show what the page shows.
_PERSON = """
    SELECT 'XC' AS sport, r.date, r.meet_id, r.div_id,
           NULL::bigint AS event_id, NULL::text AS event_short,
           r.time_seconds, r.source,
           COALESCE(m.meet_name, mx.meet_name) AS meet_name
    FROM   results r
    LEFT   JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
    LEFT   JOIN meets_tfrrs mx
           ON  r.source = 'tfrrs' AND mx.meet_id = r.meet_id
           AND mx.sport = 'XC'
    WHERE  r.person_id = %(pid)s
    UNION ALL
    SELECT 'TF', t.date, t.meet_id, t.div_id, t.event_id, t.event_short,
           t.time_seconds, t.source,
           COALESCE(NULLIF(btrim(m.meet_name), ''), mt.meet_name)
    FROM   results_tf t
    LEFT   JOIN meets_tf m
           ON  m.meet_id = t.meet_id AND m.div_id = t.div_id
           AND m.event_id = t.event_id AND m.source = t.source
    LEFT   JOIN meets_tf_meta mt ON mt.meet_id = t.meet_id
    WHERE  t.person_id = %(pid)s
    ORDER  BY 2, 1
"""


# ★ IS THE 3.4M REALLY MISSING, OR IS IT THE source CONDITION? The
#   nameless count above joins meets_tf on (meet_id, div_id, event_id,
#   source), and meets_tf.source was added to that table LATER -- every row
#   written before it is NULL, which fails the join and counts as "no
#   meets_tf row" whether or not one exists. This measures the join both
#   ways so the number means something.
_JOIN_STRICTNESS = """
    WITH s AS (SELECT meet_id, div_id, event_id, source FROM results_tf
               WHERE source = 'anet' LIMIT 2000000)
    SELECT count(*)                                  AS sampled,
           count(m4.meet_id)                         AS matched_with_source,
           count(m3.meet_id)                         AS matched_without_source
    FROM   s
    LEFT   JOIN meets_tf m4
           ON  m4.meet_id = s.meet_id AND m4.div_id = s.div_id
           AND m4.event_id = s.event_id AND m4.source = s.source
    LEFT   JOIN meets_tf m3
           ON  m3.meet_id = s.meet_id AND m3.div_id = s.div_id
           AND m3.event_id = s.event_id
"""

_MEETS_TF_SOURCE = """
    SELECT source, count(*) AS n FROM meets_tf GROUP BY source ORDER BY n DESC
"""

# The anet-on-anet duplicates, in full: one person, one day, one time, in
# both tables. A 1600m cannot also be a cross country race, so one of the
# two rows is in the wrong table -- these print the pair so we can see
# WHICH, and what meet each side thinks it was.
_ANET_PAIRS = """
    SELECT x.meet_id   AS xc_meet, x.div_id AS xc_div, mx.meet_name AS xc_name,
           t.meet_id   AS tf_meet, t.div_id AS tf_div, t.event_id,
           t.event_short, mt.meet_name     AS tf_name,
           x.date, x.time_seconds
    FROM   results_tf t
    JOIN   results    x
           ON  x.person_id = t.person_id
           AND x.date      = t.date
           AND abs(x.time_seconds - t.time_seconds) < 0.01
    LEFT   JOIN meets mx
           ON  mx.div_id = x.div_id AND mx.source = x.source
    LEFT   JOIN meets_tf mt
           ON  mt.meet_id = t.meet_id AND mt.div_id = t.div_id
           AND mt.event_id = t.event_id
    WHERE  t.source = 'anet' AND x.source = 'anet'
      AND  t.time_seconds IS NOT NULL AND x.time_seconds IS NOT NULL
      AND  t.person_id IS NOT NULL
    ORDER  BY x.date DESC
    LIMIT  25
"""


def _table(cur, rows):
    cols = [d[0] for d in cur.description]
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
    ap.add_argument("--person", type=int,
                    help="one person_id: every race, both sports, in full")
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor() as cur:
            if args.person:
                print(f"  every stored race for person {args.person}:")
                cur.execute(_PERSON, {"pid": args.person})
                _table(cur, cur.fetchall())
                return

            print("  track rows that are the SAME RACE as a cross country row "
                  "(same person, same day, same time):", flush=True)
            cur.execute(_DUPES)
            _table(cur, cur.fetchall())

            print()
            print("  track rows with no meet name anywhere -- what the athlete "
                  "page prints as \"Race Results\":", flush=True)
            cur.execute(_NAMELESS)
            _table(cur, cur.fetchall())

            print()
            print("  meets_tf rows by source (a NULL source fails the join "
                  "and fakes a missing row):", flush=True)
            cur.execute(_MEETS_TF_SOURCE)
            _table(cur, cur.fetchall())

            print()
            print("  the same join with and without the source condition, "
                  "over 2M anet rows:", flush=True)
            cur.execute(_JOIN_STRICTNESS)
            _table(cur, cur.fetchall())

            print()
            print("  anet-on-anet pairs in full -- a 1600m cannot be a cross "
                  "country race, so one side is in the wrong table:", flush=True)
            cur.execute(_ANET_PAIRS)
            _table(cur, cur.fetchall())
        conn.rollback()


if __name__ == "__main__":
    main()
