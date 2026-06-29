# Project: xc-predictor
# File:    backfill/classify_tf_done_rows_v2.py
# Purpose: READ-ONLY. The v1 classify over-counted "anet meets with results but
#          no meta" because it filtered the QUEUE on source='anet' but then
#          checked results_tf for ANY row with that meet_id — so TFRRS results
#          sharing an integer meet_id (disjoint id space) bled into the anet
#          count. This version filters EVERY join on source='anet': the queue,
#          the results_tf EXISTS, and the meets_tf_meta EXISTS. The result is the
#          TRUE anet-only picture. Writes nothing.
#
# Note on meets_tf_meta: it has no source column (anet-only table), so every row
# in it is anet by construction — no source filter needed there. results_tf DOES
# have source, and that's where the cross-source bleed happened, so it's filtered.

import sys
sys.path.insert(0, "scripts")

from database import initPool, getConn, closePool


# tfDoneCountsSourceCorrect
# Purpose: For anet TF meets at scraped=1, count results/meta presence using only
#          ANET results_tf rows. The r.source='anet' predicate inside the EXISTS
#          is the whole fix — it stops a TFRRS result under the same meet_id from
#          counting as "this anet meet has results".
# Arguments:
#           conn: open connection (read-only)
# Output:   dict of counts
def tfDoneCountsSourceCorrect(conn):
    sql = """
        WITH done AS (
            SELECT meet_id
            FROM   meet_queue
            WHERE  sport = 'TF' AND source = 'anet' AND scraped = 1
        )
        SELECT
            COUNT(*)                                              AS total_done,
            COUNT(*) FILTER (WHERE has_anet_results)             AS with_anet_results,
            COUNT(*) FILTER (WHERE has_meta)                     AS with_meta,
            COUNT(*) FILTER (WHERE has_anet_results AND NOT has_meta) AS anet_results_no_meta,
            COUNT(*) FILTER (WHERE NOT has_anet_results AND NOT has_meta) AS neither
        FROM (
            SELECT
                d.meet_id,
                EXISTS (
                    SELECT 1 FROM results_tf r
                    WHERE r.meet_id = d.meet_id
                      AND r.source = 'anet'          -- <-- THE FIX: anet results only
                ) AS has_anet_results,
                EXISTS (
                    SELECT 1 FROM meets_tf_meta m
                    WHERE m.meet_id = d.meet_id      -- anet-only table; no source col
                ) AS has_meta
            FROM done d
        ) x
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, cur.fetchone()))


# crossSourceBleed
# Purpose: Quantify exactly how much the v1 over-count was: anet-queue meets at
#          scraped=1 that have NO anet results but DO have tfrrs results under the
#          same id. These are the phantom "results but no meta" rows v1 reported.
# Arguments:
#           conn: open connection
# Output:   int count
def crossSourceBleed(conn):
    sql = """
        WITH done AS (
            SELECT meet_id FROM meet_queue
            WHERE sport = 'TF' AND source = 'anet' AND scraped = 1
        )
        SELECT COUNT(*)
        FROM done d
        WHERE NOT EXISTS (SELECT 1 FROM results_tf r
                          WHERE r.meet_id = d.meet_id AND r.source = 'anet')
          AND EXISTS     (SELECT 1 FROM results_tf r
                          WHERE r.meet_id = d.meet_id AND r.source = 'tfrrs')
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


# main
def main():
    initPool()
    try:
        with getConn() as conn:
            print("Source-correct classify of anet TF rows at scraped=1")
            print("(results_tf EXISTS filtered to source='anet')\n")
            c = tfDoneCountsSourceCorrect(conn)
            total = c["total_done"]

            print(f"    total at scraped=1:             {total:,}")
            print(f"    have ANET results_tf rows:      {c['with_anet_results']:,}")
            print(f"    have meets_tf_meta rows:        {c['with_meta']:,}")
            print(f"    anet results but NO meta:       {c['anet_results_no_meta']:,}")
            print(f"    neither anet results nor meta:  {c['neither']:,}")

            bleed = crossSourceBleed(conn)
            print(f"\n    [v1 artifact] anet-queue meets with NO anet results but")
            print(f"    WITH tfrrs results under same id: {bleed:,}")
            print(f"    (these were the phantom 'results but no meta' in v1)")

            print("\n--- read ---")
            print("    'anet results but NO meta' is the REAL bucket to explain.")
            print("    If small -> it's the genuinely-empty old anet meets (e.g. 27256,")
            print("    HasResults=0 on anet now). If large -> dig further.")

    finally:
        closePool()


if __name__ == "__main__":
    main()