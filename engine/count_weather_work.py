# Project: xc-predictor
# File:    scripts/count_weather_work.py
# Purpose: READ-ONLY, fast. Answer "how many weather API calls will the backfill
#          cost?" WITHOUT the slow results/results_tf join the real work query
#          uses (that join only exists to attach a DATE; a COUNT doesn't need
#          it). Counts distinct meet-locations with GPS per source -- the
#          per-meet call-count upper bound (the real run skips meets whose
#          results carry no parseable date, so actual <= this). Writes nothing.
# USAGE:  python scripts/count_weather_work.py
# ============================================================================
import sys
sys.path.insert(0, "scripts")
from database import getConn, initPool


def _scalar(cur, sql):
    cur.execute(sql)
    return cur.fetchone()[0]


def main():
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        print("counting distinct meet-locations with GPS (no results join)...")

        anet_xc = _scalar(cur, """
            SELECT count(DISTINCT meet_id) FROM meets
            WHERE gps_lat IS NOT NULL AND gps_long IS NOT NULL
        """)
        print(f"  anet XC   (meets)       : {anet_xc:,}")

        anet_tf = _scalar(cur, """
            SELECT count(DISTINCT meet_id) FROM meets_tf
            WHERE gps_lat IS NOT NULL AND gps_long IS NOT NULL
        """)
        print(f"  anet TF   (meets_tf)    : {anet_tf:,}")

        tfrrs_xc = _scalar(cur, """
            SELECT count(DISTINCT meet_id) FROM meets_tfrrs
            WHERE sport = 'XC'
              AND gps_lat IS NOT NULL AND gps_long IS NOT NULL
        """)
        print(f"  tfrrs XC  (meets_tfrrs) : {tfrrs_xc:,}")

        total = anet_xc + anet_tf + tfrrs_xc
        conn.rollback()

    print(f"\n  TOTAL meet-locations (~API calls, per-meet): {total:,}")
    print(f"  monthly plan budget (Standard) : 1,000,000")
    if total > 1_000_000:
        print("  => OVER the 1M plan on the per-meet approach. Grid-dedup needed.")
    else:
        print("  => under 1M on the per-meet approach.")
    print("\n  NOTE: this is an UPPER BOUND. The real backfill also drops meets")
    print("  whose results have no parseable date, so actual calls <= this.")
    print("  It also does NOT reflect grid-dedup, which would cut it further.")


if __name__ == "__main__":
    main()