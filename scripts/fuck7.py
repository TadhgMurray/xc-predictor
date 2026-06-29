# Project: xc-predictor
# File:    scripts/reset_xc_neither_full.py
# Purpose: Reset the FULL XC "neither" bucket to scraped=0 so the full XC path
#          re-scrapes it. We confirmed this is NOT a saver bug: ~10% of these
#          still have live data on anet (recoverable), the rest are anet-
#          depopulated and will simply re-mark empty. This is the recovery pass
#          for the live slice; the empties cost only re-fetches.
#
# INVARIANT: identity is (meet_id, sport, source). This flips ONLY the
# (sport='XC', source='anet') rows; any TF/tfrrs row sharing an integer is left
# alone.
#
# WRITES (bulk UPDATE). Builds the bucket set-based (one scan each over results /
# meets), prints the count, prompts y/N. RUN WITH THE LAUNCHER OFF — the `meets`
# scan holds a read lock a concurrent launcher startup would wedge on (until the
# _migrateLocationID guard is in).
#
# Run from project root:  python scripts/reset_xc_neither_full.py

import sys
sys.path.insert(0, "scripts")
from database import getConn


# _buildBucket
# Purpose:   Materialize the neither bucket: scraped=1 XC-anet queue meets with
#            NO anet results and NO meets row. Set-based — one scan each into a
#            DISTINCT-id temp table, then LEFT JOIN (no per-row EXISTS).
# Arguments: cur — open cursor.
# Output:    None (creates TEMP TABLE neither(meet_id)).
def _buildBucket(cur) -> None:
    for t in ("neither", "r_xc", "m_xc"):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    cur.execute("CREATE TEMP TABLE r_xc AS "
                "SELECT DISTINCT meet_id FROM results WHERE source='anet'")
    cur.execute("CREATE TEMP TABLE m_xc AS "
                "SELECT DISTINCT meet_id FROM meets "
                "WHERE source='anet' AND meet_id IS NOT NULL")
    cur.execute("""
        CREATE TEMP TABLE neither AS
        SELECT q.meet_id
        FROM meet_queue q
        LEFT JOIN r_xc r ON r.meet_id = q.meet_id
        LEFT JOIN m_xc m ON m.meet_id = q.meet_id
        WHERE q.sport='XC' AND q.source='anet' AND q.scraped=1
          AND r.meet_id IS NULL AND m.meet_id IS NULL
    """)


# _reset
# Purpose:   Flip the bucket's XC-anet queue rows to scraped=0. sport+source
#            paired and scraped=1 guarded; joins meet_queue to the bucket temp.
# Arguments: cur — open cursor.
# Output:    number of queue rows updated.
def _reset(cur) -> int:
    cur.execute("""
        UPDATE meet_queue q
        SET scraped = 0
        FROM neither n
        WHERE q.meet_id = n.meet_id
          AND q.sport='XC' AND q.source='anet' AND q.scraped=1
    """)
    return cur.rowcount


# main
# Purpose:   Build the bucket, show the count, confirm, flip, commit.
def main() -> None:
    with getConn() as conn:
        cur = conn.cursor()
        print("building bucket (one scan over results / meets, a few sec)...",
              flush=True)
        _buildBucket(cur)
        cur.execute("SELECT COUNT(*) FROM neither")
        n = cur.fetchone()[0]
        print(f"XC neither meets to reset: {n:,}")
        if not n:
            print("nothing to do.")
            return
        if input(f"flip {n:,} XC-anet rows to scraped=0? [y/N] ").strip().lower() != "y":
            print("aborted, no changes.")
            return
        updated = _reset(cur)
        conn.commit()
        print(f"done: {updated:,} XC-anet queue rows set to scraped=0.")


if __name__ == "__main__":
    main()