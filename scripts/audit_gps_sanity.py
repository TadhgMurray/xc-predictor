# Project: xc-predictor
# File:    scripts/audit_gps_sanity.py
# Purpose: READ-ONLY. Presence != correctness. Flag stored gps that is
#          implausible: off-planet range, (0,0) null-island, or a US-state row
#          whose coord falls OUTSIDE that state's bounding box (catches the
#          wrong-same-name pins). Overseas ('OS'/'os'/'CS' + foreign) can't be
#          box-checked without a country map, so they're listed for eyeballing.
#          Writes a CSV of every suspect; changes nothing.
# USAGE:  python scripts/audit_gps_sanity.py
# ============================================================================
import csv, os, sys
sys.path.insert(0, "scripts")
from database import getConn, initPool

_OUT = "gps_suspects.csv"
_TABLES = ("meets", "meets_tf", "meets_tf_meta", "meets_tfrrs")

# generous US state boxes (lat_min, lat_max, lon_min, lon_max)
_BBOX = {
 "AL":(30.1,35.1,-88.5,-84.9),"AK":(51,72,-179,-129),"AZ":(31.3,37.1,-114.9,-109.0),
 "AR":(33.0,36.6,-94.7,-89.6),"CA":(32.4,42.1,-124.6,-114.0),"CO":(36.9,41.1,-109.1,-102.0),
 "CT":(40.9,42.1,-73.8,-71.7),"DE":(38.4,39.9,-75.8,-75.0),"FL":(24.4,31.1,-87.7,-79.9),
 "GA":(30.3,35.1,-85.7,-80.8),"HI":(18.9,22.3,-160.3,-154.8),"ID":(41.9,49.1,-117.3,-110.9),
 "IL":(36.9,42.6,-91.6,-86.9),"IN":(37.7,41.8,-88.1,-84.7),"IA":(40.3,43.6,-96.7,-90.1),
 "KS":(36.9,40.1,-102.1,-94.5),"KY":(36.4,39.2,-89.7,-81.8),"LA":(28.9,33.1,-94.1,-88.8),
 "ME":(43.0,47.5,-71.1,-66.9),"MD":(37.8,39.8,-79.5,-75.0),"MA":(41.1,43.0,-73.6,-69.8),
 "MI":(41.6,48.4,-90.5,-82.3),"MN":(43.4,49.5,-97.4,-89.4),"MS":(30.1,35.1,-91.7,-88.0),
 "MO":(35.9,40.7,-95.9,-89.0),"MT":(44.3,49.1,-116.1,-104.0),"NE":(39.9,43.1,-104.1,-95.2),
 "NV":(35.0,42.1,-120.1,-114.0),"NH":(42.6,45.4,-72.6,-70.5),"NJ":(38.8,41.4,-75.7,-73.8),
 "NM":(31.3,37.1,-109.1,-103.0),"NY":(40.4,45.1,-79.8,-71.8),"NC":(33.7,36.7,-84.4,-75.3),
 "ND":(45.9,49.1,-104.1,-96.5),"OH":(38.3,42.1,-84.9,-80.4),"OK":(33.6,37.1,-103.1,-94.4),
 "OR":(41.9,46.4,-124.7,-116.4),"PA":(39.6,42.6,-80.6,-74.6),"RI":(41.1,42.1,-71.9,-71.1),
 "SC":(32.0,35.3,-83.4,-78.4),"SD":(42.4,45.9,-104.1,-96.4),"TN":(34.9,36.7,-90.4,-81.6),
 "TX":(25.7,36.6,-106.7,-93.4),"UT":(36.9,42.1,-114.1,-108.9),"VT":(42.7,45.1,-73.5,-71.4),
 "VA":(36.5,39.6,-83.8,-75.1),"WA":(45.5,49.1,-124.9,-116.9),"WV":(37.1,40.7,-82.7,-77.7),
 "WI":(42.4,47.2,-93.0,-86.7),"WY":(40.9,45.1,-111.1,-104.0),
}


def _flag(state, lat, lon):
    """Return a reason string if this coord is implausible, else None."""
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return "off-planet range"
    if abs(lat) < 0.5 and abs(lon) < 0.5:
        return "null-island (0,0)"
    st = (state or "").strip().upper()
    if st in _BBOX:
        a, b, c, d = _BBOX[st]
        if not (a <= lat <= b and c <= lon <= d):
            return f"outside {st} box"
    return None                                   # overseas/unknown -> not boxed


def _auditTable(cur, table, w):
    cur.execute(f"""SELECT location_id, state, gps_lat, gps_long
                    FROM {table} WHERE gps_lat IS NOT NULL""")
    n = bad = 0
    for loc, state, lat, lon in cur.fetchall():
        n += 1
        reason = _flag(state, lat, lon)
        if reason:
            bad += 1
            w.writerow([table, loc, state, lat, lon, reason])
    print(f"  {table:<13}: {n:,} placed | {bad:,} flagged")
    return bad


def main():
    initPool()
    path = os.path.join("scripts", _OUT)
    total = 0
    with getConn() as conn, conn.cursor() as cur, \
         open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["table", "location_id", "state", "gps_lat", "gps_long", "reason"])
        print("=== GPS PLAUSIBILITY AUDIT (read-only) ===")
        for t in _TABLES:
            total += _auditTable(cur, t, w)
        conn.rollback()
    print(f"\n  wrote {path}  ({total:,} suspects)")
    if total == 0:
        print("  no range/null-island/out-of-state coords. US side is clean.")
    else:
        print("  inspect the CSV -- these point somewhere wrong.")
    print("  NOTE: overseas coords aren't box-checked; skim those in the DB "
          "or trust the city-level keyword placements.")


if __name__ == "__main__":
    main()