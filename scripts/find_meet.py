#!/usr/bin/env python3
"""
find_meet.py -- is a meet in the database, and if not, why not.

    /srv/venv/bin/python scripts/find_meet.py "St. Mary"          # by name
    /srv/venv/bin/python scripts/find_meet.py 612345              # by meet id
    /srv/venv/bin/python scripts/find_meet.py 612345 --requeue    # ask again

★ WHY (owner, 2026-09-26: "find out why St. Mary's Invite wasn't scraped").
  meet_queue holds only (meet_id, sport, source, scraped), with no name, so
  "is this meet here" took raw SQL. This looks a name up in every meet
  table (anet XC meets, anet TF meets_tf_meta, tfrrs meets_tfrrs), joins the
  queue row, counts the results, and says what the queue state means:

    0 due      the next scrape will claim it
    1 done     scraped; with 0 results it was asked while still scheduled
    2 failed   retried on the next start (ANET_RETRY_FAILED / TFRRS_RETRY_FAILED)
    3 claimed  mid-scrape, or stranded by a stopped run (reset on start)
    4 no meet  the site said nothing is at this id -- re-asked only if a new
               forward block covers it; --requeue asks now
    (none)     never seeded: above the forward walk, or deleted

  A meet id is on the site's own URL: athletic.net/CrossCountry/meet/<ID>.
  --requeue sets scraped = 0 for that id (both feeds' rows that exist), so
  the next launcher run claims it. READ-ONLY without --requeue.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.environ.setdefault("XCP_DB_QUIET", "1")
from database import getConn                                  # noqa: E402

STATES = {0: "due", 1: "done", 2: "failed", 3: "claimed", 4: "no meet here"}

# (label, table, name col, date col, source, sport, results table, extra where)
TABLES = (
    ("anet XC", "meets", "meet_name", "meet_date", "anet", "XC", "results",
     "t.source = 'anet'"),
    ("anet TF", "meets_tf_meta", "meet_name", "meet_date", "anet", "TF",
     "results_tf", ""),
    ("tfrrs XC", "meets_tfrrs", "meet_name", "date", "tfrrs", "XC", "results",
     "t.sport = 'XC'"),
    ("tfrrs TF", "meets_tfrrs", "meet_name", "date", "tfrrs", "TF",
     "results_tf", "t.sport = 'TF'"),
)


def _hasCol(cur, table, col):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = %s AND column_name = %s""", (table, col))
    return cur.fetchone() is not None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", help="a meet name (any part) or a meet id")
    ap.add_argument("--requeue", action="store_true")
    a = ap.parse_args()
    by_id = a.what.strip().isdigit()
    found = []
    with getConn() as conn:
        with conn.cursor() as cur:
            for label, table, ncol, dcol, source, sport, res, where in TABLES:
                dsel = f"t.{dcol}" if _hasCol(cur, table, dcol) else "NULL"
                cond = ("t.meet_id = %(v)s" if by_id
                        else f"t.{ncol} ILIKE '%%' || %(v)s || '%%'")
                extra = f" AND {where}" if where else ""
                cur.execute(f"""
                    SELECT DISTINCT t.meet_id, t.{ncol}, {dsel}
                    FROM   {table} t
                    WHERE  {cond}{extra}
                    ORDER  BY 3 DESC NULLS FIRST
                    LIMIT  40
                """, {"v": int(a.what) if by_id else a.what.strip()})
                for mid, name, date in cur.fetchall():
                    cur.execute("SELECT scraped FROM meet_queue WHERE meet_id = %s "
                                "AND sport = %s AND source = %s",
                                (mid, sport, source))
                    q = cur.fetchone()
                    cur.execute(f"SELECT count(*) FROM {res} WHERE meet_id = %s "
                                f"AND source = %s", (mid, source))
                    n = cur.fetchone()[0]
                    found.append((label, mid, name, date, q[0] if q else None, n,
                                  sport, source))
            if by_id and not found:
                cur.execute("SELECT sport, source, scraped FROM meet_queue "
                            "WHERE meet_id = %s", (int(a.what),))
                rows = cur.fetchall()
                print(f"no meet row for id {a.what} in any feed")
                for sport, source, st in rows:
                    print(f"  queue: {source} {sport} state {st} "
                          f"({STATES.get(st, '?')})")
                if not rows:
                    print("  and no queue row: never seeded (above the forward "
                          "walk?) -- --requeue adds it")
                found = [("queue", int(a.what), None, None, st, 0, sport, source)
                         for sport, source, st in rows] or \
                        [("new", int(a.what), None, None, None, 0, "XC", "anet")]

            for label, mid, name, date, st, n, sport, source in found:
                if name is not None:
                    print(f"{label:<9} {mid:>9}  {str(date or '-'):<10}  "
                          f"{(name or '')[:48]:<48}  queue "
                          f"{'-' if st is None else st} "
                          f"({STATES.get(st, 'never seeded')}), {n:,} results")
                    if st == 1 and n == 0:
                        print(f"{'':<11}done with no results: asked before "
                              "results were posted. --requeue to ask again.")

            if a.requeue:
                done = 0
                for _l, mid, _n, _d, _st, _c, sport, source in found:
                    cur.execute("""
                        INSERT INTO meet_queue (meet_id, sport, source, scraped)
                        VALUES (%s, %s, %s, 0)
                        ON CONFLICT DO NOTHING""", (mid, sport, source))
                    cur.execute("""UPDATE meet_queue SET scraped = 0
                                   WHERE meet_id = %s AND sport = %s
                                     AND source = %s""", (mid, sport, source))
                    done += cur.rowcount
                conn.commit()
                print(f"requeued {done} queue row(s); run the launcher to scrape "
                      "them (ANET_NO_SEED=1 skips the walk)")


if __name__ == "__main__":
    main()
