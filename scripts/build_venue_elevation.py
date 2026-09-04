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

_USGS = "https://epqs.nationalmap.gov/v1/json"
_METEO = "https://api.open-meteo.com/v1/elevation"
_BATCH = 100
_WORKERS = 6

# ! TWO SOURCES, NEITHER HAMMERED. USGS's point service (3DEP, US only) has
#   no published cap and answers one point a call, so a small thread pool
#   walks it; a venue outside the US or a USGS miss goes to Open-Meteo in
#   batches of 100, whose free tier counts every coordinate as a call and
#   answered 429 at one request a second (2026-09-04). That client now
#   slows ITSELF: a 429 doubles the wait between calls (up to a minute)
#   and every clean answer eases it back. Every batch is committed as it
#   lands, so a stop keeps what was fetched.
_meteo_pace = {"s": 3.0}

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


def _inUS(lat, lon):
    return (24.0 <= lat <= 71.5 and -179.9 <= lon <= -66.0) or \
           (18.5 <= lat <= 22.5 and -160.5 <= lon <= -154.5)     # Hawaii


def _usgsOne(point):
    key, lat, lon = point
    q = urllib.parse.urlencode({"x": f"{lon:.6f}", "y": f"{lat:.6f}",
                                "units": "Meters", "output": "json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(f"{_USGS}?{q}", timeout=20) as r:
                v = json.load(r).get("value")
            if v is None:
                return key, None
            v = float(v)
            return key, (v if -500 < v < 9000 else None)
        except Exception:                                    # noqa: BLE001
            time.sleep(1.5 * (attempt + 1))
    return key, None


def _meteoBatch(points):
    lat = ",".join(f"{p[1]:.5f}" for p in points)
    lon = ",".join(f"{p[2]:.5f}" for p in points)
    url = f"{_METEO}?{urllib.parse.urlencode({'latitude': lat, 'longitude': lon})}"
    for _attempt in range(12):
        time.sleep(_meteo_pace["s"])
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                out = json.load(r)["elevation"]
            _meteo_pace["s"] = max(3.0, _meteo_pace["s"] * 0.8)
            return out
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                _meteo_pace["s"] = min(60.0, _meteo_pace["s"] * 2)
                print(f"    open-meteo 429: pacing {_meteo_pace['s']:.0f}s "
                      f"between calls", flush=True)
            else:
                print(f"    open-meteo HTTP {exc.code}; retrying", flush=True)
        except Exception as exc:                             # noqa: BLE001
            print(f"    open-meteo failed ({exc}); retrying", flush=True)
    return [None] * len(points)


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
        ins = ("INSERT INTO venue_elevation (key, elevation_m, source) "
               "VALUES (%s, %s, %s) ON CONFLICT (key) DO NOTHING")
        # the feed's own altitude first
        cur.execute(_TF_META)
        meta = {k: float(v) for k, v in cur.fetchall()}
        rows = [(k, meta[k], "meets_tf_meta") for k, _, _ in want if k in meta]
        if rows:
            cur.executemany(ins, rows)
            conn.commit()
            print(f"    {len(rows):,} from meets_tf_meta")
        todo = [p for p in want if p[0] not in meta]
        # USGS for the US, a few at a time
        us = [p for p in todo if _inUS(p[1], p[2])]
        rest = [p for p in todo if not _inUS(p[1], p[2])]
        print(f"    USGS: {len(us):,} venues, {_WORKERS} at a time")
        from concurrent.futures import ThreadPoolExecutor
        done, misses = 0, []
        with ThreadPoolExecutor(_WORKERS) as ex:
            for i in range(0, len(us), 200):
                chunk = us[i:i + 200]
                got = list(ex.map(_usgsOne, chunk))
                cur.executemany(ins, [(k, v, "usgs") for k, v in got if v is not None])
                conn.commit()
                misses += [p for p, (k, v) in zip(chunk, got) if v is None]
                done += len(chunk)
                if (i // 200) % 10 == 0:
                    print(f"    {done:,} / {len(us):,}  ({len(misses):,} misses)",
                          flush=True)
        rest += misses
        if rest:
            print(f"    open-meteo: {len(rest):,} venues in batches of {_BATCH}")
            for i in range(0, len(rest), _BATCH):
                chunk = rest[i:i + _BATCH]
                elev = _meteoBatch(chunk)
                cur.executemany(ins, [(p[0], float(e), "open-meteo")
                                      for p, e in zip(chunk, elev) if e is not None])
                conn.commit()
                if (i // _BATCH) % 20 == 0:
                    print(f"    {i + len(chunk):,} / {len(rest):,}", flush=True)
        cur.execute("SELECT count(*), max(elevation_m), "
                    "count(*) FILTER (WHERE elevation_m > 1200) FROM venue_elevation")
        n, mx, high = cur.fetchone()
        print(f"  venue_elevation: {n:,} venues, highest {mx:.0f} m, "
              f"{high:,} above 1200 m")


if __name__ == "__main__":
    main()
