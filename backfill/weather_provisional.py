#!/usr/bin/env python3
"""
weather_provisional.py -- weather for the races ERA5 has not reached yet,
from Open-Meteo's weather models, marked provisional until ERA5 replaces it.
Nightly step 04w.

    /srv/venv/bin/python backfill/weather_provisional.py            # DRY RUN: what it would fetch
    /srv/venv/bin/python backfill/weather_provisional.py --apply    # fetch, store, re-rate those races
    /srv/venv/bin/python backfill/weather_provisional.py --apply --days 14

★ WHY (owner, 2026-11-02: the NESCAC race of Oct 31 had no weather; "yes").
  The ratings read hourly weather per 0.25-degree cell from weather_grid,
  filled from ERA5, which runs about five days behind (era5_cds_recent.py).
  So a race from this week was rated with no weather at all until a later
  full run. Open-Meteo serves model weather (the forecast models' own
  analysis of the hours just past) for any point within hours of the race.

  1. the work: every (cell, day) a meet in the last --days needs that
     weather_grid lacks (same snap as the ERA5 scripts: 0.25 degree, the
     longitude wrapped into 0..360)
  2. fetch the day's 24 UTC hours per cell from Open-Meteo, in the ERA5
     scripts' units and derivations: degC, hPa, %, mm, km/h, W/m2;
     apparent temperature by the same Steadman formula (absorbed sun
     0.175); soil moisture as 0-7 cm like ERA5's top layer; snow as water
     equivalent
  3. store with provisional = true; atmost_era5_zarr.writeWeatherRows
     overwrites a provisional row with ERA5 when it arrives, and its
     resume check does not count a provisional day as done
  4. re-normalise the races on those cells and days (fix_stragglers.
     renormalize): rows whose time moved lose their rating, and the night's
     fill_ratings prices them with the weather

! THE WEATHER CORRECTION WAS FITTED ON ERA5. Model weather can differ by a
  degree or two; for the days until ERA5 replaces it, a race's weather
  adjustment can be a little off. That is the trade the owner took.
! THE KEY. OPENMETEO_KEY in /etc/xc-predictor.env uses the paid
  (commercial-licence) endpoint, as weather_backfill.py does; without it
  the free endpoint, which Open-Meteo licenses for non-commercial use.
"""
import argparse
import datetime
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GRID_DEG = 0.25
SUN_ABSORBED_FRACTION = 0.175          # atmost_era5_zarr's, for the same apparent temperature
BATCH = 100                            # coordinates per request
PAUSE_S = 1.0
HOURLY = ("temperature_2m,dew_point_2m,relative_humidity_2m,precipitation,surface_pressure,"
          "cloud_cover,shortwave_radiation,wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
          "soil_moisture_0_to_1cm,soil_moisture_1_to_3cm,soil_moisture_3_to_9cm,"
          "snow_depth,snowfall")
SNOW_DENSITY = 0.2                     # snow depth (m) -> water equivalent (m), a typical pack


def snap(lat, lon):
    """(cell_lat, cell_lon) the way the ERA5 scripts and backfill_normalize
    snap: to the 0.25 grid first, longitude then wrapped into 0..360. Pure."""
    return (round(round(float(lat) / GRID_DEG) * GRID_DEG, 2),
            round((round(float(lon) / GRID_DEG) * GRID_DEG) % 360, 2))


def lon180(lon):
    return round(((float(lon) + 180.0) % 360.0) - 180.0, 2)


def apparent(temp_c, rh, wind_kmh, solar):
    """Steadman apparent temperature, atmost_era5_zarr._deriveApparentTemperature's
    exact formula. Pure."""
    ws = wind_kmh / 3.6
    e = (rh / 100.0) * 6.105 * math.exp((17.27 * temp_c) / (237.7 + temp_c))
    return (temp_c + 0.348 * e - 0.70 * ws
            + 0.70 * (SUN_ABSORBED_FRACTION * solar / (ws + 10.0)) - 4.25)


def rowsFrom(cell, payload):
    """One location's Open-Meteo hourly payload -> weather_grid rows in the
    ERA5 scripts' units. Pure."""
    h = payload.get("hourly") or {}
    times = h.get("time") or []
    out = []

    def v(k, i, default=None):
        arr = h.get(k) or []
        x = arr[i] if i < len(arr) else None
        return default if x is None else float(x)
    for i, t in enumerate(times):
        temp, rh = v("temperature_2m", i), v("relative_humidity_2m", i)
        if temp is None or rh is None:
            continue
        wind, solar = v("wind_speed_10m", i, 0.0), v("shortwave_radiation", i, 0.0)
        s01, s13, s39 = (v("soil_moisture_0_to_1cm", i), v("soil_moisture_1_to_3cm", i),
                         v("soil_moisture_3_to_9cm", i))
        soil = None
        if None not in (s01, s13, s39):
            soil = (1 * s01 + 2 * s13 + 4 * s39) / 7.0      # the 0-7 cm layer ERA5 reports
        out.append({
            "cell_lat": cell[0], "cell_lon": cell[1],
            "date": t[:10], "hour": int(t[11:13]),
            "temperature_2m": temp,
            "dew_point_2m": v("dew_point_2m", i),
            "relative_humidity_2m": rh,
            "apparent_temperature": apparent(temp, rh, wind, solar),
            "precipitation": v("precipitation", i, 0.0),
            "weather_code": None,
            "surface_pressure": v("surface_pressure", i),
            "cloud_cover": v("cloud_cover", i),
            "solar_radiation": solar,
            "wind_speed_10m": wind,
            "wind_direction_10m": v("wind_direction_10m", i),
            "wind_gust": v("wind_gusts_10m", i),
            "soil_moisture": soil,
            "snow_depth": v("snow_depth", i, 0.0) * SNOW_DENSITY,
            "snowfall": v("snowfall", i, 0.0) * 0.001,       # cm of snow -> m of water
            "provisional": True,
        })
    return out


_WORK = """
    WITH m AS (
        SELECT DISTINCT m.gps_lat, m.gps_long, substr(r.date, 1, 10) AS d, m.source, m.meet_id
        FROM   meets m JOIN results r ON r.meet_id = m.meet_id AND r.div_id = m.div_id
                                     AND r.source = m.source
        WHERE  m.gps_lat IS NOT NULL AND m.gps_long IS NOT NULL
          AND  r.date >= %(since)s AND r.date <= %(today)s
        UNION
        SELECT DISTINCT gps_lat, gps_long, substr(meet_date::text, 1, 10), 'anet', meet_id
        FROM   meets_tf_meta
        WHERE  gps_lat IS NOT NULL AND gps_long IS NOT NULL
          AND  meet_date::text >= %(since)s AND meet_date::text <= %(today)s
        UNION
        SELECT DISTINCT gps_lat, gps_long, substr(date::text, 1, 10), 'tfrrs', meet_id
        FROM   meets_tfrrs
        WHERE  gps_lat IS NOT NULL AND gps_long IS NOT NULL
          AND  date::text >= %(since)s AND date::text <= %(today)s
    )
    SELECT gps_lat, gps_long, d, source, meet_id FROM m
"""


def work(cur, days):
    """({(cell, day): [(source, meet_id)]}) for the meets of the last `days`
    whose cell and day weather_grid lacks."""
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=days)).isoformat()
    cur.execute(_WORK, {"since": since, "today": today.isoformat()})
    need = {}
    for lat, lon, d, src, mid in cur.fetchall():
        need.setdefault((snap(lat, lon), d), set()).add((src, int(mid)))
    if not need:
        return {}
    cells = sorted({c for c, _d in need})
    cur.execute("""SELECT DISTINCT cell_lat, cell_lon, date::text FROM weather_grid
                   WHERE date >= %s AND (cell_lat, cell_lon) IN %s""",
                (since, tuple(cells)))
    have = {((float(a), float(b)), str(d)[:10]) for a, b, d in cur.fetchall()}
    return {k: v for k, v in need.items() if k not in have}


def fetchDay(day, cells):
    """{cell: payload} for one day's cells from Open-Meteo."""
    key = os.environ.get("OPENMETEO_KEY", "").strip()
    base = ("https://customer-api.open-meteo.com/v1/forecast" if key
            else "https://api.open-meteo.com/v1/forecast")
    out = {}
    for i in range(0, len(cells), BATCH):
        part = cells[i:i + BATCH]
        q = {"latitude": ",".join(str(c[0]) for c in part),
             "longitude": ",".join(str(lon180(c[1])) for c in part),
             "start_date": day, "end_date": day, "hourly": HOURLY,
             "timezone": "GMT", "wind_speed_unit": "kmh"}
        if key:
            q["apikey"] = key
        url = base + "?" + urllib.parse.urlencode(q)
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict):
            data = [data]
        for c, payload in zip(part, data):
            out[c] = payload
        time.sleep(PAUSE_S)
    return out


def ensureColumn(cur):
    cur.execute("ALTER TABLE weather_grid ADD COLUMN IF NOT EXISTS provisional boolean "
                "NOT NULL DEFAULT false")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--days", type=int, default=10)
    a = ap.parse_args()
    from database import getConn
    from psycopg2.extras import execute_values
    with getConn() as conn:
        with conn.cursor() as cur:
            todo = work(cur, a.days)
        conn.rollback()
        by_day = {}
        for (cell, d), _m in todo.items():
            by_day.setdefault(d, []).append(cell)
        print(f"[weather] {len(todo):,} cell-days in the last {a.days} days without weather "
              f"({len(by_day)} days, {sum(len(v) for v in todo.values()):,} meets)")
        if not todo or not a.apply:
            if todo and not a.apply:
                print("  DRY RUN -- --apply fetches them")
            return 0
        n_rows, races = 0, set()
        with conn.cursor() as cur:
            ensureColumn(cur)
            conn.commit()
            for d in sorted(by_day):
                try:
                    got = fetchDay(d, sorted(by_day[d]))
                except Exception as exc:                        # noqa: BLE001
                    print(f"  {d}: Open-Meteo failed ({type(exc).__name__}: {exc}); left for tomorrow")
                    continue
                rows = [r for c, p in got.items() for r in rowsFrom(c, p)]
                if not rows:
                    continue
                cols = list(rows[0].keys())
                execute_values(cur, f"INSERT INTO weather_grid ({', '.join(cols)}) VALUES %s "
                                    f"ON CONFLICT (cell_lat, cell_lon, date, hour) DO NOTHING",
                               [tuple(r[c] for c in cols) for r in rows], page_size=5000)
                conn.commit()
                n_rows += len(rows)
                for c in got:
                    races |= todo.get((c, d), set())
                print(f"  {d}: {len(got)} cells, {len(rows):,} hours")
        print(f"[weather] {n_rows:,} provisional hours stored for {len(races):,} meets")
        if races:
            from fix_stragglers import renormalize
            rc, moved = renormalize(conn, sorted(races), sport="both")
            if rc != 0:
                print(f"[weather] re-normalising exited {rc}; the weather is stored, the "
                      f"ratings follow on the next full run")
                return 0
            print(f"[weather] {moved:,} rows re-normalised with the weather; their ratings "
                  f"are cleared for fill_ratings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
