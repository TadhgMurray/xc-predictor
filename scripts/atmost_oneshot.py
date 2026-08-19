# Project: xc-predictor
# File:    scripts/atmos_oneshot.py
# ============================================================================
# THE ONE-SHOT — run once, walk away, never open again.
#
# Closes GPS + elevation + weather for ALL FOUR streams:
#     anet XC (meets)   anet TF (meets_tf)   tfrrs XC/TF (meets_tfrrs)
#
# STAGES (each idempotent + resumable; Ctrl-C and re-run safely):
#   1 GPS   1a BORROW: canon-linked tfrrs meets copy gps + location_id from
#                      their anet twin via canon_meet_id (NO network -- the ~89%
#                      that used to force repeated geocoding).
#           1b GEOCODE: only the non-canon tfrrs residue, Census->Nominatim,
#                       cached (hits only, so a re-run retries misses).
#   2 ELEV  one elevation lookup per DISTINCT coord (static), -> altitude_meters
#           on all three venue tables.
#   3 WX    hourly weather for every GPS meet in all four streams lacking it.
#           The weather table IS the resume state.
#   4 VERIFY  final coverage table. Reads clean => done forever.
#
# USAGE:
#   $env:OPENMETEO_KEY = "your-key"
#   python scripts/atmos_oneshot.py                       # dry run: counts only
#   python scripts/atmos_oneshot.py --apply --limit 300   # smoke test
#   python scripts/atmos_oneshot.py --apply               # the real run
# ============================================================================
import argparse, datetime, json, os, re, sys, time
import requests
import psycopg2.extras
import io
import csv
import concurrent.futures as _cf
from psycopg2.extras import execute_values
sys.path.insert(0, "scripts")
from database import initPool, closePool, getConn

API_KEY = os.environ.get("OPENMETEO_KEY", "").strip()
GEO_CACHE = "atmos_geocode_cache.json"
UA = "xc-predictor-atmos (contact: tadhg)"

# Self-hosted Open-Meteo (see setup). Override with env OM_ARCHIVE if needed.
WX_ARCHIVE  = os.environ.get("OM_ARCHIVE",  "http://127.0.0.1:8080/v1/archive")
WX_FORECAST = os.environ.get("OM_FORECAST", "http://127.0.0.1:8080/v1/forecast")
SELF_HOSTED = "127.0.0.1" in WX_ARCHIVE or "localhost" in WX_ARCHIVE
ELEV_URL    = "https://customer-api.open-meteo.com/v1/elevation"
CENSUS      = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM   = "https://nominatim.openstreetmap.org/search"

WX_BATCH, ELEV_BATCH = 200, 100          # URL-safe GET sizes (elev hard cap 100)
HIST_CUTOFF_DAYS = 7
MIN_YEAR, MAX_YEAR = 1940, 2027
HOURLY = ["temperature_2m","dew_point_2m","relative_humidity_2m",
          "apparent_temperature","precipitation","weather_code",
          "surface_pressure","cloud_cover","wind_speed_10m","wind_direction_10m"]

# (label, table, sport_filter, weather-source-tag, own_date_col_or_None)
STREAMS = [
    ("anet XC",  "meets",       None, "anet",     None),
    ("anet TF",  "meets_tf",    None, "anet_tf",  None),
    ("tfrrs XC", "meets_tfrrs", "XC", "tfrrs",    "date"),
    ("tfrrs TF", "meets_tfrrs", "TF", "tfrrs_tf", "date"),
]


# ============================================================================
# STAGE 1 -- GPS  (1a borrow via canon_meet_id, 1b geocode residue)
# ============================================================================

def _ensureColumns(conn):
    cur = conn.cursor()
    for t in ("meets", "meets_tf", "meets_tfrrs"):
        cur.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS altitude_meters REAL")
    cur.execute("ALTER TABLE meets_tfrrs ADD COLUMN IF NOT EXISTS location_id BIGINT")
    conn.commit()

def _borrowSql(sport):
    # TF twins live in meets_tf; XC twins in meets. The twin table is scoped per
    # sport, which also guards against meet_id collisions across sport/source.
    if sport == "TF":
        canon = ("SELECT r.canon_meet_id FROM results_tf r "
                 "WHERE r.meet_id=t.meet_id AND r.source='tfrrs' "
                 "AND r.canon_meet_id IS NOT NULL LIMIT 1")
        twin = "meets_tf"
    else:
        canon = ("SELECT canon_meet_id FROM ("
                 " SELECT canon_meet_id FROM results    WHERE meet_id=t.meet_id AND source='tfrrs' AND canon_meet_id IS NOT NULL"
                 " UNION ALL"
                 " SELECT canon_meet_id FROM results_tf WHERE meet_id=t.meet_id AND source='tfrrs' AND canon_meet_id IS NOT NULL"
                 ") u WHERE canon_meet_id IS NOT NULL LIMIT 1")
        twin = "meets"
    return f"""
        SELECT t.meet_id, a.gps_lat, a.gps_long, a.location_id
        FROM meets_tfrrs t
        JOIN LATERAL ({canon}) l ON TRUE
        JOIN LATERAL (
            SELECT gps_lat, gps_long, location_id FROM {twin} m
            WHERE m.meet_id = l.canon_meet_id AND m.gps_lat IS NOT NULL
            LIMIT 1
        ) a ON TRUE
        WHERE t.sport = '{sport}' AND t.gps_lat IS NULL
    """

def _borrow(conn, sport, do_write):
    with conn.cursor() as cur:
        cur.execute(_borrowSql(sport))
        rows = cur.fetchall(); conn.rollback()
    print(f"  1a borrow {sport}: {len(rows):,} tfrrs meets can copy from anet twin", flush=True)
    if do_write and rows:
        with conn.cursor() as cur:
            for mid, lat, lon, loc in rows:
                cur.execute("""UPDATE meets_tfrrs SET gps_lat=%s, gps_long=%s, location_id=%s
                               WHERE meet_id=%s AND sport=%s AND gps_lat IS NULL""",
                            (lat, lon, loc, mid, sport))
            conn.commit()
    return len(rows)

def _norm(s):
    if not s: return ""
    s = "".join(c for c in s if ord(c) < 128).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s,]", " ", s)).strip()

def _geoCache():
    return json.load(open(GEO_CACHE, encoding="utf-8")) if os.path.exists(GEO_CACHE) else {}

def _geoSave(c):
    tmp = GEO_CACHE + ".tmp"; json.dump(c, open(tmp,"w",encoding="utf-8")); os.replace(tmp, GEO_CACHE)

def _census(q):
    try:
        r = requests.get(CENSUS, params={"address": q,"benchmark":"Public_AR_Current","format":"json"}, timeout=30)
        m = r.json().get("result",{}).get("addressMatches",[])
        if m: c = m[0]["coordinates"]; return float(c["y"]), float(c["x"])
    except Exception: pass
    return None

def _nominatim(q):
    try:
        r = requests.get(NOMINATIM, params={"q": q,"format":"json","limit":1},
                         headers={"User-Agent": UA}, timeout=30)
        j = r.json()
        if j: return float(j[0]["lat"]), float(j[0]["lon"])
    except Exception: pass
    return None

_GEO_OM = "https://customer-geocoding-api.open-meteo.com/v1/search"
_CENSUS_BATCH = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
_OM_WORKERS = 8

_STATE_NAME = {
 "AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California",
 "CO":"Colorado","CT":"Connecticut","DE":"Delaware","FL":"Florida","GA":"Georgia",
 "HI":"Hawaii","ID":"Idaho","IL":"Illinois","IN":"Indiana","IA":"Iowa","KS":"Kansas",
 "KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland","MA":"Massachusetts",
 "MI":"Michigan","MN":"Minnesota","MS":"Mississippi","MO":"Missouri","MT":"Montana",
 "NE":"Nebraska","NV":"Nevada","NH":"New Hampshire","NJ":"New Jersey","NM":"New Mexico",
 "NY":"New York","NC":"North Carolina","ND":"North Dakota","OH":"Ohio","OK":"Oklahoma",
 "OR":"Oregon","PA":"Pennsylvania","RI":"Rhode Island","SC":"South Carolina",
 "SD":"South Dakota","TN":"Tennessee","TX":"Texas","UT":"Utah","VT":"Vermont",
 "VA":"Virginia","WA":"Washington","WV":"West Virginia","WI":"Wisconsin",
 "WY":"Wyoming","DC":"District of Columbia",
}


def _hasStreet(s):
    return bool(s and re.search(r"\d", s))


def _censusBatch(street_venues):
    """POST up to 10k (id, street, city, state) at once. Returns {key:(lat,lon)}."""
    out = {}
    for i in range(0, len(street_venues), 10000):
        chunk = street_venues[i:i+10000]
        buf = io.StringIO(); w = csv.writer(buf)
        for v in chunk:
            w.writerow([v["key"], v["street"], v["city"] or "", v["state"] or "", ""])
        try:
            r = requests.post(_CENSUS_BATCH,
                              files={"addressFile": ("a.csv", buf.getvalue(), "text/csv")},
                              data={"benchmark": "Public_AR_Current"}, timeout=300)
            r.raise_for_status()
            for row in csv.reader(io.StringIO(r.text)):
                if len(row) >= 6 and row[2] == "Match" and row[5]:
                    lon, lat = row[5].split(",")
                    out[row[0]] = (float(lat), float(lon))
        except Exception as e:
            print(f"    [census-batch] chunk failed: {e}", flush=True)
    return out


def _omCity(city, state):
    """One Open-Meteo geocoding lookup for a US (city,state). Returns (lat,lon)|None."""
    try:
        r = requests.get(_GEO_OM, params={"name": city, "count": 10, "language": "en",
                                          "format": "json", "apikey": API_KEY}, timeout=30)
        res = (r.json() or {}).get("results") or []
    except Exception:
        return None
    us = [x for x in res if x.get("country_code") == "US"]
    if not us:
        return None
    want = _STATE_NAME.get((state or "").strip().upper(), "").lower()
    in_state = [x for x in us if (x.get("admin1") or "").lower() == want]
    pick = (in_state or us)
    pick.sort(key=lambda x: x.get("population") or 0, reverse=True)
    return (pick[0]["latitude"], pick[0]["longitude"])


def _geocodeResidue(do_write, limit):
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT meet_id, sport,
                   NULLIF(TRIM(venue_name),''), NULLIF(TRIM(city),''),
                   NULLIF(TRIM(state),''), NULLIF(TRIM(location_raw),'')
            FROM meets_tfrrs
            WHERE gps_lat IS NULL
              AND (city IS NOT NULL OR venue_name IS NOT NULL OR location_raw IS NOT NULL)
            ORDER BY sport, meet_id""")
        rows = cur.fetchall(); conn.rollback()

    # dedup to venues; each carries its meets + a street (if any) for census
    venues = {}
    for mid, sport, venue, city, state, raw in rows:
        key = _norm("|".join((venue or "", city or "", state or "", raw or "")))
        if not key: continue
        v = venues.setdefault(key, {"key": key, "meets": [], "venue": venue,
                                    "city": city, "state": state, "raw": raw,
                                    "street": raw if _hasStreet(raw) else None})
        v["meets"].append((mid, sport))

    keys = list(venues)
    if limit: keys = keys[:limit]
    n_meets = sum(len(venues[k]["meets"]) for k in keys)
    street_n = sum(1 for k in keys if venues[k]["street"])
    print(f"  1b geocode: {len(keys):,} venues ({n_meets:,} meets) -- "
          f"{street_n} with street->census, rest->open-meteo city", flush=True)
    if not do_write or not keys: return 0

    # 1) Census batch for the street venues (one or few POSTs)
    census = _censusBatch([venues[k] for k in keys if venues[k]["street"]])
    print(f"    census matched {len(census)} street venues", flush=True)

    # 2) Open-Meteo for distinct (city,state) among the rest (threaded, paid = no limit)
    need_city = {(venues[k]["city"], venues[k]["state"]) for k in keys
                 if venues[k]["key"] not in census and venues[k]["city"] and venues[k]["state"]}
    print(f"    open-meteo: {len(need_city)} distinct cities to look up", flush=True)
    city_coords = {}
    def _job(cs): return cs, _omCity(cs[0], cs[1])
    with _cf.ThreadPoolExecutor(max_workers=_OM_WORKERS) as ex:
        for n, (cs, coord) in enumerate(ex.map(_job, need_city), 1):
            if coord: city_coords[cs] = coord
            if n % 200 == 0: print(f"      {n}/{len(need_city)} cities", flush=True)
    print(f"    open-meteo matched {len(city_coords)} cities", flush=True)

    # 3) assemble meet -> coords and ONE batched write
    updates = []
    for k in keys:
        v = venues[k]
        coord = census.get(k) or city_coords.get((v["city"], v["state"]))
        if not coord: continue
        for mid, sport in v["meets"]:
            updates.append((mid, sport, coord[0], coord[1]))
    print(f"    writing {len(updates):,} meet rows ...", flush=True)
    with getConn() as conn, conn.cursor() as cur:
        execute_values(cur,
            "UPDATE meets_tfrrs t SET gps_lat=d.lat::real, gps_long=d.lon::real "
            "FROM (VALUES %s) AS d(meet_id, sport, lat, lon) "
            "WHERE t.meet_id=d.meet_id::bigint AND t.sport=d.sport AND t.gps_lat IS NULL",
            updates, template="(%s,%s,%s,%s)", page_size=5000)
        conn.commit()
    print(f"  geocoded -> {len(updates):,} meet rows written", flush=True)
    return len(updates)


def stageGps(do_write, limit):
    # BORROW REMOVED: verified 3 ways that tfrrs TF meets are ~2% canon-linked
    # and 0% resolve to a GPS-bearing anet-TF twin (meets_tf_meta), so borrow
    # recovers nothing. It also read meets_tf (14M, no meet_id index) per row --
    # a seq-scan-per-meet hazard. Geocoding (dedup + Census-first) carries it.
    print("\n=== STAGE 1: GPS (geocode; borrow verified empty for tfrrs) ===")
    with getConn() as conn:
        _ensureColumns(conn)
    _geocodeResidue(do_write, limit)


# ============================================================================
# STAGE 2 -- ELEVATION
# ============================================================================

def _distinctCoords(cur):
    cur.execute("""
        SELECT DISTINCT lat, long FROM (
          SELECT gps_lat lat, gps_long long FROM meets      WHERE gps_lat IS NOT NULL AND altitude_meters IS NULL
          UNION SELECT gps_lat, gps_long   FROM meets_tf    WHERE gps_lat IS NOT NULL AND altitude_meters IS NULL
          UNION SELECT gps_lat, gps_long   FROM meets_tfrrs WHERE gps_lat IS NOT NULL AND altitude_meters IS NULL
        ) c""")
    return cur.fetchall()

def _fetchElev(batch):
    lats = ",".join(str(la) for la,_ in batch); lons = ",".join(str(lo) for _,lo in batch)
    try:
        r = requests.get(ELEV_URL, params={"latitude":lats,"longitude":lons,"apikey":API_KEY}, timeout=60)
        if r.status_code != 200: return [(la,lo,None) for la,lo in batch]
        el = r.json().get("elevation", [])
        if len(el) != len(batch): return [(la,lo,None) for la,lo in batch]
        return [(la,lo,el[i]) for i,(la,lo) in enumerate(batch)]
    except Exception:
        return [(la,lo,None) for la,lo in batch]

def _ensureGpsIndexes(conn):
    # one-time (gps_lat,gps_long) indexes so the join-updates aren't seq scans.
    cur = conn.cursor()
    for t in ("meets", "meets_tf", "meets_tfrrs"):
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_gps ON {t}(gps_lat, gps_long)")
    conn.commit()


def _fetchAllElev(coords):
    # threaded fetch (paid tier, no rate limit): 100 coords/call, 8 workers.
    chunks = [coords[i:i+ELEV_BATCH] for i in range(0, len(coords), ELEV_BATCH)]
    out = []
    with _cf.ThreadPoolExecutor(max_workers=8) as ex:
        for n, res in enumerate(ex.map(_fetchElev, chunks), 1):
            out += [(la, lo, el) for la, lo, el in res if el is not None]
            if n % 20 == 0:
                print(f"    fetched {n}/{len(chunks)} batches", flush=True)
    return out


def stageElevation(do_write, limit):
    print("\n=== STAGE 2: elevation (all coords, all tables) ===")
    with getConn() as conn, conn.cursor() as cur:
        coords = _distinctCoords(cur); conn.rollback()
    if limit: coords = coords[:limit]
    print(f"  {len(coords):,} distinct coords need elevation", flush=True)
    if not do_write or not coords: return

    with getConn() as conn:
        _ensureGpsIndexes(conn)                     # helps the join-update below
    results = _fetchAllElev(coords)
    print(f"  fetched {len(results):,} elevations; writing (batched join) ...", flush=True)

    # ONE temp table + one join-update per table -> a handful of scans, not 156k.
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE elev_tmp(lat real, lon real, elev real) ON COMMIT DROP")
        execute_values(cur, "INSERT INTO elev_tmp(lat,lon,elev) VALUES %s",
                       results, page_size=10000)
        cur.execute("CREATE INDEX ON elev_tmp(lat, lon)")
        for t in ("meets", "meets_tf", "meets_tfrrs"):
            cur.execute(f"""UPDATE {t} tt SET altitude_meters = e.elev
                            FROM elev_tmp e
                            WHERE tt.gps_lat = e.lat AND tt.gps_long = e.lon
                              AND tt.altitude_meters IS NULL""")
            print(f"    {t}: {cur.rowcount:,} rows set", flush=True)
        conn.commit()
    print("  elevation done", flush=True)


def _weatherTable(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS weather(
            meet_id BIGINT, source TEXT NOT NULL DEFAULT 'anet', hour INTEGER,
            temp_c REAL, dew_point_c REAL, humidity REAL, apparent_temp_c REAL,
            precipitation_mm REAL, weather_code INTEGER, pressure_hpa REAL,
            cloud_cover INTEGER, wind_speed_kmh REAL, wind_dir REAL,
            fetched_at TEXT, used_utc_fallback BOOLEAN DEFAULT FALSE,
            PRIMARY KEY(meet_id, source, hour))""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_weather_meet_src ON weather(meet_id, source)")
    conn.commit()

def _streamMeets(cur, table, sport, source, date_col):
    if date_col:
        cur.execute(f"""
            SELECT m.meet_id, m.gps_lat, m.gps_long, m.{date_col}, '{source}'
            FROM meets_tfrrs m
            LEFT JOIN weather w ON w.meet_id=m.meet_id AND w.source='{source}' AND w.hour=0
            WHERE m.sport='{sport}' AND m.gps_lat IS NOT NULL
              AND m.{date_col} ~ '^[0-9]{{4}}-'
              AND substring(m.{date_col},1,4)::int BETWEEN {MIN_YEAR} AND {MAX_YEAR}
              AND w.meet_id IS NULL""")
    else:
        res = "results" if table == "meets" else "results_tf"
        cur.execute(f"""
            SELECT m.meet_id, m.gps_lat, m.gps_long, d.dt, '{source}'
            FROM (SELECT DISTINCT meet_id, gps_lat, gps_long FROM {table}
                  WHERE gps_lat IS NOT NULL) m
            JOIN LATERAL (
                SELECT r.date dt FROM {res} r
                WHERE r.meet_id=m.meet_id AND r.date ~ '^[0-9]{{4}}-'
                  AND substring(r.date,1,4)::int BETWEEN {MIN_YEAR} AND {MAX_YEAR}
                LIMIT 1) d ON TRUE
            LEFT JOIN weather w ON w.meet_id=m.meet_id AND w.source='{source}' AND w.hour=0
            WHERE w.meet_id IS NULL""")
    return cur.fetchall()

def _isHist(ds):
    try: d = datetime.datetime.strptime(ds, "%Y-%m-%d").date()
    except Exception: return True
    return d <= datetime.date.today() - datetime.timedelta(days=HIST_CUTOFF_DAYS)

def _utcToLocal(lat, lon, ds, tf):
    try:
        import pytz
        z = tf.timezone_at(lat=lat, lng=lon)
        if not z: return {i:i for i in range(24)}, True
        tz = pytz.timezone(z); base = datetime.datetime.strptime(ds,"%Y-%m-%d")
        return {h: pytz.utc.localize(base.replace(hour=h)).astimezone(tz).hour for h in range(24)}, False
    except Exception:
        return {i:i for i in range(24)}, True

def _fetchWx(meets, url):
    date = meets[0][3]
    p = {"latitude":",".join(str(m[1]) for m in meets),
         "longitude":",".join(str(m[2]) for m in meets),
         "start_date":date,"end_date":date,"hourly":",".join(HOURLY),
         "timezone":"UTC","wind_speed_unit":"kmh"}
    if not SELF_HOSTED: p["apikey"] = API_KEY
    try:
        r = requests.get(url, params=p, timeout=90)
        if r.status_code != 200:
            print(f"    [wx] {date}: HTTP {r.status_code}", flush=True)
            return [(*m, None) for m in meets]
        pl = r.json()
        if isinstance(pl, dict): pl = [pl]
        if len(pl) != len(meets): return [(*m, None) for m in meets]
        return [(*m, pl[i]) for i,m in enumerate(meets)]
    except Exception as e:
        print(f"    [wx] {date}: {e}", flush=True)
        return [(*m, None) for m in meets]

def stageWeather(do_write, limit):
    print("\n=== STAGE 3: weather, all four streams ===")
    from timezonefinder import TimezoneFinder
    tf = TimezoneFinder()
    fetched = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with getConn() as conn:
        _weatherTable(conn)
        allmeets = []
        with conn.cursor() as cur:
            for label, table, sport, source, dcol in STREAMS:
                ms = _streamMeets(cur, table, sport, source, dcol); conn.rollback()
                print(f"  {label:<9}: {len(ms):,} meets need weather", flush=True)
                allmeets += ms
    total = len(allmeets); print(f"  TOTAL weather to fetch: {total:,}", flush=True)
    if not do_write: return
    if limit: allmeets = allmeets[:limit]; total = len(allmeets)
    if not allmeets: return

    # work units: one (url, up-to-WX_BATCH meets) per group, all same date
    by_date = {}
    for m in allmeets: by_date.setdefault(m[3], []).append(m)
    units = []
    for date in sorted(by_date):
        url = WX_ARCHIVE if _isHist(date) else WX_FORECAST
        ms = by_date[date]
        for i in range(0, len(ms), WX_BATCH):
            units.append((url, ms[i:i+WX_BATCH]))
    print(f"  {len(units):,} batched requests, threaded x8", flush=True)

    done = failed = 0; buf = []; start = time.time()
    with getConn() as conn, _cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_fetchWx, chunk, url) for url, chunk in units]
        for k, fut in enumerate(_cf.as_completed(futs), 1):
            for mid, lat, lon, d, source, data in fut.result():
                if data is None: failed += 1; continue
                buf += _wxRows(mid, lat, lon, d, source, data, tf, fetched); done += 1
                if len(buf) >= 5000:
                    _wxFlush(conn, buf); buf = []
            if k % 50 == 0:
                el = time.time()-start; rate = (done+failed)/el if el else 0
                eta = (total-done-failed)/rate/3600 if rate else 0
                print(f"  {done+failed:,}/{total:,} "
                      f"({100*(done+failed)/max(total,1):.1f}%) ~{eta:.1f}h", flush=True)
        if buf: _wxFlush(conn, buf)
    print(f"  weather done: {done:,} saved, {failed:,} failed", flush=True)


def stageVerify():
    print("\n=== STAGE 4: final coverage ===")
    with getConn() as conn, conn.cursor() as cur:
        print(f"  {'stream':<9} {'gps':>10} {'gps_null':>9} {'altitude':>9} {'weather':>10}")
        for label, table, sport, source, _ in STREAMS:
            w = f"sport='{sport}'" if sport else "TRUE"
            cur.execute(f"""SELECT count(DISTINCT meet_id) FILTER (WHERE gps_lat IS NOT NULL),
                                   count(DISTINCT meet_id) FILTER (WHERE gps_lat IS NULL),
                                   count(DISTINCT meet_id) FILTER (WHERE altitude_meters IS NOT NULL)
                            FROM {table} WHERE {w}""")
            gps, nogps, alt = cur.fetchone()
            cur.execute("SELECT count(DISTINCT meet_id) FROM weather WHERE source=%s",(source,))
            wx = cur.fetchone()[0]; conn.rollback()
            print(f"  {label:<9} {gps or 0:>10,} {nogps or 0:>9,} {alt or 0:>9,} {wx or 0:>10,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if a.apply and not API_KEY and not SELF_HOSTED:
        print('ERROR: set OPENMETEO_KEY first ($env:OPENMETEO_KEY="...")'); return
    initPool()
    try:
        stageGps(a.apply, a.limit)
        stageElevation(a.apply, a.limit)
        stageWeather(a.apply, a.limit)
        stageVerify()
        print("\nDONE." + ("" if a.apply else "  (dry-run -- add --apply to execute)"))
    finally:
        closePool()


if __name__ == "__main__":
    main()