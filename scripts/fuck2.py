# Project: xc-predictor
# File:    backfill/check_rescrape_quality.py
# Purpose: READ-ONLY. While the TF meta-only re-scrape runs, confirm it's saving
#          CORRECT data at scale (not just at all). Checks meets_tf_meta coverage
#          (GoogleData / address / GPS / season), the meet-level field spreads,
#          and crucially that meets_tf.division holds NAMES ('Open','Invitational')
#          not gender letters ('B'/'M'/'F'). Run ~10 min after launch. Writes
#          nothing. Safe to run repeatedly as the crawl progresses.

import sys
sys.path.insert(0, "scripts")

from database import initPool, getConn, closePool


# metaCoverage
# Purpose: Of the meets_tf_meta rows written so far, what fraction have each key
#          field populated -- the capture targets we added this re-scrape.
# Arguments:
#           conn: open psycopg2 connection (read-only)
# Output:  dict of {field: (non_null_count, total)}
def metaCoverage(conn):
    sql = """
        SELECT
            COUNT(*)                                            AS total,
            COUNT(*) FILTER (WHERE google_data IS NOT NULL)     AS has_google,
            COUNT(*) FILTER (WHERE track_length IS NOT NULL)    AS track_length,
            COUNT(*) FILTER (WHERE gps_lat   IS NOT NULL)       AS has_gps,
            COUNT(*) FILTER (WHERE city      IS NOT NULL)       AS has_city,
            COUNT(*) FILTER (WHERE address   IS NOT NULL)       AS has_address,
            COUNT(*) FILTER (WHERE state     IS NOT NULL)       AS has_state,
            COUNT(*) FILTER (WHERE season_id IS NOT NULL)       AS has_season,
            COUNT(*) FILTER (WHERE venue_name IS NOT NULL)      AS has_venue,
            COUNT(*) FILTER (WHERE track_type IS NOT NULL)      AS has_tracktype,
            COUNT(*) FILTER (WHERE source = 'anet')             AS src_anet
        FROM meets_tf_meta
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        row = dict(zip(cols, cur.fetchone()))
    total = row.pop("total")
    return total, row


# divisionSanity
# Purpose: The load-bearing correctness check. meets_tf.division must be NAMES.
#          Gender letters ('B','G','M','F') leaking in = the _divisionByIdDiv
#          map failed for those meets. Reports how many distinct division values
#          look like gender letters vs real names.
# Arguments:
#           conn: open connection
# Output:  (suspicious_rows, total_rows, sample_values)
def divisionSanity(conn):
    with conn.cursor() as cur:
        # Rows whose division is a bare gender letter -> suspicious.
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE division IN ('B','G','M','F')) AS gender_like,
                COUNT(*) FILTER (WHERE division IS NULL)              AS null_div,
                COUNT(*)                                             AS total
            FROM meets_tf
            WHERE source = 'anet'
        """)
        gender_like, null_div, total = cur.fetchone()

        # A spread of the actual distinct division values, most common first.
        cur.execute("""
            SELECT division, COUNT(*) AS n
            FROM   meets_tf
            WHERE  source = 'anet'
            GROUP  BY division
            ORDER  BY n DESC
            LIMIT  20
        """)
        sample = cur.fetchall()
    return gender_like, null_div, total, sample


# progressSnapshot
# Purpose: Rough progress -- how many meets_tf_meta rows exist now (≈ meets done
#          on the meta path) so the quality numbers have context.
# Arguments:
#           conn: open connection
# Output:  (meets_tf_meta_rows, meets_tf_rows)
def progressSnapshot(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM meets_tf_meta")
        meta = cur.fetchone()[0]
        cur.execute("SELECT reltuples::bigint FROM pg_class WHERE relname = 'meets_tf'")
        tf = cur.fetchone()[0]
    return meta, tf


# main
def main():
    initPool()
    try:
        with getConn() as conn:

            meta_rows, tf_rows = progressSnapshot(conn)
            print(f"===== progress =====")
            print(f"    meets_tf_meta rows so far: {meta_rows:,}")
            print(f"    meets_tf rows (approx):    {tf_rows:,}")

            if meta_rows == 0:
                print("\n    No meets_tf_meta rows yet — wait a few minutes and re-run.")
                return

            print(f"\n===== meets_tf_meta field coverage (of {meta_rows:,}) =====")
            total, cov = metaCoverage(conn)
            for field, n in cov.items():
                pct = 100.0 * n / total if total else 0
                print(f"    {field:14} {n:>10,}  ({pct:5.1f}%)")

            print(f"\n===== meets_tf division sanity (must be NAMES) =====")
            gender_like, null_div, total, sample = divisionSanity(conn)
            print(f"    total anet rows:        {total:,}")
            print(f"    gender-letter division: {gender_like:,}  "
                  f"(should be ~0 — these are WRONG)")
            print(f"    NULL division:          {null_div:,}")
            print(f"    distinct division values (top 20):")
            for div, n in sample:
                print(f"        {str(div):24} {n:,}")

            print("\n--- read ---")
            print("    GOOD: has_google/has_gps/has_city high, src_anet=100%,")
            print("          gender-letter division ≈ 0, division values are names.")
            print("    BAD:  many gender-letter or NULL divisions, or has_google ~0%.")

    finally:
        closePool()


if __name__ == "__main__":
    main()