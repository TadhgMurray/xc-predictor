# Project: xc-predictor
# File:    backfill/migrate_source_identity.py
# Purpose: Adds source-provenance + cross-source identity columns ahead of the
#          TFRRS (and later Power-of-10) integration, so a future data source
#          needs NO retroactive ALTER on 100M+ rows.
#
#          Columns added:
#            athletes:  source, id_system, native_id, person_id
#            meets:     source, id_system, native_id
#            meets_tf:  source, id_system, native_id
#          (NOT on results / results_tf — a result's source is derived by join
#           to its athlete/meet. See the 6/25 diagnostic note: orphan rows are
#           NULL-id profile-less entries that don't participate in identity.)
#
# Staging (each stage idempotent + resumable; explicit commits throughout):
#   1. ADD COLUMN ... nullable, no default   -> metadata-only, instant
#   2. Backfill anet provenance, BATCHED + commit-per-batch
#   3. Add indexes on (person_id), (source)
#   4. (manual / later) tighten to NOT NULL once verified
#
# RUN THIS ONLY AFTER THE ANET SCRAPE HAS FULLY DRAINED. It's written to be
# safe during live writes (no long locks), but there's no reason to race it.
#
# CRITICAL house rule (w-doc 10): getConn() does NOT auto-commit. Every write
# path here calls conn.commit() explicitly, or the work silently rolls back.
 
import sys
import time
 
sys.path.insert(0, "scripts")
from database import initPool, closePool, getConn
 
# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #
 
# The provenance values stamped on every existing row. Everything currently
# in the DB came from athletic.net.
ANET_SOURCE    = "anet"
ANET_ID_SYSTEM = "anet"
 
# Backfill batch size. Big enough to be efficient, small enough that one
# batch is a short transaction (keeps lock windows tiny + makes the job
# resumable if interrupted).
BATCH_SIZE = 1_000_000
 
# Tables that get the full provenance set. athletes ALSO gets person_id
# (handled separately, since meets don't need an identity link).
PROVENANCE_TABLES = ["athletes", "meets", "meets_tf"]


# ------------------------------------------------------------------ #
# STAGE 1 — ADD COLUMNS (nullable, no default -> metadata-only, instant)
# ------------------------------------------------------------------ #
#
# We add every column nullable with NO default. In Postgres this is a
# catalog change only — it does NOT rewrite the table, so it's instant even
# on 142M rows and takes no meaningful lock. (ADD COLUMN ... DEFAULT x NOT
# NULL is the dangerous form that rewrites; we deliberately avoid it.)
 
 
# _addColumnIfMissing
# Purpose: Add one column to one table, only if it isn't already there.
#          Idempotent so the whole migration can be re-run safely.
# Arguments:
#           conn:      an open connection (caller owns commit)
#           table:     table name, e.g. "athletes"
#           column:    column name, e.g. "source"
#           col_type:  SQL type, e.g. "TEXT" or "BIGINT"
# Output:  bool — True if it added the column, False if it already existed
def _addColumnIfMissing(conn, table: str, column: str, col_type: str) -> bool:
    cursor = conn.cursor()
 
    # information_schema.columns is Postgres's catalog of every column.
    # Ask whether this one already exists before trying to add it.
    cursor.execute("""
        SELECT 1
        FROM   information_schema.columns
        WHERE  table_name = %s AND column_name = %s
    """, (table, column))
 
    if cursor.fetchone() is not None:
        print(f"    {table}.{column} already exists — skipping")
        return False
 
    # f-string for the identifier parts (table/column/type) because psycopg2
    # parameterises VALUES, not identifiers. These come from our own constants,
    # never user input, so there's no injection surface.
    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    print(f"    {table}.{column} ({col_type}) added")
    return True

# stage1AddColumns
# Purpose: Add the provenance columns to athletes/meets/meets_tf, and
#          person_id to athletes only. One commit at the end — these are all
#          fast metadata changes, safe to group.
# Arguments:
#           conn: an open connection
# Output:  none
def stage1AddColumns(conn) -> None:
    print("\n[Stage 1] Adding columns (nullable, no default)...")
 
    # source / id_system are TEXT; native_id is BIGINT (the source's own id).
    for table in PROVENANCE_TABLES:
        _addColumnIfMissing(conn, table, "source",    "TEXT")
        _addColumnIfMissing(conn, table, "id_system", "TEXT")
        _addColumnIfMissing(conn, table, "native_id", "BIGINT")
 
    # person_id lives on athletes only — it's the cross-source identity link.
    _addColumnIfMissing(conn, "athletes", "person_id", "BIGINT")
    # --- TFRRS-driven additions (result-level, on results_tf + results) ---
    # athlete_name: preserve the name so profile-less rows aren't anonymous
    #   (fixes the anet NULL-id orphan problem too). On BOTH result tables.
    _addColumnIfMissing(conn, "results_tf", "athlete_name", "TEXT")
    _addColumnIfMissing(conn, "results",    "athlete_name", "TEXT")

    # result_kind: "running"/"field"/"combined" — tells consumers what `mark`
    #   holds (metres vs decathlon points). results_tf only (XC is all running).
    _addColumnIfMissing(conn, "results_tf", "result_kind", "TEXT")

    # Identity/provenance on the RESULT tables too, since TFRRS results carry a
    # native_id with no anet athlete_id yet. (athletes/meets already covered.)
    for table in ("results", "results_tf"):
        _addColumnIfMissing(conn, table, "source",    "TEXT")
        _addColumnIfMissing(conn, table, "id_system", "TEXT")
        _addColumnIfMissing(conn, table, "native_id", "BIGINT")
        _addColumnIfMissing(conn, table, "person_id", "BIGINT")
 
    conn.commit()   # explicit — getConn() will NOT auto-commit (w-doc 10)
    print("[Stage 1] Done.")


# ------------------------------------------------------------------ #
# STAGE 2 — BACKFILL anet provenance, BATCHED
# ------------------------------------------------------------------ #
#
# Every existing row is anet. We set source/id_system to the anet constants,
# native_id = the table's own id (athlete_id / div_id), and for athletes,
# person_id = athlete_id (correct seed today: every anet athlete is trivially
# their own person; cross-source merges become plain UPDATEs later).
#
# Done in batches so each UPDATE is a short transaction. We drive the batching
# off "rows where source IS NULL" so the job is RESUMABLE — re-running picks up
# only the not-yet-filled rows.


# _backfillBatch
# Purpose: Fill provenance on up to BATCH_SIZE not-yet-filled rows of one
#          table, in a single committed UPDATE. Resumable: only touches rows
#          where source IS NULL.
# Arguments:
#           conn:       open connection.
#           table:      table to backfill.
#           native_col: column whose value becomes native_id.
#           person_col: column whose value becomes person_id, or None to skip
#                       person_id (meets/meets_tf don't have a person).
# Output:   int — rows this batch updated (0 = table done).
def _backfillBatch(conn, table: str, native_col: str, person_col: str | None) -> int:
    cursor = conn.cursor()

    # person_id only when the table has a person (athletes, results, results_tf).
    person_set = f", person_id = {person_col}" if person_col else ""

    cursor.execute(f"""
        UPDATE {table}
        SET    source = %s,
               id_system = %s,
               native_id = {native_col}
               {person_set}
        WHERE  ctid IN (
            SELECT ctid FROM {table}
            WHERE  source IS NULL
            LIMIT  {BATCH_SIZE}
        )
    """, (ANET_SOURCE, ANET_ID_SYSTEM))
 
    updated = cursor.rowcount
    conn.commit()   # commit EACH batch — short transaction, resumable
    return updated

# _countRemaining
# Purpose: How many rows in `table` still have NULL source (still to backfill).
#          Used to show progress and to know when a table is done.
# Arguments:
#           conn:  an open connection
#           table: table name
# Output:  int — rows still needing backfill
def _countRemaining(conn, table: str) -> int:
    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE source IS NULL")
    return cursor.fetchone()[0]
 
 
# _backfillTable
# Purpose: Backfill one table to completion, batch by batch.
# Arguments:
#           conn, table, native_col: as before.
#           person_col: column feeding person_id, or None.
# Output:   none.
def _backfillTable(conn, table: str, native_col: str, person_col: str | None) -> None:
    remaining = _countRemaining(conn, table)
    print(f"\n  {table}: {remaining:,} rows to backfill")

    done = 0
    while True:
        updated = _backfillBatch(conn, table, native_col, person_col)
        if updated == 0:
            break
        done += updated
        print(f"    {table}: {done:,} filled ({_countRemaining(conn, table):,} remaining)")
        time.sleep(0.05)

    print(f"  {table}: complete ({done:,} rows filled this run)")

# _backfillTableSinglePass
# Purpose: Stamp an entire table in ONE UPDATE (no batching). Scans the table
#          exactly once instead of re-scanning for NULLs each batch, so it's
#          faster on huge tables - at the cost of one long transaction (no
#          mid-way resume). Safe post-scrape since nothing else is writing.
#          Still WHERE source IS NULL, so it's resumable at the table level:
#          a re-run only touches rows not yet stamped.
# Arguments:
#           conn, table, native_col, person_col: as _backfillTable.
# Output:   none.
def _backfillTableSinglePass(conn, table: str, native_col: str, person_col: str | None) -> None:
    remaining = _countRemaining(conn, table)
    print(f"\n  {table}: {remaining:,} rows to backfill (single pass)")

    person_set = f", person_id = {person_col}" if person_col else ""
    cursor = conn.cursor()
    cursor.execute(f"""
        UPDATE {table}
        SET    source = %s,
               id_system = %s,
               native_id = {native_col}
               {person_set}
        WHERE  source IS NULL
    """, (ANET_SOURCE, ANET_ID_SYSTEM))
    conn.commit()
    print(f"  {table}: complete ({cursor.rowcount:,} rows filled)")
 
 
# stage2Backfill
# Purpose: Backfill provenance on all three tables. athletes also gets
#          person_id = athlete_id.
# Arguments:
#           conn: an open connection
# Output:  none
def stage2Backfill(conn) -> None:
    print("\n[Stage 2] Backfilling anet provenance (batched)...")
 
    # athletes: native_id = athlete_id, AND person_id = athlete_id.
    _backfillTable(conn, "athletes", "athlete_id", "athlete_id")
 
    # meets keyed on div_id (one row per division per a/s §2); native_id=div_id.
    _backfillTable(conn, "meets", "meet_id", None)
 
    # meets_tf composite-keyed; div_id is still a sensible native_id stamp.
    _backfillTable(conn, "meets_tf", "meet_id", None)

    _backfillTableSinglePass(conn, "results",    "result_id", "athlete_id")   # 35M, one pass
    _backfillTableSinglePass(conn, "results_tf", "result_id", "athlete_id")   # 142M, one pass
 
    print("[Stage 2] Done.")
 
# ------------------------------------------------------------------ #
# STAGE 3 — INDEXES (after data is in, so the index builds once on full data)
# ------------------------------------------------------------------ #
#
# person_id will be queried constantly once identity resolution runs ("all
# rows for this person"); source will filter by data origin. Index both.
# CREATE INDEX IF NOT EXISTS makes this idempotent.
 
 
# _createIndexIfMissing
# Purpose: Create one index, idempotently.
# Arguments:
#           conn:       an open connection
#           index_name: name to give the index
#           table:      table to index
#           column:     column to index
# Output:  none
def _createIndexIfMissing(conn, index_name: str,
                          table: str, column: str) -> None:
    cursor = conn.cursor()
    # IF NOT EXISTS -> safe to re-run. (Not CONCURRENTLY: that can't run inside
    # a transaction block and we're post-scrape, so a brief lock is fine.)
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})"
    )
    conn.commit()
    print(f"    index {index_name} on {table}({column}) ready")
 
 
# stage3Indexes
# Purpose: Add the person_id and source indexes.
# Arguments:
#           conn: an open connection
# Output:  none
def stage3Indexes(conn) -> None:
    print("\n[Stage 3] Creating indexes...")
    _createIndexIfMissing(conn, "idx_athletes_person_id", "athletes", "person_id")
    _createIndexIfMissing(conn, "idx_athletes_source",    "athletes", "source")
    _createIndexIfMissing(conn, "idx_meets_source",       "meets",    "source")
    _createIndexIfMissing(conn, "idx_meets_tf_source",    "meets_tf", "source")
    print("[Stage 3] Done.")
 
# ------------------------------------------------------------------ #
# STAGE 4 — NOT NULL tightening — DELIBERATELY MANUAL / DEFERRED
# ------------------------------------------------------------------ #
#
# We do NOT auto-tighten source/id_system/person_id to NOT NULL here. Reasons:
#   - Verify the backfill actually filled everything first (eyeball the
#     _countRemaining == 0 output above).
#   - ALTER ... SET NOT NULL scans the whole table to validate; run it
#     deliberately, not as a surprise tail of a long script.
# When ready, run manually (psql or a tiny script):
#     ALTER TABLE athletes ALTER COLUMN source     SET NOT NULL;
#     ALTER TABLE athletes ALTER COLUMN id_system  SET NOT NULL;
#     ALTER TABLE athletes ALTER COLUMN person_id  SET NOT NULL;
#     -- (repeat source/id_system for meets, meets_tf)
# native_id stays NULLABLE (a future source may lack a native id).
 
# ------------------------------------------------------------------ #
# MAIN
# ------------------------------------------------------------------ #
 
# main
# Purpose: Run stages 1-3 in order behind one pool open/close. Stage 4 is
#          manual (see above). Prompts before starting since it writes.
# Output:  none
def main() -> None:
    initPool()
    try:
        print("Source/identity migration")
        print("Stages: 1 add columns -> 2 batched backfill -> 3 indexes")
        print("(Stage 4 NOT NULL tightening is manual — see file footer.)")
        confirm = input("\nProceed? Type 'yes': ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            return
 
        # All stages share one connection. getConn() is a contextmanager that
        # rolls back on exception + returns the conn to the pool on exit.
        with getConn() as conn:
            stage1AddColumns(conn)
            stage2Backfill(conn)
            stage3Indexes(conn)
 
        print("\nMigration complete (stages 1-3). "
              "Verify, then run Stage 4 NOT NULL manually.")
    finally:
        closePool()
 
 
if __name__ == "__main__":
    main()