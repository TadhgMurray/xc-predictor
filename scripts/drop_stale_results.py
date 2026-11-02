#!/usr/bin/env python3
"""
drop_stale_results.py -- athletic.net rows a later posting of their race no
longer lists. One-off cleanup for what the saver now does on every scrape
(database.dropStaleResults).

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/drop_stale_results.py                      # DRY RUN
    $P scripts/drop_stale_results.py --write
    $P scripts/drop_stale_results.py --since 2025-08-01 --write

★ WHY (owner, 2026-11-02: UIAAA Invitational, every runner listed twice).
  athletic.net gives every result a new id when a meet re-posts its results,
  and the saver keyed only on result_id: a re-scrape added the new rows and
  kept the old. A division is saved whole with ONE scraped_at, so after a
  re-scrape every row the page still lists carries the new stamp; a row of
  that division stamped more than GAP_MIN earlier was not on the page.

! athletic.net's own rows only (result_id under 3e12: not AthleticLIVE's),
  rows with a scraped_at (older rows predate the stamp and are left), and
  only divisions whose newest scrape wrote MIN_FRESH rows or more.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ALIVE_BASE = 3_000_000_000_000
GAP_MIN = 10
MIN_FRESH = 3

_STALE = f"""
    WITH d AS (
        SELECT meet_id, div_id, max(scraped_at) AS newest
        FROM   results
        WHERE  source = 'anet' AND result_id > 0 AND result_id < {ALIVE_BASE}
          AND  scraped_at IS NOT NULL AND date >= %(since)s
        GROUP  BY 1, 2
    ), fresh AS (
        SELECT d.meet_id, d.div_id, d.newest, count(*) AS n_fresh
        FROM   d JOIN results r USING (meet_id, div_id)
        WHERE  r.source = 'anet' AND r.result_id > 0 AND r.result_id < {ALIVE_BASE}
          AND  r.scraped_at >= d.newest - make_interval(mins => {GAP_MIN})
        GROUP  BY 1, 2, 3
    )
    SELECT r.result_id, r.meet_id, r.div_id, f.n_fresh
    FROM   results r JOIN fresh f USING (meet_id, div_id)
    WHERE  r.source = 'anet' AND r.result_id > 0 AND r.result_id < {ALIVE_BASE}
      AND  r.scraped_at < f.newest - make_interval(mins => {GAP_MIN})
      AND  f.n_fresh >= {MIN_FRESH}
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="first date (default: this season's first day)")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", type=int, default=15)
    a = ap.parse_args()
    since = a.since
    if not since:
        import datetime
        from season_year import academicYear, seasonStart
        since = seasonStart(academicYear(datetime.date.today())).isoformat()
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '60min'")
        cur.execute(_STALE, {"since": since})
        rows = cur.fetchall()
        by = {}
        for rid, m, d, n_fresh in rows:
            by.setdefault((m, d), [0, n_fresh])[0] += 1
        print(f"[stale] {len(rows):,} rows in {len(by):,} races since {since} that the race's "
              f"latest posting no longer lists")
        for (m, d), (n, fresh) in sorted(by.items(), key=lambda x: -x[1][0])[:a.show]:
            print(f"   {n:>5} old beside {fresh:>5} current  https://racecast.co/race/xc/{m}/{d}")
        if not rows or not a.write:
            if rows:
                print("  DRY RUN -- --write deletes them")
            conn.rollback()
            return 0
        ids = [r[0] for r in rows]
        for i in range(0, len(ids), 5000):
            cur.execute("DELETE FROM results WHERE source = 'anet' AND result_id = ANY(%s)",
                        (ids[i:i + 5000],))
        conn.commit()
        print(f"[stale] {len(ids):,} rows deleted; the rankings drop them on their next build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
