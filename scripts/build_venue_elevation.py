"""
build_venue_elevation.py -- one elevation per solve cell, from the venue's
stored coordinates, for the joint solve's altitude term (issue 172).

    python scripts/build_venue_elevation.py            # build / top up
    python scripts/build_venue_elevation.py --report   # counts only

Writes venue_elevation (key, elevation_m, source). The key is the cell
key's venue part: 'XC:<canonical_id>' (every distance at a course shares
it) and 'TF:loc:<location_id>' (indoor and outdoor share it). Elevations
come from the Open-Meteo elevation API (free, no key, 100 points per
call, 90 m SRTM) for every venue with coordinates; meets_tf_meta's own
altitude_meters is taken first where the tfrrs feed supplied one. Only
venues not yet in the table are fetched, so a rerun is cheap.
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "scripts")
from database import getConn                                   # noqa: E402

_API = "https://api.open-meteo.com/v1/elevation"
_BATCH = 100
# ! THE FREE TIER RATE-LIMITS BY THE MINUTE and answered 429 on the second
#   call at full speed (2026-09-04). One call a second, a Retry-After
#   honoured when sent, a minute's wait otherwise, and every batch is
#   COMMITTED as it lands so a stop keeps what was fetched.
_PACE_S = 1.2

_DDL = """
CREATE TABLE IF NOT EXISTS venue_elevation (
    key          text  PRIMARY KEY,
    elevation_m  real  NOT NULL,
    source       text  NOT NULL
)
"""

_WANT = """
    SELECT 'XC:' || canonical_id::text AS key,
           avg(gps_lat) AS lat, avg(gps_long) AS lon
    FROM   course_canonical
    WHERE  gps_lat IS NOT NULL AND gps_long IS NOT NULL
    GROUP  BY canonical_id
    UNION ALL
    SELECT 'TF:loc:' || location_id::text,
           avg(gps_lat), avg(gps_long)
    FROM   meets_tf
    WHERE  location_id IS NOT NULL AND location_id <> 0
      AND  gps_lat IS NOT NULL AND gps_long IS NOT NULL
    GROUP  BY location_id
"""

_TF_META = """
    SELECT 'TF:loc:' || location_id::text, avg(altitude_meters)
    FROM   meets_tf_meta
    WHERE  location_id IS NOT NULL AND altitude_meters IS NOT NULL
    GROUP  BY location_id
"""


def _fetch(points):
    lat = ",".join(f"{p[1]:.5f}" for p in points)
    lon = ",".join(f"{p[2]:.5f}" for p in points)
    url = f"{_API}?{urllib.parse.urlencode({'latitude': lat, 'longitude': lon})}"
    for attempt in range(8):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)["elevation"]
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = int(exc.headers.get("Retry-After") or 60)
            else:
                wait = 5 * (attempt + 1)
            print(f"    HTTP {exc.code}; waiting {wait}s", flush=True)
            time.sleep(wait)
        except Exception as exc:                             # noqa: BLE001
            wait = 5 * (attempt + 1)
            print(f"    fetch failed ({exc}); retry in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError("the elevation API refused eight times")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_DDL)
        conn.commit()
        cur.execute("SELECT key FROM venue_elevation")
        have = {r[0] for r in cur.fetchall()}
        cur.execute(_WANT)
        want = [(k, float(la), float(lo)) for k, la, lo in cur.fetchall()
                if k not in have]
        print(f"  venue_elevation: {len(have):,} known, {len(want):,} to fetch")
        if args.report or not want:
            return
        # the feed's own altitude first
        cur.execute(_TF_META)
        meta = {k: float(v) for k, v in cur.fetchall()}
        ins = ("INSERT INTO venue_elevation (key, elevation_m, source) "
               "VALUES (%s, %s, %s) ON CONFLICT (key) DO NOTHING")
        rows = [(k, meta[k], "meets_tf_meta") for k, _, _ in want if k in meta]
        if rows:
            cur.executemany(ins, rows)
            conn.commit()
            print(f"    {len(rows):,} from meets_tf_meta")
        todo = [p for p in want if p[0] not in meta]
        for i in range(0, len(todo), _BATCH):
            chunk = todo[i:i + _BATCH]
            elev = _fetch(chunk)
            got = [(p[0], float(e), "open-meteo") for p, e in zip(chunk, elev)
                   if e is not None]
            cur.executemany(ins, got)
            conn.commit()                         # keep what landed
            if (i // _BATCH) % 25 == 0:
                print(f"    {i + len(chunk):,} / {len(todo):,}", flush=True)
            time.sleep(_PACE_S)
        cur.execute("SELECT count(*), max(elevation_m), "
                    "count(*) FILTER (WHERE elevation_m > 1200) FROM venue_elevation")
        n, mx, high = cur.fetchone()
        print(f"  venue_elevation: {n:,} venues, highest {mx:.0f} m, "
              f"{high:,} above 1200 m")


if __name__ == "__main__":
    main()
