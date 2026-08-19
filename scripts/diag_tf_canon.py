# Project: xc-predictor
# File:    scripts/diag_borrow_check3.py
# Purpose: READ-ONLY, fast. Fix the biased sample: ORDER BY random() (cheap on
#          77k meets_tfrrs) instead of lowest-meet_id, and resolve canon against
#          meets_tf_meta (PK). Gives the TRUE borrowable rate for tfrrs TF.
#          Bounded: 500 random meets, indexed results_tf seek each, PK join.
# USAGE:  python scripts\diag_borrow_check3.py
# ============================================================================
import sys
sys.path.insert(0, "scripts")
from database import getConn, initPool

SAMPLE = 500


def main():
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""
            WITH sample AS (
                SELECT t.meet_id AS tf_meet,
                       (SELECT r.canon_meet_id FROM results_tf r
                        WHERE r.meet_id = t.meet_id AND r.source = 'tfrrs'
                          AND r.canon_meet_id IS NOT NULL
                        LIMIT 1) AS canon
                FROM meets_tfrrs t
                WHERE t.sport = 'TF' AND t.gps_lat IS NULL
                ORDER BY random()
                LIMIT %s
            )
            SELECT
              count(*)                                            AS sampled,
              count(*) FILTER (WHERE s.canon IS NOT NULL)         AS linked,
              count(*) FILTER (WHERE mm.meet_id IS NOT NULL)      AS canon_in_meta,
              count(*) FILTER (WHERE mm.gps_lat IS NOT NULL)      AS twin_has_gps,
              count(*) FILTER (WHERE mm.location_id IS NOT NULL)  AS twin_has_locid
            FROM sample s
            LEFT JOIN meets_tf_meta mm ON mm.meet_id = s.canon
        """, (SAMPLE,))
        sm, lk, inmeta, gps, loc = cur.fetchone()
        conn.rollback()

        print(f"  random sample of null-gps TF meets : {sm}")
        print(f"    canon-linked                     : {lk}  ({100*lk/sm:.0f}%)")
        print(f"    canon resolves in meets_tf_meta  : {inmeta}")
        print(f"      twin HAS gps  (borrowable)     : {gps}  ({100*gps/sm:.0f}% of all)")
        print(f"      twin HAS location_id           : {loc}")
        if lk:
            print(f"    of the LINKED slice, borrowable  : {100*gps/lk:.0f}%")
        est = int(60463 * gps / sm)
        print(f"\n  => extrapolated borrowable TF meets: ~{est:,} of 60,463 "
              f"(rest need geocoding)")


if __name__ == "__main__":
    main()