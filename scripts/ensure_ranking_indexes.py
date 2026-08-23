# Project: xc-predictor / scripts
# File:    ensure_ranking_indexes.py
# Purpose: Put the canonical indexes back on the LIVE ranking_results and
#          athlete_season, without a 112-minute rebuild.
#
# WHY THIS EXISTS
# ---------------
# build_ranking_results replayed the LIVE table's index catalogue onto the
# shadow it swaps in. That is self-erasing: once a run swaps in a table with
# no indexes, every run after it copies "no indexes" forward, silently.
# athlete_season never had them built at all -- createShadow leaves indexes
# off on purpose and nothing put them back.
#
# The fix in build_ranking_results.py makes the NEXT run correct. This script
# fixes the table that is live RIGHT NOW, which is the one the site is
# serving. It is idempotent: run it as often as you like.
#
# CONCURRENTLY by default, so the site keeps serving while they build. That is
# slower than a plain CREATE INDEX and cannot run inside a transaction, hence
# the autocommit connection. Pass --lock for the faster blocking build if the
# site is down anyway.
#
#     python scripts/ensure_ranking_indexes.py            # check only
#     python scripts/ensure_ranking_indexes.py --apply
#     python scripts/ensure_ranking_indexes.py --apply --lock

import argparse
import sys
import time

sys.path.insert(0, "racecast")
sys.path.insert(0, "scripts")
from database import getConn                      # noqa: E402
from build_ranking_results import _CANONICAL_INDEXES   # noqa: E402


def _cols(ddl):
    """The column list of an index definition, whitespace and case removed.

    Compared on columns rather than name because an index built by an earlier
    run carries an auto-generated name -- matching on the name would build the
    same tree a second time under a new one.
    """
    i = ddl.find("(")
    return ddl[i:].replace(" ", "").lower() if i >= 0 else ddl


def existing(cur, table):
    cur.execute("""
        SELECT indexname, indexdef,
               pg_size_pretty(pg_relation_size(indexname::regclass)) AS size
        FROM   pg_indexes
        WHERE  schemaname = 'public' AND tablename = %s
    """, (table,))
    return cur.fetchall()


def rowCount(cur, table):
    # The planner's estimate, not count(*): this is a report line, not a
    # number anything depends on, and count(*) on 56M rows is a minute.
    cur.execute("SELECT reltuples::bigint FROM pg_class WHERE relname = %s",
                (table,))
    row = cur.fetchone()
    return int(row[0]) if row else 0


def main():
    ap = argparse.ArgumentParser(
        description="Report, and optionally restore, the indexes the site "
                    "needs on ranking_results and athlete_season.")
    ap.add_argument("--apply", action="store_true",
                    help="actually create what is missing (default: report)")
    ap.add_argument("--lock", action="store_true",
                    help="plain CREATE INDEX instead of CONCURRENTLY -- "
                         "faster, but blocks writes to the table")
    args = ap.parse_args()

    missing_total = 0
    with getConn() as conn:
        conn.autocommit = True          # CONCURRENTLY cannot run in a txn
        with conn.cursor() as cur:
            for table, wanted in _CANONICAL_INDEXES.items():
                have = existing(cur, table)
                have_cols = {_cols(d) for _n, d, _s in have}
                n = rowCount(cur, table)
                print(f"\n{table}  (~{n:,} rows)")
                if not have:
                    print("  NO INDEXES AT ALL -- every query on this table "
                          "is a full scan.")
                for name, _d, size in have:
                    print(f"  have    {name}  {size}")

                todo = [(nm, cols) for nm, cols in wanted
                        if _cols(cols) not in have_cols]
                missing_total += len(todo)
                for nm, cols in todo:
                    full = f"{table}_{nm}"
                    if not args.apply:
                        print(f"  MISSING {full} {cols}")
                        continue
                    how = "" if args.lock else "CONCURRENTLY "
                    sql = (f"CREATE INDEX {how}IF NOT EXISTS {full} "
                           f"ON public.{table} {cols}")
                    print(f"  building {full} {cols} ...", flush=True)
                    t0 = time.time()
                    cur.execute(sql)
                    print(f"    [{time.time() - t0:7.1f}s] {full}")

            if args.apply and missing_total:
                for table in _CANONICAL_INDEXES:
                    print(f"\n  ANALYZE {table}")
                    cur.execute(f"ANALYZE {table}")

    print()
    if not missing_total:
        print("  every canonical index is present.")
    elif args.apply:
        print(f"  built {missing_total} indexes.")
    else:
        print(f"  {missing_total} missing. Re-run with --apply to build them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
