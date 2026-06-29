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
 
# Locations per Open-Meteo call. The API accepts up to 1000 coordinates in one
# request (comma-separated lat/long), returning an array of per-location results
# in order. We cap our per-date batches at this.
BATCH_SIZE = 1000
 
# Open-Meteo API endpoints.
# Historical API covers dates up to HISTORICAL_CUTOFF_DAYS ago.
# Forecast API covers dates from today forward (and a few days back).
HISTORICAL_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL   = "https://api.open-meteo.com/v1/forecast"

# The archive API lags real-time by several days, so very recent meets must use
# the forecast API instead. Meets at least this many days old use the archive;
# newer ones use forecast. (Was referenced but never defined before — the bug
# that made _isHistorical raise NameError.)
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
 
# How long to wait between API calls to avoid rate limiting.
# Open-Meteo's free tier allows 10,000 calls/day — at 100 meets/call we
# can fetch 1M meets/day, so rate limiting isn't a concern.
# We still add a small sleep to be a good API citizen.
SLEEP_BETWEEN_BATCHES = 0.5

# Network timeout per call. A 1000-location call is bigger, so give it
# room, but a healthy response still returns quickly.
REQUEST_TIMEOUT = 60

# ─────────────────────────────────────────────────────────────────────────────
# Table creation
# ─────────────────────────────────────────────────────────────────────────────

# _createWeatherTable
# Purpose: Creates the weather table if it doesn't already exist
#          One row per (meet_id, hour) - 24 rows per meet, one per hour
#          of the day in local time.
# Arguments:
#           conn: psycopg2 connection from the pool.
# Output: None.
def _createWeatherTable(conn):

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weather(
            meet_id             BIGINT,
                   
            -- Hour of the day in LOCAL time (0-23).
            -- Converted from UTC using the meet's GPS coordinates to
            -- determine timezone. Stored as local time so the Flask app
            -- can query directly by race start time without timezone math.
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
            
            -- If the meet has no GPS location we use UTC fallback. We
            -- mark it in case we can manually fix later.
            used_utc_fallback   BOOLEAN DEFAULT FALSE,
            
            PRIMARY KEY(meet_id, hour)
        )
    """)

    # Index for fast lookup by meet_id — the Flask app will query
    # WHERE meet_id = X AND hour = Y frequently.
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_weather_meet_id ON weather (meet_id)
    """)
 
    conn.commit()
    print("[weather] Weather table ready")

# ─────────────────────────────────────────────────────────────────────────────
# Meet fetching
# ─────────────────────────────────────────────────────────────────────────────

# _getMeetsToFetch
# Purpose: Queries all unique meets from both the XC and TF tables that
#          have GPS coordinates and haven't been fetched yet.
#          Deduplicates by meet_id since the meets table has one row per
#          division but we only need one weather fetch per meet.\
# Arguments:
#           conn: psycopg2 connection from the pool.
# Output: List of (meet_id, gps_lat, gps_long, date) tuples.
def _getMeetsToFetch(conn) -> list:
    cursor = conn.cursor()
 
    # For each sport: join meets (GPS) to its results table (date), take the
    # earliest dated result as the meet's representative date, dedup to one row
    # per meet, then drop meets already present in weather.
    cursor.execute("""
        SELECT m.meet_id, m.gps_lat, m.gps_long, m.meet_date
        FROM (
            SELECT meets.meet_id,
                   meets.gps_lat,
                   meets.gps_long,
                   MIN(results.date) AS meet_date
            FROM meets
            JOIN results ON results.meet_id = meets.meet_id
            WHERE meets.gps_lat IS NOT NULL
              AND meets.gps_long IS NOT NULL
              AND results.date IS NOT NULL
              AND results.date <> ''
            GROUP BY meets.meet_id, meets.gps_lat, meets.gps_long
 
            UNION
 
            SELECT meets_tf.meet_id,
                   meets_tf.gps_lat,
                   meets_tf.gps_long,
                   MIN(results_tf.date) AS meet_date
            FROM meets_tf
            JOIN results_tf ON results_tf.meet_id = meets_tf.meet_id
            WHERE meets_tf.gps_lat IS NOT NULL
              AND meets_tf.gps_long IS NOT NULL
              AND results_tf.date IS NOT NULL
              AND results_tf.date <> ''
            GROUP BY meets_tf.meet_id, meets_tf.gps_lat, meets_tf.gps_long
        ) m
        LEFT JOIN weather w ON w.meet_id = m.meet_id AND w.hour = 0
        WHERE w.meet_id IS NULL
        ORDER BY m.meet_date, m.meet_id
    """)
 
    rows = cursor.fetchall()
    print(f"[weather] {len(rows)} meets to fetch")
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
#          We zip that array back onto the meets we sent.
# Arguments:
#           same_date_meets: list of (meet_id, lat, long, date) — all same date,
#                            length <= BATCH_SIZE.
#           url:             HISTORICAL_URL or FORECAST_URL (chosen by caller).
# Output:   list of (meet_id, lat, long, date, per_location_json_or_None).
def _callOpenMeteoBatch(same_date_meets: list, url: str) -> list:
    # All meets here share one date, so one start/end for the whole call.
    date = same_date_meets[0][3]
 
    lats  = ",".join(str(m[1]) for m in same_date_meets)
    longs = ",".join(str(m[2]) for m in same_date_meets)
 
    params = {
        "latitude":        lats,
        "longitude":       longs,
        "start_date":      date,
        "end_date":        date,
        "hourly":          ",".join(HOURLY_VARIABLES),
        "timezone":        "UTC",   # fetch in UTC, convert to local at save time
        "wind_speed_unit": "kmh",
    }
 
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            print(f"[weather] API error (date {date}, {len(same_date_meets)} "
                  f"meets): status {response.status_code}")
            return [(mid, lat, lon, d, None)
                    for mid, lat, lon, d in same_date_meets]
 
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
            return [(mid, lat, lon, d, None)
                    for mid, lat, lon, d in same_date_meets]
 
        return [(mid, lat, lon, d, payload[i])
                for i, (mid, lat, lon, d) in enumerate(same_date_meets)]
 
    except Exception as e:
        print(f"[weather] Exception (date {date}, {len(same_date_meets)} meets): {e}")
        return [(mid, lat, lon, d, None)
                for mid, lat, lon, d in same_date_meets]
 
 
# _fetchDate
# Purpose: Fetch all meets for ONE date. Splits them into archive vs forecast
#          (a single date is wholly one or the other, but we split defensively),
#          and chunks into <=BATCH_SIZE calls in case a single date has >1000
#          meets (big championship days).
# Arguments:
#           same_date_meets: all meets on one date.
# Output:   list of (meet_id, lat, long, date, per_location_json_or_None).
def _fetchDate(same_date_meets: list) -> list:
    url = HISTORICAL_URL if _isHistorical(same_date_meets[0][3]) else FORECAST_URL
 
    results = []
    for i in range(0, len(same_date_meets), BATCH_SIZE):
        chunk = same_date_meets[i:i + BATCH_SIZE]
        results += _callOpenMeteoBatch(chunk, url)
        time.sleep(SLEEP_BETWEEN_BATCHES)
    return results


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
#          hour rows. Stores used_utc_fallback (the previous version computed it
#          but never saved it, and the table column was misspelled fetched_ar).
# Arguments:
#           conn, meet_id, lat, lon, date_str: identity + location/date.
#           data:       this meet's per-location JSON object (has "hourly").
#           tf:         shared TimezoneFinder.
#           fetched_at: UTC timestamp string for this run.
# Output:   True on save, False on parse/save failure.
def _parseAndSave(conn, meet_id, lat, lon, date_str, data, tf, fetched_at) -> bool:
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
                meet_id, hour,
                temp_c, dew_point_c, humidity, apparent_temp_c,
                precipitation_mm, weather_code,
                pressure_hpa, cloud_cover,
                wind_speed_kmh, wind_dir,
                fetched_at, used_utc_fallback
            )
            VALUES %s
            ON CONFLICT (meet_id, hour) DO NOTHING
        """, rows)
        conn.commit()
        return True
 
    except Exception as e:
        print(f"[weather] Failed to save meet {meet_id}: {e}")
        conn.rollback()
        return False
    

# ─────────────────────────────────────────────────────────────────────────────
# Progress + entry point
# ─────────────────────────────────────────────────────────────────────────────

# _printProgress
# Purpose: Prints a progress line showing how many meets have been fetched
#          and the estimated time remaining.
# Arguments:
#           fetched: number of meets fetched so far.
#           total: total meets to fetch.
#           start_time: time.time() value from when the script started.
# Output: None.
def _printProgress(fetched: int, total: int, start_time: float):
 
    elapsed   = time.time() - start_time
    per_meet  = elapsed / fetched if fetched > 0 else 0
    remaining = (total - fetched) * per_meet
 
    # Format remaining time as hours and minutes.
    hours   = int(remaining // 3600)
    minutes = int((remaining % 3600) // 60)
 
    print(f"[weather] {fetched}/{total} meets "
          f"({100 * fetched / total:.1f}%) — "
          f"~{hours}h {minutes}m remaining")

# main
# Purpose: Runs the full weather backfill pipeline.
#          Fetches weather for all meets with GPS coordinates that don't
#          already have weather data, in batches of BATCH_SIZE.
# Arguments: None.
# Output: None.
def main():

    print("[weather] Starting weather backfill (batched by date)")
    initPool()
 
    # TimezoneFinder loads a ~20MB DB — build once, reuse for every meet.
    tf = TimezoneFinder()
    fetched_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    start_time = time.time()
 
    try:
        with getConn() as conn:
            _createWeatherTable(conn)
            meets = _getMeetsToFetch(conn)
 
        total = len(meets)
        if total == 0:
            print("[weather] All meets already have weather — nothing to do")
            return
 
        # Group by date: each distinct date is one (or, if >1000 meets, a few)
        # multi-location call(s). Far fewer distinct dates than meets.
        by_date = _groupByDate(meets)
        print(f"[weather] {len(by_date)} distinct dates to fetch "
              f"across {total} meets")
 
        done = 0
        failed = 0
        # Fetches weather from dates and saves.
        for date in sorted(by_date.keys()):
            results = _fetchDate(by_date[date])
 
            with getConn() as conn:
                for meet_id, lat, lon, d, data in results:
                    if data is None:
                        failed += 1
                        continue
                    if _parseAndSave(conn, meet_id, lat, lon, d, data, tf, fetched_at):
                        done += 1
                    else:
                        failed += 1
 
            _printProgress(done + failed, total, start_time)
 
        elapsed = time.time() - start_time
        print(f"\n[weather] Done — {done} saved, {failed} failed "
              f"in {elapsed/3600:.2f}h")
 
    finally:
        closePool()
 
 
if __name__ == "__main__":
    main()