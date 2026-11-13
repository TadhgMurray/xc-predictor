"""
build_meet_counts.py -- every meet's newest result date and result count.

    python racecast/build_meet_counts.py            # both sports, swap
    python racecast/build_meet_counts.py --dry-run

Run from the PROJECT ROOT. Pipeline step 13f4 / nightly. Read by
meets_filter.filteredMeets' browse path.

★ WHY (2026-11-12). A /meets browse view counted every result of up to
  3,200 candidate meets per request (a LATERAL per meet): 2.3 s quiet, 55 s
  during a pipeline step. The views are shared once computed
  (meets_filter, sharedcache), but the first visitor of each still paid.
  This table answers the same two numbers by key.

★ EXACT, OR NOT USED. Per results table the meta row keeps the table's oid
  and its change stamp (pg_stat_user_tables' inserts + updates + deletes),
  read before and after the aggregate -- a table that changed while it was
  read is not stored. The page uses the counts only while the live table
  still has that oid and stamp: a go-live (a new table) or a scrape's
  insert (a new stamp) sends it back to counting live, as before.
"""
import argparse
import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

TABLE = "meet_counts"
META = "meet_counts_meta"
SOURCES = {"XC": "results", "TF": "results_tf"}
_SANE = r"^(19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}$"

STAMP_SQL = """
    SELECT c.oid::bigint,
           COALESCE(s.n_tup_ins, 0) + COALESCE(s.n_tup_upd, 0) + COALESCE(s.n_tup_del, 0)
    FROM   pg_class c
    LEFT   JOIN pg_stat_user_tables s ON s.relid = c.oid
    WHERE  c.oid = to_regclass(%s)
"""


def stamp(cur, table):
    """(oid, change count) of `table`, or None when it does not exist."""
    cur.execute(STAMP_SQL, (table,))
    r = cur.fetchone()
    if not r:
        return None
    return (int(r[0]), int(r[1])) if not isinstance(r, dict) else \
        (int(list(r.values())[0]), int(list(r.values())[1]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from database import getConn
    from dbfast import interruptible, swapTable
    interruptible()
    t0 = time.time()
    rows, meta = [], []
    with getConn() as conn:
        with conn.cursor() as cur:
            for sport, table in SOURCES.items():
                before = stamp(cur, table)
                conn.commit()
                if before is None:
                    print(f"  {table}: missing, skipped", flush=True)
                    continue
                cur.execute(f"""SELECT meet_id, max(date), count(*) FROM {table}
                                WHERE date ~ %s AND meet_id IS NOT NULL
                                GROUP BY meet_id""", (_SANE,))
                got = cur.fetchall()
                conn.commit()
                after = stamp(cur, table)
                conn.commit()
                if after != before:
                    print(f"  {table}: changed while it was read ({before} -> {after}); "
                          f"not stored -- the pages count it live", flush=True)
                    continue
                rows += [(sport, m, d, n) for m, d, n in got]
                meta.append((sport, table, before[0], before[1]))
                print(f"  {table}: {len(got):,} meets [{time.time() - t0:.0f}s]", flush=True)
        if args.dry_run or not meta:
            print("nothing written" + (" (dry run)" if args.dry_run else ""))
            return 0
        import psycopg2.extras as E
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {TABLE}_new, {META}_new")
            cur.execute(f"""CREATE TABLE {TABLE}_new (sport text NOT NULL, meet_id bigint NOT NULL,
                                date text, n_results int NOT NULL,
                                PRIMARY KEY (sport, meet_id))""")
            cur.execute(f"""CREATE TABLE {META}_new (sport text PRIMARY KEY, tbl text NOT NULL,
                                oid bigint NOT NULL, stamp bigint NOT NULL,
                                built_at timestamptz NOT NULL DEFAULT now())""")
            E.execute_values(cur, f"INSERT INTO {TABLE}_new VALUES %s", rows, page_size=5000)
            E.execute_values(cur, f"INSERT INTO {META}_new (sport, tbl, oid, stamp) VALUES %s", meta)
        conn.commit()
        swapTable(conn, TABLE, renames=[(f"{TABLE}_new_pkey", f"{TABLE}_pkey")])
        swapTable(conn, META, renames=[(f"{META}_new_pkey", f"{META}_pkey")])
    print(f"{TABLE}: swapped in, {len(rows):,} meets, {time.time() - t0:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
