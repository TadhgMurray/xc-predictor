# Project: xc-predictor
# File:    tfrrs/sweep/prefill_tfrrs_queue.py
# Purpose: Seed meet_queue with the whole TFRRS id range (0..ID_CEILING) x sports
#          as scraped=0. The driver then drains it: each claimed id is fetched
#          once and either scraped (real meet) or DELETED from the queue (event
#          page that shares the id space). One fetch per id, no separate sweep.

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))

import psycopg2.extras
from database import getConn

ID_CEILING   = 100_000
SPORTS       = ("XC", "TF")
SOURCE_TFRRS = "tfrrs"
BATCH        = 5000


# _seedRange
# Purpose: Insert one batch of (meet_id, sport, source, scraped=0) rows.
#          Idempotent via ON CONFLICT so re-running never duplicates or resets.
# Arguments:
#           conn:     open connection (caller commits).
#           lo, hi:   inclusive id range for this batch.
# Output:   none.
def _seedRange(conn, lo, hi):
    rows = []
    for meet_id in range(lo, hi + 1):
        for sport in SPORTS:
            rows.append((meet_id, sport, SOURCE_TFRRS, 0))
    cursor = conn.cursor()
    psycopg2.extras.execute_values(cursor, """
        INSERT INTO meet_queue (meet_id, sport, source, scraped)
        VALUES %s
        ON CONFLICT (meet_id, sport, source) DO NOTHING
    """, rows)


# main
# Purpose: Seed the full range in batches, committing each so a crash keeps
#          progress and the giant insert never sits in one transaction.
def main():
    with getConn() as conn:
        lo = 0
        while lo <= ID_CEILING:
            hi = min(lo + BATCH - 1, ID_CEILING)
            _seedRange(conn, lo, hi)
            conn.commit()
            print(f"[PREFILL] seeded {lo}..{hi}", flush=True)
            lo = hi + 1
    print("[PREFILL] done", flush=True)


if __name__ == "__main__":
    main()