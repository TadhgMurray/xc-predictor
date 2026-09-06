"""forecast.py -- the weather a predicted race would be run in.

Two answers for one venue-day-hour, both shaped like a row of the model's
`weather` table (temp_c, dew_point_c, humidity, apparent_temp_c,
precipitation_mm, pressure_hpa, cloud_cover, wind_speed_kmh, wind_dir):

    normalAt(cur, lat, lon, date, hour)   the venue's OWN normal for that
                                          time of year at that hour: every
                                          year of the ERA5 grid cell, the
                                          fortnight around the date, averaged
    forecastAt(lat, lon, date, hour)      Open-Meteo's forecast for that hour,
                                          16 days out at most, cached per
                                          (place, day, hour) for six hours

★ THE PREDICTIONS PAGE SHOWS BOTH (owner, 2026-09-06: "a race at normal
  weather / a race at forecasted weather"). The model takes the target's
  weather in its context vector, so each answer is one more pass of the
  network on the same history, not a second model.

! FREE, KEYLESS, NON-COMMERCIAL. Open-Meteo's free tier is for
  non-commercial use, ~10,000 calls a day; the cache keeps a popular meet
  page at one call per venue-day. If the site ever earns, swap SOURCE for
  the NWS client (US only, no commercial clause) or Open-Meteo's paid tier
  -- the row shape is the seam.

! NEVER A 500. No network, a slow answer, a date out of range, a venue
  with no coordinates, a database role that cannot create the cache table:
  every one of those returns None with a reason, and the page shows the
  normal-weather number alone.
"""
import json
import math
import threading
import time
import urllib.parse
import urllib.request
from datetime import date as _date, datetime, timedelta, timezone

SOURCE = "open-meteo"
_URL = "https://api.open-meteo.com/v1/forecast"
_HOURLY = ("temperature_2m", "dew_point_2m", "relative_humidity_2m",
           "apparent_temperature", "precipitation", "surface_pressure",
           "cloud_cover", "wind_speed_10m", "wind_direction_10m")
# Open-Meteo name -> the model's `weather` column. Units agree without
# conversion: degC, %, mm, hPa, %, km/h, degrees.
_FIELD = {"temperature_2m": "temp_c", "dew_point_2m": "dew_point_c",
          "relative_humidity_2m": "humidity", "apparent_temperature": "apparent_temp_c",
          "precipitation": "precipitation_mm", "surface_pressure": "pressure_hpa",
          "cloud_cover": "cloud_cover", "wind_speed_10m": "wind_speed_kmh",
          "wind_direction_10m": "wind_dir"}
FIELDS = tuple(_FIELD.values())

# ! MIRRORS model/feature_extraction.XC_DEFAULT_HOUR / TF_DEFAULT_HOUR: the
#   hour the model's training rows took their weather at. Not imported --
#   that module parses corrections.py at import. tests/test_forecast.py pins
#   the two.
RACE_HOUR = {"XC": 9, "TF": 15}

HORIZON_DAYS = 16          # Open-Meteo's reach
FRESH_SECONDS = 6 * 3600   # a forecast older than this is fetched again
TIMEOUT = 4.0              # seconds a page will wait on the network
_GRID = 0.25               # ERA5 cell, as backfill_normalize snaps it

_mem = {}                  # (kind, key) -> (expires, row); the in-process layer
_lock = threading.Lock()


def raceHour(sport):
    return RACE_HOUR.get((sport or "XC").upper(), 9)


# ------------------------------------------------------------------ #
#  the venue's normal
# ------------------------------------------------------------------ #

def _snapCell(lat, lon):
    clat = round(round(float(lat) / _GRID) * _GRID, 2)
    clon = round((round(float(lon) / _GRID) * _GRID) % 360, 2)
    return clat, clon


def normalAt(cur, lat, lon, day, hour, window_days=14):
    """The cell's average at that local hour over every year, within
    `window_days` of the date's day-of-year. None when the grid has no
    rows there (a venue with no coordinates, a cell never fetched)."""
    if lat is None or lon is None or not day:
        return None
    try:
        d = _asDate(day)
        clat, clon = _snapCell(lat, lon)
    except (TypeError, ValueError):
        return None
    key = ("normal", clat, clon, d.timetuple().tm_yday // 7, int(hour))
    hit = _memGet(key)
    if hit is not None:
        return hit
    signed = "(CASE WHEN cell_lon > 180 THEN cell_lon - 360 ELSE cell_lon END)"
    local = f"mod(mod(hour + round({signed} / 15.0)::int, 24) + 24, 24)"
    doy = d.timetuple().tm_yday
    try:
        cur.execute(f"""
            SELECT avg(temperature_2m), avg(dew_point_2m), avg(relative_humidity_2m),
                   avg(apparent_temperature), avg(precipitation), avg(surface_pressure),
                   avg(cloud_cover), avg(wind_speed_10m), count(*)
            FROM   weather_grid
            WHERE  cell_lat = %s AND cell_lon = %s AND {local} = %s
              AND  abs(extract(doy FROM date) - %s) <= %s
        """, (clat, clon, int(hour), doy, window_days))
        r = cur.fetchone()
        if isinstance(r, dict):
            r = list(r.values())
    except Exception:                                    # noqa: BLE001
        cur.connection.rollback()
        return None
    if not r or not r[8]:
        return None
    row = {"temp_c": r[0], "dew_point_c": r[1], "humidity": r[2],
           "apparent_temp_c": r[3], "precipitation_mm": r[4], "pressure_hpa": r[5],
           "cloud_cover": r[6], "wind_speed_kmh": r[7], "wind_dir": None,
           "n_hours": int(r[8]), "kind": "normal", "hour_local": int(hour)}
    _memPut(key, row, 24 * 3600)
    return row


# ------------------------------------------------------------------ #
#  the forecast
# ------------------------------------------------------------------ #

def forecastAt(lat, lon, day, hour, cur=None, now=None):
    """Open-Meteo's forecast for that local hour, or None with the reason
    in forecastReason(). `cur` enables the table cache; without it the
    in-process cache still holds a fetch for FRESH_SECONDS."""
    if lat is None or lon is None or not day:
        return None
    try:
        d = _asDate(day)
    except (TypeError, ValueError):
        return None
    today = (now or datetime.now(timezone.utc)).date()
    if d < today:
        return None                         # the past is the grid's business
    if (d - today).days > HORIZON_DAYS:
        return None
    key = ("fc", round(float(lat), 2), round(float(lon), 2), d.isoformat(), int(hour))
    hit = _memGet(key)
    if hit is not None:
        return hit
    row = _tableGet(cur, key) if cur is not None else None
    if row is None:
        row = _fetch(float(lat), float(lon), d, int(hour))
        if row is not None and cur is not None:
            _tablePut(cur, key, row)
    if row is not None:
        _memPut(key, row, FRESH_SECONDS)
    return row


def _fetch(lat, lon, d, hour):
    q = urllib.parse.urlencode({
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "hourly": ",".join(_HOURLY), "timezone": "auto",
        "start_date": d.isoformat(), "end_date": d.isoformat()})
    req = urllib.request.Request(f"{_URL}?{q}",
                                 headers={"User-Agent": "racecast.co predictions"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:                                    # noqa: BLE001
        return None
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    want = f"{d.isoformat()}T{hour:02d}:00"
    if want not in times:
        return None
    i = times.index(want)
    row = {}
    for src, dst in _FIELD.items():
        vals = hourly.get(src) or []
        v = vals[i] if i < len(vals) else None
        row[dst] = float(v) if v is not None else None
    if row.get("temp_c") is None:
        return None
    row.update({"kind": "forecast", "source": SOURCE, "hour_local": hour,
                "timezone": data.get("timezone"),
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="minutes")})
    return row


# ------------------------------------------------------------------ #
#  words for the page
# ------------------------------------------------------------------ #

def describe(row):
    """'24C, feels 27, wind 12 km/h, 3 mm rain, overcast' -- or None."""
    if not row:
        return None
    parts = []
    t = row.get("temp_c")
    if t is not None:
        parts.append(f"{t:.0f}°C")
        a = row.get("apparent_temp_c")
        if a is not None and abs(a - t) >= 2:
            parts.append(f"feels {a:.0f}")
    w = row.get("wind_speed_kmh")
    if w is not None and w >= 10:
        parts.append(f"wind {w:.0f} km/h")
    p = row.get("precipitation_mm")
    if p is not None and p >= 0.3:
        parts.append(f"{p:.1f} mm rain")
    c = row.get("cloud_cover")
    if c is not None:
        parts.append("overcast" if c >= 80 else "sunny" if c <= 20 else "some cloud")
    return ", ".join(parts) or None


# ------------------------------------------------------------------ #
#  caches
# ------------------------------------------------------------------ #

def _memGet(key):
    with _lock:
        hit = _mem.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        _mem.pop(key, None)
    return None


def _memPut(key, row, ttl):
    with _lock:
        if len(_mem) > 5000:
            _mem.clear()
        _mem[key] = (time.time() + ttl, row)


_DDL = """
CREATE TABLE IF NOT EXISTS weather_forecast (
    lat        real    NOT NULL,
    lon        real    NOT NULL,
    date       date    NOT NULL,
    hour       int     NOT NULL,
    fetched_at timestamptz NOT NULL,
    source     text    NOT NULL,
    row        jsonb   NOT NULL,
    PRIMARY KEY (lat, lon, date, hour)
)
"""
_table = {"ok": None}


def _ensureTable(cur):
    if _table["ok"] is not None:
        return _table["ok"]
    try:
        cur.execute(_DDL)
        cur.connection.commit()
        _table["ok"] = True
    except Exception:                                    # noqa: BLE001
        cur.connection.rollback()
        _table["ok"] = False
    return _table["ok"]


def _tableGet(cur, key):
    if not _ensureTable(cur):
        return None
    _k, lat, lon, day, hour = key
    try:
        cur.execute("""
            SELECT row FROM weather_forecast
            WHERE  lat = %s AND lon = %s AND date = %s AND hour = %s
              AND  fetched_at > now() - make_interval(secs => %s)
        """, (lat, lon, day, hour, FRESH_SECONDS))
        r = cur.fetchone()
    except Exception:                                    # noqa: BLE001
        cur.connection.rollback()
        return None
    if not r:
        return None
    row = r["row"] if isinstance(r, dict) else r[0]
    return row if isinstance(row, dict) else json.loads(row)


def _tablePut(cur, key, row):
    if not _ensureTable(cur):
        return
    _k, lat, lon, day, hour = key
    try:
        cur.execute("""
            INSERT INTO weather_forecast (lat, lon, date, hour, fetched_at, source, row)
            VALUES (%s, %s, %s, %s, now(), %s, %s)
            ON CONFLICT (lat, lon, date, hour) DO UPDATE
               SET fetched_at = EXCLUDED.fetched_at, source = EXCLUDED.source,
                   row = EXCLUDED.row
        """, (lat, lon, day, hour, SOURCE, json.dumps(row)))
        cur.connection.commit()
    except Exception:                                    # noqa: BLE001
        cur.connection.rollback()


def _asDate(day):
    if isinstance(day, _date):
        return day
    return _date.fromisoformat(str(day)[:10])
