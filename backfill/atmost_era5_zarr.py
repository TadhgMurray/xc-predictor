#!/usr/bin/env python
# =============================================================================
# atmos_era5_zarr.py  --  point-sample ERA5 weather for xc-predictor meets
# -----------------------------------------------------------------------------
# THE IDEA (read this before the code)
# -----------------------------------------------------------------------------
# You have ~614k meets, each a (lat, lon, date). You want each meet's race-day
# hourly weather. Instead of MIRRORING the whole ERA5 archive to disk (the sync
# that filled your C: drive) or paying Open-Meteo, you read ERA5 straight from a
# free public cloud copy and pull ONLY the points you need -- lazily, over the
# network, nothing lands on disk except the answers.
#
# The one trick that makes this fast: ERA5 in the cloud is a Zarr store, split
# into "chunks". You pay to fetch a whole chunk even for one value inside it.
# We use a store whose chunks are laid out BY LOCATION (a "temporal" layout),
# so one cell's whole time-series is one cheap read. That is why the main loop
# iterates over GRID CELLS, not dates: the loop order is chosen to match the
# chunk layout. (If you ever swap to a time-sliced store like ARCO ar/full,
# you must flip the loop to iterate over dates instead, or you re-read the whole
# globe per cell.)
#
# Dedup first: ERA5 is 0.25 degrees (~25 km), so two venues in the same grid
# cell on the same day get identical weather. We snap every meet to the grid and
# collapse to distinct (cellLat, cellLon, date) BEFORE fetching. That is the
# whole reason 614k meets becomes a tractable number of reads.
#
# -----------------------------------------------------------------------------
# WHAT YOU MUST CONFIRM BEFORE A REAL RUN  (marked CONFIRM: in the code)
# -----------------------------------------------------------------------------
#   1. The temporal store's group name + variable names (list(ds.data_vars)).
#   2. Your `weather` table's real columns -> fix the INSERT in writeWeatherRows.
#   3. Accumulated fields' units/rate in this store -> _convertBaseUnits:
#        total_precipitation (m vs mm) and surface_solar_radiation_downwards
#        (J/m^2-per-hour vs already-W/m^2). Analysis-ready stores sometimes
#        de-accumulate; if so, drop the /3600 and the *1000.
#   4. Timezone: we pull the UTC calendar day. A late US race can straddle the
#      UTC midnight; decide if you want local-day instead (see _neededHours).
#
# -----------------------------------------------------------------------------
# DEPENDENCIES
#   pip install xarray zarr icechunk numpy pandas
#   (psycopg2 is already a project dep.) DB access goes through the project's
#   database.py pool + getConn(), and connection settings come from config.py's
#   PG_CONFIG -- this script does NOT open its own connection or take a --dsn.
# =============================================================================

import sys
sys.path.insert(0, "scripts")   # database.py lives in scripts/; run from repo root

import argparse
import math
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import xarray as xr
from psycopg2.extras import execute_values          # fast batched INSERT
from database import getConn, initPool, closePool   # project DB layer (pool + ctx mgr)


# =============================================================================
# SECTION 1 -- CONFIG.  Every value here is also exposed as a CLI flag in main().
# =============================================================================

GRID_DEG = 0.25          # ERA5 resolution in degrees; the cell size we snap to.
WRITE_BATCH = 5000       # rows per DB insert; keeps memory + round-trips sane.
TIME_DIM = "valid_time"  # this store names the hourly axis 'valid_time', not 'time'.
# Grid-keyed target table. NOT your old per-meet `weather` table (different shape);
# this one is keyed (cell_lat, cell_lon, date, hour). Join it to meets later on
# the snapped cell + date.
WEATHER_TABLE = "weather_grid"

# Map OUR output column  ->  the raw variable name in the Zarr store.
# CONFIRM (1): print list(ds.data_vars) and fix the right-hand side. Some stores
# use long names ("2m_temperature"), others short ECMWF names ("t2m").
VAR_MAP = {
    "temperature_2m":   "t2m",    # 2m air temperature, Kelvin
    "dew_point_2m":     "d2m",    # 2m dew point, Kelvin
    "precipitation":    "tp",     # total precipitation, metres (accumulated/hour)
    "surface_pressure": "sp",     # surface pressure, Pascals
    "cloud_cover":      "tcc",    # total cloud cover, fraction 0..1
    "wind_u":           "u10",    # 10m u-wind, m/s (east+)
    "wind_v":           "v10",    # 10m v-wind, m/s (north+)
    "solar_radiation":  "ssrd",   # surface solar radiation DOWNWARDS, J/m^2 accum/hour
    "wind_gust":        "fg10",   # 10m wind gust, max over the hour, m/s
    # XC course-condition passthroughs (not air weather -- ground state):
    "soil_moisture":    "swvl1",  # top 0-7cm volumetric soil water, m3/m3 (0..1) = mud
    "snow_depth":       "sd",     # snow on ground, m of WATER EQUIVALENT (not phys. depth)
    "snowfall":         "sf",     # snowfall, m of water equivalent (accumulated/hour)
}
# ^ This store uses ECMWF GRIB short names (t2m, not 2m_temperature), confirmed
#   live from list(ds.data_vars). ssrd = downwards (what we want); ssr = net.

# The Earthmover free ERA5 Icechunk store (anonymous S3). CONFIRM (1): the group
# name for the point-optimised layout -- the spatial one is "single/spatial",
# so the temporal one is almost certainly "single/temporal". Verify, don't trust.
STORE_BUCKET = "earthmover-icechunk-era5"
STORE_PREFIX = "icechunkV2"
STORE_REGION = "us-east-1"
STORE_GROUP = "single/temporal"


# =============================================================================
# SECTION 2 -- GRID SNAPPING.  Meet coords -> ERA5 grid cells.
# =============================================================================

def _snapToEra5Grid(lat, lon):
    """
    Purpose: put an arbitrary venue coordinate onto ERA5's 0.25-degree grid, so
             many nearby meets collapse onto one shared cell (one fetch).
    Arguments:
        lat: float, degrees north, range -90..90 (a meet's gps_lat).
        lon: float, degrees east, range -180..180 as stored in your DB.
             EDGE CASE: ERA5 longitude runs 0..360, so a US venue at -71.0 must
             become 289.0 or every lookup silently lands on the wrong side of
             the planet. That wrap is the whole point of this helper.
    Output: (cellLat, cellLon) floats snapped to the grid, cellLon in 0..360.
    """
    cellLat = round(lat / GRID_DEG) * GRID_DEG      # nearest 0.25-deg latitude line
    cellLon = round(lon / GRID_DEG) * GRID_DEG      # nearest 0.25-deg longitude line
    cellLon = cellLon % 360                          # wrap -71.0 -> 289.0
    return round(cellLat, 2), round(cellLon, 2)      # 2 dp kills float dust


def loadMeetGrid(conn):
    """
    Purpose: read every GPS-bearing meet, snap to the grid, and return the
             DISTINCT (cellLat, cellLon, date) work list -- this is what we fetch.
    Arguments:
        conn: a pooled psycopg2 connection (from the project's getConn()).
    Output: pandas DataFrame with columns [cellLat, cellLon, date], deduped.
    CONFIRM: the tfrrs-TF branch below -- point it at wherever that stream's
             geocoded GPS actually landed (see the comment on it).
    """
    # Weather only needs (venue, day) -- it's sport/source-agnostic -- so we
    # UNION each stream's DISTINCT (gps, date). No cross-stream meet_id join
    # anywhere (the invariant): each SELECT stays inside one stream.
    #
    # PERF: the anet-XC branch is the only heavy one (date lives on `results`,
    # ~34M rows). We COLLAPSE both sides to one row per meet_id FIRST, so the
    # join is ~800k x ~800k instead of 34M multiplied by a per-meet fan-out.
    # The other three branches hit small meta tables and are near-instant.
    sql = """
        WITH anet_xc_days AS (           -- 34M result rows -> ~1 row per (meet, day)
            SELECT DISTINCT meet_id, date
            FROM   results
            WHERE  source = 'anet' AND date IS NOT NULL
        ),
        anet_xc_venues AS (              -- meets has many div rows per meet_id
            SELECT DISTINCT meet_id, gps_lat, gps_long
            FROM   meets
            WHERE  gps_lat IS NOT NULL AND gps_long IS NOT NULL
              AND  meet_id IS NOT NULL
        )
        -- anet XC: join the two pre-collapsed sets on meet_id (same stream).
        SELECT v.gps_lat, v.gps_long, d.date
        FROM   anet_xc_venues v
        JOIN   anet_xc_days   d ON d.meet_id = v.meet_id

        UNION   -- anet TF: GPS + date both in meets_tf_meta
        SELECT DISTINCT gps_lat, gps_long, meet_date AS date
        FROM   meets_tf_meta
        WHERE  gps_lat IS NOT NULL AND gps_long IS NOT NULL
          AND  meet_date IS NOT NULL

        UNION   -- tfrrs XC: GPS + date both in meets_tfrrs (sport='XC')
        SELECT DISTINCT gps_lat, gps_long, date
        FROM   meets_tfrrs
        WHERE  sport = 'XC' AND gps_lat IS NOT NULL
          AND  gps_long IS NOT NULL AND date IS NOT NULL

        UNION   -- tfrrs TF: CONFIRM this table. If the ~22k geocoded tfrrs-TF
                -- meets landed in meets_tfrrs under sport='TF', this is right;
                -- if they went to a different table, repoint this branch.
        SELECT DISTINCT gps_lat, gps_long, date
        FROM   meets_tfrrs
        WHERE  sport = 'TF' AND gps_lat IS NOT NULL
          AND  gps_long IS NOT NULL AND date IS NOT NULL
    """
    with conn.cursor() as cur:                        # pooled psycopg2 cursor
        cur.execute(sql)
        fetched = cur.fetchall()                       # list of (lat, long, date) tuples
    # Build the frame ourselves so pandas never touches the raw DBAPI connection.
    raw = pd.DataFrame(fetched, columns=["gps_lat", "gps_long", "date"])
    # Snap VECTORISED. A row-wise .apply over ~1M+ rows would be the real Python
    # bottleneck; numpy on whole columns is orders of magnitude faster. This
    # mirrors _snapToEra5Grid exactly (round to grid, wrap lon into 0..360).
    lat = raw["gps_lat"].to_numpy(dtype=float)
    lon = raw["gps_long"].to_numpy(dtype=float)
    raw["cellLat"] = np.round(np.round(lat / GRID_DEG) * GRID_DEG, 2)
    raw["cellLon"] = np.round((np.round(lon / GRID_DEG) * GRID_DEG) % 360, 2)
    # Collapse to unique work units; this is the 614k -> (far fewer) reduction.
    return raw[["cellLat", "cellLon", "date"]].drop_duplicates().reset_index(drop=True)


# =============================================================================
# SECTION 3 -- RESUME.  Skip work already stored (idempotent re-runs).
# =============================================================================

def loadDoneKeys(conn):
    """
    Purpose: fetch the (cellLat, cellLon, date) triples already in `weather` so a
             re-run does zero duplicate work -- the table IS the resume state.
    Arguments: conn: a pooled psycopg2 connection (from getConn()).
    Output: a set of (cellLat, cellLon, dateStr) tuples for O(1) membership tests.
    CONFIRM: match the column names your writeWeatherRows actually inserts.
    """
    sql = f"SELECT DISTINCT cell_lat, cell_lon, date FROM {WEATHER_TABLE}"
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            fetched = cur.fetchall()
    except Exception:
        # Missing table etc. leaves the txn aborted; roll back so the pooled
        # connection is reusable, then treat it as "nothing done yet".
        conn.rollback()
        return set()
    return {(r[0], r[1], str(r[2])) for r in fetched}


# =============================================================================
# SECTION 4 -- OPEN THE STORE.  Once, lazily, and validate our variable names.
# =============================================================================

def openEra5Store():
    """
    Purpose: open the temporally-chunked ERA5 Zarr once and hand back a lazy
             xarray Dataset. "Lazy" = metadata only; no weather bytes move until
             a .sel(...) actually needs them.
    Arguments: none (store location comes from SECTION 1 config).
    Output: xarray.Dataset covering the surface variables, hourly, 1940->present.
    """
    import icechunk  # imported here so the module still loads without it installed
    storage = icechunk.s3_storage(
        bucket=STORE_BUCKET, prefix=STORE_PREFIX,
        region=STORE_REGION, anonymous=True,   # anonymous=True -> no AWS account
    )
    repo = icechunk.Repository.open(storage)
    session = repo.readonly_session("main")
    ds = xr.open_zarr(session.store, group=STORE_GROUP, consolidated=False, chunks=None)
    _validateVarNames(ds)
    return ds


def _validateVarNames(ds):
    """
    Purpose: fail LOUD and early if the store renamed a variable, instead of
             silently writing a column full of NULLs you notice weeks later.
    Arguments: ds: the opened xarray Dataset.
    Output: None; raises KeyError listing what's missing.
    """
    missing = [raw for raw in VAR_MAP.values() if raw not in ds.data_vars]
    if missing:
        raise KeyError(
            f"Store is missing {missing}. Available: {sorted(ds.data_vars)[:40]} ..."
        )


# =============================================================================
# SECTION 5 -- FETCH.  Read whole 3-deg TILES (= one chunk) at a time, so the
# expensive chunk read/decompress is shared by every cell inside it.
# =============================================================================

def fetchTileWeather(ds, tileCells):
    """
    Purpose: ONE network load for a whole 3-degree tile (its cells sit in the same
             (year, tile) chunks), then slice each cell out of memory. This is the
             speed fix: the chunk is read+decompressed once, not once per cell.
    Arguments:
        ds: the lazy xarray Dataset from openEra5Store().
        tileCells: list of ((cellLat, cellLon), dates) -- all cells in one tile.
    Output: list[dict] -- rows for every cell in the tile.
    """
    lats = [cl for (cl, cn), _ in tileCells]
    lons = [cn for (cl, cn), _ in tileCells]
    allDates = sorted({d for _, dates in tileCells for d in dates})
    hours = _neededHours(allDates)                    # union of every cell's hours

    # Vectorised point-selection over the tile's cells -> dims (cell, valid_time).
    picked = ds[list(VAR_MAP.values())].sel(
        latitude=xr.DataArray(lats, dims="cell"),
        longitude=xr.DataArray(lons, dims="cell"),
        method="nearest")
    lo, hi = _coverageBounds(picked)
    inRange = hours[(hours >= lo) & (hours <= hi)]
    if inRange.size == 0:                             # whole tile out of coverage
        return []
    block = picked.sel({TIME_DIM: inRange}).load()   # THE read (chunks fetched once)

    # Now serve each cell from the in-memory block -- no more network.
    rows = []
    for ci, ((cl, cn), dates) in enumerate(tileCells):
        cellHours = _neededHours(dates)
        cellHours = cellHours[(cellHours >= lo) & (cellHours <= hi)]
        if cellHours.size == 0:
            continue
        cellDS = block.isel(cell=ci).sel({TIME_DIM: cellHours})  # in-memory slice
        rows.extend(_cellRowsFromDataset(cl, cn, cellDS))
    return rows


def fetchCellWeather(ds, cellLat, cellLon, dates):
    """Single-cell fetch (kept for --debug). One network read for one cell."""
    hours = _neededHours(dates)
    cell = _selectCellHours(ds, cellLat, cellLon, hours)
    return _cellRowsFromDataset(cellLat, cellLon, cell)


def _cellRowsFromDataset(cellLat, cellLon, cellDS):
    """
    Purpose: turn ONE cell's per-hour Dataset (dims: valid_time) into DB rows --
             the convert + derive + shape core, shared by tile and single-cell paths.
    Arguments: cellLat, cellLon: grid coords; cellDS: xarray Dataset over valid_time.
    Output: list[dict], one per hour (or [] if the cell had no in-coverage hours).
    """
    if cellDS[TIME_DIM].size == 0:
        return []
    actualHours = pd.DatetimeIndex(cellDS[TIME_DIM].values)
    base = _convertBaseUnits(cellDS)                  # dict of numpy arrays, real units
    base["relative_humidity_2m"] = _deriveRelativeHumidity(
        base["temperature_2m"], base["dew_point_2m"])
    base["wind_speed_10m"] = _deriveWindSpeed(base["wind_u"], base["wind_v"])
    base["wind_direction_10m"] = _deriveWindDirection(base["wind_u"], base["wind_v"])
    base["apparent_temperature"] = _deriveApparentTemperature(
        base["temperature_2m"], base["relative_humidity_2m"],
        base["wind_speed_10m"], base["solar_radiation"])
    return _rowsFromHourly(cellLat, cellLon, actualHours, base)


def _coverageBounds(picked):
    """(lo, hi) datetime64 for the archive's real time span -- clamps out-of-range
       (pre-1940 / too-recent) dates so .sel can't KeyError on a missing hour."""
    coverage = picked[TIME_DIM].values
    return coverage[0], coverage[-1]


def _neededHours(dates):
    """
    Purpose: expand a set of dates into the exact hourly timestamps to request.
    Arguments: dates: iterable of 'YYYY-MM-DD' strings.
    Output: pandas.DatetimeIndex of every hour 00:00..23:00 UTC across the dates.
    CONFIRM (4): this is the UTC calendar day. For local-day, offset each date by
                 the venue's UTC offset before expanding.
    """
    stamps = []
    for d in dates:
        start = pd.Timestamp(d)                       # midnight UTC that date
        stamps.extend(start + pd.Timedelta(hours=h) for h in range(24))
    return pd.DatetimeIndex(stamps)


def _selectCellHours(ds, cellLat, cellLon, hours):
    """
    Purpose: pull just this cell's values at just these hours (single-cell path).
    Arguments: ds, cellLat, cellLon as above; hours: DatetimeIndex to fetch.
    Output: an in-memory xarray Dataset (VAR_MAP raw vars) sized (valid_time,).
    """
    picked = ds[list(VAR_MAP.values())]               # keep only vars we use
    # method='nearest' snaps to the closest grid node (cellLat/Lon are already
    # on-grid, so this just resolves float-vs-stored-coord rounding).
    point = picked.sel(latitude=cellLat, longitude=cellLon, method="nearest")
    # Clamp to the archive's real coverage: ERA5 starts 1940 and lags real time
    # by weeks-to-months, so a future/too-recent (your data reaches 2027) or a
    # pre-1940 meet date must be dropped, else .sel raises KeyError on a missing
    # timestamp. ERA5 is gap-free inside its range, so a min/max clamp suffices.
    coverage = point[TIME_DIM].values                 # sorted datetime64 array
    lo, hi = coverage[0], coverage[-1]
    inRange = hours[(hours >= lo) & (hours <= hi)]     # keep only covered hours
    return point.sel({TIME_DIM: inRange}).load()      # .load() forces the fetch now


# =============================================================================
# SECTION 6 -- UNIT CONVERSION + DERIVED VARIABLES.  One tiny helper each.
# =============================================================================

def _convertBaseUnits(cell):
    """
    Purpose: turn raw ERA5 SI units into the human units your DB expects.
    Arguments: cell: the loaded per-cell Dataset (raw VAR_MAP variables).
    Output: dict[str, np.ndarray] keyed by OUR output names, correct units.
    """
    g = lambda raw: cell[raw].values                  # short local getter
    return {
        "temperature_2m":   g("t2m") - 273.15,        # K  -> degC
        "dew_point_2m":     g("d2m") - 273.15,        # K  -> degC
        "surface_pressure": g("sp") / 100.0,          # Pa -> hPa
        "cloud_cover":      g("tcc") * 100.0,         # frac -> %
        "precipitation":    g("tp") * 1000.0,         # m  -> mm   CONFIRM(3): sanity magnitudes
        "wind_u":           g("u10"),                 # keep m/s for derive
        "wind_v":           g("v10"),
        "solar_radiation":  g("ssrd") / 3600.0,       # J/m^2 -> W/m^2   CONFIRM(3): sanity magnitudes
        "wind_gust":        g("fg10") * 3.6,          # m/s -> km/h (final output)
        # Passthroughs (no conversion): ground state, not air weather.
        "soil_moisture":    g("swvl1"),               # m3/m3, 0..1 (mud proxy)
        "snow_depth":       g("sd"),                  # m water-equivalent (ERA5 convention)
        "snowfall":         g("sf"),                  # m water-equivalent, accum/hour
    }


def _deriveRelativeHumidity(tempC, dewC):
    """
    Purpose: RH from temperature + dew point via the Magnus formula.
    Arguments: tempC, dewC: np.ndarrays of air temp and dew point in degC.
    Output: np.ndarray of relative humidity in % (clipped to 0..100).
    """
    a, b = 17.625, 243.04                             # Magnus coefficients
    eDew = np.exp((a * dewC) / (b + dewC))            # vapour pressure at dew point
    eAir = np.exp((a * tempC) / (b + tempC))          # saturation vapour pressure
    return np.clip(100.0 * eDew / eAir, 0.0, 100.0)


def _deriveWindSpeed(u, v):
    """
    Purpose: scalar wind speed from the two vector components.
    Arguments: u, v: np.ndarrays, east+ and north+ wind in m/s.
    Output: np.ndarray wind speed in km/h (Pythagoras, then m/s -> km/h).
    """
    return np.hypot(u, v) * 3.6                        # sqrt(u^2+v^2) * 3.6


def _deriveWindDirection(u, v):
    """
    Purpose: meteorological wind direction -- the compass bearing the wind blows
             FROM (0=N, 90=E), which is what runners/forecasts mean.
    Arguments: u, v: np.ndarrays in m/s.
    Output: np.ndarray of degrees in 0..360.
    """
    # atan2 gives the direction wind blows TOWARD; +180 flips it to FROM.
    deg = np.degrees(np.arctan2(u, v))                # note (u, v) order = FROM-north
    return (deg + 180.0) % 360.0


# Share of downward shortwave a standing, clothed body absorbs: ~0.25 of
# its surface faces the sun, ~0.7 absorptivity. Steadman's Q wants exactly
# this quantity; the raw irradiance is four to six times too much.
SUN_ABSORBED_FRACTION = 0.175


def _deriveApparentTemperature(tempC, rh, windKmh, solarWm2):
    """
    Purpose: a "feels like" temperature folding in humidity, wind, and sun.
             This is the Steadman (Australian BOM) apparent temperature -- a
             citable, defensible formula. It is NOT a byte-match to Open-Meteo's
             apparent_temperature (they use their own coefficients); treat it as
             "a reasonable feels-like", exactly as we agreed.
    Arguments:
        tempC:    np.ndarray air temperature, degC.
        rh:       np.ndarray relative humidity, % (0..100).
        windKmh:  np.ndarray wind speed, km/h (converted to m/s inside).
        solarWm2: np.ndarray downward shortwave, W/m^2.
                  APPROXIMATION: Steadman's Q is *net radiation absorbed by the
                  body*; we proxy it with downward shortwave, which overstates
                  sun on a clothed runner. Use the commented shade line to drop
                  the sun term entirely if you'd rather not trust the proxy.
    Output: np.ndarray apparent temperature, degC.
    """
    ws = windKmh / 3.6                                # km/h -> m/s (formula wants m/s)
    # e = water-vapour pressure (hPa), from RH times the saturation curve.
    e = (rh / 100.0) * 6.105 * np.exp((17.27 * tempC) / (237.7 + tempC))
    # ★ Q IS THE RADIATION A BODY ABSORBS, NOT THE SUNLIGHT THAT FALLS
    #   (2026-09-06, SITE_NOTES #4). This line used the raw downward
    #   shortwave -- 800-1000 W/m2 in full sun -- where Steadman's Q is the
    #   net radiation absorbed per unit BODY surface, and every sunny hour
    #   read 20-35 C hotter than it felt. A standing body presents about a
    #   quarter of its surface to the sun and absorbs about 70% of what
    #   lands, so Q ~ 0.175 x downward shortwave: full sun is ~150 W/m2 and
    #   about +5 C in a light breeze, which is what the formula's authors
    #   describe. The stored raw inputs let scripts/recompute_apparent_temp
    #   rewrite every existing row to the same rule.
    at = (tempC + 0.348 * e - 0.70 * ws
          + 0.70 * (SUN_ABSORBED_FRACTION * solarWm2 / (ws + 10.0)) - 4.25)
    # Shade version (no sun proxy):  at = tempC + 0.33*e - 0.70*ws - 4.00
    return at


def _weatherCodeStub(n):
    """CONFIRM: WMO code is Open-Meteo's heuristic; left NULL for now."""
    return np.full(n, None, dtype=object)


# =============================================================================
# SECTION 7 -- SHAPE ROWS + WRITE.
# =============================================================================

def _rowsFromHourly(cellLat, cellLon, hours, base):
    """
    Purpose: zip the aligned hourly arrays into one dict per hour for insertion.
    Arguments:
        cellLat, cellLon: this cell's grid coords.
        hours: DatetimeIndex aligned index-for-index with every array in `base`.
        base: dict of output-named np.ndarrays (converted + derived).
    Output: list[dict], each carrying the key + all output variables for one hour.
    """
    n = len(hours)
    wc = _weatherCodeStub(n)                           # weather_code stays a stub
    rows = []
    for i in range(n):
        ts = hours[i]
        rows.append({
            "cell_lat": cellLat, "cell_lon": cellLon,
            "date": ts.strftime("%Y-%m-%d"), "hour": int(ts.hour),
            "temperature_2m":       float(base["temperature_2m"][i]),
            "dew_point_2m":         float(base["dew_point_2m"][i]),
            "relative_humidity_2m": float(base["relative_humidity_2m"][i]),
            "apparent_temperature": float(base["apparent_temperature"][i]),
            "precipitation":        float(base["precipitation"][i]),
            "weather_code":         wc[i],
            "surface_pressure":     float(base["surface_pressure"][i]),
            "cloud_cover":          float(base["cloud_cover"][i]),
            "solar_radiation":      float(base["solar_radiation"][i]),
            "wind_speed_10m":       float(base["wind_speed_10m"][i]),
            "wind_direction_10m":   float(base["wind_direction_10m"][i]),
            "wind_gust":            float(base["wind_gust"][i]),
            "soil_moisture":        float(base["soil_moisture"][i]),
            "snow_depth":           float(base["snow_depth"][i]),
            "snowfall":             float(base["snowfall"][i]),
        })
    return rows


def writeWeatherRows(conn, rows):
    """
    Purpose: insert a batch of hourly rows idempotently (safe to re-run).
    Arguments:
        conn: a pooled psycopg2 connection (the CALLER wraps it in getConn()
              and commits, matching the project's getConn usage pattern).
        rows: list[dict] from _rowsFromHourly.
    Output: None. Does NOT commit -- the caller commits so a whole batch is one
            transaction and getConn()'s rollback-on-error stays meaningful.
    CONFIRM (2): columns + the unique key in ON CONFLICT must match your table.
    """
    if not rows:
        return
    cols = list(rows[0].keys())
    # execute_values fills the single "%s" with all rows in one round trip; far
    # faster than executemany. VALUES %s is its required template placeholder.
    sql = (
        f"INSERT INTO {WEATHER_TABLE} ({', '.join(cols)}) VALUES %s "
        f"ON CONFLICT (cell_lat, cell_lon, date, hour) DO NOTHING"
    )
    data = [tuple(r[c] for c in cols) for r in rows]
    with conn.cursor() as cur:
        # page_size defaults to 100 -> a 14k-row batch would be ~140 round-trips.
        # Send the whole batch in one shot instead.
        execute_values(cur, sql, data, page_size=len(data))


# =============================================================================
# SECTION 8 -- ORCHESTRATION + CLI.
# =============================================================================

def _parseArgs():
    """Purpose: expose every knob as a flag. Output: parsed argparse namespace."""
    p = argparse.ArgumentParser(description="Point-sample ERA5 weather for meets.")
    # No --dsn: the connection comes from config.PG_CONFIG through database.py's pool.
    p.add_argument("--limit", type=int, default=None,
                   help="Smoke test: process only the first N cells.")
    p.add_argument("--dry-run", action="store_true",
                   help="List work, fetch nothing, write nothing.")
    p.add_argument("--debug", action="store_true",
                   help="Narrate each tile (cells, rows, load time) instead of "
                        "the periodic progress line.")
    return p.parse_args()


def main():
    """Purpose: run the backfill end to end. Output: None (writes to DB)."""
    args = _parseArgs()
    initPool()                                         # open the shared pool once
    try:
        # --- READ PHASE: one short-lived pooled connection for both reads. ---
        with getConn() as conn:
            work = loadMeetGrid(conn)                   # DataFrame [cellLat,cellLon,date]
            done = loadDoneKeys(conn)                   # set of finished triples

        # Drop already-done triples so a resume does no duplicate work.
        work = work[~work.apply(
            lambda r: (r.cellLat, r.cellLon, str(r.date)) in done, axis=1)]

        # One date-list per cell, then bucket cells into 3-deg TILES (= one store
        # chunk) so each chunk is read once and shared by all its cells.
        byCell = work.groupby(["cellLat", "cellLon"])["date"].apply(list)
        cells = list(byCell.items())
        if args.limit:
            cells = cells[:args.limit]
        tiles = _groupIntoTiles(cells)

        print(f"{len(cells)} cells in {len(tiles)} tiles "
              f"({sum(len(d) for _, d in cells)} cell-days).")
        if args.dry_run:
            return

        # --- FETCH + WRITE PHASE: one tile-read at a time, batched writes. ---
        ds = openEra5Store()
        with getConn() as conn:
            _runBackfill(ds, tiles, conn, args.debug)
    finally:
        closePool()                                     # always release the pool


def _groupIntoTiles(cells, tileDeg=3.0):
    """
    Purpose: bucket cells into 3-degree blocks that line up with the store's
             12x12 (=3deg) spatial chunks, so every cell in a block shares the
             same chunk reads (the whole point of the speed fix).
    Arguments: cells: list of ((cellLat, cellLon), dates); tileDeg: block size.
    Output: list of tiles, each a list of ((cellLat, cellLon), dates).
    """
    tiles = {}
    for (cl, cn), dates in cells:
        key = (math.floor(cl / tileDeg), math.floor(cn / tileDeg))  # chunk-aligned
        tiles.setdefault(key, []).append(((cl, cn), dates))
    return list(tiles.values())


def _runBackfill(ds, tiles, conn, debug=False):
    """
    Purpose: fetch tile by tile (each is one shared chunk read) and write rows in
             batches, with progress. Serial by design -- one big per-tile .load()
             lets zarr parallelise its own chunk fetches internally, which is the
             speedup, with no user threads to deadlock.
    Arguments:
        ds: the open ERA5 dataset.
        tiles: list of tiles (each a list of ((cellLat, cellLon), dates)).
        conn: pooled psycopg2 connection (single-thread writes).
        debug: narrate each tile with its cell count, row count, and load time.
    Output: None. Writes to DB; prints progress + a final summary.
    """
    total = len(tiles)
    t0 = time.time()
    every = max(1, total // 50)                        # ~50 progress lines total
    buffer, done, failed = [], 0, 0
    for tileCells in tiles:
        ts = time.time()
        try:
            rows = fetchTileWeather(ds, tileCells)
        except Exception as e:                         # transient -> retry on resume
            failed += 1
            print(f"  ! tile failed ({e}); retry on resume", flush=True)
            done += 1
            continue
        fetchSecs = time.time() - ts                   # time the FETCH
        buffer.extend(rows)
        done += 1
        writeSecs = 0.0
        if len(buffer) >= WRITE_BATCH:
            ws = time.time()
            writeWeatherRows(conn, buffer)             # time the WRITE too
            conn.commit()
            writeSecs = time.time() - ws
            buffer = []
        if debug:
            print(f"  [{done}/{total}] tile: {len(tileCells)} cells -> "
                  f"{len(rows)} rows | fetch {fetchSecs:.1f}s + write "
                  f"{writeSecs:.1f}s", flush=True)
        if not debug and (done % every == 0 or done == total):
            _printProgress(done, total, t0)
    writeWeatherRows(conn, buffer)                     # final partial batch
    conn.commit()
    # ! A STEP THAT FETCHED NOTHING IS A FAILED STEP (run16, 2026-09-07):
    #   every tile failed on a missing codec and the pipeline carried on,
    #   refitting and re-normalising with no 2026 weather. Some tiles
    #   failing is transient and resumable; all of them is a broken venv.
    if total and failed == total:
        print(f"  !! every tile failed ({failed}/{total}); the store's codec is "
              f"missing from this venv? (pip install pcodec) -- step failed",
              flush=True)
        raise SystemExit(2)
    print(f"done: {total} tiles in {time.time() - t0:.0f}s"
          f"{f' ({failed} failed -- rerun to retry)' if failed else ''}.", flush=True)


def _printProgress(done, total, t0):
    """One progress line: fraction of tiles done, elapsed, rate, rough ETA."""
    elapsed = time.time() - t0
    rate = done / elapsed if elapsed else 0            # tiles per second so far
    eta = (total - done) / rate if rate else 0         # seconds remaining
    print(f"  [{done}/{total}] {elapsed:5.0f}s | {rate:4.1f} tiles/s | "
          f"~{eta:5.0f}s left", flush=True)


if __name__ == "__main__":
    main()