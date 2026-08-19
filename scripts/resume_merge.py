# Project: xc-predictor
# File:    scripts/resume_merge.py
# Purpose: Recover from a merge that crashed AFTER the drain, without re-running
#          the drain. Inspect the wreckage, clean it, and finish the merge from
#          the staging table that survived.
#
# ============================================================================
# WHY THE WORK SURVIVED
# ============================================================================
# The copy-path backfill has two phases with different transaction shapes:
#
#   DRAIN  -- reads every row, computes normalized_time, COPYs it into
#             bf_staging_<sport>. `_copyToStaging` COMMITS after every batch.
#             ==> the staging table is DURABLE. A crash cannot undo it.
#
#   MERGE  -- rebuilds the table, builds every index, swaps. ALL of it runs in
#             ONE transaction, committed only after the swap.
#             ==> a crash anywhere rolls back ALL of it. results_tf_new and its
#                 five indexes vanish.
#
# So when `_swapTables` hit `DuplicateTable: relation "results_tf_old" already
# exists`, it discarded 495.6s of heap rebuild plus 338.7s of index building --
# and lost nothing from the ~20-minute drain. The 34M computed values are still
# sitting in bf_staging_tf.
#
# This script finishes the job from there.
#
# ============================================================================
# THE DANGEROUS PART
# ============================================================================
# `<table>_old` means one of TWO things, and they demand opposite treatment:
#
#   (a) A COMPLETED earlier merge left it as the undo copy. Then `<table>`
#       already holds normalized values, and `<table>_old` is the only surviving
#       copy of the originals. Dropping it is irreversible.
#
#   (b) Crash debris. Then it is a duplicate of `<table>` and dropping it is
#       free.
#
# The script does NOT guess. It reports what it sees and refuses to act without
# an explicit flag. Read the report before you pass one.
#
# USAGE
#   python scripts/resume_merge.py --table results_tf              # report only
#   python scripts/resume_merge.py --table results_tf --clean      # drop debris
#   python scripts/resume_merge.py --table results_tf --finish     # run the merge

import argparse
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "backfill")

from database import getConn, initPool
from backfill_normalize import _mergeStagingIntoTable, _stagingTable


# ================================================================== #
# INSPECTION
# ================================================================== #

# _exists
# Purpose : does this relation exist? to_regclass returns its OID or NULL, and
#           unlike `SELECT 1 FROM foo` it never raises -- so a missing table does
#           not abort the surrounding transaction.
def _exists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


# _count
# Purpose : exact row count. Slow on 193M rows, but this is a decision about
#           whether to destroy data; an estimate will not do.
def _count(cur, name):
    cur.execute(f"SELECT count(*) FROM {name}")
    return cur.fetchone()[0]


# _normalizedCoverage
# Purpose : how many rows already carry a normalized_time.
# This is what distinguishes case (a) from case (b) above. If `<table>` is
#   heavily populated and `<table>_old` is not, a merge ALREADY COMPLETED and
#   `<table>_old` is your only copy of the originals. If both look the same,
#   `<table>_old` is debris.
def _normalizedCoverage(cur, name):
    cur.execute(f"SELECT count(*), count(normalized_time) FROM {name}")
    n, nn = cur.fetchone()
    return n, nn, (100.0 * nn / n if n else 0.0)


# _report
# Purpose : everything needed to decide, printed once, before anything is done.
def _report(cur, table):
    staging = _stagingTable(table.replace("results_tf", "TF")
                            .replace("results", "XC"))
    state = {"table": table, "staging": staging}

    print("=" * 74)
    print(f"STATE OF THE WORLD: {table}")
    print("=" * 74)

    for name in (table, f"{table}_old", f"{table}_new"):
        if not _exists(cur, name):
            print(f"  {name:<20} does not exist")
            state[name] = None
            continue
        n, nn, pct = _normalizedCoverage(cur, name)
        print(f"  {name:<20} {n:>14,} rows   normalized_time on "
              f"{nn:,} ({pct:.1f}%)")
        state[name] = (n, nn, pct)

    if _exists(cur, staging):
        s = _count(cur, staging)
        print(f"  {staging:<20} {s:>14,} rows   <- the DRAIN's output, durable")
        state["staging_rows"] = s
    else:
        print(f"  {staging:<20} does not exist   <- nothing to resume from")
        state["staging_rows"] = 0
    return state


# _interpret
# Purpose : say, in words, which of the two stories the numbers tell -- and NEVER
#           choose for the user when the evidence is ambiguous.
def _interpret(state, table):
    old = state.get(f"{table}_old")
    new = state.get(f"{table}_new")
    main = state.get(table)
    print("-" * 74)

    if new is not None:
        print(f"  {table}_new exists. That is impossible after a clean rollback,")
        print(f"  so the process was killed mid-merge (SIGKILL, power loss).")
        print(f"  It is always safe to drop: it was never swapped in.")

    if old is None:
        print(f"  No {table}_old. Nothing blocks the merge.")
        return "clear"

    if main and old:
        _n_m, _nn_m, pct_m = main
        _n_o, _nn_o, pct_o = old
        if pct_m > pct_o + 5.0:
            print(f"  {table} has MORE normalized_time coverage than {table}_old")
            print(f"  ({pct_m:.1f}% vs {pct_o:.1f}%).")
            print(f"  ==> A MERGE ALREADY COMPLETED. {table}_old is your ONLY")
            print(f"      copy of the original values. Verify before dropping:")
            print(f"        python scripts/verify_merge.py --table {table} --drop")
            print(f"      Do NOT use --clean here.")
            return "completed"
        print(f"  {table} and {table}_old have similar coverage "
              f"({pct_m:.1f}% vs {pct_o:.1f}%).")
        print(f"  ==> {table}_old is most likely CRASH DEBRIS from a swap that")
        print(f"      renamed the table and then rolled back, or from a run you")
        print(f"      interrupted. --clean will drop it.")
        return "debris"
    return "unknown"


# ================================================================== #
# ACTIONS
# ================================================================== #

# _clean
# Purpose : remove leftovers so the merge's precondition holds.
# We drop `_new` unconditionally (it was never live) but require the caller to
#   have read the interpretation before dropping `_old`.
def _clean(conn, table):
    with conn.cursor() as cur:
        for suffix in ("_new", "_old"):
            name = f"{table}{suffix}"
            if _exists(cur, name):
                print(f"  dropping {name} ...")
                cur.execute(f"DROP TABLE {name}")
    conn.commit()
    print("  clean.")


# _finish
# Purpose : run ONLY the merge, from the staging table the drain already filled.
# This skips the entire drain -- no re-scan, no re-compute, no re-COPY. The
#   staging table is exactly what `_mergeStagingIntoTable` expects, because that
#   is the contract the drain wrote it under.
def _finish(conn, table, staging):
    print(f"\nresuming merge: {table} <- {staging}")
    print("(the drain is NOT re-run; staging already holds every computed value)")
    _mergeStagingIntoTable(conn, table, staging)


def main():
    ap = argparse.ArgumentParser(
        description="Inspect and resume a crashed copy-path merge.")
    ap.add_argument("--table", default="results_tf",
                    help="results (XC) or results_tf (TF)")
    ap.add_argument("--clean", action="store_true",
                    help="drop <table>_new and <table>_old. Read the report "
                         "first: if a merge already COMPLETED, <table>_old is "
                         "your only copy of the originals.")
    ap.add_argument("--finish", action="store_true",
                    help="run the merge from the surviving staging table")
    args = ap.parse_args()

    table = args.table
    sport = "TF" if table.endswith("_tf") else "XC"
    staging = _stagingTable(sport)

    initPool()
    with getConn() as conn:
        with conn.cursor() as cur:
            _report(cur, table)
            verdict = _interpret({
                table: _normalizedCoverage(cur, table) if _exists(cur, table) else None,
                f"{table}_old": _normalizedCoverage(cur, f"{table}_old")
                                if _exists(cur, f"{table}_old") else None,
                f"{table}_new": _normalizedCoverage(cur, f"{table}_new")
                                if _exists(cur, f"{table}_new") else None,
            }, table)
            has_staging = _exists(cur, staging)
            staging_rows = _count(cur, staging) if has_staging else 0
        conn.rollback()          # release ACCESS SHARE before any DDL

        if args.clean:
            if verdict == "completed":
                print("\nREFUSING --clean: a merge already completed and "
                      f"{table}_old holds the only originals.")
                print("Use verify_merge.py --drop, which checks before it destroys.")
                sys.exit(1)
            _clean(conn, table)

        if args.finish:
            if not has_staging or staging_rows == 0:
                print(f"\nCannot finish: {staging} is missing or empty. "
                      f"The drain must be re-run.")
                sys.exit(1)
            with conn.cursor() as cur:
                for suffix in ("_old", "_new"):
                    if _exists(cur, f"{table}{suffix}"):
                        print(f"\nCannot finish: {table}{suffix} still exists. "
                              f"Run --clean first (after reading the report).")
                        sys.exit(1)
            _finish(conn, table, staging)

    if not args.clean and not args.finish:
        print("\n" + "-" * 74)
        print("Report only. Nothing was changed. When you have read the above:")
        print(f"  python scripts/resume_merge.py --table {table} --clean")
        print(f"  python scripts/resume_merge.py --table {table} --finish")


if __name__ == "__main__":
    main()