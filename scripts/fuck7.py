#!/usr/bin/env python3
# Project: xc-predictor
# File:    dedup/prep_for_merge.py
# Purpose: Get the database ready for merge_links.py, entirely from Python -- no
#          psql. Three jobs, each idempotent (safe to run more than once):
#
#            1. ADD canon_meet_id to results / results_tf. The merge writes this
#               column for meet links; it does not exist yet, so without this the
#               meet pass errors the moment it goes live.
#            2. ADD a native_id index on results_tf. The merge stamps the tfrrs
#               side by native_id, which is unindexed -> a full scan of ~193M
#               rows. The index turns that from minutes into seconds.
#            3. CHECK for stale tfrrs-XC rows left in results_tf. If any remain,
#               the meet stamp (which matches on meet_id + source but NOT sport)
#               can bleed across sports, because meet_id integers are reused
#               across sports. This only REPORTS -- it changes nothing -- so you
#               decide whether to clean up before merging meets.
#
#          Nothing here writes to result/athlete DATA. It only adds empty columns
#          and an index, and reads a count.

import sys

sys.path.insert(0, "scripts")
from database import getConn


# ================================================================== #
# CHUNK 1 -- DB ACCESS
# ================================================================== #


# _exec
# Purpose: Run one statement and commit. For DDL (ALTER/CREATE), which is what
#          this whole script is.
# Arguments: sql, params.
# Output:  none.
def _exec(sql, params=()):
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()


# _scalar
# Purpose: Run a query that returns a single number and hand it back.
# Arguments: sql, params.
# Output:  the first column of the first row (or 0 if no rows).
def _scalar(sql, params=()):
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else 0


# ================================================================== #
# CHUNK 2 -- JOB 1: the canon_meet_id column
# ================================================================== #


# _addCanonMeetId
# Purpose: Add canon_meet_id BIGINT to both results tables if missing. IF NOT
#          EXISTS makes a re-run a no-op, so this is safe to run repeatedly.
# Output:  none (prints what it did).
def _addCanonMeetId():
    for table in ("results", "results_tf"):
        # ADD COLUMN IF NOT EXISTS: adds the column only when absent; on a table
        # that already has it, this does nothing and does not error.
        _exec(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS canon_meet_id BIGINT")
        print(f"  canon_meet_id present on {table}.")


# ================================================================== #
# CHUNK 3 -- JOB 2: the native_id index (speeds the tfrrs stamp)
# ================================================================== #


# _addNativeIdIndex
# Purpose: Build a partial index on results_tf(native_id) for tfrrs rows, so the
#          merge's tfrrs-side UPDATE uses an index instead of scanning 193M rows.
#          CONCURRENTLY = builds without locking the table for writes; the
#          tradeoff is it CANNOT run inside a transaction, so this uses its own
#          autocommit connection rather than the _exec helper.
# Output:  none (prints).
def _addNativeIdIndex():
    with getConn() as conn:
        conn.autocommit = True              # CONCURRENTLY forbids a transaction
        cur = conn.cursor()
        cur.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_results_tf_native_id
            ON results_tf (native_id) WHERE source = 'tfrrs'
        """)
    print("  idx_results_tf_native_id present (tfrrs stamp will use it).")


# ================================================================== #
# CHUNK 4 -- JOB 3: the stale tfrrs-XC safety check (report only)
# ================================================================== #


# _checkStaleXC
# Purpose: Count tfrrs rows in results_tf that look like XC (the mis-route the
#          roadmap flagged for cleanup). If >0, the meet merge can cross sports;
#          you should clean those before stamping canon_meet_id on meets.
#          NOTE: adjust the XC test to however the mis-route actually marked the
#          rows -- result_kind is the likeliest discriminator on results_tf.
# Output:  the count (also prints a verdict).
def _checkStaleXC():
    n = _scalar("""
        SELECT count(*) FROM results_tf
        WHERE source = 'tfrrs' AND result_kind = 'XC'
    """)
    if n == 0:
        print("  stale tfrrs-XC rows in results_tf: 0  -> meet merge is safe.")
    else:
        print(f"  stale tfrrs-XC rows in results_tf: {n:,}  -> CLEAN BEFORE meet merge")
        print("     (the athlete merge is unaffected; only the meet stamp is at risk).")
    return n


# ================================================================== #
# CHUNK 5 -- DRIVER
# ================================================================== #


# main
# Purpose: Run the three prep jobs in order and print a go / no-go for meets.
# Output:  none.
def main():
    print("=== prep_for_merge: readying the DB for merge_links.py ===\n")
    print("job 1: canon_meet_id column")
    _addCanonMeetId()
    print("\njob 2: native_id index")
    _addNativeIdIndex()
    print("\njob 3: stale tfrrs-XC check")
    stale = _checkStaleXC()

    print("\n--- summary ---")
    print("  athlete merge: ready.")
    print("  meet merge:   ", "ready." if stale == 0 else "clean stale XC rows first.")


if __name__ == "__main__":
    main()