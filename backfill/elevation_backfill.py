# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Backfill
# File Title: elevation_backfill.py
# Purpose: Fetches ground elevation (metres above sea level) from Open-Meteo for
#          every venue in the database that has GPS coordinates, and writes it to
#          a new altitude_meters column on meets and meets_tf. Feeds the model's
#          altitude_delta feature (race altitude vs an athlete's prior-race
#          median altitude).
#
#          KEY DIFFERENCE FROM weather_backfill: elevation depends ONLY on
#          (lat, long) — not on date, not on time-of-day, no timezone math. A
#          venue's altitude never changes. So the unit of work is a DISTINCT
#          COORDINATE, looked up once, then written back to every row sharing it.
#          This makes it far cheaper than the weather backfill.
#
#          Resumable — coordinates whose rows already have altitude_meters are
#          skipped. Run it, stop it, run it again; it picks up where it left off.
 
import sys
import time
import requests
 
sys.path.insert(0, "scripts")
from database import initPool, closePool, getConn

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Open-Meteo's elevation endpoint. Free, no API key (same access model as the
# weather backfill). Accepts MULTIPLE coordinates per call as comma-separated
# latitude= and longitude= lists, returning an elevation[] array in order.
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
 
# Coordinates per API call. Open-Meteo's elevation endpoint accepts up to 100
# lat/long pairs in one request, so we look up 100 venues per HTTP call instead
# of one-at-a-time. This is the single biggest efficiency lever here.
BATCH_SIZE = 1000
 
# Small pause between calls — same "good API citizen" courtesy as the weather
# backfill. At 100 coords/call and 10k calls/day, throughput is not a concern.
SLEEP_BETWEEN_BATCHES = 0.5  # seconds
 
# Per-request network timeout (seconds).
REQUEST_TIMEOUT = 30


# ─────────────────────────────────────────────────────────────────────────────
# Column migration
# ─────────────────────────────────────────────────────────────────────────────


# _addAltitudeColumns
# Purpose: Add the altitude_meters column to meets and meets_tf if absent.
#          ADD COLUMN IF NOT EXISTS is idempotent and — for a NULLable column
#          with no default — effectively instant on Postgres (no table rewrite,
#          no lock of consequence), even on the large meets tables.
# Arguments:
#           conn: psycopg2 connection from the pool.
# Output:   None.
def _addAltitudeColumns(conn):
    cursor = conn.cursor()
    cursor.execute("ALTER TABLE meets    ADD COLUMN IF NOT EXISTS altitude_meters REAL")
    cursor.execute("ALTER TABLE meets_tf ADD COLUMN IF NOT EXISTS altitude_meters REAL")
    conn.commit()
    print("[elevation] altitude_meters column ready on meets, meets_tf")


# ─────────────────────────────────────────────────────────────────────────────
# Work query
# ─────────────────────────────────────────────────────────────────────────────


# _getCoordsToFetch
# Purpose: Return the DISTINCT (lat, long) pairs that still need an elevation —
#          i.e. coordinates that appear in meets/meets_tf with non-NULL GPS but
#          where rows at that coordinate still have altitude_meters NULL.
#          Deduplicated ACROSS BOTH tables (a venue hosts both XC and TF, so the
#          same coordinate must be looked up only once).
# Arguments:
#           conn: psycopg2 connection from the pool.
# Output:   list of (lat, long) tuples.
def _getCoordsToFetch(conn) -> list:
    cursor = conn.cursor()
 
    # UNION the still-NULL coordinates from both tables, then DISTINCT the union
    # so a venue shared by XC and TF is one lookup. We key on the raw stored
    # lat/long pair (the same values we'll match on when writing back), so the
    # write-back UPDATE lines up exactly with what we fetched.
    cursor.execute("""
        SELECT DISTINCT lat, long FROM (
            SELECT gps_lat AS lat, gps_long AS long
            FROM meets
            WHERE gps_lat IS NOT NULL
              AND gps_long IS NOT NULL
              AND altitude_meters IS NULL
 
            UNION
 
            SELECT gps_lat AS lat, gps_long AS long
            FROM meets_tf
            WHERE gps_lat IS NOT NULL
              AND gps_long IS NOT NULL
              AND altitude_meters IS NULL
        ) coords
        ORDER BY lat, long
    """)
 
    rows = cursor.fetchall()
    print(f"[elevation] {len(rows)} distinct coordinates to fetch")
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────


# _fetchElevations
# Purpose: Look up elevation for a batch of up to BATCH_SIZE coordinates in ONE
#          Open-Meteo call. The endpoint takes comma-separated latitude= and
#          longitude= lists and returns an elevation[] array in the SAME ORDER,
#          so result[i] is the elevation for batch[i].
# Arguments:
#           batch: list of (lat, long) tuples (length <= BATCH_SIZE).
# Output:   list of (lat, long, elevation) tuples. elevation is None for the
#           whole batch if the call failed (so the caller leaves them NULL and a
#           later run retries them).
def _fetchElevations(batch: list) -> list:
    # Build the parallel comma-separated coordinate lists the endpoint expects.
    lats = ",".join(str(lat)  for lat, _    in batch)
    longs = ",".join(str(long) for _,  long in batch)
 
    try:
        response = requests.get(
            ELEVATION_URL,
            params={"latitude": lats, "longitude": longs},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            print(f"[elevation] API error: status {response.status_code} "
                  f"(batch of {len(batch)})")
            return [(lat, long, None) for lat, long in batch]
 
        # The response JSON has an "elevation" list aligned to the input order.
        elevations = response.json().get("elevation", [])
 
        # Defensive: if the array length doesn't match what we sent, we can't
        # trust the alignment — fail the whole batch to None rather than risk
        # writing the wrong altitude to a venue.
        if len(elevations) != len(batch):
            print(f"[elevation] length mismatch: sent {len(batch)}, "
                  f"got {len(elevations)} — failing batch")
            return [(lat, long, None) for lat, long in batch]
 
        # Zip the elevations back onto their coordinates, in order.
        return [(lat, long, elevations[i])
                for i, (lat, long) in enumerate(batch)]
 
    except Exception as e:
        print(f"[elevation] Exception fetching batch of {len(batch)}: {e}")
        return [(lat, long, None) for lat, long in batch]
 
 
# ─────────────────────────────────────────────────────────────────────────────
# Write-back
# ─────────────────────────────────────────────────────────────────────────────
 
# _saveElevations
# Purpose: Write each fetched elevation back to EVERY row at that coordinate, in
#          BOTH meets and meets_tf. One coordinate -> potentially many divisions
#          across both sports, all sharing the venue's altitude.
# Arguments:
#           conn:    psycopg2 connection from the pool.
#           results: list of (lat, long, elevation) tuples from _fetchElevations.
# Output:   number of coordinates actually written (elevation not None).
def _saveElevations(conn, results: list) -> int:
    cursor = conn.cursor()
    written = 0
 
    for lat, long, elevation in results:
        if elevation is None:
            # Failed lookup — leave altitude NULL so a later run retries it.
            continue
 
        # Match the SAME stored lat/long we fetched from. These are the exact
        # REAL values already in the table (we SELECTed them out, didn't compute
        # them), so equality matching is safe — we're comparing a value against
        # itself, not against a recomputed float. Update both tables; the WHERE
        # also guards on altitude_meters IS NULL so we only touch still-empty
        # rows (keeps the write idempotent and cheap on re-runs).
        cursor.execute("""
            UPDATE meets SET altitude_meters = %s
            WHERE gps_lat = %s AND gps_long = %s AND altitude_meters IS NULL
        """, (elevation, lat, long))
 
        cursor.execute("""
            UPDATE meets_tf SET altitude_meters = %s
            WHERE gps_lat = %s AND gps_long = %s AND altitude_meters IS NULL
        """, (elevation, lat, long))
 
        written += 1
 
    conn.commit()   # getConn does not autocommit — commit the batch's writes.
    return written
 
 
# ─────────────────────────────────────────────────────────────────────────────
# Progress
# ─────────────────────────────────────────────────────────────────────────────
 
# _printProgress
# Purpose: Print a progress line with a rough ETA, mirroring the weather
#          backfill's reporting.
# Arguments:
#           done:       coordinates processed so far.
#           total:      total coordinates to process.
#           start_time: time.time() at run start.
# Output:   None.
def _printProgress(done: int, total: int, start_time: float):
    elapsed   = time.time() - start_time
    per       = elapsed / done if done > 0 else 0
    remaining = (total - done) * per
    hours     = int(remaining // 3600)
    minutes   = int((remaining % 3600) // 60)
    pct       = (100 * done / total) if total else 100.0
    print(f"[elevation] {done}/{total} coords ({pct:.1f}%) — "
          f"~{hours}h {minutes}m remaining")
 
 
# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
 
# main
# Purpose: Run the full elevation backfill: add the column, find distinct
#          coordinates still needing elevation, look them up in batches, and
#          write each back to both tables. Resumable and idempotent.
# Arguments: None.
# Output:   None.
def main():
    print("[elevation] Starting elevation backfill")
    initPool()
    start_time = time.time()
 
    try:
        # Add the column and pull the work list up front.
        with getConn() as conn:
            _addAltitudeColumns(conn)
            coords = _getCoordsToFetch(conn)
 
        total = len(coords)
        if total == 0:
            print("[elevation] All venues already have elevation — nothing to do")
            return
 
        done = 0
        failed = 0
        for i in range(0, total, BATCH_SIZE):
            batch = coords[i:i + BATCH_SIZE]
            results = _fetchElevations(batch)
 
            with getConn() as conn:
                written = _saveElevations(conn, results)
 
            done   += written
            failed += len(batch) - written
            _printProgress(done + failed, total, start_time)
 
            time.sleep(SLEEP_BETWEEN_BATCHES)
 
        elapsed = time.time() - start_time
        print(f"\n[elevation] Done — {done} coords written, {failed} failed "
              f"in {elapsed/3600:.2f}h")
 
    finally:
        closePool()
 
 
if __name__ == "__main__":
    main()