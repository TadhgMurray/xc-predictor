# Project: xc-predictor
# File:    scripts/pipeline.py
# Purpose: ONE command. backfill -> engine -> suspects, with the bookkeeping
#          between stages done for you instead of by you.
#
# ============================================================================
# WHY THIS EXISTS
# ============================================================================
# Both heavy writes (backfill_normalize.py and speed_ratings.py) rebuild their
# table and leave the previous heap behind as `<table>_old`. That is a real undo
# button: nothing is irreversible until it is dropped.
#
# It is also a piece of STATE that the next stage refuses to start with. So a
# three-stage run became:
#
#     backfill --apply
#     verify_merge --table results --column normalized_time --drop
#     speed_ratings --sport XC
#     verify_merge --table results --column speed_rating --drop
#     diag_suspects --sport XC
#
# Five commands, two of which exist only to clean up after the previous one, each
# needing a different --column, and any of them forgotten leaves the next one
# dying at second zero with a wall of exclamation marks.
#
# The undo button is right for one-off surgery. It is wrong for a pipeline you
# run twenty times. So: this script verifies and drops between stages, and
# --keep-old opts back into doing it by hand.
#
# ============================================================================
# WHAT IT CHECKS BEFORE DROPPING
# ============================================================================
# Never a bare DROP. Before removing <table>_old it confirms:
#   1. both tables exist
#   2. their row counts are identical
#   3. the merged column actually changed in at least one row
# If any check fails it STOPS and leaves the old table alone. That is the same
# contract verify_merge.py has; this just stops making you type it.
#
# ============================================================================
# USAGE
#   python scripts/pipeline.py --sport XC                # the whole chain
#   python scripts/pipeline.py --sport XC --from engine  # resume mid-chain
#   python scripts/pipeline.py --sport XC --dry-run      # print, run nothing
#   python scripts/pipeline.py --sport both
#   python scripts/pipeline.py --sport XC --keep-old     # do the drops yourself
#
# STAGES
#   clean     drop any leftover <table>_old / <table>_new from a crashed run
#   backfill  backfill_normalize.py --apply     (rewrites normalized_time)
#   engine    speed_ratings.py                  (rewrites speed_rating)
#   suspects  diag_suspects.py                  (read-only; writes two .txt)
#
# The launcher must be OFF for backfill and engine: both swap the table, and a
# row written mid-rebuild lands in <table>_old and is lost.

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, "scripts")

from database import getConn, initPool


_TABLE = {"XC": "results", "TF": "results_tf"}
_STAGES = ("clean", "weather", "backfill", "engine", "suspects")

# Which column each stage rewrites. The drop check compares THIS column; pointing
# it at an untouched one reports "zero rows changed" on a healthy merge.
# NOTE: `weather` is NOT here -- it writes a PICKLE (weather_correction_<sport>.pkl),
# not a table, so it has no _old/_new bookkeeping. It's like `suspects`: just runs.
_STAGE_COLUMN = {"backfill": "normalized_time", "engine": "speed_rating"}


# ================================================================== #
# TABLE BOOKKEEPING
# ================================================================== #

def _exists(cur, name):
    """to_regclass returns the OID or NULL. Exception-free existence test: a
    missing table must not abort the surrounding transaction."""
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


def _count(cur, name):
    cur.execute(f"SELECT count(*) FROM {name}")
    return cur.fetchone()[0]


# _changedRows
# Purpose : did `column` actually change between the old and new tables?
# Syntax  : `IS DISTINCT FROM` is null-safe inequality. Plain `<>` yields NULL
#           when either side is NULL, and NULL is not TRUE -- so `<>` would skip
#           every row that went from NULL to a value, which is most of them.
def _changedRows(cur, table, old, column):
    cur.execute(f"""
        SELECT count(*) FROM {table} n JOIN {old} o USING (result_id)
        WHERE n.{column} IS DISTINCT FROM o.{column}
    """)
    return cur.fetchone()[0]


# Eligibility, per column. A recompute NULLs the rows it declined; a nulled row
# that is STILL eligible means the recompute lost data. `n.` qualifies the NEW
# table -- unqualified names are ambiguous across the join.
_ELIGIBLE = {
    "speed_rating": """
        n.normalized_time IS NOT NULL
        AND n.normalized_time BETWEEN 600 AND 3600
        AND n.date IS NOT NULL
        AND n.person_id IS NOT NULL
    """,
}


def _nulledRows(cur, table, old, column):
    cur.execute(f"""
        SELECT count(*) FROM {table} n JOIN {old} o USING (result_id)
        WHERE o.{column} IS NOT NULL AND n.{column} IS NULL
    """)
    return cur.fetchone()[0]


# _lostEligibleRows
# Purpose : rows the recompute nulled that it had no right to null.
# Output  : the count, or None when no SQL eligibility test exists for `column`.
def _lostEligibleRows(cur, table, old, column):
    elig = _ELIGIBLE.get(column)
    if elig is None:
        return None
    cur.execute(f"""
        SELECT count(*) FROM {table} n JOIN {old} o USING (result_id)
        WHERE o.{column} IS NOT NULL AND n.{column} IS NULL AND ({elig})
    """)
    return cur.fetchone()[0]


# _verifyAndDrop
# Purpose : the checks, then the drop. Refuses on any failure.
# This is verify_merge.py's contract, inlined so no human has to remember which
#   --column belongs to which stage.
def _verifyAndDrop(table, column, dry_run):
    old = f"{table}_old"
    with getConn() as conn:
        with conn.cursor() as cur:
            if not _exists(cur, old):
                print(f"  [{old}] does not exist -- nothing to drop")
                return True
            n_new, n_old = _count(cur, table), _count(cur, old)
            if n_new != n_old:
                print(f"  !! ROW COUNT MISMATCH {table}={n_new:,} "
                      f"{old}={n_old:,}. Keeping {old}.")
                return False
            changed = _changedRows(cur, table, old, column)
            if changed == 0:
                print(f"  !! zero rows of `{column}` changed. Either the merge "
                      f"did not apply, or this is the wrong column. Keeping {old}.")
                return False
            # Both writers are FULL RECOMPUTES: a row they declined is now NULL.
            # That is correct only if the row is genuinely ineligible. We check,
            # rather than tolerate. (For normalized_time the eligibility test is
            # the backfill's whole skip ladder and cannot be written in SQL, so
            # we report the count.)
            lost = _lostEligibleRows(cur, table, old, column)
            if lost is None:
                nulled = _nulledRows(cur, table, old, column)
                print(f"  [{old}] {nulled:,} rows nulled by the recompute "
                      f"(rows the backfill skipped)")
            elif lost > 0:
                print(f"  !! {lost:,} rows were nulled that are still ELIGIBLE. "
                      f"The recompute lost real data. Keeping {old}.")
                return False
            print(f"  [{old}] {n_new:,} rows match, {changed:,} rows of "
                  f"`{column}` changed")
        conn.rollback()          # end the read txn before DROP takes its lock

        if dry_run:
            print(f"  [dry-run] would DROP TABLE {old}")
            return True
        with conn.cursor() as cur:
            cur.execute(f"SELECT pg_size_pretty(pg_total_relation_size('{old}'))")
            size = cur.fetchone()[0]
            cur.execute(f"DROP TABLE {old}")
        conn.commit()
        print(f"  [{old}] dropped, reclaimed {size}")
    return True


# _cleanLeftovers
# Purpose : remove <table>_new and, if it is safe, <table>_old.
# <table>_new can ALWAYS be dropped: it was never swapped in, so nothing ever
#   read from it. Its existence means a run was killed mid-merge.
# <table>_old is only dropped when it is provably a duplicate of the live table
#   -- i.e. a rebuild ran but nothing changed, so it is debris rather than an undo
#   copy. Otherwise it is left for a human.
def _cleanLeftovers(table, dry_run):
    with getConn() as conn:
        with conn.cursor() as cur:
            new = f"{table}_new"
            if _exists(cur, new):
                print(f"  [{new}] exists -- a run was killed mid-merge. Dropping.")
                if not dry_run:
                    cur.execute(f"DROP TABLE {new}")
            old = f"{table}_old"
            if _exists(cur, old):
                print(f"  [{old}] exists from a previous stage.")
                print(f"     It will be verified and dropped after the next merge,")
                print(f"     or you can drop it now with verify_merge.py.")
        if not dry_run:
            conn.commit()


# ================================================================== #
# STAGE RUNNERS
# ================================================================== #

# _run
# Purpose : execute one child command, streaming its output, and STOP the whole
#           pipeline on a nonzero exit.
# Syntax  : subprocess.run without capture_output so the child's prints appear
#           live -- these stages take minutes and a silent terminal is
#           indistinguishable from a hang.
def _run(cmd, dry_run):
    print(f"\n$ {' '.join(cmd)}")
    if dry_run:
        return
    t0 = time.time()
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"\n[pipeline] `{' '.join(cmd)}` exited {r.returncode}. Stopping.\n"
                 f"Nothing after this stage has run. Fix it, then resume with\n"
                 f"  python scripts/pipeline.py --sport <sport> --from <stage>")
    print(f"[pipeline] took {time.time() - t0:.0f}s")


# _stageWeather
# Purpose : (re)fit the race-day weather correction, writing
#           engine/data/weather_correction_<sport>.pkl, which the NEXT backfill
#           applies as the last link in the normalize chain.
# Ordering: BEFORE backfill on purpose -- it fits on the CURRENT normalized_time
#           (this run's input = the previous run's output), then the backfill
#           bakes the correction in. --refresh so it re-queries current data
#           instead of serving a stale cache from before the last engine run.
# No table swap -> no verify/drop bookkeeping (it's like `suspects`).
# COST: this is the heavy weather join. Once the artifact is stable you normally
#       SKIP it with `--from backfill`; re-run it only when you want to re-fit.
def _stageWeather(sport, dry_run):
    _run([sys.executable, os.path.join("engine", "fit_weather_correction.py"),
          "--sport", sport, "--refresh"], dry_run)


def _stageBackfill(sport, dry_run):
    _run([sys.executable, os.path.join("backfill", "backfill_normalize.py"),
          "--sport", sport, "--apply"], dry_run)


def _stageEngine(sport, dry_run):
    _run([sys.executable, os.path.join("engine", "speed_ratings.py"),
          "--sport", sport], dry_run)


def _stageSuspects(sport, dry_run):
    _run([sys.executable, os.path.join("scripts", "diag_suspects.py"),
          "--sport", sport], dry_run)


_STAGE_FN = {"weather": _stageWeather, "backfill": _stageBackfill,
             "engine": _stageEngine, "suspects": _stageSuspects}


# ================================================================== #
# ORCHESTRATION
# ================================================================== #

def _runSport(sport, stages, keep_old, dry_run):
    table = _TABLE[sport]
    print("\n" + "=" * 74)
    print(f"PIPELINE  --  {sport} ({table})   stages: {', '.join(stages)}")
    print("=" * 74)

    for stage in stages:
        print(f"\n{'-' * 74}\n[{stage}]\n{'-' * 74}")

        if stage == "clean":
            _cleanLeftovers(table, dry_run)
            continue

        # Every merge stage needs a clean slate: <table>_old must not exist, or
        # the swap fails AFTER the rebuild. If a previous stage left one, verify
        # and drop it now using THAT stage's column.
        if stage in _STAGE_COLUMN and not keep_old:
            with getConn() as conn, conn.cursor() as cur:
                stale = _exists(cur, f"{table}_old")
                conn.rollback()
            if stale:
                prev = _prevMergeStage(stage)
                print(f"  {table}_old is left over. Verifying against "
                      f"`{_STAGE_COLUMN[prev]}` before dropping.")
                if not _verifyAndDrop(table, _STAGE_COLUMN[prev], dry_run):
                    sys.exit("[pipeline] refusing to continue with an unverified "
                             f"{table}_old. Investigate, then rerun.")

        _STAGE_FN[stage](sport, dry_run)

        # And clean up after ourselves, so the next stage starts clean.
        if stage in _STAGE_COLUMN and not keep_old:
            _verifyAndDrop(table, _STAGE_COLUMN[stage], dry_run)


# _prevMergeStage
# Purpose : which stage most likely wrote the leftover <table>_old?
# `backfill` and `engine` are the only merge stages. If we are about to run the
#   backfill and an _old exists, it came from a previous engine run (speed_rating)
#   or from a previous backfill (normalized_time). We try the stage BEFORE this
#   one in the chain, then fall back to the other.
def _prevMergeStage(stage):
    return "engine" if stage == "backfill" else "backfill"


def main():
    ap = argparse.ArgumentParser(
        description="Run the normalize -> rate -> detect chain as one command.")
    ap.add_argument("--sport", choices=["XC", "TF", "both"], default="XC")
    ap.add_argument("--from", dest="start", choices=_STAGES, default="clean",
                    help="resume at this stage")
    ap.add_argument("--only", choices=_STAGES,
                    help="run exactly one stage")
    ap.add_argument("--keep-old", action="store_true",
                    help="do NOT drop <table>_old between stages. You then have "
                         "to verify and drop them by hand, and the next merge "
                         "will refuse to start until you do.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print every command and drop, execute nothing")
    args = ap.parse_args()

    if args.only:
        stages = [args.only]
    else:
        stages = list(_STAGES[_STAGES.index(args.start):])

    initPool()
    print("!! the launcher/scraper must be OFF: backfill and engine swap the")
    print("!! table, and rows written mid-rebuild land in <table>_old and are lost")
    if args.dry_run:
        print("\n[DRY RUN] nothing will be executed or dropped")

    sports = ("XC", "TF") if args.sport == "both" else (args.sport,)
    for sp in sports:
        _runSport(sp, stages, args.keep_old, args.dry_run)

    print("\n" + "=" * 74)
    print("PIPELINE COMPLETE")
    print("=" * 74)
    print("  suspects_div_<sport>.txt   divisions where the whole field moved")
    print("  suspects_row_<sport>.txt   rows where one runner moved")


if __name__ == "__main__":
    main()