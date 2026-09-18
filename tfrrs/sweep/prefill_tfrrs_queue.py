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

# ! A FLOOR FOR THE CEILING, NOT THE CEILING. The real one is computed from
#   the data by ceilingFor() -- a fixed 100,000 stopped covering new meets the
#   moment TFRRS passed it, and tfrrs ids are already at ~96,000, so a re-run
#   of this file seeded nothing and the queue stayed empty.
ID_CEILING   = int(os.environ.get("TFRRS_ID_CEILING", 100_000))
SPORTS       = ("XC", "TF")
SOURCE_TFRRS = "tfrrs"
BATCH        = 5000

# How far past the highest id we have ever seen to seed.
AHEAD = int(os.environ.get("TFRRS_SEED_AHEAD", 2000))


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


def ceilingFor(conn):
    """How high to seed: AHEAD past the highest id we have ever seen.

    ★ FROM THE DATA, NOT A CONSTANT. Sources checked: the queue (what we have
      asked), results and results_tf (what answered), and meets_tfrrs (real
      meets, including ones with no results). Whichever is highest wins, plus
      AHEAD, floored at ID_CEILING so an empty database still gets a full
      sweep.
    """
    highest = ID_CEILING - AHEAD
    with conn.cursor() as cur:
        for sql in (
            "SELECT max(meet_id) FROM meet_queue WHERE source = 'tfrrs'",
            "SELECT max(meet_id) FROM results    WHERE source = 'tfrrs'",
            "SELECT max(meet_id) FROM results_tf WHERE source = 'tfrrs'",
            "SELECT max(meet_id) FROM meets_tfrrs",
        ):
            try:
                cur.execute(sql)
                got = cur.fetchone()[0]
            except Exception:                              # noqa: BLE001
                conn.rollback()
                continue
            if got and got > highest:
                highest = got
    conn.rollback()
    return highest + AHEAD


def seed(conn, ceiling=None, verbose=True):
    """Seed 0..ceiling for both sports. Idempotent; returns the ceiling used.

    ! CALLED BY THE LAUNCHER TOO, so nobody has to remember to run this file
      before a scrape (owner, 2026-09-18: "do I need to manually run prefill
      for tfrrs or can I just run the launch_tfrrs").
    """
    ceiling = ceiling or ceilingFor(conn)
    lo = 0
    while lo <= ceiling:
        hi = min(lo + BATCH - 1, ceiling)
        _seedRange(conn, lo, hi)
        conn.commit()
        if verbose and (hi % 50000 < BATCH or hi == ceiling):
            print(f"[PREFILL] seeded up to {hi:,}", flush=True)
        lo = hi + 1
    return ceiling


# main
# Purpose: Seed the range in batches, committing each so a crash keeps
#          progress and the giant insert never sits in one transaction.
def main():
    with getConn() as conn:
        ceiling = seed(conn)
    print(f"[PREFILL] done up to {ceiling:,}", flush=True)


if __name__ == "__main__":
    main()