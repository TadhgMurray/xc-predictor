# Project: xc-predictor
# Author:  Tadhg Murray
# File:    scripts/drop_old_tables.py
# Purpose: drop the *_old shells left behind by backfill_normalize's table swap.
#
# WHY THIS EXISTS
# ---------------
# backfill_normalize rebuilds `results` by CTAS + atomic swap, which leaves
# `results_old` / `results_tf_old` behind. Those shells BLOCK EVERY MERGE, and
# they also hold the old indexes' NAMES -- index names are unique per schema,
# so the next rebuild dies with `DuplicateTable: "results_pkey" already exists`
# AFTER ~800s of work. Dropping them here frees both.
#
# TRADE-OFF, DELIBERATE: the _old tables ARE the pre-backfill snapshot. Once
# this runs there is no revert. That's acceptable inside the pipeline because
# step 6 (merged engine) cannot run while they exist anyway.

import sys
sys.path.insert(0, "scripts")            # database.py lives in scripts/

from database import getConn, initPool, closePool


# ------------------------------------------------------------------ #
# CHUNK 0 — CONSTANTS
# ------------------------------------------------------------------ #

# HARDCODED, never taken from argv. See _dropTable for why that matters.
OLD_TABLES = ("results_old", "results_tf_old")


# ------------------------------------------------------------------ #
# CHUNK 1 — HELPERS (one question each)
# ------------------------------------------------------------------ #

# _tableExists
# Purpose:   does this table exist, without raising if it doesn't?
# Arguments: cur -- an open cursor; name -- table name.
# Output:    bool.
# Syntax:    to_regclass() returns NULL instead of erroring on a missing
#            relation, so this is one round trip with no exception handling.
#            The %s IS a real bind parameter here -- `name` is being compared
#            as a STRING, not used as an identifier.
def _tableExists(cur, name):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
    return cur.fetchone()[0]


# _rowCount
# Purpose:   how many rows are we about to destroy? Logged, never gated on.
# Arguments: cur -- open cursor; name -- table name.
# Output:    int.
# Note:      a NONZERO count that differs wildly from the live table is the
#            scraper-ran-during-the-swap signature (7.8). Printed so the
#            morning log shows it; not fatal, because by this point the swap
#            has already happened and refusing to drop wouldn't recover them.
def _rowCount(cur, name):
    cur.execute(f"SELECT count(*) FROM {name}")
    return int(cur.fetchone()[0])


# _dropTable
# Purpose:   drop one shell.
# Arguments: cur -- open cursor; name -- table name from OLD_TABLES.
# Output:    None (side effect).
# Syntax:    an f-string, NOT a bind parameter -- psycopg2's %s binds VALUES,
#            never IDENTIFIERS. `DROP TABLE %s` with ("results_old",) sends
#            DROP TABLE 'results_old' (a quoted string) and syntax-errors.
# ★ THIS IS WHY OLD_TABLES IS A HARDCODED CONSTANT AND NOT AN ARGUMENT.
#   The moment a table name reaches an f-string it is concatenated into SQL,
#   so the ONLY safe source is a literal in this file. Never argv, never a
#   query result, never a config value.
# CASCADE is deliberately ABSENT: these shells should own nothing but their
# own indexes. If a DROP fails on a dependency, that dependency is a surprise
# and deserves to stop the pipeline, not be silently destroyed.
def _dropTable(cur, name):
    cur.execute(f"DROP TABLE {name}")


# ------------------------------------------------------------------ #
# CHUNK 2 — THE ONE PASS
# ------------------------------------------------------------------ #

# _dropIfPresent
# Purpose:   the whole decision for a single table -- check, log, drop.
# Arguments: cur -- open cursor; name -- table name.
# Output:    True if it dropped something, False if there was nothing to do.
# Note:      absence is NOT an error. The pipeline may run when a previous
#            run already cleaned up, or when the backfill swapped only one of
#            the two sports. A missing shell is the desired end state.
def _dropIfPresent(cur, name):
    if not _tableExists(cur, name):
        print(f"[drop] {name:<16} absent -- nothing to do")
        return False
    n = _rowCount(cur, name)
    _dropTable(cur, name)
    print(f"[drop] {name:<16} dropped ({n:,} rows released)")
    return True


# dropOldTables
# Purpose:   the entry point -- open one connection, walk OLD_TABLES, commit.
# Arguments: none.
# Output:    None; prints a ledger line per table plus a summary.
def dropOldTables():
    initPool()
    dropped = 0
    try:
        with getConn() as conn:
            with conn.cursor() as cur:
                for name in OLD_TABLES:
                    if _dropIfPresent(cur, name):
                        dropped += 1
            # ONE commit for both drops. DDL is transactional in Postgres, so
            # either both shells go or neither does -- no half-cleaned state
            # for the engine to trip over.
            conn.commit()
    finally:
        closePool()
    print(f"[drop] done -- {dropped} of {len(OLD_TABLES)} tables removed")


if __name__ == "__main__":
    dropOldTables()