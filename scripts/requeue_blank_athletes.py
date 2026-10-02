#!/usr/bin/env python3
"""
requeue_blank_athletes.py -- find the anet meets whose results show no name,
and put them back on the scrape queue so a re-scrape fills the names in.

    /srv/venv/bin/python scripts/requeue_blank_athletes.py              # DRY RUN
    /srv/venv/bin/python scripts/requeue_blank_athletes.py --since 2025 # only recent
    /srv/venv/bin/python scripts/requeue_blank_athletes.py --apply      # requeue

★ WHY (owner, 2026-09-25: a Monte Vista v Cal dual meet listing every
  freshman as "Unknown", each rated ~25 points above a named runner with the
  same time). The cross country save path writes an athletes row only through
  saveResultsBulk's foreign-key placeholder, and that placeholder carried
  ("", "", "") for first name, last name and gender. Every athlete first seen
  in a cross country meet got no name (pages say Unknown) and no gender (the
  engine pools them in the unknown-gender pool, whose different mean is the
  inflated rating). The saver now writes the result's own FirstName /
  LastName / Gender and fills a blank row on conflict -- so a RE-SCRAPE of a
  meet repairs every blank athlete in it. This finds those meets and marks
  them unscraped (meet_queue.scraped = 0), which the scraper claims next.

★ EVERY CAUSE, NOT ONLY THE BLANK ROW (owner, 2026-10-02: "I can promise you
  athletic net has the names"). The first version found athletes whose rows
  were all blank; scripts/unknown_names.py found more nameless results than
  that: a result whose person has NO athletes row at all, and one with no
  person. The test here is the site's own -- a row is nameless when neither
  `athletes` (by person or athlete id) nor the row's athlete_name has a
  name -- and a re-scrape repairs every kind: the athletes write fills or
  inserts the name, the results upsert fills a missing athlete_id and
  person_id (COALESCE: a person dedup wrote is never replaced), and the
  feed's own name is now kept on the result row (database._feedName) -- the
  only fix for a row the feed never tied to a registered athlete (2.6M
  track rows with no person: their names were dropped at save time).

! TRACK RELAYS ARE LEFT OUT: a relay row is a team, stored with no athlete
  on purpose; re-scraping cannot give it a name. The race page names the
  squad and its runners.

! A meet with no meet_queue row cannot be requeued; those are counted and
  --apply adds them to the queue (source anet, unscraped).

READ-ONLY without --apply.
"""
import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from database import getConn                                  # noqa: E402

SEASON = ("CASE WHEN substring(r.date, 6, 2) >= '08' THEN substring(r.date, 1, 4)::int"
          " ELSE substring(r.date, 1, 4)::int - 1 END")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=int, default=None,
                    help="only meets from this academic season on")
    ap.add_argument("--min-rows", type=int, default=1,
                    help="only meets with at least this many nameless rows")
    ap.add_argument("--sport", choices=("XC", "TF"), default=None,
                    help="one sport only (e.g. --sport XC --since 2026 first)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    since = f"{a.since}-08-01" if a.since else "0000"
    t0 = time.time()
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            cur.execute("SET work_mem = '1GB'")
            # every id the site can put a name to (scripts/unknown_names.py)
            cur.execute("""
                CREATE TEMP TABLE rb_named AS
                SELECT DISTINCT k AS id FROM (
                    SELECT person_id AS k FROM athletes
                    WHERE  NULLIF(btrim(concat_ws(' ', first_name, last_name)), '') IS NOT NULL
                      AND  person_id IS NOT NULL
                    UNION ALL
                    SELECT athlete_id FROM athletes
                    WHERE  NULLIF(btrim(concat_ws(' ', first_name, last_name)), '') IS NOT NULL) x;
                CREATE UNIQUE INDEX ON rb_named (id);
                ANALYZE rb_named
            """)
            cur.execute(f"""
                CREATE TEMP TABLE rb_meets AS
                SELECT sport, meet_id, min(season) AS season, count(*) AS nameless FROM (
                    SELECT 'XC'::text AS sport, r.meet_id, {SEASON} AS season
                    FROM   results r
                    LEFT JOIN rb_named n ON n.id = COALESCE(r.person_id, r.athlete_id)
                    WHERE  r.source = 'anet' AND r.date >= %(since)s
                      AND  n.id IS NULL AND NULLIF(btrim(r.athlete_name), '') IS NULL
                    UNION ALL
                    SELECT 'TF', r.meet_id, {SEASON}
                    FROM   results_tf r
                    LEFT JOIN rb_named n ON n.id = COALESCE(r.person_id, r.athlete_id)
                    WHERE  r.source = 'anet' AND r.date >= %(since)s
                      AND  COALESCE(r.is_relay, 0) = 0
                      AND  n.id IS NULL AND NULLIF(btrim(r.athlete_name), '') IS NULL
                ) x
                WHERE  %(sport)s::text IS NULL OR sport = %(sport)s
                GROUP  BY 1, 2
                HAVING count(*) >= %(min_rows)s
            """, {"since": since, "min_rows": a.min_rows, "sport": a.sport})
            cur.execute("""
                SELECT m.sport, m.season, count(*), sum(m.nameless),
                       count(*) FILTER (WHERE q.meet_id IS NULL)
                FROM   rb_meets m
                LEFT JOIN meet_queue q
                       ON q.meet_id = m.meet_id AND upper(q.sport) = m.sport
                      AND COALESCE(q.source, 'anet') = 'anet'
                GROUP  BY 1, 2 ORDER BY 1, 2""")
            rows = cur.fetchall()
            print(f"[requeue] anet meets with nameless results (relays left out)  "
                  f"({time.time() - t0:.0f}s)")
            print(f"  {'sport':<6}{'season':<8}{'meets':>9}{'nameless rows':>15}{'not queued':>12}")
            tot = [0, 0, 0]
            for sport, season, n, rws, nq in rows:
                print(f"  {sport:<6}{str(season):<8}{n:>9,}{rws:>15,}{nq:>12,}")
                tot = [tot[0] + n, tot[1] + rws, tot[2] + nq]
            print(f"  {'all':<14}{tot[0]:>9,}{tot[1]:>15,}{tot[2]:>12,}")
            if not a.apply:
                print("[requeue] dry run -- --apply marks these meets unscraped")
                conn.rollback()
                return
            cur.execute("""
                UPDATE meet_queue q SET scraped = 0
                FROM   rb_meets m
                WHERE  q.meet_id = m.meet_id AND upper(q.sport) = m.sport
                  AND  COALESCE(q.source, 'anet') = 'anet' AND q.scraped <> 0
            """)
            print(f"[requeue] {cur.rowcount:,} meets marked unscraped")
            cur.execute("""
                INSERT INTO meet_queue (meet_id, sport, source, scraped)
                SELECT m.meet_id, m.sport, 'anet', 0 FROM rb_meets m
                WHERE NOT EXISTS (SELECT 1 FROM meet_queue q
                                  WHERE q.meet_id = m.meet_id AND upper(q.sport) = m.sport
                                    AND COALESCE(q.source, 'anet') = 'anet')
                ON CONFLICT DO NOTHING
            """)
            print(f"[requeue] {cur.rowcount:,} meets added to the queue; the "
                  f"scraper's next claims repair their names")
        conn.commit()


if __name__ == "__main__":
    main()
