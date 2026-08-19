# Project: xc-predictor
# File:    scripts/diag_tfrrs_geo.py
# Purpose: READ-ONLY, FAST (<~30s). Why are tfrrs meets going to slow Nominatim?
#          Avoids per-row correlated scans of results_tf (193M). Block 2 samples
#          a fixed number of meets and probes each ONE indexed lookup, then
#          extrapolates -- bounded work, not 60k full probes. Writes nothing.
# USAGE:  python scripts/diag_tfrrs_geo.py
# ============================================================================
import sys
sys.path.insert(0, "scripts")
from database import getConn, initPool

SAMPLE = 400          # meets probed in block 2 (bounded, indexed -> quick)


def q1(cur):
    # single grouped scan of meets_tfrrs (~77k rows) -- fast.
    print("  [1] clue coverage on null-gps tfrrs meets:")
    cur.execute("""
        SELECT sport, count(*),
               count(*) FILTER (WHERE venue_name IS NOT NULL),
               count(*) FILTER (WHERE city IS NOT NULL),
               count(*) FILTER (WHERE state IS NOT NULL),
               count(*) FILTER (WHERE location_raw IS NOT NULL),
               count(*) FILTER (WHERE city IS NULL AND state IS NULL
                                  AND venue_name IS NULL AND location_raw IS NULL)
        FROM meets_tfrrs WHERE gps_lat IS NULL
        GROUP BY sport ORDER BY sport
    """)
    print(f"    {'sport':<5}{'null':>8}{'venue':>8}{'city':>8}{'state':>8}{'raw':>8}{'noclue':>8}")
    for r in cur.fetchall():
        print(f"    {r[0]:<5}{r[1]:>8,}{r[2]:>8,}{r[3]:>8,}{r[4]:>8,}{r[5]:>8,}{r[6]:>8,}")


def q2(cur):
    # BOUNDED: take SAMPLE null-gps meets per sport, probe each with ONE indexed
    # lookup, report the borrowable rate, extrapolate. No full-table correlation.
    print(f"\n  [2] canon-borrow rate (sampled {SAMPLE}/sport, extrapolated):")
    for sport, res, twin in (("TF","results_tf","meets_tf"), ("XC","results","meets")):
        cur.execute(f"""SELECT meet_id FROM meets_tfrrs
                        WHERE sport='{sport}' AND gps_lat IS NULL
                        ORDER BY meet_id LIMIT {SAMPLE}""")
        ids = [r[0] for r in cur.fetchall()]
        if not ids:
            print(f"    {sport}: no null-gps meets"); continue
        linked = borrowable = 0
        for mid in ids:
            cur.execute(f"""SELECT canon_meet_id FROM {res}
                            WHERE meet_id=%s AND source='tfrrs'
                              AND canon_meet_id IS NOT NULL LIMIT 1""", (mid,))
            row = cur.fetchone()
            if not row: continue
            linked += 1
            cur.execute(f"""SELECT 1 FROM {twin}
                            WHERE meet_id=%s AND gps_lat IS NOT NULL LIMIT 1""", (row[0],))
            if cur.fetchone(): borrowable += 1
        print(f"    {sport}: of {len(ids)} sampled -> {linked} canon-linked, "
              f"{borrowable} borrowable ({100*borrowable/len(ids):.0f}%)")


def q3(cur):
    print("\n  [3] sample null-gps rows (what the geocoder sees):")
    cur.execute("""SELECT sport, venue_name, city, state, location_raw
                   FROM meets_tfrrs
                   WHERE gps_lat IS NULL AND (venue_name IS NOT NULL
                         OR location_raw IS NOT NULL OR city IS NOT NULL)
                   ORDER BY meet_id LIMIT 20""")
    for sp, v, c, st, raw in cur.fetchall():
        print(f"    [{sp}] venue={v!r} city={c!r} state={st!r} raw={raw!r}")


def q4(cur):
    print("\n  [4] top states among null-gps tfrrs with a state:")
    cur.execute("""SELECT upper(trim(state)), count(*) FROM meets_tfrrs
                   WHERE gps_lat IS NULL AND state IS NOT NULL
                   GROUP BY 1 ORDER BY 2 DESC LIMIT 12""")
    for s, n in cur.fetchall():
        print(f"    {s:<6} {n:,}")


def main():
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        q1(cur); q2(cur); q3(cur); q4(cur)
        conn.rollback()


if __name__ == "__main__":
    main()