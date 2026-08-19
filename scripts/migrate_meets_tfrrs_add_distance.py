# Project: xc-predictor
# Author:  Tadhg Murray
# File:    scripts/migrate_meets_tfrrs_add_distance.py
# Purpose: SCHEMA STEP (#2, sub-step 1 of 2) for giving tfrrs XC a distance home.
#          Additively add two nullable columns to meets_tfrrs:
#              div_name  text   — the tfrrs division label ("1 Miles Family Race")
#              distance  real   — that division's distance IN METERS (1609 = 1mi)
#          These are populated later by the re-scrape (#1). This migration ONLY
#          adds the columns; it does NOT change the grain or the primary key.
#
#   WHY ONLY THE COLUMNS TODAY (the two-phase plan)
#   ----------------------------------------------
#   The END state is: meets_tfrrs becomes ONE ROW PER DIVISION, PK
#   (meet_id, sport, div_name) — mirroring anet's meets(div_id PK, distance),
#   one row per division carrying a distance. But we CANNOT widen the PK to
#   include div_name yet, because:
#       * a PRIMARY KEY column is implicitly NOT NULL, and
#       * every existing meets_tfrrs row currently has div_name = NULL
#         (the column didn't exist until now).
#   So widening the PK now would fail. The order is FORCED:
#       sub-step 1 (THIS FILE): add div_name + distance, NULLABLE. Non-breaking.
#       [re-scrape populates div_name + distance on every row]
#       sub-step 2 (LATER):     drop old PK, add PK (meet_id, sport, div_name),
#                               after disambiguating duplicate div_names.
#   This file is deliberately small and SAFE: adding nullable columns changes no
#   existing row and breaks no existing read (SELECT * just gains two NULLs).
#
#   THE HIERARCHY (where this sits)
#   -------------------------------
#     meets_tfrrs  TODAY:        one row per meet, PK (meet_id, sport)
#          |  + div_name text NULL, + distance real NULL   <-- THIS FILE
#          v
#     meets_tfrrs  TRANSITIONAL: one row per meet, two new empty columns
#          |  (re-scrape fills div_name + distance; grain -> per division)
#          v
#     meets_tfrrs  FINAL:        one row per division, PK (meet_id, sport, div_name)
#
#   UNIT/TYPE: distance is `real` and stored in METERS, matching anet's
#   meets.distance exactly, so the normalizer can treat both sources identically.
#
#   SAFETY: DRY-RUN BY DEFAULT (prints the exact ALTER statements, runs nothing).
#   --apply executes them. IDEMPOTENT: re-running skips columns that already exist.
#
#   RUN:
#       python scripts/migrate_meets_tfrrs_add_distance.py          # dry run
#       python scripts/migrate_meets_tfrrs_add_distance.py --apply  # execute

import sys
import argparse

sys.path.insert(0, "scripts")

from database import getConn, initPool


# The columns this migration adds: (name, SQL type). Declared once so the
# preview, the existence check, and the ALTER all use the same source of truth.
_NEW_COLUMNS = [
    ("div_name", "text"),   # tfrrs division label; part of the FUTURE PK
    ("distance", "real"),   # division distance in METERS (matches anet meets.distance)
]

_TABLE = "meets_tfrrs"


# ================================================================== #
# GUARD — IDEMPOTENCE.  Check whether a column already exists so a
#   re-run doesn't error on "column already exists". This is what makes
#   the migration safe to run twice.
# ================================================================== #

def _columnExists(cur, table, column):
    """
    Purpose : True iff <table>.<column> already exists.
    Arguments: cur — read cursor; table, column — names.
    Output  : bool.

    Syntax:
      - information_schema.columns is Postgres's catalog of every column.
      - A returned row means the column is already there -> we should SKIP adding
        it (adding again would raise). fetchone() is None => absent => add it.
    """
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s AND column_name=%s
    """, (table, column))
    return cur.fetchone() is not None


# ================================================================== #
# PLAN — decide, per column, whether it needs adding, WITHOUT executing.
#   Pure-ish (reads catalog, writes nothing). Produces the list of ALTER
#   statements we WOULD run, so the dry run can print exactly them.
# ================================================================== #

def _buildPlan(cur, table, new_columns):
    """
    Purpose : work out which columns still need adding and the exact SQL for each.
    Arguments: cur — read cursor; table — target table; new_columns — [(name,type)].
    Output  : list of (column, sql_string) for the columns that are MISSING.

    Mechanics:
      - For each desired column, check _columnExists; if absent, build its
        ALTER TABLE ... ADD COLUMN statement. Columns already present are omitted
        (that's the idempotence — a second run yields an empty plan).
      - ADD COLUMN with no NOT NULL / no DEFAULT is a metadata-only change in
        Postgres: it does NOT rewrite the 76k rows, so it's instant and safe.
      - The identifiers here are our OWN constants (not user input), so formatting
        them into the SQL is safe.
    """
    plan = []
    for column, coltype in new_columns:
        if not _columnExists(cur, table, column):
            sql = f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"
            plan.append((column, sql))
    return plan


# ================================================================== #
# APPLY — execute the planned ALTERs in one transaction.
# ================================================================== #

def _applyPlan(conn, plan):
    """
    Purpose : run the planned ALTER statements, atomically.
    Arguments: conn — write connection; plan — [(column, sql)] from _buildPlan.
    Output  : the number of columns added (int).

    Mechanics / syntax:
      - One cursor, execute each ALTER, commit ONCE at the end so the whole
        migration is all-or-nothing (if one ALTER failed we'd never commit and
        Postgres would roll back the rest).
      - DDL in Postgres is transactional, so this rollback safety is real for
        ALTER TABLE (unlike some other databases).
    """
    cur = conn.cursor()
    for _column, sql in plan:
        cur.execute(sql)
    conn.commit()
    return len(plan)


# ================================================================== #
# REPORT — show the plan (dry run) or confirm the apply.
# ================================================================== #

def _printPlan(table, plan, existing_note, apply):
    """
    Purpose : print exactly what will (or did) change.
    Arguments: table; plan — [(column, sql)]; existing_note — columns already
               present (skipped); apply — whether we're executing.
    Output  : None (prints).
    """
    mode = "APPLY" if apply else "DRY RUN"
    print("=" * 72)
    print(f"MIGRATE {table}: add div_name, distance   ({mode})")
    print("=" * 72)
    if existing_note:
        print(f"already present (skipped): {', '.join(existing_note)}")
    if not plan:
        print("nothing to add — all target columns already exist.")
        return
    print("statements to run:")
    for _column, sql in plan:
        print(f"  {sql}")


# ================================================================== #
# ORCHESTRATION
# ================================================================== #

def _run(conn, apply):
    """
    Purpose : plan the migration, print it, and (under --apply) execute it.
    Arguments: conn — connection; apply — execute iff True.
    Output  : None (prints; alters schema only under --apply).

    Sequence: read which of the target columns already exist (for the skip note),
    build the plan of missing ones, print, then optionally apply. Each step is a
    helper so this stays short and legible.
    """
    with conn.cursor() as cur:
        # Which target columns are ALREADY present (for an informative skip note).
        existing_note = [c for c, _t in _NEW_COLUMNS if _columnExists(cur, _TABLE, c)]
        plan = _buildPlan(cur, _TABLE, _NEW_COLUMNS)

    _printPlan(_TABLE, plan, existing_note, apply)

    if not plan:
        return

    if apply:
        n = _applyPlan(conn, plan)
        print("-" * 72)
        print(f"APPLIED: added {n} column(s) to {_TABLE}.")
        print("Next: re-scrape to populate div_name + distance, THEN run sub-step 2")
        print("(widen PK to (meet_id, sport, div_name) after disambiguation).")
    else:
        print("-" * 72)
        print("DRY RUN: nothing changed. Re-run with --apply to execute.")


def main():
    ap = argparse.ArgumentParser(
        description="Add nullable div_name+distance columns to meets_tfrrs (dry-run default).")
    ap.add_argument("--apply", action="store_true",
                    help="execute the ALTERs (default: dry run, changes nothing).")
    args = ap.parse_args()

    initPool()
    conn = getConn()
    if not hasattr(conn, "cursor") and hasattr(conn, "__enter__"):
        conn = conn.__enter__()

    _run(conn, args.apply)


if __name__ == "__main__":
    main()