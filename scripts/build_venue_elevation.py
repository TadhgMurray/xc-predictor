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
    for attempt in range(2):
        try:
            with urllib.request.urlopen(f"{_USGS}?{q}", timeout=8) as r:
                v = json.load(r).get("value")
            if v is None:
                return key, None
            v = float(v)
            return key, (v if -500 < v < 9000 else None)
        except Exception as exc:                             # noqa: BLE001
            _usgs_err["last"] = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0 * (attempt + 1))
    return key, None


_usgs_err = {"last": ""}


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


_CHECK_XC = ("Woodward Park", "Mt. San Antonio", "Great Park",
             "Crystal Springs", "Brigham Young", "Lakeside Park",
             "Toka Sticks", "Glendoveer", "Apalachee", "Detweiller")


def _check():
    """The venues whose heights a person knows, and the shape of the table:
    Woodward Park (Fresno) is ~90 m, Mt. SAC ~150 m, Great Park (Irvine)
    ~70 m, Crystal Springs ~90 m, BYU (Provo) ~1,400 m, Glendoveer
    (Portland) ~80 m, Simplot / Holt Arena (Pocatello) ~1,360 m."""
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT source, count(*), round(avg(elevation_m)::numeric),
                              count(*) FILTER (WHERE elevation_m > 1200)
                       FROM venue_elevation GROUP BY source""")
        for src, n, mean, high in cur.fetchall():
            print(f"  {src:<14} {n:>8,} venues  mean {mean:>6} m  {high:>6,} above 1200 m")
        cur.execute("SELECT elevation_m FROM venue_elevation WHERE key = 'TF:loc:113310'")
        r = cur.fetchone()
        print(f"  Simplot / Holt Arena (TF:loc:113310): "
              f"{f'{r[0]:.0f} m' if r else 'MISSING'}   (expect ~1,360)")
        for name in _CHECK_XC:
            cur.execute("""SELECT cc.course_name, ve.elevation_m
                           FROM course_canonical cc
                           JOIN venue_elevation ve ON ve.key = 'XC:' || cc.canonical_id::text
                           WHERE cc.course_name ILIKE %s
                           ORDER BY cc.course_name LIMIT 2""", (f"%{name}%",))
            rows = cur.fetchall()
            if not rows:
                print(f"  {name:<20} (no course with that name has an elevation)")
            for cn, e in rows:
                print(f"  {cn[:44]:<44} {e:>7.0f} m")
        cur.execute("""SELECT key, elevation_m FROM venue_elevation
                       ORDER BY elevation_m DESC LIMIT 5""")
        print("  highest five (a US track above ~3,100 m is a bad coordinate):")
        for k, e in cur.fetchall():
            name = None
            try:
                if k.startswith("TF:loc:"):
                    cur.execute("""SELECT meet_name, state, count(*) FROM meets_tf
                                   WHERE location_id = %s GROUP BY 1, 2
                                   ORDER BY 3 DESC LIMIT 1""", (int(k.split(":")[2]),))
                else:
                    cur.execute("""SELECT course_name, NULL, 0 FROM course_canonical
                                   WHERE canonical_id = %s LIMIT 1""", (int(k.split(":")[1]),))
                r = cur.fetchone()
                name = f"{r[0]} ({r[1]})" if r and r[1] else (r[0] if r else None)
            except Exception:                                # noqa: BLE001
                conn.rollback()
            print(f"    {k:<16} {e:>6.0f} m   {name or '?'}")
        cur.execute("""SELECT count(*) FROM venue_elevation
                       WHERE elevation_m < -50 OR elevation_m > 4500""")
        print(f"  implausible (< -50 m or > 4,500 m): {cur.fetchone()[0]:,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="spot-check known venues and the distribution")
    args = ap.parse_args()
    if args.check:
        _check()
        return
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_DDL)
        conn.commit()
        cur.execute("SELECT key FROM venue_elevation")
        have = {r[0] for r in cur.fetchall()}
        cur.execute(_WANT)
        want = [(k, float(la), float(lo)) for k, la, lo in cur.fetchall()
                if k not in have]
        print(f"  venue_elevation: {len(have):,} known, {len(want):,} to fetch",
              flush=True)
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
        print(f"    USGS: {len(us):,} US venues, {_WORKERS} at a time; "
              f"{len(rest):,} outside the US go to open-meteo", flush=True)
        from concurrent.futures import ThreadPoolExecutor
        done, misses, t0 = 0, [], time.time()
        with ThreadPoolExecutor(_WORKERS) as ex:
            for i in range(0, len(us), 120):
                chunk = us[i:i + 120]
                got = list(ex.map(_usgsOne, chunk))
                hits = [(k, v, "usgs") for k, v in got if v is not None]
                cur.executemany(ins, hits)
                conn.commit()
                misses += [p for p, (k, v) in zip(chunk, got) if v is None]
                done += len(chunk)
                el = time.time() - t0
                print(f"    USGS {done:,} / {len(us):,}  hits {done - len(misses):,}"
                      f"  misses {len(misses):,}  [{el:.0f}s"
                      f"{(', ' + _usgs_err['last']) if _usgs_err['last'] else ''}]",
                      flush=True)
                # ! IF USGS IS NOT ANSWERING, STOP ASKING. Half misses after
                #   the first few hundred means blocked or down; the rest of
                #   the country goes to open-meteo instead of grinding
                #   through timeouts.
                if done >= 360 and len(misses) * 2 > done:
                    print("    USGS is mostly failing -- the remaining "
                          f"{len(us) - done:,} go to open-meteo", flush=True)
                    misses += us[done:]
                    break
        rest += misses
        if rest:
            print(f"    open-meteo: {len(rest):,} venues in batches of {_BATCH}")
            for i in range(0, len(rest), _BATCH):
                chunk = rest[i:i + _BATCH]
                elev = _meteoBatch(chunk)
                cur.executemany(ins, [(p[0], float(e), "open-meteo")
                                      for p, e in zip(chunk, elev) if e is not None])
                conn.commit()
                print(f"    open-meteo {i + len(chunk):,} / {len(rest):,}  "
                      f"(pace {_meteo_pace['s']:.0f}s)", flush=True)
        cur.execute("SELECT count(*), max(elevation_m), "
                    "count(*) FILTER (WHERE elevation_m > 1200) FROM venue_elevation")
        n, mx, high = cur.fetchone()
        print(f"  venue_elevation: {n:,} venues, highest {mx:.0f} m, "
              f"{high:,} above 1200 m")


if __name__ == "__main__":
    main()
