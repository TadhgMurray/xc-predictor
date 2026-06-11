# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Backfill
# Date: 6/8/2026
# File Title: weather_backfill.py
# Purpose: Fetches historical and forecast weather data from Open-Meteo
#          for every meet in the database that has GPS coordinates.
#          Stores 24 hourly rows per meet (one per hour of the day, in local
#          time) so the Flask app can look up weather for any user-specified
#          race start time.
#
#          Resumable — already-fetched meets are skipped automatically.
#          Run it, stop it, run it again — it picks up where it left off.

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
from config import PG_CONFIG

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
 
# How many meets to fetch weather for in one Open-Meteo API call.
# Open-Meteo's batch endpoint accepts multiple lat/lon/date combos in one
# request. 100 is a good balance — large enough to be fast, small enough
# that a single failure doesn't lose too many meets.
BATCH_SIZE = 100
 
# Open-Meteo API endpoints.
# Historical API covers dates up to HISTORICAL_CUTOFF_DAYS ago.
# Forecast API covers dates from today forward (and a few days back).
HISTORICAL_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL   = "https://api.open-meteo.com/v1/forecast"

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
SLEEP_BETWEEN_BATCHES = 0.5  # seconds

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
            wind_speed_km       REAL,    -- wind speed (km/h)
            wind_dir            REAL,    -- wind direction (degrees, 0=N)

            -- Metadata
            fetched_ar          TEXT,    -- UTC timestamp when this row was fetched
            
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

    # UNION combines XC meets and TF meets into one result set.
    # DISTINCT ensures we only get one row per meet_id even if
    # multiple divisions share the same meet_id and GPS.
    # LEFT JOIN weather filters out meets we've alreayd fetched -
    # w.meet_id IS NULL means no matching row in weather yet.
    cursor.execute("""
        SELECT DISTINCT m.meet_id, m.gps_lat, m.gps_long, m.date
        FROM (
            SELECT meet_id, gps_lat, gps_long,
                    MIN(date) as date
            FROM meets
            WHERE gps_lat is NOT NULL
                AND gps_long IS NOT NULL
                AND date IS NOT NULL
                AND date != ''
            GROUP BY meet_id, gps_lat, gps_long
            
            UNION
                   
            SELECT meet_id, gps_lat, gps_long,
                   MIN(date) as date
            FROM meets_tf
            WHERE gps_lat IS NOT NULL
                AND gps_long IS NOT NULL
                AND date IS NOT NULL
                AND date != ''
            GROUP BY meet_id, gps_lat, gps_long       
        ) m
        LEFT JOIN weather w ON w.meet_id = m.meet_id AND w.hour = 0
        WHERE w.meet_id IS NULL
        ORDER BY m.meet_id
    """)

    # Fetches all the applicable rows.
    rows = cursor.fetchall()
    print(f"[weather] {len(rows)} meets to fetch")
    return rows

# ─────────────────────────────────────────────────────────────────────────────
# API helpers
# ─────────────────────────────────────────────────────────────────────────────

# _isHistorical
# Purpose: Retuurns True if the meet date is old enough for the historical API.
#          Meets within HISTORICAL_CUTOFF_DAYS of today use the forecast API.
# Arguments:
#           date_str: date string in "YYYY-MM-DD" format.
# Output: True if historical, False if forecast.
def _isHistorical(date_str: str) -> bool:

    try:
        meet_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        # Unparseable date — default to historical to be safe.
        return True
    
    # Finds the cutoff for the forecast API by taking the time today and taking
    # away HISTORICAL_CUTOFF_DAYS using timedelta
    cutoff = datetime.date.today() - datetime.timedelta(days=HISTORICAL_CUTOFF_DAYS)

    # If meet_date is before the cutoff -> historical, after -> forecast
    return meet_date <= cutoff

# _buildApiUrl
# Purpose: Returns the correct Open-Meteo URL based on whether the meet
#          date is historical or recent/future.
# Arguments:
#           date_str: date string in "YYYY-MM-DD" format.
# Output: URL string.
def _buildApiUrl(date_str: str) -> str:
    return HISTORICAL_URL if _isHistorical(date_str) else FORECAST_URL

# _fetchBatch
# Purpose: Calls Open-Meteo for a batch of meets and returns the raw
#          API responses. Handles the case where a batch contains both
#          historical and forecast meets by splitting into two calls.
# Arguments:
#           batch: list of (meet_id, gps_lat, gps_long, date) tuples.
# Output: List of (meet_id, gps_lat, gps_long, date, api_response) tuples.
#         api_response is None if the call failed for that meet.
def _fetchBatch(batch: list) -> list:

    # Split batch into historical and forecast meets based on
    # date arg in batch. Open-Meteo has separate endpoints for
    # each so we can't mix them in one call. For each element in
    # the batch we put it in the historical or forecast bucket
    # based on its date.
    historical =[m for m in batch if _isHistorical(m[3])]
    forecast = [m for m in batch if not _isHistorical(m[3])]

    results = []

    if historical:
        results += _callOpenMeteo(historical, HISTORICAL_URL)
    
    if forecast:
        results += _callOpenMeteo(forecast, FORECAST_URL)

    return results

def _callOpenMeteo(meets: list, url: str) -> list:

    results = []

    for meet_id, lat, long, date in meets:

        try:
            
            # Build query parameters for this meet.
            # Open-Meteo takes lat/lon, a date range (we use the saem date for
            # start and end to get just that day), and the variables.
            params = {
                "latitude":        lat,
                "longitude":       lon,
                "start_date":      date,
                "end_date":        date,
                # Joins all the hourly variables together
                # into a comma-separate string.
                "hourly":          ",".join(HOURLY_VARIABLES),
                "timezone":        "UTC",  # always fetch in UTC, convert later
                "wind_speed_unit": "kmh",  # consistent units
            }

            response = requests.get(url, params=params, timeout=3-)

            # 200 = success. Anything else means the API rejected our request.
            # In that case we append it with no response.
            if response.status_code != 200:
                print(f"[weather] API error for meet {meet_id}: "
                      f"status {response.status_code}")
                results.append((meet_id, lat, lon, date, None))
                continue
            
            # Append response.json because it succeeded.
            results.append((meet_id, lat, lon, date, response.json()))
 
        except Exception as e:
            print(f"[weather] Exception fetching meet {meet_id}: {e}")
            results.append((meet_id, lat, lon, date, None))
 
    return results

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
def _utcToLocalhours(lat: float, long: float, 
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
# Purpose: Parses the Open-Meteo API response for one meet and saves
#          24 rows (one per local hour) to the weather table.
# Arguments:
#           conn: psycopg2 connection from the pool.
#           meet_id: athletic.net meet ID.
#           lat: meet latitude.
#           lon: meet longitude.
#           date_str: meet date string.
#           data: parsed JSON response from Open-Meteo.
#           tf: shared TimezoneFinder instance.
#           fetched_at: UTC timestamp string for this fetch.
# Output: True if saved successfully, False if parsing failed.
def _parseAndSave(conn, meet_id: int, lat: float, lon: float,
                  date_str: str, data: dict,
                  tf: TimezoneFinder, fetched_at: str) -> bool:
    
    try:
        # Stores the hourly data for that day.
        hourly = data.get("hourly", {})

        # Open-Meteo returns each variable as a list of 24 values,
        # one per hour of the day in UTC. We assign all the actualy
        # values to these lists. We assign None if no value exists.
        # e.g. hourly["temperature_2m"] = [12.1, 11.8, 11.5, ..., 18.3]
        #       index 0 = midnight UTC, index 23 = 11pm UTC
        temps        = hourly.get("temperature_2m", [None] * 24)
        dew_points   = hourly.get("dew_point_2m", [None] * 24)
        humidities   = hourly.get("relative_humidity_2m", [None] * 24)
        apparent     = hourly.get("apparent_temperature", [None] * 24)
        precip       = hourly.get("precipitation", [None] * 24)
        codes        = hourly.get("weather_code", [None] * 24)
        pressures    = hourly.get("surface_pressure", [None] * 24)
        clouds       = hourly.get("cloud_cover", [None] * 24)
        wind_speeds  = hourly.get("wind_speed_10m", [None] * 24)
        wind_dirs    = hourly.get("wind_direction_10m", [None] * 24)

        # Get the UTC → local hour mapping for this meet's location.
        utc_to_local = _utcToLocalHours(lat, lon, date_str, tf)
 
        # Builds one row per hour using the lists we made.
        rows = []
        for utc_hour in range(24):
            local_hour = utc_to_local[utc_hour]
            rows.append((
                meet_id,
                local_hour,
                temps[utc_hour]       if utc_hour < len(temps)       else None,
                dew_points[utc_hour]  if utc_hour < len(dew_points)  else None,
                humidities[utc_hour]  if utc_hour < len(humidities)  else None,
                apparent[utc_hour]    if utc_hour < len(apparent)     else None,
                precip[utc_hour]      if utc_hour < len(precip)       else None,
                codes[utc_hour]       if utc_hour < len(codes)        else None,
                pressures[utc_hour]   if utc_hour < len(pressures)    else None,
                clouds[utc_hour]      if utc_hour < len(clouds)        else None,
                wind_speeds[utc_hour] if utc_hour < len(wind_speeds)  else None,
                wind_dirs[utc_hour]   if utc_hour < len(wind_dirs)    else None,
                fetched_at
            ))
 
        cursor = conn.cursor()

        # Insert all 24 rows in one query using execute_values.
        # ON CONFLICT DO NOTHING means if we somehow re-fetch a meet
        # that already has weather data, we skip it cleanly.
        psycopg2.extras.execute_values(cursor, """
            INSERT INTO weather (
                meet_id, hour,
                temp_c, dew_point_c, humidity, apparent_temp_c,
                precipitation_mm, weather_code,
                pressure_hpa, cloud_cover,
                wind_speed_kmh, wind_dir,
                fetched_at
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
# Progress tracking
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
    
# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

# main
# Purpose: Runs the full weather backfill pipeline.
#          Fetches weather for all meets with GPS coordinates that don't
#          already have weather data, in batches of BATCH_SIZE.
# Arguments: None.
# Output: None.
def main():

    print("[weather] Starting weather backfill")
 
    initPool()
 
    # TimezoneFinder is expensive to initialize (loads a ~20MB timezone
    # database) so we create one instance and reuse it for every meet.
    tf = TimezoneFinder()
 
    # Timestamp for all rows fetched in this run.
    fetched_at = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
 
    start_time = time.time()
 
    try:
        with getConn() as conn:
 
            # Create weather table if it doesn't exist yet.
            _createWeatherTable(conn)
 
            # Get all meets that still need weather data.
            meets = _getMeetsToFetch(conn)
 
        total   = len(meets)
        fetched = 0
        failed  = 0
 
        if total == 0:
            print("[weather] All meets already have weather data — nothing to do")
            return
        
        # Process meets in batches of BATCH_SIZE.
        for i in range(0, total, BATCH_SIZE):
            
            # Gets a batch in batches of BATCH_SIZE. This takes
            # the index for i to i + BATCH_SIZE.
            batch = meets[i:i + BATCH_SIZE]
 
            # Fetch weather from Open-Meteo for this batch.
            results = _fetchBatch(batch)
 
            # Save each meet's weather data to the DB.
            with getConn() as conn:
                for meet_id, lat, lon, date, data in results:
 
                    if data is None:
                        # API call failed for this meet — skip it.
                        # It will be retried on the next run since it
                        # won't have a weather row.
                        failed += 1
                        continue
 
                    success = _parseAndSave(
                        conn, meet_id, lat, lon, date,
                        data, tf, fetched_at
                    )
 
                    if success:
                        fetched += 1
                    else:
                        failed += 1
 
            # Print progress every batch.
            _printProgress(fetched + failed, total, start_time)
 
            # Brief pause between batches to be a good API citizen.
            time.sleep(SLEEP_BETWEEN_BATCHES)
 
        elapsed = time.time() - start_time
        print(f"\n[weather] Done — {fetched} fetched, {failed} failed "
              f"in {elapsed/3600:.1f}h")
 
    finally:
        closePool()
 
 
if __name__ == "__main__":
    main()