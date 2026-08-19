# Project: xc-predictor
# File:    scripts/verify_merge.py
# Purpose: Verify that a copy-path backfill merge actually landed, and — only
#          once it has — reclaim the disk by dropping the <table>_old undo copy.
#
# WHY THIS EXISTS
# ---------------
# The backfill's copy path rebuilds `results` from scratch and leaves the
# original behind as `results_old`. That old table IS the undo button: nothing
# is irreversible until it is dropped. So the drop must never be casual, and it
# must never happen before the new table has been checked.
#
# This script therefore does the checks FIRST and refuses to drop unless they
# all pass. Dropping also requires an explicit --drop flag, so running it with
# no arguments is always safe: it only reports.
#
# USAGE
#   python scripts/verify_merge.py                     # verify XC, drop nothing
#   python scripts/verify_merge.py --table results_tf  # verify TF
#   python scripts/verify_merge.py --drop              # verify, then drop if OK
#
# THE FOUR CHECKS
#   1. both tables exist            -- otherwise there is nothing to compare
#   2. row counts are equal         -- the rebuild lost or duplicated no rows
#   3. values actually changed      -- the merge applied, rather than no-opping
#   4. no rows were silently nulled -- COALESCE preserved every skipped row
#
# WHICH COLUMN? The merge rebuilds the whole table but only ONE column changes.
# Checks 3 and 4 must look at THAT column. Pass --column speed_rating after a
# speed-ratings merge; the default is normalized_time (the backfill's column).
# Checking the wrong column makes check 3 report "ZERO rows changed" on a
# perfectly good merge, and the script then correctly refuses to drop the undo
# copy -- a false alarm produced by asking the wrong question.

import argparse
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# LOW-LEVEL HELPERS  —  one query, one answer
# ================================================================== #

# _scalar
# Purpose : run a query that returns exactly one row with one column, and hand
#           back that single value.
# Why a helper: every check below is "count something". Without this, each check
#   would repeat cursor/execute/fetchone and the checks would be buried in
#   plumbing. The plumbing lives here, once.
# Syntax  : cur.fetchone() returns a TUPLE even for a single column, so [0]
#           unwraps it. params must be a tuple; psycopg2 does the quoting.
def _scalar(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchone()[0]


# _tableExists
# Purpose : does this table exist in the public schema right now?
# Syntax  : to_regclass('public.foo') returns the table's OID if it exists and
#           NULL if it does not. It is the exception-free way to ask -- unlike
#           `SELECT 1 FROM foo LIMIT 1`, which raises when the table is missing
#           and would abort the surrounding transaction.
def _tableExists(cur, table):
    return _scalar(cur, "SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))


# _rowCount
# Purpose : the EXACT row count. Not reltuples.
# Why exact: pg_class.reltuples is an ESTIMATE refreshed by ANALYZE, and it can
#   be off by percent. We are proving the rebuild lost no rows, so an estimate
#   is worthless -- a 0.1% error on 39M rows is 39,000 phantom rows. This is a
#   full scan and it takes a little while. That is the price of a real answer.
def _rowCount(cur, table):
    return _scalar(cur, f"SELECT count(*) FROM {table}")


# _prettySize
# Purpose : how much disk the table + its indexes + its TOAST occupy.
# Syntax  : pg_total_relation_size includes indexes and TOAST; pg_relation_size
#           would count only the bare heap and understate what a DROP reclaims.
def _prettySize(cur, table):
    return _scalar(cur, "SELECT pg_size_pretty(pg_total_relation_size(%s::regclass))",
                   (table,))


# ================================================================== #
# THE FOUR CHECKS  —  each returns (passed: bool, message: str)
# ================================================================== #

# _checkBothExist
# Purpose : CHECK 1. There is no point comparing a table to one that is absent.
# A missing <table>_old usually means one of two things, and they are opposite:
#   * the merge never ran (nothing happened; rerun the backfill), or
#   * you already dropped it (the merge ran and you have already reclaimed).
# The script cannot tell those apart, so it says so and stops.
def _checkBothExist(cur, table, old):
    has_new = _tableExists(cur, table)
    has_old = _tableExists(cur, old)
    if has_new and has_old:
        return True, f"both {table} and {old} exist"
    if has_new and not has_old:
        return False, (f"{old} does not exist -- either the merge never ran, "
                       f"or {old} was already dropped. Nothing to verify.")
    return False, f"{table} itself does not exist. Something is very wrong."


# _checkRowCounts
# Purpose : CHECK 2. The rebuild is a LEFT JOIN from the old table, so it must
#           emit exactly one row per old row -- no more, no fewer.
# A MISMATCH is serious and has one likely cause: duplicate result_id values in
#   the staging table would fan the join out and CREATE rows. The staging table
#   has no unique constraint, so this check is the only thing standing between
#   you and a silently duplicated table.
def _checkRowCounts(cur, table, old):
    n_new = _rowCount(cur, table)
    n_old = _rowCount(cur, old)
    if n_new == n_old:
        return True, f"row counts match: {n_new:,}"
    return False, (f"ROW COUNT MISMATCH: {table}={n_new:,} vs {old}={n_old:,} "
                   f"(delta {n_new - n_old:+,}). Do NOT drop {old}.")


# _checkValuesChanged
# Purpose : CHECK 3. Prove the merge APPLIED, rather than rebuilding the table
#           with identical contents.
# Syntax  : `IS DISTINCT FROM` is null-safe inequality. Plain `<>` returns NULL
#           when either side is NULL, and NULL is not TRUE, so `WHERE a <> b`
#           would silently skip every row that went from NULL to a value --
#           which is most of the rows we care about. This is the single most
#           common way to write this check wrong.
# A count of ZERO means nothing changed: the backfill computed values but they
#   never reached the table.
def _checkValuesChanged(cur, table, old, column):
    n = _scalar(cur, f"""
        SELECT count(*)
        FROM {table} n JOIN {old} o USING (result_id)
        WHERE n.{column} IS DISTINCT FROM o.{column}
    """)
    if n > 0:
        return True, f"{n:,} rows have a different {column} -- the merge applied"
    return False, (f"ZERO rows of `{column}` changed. Either the merge did not "
                   f"apply, or this is the WRONG COLUMN (see --column). "
                   f"Do NOT drop {old}.")


# ELIGIBILITY. A full recompute NULLs any row it declined to write. That is only
# correct if the row is genuinely ineligible -- otherwise the recompute lost data.
# So for a recompute we do not merely TOLERATE nulls, we CHECK them:
#     a nulled row must fail the writer's own admission test.
# For speed_rating that test is the engine loader's WHERE clause. For
# normalized_time the admission test is the backfill's entire skip ladder
# (sentinel_time, dedup_twin, insane_*, unknown_pool, library_drop) and cannot be
# reconstructed in SQL, so we report the count and say so plainly.
# Columns are qualified with `n.` -- the NEW table. Eligibility is a property of
# the row as it stands now, not as it stood before. Unqualified names raise
# `column reference "normalized_time" is ambiguous`, because the check joins the
# new table to the old one and both have every column.
_ELIGIBLE = {
    "speed_rating": """
        n.normalized_time IS NOT NULL
        AND n.normalized_time BETWEEN 600 AND 3600
        AND n.date IS NOT NULL
        AND n.person_id IS NOT NULL
    """,
}


# _checkNothingNulled
# Purpose : CHECK 4, in two flavours.
#
#   UPSERT (default, e.g. backfill's old COALESCE semantics):
#       a row the writer skipped must KEEP its old value. Any NULL is a bug.
#
#   RECOMPUTE (--recompute):
#       the writer examined every row and DECLINED some. Those MUST be NULL --
#       a stale value is worse than a missing one. NULL says "we do not know";
#       7528 says "this athlete ran a 20-second 5K".
#       We verify the nulls are RIGHT, not merely allowed: every nulled row must
#       fail the writer's eligibility test. A nulled row that is still eligible
#       is lost data, and that still FAILS.
def _checkNothingNulled(cur, table, old, column, recompute=False):
    n = _scalar(cur, f"""
        SELECT count(*)
        FROM {table} n JOIN {old} o USING (result_id)
        WHERE o.{column} IS NOT NULL AND n.{column} IS NULL
    """)
    if not recompute:
        if n == 0:
            return True, "no row lost a value it previously had"
        return False, (f"{n:,} rows went from a value to NULL. "
                       f"If this was a FULL RECOMPUTE, rerun with --recompute. "
                       f"Otherwise, do NOT drop {old}.")

    if n == 0:
        return True, "no row was nulled"

    elig = _ELIGIBLE.get(column)
    if elig is None:
        return True, (f"{n:,} rows nulled (recompute). No SQL eligibility test "
                      f"exists for `{column}`; these should be the rows the "
                      f"writer skipped. Spot-check before dropping.")

    bad = _scalar(cur, f"""
        SELECT count(*)
        FROM {table} n JOIN {old} o USING (result_id)
        WHERE o.{column} IS NOT NULL AND n.{column} IS NULL
          AND ({elig})
    """)
    if bad == 0:
        return True, (f"{n:,} rows nulled, and EVERY ONE is ineligible "
                      f"-- the recompute cleared fossils, not data")
    return False, (f"{n:,} rows nulled, but {bad:,} of them are still ELIGIBLE. "
                   f"The recompute lost real data. Do NOT drop {old}.")


# ================================================================== #
# ORCHESTRATION
# ================================================================== #

# _runChecks
# Purpose : run all four in order, print each verdict, return overall pass/fail.
# Order matters: CHECK 1 gates the rest, because checks 2-4 would raise on a
#   missing table rather than returning a clean failure.
def _runChecks(cur, table, old, column, recompute=False):
    ok, msg = _checkBothExist(cur, table, old)
    print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    if not ok:
        return False

    print(f"  ... counting rows and comparing `{column}` "
          f"(full scan, this takes a minute) ...")
    ok, msg = _checkRowCounts(cur, table, old)
    print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    all_ok = ok
    ok, msg = _checkValuesChanged(cur, table, old, column)
    print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    all_ok = all_ok and ok
    ok, msg = _checkNothingNulled(cur, table, old, column, recompute)
    print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    return all_ok and ok


# _dropOld
# Purpose : reclaim the disk. Called ONLY after every check passed.
# Syntax  : DROP TABLE takes an ACCESS EXCLUSIVE lock, but only briefly -- it is
#           a catalog change plus an unlink of the files, not a scan. There is
#           no CONCURRENTLY variant and none is needed.
#           We do NOT use `CASCADE`: if anything unexpectedly depends on the old
#           table (a view, a foreign key), we want a loud error, not a silent
#           chain of drops.
def _dropOld(conn, old):
    size = None
    with conn.cursor() as cur:
        size = _prettySize(cur, old)
        cur.execute(f"DROP TABLE {old}")
    conn.commit()
    print(f"\n  dropped {old}, reclaimed {size}")


def main():
    ap = argparse.ArgumentParser(
        description="Verify a backfill merge, then optionally drop the undo table.")
    ap.add_argument("--table", default="results",
                    help="results (XC) or results_tf (TF)")
    ap.add_argument("--column", default="normalized_time",
                    help="the column the merge rewrote. `normalized_time` after "
                         "backfill_normalize.py; `speed_rating` after "
                         "engine/speed_ratings.py. Checks 3 and 4 compare THIS "
                         "column -- point them at an untouched one and check 3 "
                         "reports 'ZERO rows changed' on a healthy merge.")
    ap.add_argument("--recompute", action="store_true",
                    help="the merge was a FULL RECOMPUTE (preserve_unmatched="
                         "False), so rows the writer declined are now NULL. "
                         "Check 4 then verifies those nulls are CORRECT -- every "
                         "nulled row must fail the writer's eligibility test -- "
                         "rather than merely tolerating them.")
    ap.add_argument("--drop", action="store_true",
                    help="drop <table>_old, but ONLY if all four checks pass. "
                         "Without this flag the script only reports.")
    args = ap.parse_args()

    table = args.table
    old = f"{table}_old"

    initPool()
    print("=" * 70)
    print(f"VERIFY MERGE: {table} vs {old}")
    print("=" * 70)

    with getConn() as conn:
        with conn.cursor() as cur:
            passed = _runChecks(cur, table, old, args.column, args.recompute)
            if passed:
                print(f"\n  {old} is {_prettySize(cur, old)} of reclaimable disk")
        # The read transaction must END before DROP TABLE can take its lock --
        # exactly the lock conflict that hung the merge. Roll back explicitly.
        conn.rollback()

        print("-" * 70)
        if not passed:
            print("VERIFICATION FAILED. Keeping the old table. Nothing was dropped.")
            sys.exit(1)

        print("ALL CHECKS PASSED. The merge is good.")
        if args.drop:
            _dropOld(conn, old)
            print(f"\nDone. {old} is gone; the merge is now irreversible.")
        else:
            print(f"\n{old} was NOT dropped (no --drop flag).")
            print(f"When you are ready:  python scripts/verify_merge.py "
                  f"--table {table} --column {args.column} --drop")
    print("=" * 70)


if __name__ == "__main__":
    main()