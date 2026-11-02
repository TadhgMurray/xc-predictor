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
MISS_SECONDS = 15 * 60     # a failed race-day fetch is not retried sooner
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
    # ⚠ EVERY COLUMN IS ALIASED, AND THAT IS NOT STYLE (owner, 2026-09-16:
    #   every prediction died on "IndexError: list index out of range").
    #
    #   Postgres labels an unaliased avg() as just "avg" -- so eight of these
    #   nine columns came back with the SAME NAME. Under RealDictCursor,
    #   which every web route uses, the row is a DICT: the eight collapse
    #   into one key, list(r.values()) yields two values rather than nine,
    #   and r[8] is off the end.
    #
    # ! IT WORKS PERFECTLY UNDER A TUPLE CURSOR, which is how a script would
    #   reach it, so the fault only ever appeared through the website. That
    #   is also why it hid: /api/predict/weather catches everything and
    #   reports "no weather", so this read as a missing forecast for weeks
    #   rather than as a crash, until the prediction path hit it without a
    #   catch.
    #
    # ! AND THE ROW IS READ BY NAME NOW, not by position. Aliasing alone
    #   would fix today's bug and leave the next column added in the middle
    #   to silently shift every field after it.
    cols = ("temp_c", "dew_point_c", "humidity", "apparent_temp_c",
            "precipitation_mm", "pressure_hpa", "cloud_cover",
            "wind_speed_kmh", "n_hours")
    try:
        cur.execute(f"""
            SELECT avg(temperature_2m)        AS temp_c,
                   avg(dew_point_2m)          AS dew_point_c,
                   avg(relative_humidity_2m)  AS humidity,
                   avg(apparent_temperature)  AS apparent_temp_c,
                   avg(precipitation)         AS precipitation_mm,
                   avg(surface_pressure)      AS pressure_hpa,
                   avg(cloud_cover)           AS cloud_cover,
                   avg(wind_speed_10m)        AS wind_speed_kmh,
                   count(*)                   AS n_hours
            FROM   weather_grid
            WHERE  cell_lat = %s AND cell_lon = %s AND {local} = %s
              AND  abs(extract(doy FROM date) - %s) <= %s
        """, (clat, clon, int(hour), doy, window_days))
        r = cur.fetchone()
        if r is not None and not isinstance(r, dict):
            r = dict(zip(cols, r))            # a plain tuple cursor
    except Exception:                                    # noqa: BLE001
        cur.connection.rollback()
        return None
    if not r or not r.get("n_hours"):
        return None
    row = {k: r.get(k) for k in cols[:-1]}
    row.update({"wind_dir": None, "n_hours": int(r["n_hours"]),
                "kind": "normal", "hour_local": int(hour)})
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
    """'75°F, feels 81, wind 9 mph, 0.10 in rain, overcast' -- or None.

    ★ °F AND MPH, AS THE RACE PAGES (UX review, 2026-10-24): forecasts read
      in °C, km/h and mm beside race pages in °F and mph. The stored row
      stays metric (Open-Meteo's units); only the words change."""
    if not row:
        return None
    parts = []
    t = row.get("temp_c")
    if t is not None:
        parts.append(f"{t * 9 / 5 + 32:.0f}°F")
        a = row.get("apparent_temp_c")
        if a is not None and abs(a - t) >= 2:
            parts.append(f"feels {a * 9 / 5 + 32:.0f}")
    w = row.get("wind_speed_kmh")
    if w is not None and w >= 10:
        parts.append(f"wind {w / 1.609344:.0f} mph")
    p = row.get("precipitation_mm")
    if p is not None and p >= 0.3:
        parts.append(f"{p / 25.4:.2f} in rain")
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


# ------------------------------------------------------------------ #
#  the forecast as the ratings' weather correction reads it
# ------------------------------------------------------------------ #
#
# ★ WHY (owner, 2026-11-02: "sure", to forecasts moving upcoming
#   predictions). A rating is a time with the day's weather divided out
#   (normalize_distance._applyWeather: norm = time / wmult, wmult measured
#   against the venue's own normal for that fortnight). A predicted time
#   comes back from ratings, so it is a time on a TYPICAL day at that
#   course. Multiplying it by the same wmult for the forecast day is the
#   correction run in reverse: a hot or muddy forecast slows every runner
#   by exactly what the ratings would credit them for it afterwards.
#
# ! NOT THE MODEL'S WEATHER FEATURES. Those biased predictions by -5%
#   (predict._weatherVariants, app._target); this path never touches the
#   network. It is the measured correction, nothing new fitted.
#
# ! THE SAME DAY SUMMARY THE CORRECTION WAS FITTED ON: the artifact's local
#   race window (race_local_hours), avg apparent temperature (Steadman, the
#   grid's formula), avg wind, summed rain, avg 0-7 cm soil, snow as water.
#   weather_provisional.rowsFrom turns Open-Meteo's hours into grid rows,
#   so the two can never read a forecast differently.
#
# ! CROSS COUNTRY ONLY for now: a track venue's correction key is its
#   location and indoor flag (backfill_normalize._loadMeetCellsAnetTF),
#   which a prediction target does not carry yet.

_DAY_HOURLY = None          # weather_provisional.HOURLY, read on first use
_DAY_CODE = {"XC": -1, "TF": -2}     # weather_forecast.hour for a day summary


def _provisional():
    import os
    import sys
    bf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backfill")
    if bf not in sys.path:
        sys.path.insert(0, bf)
    import weather_provisional
    return weather_provisional


def daySummary(rows, lon, day, hours, min_hours=3):
    """Grid rows (UTC hours, weather_provisional.rowsFrom) -> the correction's
    day dict {apparent_temp, wind, precip, soil, snow, doy} over the LOCAL
    window `hours` (lo, hi inclusive), local = UTC + round(lon / 15) as the
    fitter reads it. None when fewer than `min_hours` of the window are
    there. Pure."""
    d = _asDate(day)
    signed = ((float(lon) + 180.0) % 360.0) - 180.0
    off = int(round(signed / 15.0))
    lo, hi = hours
    got = []
    for r in rows:
        t = datetime.fromisoformat(f"{r['date']}T{int(r['hour']):02d}:00") + timedelta(hours=off)
        if t.date() == d and lo <= t.hour <= hi:
            got.append(r)
    if len(got) < min_hours:
        return None

    def avg(k):
        v = [float(r[k]) for r in got if r.get(k) is not None]
        return sum(v) / len(v) if v else None
    snowfall = sum(float(r.get("snowfall") or 0.0) for r in got)
    depth = avg("snow_depth")
    return {"apparent_temp": avg("apparent_temperature"),
            "wind": avg("wind_speed_10m"),
            "precip": sum(float(r.get("precipitation") or 0.0) for r in got),
            "soil": avg("soil_moisture"),
            "snow": (depth or 0.0) + snowfall,
            "doy": d.timetuple().tm_yday,
            "hours": len(got)}


def raceDayWeather(lat, lon, day, sport="XC", cur=None, now=None):
    """The forecast race day as the weather correction reads it (daySummary),
    16 days out at most; None past the horizon, for a past date, a venue
    with no coordinates, or no answer from Open-Meteo. Cached like
    forecastAt: six hours in process and in weather_forecast."""
    sport = (sport or "XC").upper()
    if lat is None or lon is None or not day or sport not in _DAY_CODE:
        return None
    try:
        d = _asDate(day)
    except (TypeError, ValueError):
        return None
    today = (now or datetime.now(timezone.utc)).date()
    if d < today or (d - today).days > HORIZON_DAYS:
        return None
    hours = _raceWindow(sport)
    if hours is None:
        return None
    key = ("fc", round(float(lat), 2), round(float(lon), 2), d.isoformat(), _DAY_CODE[sport])
    hit = _memGet(key)
    if hit is not None:
        return hit or None
    wx = _tableGet(cur, key) if cur is not None else None
    if wx is None:
        rows = _fetchDayRows(float(lat), float(lon), d)
        wx = daySummary(rows, lon, d, hours) if rows else None
        if wx is not None:
            wx.update({"kind": "forecast_day", "source": SOURCE,
                       "fetched_at": datetime.now(timezone.utc).isoformat(timespec="minutes")})
            if cur is not None:
                _tablePut(cur, key, wx)
    # ! A MISS IS REMEMBERED TOO, for MISS_SECONDS: with Open-Meteo down
    #   every prediction of that race would otherwise wait out TIMEOUT
    _memPut(key, wx if wx is not None else {}, FRESH_SECONDS if wx is not None else MISS_SECONDS)
    return wx or None


def _raceWindow(sport):
    try:
        from normalize_distance import _weatherArtifactFor
    except Exception:                                    # noqa: BLE001
        return None
    art = _weatherArtifactFor(sport)
    if not art:
        return None
    return tuple(art.get("race_local_hours", (8, 12)))


def _fetchDayRows(lat, lon, d):
    """The day's 24 UTC hours at the venue as weather_grid rows, or None.
    The paid endpoint when OPENMETEO_KEY is set (weather_provisional's rule)."""
    import os
    wp = _provisional()
    key = os.environ.get("OPENMETEO_KEY", "").strip()
    base = ("https://customer-api.open-meteo.com/v1/forecast" if key else _URL)
    q = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
         "hourly": wp.HOURLY, "timezone": "GMT", "wind_speed_unit": "kmh",
         "start_date": d.isoformat(), "end_date": d.isoformat()}
    if key:
        q["apikey"] = key
    req = urllib.request.Request(f"{base}?{urllib.parse.urlencode(q)}",
                                 headers={"User-Agent": "racecast.co predictions"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:                                    # noqa: BLE001
        return None
    if isinstance(data, list):
        data = data[0] if data else {}
    return wp.rowsFrom((lat, lon), data) or None


def weatherEffect(wx, course, sport, distance):
    """The multiplier a forecast day puts on a typical-day time at this
    course: the ratings' own wmult (conversions._weather_mult), 1.0 when
    the correction refuses the day or has no artifact. Pure but for the
    artifact."""
    if not wx:
        return 1.0
    try:
        import conversions
        return float(conversions._weather_mult(
            {k: wx.get(k) for k in ("apparent_temp", "wind", "precip", "soil", "snow", "doy")},
            course, sport, distance))
    except Exception:                                    # noqa: BLE001
        return 1.0


def describeDay(wx):
    """'feels 74°F, wind 9 mph, 0.10 in rain' for a day summary, or None."""
    if not wx:
        return None
    parts = []
    t = wx.get("apparent_temp")
    if t is not None:
        parts.append(f"feels {t * 9 / 5 + 32:.0f}°F")
    w = wx.get("wind")
    if w is not None and w >= 10:
        parts.append(f"wind {w / 1.609344:.0f} mph")
    p = wx.get("precip")
    if p is not None and p >= 0.3:
        parts.append(f"{p / 25.4:.2f} in rain")
    s = wx.get("snow")
    if s is not None and s >= 0.005:
        parts.append("snow")
    return ", ".join(parts) or None
