# Project: xc-predictor
# File:    scripts/count_all_four.py
# Purpose: READ-ONLY. The FOUR streams, no more dropping one: anet XC, anet TF,
#          tfrrs XC, tfrrs TF. For each: meets with GPS, meets still null, and
#          how many already have weather. Writes nothing.
# USAGE:  python scripts/count_all_four.py
# ============================================================================
import sys
sys.path.insert(0, "scripts")
from database import getConn, initPool


def _row(cur, label, table, where, weather_source):
    cur.execute(f"""
        SELECT count(DISTINCT meet_id) FILTER (WHERE gps_lat IS NOT NULL),
               count(DISTINCT meet_id) FILTER (WHERE gps_lat IS NULL)
        FROM {table} WHERE {where}
    """)
    with_gps, null_gps = cur.fetchone()
    cur.execute("SELECT count(DISTINCT meet_id) FROM weather WHERE source = %s",
                (weather_source,))
    have_wx = cur.fetchone()[0]
    conn_rollback()
    return (label, with_gps or 0, null_gps or 0, have_wx or 0)


def main():
    initPool()
    global conn_rollback
    with getConn() as conn, conn.cursor() as cur:
        conn_rollback = conn.rollback
        streams = [
            _row(cur, "anet XC",  "meets",       "TRUE",            "anet"),
            _row(cur, "anet TF",  "meets_tf",    "TRUE",            "anet_tf"),
            _row(cur, "tfrrs XC", "meets_tfrrs", "sport = 'XC'",    "tfrrs"),
            _row(cur, "tfrrs TF", "meets_tfrrs", "sport = 'TF'",    "tfrrs_tf"),
        ]

    print(f"  {'stream':<9} {'gps':>10} {'gps_null':>10} {'has_weather':>12}")
    tot_gps = tot_null = 0
    for label, gps, nogps, wx in streams:
        print(f"  {label:<9} {gps:>10,} {nogps:>10,} {wx:>12,}")
        tot_gps += gps; tot_null += nogps
    print(f"  {'TOTAL':<9} {tot_gps:>10,} {tot_null:>10,}")
    print(f"\n  weather to fetch (gps present): {tot_gps:,}   plan budget: 1,000,000")


if __name__ == "__main__":
    main()