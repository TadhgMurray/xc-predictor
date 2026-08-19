# Project: xc-predictor
# File:    scripts/drop_dupe_indexes.py
# Purpose: Remove redundant indexes from a results table before a full-column
#          backfill, and drop the index on the column being rewritten.
#
# WHY THIS EXISTS
# ---------------
# EXPLAIN (ANALYZE, BUFFERS) on one 10,000-row flush page of the backfill:
#
#     Update on results        (actual time=5538.716 ...)
#       Buffers: shared hit=317387 read=55752 dirtied=54575 written=38290
#       ->  Nested Loop        (actual time=0.064..258.447 rows=10000)
#             ->  Index Scan using results_pkey  (10000 searches, ~22us each)
#
# FINDING the rows cost 258 ms.  WRITING them cost 5,281 ms -- 95% of the
# statement.  54,575 dirty 8 KB pages for 10,000 rows is 45 KB of disk written
# to change one 4-byte float.  The cause:
#   * every UPDATE writes a NEW heap tuple (Postgres never updates in place),
#   * HOT (heap-only tuple) cannot fire, because `normalized_time` is indexed
#     AND the table was bulk-loaded at fillfactor=100 (no free space on-page),
#   * so EVERY index takes a fresh, random-page B-tree insertion.
#
# The write cost is therefore roughly proportional to the INDEX COUNT. And
# `results` carries eight, two pairs of which appear to be exact duplicates:
#
#     idx_results_athlete_id        results_athlete_id_idx     <- same column
#     idx_results_meet_id           results_meet_id_idx        <- same column
#     idx_results_canon_meet_id     results_native_id_idx
#     idx_results_normalized_time   results_pkey
#
# Dropping the two duplicates + the normalized_time index takes 8 indexes to 5:
# a ~30% cut to the write cost, permanently, for free. The duplicates were
# never earning anything -- the planner only ever uses one of an identical pair,
# but the executor maintains BOTH on every insert and update, including the
# scraper's.
#
# USAGE
#   1. Set TABLE below ("results" or "results_tf").
#   2. python scripts/drop_dupe_indexes.py
#   3. Run the backfill.
#   4. python scripts/drop_dupe_indexes.py --rebuild      (restores the
#      normalized_time index; duplicates stay gone, on purpose.)
#
# SAFETY
#   Nothing here touches data. An index is derived state: worst case you rebuild
#   it. DROP INDEX CONCURRENTLY takes only a SHARE UPDATE EXCLUSIVE lock, so the
#   scraper keeps running while it works.

import argparse
import sys
import time

sys.path.insert(0, "scripts")

from database import getConn, initPool


TABLE = "results"        # "results" for XC, "results_tf" for TF


# ================================================================== #
# THE RUNNER  —  statements that refuse to live inside a transaction
# ================================================================== #

# _execAutocommit
# Purpose : run ONE statement OUTSIDE any transaction block.
# Why     : DROP INDEX CONCURRENTLY and CREATE INDEX CONCURRENTLY both refuse to
#           run inside a transaction -- they commit at several points internally,
#           which is exactly what lets them avoid taking an exclusive lock.
#           psycopg2 opens an implicit BEGIN before your first statement, so we
#           must suppress it by setting conn.autocommit = True.
# Why restore: the connection goes back to the POOL when the `with` exits.
#           Leaving autocommit on would silently change behaviour for whoever
#           grabs it next -- including the backfill's server-side named cursor,
#           which CANNOT run under autocommit (psycopg2 raises "can't use a named
#           cursor outside of transactions"). Restoring is a bug fence.
# Syntax  : `finally` runs even if execute() raises, so the restore is guaranteed.
# Arguments: sql -- one statement. label -- what to print.
# Output  : None. Prints a timed line, because these can run for minutes and a
#           silent terminal is indistinguishable from a hang.
def _execAutocommit(sql, label):
    with getConn() as conn:
        prior = conn.autocommit
        conn.autocommit = True
        try:
            t0 = time.time()
            with conn.cursor() as cur:          # `with` closes the cursor on any exit
                cur.execute(sql)
            print(f"  [{time.time() - t0:7.1f}s] {label}")
        finally:
            conn.autocommit = prior


# ================================================================== #
# THE ANALYSIS  —  which indexes are redundant, and how big are they
# ================================================================== #

# _indexRows
# Purpose : every index on `table`, with its size and its exact CREATE statement.
# Syntax  : pg_indexes.indexdef IS the full DDL Postgres would emit to recreate
#           the index -- columns, opclass, uniqueness, partial WHERE, all of it.
#           `indexname::regclass` casts the name to an object identifier so
#           pg_relation_size can measure it; pg_size_pretty formats the bytes.
# Output  : [(name, size_bytes, pretty_size, indexdef), ...]
def _indexRows(cur, table):
    cur.execute("""
        SELECT indexname,
               pg_relation_size(indexname::regclass),
               pg_size_pretty(pg_relation_size(indexname::regclass)),
               indexdef
        FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = %s
        ORDER BY indexname
    """, (table,))
    return cur.fetchall()


# _identityOf
# Purpose : strip an index's NAME from its DDL, leaving only what it INDEXES.
# Syntax  : indexdef looks like
#             CREATE INDEX myname ON public.results USING btree (athlete_id)
#           `.split(" ON ", 1)` splits on the FIRST " ON " and returns two parts;
#           `[1]` takes the tail. Everything before it (CREATE INDEX + the name)
#           is discarded. What remains -- table, method, columns, opclass, any
#           WHERE clause -- is the index's IDENTITY.
# Two indexes are redundant if and only if their identities are equal.
# `UNIQUE` lives BEFORE the " ON ", so a unique index and a plain index on the
#   same column will NOT be treated as duplicates. That is correct: a unique
#   index enforces a constraint and is not interchangeable with a plain one.
def _identityOf(indexdef):
    return indexdef.split(" ON ", 1)[1].strip()


# _findDuplicates
# Purpose : group indexes by identity; the first of each group is kept, the rest
#           are droppable.
# Detail  : results_pkey is skipped entirely. It backs a PRIMARY KEY CONSTRAINT,
#           so DROP INDEX will refuse; and the backfill's flush joins on it, so
#           we want it. Any index whose name ends in _pkey is left alone.
# Output  : [(droppable_name, pretty_size, kept_name), ...]
def _findDuplicates(rows):
    seen = {}                                   # identity -> the name we keep
    droppable = []
    for name, _bytes, pretty, ddl in rows:
        if name.endswith("_pkey"):
            continue
        identity = _identityOf(ddl)
        if identity in seen:
            droppable.append((name, pretty, seen[identity]))
        else:
            seen[identity] = name
    return droppable


# ================================================================== #
# THE ACTIONS
# ================================================================== #

# _dropIndex
# Purpose : remove one index without locking out the scraper.
# Syntax  : CONCURRENTLY makes Postgres take a SHARE UPDATE EXCLUSIVE lock rather
#           than ACCESS EXCLUSIVE, so readers AND writers keep working. IF EXISTS
#           makes the script rerunnable -- run it twice, the second is a no-op.
def _dropIndex(name):
    _execAutocommit(f"DROP INDEX CONCURRENTLY IF EXISTS {name}",
                    f"DROP {name}")


# _rebuildNormalizedIndex
# Purpose : recreate the index on the rewritten column, ONCE, after the backfill.
# Why this is the whole point: building an index in one pass means Postgres SORTS
#   the column and writes the B-tree bottom-up -- sequential I/O. Maintaining it
#   during the backfill meant 34.8M random single-key insertions instead.
# maintenance_work_mem governs how much of that sort stays in RAM. It is a
#   SESSION setting -- no postgresql.conf edit, no restart, no superuser.
def _rebuildNormalizedIndex(table):
    _execAutocommit("SET maintenance_work_mem = '4GB'", "maintenance_work_mem=4GB")
    _execAutocommit(
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{table}_normalized_time "
        f"ON {table} (normalized_time)",
        f"CREATE idx_{table}_normalized_time")


# ================================================================== #
# ORCHESTRATION
# ================================================================== #

# _report
# Purpose : print the current index inventory. Run this before and after so the
#           change is visible rather than asserted.
def _report(table):
    with getConn() as conn, conn.cursor() as cur:
        rows = _indexRows(cur, table)
    print(f"\n{len(rows)} indexes on {table}:")
    for name, _b, pretty, ddl in rows:
        print(f"  {name:<34} {pretty:>10}   {_identityOf(ddl)}")
    return rows


# _doDrop
# Purpose : the forward pass. Drop duplicates, then the normalized_time index.
# The two are separated because they have different justifications:
#   duplicates    -> permanently worthless, never rebuild them.
#   normalized_time -> useful, but not DURING a full-column rewrite. Rebuild after.
def _doDrop(table):
    rows = _report(table)
    dupes = _findDuplicates(rows)

    if not dupes:
        print("\nno duplicate indexes found.")
    else:
        print(f"\n{len(dupes)} redundant indexes (identical definition):")
        for name, pretty, kept in dupes:
            print(f"  {name} ({pretty}) duplicates {kept}")
        print()
        for name, _, _ in dupes:
            _dropIndex(name)

    print(f"\ndropping idx_{table}_normalized_time for the duration of the backfill:")
    _dropIndex(f"idx_{table}_normalized_time")

    _report(table)
    print(f"\nNow run the backfill. Afterwards:"
          f"\n  python scripts/drop_dupe_indexes.py --rebuild")


# _doRebuild
# Purpose : the reverse pass. Only the normalized_time index comes back; the
#           duplicates stay gone, which is the point.
def _doRebuild(table):
    print(f"rebuilding idx_{table}_normalized_time (one sorted pass):")
    _rebuildNormalizedIndex(table)
    _report(table)
    print("\nFinally, reclaim dead tuples and refresh planner stats:")
    print(f"  VACUUM (ANALYZE) {table};")


def main():
    ap = argparse.ArgumentParser(
        description="Drop redundant indexes before a full-column backfill.")
    ap.add_argument("--table", default=TABLE,
                    help="results (XC) or results_tf (TF)")
    ap.add_argument("--rebuild", action="store_true",
                    help="recreate the normalized_time index AFTER the backfill")
    args = ap.parse_args()

    initPool()
    if args.rebuild:
        _doRebuild(args.table)
    else:
        _doDrop(args.table)


if __name__ == "__main__":
    main()# Project: xc-predictor
# File:    scripts/drop_dupe_indexes.py
# Purpose: Remove redundant indexes from a results table before a full-column
#          backfill, and drop the index on the column being rewritten.
#
# WHY THIS EXISTS
# ---------------
# EXPLAIN (ANALYZE, BUFFERS) on one 10,000-row flush page of the backfill:
#
#     Update on results        (actual time=5538.716 ...)
#       Buffers: shared hit=317387 read=55752 dirtied=54575 written=38290
#       ->  Nested Loop        (actual time=0.064..258.447 rows=10000)
#             ->  Index Scan using results_pkey  (10000 searches, ~22us each)
#
# FINDING the rows cost 258 ms.  WRITING them cost 5,281 ms -- 95% of the
# statement.  54,575 dirty 8 KB pages for 10,000 rows is 45 KB of disk written
# to change one 4-byte float.  The cause:
#   * every UPDATE writes a NEW heap tuple (Postgres never updates in place),
#   * HOT (heap-only tuple) cannot fire, because `normalized_time` is indexed
#     AND the table was bulk-loaded at fillfactor=100 (no free space on-page),
#   * so EVERY index takes a fresh, random-page B-tree insertion.
#
# The write cost is therefore roughly proportional to the INDEX COUNT. And
# `results` carries eight, two pairs of which appear to be exact duplicates:
#
#     idx_results_athlete_id        results_athlete_id_idx     <- same column
#     idx_results_meet_id           results_meet_id_idx        <- same column
#     idx_results_canon_meet_id     results_native_id_idx
#     idx_results_normalized_time   results_pkey
#
# Dropping the two duplicates + the normalized_time index takes 8 indexes to 5:
# a ~30% cut to the write cost, permanently, for free. The duplicates were
# never earning anything -- the planner only ever uses one of an identical pair,
# but the executor maintains BOTH on every insert and update, including the
# scraper's.
#
# USAGE
#   1. Set TABLE below ("results" or "results_tf").
#   2. python scripts/drop_dupe_indexes.py
#   3. Run the backfill.
#   4. python scripts/drop_dupe_indexes.py --rebuild      (restores the
#      normalized_time index; duplicates stay gone, on purpose.)
#
# SAFETY
#   Nothing here touches data. An index is derived state: worst case you rebuild
#   it. DROP INDEX CONCURRENTLY takes only a SHARE UPDATE EXCLUSIVE lock, so the
#   scraper keeps running while it works.

import argparse
import sys
import time

sys.path.insert(0, "scripts")

from database import getConn, initPool


TABLE = "results"        # "results" for XC, "results_tf" for TF


# ================================================================== #
# THE RUNNER  —  statements that refuse to live inside a transaction
# ================================================================== #

# _execAutocommit
# Purpose : run ONE statement OUTSIDE any transaction block.
# Why     : DROP INDEX CONCURRENTLY and CREATE INDEX CONCURRENTLY both refuse to
#           run inside a transaction -- they commit at several points internally,
#           which is exactly what lets them avoid taking an exclusive lock.
#           psycopg2 opens an implicit BEGIN before your first statement, so we
#           must suppress it by setting conn.autocommit = True.
# Why restore: the connection goes back to the POOL when the `with` exits.
#           Leaving autocommit on would silently change behaviour for whoever
#           grabs it next -- including the backfill's server-side named cursor,
#           which CANNOT run under autocommit (psycopg2 raises "can't use a named
#           cursor outside of transactions"). Restoring is a bug fence.
# Syntax  : `finally` runs even if execute() raises, so the restore is guaranteed.
# Arguments: sql -- one statement. label -- what to print.
# Output  : None. Prints a timed line, because these can run for minutes and a
#           silent terminal is indistinguishable from a hang.
def _execAutocommit(sql, label):
    with getConn() as conn:
        prior = conn.autocommit
        conn.autocommit = True
        try:
            t0 = time.time()
            with conn.cursor() as cur:          # `with` closes the cursor on any exit
                cur.execute(sql)
            print(f"  [{time.time() - t0:7.1f}s] {label}")
        finally:
            conn.autocommit = prior


# ================================================================== #
# THE ANALYSIS  —  which indexes are redundant, and how big are they
# ================================================================== #

# _indexRows
# Purpose : every index on `table`, with its size and its exact CREATE statement.
# Syntax  : pg_indexes.indexdef IS the full DDL Postgres would emit to recreate
#           the index -- columns, opclass, uniqueness, partial WHERE, all of it.
#           `indexname::regclass` casts the name to an object identifier so
#           pg_relation_size can measure it; pg_size_pretty formats the bytes.
# Output  : [(name, size_bytes, pretty_size, indexdef), ...]
def _indexRows(cur, table):
    cur.execute("""
        SELECT indexname,
               pg_relation_size(indexname::regclass),
               pg_size_pretty(pg_relation_size(indexname::regclass)),
               indexdef
        FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = %s
        ORDER BY indexname
    """, (table,))
    return cur.fetchall()


# _identityOf
# Purpose : strip an index's NAME from its DDL, leaving only what it INDEXES.
# Syntax  : indexdef looks like
#             CREATE INDEX myname ON public.results USING btree (athlete_id)
#           `.split(" ON ", 1)` splits on the FIRST " ON " and returns two parts;
#           `[1]` takes the tail. Everything before it (CREATE INDEX + the name)
#           is discarded. What remains -- table, method, columns, opclass, any
#           WHERE clause -- is the index's IDENTITY.
# Two indexes are redundant if and only if their identities are equal.
# `UNIQUE` lives BEFORE the " ON ", so a unique index and a plain index on the
#   same column will NOT be treated as duplicates. That is correct: a unique
#   index enforces a constraint and is not interchangeable with a plain one.
def _identityOf(indexdef):
    return indexdef.split(" ON ", 1)[1].strip()


# _findDuplicates
# Purpose : group indexes by identity; the first of each group is kept, the rest
#           are droppable.
# Detail  : results_pkey is skipped entirely. It backs a PRIMARY KEY CONSTRAINT,
#           so DROP INDEX will refuse; and the backfill's flush joins on it, so
#           we want it. Any index whose name ends in _pkey is left alone.
# Output  : [(droppable_name, pretty_size, kept_name), ...]
def _findDuplicates(rows):
    seen = {}                                   # identity -> the name we keep
    droppable = []
    for name, _bytes, pretty, ddl in rows:
        if name.endswith("_pkey"):
            continue
        identity = _identityOf(ddl)
        if identity in seen:
            droppable.append((name, pretty, seen[identity]))
        else:
            seen[identity] = name
    return droppable


# ================================================================== #
# THE ACTIONS
# ================================================================== #

# _dropIndex
# Purpose : remove one index without locking out the scraper.
# Syntax  : CONCURRENTLY makes Postgres take a SHARE UPDATE EXCLUSIVE lock rather
#           than ACCESS EXCLUSIVE, so readers AND writers keep working. IF EXISTS
#           makes the script rerunnable -- run it twice, the second is a no-op.
def _dropIndex(name):
    _execAutocommit(f"DROP INDEX CONCURRENTLY IF EXISTS {name}",
                    f"DROP {name}")


# _rebuildNormalizedIndex
# Purpose : recreate the index on the rewritten column, ONCE, after the backfill.
# Why this is the whole point: building an index in one pass means Postgres SORTS
#   the column and writes the B-tree bottom-up -- sequential I/O. Maintaining it
#   during the backfill meant 34.8M random single-key insertions instead.
# maintenance_work_mem governs how much of that sort stays in RAM. It is a
#   SESSION setting -- no postgresql.conf edit, no restart, no superuser.
def _rebuildNormalizedIndex(table):
    _execAutocommit("SET maintenance_work_mem = '4GB'", "maintenance_work_mem=4GB")
    _execAutocommit(
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{table}_normalized_time "
        f"ON {table} (normalized_time)",
        f"CREATE idx_{table}_normalized_time")


# ================================================================== #
# ORCHESTRATION
# ================================================================== #

# _report
# Purpose : print the current index inventory. Run this before and after so the
#           change is visible rather than asserted.
def _report(table):
    with getConn() as conn, conn.cursor() as cur:
        rows = _indexRows(cur, table)
    print(f"\n{len(rows)} indexes on {table}:")
    for name, _b, pretty, ddl in rows:
        print(f"  {name:<34} {pretty:>10}   {_identityOf(ddl)}")
    return rows


# _doDrop
# Purpose : the forward pass. Drop duplicates, then the normalized_time index.
# The two are separated because they have different justifications:
#   duplicates    -> permanently worthless, never rebuild them.
#   normalized_time -> useful, but not DURING a full-column rewrite. Rebuild after.
def _doDrop(table):
    rows = _report(table)
    dupes = _findDuplicates(rows)

    if not dupes:
        print("\nno duplicate indexes found.")
    else:
        print(f"\n{len(dupes)} redundant indexes (identical definition):")
        for name, pretty, kept in dupes:
            print(f"  {name} ({pretty}) duplicates {kept}")
        print()
        for name, _, _ in dupes:
            _dropIndex(name)

    print(f"\ndropping idx_{table}_normalized_time for the duration of the backfill:")
    _dropIndex(f"idx_{table}_normalized_time")

    _report(table)
    print(f"\nNow run the backfill. Afterwards:"
          f"\n  python scripts/drop_dupe_indexes.py --rebuild")


# _doRebuild
# Purpose : the reverse pass. Only the normalized_time index comes back; the
#           duplicates stay gone, which is the point.
def _doRebuild(table):
    print(f"rebuilding idx_{table}_normalized_time (one sorted pass):")
    _rebuildNormalizedIndex(table)
    _report(table)
    print("\nFinally, reclaim dead tuples and refresh planner stats:")
    print(f"  VACUUM (ANALYZE) {table};")


def main():
    ap = argparse.ArgumentParser(
        description="Drop redundant indexes before a full-column backfill.")
    ap.add_argument("--table", default=TABLE,
                    help="results (XC) or results_tf (TF)")
    ap.add_argument("--rebuild", action="store_true",
                    help="recreate the normalized_time index AFTER the backfill")
    args = ap.parse_args()

    initPool()
    if args.rebuild:
        _doRebuild(args.table)
    else:
        _doDrop(args.table)


if __name__ == "__main__":
    main()