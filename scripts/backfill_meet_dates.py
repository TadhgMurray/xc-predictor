"""
backfill_meet_dates.py -- give every meet a date, and index it, so /meets can
filter and order by it.

    python scripts/backfill_meet_dates.py            # backfill + indexes
    python scripts/backfill_meet_dates.py --dry-run  # counts only

Run from the PROJECT ROOT with /etc/xc-predictor.env loaded. Safe with the
site running: the UPDATEs fill only rows with no date, in batches, and the
indexes are built CONCURRENTLY. Idempotent: a second run finds nothing to do.

★ WHY (audit 2026-11-09). /meets?year=2025 showed 0 meets and a date range
  showed 1: the browse query picked an arbitrary 3,200 meets and applied the
  year only afterwards, because `meets` had no usable date -- meet_date has
  been written by the scraper only since 2026-09-18, and 172,382 of 862,124
  meets carried one. The results carry a date on every row, but no index on
  it. This fills meets.meet_date (XC, per division) and meets_tf_meta.meet_date
  (track, per meet) from their results, the earliest day raced, and indexes
  both; meets_filter switches to them once the indexes exist
  (meets_filter.meetDatesIndexed).
"""
import argparse
import os
import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "racecast"))
from database import getConn  # noqa: E402

SANE = r"^(19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}"
BATCH = 50_000

INDEXES = (
    ("meets_meet_date_idx", "meets", "(meet_date)"),
    ("meets_tf_meta_meet_date_idx", "meets_tf_meta", "(meet_date)"),
)


def _xc(cur, dry):
    cur.execute("SELECT count(*) FROM meets WHERE NULLIF(meet_date, '') IS NULL")
    todo = cur.fetchone()[0]
    print(f"meets without a date: {todo:,}", flush=True)
    if dry or not todo:
        return 0
    done = 0
    while True:
        t0 = time.time()
        cur.execute(f"""
            WITH pick AS (
                SELECT div_id, source FROM meets
                WHERE  NULLIF(meet_date, '') IS NULL
                  AND  div_id NOT IN (SELECT div_id FROM _no_date)
                LIMIT  {BATCH}
            ),
            got AS (
                SELECT p.div_id, p.source,
                       (SELECT min(left(r.date, 10)) FROM results r
                        WHERE  r.div_id = p.div_id AND r.source = p.source
                          AND  r.date ~ %s) AS day
                FROM   pick p
            ),
            miss AS (
                INSERT INTO _no_date SELECT div_id FROM got WHERE day IS NULL
            )
            UPDATE meets m SET meet_date = g.day
            FROM   got g
            WHERE  m.div_id = g.div_id AND m.source = g.source AND g.day IS NOT NULL""",
                    (SANE,))
        n = cur.rowcount
        cur.execute("SELECT count(*) FROM _no_date")
        skipped = cur.fetchone()[0]
        cur.connection.commit()
        done += n
        print(f"  meets: +{n:,} dated ({done:,} so far, {skipped:,} with no dated result) "
              f"[{time.time() - t0:.0f}s]", flush=True)
        cur.execute("""SELECT EXISTS (SELECT 1 FROM meets WHERE NULLIF(meet_date, '') IS NULL
                                      AND div_id NOT IN (SELECT div_id FROM _no_date))""")
        if not cur.fetchone()[0]:
            return done


def _tf(cur, dry):
    cur.execute("""SELECT count(DISTINCT m.meet_id) FROM meets_tf m
                   LEFT JOIN meets_tf_meta t ON t.meet_id = m.meet_id
                   WHERE NULLIF(t.meet_date, '') IS NULL""")
    todo = cur.fetchone()[0]
    print(f"track meets without a date: {todo:,}", flush=True)
    if dry or not todo:
        return 0
    t0 = time.time()
    # only rows that already exist in meets_tf_meta are dated here; a track
    # meet with no meta row keeps no date (the browse falls back to its
    # results for it, as before)
    cur.execute("""
        UPDATE meets_tf_meta t SET meet_date = d.day
        FROM  (SELECT r.meet_id, min(left(r.date, 10)) AS day
               FROM   results_tf r
               WHERE  r.meet_id IN (SELECT meet_id FROM meets_tf_meta
                                    WHERE NULLIF(meet_date, '') IS NULL)
                 AND  r.date ~ %s
               GROUP  BY r.meet_id) d
        WHERE  t.meet_id = d.meet_id AND NULLIF(t.meet_date, '') IS NULL""", (SANE,))
    n = cur.rowcount
    cur.connection.commit()
    print(f"  track meets: +{n:,} dated [{time.time() - t0:.0f}s]", flush=True)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE IF NOT EXISTS _no_date (div_id bigint PRIMARY KEY)")
            _xc(cur, a.dry_run)
            _tf(cur, a.dry_run)
            conn.commit()
            if a.dry_run:
                return
            old = conn.autocommit
            conn.autocommit = True
            try:
                for name, table, cols in INDEXES:
                    t0 = time.time()
                    cur.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} {cols}")
                    print(f"index {name}: ready [{time.time() - t0:.0f}s]", flush=True)
            finally:
                conn.autocommit = old
    print("done", flush=True)


if __name__ == "__main__":
    main()
