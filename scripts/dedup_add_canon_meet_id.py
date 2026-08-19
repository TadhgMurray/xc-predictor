#!/usr/bin/env python3
# Project: xc-predictor
# File:    dedup/add_canon_meet_id.py
# Purpose: One-time (but safely re-runnable) schema prep for the merge step.
#          merge_links.py stamps a shared canon_meet_id onto both the anet and
#          tfrrs rows of each confirmed meet link -- but that column does not
#          exist yet on either results table. This script adds it.
#
#          It must add the column to BOTH result tables, because meets live in
#          two places by sport:
#              results_tf  -> TF meets (anet + tfrrs)
#              results     -> XC meets (anet + tfrrs)
#          Miss one and merge_links.py crashes on that sport's meet pass with
#          'column "canon_meet_id" does not exist'.
#
#          SAFETY (this is the §5 FIX-2 lesson baked in): a bare
#          'ALTER TABLE ... ADD COLUMN' both (a) errors if the column already
#          exists and (b) takes an ACCESS EXCLUSIVE lock EVERY run, even a no-op,
#          which can hang behind a live meets-scan. So we CHECK first via
#          information_schema.columns and ALTER only when the column is missing.
#          That makes a second run a true no-op that takes no exclusive lock.
#
#          RUN WITH THE LAUNCHER/SCRAPER OFF. The ALTER takes ACCESS EXCLUSIVE on
#          the target table; if a long results/results_tf scan is in flight it
#          will queue behind it (and block everyone else behind THAT).
#
# Output:  prints what it did for each table; no return value.

import sys

sys.path.insert(0, "scripts")
from database import getConn

# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

# The column we are adding, and its type. BIGINT because it holds a meet_id
# (anet meet_id, reused as the canonical value -- same width as the source ids).
CANON_COLUMN = "canon_meet_id"
CANON_TYPE   = "BIGINT"

# Both result tables that need the column -- the per-sport split used everywhere.
TARGET_TABLES = ["results", "results_tf"]

# Build an index on the new column so downstream reads that filter/group by
# canon_meet_id (the engine treating two source meets as one) don't full-scan.
# Set False if you'd rather add it later -- it is NOT needed by merge_links.py
# itself (that writes by meet_id, already indexed).
BUILD_INDEX = True


# ------------------------------------------------------------------ #
# CHUNK 1 -- INSPECTION: does the column already exist?
# ------------------------------------------------------------------ #
#
# The whole safety story rests on one read: ask the catalog whether the column
# is already there, and only mutate if it isn't. One helper, used as the guard.


# _columnExists
# Purpose: Ask Postgres' catalog whether `column` is already on `table`. This is
#          the guard that makes the script idempotent and lock-free on re-runs.
# Arguments:
#           table:  table name to check (a plain identifier, e.g. 'results').
#           column: column name to look for (e.g. 'canon_meet_id').
# Output:  True if the column exists on that table, else False.
def _columnExists(table: str, column: str) -> bool:
    with getConn() as conn:
        cur = conn.cursor()
        # information_schema.columns has one row per (table, column). If our
        # (table, column) pair returns a row, the column is present. Parameters
        # are passed as VALUES (not f-string'd) so this read is injection-safe.
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
            """,
            (table, column),
        )
        return cur.fetchone() is not None


# _indexExists
# Purpose: Same idea as _columnExists but for the index, so re-running doesn't try
#          to CREATE INDEX twice (which would error).
# Arguments:
#           index_name: the index's name (we name it deterministically below).
# Output:  True if an index by that name exists, else False.
def _indexExists(index_name: str) -> bool:
    with getConn() as conn:
        cur = conn.cursor()
        # pg_indexes lists indexes by name; a returned row means it's already there.
        cur.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname = %s",
            (index_name,),
        )
        return cur.fetchone() is not None


# ------------------------------------------------------------------ #
# CHUNK 2 -- MUTATION: add the column / index, only when absent
# ------------------------------------------------------------------ #
#
# Each mutator first calls its CHUNK-1 guard and returns early if there's nothing
# to do -- so the ACCESS EXCLUSIVE-taking statement only ever runs when needed.


# _addColumn
# Purpose: Add CANON_COLUMN to one table IF it isn't already there. The guard is
#          what keeps a re-run from taking an exclusive lock or erroring.
# Arguments:
#           table: the table to alter ('results' or 'results_tf').
# Output:  none (prints what it did).
def _addColumn(table: str) -> None:
    if _columnExists(table, CANON_COLUMN):
        # Already present -> do NOTHING (no ALTER, so no exclusive lock taken).
        print(f"  {table}.{CANON_COLUMN} already exists -- skipping.")
        return

    with getConn() as conn:
        cur = conn.cursor()
        # The table name can't be a bound parameter (it's an identifier, not a
        # value), so it's interpolated -- safe because it comes from our own
        # TARGET_TABLES constant, never user input. The column adds as all-NULL,
        # which is instant in Postgres (no table rewrite for a nullable add).
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {CANON_COLUMN} {CANON_TYPE}")
        conn.commit()   # getConn does not auto-commit; make the DDL stick
    print(f"  {table}: added column {CANON_COLUMN} {CANON_TYPE}.")


# _addIndex
# Purpose: Build an index on CANON_COLUMN for one table IF missing and IF
#          BUILD_INDEX is on. Names the index deterministically so the existence
#          check and the create agree.
# Arguments:
#           table: the table to index.
# Output:  none (prints what it did).
def _addIndex(table: str) -> None:
    if not BUILD_INDEX:
        return
    index_name = f"idx_{table}_{CANON_COLUMN}"   # e.g. idx_results_canon_meet_id
    if _indexExists(index_name):
        print(f"  {index_name} already exists -- skipping.")
        return

    with getConn() as conn:
        cur = conn.cursor()
        # A plain (non-CONCURRENT) index build also takes a lock and blocks writes
        # to the table while it runs -- fine here because we run with the scraper
        # OFF. If you ever build this on a live table, switch to CREATE INDEX
        # CONCURRENTLY (which can't run inside a transaction block).
        cur.execute(f"CREATE INDEX {index_name} ON {table} ({CANON_COLUMN})")
        conn.commit()
    print(f"  {table}: built index {index_name} on ({CANON_COLUMN}).")


# ------------------------------------------------------------------ #
# CHUNK 3 -- DRIVER
# ------------------------------------------------------------------ #


# main
# Purpose: Add canon_meet_id (and optionally its index) to every target table,
#          idempotently. Safe to run more than once.
# Output:  none (prints a per-table report).
def main():
    print("=== add canon_meet_id to results tables (idempotent) ===")
    print("    (run with the launcher/scraper OFF -- this takes ACCESS EXCLUSIVE)\n")
    for table in TARGET_TABLES:
        print(f"--- {table} ---")
        _addColumn(table)
        _addIndex(table)
    print("\n=== done. merge_links.py's meet pass can now stamp canon_meet_id. ===")


if __name__ == "__main__":
    main()