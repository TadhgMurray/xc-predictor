#!/usr/bin/env python3
"""
requeue_blank_athletes.py -- find the anet athletes stored with no name and
no gender, and put their meets back on the scrape queue.

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

READ-ONLY without --apply.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from database import getConn                                  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=None,
                    help="only meets from this academic season on")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    since = f"{a.since}-08-01" if a.since else "0000"
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TEMP TABLE rb_blank AS
                SELECT athlete_id
                FROM   athletes
                WHERE  source = 'anet'
                GROUP  BY athlete_id
                HAVING bool_and(COALESCE(btrim(first_name), '') = ''
                                AND COALESCE(btrim(last_name), '') = '')
            """)
            cur.execute("CREATE INDEX ON rb_blank (athlete_id)")
            cur.execute("SELECT count(*) FROM rb_blank")
            n_ath = cur.fetchone()[0]
            print(f"[requeue] anet athletes with no name on any row: {n_ath:,}")
            cur.execute("""
                CREATE TEMP TABLE rb_meets AS
                SELECT DISTINCT 'XC'::text AS sport, r.meet_id,
                       CASE WHEN substring(r.date, 6, 2) >= '08'
                            THEN substring(r.date, 1, 4)::int
                            ELSE substring(r.date, 1, 4)::int - 1 END AS season
                FROM   results r JOIN rb_blank b ON b.athlete_id = r.athlete_id
                WHERE  r.source = 'anet' AND r.date >= %(since)s
                UNION
                SELECT DISTINCT 'TF', r.meet_id,
                       CASE WHEN substring(r.date, 6, 2) >= '08'
                            THEN substring(r.date, 1, 4)::int
                            ELSE substring(r.date, 1, 4)::int - 1 END
                FROM   results_tf r JOIN rb_blank b ON b.athlete_id = r.athlete_id
                WHERE  r.source = 'anet' AND r.date >= %(since)s
            """, {"since": since})
            cur.execute("""SELECT sport, season, count(DISTINCT meet_id)
                           FROM rb_meets GROUP BY 1, 2 ORDER BY 1, 2""")
            print(f"  {'sport':<6}{'season':<8}{'meets':>9}")
            for sport, season, n in cur.fetchall():
                print(f"  {sport:<6}{season:<8}{n:>9,}")
            if not a.apply:
                print("[requeue] dry run -- --apply marks these meets unscraped")
                conn.rollback()
                return
            cur.execute("""
                UPDATE meet_queue q SET scraped = 0
                FROM   (SELECT DISTINCT sport, meet_id FROM rb_meets) m
                WHERE  q.meet_id = m.meet_id AND upper(q.sport) = m.sport
                  AND  q.source = 'anet' AND q.scraped <> 0
            """)
            print(f"[requeue] {cur.rowcount:,} meets marked unscraped; the "
                  f"scraper's next claims repair their athletes")
        conn.commit()


if __name__ == "__main__":
    main()
