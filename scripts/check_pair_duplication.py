#!/usr/bin/env python3
# Project: xc-predictor
# File:    scripts/null_merge_stamps.py
# Purpose: Reset the merge's stamps ahead of a re-merge on cleaned links.
#          The merge wrote person_id / canon_meet_id onto result rows; fake
#          meet links stamped WRONG identities onto tfrrs rows. anet rows are
#          untouched here because their stamps are tautologies (person_id =
#          their own athlete_id, canon_meet_id = their own meet_id) -- true
#          whether or not the triggering link was fake.
#
#          Destructive ONLY to the two derived columns, only on source='tfrrs'
#          rows. Fully recoverable: re-running merge_links.py regenerates them.
#
#          Run:  python scripts/null_merge_stamps.py

import sys
import time

sys.path.insert(0, "scripts")
from database import getConn

# (table, column) pairs to reset -- 2 tables x 2 stamp columns.
TARGETS = [("results_tf", "person_id"),
           ("results_tf", "canon_meet_id"),
           ("results",    "person_id"),
           ("results",    "canon_meet_id")]


# _nullColumn
# Purpose:   null ONE stamp column on ONE table's tfrrs rows, timed.
# Arguments: cur   -- shared cursor (one connection, one commit at the end,
#                     so the reset is all-or-nothing).
#            table -- "results_tf" or "results"; which results table.
#            col   -- "person_id" or "canon_meet_id"; which stamp to clear.
# Output:    int -- rows actually cleared. The IS NOT NULL guard means only
#            rows that Hold a stamp are rewritten; rows already null cost
#            nothing (matters enormously on a 193M-row table).
def _nullColumn(cur, table, col):
    t0 = time.time()
    cur.execute(f"""
        UPDATE {table} SET {col} = NULL
        WHERE source = 'tfrrs' AND {col} IS NOT NULL
    """)
    n = cur.rowcount
    print(f"  [{time.time()-t0:6.1f}s] {table}.{col}: cleared {n:,} rows")
    return n


def main():
    print("=== nulling merge stamps on tfrrs rows (pre-re-merge reset) ===")
    with getConn() as conn:
        cur = conn.cursor()
        for table, col in TARGETS:
            _nullColumn(cur, table, col)
        conn.commit()          # one commit: reset lands atomically or not at all
    print("Done. Re-run merge_links.py (DRY_RUN=True first) on the clean links.")


if __name__ == "__main__":
    main()