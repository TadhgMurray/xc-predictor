"""
weather_from_grid.py -- fill the model's weather table from weather_grid.

    python scripts/weather_from_grid.py            # dry run: counts + samples
    python scripts/weather_from_grid.py --apply    # write

★ TWO WEATHER SYSTEMS, ONE BRIDGE. The ratings' weather lives in
  weather_grid: hourly ERA5 sampled per 25km cell by
  backfill/atmost_era5_zarr.py, already converted to the site's units
  (degC, hPa, %, mm) with humidity, wind speed/direction and Steadman
  apparent temperature derived at write time. The MODEL's feature
  extraction reads a different table -- `weather`, per-meet hourly,
  meant for an Open-Meteo backfill that was never run, so it trained
  on zeros (or died on the missing table, before the guard).

  This derives `weather` FROM the grid: each meet with GPS snaps to
  its ERA5 cell exactly the way backfill_normalize does, takes the
  race date's 24 grid hours, converts UTC to local with the fitter's
  own longitude/15 approximation, and writes the per-meet rows the
  corpus SQL joins. Open-Meteo (weather_backfill.py / atmost_oneshot
  stage 3) stays the finer-resolution upgrade for someday; rows it
  fetched are never overwritten (ON CONFLICT DO NOTHING).

★ THE SNAP MUST MATCH THE BACKFILL, or every join misses: snap to the
  0.25-degree grid FIRST, then wrap longitude into 0..360, round 2dp
  -- backfill_normalize._snapCell's exact arithmetic, in SQL.

Idempotent and resumable; safe to re-run after new meets arrive.
"""

import argparse
import sys
import time

sys.path.insert(0, "scripts")
sys.path.insert(0, "backfill")

from database import getConn                       # noqa: E402

# weather_backfill.py owns this shape, but importing it drags in
# timezonefinder (an Open-Meteo-fetch dependency this derivation never
# needs), so the DDL is inlined -- keep it matching _createWeatherTable.
_WEATHER_DDL = """
    CREATE TABLE IF NOT EXISTS weather(
        meet_id             BIGINT,
        source              TEXT NOT NULL DEFAULT 'anet',
        hour                INTEGER,
        temp_c              REAL,
        dew_point_c         REAL,
        humidity            REAL,
        apparent_temp_c     REAL,
        precipitation_mm    REAL,
        weather_code        INTEGER,
        pressure_hpa        REAL,
        cloud_cover         INTEGER,
        wind_speed_kmh      REAL,
        wind_dir            REAL,
        fetched_at          TEXT,
        used_utc_fallback   BOOLEAN DEFAULT FALSE,
        PRIMARY KEY(meet_id, source, hour)
    )
"""


def ensureWeatherTable(conn):
    with conn.cursor() as cur:
        cur.execute(_WEATHER_DDL)

GRID = 0.25

# snap-then-wrap, as stored (backfill_normalize._snapCell in SQL).
# Python's % is a true modulo for negatives and so is Postgres mod()
# on the already-positive result of (x % 360 + 360).
_CELL_LAT = f"round((round(({{lat}})::numeric / {GRID}) * {GRID}), 2)"
_CELL_LON = (f"round((mod(mod((round(({{lon}})::numeric / {GRID}) * {GRID}),"
             f" 360) + 360, 360)), 2)")

# grid hour is UTC; the weather table's hour is LOCAL. The fitter's own
# approximation: offset = round(signed_longitude / 15).
_LOCAL_HOUR = ("mod(mod(g.hour + round((CASE WHEN g.cell_lon > 180 "
               "THEN g.cell_lon - 360 ELSE g.cell_lon END) / 15.0)::int, 24)"
               " + 24, 24)")

# (label, meets table, results table for the meet's date)
_STREAMS = [
    ("XC", "meets", "results"),
    ("TF", "meets_tf", "results_tf"),
]


def _sql(meets, results, apply):
    meet_dates = f"""
        SELECT m.meet_id, m.source,
               {_CELL_LAT.format(lat='max(m.gps_lat)')}  AS cell_lat,
               {_CELL_LON.format(lon='max(m.gps_long)')} AS cell_lon,
               min(r.date)::date                         AS date
        FROM   {meets} m
        JOIN   {results} r ON r.meet_id = m.meet_id AND r.source = m.source
        WHERE  m.gps_lat IS NOT NULL AND m.gps_long IS NOT NULL
          AND  r.date ~ '^[0-9]{{4}}-'
        GROUP  BY m.meet_id, m.source
    """
    select = f"""
        SELECT mm.meet_id, mm.source, {_LOCAL_HOUR} AS hour,
               g.temperature_2m, g.dew_point_2m, g.relative_humidity_2m,
               g.apparent_temperature, g.precipitation, g.surface_pressure,
               round(g.cloud_cover)::int,
               g.wind_speed_10m * 3.6, g.wind_direction_10m
        FROM   ({meet_dates}) mm
        JOIN   weather_grid g ON g.cell_lat = mm.cell_lat
                             AND g.cell_lon = mm.cell_lon
                             AND g.date     = mm.date
    """
    if not apply:
        return select
    return f"""
        INSERT INTO weather (meet_id, source, hour, temp_c, dew_point_c,
                             humidity, apparent_temp_c, precipitation_mm,
                             pressure_hpa, cloud_cover, wind_speed_kmh,
                             wind_dir, fetched_at)
        SELECT q.*, 'era5-grid' FROM ({select}) q
        ON CONFLICT (meet_id, source, hour) DO NOTHING
    """


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write; without it, counts and samples only")
    args = ap.parse_args()

    t0 = time.time()
    with getConn() as conn:
        cur = conn.cursor()
        ensureWeatherTable(conn)
        conn.commit()
        for label, meets, results in _STREAMS:
            if not args.apply:
                cur.execute(f"SELECT count(*), count(DISTINCT meet_id) "
                            f"FROM ({_sql(meets, results, False)}) q")
                rows, n_meets = cur.fetchone()
                cur.execute(f"SELECT * FROM ({_sql(meets, results, False)}) q "
                            f"LIMIT 3")
                print(f"  {label}: would write ~{rows:,} hourly rows "
                      f"for {n_meets:,} meets")
                for r in cur.fetchall():
                    print(f"    sample: meet {r[0]} {r[1]} h{r[2]:>2} "
                          f"temp {r[3]:.1f}C dew {r[4]:.1f}C rh {r[5]:.0f}% "
                          f"feels {r[6]:.1f}C precip {r[7]:.2f}mm "
                          f"p {r[8]:.0f}hPa cloud {r[9]}% "
                          f"wind {r[10]:.1f}km/h dir {r[11]:.0f}")
                continue
            t1 = time.time()
            cur.execute(_sql(meets, results, True))
            n = cur.rowcount
            conn.commit()
            print(f"  {label}: wrote {n:,} hourly rows "
                  f"({(time.time() - t1) / 60:.1f} min)")

        if args.apply:
            cur.execute("""
                SELECT count(*), count(DISTINCT (meet_id, source)),
                       min(temp_c), max(temp_c), min(humidity), max(humidity),
                       min(pressure_hpa), max(pressure_hpa)
                FROM weather
            """)
            n, m, tlo, thi, hlo, hhi, plo, phi = cur.fetchone()
            print(f"  weather now: {n:,} rows, {m:,} meets; "
                  f"temp {tlo:.0f}..{thi:.0f}C, humidity {hlo:.0f}..{hhi:.0f}%,"
                  f" pressure {plo:.0f}..{phi:.0f}hPa")
            bad = (tlo is not None and (tlo < -60 or thi > 60)) or \
                  (plo is not None and (plo < 700 or phi > 1150))
            if bad:
                print("  WARNING: ranges look wrong -- units slipped "
                      "somewhere. Check before training on this.")
    print(f"done in {(time.time() - t0) / 60:.1f} min "
          f"({'applied' if args.apply else 'dry run'})")


if __name__ == "__main__":
    main()
