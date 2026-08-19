# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Backfill
# Date: 6/8/2026 (batched rewrite 6/25/2026)
# File Title: weather_backfill.py
# Purpose: Fetches historical and forecast weather data from Open-Meteo for every
#          meet in the database that has GPS coordinates. Stores 24 hourly rows
#          per meet (one per hour of the day, in LOCAL time) so the Flask app can
#          look up weather for any user-specified race start time.
#
#          BATCHED BY DATE: Open-Meteo accepts up to 1000 locations in one call,
#          but every location in a call must share the same date range. So we
#          group meets by date and send one call per distinct date (<=1000 meets
#          each), turning ~170k individual calls into ~one-per-race-date. The
#          response is a JSON ARRAY of per-location objects, in the order sent.
#
#          Resumable — already-fetched meets are skipped automatically.

import sys
import os
import time
import datetime
import requests
import psycopg2.extras

# timezonefinder converts GPS coordinates to a timezone string.
# e.g. (42.3601, -71.0589) → "America/New_York"
from timezonefinder import TimezoneFinder

# pytz converts between timezones. Comes with timezonefinder.
import pytz

sys.path.insert(0, "scripts")
from database import initPool, closePool, getConn

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
 
# Locations per Open-Meteo call. The API accepts up to 1000 coordinates, BUT a
# 1000-location GET overflows the URL length limit (414). We use a documented
# GET with ~200 locations/call, whose URL stays well under 8 KB. Batching does
# NOT change cost (Open-Meteo bills per LOCATION, not per HTTP request), so a
# smaller batch is free -- it just means a few more round-trips per date.
BATCH_SIZE = 200
 
# Open-Meteo API endpoints.
# PAID (customer) API: dedicated servers, NO daily/hourly/minute rate limit, and
# a commercial-use licence. The syntax is identical to the free tier -- only the
# domain changes and every request carries the apikey. Both the archive and
# forecast endpoints move to customer-api.open-meteo.com on the paid plan.
# NOTE: the archive is a SEPARATE subdomain from forecast. Free tier:
# archive-api.open-meteo.com ; commercial: customer-archive-api.open-meteo.com.
# Using customer-api.../v1/archive (the forecast host) 404s every historical
# call -- and ~all race dates are historical.
HISTORICAL_URL = "https://customer-archive-api.open-meteo.com/v1/archive"
FORECAST_URL   = "https://customer-api.open-meteo.com/v1/forecast"

# The API key. READ FROM THE ENVIRONMENT so a paid key never lives in source or a
# shared file. Set it before running:
#   PowerShell:  $env:OPENMETEO_KEY = "your-key-here"
#   bash:        export OPENMETEO_KEY="your-key-here"
# If it is unset we stop immediately with a clear message rather than silently
# hammering the free endpoint (which would rate-limit as before).
API_KEY = os.environ.get("OPENMETEO_KEY", "").strip()

# The archive API lags real-time by several days, so very recent meets must use
# the forecast API instead. Meets at least this many days old use the archive;
# newer ones use forecast.
HISTORICAL_CUTOFF_DAYS = 7

# Weather variables to fetch from Open-Meteo.
# We fetch all potentially useful variables now so we don't have to
# re-run the backfill if the model needs more features later.
# Open-Meteo returns these as hourly arrays (24 values per day).
HOURLY_VARIABLES = [
    "temperature_2m",        # air temperature at 2m height (°C)
    "dew_point_2m",          # dew point at 2m height (°C) — better humidity measure than RH
    "relative_humidity_2m",  # relative humidity (%)
    "apparent_temperature",  # "feels like" temperature (°C)
    "precipitation",         # total precipitation (mm)
    "weather_code",          # WMO weather code (0=clear, 61=rain, 71=snow, etc.)
    "surface_pressure",      # barometric pressure (hPa) — affects oxygen at altitude
    "cloud_cover",           # total cloud cover (%)
    "wind_speed_10m",        # wind speed at 10m height (km/h)
    "wind_direction_10m",    # wind direction at 10m height (degrees, 0=N, 90=E)
]

# Small courtesy pause between calls. The paid tier has no rate limit, so this is
# just politeness / avoiding a thundering-herd on their servers; keep it tiny.
SLEEP_BETWEEN_BATCHES = 0.1

# Network timeout per call. A 1000-location POST is bigger, so give it room.
REQUEST_TIMEOUT = 60

# Sane year range for a race date. results.date is free TEXT and carries corrupt
# years (observed: 2222, 0025, 2223). Any meet whose year falls outside this
# range is skipped before it ever reaches the API -- a bad year would misroute
# the archive-vs-forecast choice or throw in strptime. 1860 predates organized
# competition; 2027 leaves headroom past 'today' for near-future scheduled meets.
_MIN_YEAR = 1860
_MAX_YEAR = 2027

# ─────────────────────────────────────────────────────────────────────────────
# Table creation
# ─────────────────────────────────────────────────────────────────────────────

# _createWeatherTable
# Purpose: Creates the weather table if it doesn't already exist, and migrates an
#          existing table to carry a `source` column in its PRIMARY KEY.
#
#          WHY source IS IN THE PK: anet meets.meet_id and tfrrs
#          meets_tfrrs.meet_id are INDEPENDENT id namespaces that overlap
#          heavily (12,940 shared ids, both starting at 1). Without a source
#          discriminator, tfrrs meet 5000's weather would collide with anet meet
#          5000's under PK (meet_id, hour), and ON CONFLICT DO NOTHING would
#          SILENTLY DROP it. So the row identity is (meet_id, source, hour).
#          source is 'anet' | 'anet_tf' | 'tfrrs'.
# Arguments:
#           conn: psycopg2 connection from the pool.
# Output: None.
def _createWeatherTable(conn):

    cursor = conn.cursor()

    # Fresh create carries the source-aware PK from the start.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weather(
            meet_id             BIGINT,
            source              TEXT NOT NULL DEFAULT 'anet',

            -- Hour of the day in LOCAL time (0-23).
            hour                INTEGER,

            -- Temperature variables
            temp_c              REAL,    -- air temperature (°C)
            dew_point_c         REAL,    -- dew point (°C)
            humidity            REAL,    -- relative humidity (%)
            apparent_temp_c     REAL,    -- "feels like" temperature (°C)

            -- Precipitation
            precipitation_mm    REAL,    -- total precipitation (mm)
            weather_code        INTEGER, -- WMO code: 0=clear, 61=rain, 71=snow

            -- Atmospheric
            pressure_hpa        REAL,    -- barometric pressure (hPa)
            cloud_cover         INTEGER, -- cloud cover (%)

            -- Wind
            wind_speed_kmh      REAL,    -- wind speed (km/h)
            wind_dir            REAL,    -- wind direction (degrees, 0=N)

            -- Metadata
            fetched_at          TEXT,    -- UTC timestamp when this row was fetched
            used_utc_fallback   BOOLEAN DEFAULT FALSE,

            PRIMARY KEY(meet_id, source, hour)
        )
    """)

    # MIGRATION for an EXISTING table created before `source` existed:
    #   1. add the column (existing rows default to 'anet' -- all prior fetches
    #      were anet, since tfrrs had no gps until this session).
    #   2. swap the PK from (meet_id, hour) to (meet_id, source, hour).
    # Both steps are idempotent: ADD COLUMN IF NOT EXISTS is a no-op if present,
    # and the PK swap only runs when the old 2-col PK is still in place.
    cursor.execute("""
        ALTER TABLE weather ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'anet'
    """)
    cursor.execute("""
        SELECT array_agg(a.attname ORDER BY array_position(i.indkey, a.attnum))
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = 'weather'::regclass AND i.indisprimary
    """)
    row = cursor.fetchone()
    pk_cols = row[0] if row and row[0] else []
    if pk_cols == ["meet_id", "hour"]:
        # rebuild the PK to include source
        cursor.execute("ALTER TABLE weather DROP CONSTRAINT weather_pkey")
        cursor.execute("ALTER TABLE weather ADD PRIMARY KEY (meet_id, source, hour)")
        print("[weather] migrated PK -> (meet_id, source, hour)")

    # Index for fast lookup by meet_id + source.
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_weather_meet_src ON weather (meet_id, source)
    """)

    conn.commit()
    print("[weather] Weather table ready (source-aware)")

# ─────────────────────────────────────────────────────────────────────────────
# Meet fetching
# ─────────────────────────────────────────────────────────────────────────────

# _getMeetsToFetch
# Purpose: Queries unique meets across THREE sources that have GPS coordinates
#          and haven't been fetched yet:
#            'anet'    -> meets      + results     (anet XC)
#            'anet_tf' -> meets_tf   + results_tf  (anet TF)
#            'tfrrs'   -> meets_tfrrs (dates off its OWN clean date column)
#          Each row is tagged with its source, because anet and tfrrs meet_ids
#          share a namespace (12,940 collisions) -- source is what keeps them
#          apart in the weather table.
#
#          CORRUPT-DATE FILTER: results.date is free TEXT and carries garbage
#          years (2222, 0025, 2223). Any meet whose representative year falls
#          outside [_MIN_YEAR, _MAX_YEAR] is skipped here so it never reaches the
#          API (a bad year would misroute archive-vs-forecast or throw in
#          strptime). tfrrs dates are clean ISO but we apply the same guard for
#          uniformity.
# Arguments:
#           conn: psycopg2 connection from the pool.
# Output: List of (meet_id, gps_lat, gps_long, date, source) tuples.
def _getMeetsToFetch(conn) -> list:
    cursor = conn.cursor()

    # A reusable SQL year-guard fragment. substring(date,1,4) is the year; we
    # keep only 4-digit years inside the sane range. Applied to each arm's date.
    #   anet/anet_tf date lives in results/results_tf (TEXT, dirty)
    #   tfrrs date lives on meets_tfrrs.date (clean ISO, guarded anyway)
    cursor.execute(f"""
        SELECT m.meet_id, m.gps_lat, m.gps_long, m.meet_date, m.source
        FROM (
            -- anet XC
            SELECT meets.meet_id,
                   meets.gps_lat,
                   meets.gps_long,
                   MIN(results.date) AS meet_date,
                   'anet'::text       AS source
            FROM meets
            JOIN results ON results.meet_id = meets.meet_id
            WHERE meets.gps_lat IS NOT NULL
              AND meets.gps_long IS NOT NULL
              AND results.date ~ '^[0-9]{{4}}-'
              AND substring(results.date,1,4)::int BETWEEN {_MIN_YEAR} AND {_MAX_YEAR}
            GROUP BY meets.meet_id, meets.gps_lat, meets.gps_long

            UNION ALL

            -- anet TF
            SELECT meets_tf.meet_id,
                   meets_tf.gps_lat,
                   meets_tf.gps_long,
                   MIN(results_tf.date) AS meet_date,
                   'anet_tf'::text       AS source
            FROM meets_tf
            JOIN results_tf ON results_tf.meet_id = meets_tf.meet_id
            WHERE meets_tf.gps_lat IS NOT NULL
              AND meets_tf.gps_long IS NOT NULL
              AND results_tf.date ~ '^[0-9]{{4}}-'
              AND substring(results_tf.date,1,4)::int BETWEEN {_MIN_YEAR} AND {_MAX_YEAR}
            GROUP BY meets_tf.meet_id, meets_tf.gps_lat, meets_tf.gps_long

            UNION ALL

            -- tfrrs XC (dates off its own clean column, no results join needed)
            SELECT meets_tfrrs.meet_id,
                   meets_tfrrs.gps_lat,
                   meets_tfrrs.gps_long,
                   meets_tfrrs.date  AS meet_date,
                   'tfrrs'::text     AS source
            FROM meets_tfrrs
            WHERE meets_tfrrs.sport = 'XC'
              AND meets_tfrrs.gps_lat IS NOT NULL
              AND meets_tfrrs.gps_long IS NOT NULL
              AND meets_tfrrs.date ~ '^[0-9]{{4}}-'
              AND substring(meets_tfrrs.date,1,4)::int BETWEEN {_MIN_YEAR} AND {_MAX_YEAR}
        ) m
        -- skip meets already fetched: match on BOTH meet_id AND source, since the
        -- weather PK now includes source (an anet and a tfrrs row can share a
        -- meet_id and must be tracked independently).
        LEFT JOIN weather w
               ON w.meet_id = m.meet_id AND w.source = m.source AND w.hour = 0
        WHERE w.meet_id IS NULL
        ORDER BY m.meet_date, m.source, m.meet_id
    """)

    rows = cursor.fetchall()
    print(f"[weather] {len(rows)} meets to fetch (anet + anet_tf + tfrrs)")
    return rows

# ─────────────────────────────────────────────────────────────────────────────
# Batched fetch (by date)
# ─────────────────────────────────────────────────────────────────────────────
 
# _groupByDate
# Purpose: Group meets by their date string, because one Open-Meteo multi-
#          location call requires ALL locations to share the same date range.
#          So date is the natural batch key — one call per distinct date.
# Arguments:
#           meets: list of (meet_id, lat, long, date) tuples.
# Output:   dict {date_str: [ (meet_id, lat, long, date), ... ]}.
def _groupByDate(meets: list) -> dict:
    by_date = {}
    for m in meets:
        by_date.setdefault(m[3], []).append(m)
    return by_date

# _callOpenMeteoBatch
# Purpose: Fetch weather for up to BATCH_SIZE meets that ALL share one date, in a
#          single multi-location call. Sends comma-separated lat/long lists; the
#          API returns a JSON ARRAY of per-location objects in the SAME ORDER.
#          We zip that array back onto the meets we sent (carrying source).
# Arguments:
#           same_date_meets: list of (meet_id, lat, long, date, source) -- all
#                            same date, length <= BATCH_SIZE.
#           url:             HISTORICAL_URL or FORECAST_URL (chosen by caller).
# Output:   list of (meet_id, lat, long, date, source, per_location_json_or_None).
def _callOpenMeteoBatch(same_date_meets: list, url: str) -> list:
    # All meets here share one date, so one start/end for the whole call.
    date = same_date_meets[0][3]
 
    lats  = ",".join(str(m[1]) for m in same_date_meets)
    longs = ",".join(str(m[2]) for m in same_date_meets)
 
    # POST, not GET. A GET puts the whole comma-separated coordinate list in the
    # URL, which overflows the server's URL-length limit on big dates (the 414
    # errors -- e.g. a 464-meet championship day). Open-Meteo accepts the exact
    # same parameters as a POST form body, which has no length limit. The apikey
    # rides along in the body too.
    params = {
        "latitude":        lats,
        "longitude":       longs,
        "start_date":      date,
        "end_date":        date,
        "hourly":          ",".join(HOURLY_VARIABLES),
        "timezone":        "UTC",   # fetch in UTC, convert to local at save time
        "wind_speed_unit": "kmh",
        "apikey":          API_KEY,
    }
 
    try:
        # GET, as the docs specify. With BATCH_SIZE=200 the comma-separated
        # coordinate URL stays under the length limit; POST is undocumented and
        # unverified on this API, so we don't rely on it.
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            # 429 = rate limited (should not happen on the paid tier, but if it
            # does we surface it distinctly). Any non-200 fails the batch to None
            # so those meets stay NULL and a later run retries them.
            tag = "rate-limited (429)" if response.status_code == 429 else \
                  f"status {response.status_code}"
            print(f"[weather] API error (date {date}, {len(same_date_meets)} "
                  f"meets): {tag}")
            return [(mid, lat, lon, d, src, None)
                    for mid, lat, lon, d, src in same_date_meets]
 
        payload = response.json()
 
        # A MULTI-location response is a list (one object per location). A SINGLE-
        # location response is a bare dict. We sent many, but if exactly one meet
        # was in this date-batch the API returns a dict — normalise to a list so
        # the zip below is uniform.
        if isinstance(payload, dict):
            payload = [payload]
 
        # Alignment guard: the array must line up 1:1 with what we sent, or we
        # can't trust which weather belongs to which meet — fail the batch.
        if len(payload) != len(same_date_meets):
            print(f"[weather] length mismatch (date {date}): sent "
                  f"{len(same_date_meets)}, got {len(payload)} — failing batch")
            return [(mid, lat, lon, d, src, None)
                    for mid, lat, lon, d, src in same_date_meets]
 
        return [(mid, lat, lon, d, src, payload[i])
                for i, (mid, lat, lon, d, src) in enumerate(same_date_meets)]
 
    except Exception as e:
        print(f"[weather] Exception (date {date}, {len(same_date_meets)} meets): {e}")
        return [(mid, lat, lon, d, src, None)
                for mid, lat, lon, d, src in same_date_meets]
 
 
# _fetchDate
# Purpose: Fetch all meets for ONE date. Splits them into archive vs forecast
#          (a single date is wholly one or the other, but we split defensively),
#          and chunks into <=BATCH_SIZE calls in case a single date has >1000
#          meets (big championship days).
# Arguments:
#           same_date_meets: all meets on one date.
# Output:   (results, n_calls) -- results is the per-meet list, n_calls is how
#           many HTTP calls this date consumed (one per chunk), so the caller can
#           track the run against the daily budget.
def _fetchDate(same_date_meets: list):
    url = HISTORICAL_URL if _isHistorical(same_date_meets[0][3]) else FORECAST_URL
 
    results = []
    n_calls = 0
    for i in range(0, len(same_date_meets), BATCH_SIZE):
        chunk = same_date_meets[i:i + BATCH_SIZE]
        results += _callOpenMeteoBatch(chunk, url)
        n_calls += 1
        time.sleep(SLEEP_BETWEEN_BATCHES)
    return results, n_calls


# ─────────────────────────────────────────────────────────────────────────────
# Date helpers
# ─────────────────────────────────────────────────────────────────────────────
 

# _isHistorical
# Purpose: True if a date is old enough for the archive API; False -> forecast.
# Arguments:
#           date_str: "YYYY-MM-DD".
# Output:   bool.
def _isHistorical(date_str: str) -> bool:
    try:
        meet_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        # Unparseable -> default to historical (the archive is the safer guess
        # for the bulk of meets, which are old).
        return True
    cutoff = datetime.date.today() - datetime.timedelta(days=HISTORICAL_CUTOFF_DAYS)
    return meet_date <= cutoff


# ─────────────────────────────────────────────────────────────────────────────
# Timezone conversion
# ─────────────────────────────────────────────────────────────────────────────

# _utcToLocalhours
# Purpose: Converts a list of 24 UTC hour indices to local hour indices
#          using the meet's GPS coordinates to determine the timezone.
# Arguments:
#           lat: latitude of the meet.
#           lon: longitude of the meet.
#           date_str: date of the meet in "YYYY-MM-DD" format.
#           tf: TimezoneFinder instance (shared across all calls for efficiency).
# Output: Tuple of a Dict mapping utc_hour (0-23) → local_hour (0-23) and a bool.
#         Returns identity mapping (0→0, 1→1 etc.) if timezone lookup fails. If 
#         timezone lookup fails and identity mapping is used, TRUE is returned
#         because for this meet_id we used the UTC fallback. 
def _utcToLocalHours(lat: float, long: float, 
                    date_str: str, tf: TimezoneFinder) -> dict:
    
    try:
        # Gets the timezone name for this GPS location.
        # e.g. "America/New_York", "America/Chicago", "America/Los_Angeles"
        tz_name = tf.timezone_at(lat=lat, lng=long)

        if not tz_name:
            # No timezone found — fall back to UTC (identity mapping). We
            # just map UTC hours to UTC hours (0: 0, 1: 1,...) and return
            # it. Wrong for most US locations but better than failing entirely.
            # Dictionary comprehension.
            return {i: i for i in range(24)}, True
        
        # Parse the date and create a timezone-aware datetime for midnight UTC.
        tz = pytz.timezone(tz_name)
        meet_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")

        # Build a mapping from each UTC hour to the corresponding local hour.
        # We check each hour individually because DST transitions mean the
        # offset can change mid-day (rare but possible near DST boundaries).
        mapping = {}
        for utc_hour in range(24):
            utc_dt    = pytz.utc.localize(meet_date.replace(hour=utc_hour))
            local_dt  = utc_dt.astimezone(tz)
            mapping[utc_hour] = local_dt.hour
 
        return mapping, False
 
    except Exception:
        # Any failure → fall back to UTC identity mapping.
        return {i: i for i in range(24)}, True
    

# ─────────────────────────────────────────────────────────────────────────────
# Saving
# ─────────────────────────────────────────────────────────────────────────────
 

# _parseAndSave
# Purpose: Parse ONE meet's per-location weather object and write its 24 local-
#          hour rows, tagged with the meet's source. The source tag is what keeps
#          an anet meet and a tfrrs meet with the SAME meet_id from colliding.
# Arguments:
#           conn, meet_id, lat, lon, date_str, source: identity + location/date.
#           data:       this meet's per-location JSON object (has "hourly").
#           tf:         shared TimezoneFinder.
#           fetched_at: UTC timestamp string for this run.
# Output:   True on save, False on parse/save failure.
def _parseAndSave(conn, meet_id, lat, lon, date_str, source, data, tf, fetched_at) -> bool:
    try:
        hourly = data.get("hourly", {}) or {}
 
        temps       = hourly.get("temperature_2m",       [None] * 24)
        dew_points  = hourly.get("dew_point_2m",         [None] * 24)
        humidities  = hourly.get("relative_humidity_2m", [None] * 24)
        apparent    = hourly.get("apparent_temperature", [None] * 24)
        precip      = hourly.get("precipitation",        [None] * 24)
        codes       = hourly.get("weather_code",         [None] * 24)
        pressures   = hourly.get("surface_pressure",     [None] * 24)
        clouds      = hourly.get("cloud_cover",          [None] * 24)
        wind_speeds = hourly.get("wind_speed_10m",       [None] * 24)
        wind_dirs   = hourly.get("wind_direction_10m",   [None] * 24)
 
        utc_to_local, used_fallback = _utcToLocalHours(lat, lon, date_str, tf)
 
        # safe(): index a variable list defensively (a short/missing array -> None
        # rather than IndexError, so one odd response can't kill the batch).
        def safe(arr, i):
            return arr[i] if i < len(arr) else None
 
        rows = []
        for utc_hour in range(24):
            local_hour = utc_to_local[utc_hour]
            rows.append((
                meet_id,
                source,
                local_hour,
                safe(temps, utc_hour),
                safe(dew_points, utc_hour),
                safe(humidities, utc_hour),
                safe(apparent, utc_hour),
                safe(precip, utc_hour),
                safe(codes, utc_hour),
                safe(pressures, utc_hour),
                safe(clouds, utc_hour),
                safe(wind_speeds, utc_hour),
                safe(wind_dirs, utc_hour),
                fetched_at,
                used_fallback,
            ))
 
        cursor = conn.cursor()
        psycopg2.extras.execute_values(cursor, """
            INSERT INTO weather (
                meet_id, source, hour,
                temp_c, dew_point_c, humidity, apparent_temp_c,
                precipitation_mm, weather_code,
                pressure_hpa, cloud_cover,
                wind_speed_kmh, wind_dir,
                fetched_at, used_utc_fallback
            )
            VALUES %s
            ON CONFLICT (meet_id, source, hour) DO NOTHING
        """, rows)
        conn.commit()
        return True
 
    except Exception as e:
        print(f"[weather] Failed to save meet {meet_id} ({source}): {e}")
        conn.rollback()
        return False
    

# ─────────────────────────────────────────────────────────────────────────────
# Progress + entry point
# ─────────────────────────────────────────────────────────────────────────────

# _printProgress
# Purpose: Prints a progress line showing meets fetched, API calls used against
#          the daily budget, percent complete, and a rough ETA.
# Arguments:
#           fetched:    number of meets processed so far (done + failed).
#           total:      total meets to process this run.
#           calls:      API calls used so far this run.
#           start_time: time.time() value from when the script started.
# Output: None.
def _printProgress(fetched: int, total: int, calls: int, start_time: float):
 
    elapsed   = time.time() - start_time
    per_meet  = elapsed / fetched if fetched > 0 else 0
    remaining = (total - fetched) * per_meet
 
    # Format remaining time as hours and minutes.
    hours   = int(remaining // 3600)
    minutes = int((remaining % 3600) // 60)
 
    print(f"[weather] {fetched}/{total} meets "
          f"({100 * fetched / total:.1f}%) — "
          f"{calls} calls — "
          f"~{hours}h {minutes}m remaining")

# main
# Purpose: Runs the full weather backfill pipeline.
#          Fetches weather for all meets with GPS coordinates that don't
#          already have weather data, in batches of BATCH_SIZE.
# Arguments: None.
# Output: None.
def main():

    print("[weather] Starting weather backfill (batched by date)")

    # Fail fast if the paid API key is not set, rather than silently hammering an
    # endpoint that will reject every call.
    if not API_KEY:
        print("[weather] ERROR: OPENMETEO_KEY is not set. Set it first:")
        print('  PowerShell:  $env:OPENMETEO_KEY = "your-key-here"')
        print('  bash:        export OPENMETEO_KEY="your-key-here"')
        return

    initPool()
 
    # TimezoneFinder loads a ~20MB DB — build once, reuse for every meet.
    tf = TimezoneFinder()
    fetched_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    start_time = time.time()
 
    try:
        with getConn() as conn:
            _createWeatherTable(conn)
            meets = _getMeetsToFetch(conn)
 
        # --dry-run reports work size (≈ call budget) and exits, no API calls.
        # --limit N fetches only the first N meets this run (cheap smoke test).
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--dry-run", action="store_true",
                        help="print counts and exit; makes no API calls")
        ap.add_argument("--limit", type=int, default=0,
                        help="fetch only the first N meets this run (0 = all)")
        args = ap.parse_args()

        total = len(meets)
        if total == 0:
            print("[weather] All meets already have weather — nothing to do")
            return

        print(f"[weather] work size: {total} meet-locations "
              f"(~{total} calls billed per-location)")
        if args.dry_run:
            print("[weather] --dry-run: no API calls made. Compare the number "
                  "above to your monthly plan budget before the full run.")
            return
        if args.limit:
            meets = meets[:args.limit]
            total = len(meets)
            print(f"[weather] --limit {args.limit}: fetching {total} this run")

        # Group by date: each distinct date is one (or, if >BATCH_SIZE meets, a
        # few) multi-location call(s). Far fewer distinct dates than meets.
        by_date = _groupByDate(meets)
        print(f"[weather] {len(by_date)} distinct dates to fetch "
              f"across {total} meets")
 
        done = 0
        failed = 0
        calls = 0

        # Paid tier: no daily/hourly/minute rate limit, so we run straight
        # through every date in one pass. Still resumable -- if the run is
        # interrupted, the next run's work query skips already-fetched meets.
        for date in sorted(by_date.keys()):
            results, n_calls = _fetchDate(by_date[date])
            calls += n_calls

            with getConn() as conn:
                for meet_id, lat, lon, d, source, data in results:
                    if data is None:
                        failed += 1
                        continue
                    if _parseAndSave(conn, meet_id, lat, lon, d, source, data, tf, fetched_at):
                        done += 1
                    else:
                        failed += 1

            _printProgress(done + failed, total, calls, start_time)

        elapsed = time.time() - start_time
        print(f"\n[weather] Done — {done} saved, {failed} failed, "
              f"{calls} calls in {elapsed/3600:.2f}h")
        if failed:
            print(f"[weather] {failed} meets failed this run "
                  f"(left NULL; re-run to retry them).")
 
    finally:
        closePool()
 
 
if __name__ == "__main__":
    main()