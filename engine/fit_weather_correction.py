# Project: xc-predictor
# Author:  Tadhg Murray
# File:    fit_weather_correction.py
# Purpose: MEASURE-THEN-FIT the race-day weather correction (last link in the
#          normalize chain: raw -> distance -> geometry -> era -> WEATHER).
#
# THE DESIGN
# ----------
# Two-way fixed effects: pin the ATHLETE (same runner cancels ability) and the
# RACE-EVENT (the same meet run across years cancels course AND season-timing).
# Only weather is left. Needs no engine outputs -- absorbs ability and difficulty.
#
#   log(nt) = athlete_effect + event_effect + BETA . (weather - reference) + eps
#
# WHY RACE-EVENT, NOT VENUE (the confound fix)
# --------------------------------------------
# A venue hosts September invitationals AND November championships, so venue
# fixed-effects let the identifying variation run DOWN THE SEASON -- where weather
# cools and everyone gets fitter at once. That common within-season fitness trend
# leaks into the temperature coefficient (makes cold look fast for a non-weather
# reason, and linearises the curve). Anchoring on the SAME MEET across years holds
# the calendar week -- and thus fitness stage -- fixed, so only year-to-year
# weather moves. Validated on synthetic: venue-FE showed a spurious -2.5% at the
# cold end (pure fitness leak); event-FE correctly showed ~0 there.
# PRICE: only races that RECUR across years with varying weather inform the fit;
# one-off races demean to zero and drop out. That's the point, not a bug.
#
# WEATHER: apparent_temperature is aggregated as the daily MAX (peak heat is what
# cooks a runner; the mean diluted it with cool mornings). NOTE: apparent_temp is
# CELSIUS like temperature_2m. It read -12..100+ before 2026-09-06 because the
# sun term was wrong, not because the unit was; see REFERENCE.
#
# OUTPUT IS THE GATE. --course prints a venue by eye; --shape reads the curve in
# WITHIN-EVENT temperature deviation (the variation event-FE actually uses).

import sys
sys.path.insert(0, "scripts")            # database.py lives in scripts/; run from repo root

import argparse
import os
import pickle

import numpy as np

from database import getConn, initPool, closePool
sys.path.insert(0, "engine")            # for the shared event-distance parser
from event_parse import distanceFromEventShort   # ONE source of truth (dict+parser)
# The cell-realism gate. IMPORTED, never mirrored: the fitter and normalize_distance
# are two callers of ONE definition, so a mirror here would only create drift
# (14: MIRRORS vs IMPORTS). If these two disagree, the fit is done on races the
# apply side will silently no-op -- betas fit on evidence that never gets used.
from normalize_distance import MAX_RACE_SNOW_M


# ------------------------------------------------------------------ #
# CHUNK 0 — CONSTANTS
# ------------------------------------------------------------------ #

GRID_DEG = 0.25
DIST_REF = 5000.0            # reference race distance (m); interaction is 0 here
# Local race window per sport (longitude-derived; no start times). XC guns in the
# morning; TF sprawls prelims-to-evening-finals, so a wider daytime band.
RACE_LOCAL_HOURS_BY_SPORT = {"XC": (8, 12), "TF": (9, 20)}
MIN_RACES = 3
DEMEAN_ITERS = 25

# WX_AGG: feature -> daytime aggregate. apparent_temp is modelled downstream by a
# SPLINE. states use avg, accumulations sum. snow is depth+snowfall merged.
WX_AGG = {
    "apparent_temp": "avg(apparent_temperature)",     # race-window mean (deg C)
    "wind":          "avg(wind_speed_10m)",
    "precip":        "sum(precipitation)",
    "soil":          "avg(soil_moisture)",
    "snow":          "avg(snow_depth) + coalesce(sum(snowfall), 0)",  # merged, metres
}
# ★ TRACK READS PEAK HEAT (2026-09-06). The track window is 9am-8pm local,
#   and the mean over eleven hours put a 3pm race at 90F down as 76F. The
#   header above always said peak heat is what cooks a runner; the config
#   never did. XC's window is the morning and its mean stays. The artifact
#   carries the expression so backfill_normalize aggregates the same way.
WX_AGG_BY_SPORT = {
    "XC": dict(WX_AGG),
    "TF": dict(WX_AGG, apparent_temp="max(apparent_temperature)"),
}
KNOT_Q = (0.05, 0.275, 0.5, 0.725, 0.95)   # quantiles where spline knots sit

# Shrinkage for the per-(venue, fortnight) weather normals. A group with one
# race would have a "normal" equal to that race, so its correction would be
# exactly zero forever; shrinking toward the global mean by count is the same
# empirical-Bayes idea as LINK_SHRINK_K in the engine. A 40-race group sits
# ~83% on its own mean, a 2-race group ~20%.
VENUE_NORM_SHRINK_K = 8
QUERIED_FEATURES = tuple(WX_AGG.keys())              # what SQL returns / loadRaces reads

# Per-sport model features. TF DROPS snow (outdoor TF is spring/fall -> snow is
# noise). Both keep soil (puddles/wet runway are real even on a track). temp+soil
# are SPLINES (heat accelerates, mud is a threshold); the rest are linear.
SPLINE_FEATURES = ("apparent_temp", "soil")
LINEAR_FEATURES_BY_SPORT = {"XC": ("wind", "precip", "snow"),
                            "TF": ("wind", "precip")}
FEATURES = QUERIED_FEATURES                           # (kept for counts/reference)

# Reference = no-op point (multiplier 1.0). apparent_temp deg C (mild ~13);
# snow/soil neutral. The spline is centered on the temp reference at apply time.
# ★ CELSIUS (2026-09-06). The grid's apparent_temperature has always been
#   the Steadman formula's output, degC; the old sun term made sunny hours
#   read 45-100, which LOOKED like Fahrenheit and was labelled so here. With
#   the sun term fixed (SITE_NOTES #4, scripts/recompute_apparent_temp) a
#   mild day is 13 C, not 55. The reference is the no-op point for a venue
#   without its own normal; at 55 it would sit off the end of the curve.
REFERENCE = {"apparent_temp": 13.0, "wind": 3.0, "precip": 0.0,
             "soil": 0.20, "snow": 0.0}

# Active per-sport config; main() overwrites these from the *_BY_SPORT tables
# once it knows --sport. Defaults are XC so the module is valid on import.
RACE_LOCAL_HOURS = RACE_LOCAL_HOURS_BY_SPORT["XC"]
LINEAR_FEATURES = LINEAR_FEATURES_BY_SPORT["XC"]

ARTIFACT_TMPL = "engine/data/weather_correction_{sport}.pkl"   # per-sport
CACHE_DIR = "engine/data"


# ------------------------------------------------------------------ #
# CHUNK 1 — SQL (per sport).
# ------------------------------------------------------------------ #

def _weatherCte(need=None):
    """The race-window weather per (cell, day).

    ★ ONLY THE CELL-DAYS A RACE USES (2026-09-28, owner: "this is taking too
      long"). Grouped over the whole of weather_grid this aggregated every
      cell of the continent for every day of the year before the join threw
      nearly all of it away. With `need` (a CTE of cell_lat, cell_lon, date)
      the grid is read through its (cell_lat, cell_lon, date) index for the
      days a race was run there, and nothing else."""
    lo, hi = RACE_LOCAL_HOURS
    aggs = ",\n                   ".join(f"{expr} AS {name}"
                                          for name, expr in WX_AGG.items())
    # local hour = UTC hour + offset, offset = round(signed_lon / 15). Keep the
    # stored UTC hours that fall in the venue's LOCAL morning -- so a 9am race is
    # scored on 9am weather everywhere, not a fixed UTC block (dawn out west).
    signed = "(CASE WHEN g.cell_lon > 180 THEN g.cell_lon - 360 ELSE g.cell_lon END)"
    offset = f"round({signed} / 15.0)::int"
    local_hour = f"mod(mod(g.hour + {offset}, 24) + 24, 24)"
    join = (f"JOIN {need} n ON n.cell_lat = g.cell_lat AND n.cell_lon = g.cell_lon "
            f"AND n.date = g.date" if need else "")
    return f"""
        wx AS MATERIALIZED (
            SELECT g.cell_lat, g.cell_lon, g.date,
                   {aggs}
            FROM   weather_grid g
            {join}
            WHERE  {local_hour} BETWEEN {lo} AND {hi}
            GROUP  BY g.cell_lat, g.cell_lon, g.date
        )
    """


# _snapSql: float8 snap matching the backfill (real columns -> hash join, not
#           nested loop). Wrap longitude 0..360 via CASE (all gps lons -180..180).
def _snapSql(lat_col, lon_col):
    g = f"{GRID_DEG}::float8"
    lat = f"round(({lat_col})::float8 / {g}) * {g}"
    lon_wrapped = (f"(CASE WHEN ({lon_col}) < 0 "
                   f"THEN ({lon_col})::float8 + 360 ELSE ({lon_col})::float8 END)")
    lon = f"round({lon_wrapped} / {g}) * {g}"
    return lat, lon


def _wxSelect():
    return ", ".join(f"wx.{f}" for f in QUERIED_FEATURES)


# _eventKey: the RECURRING-RACE identity across years = (venue, ~2-week slot of
#            the year). Season-fixed (keeps the fitness confound out) but pools
#            MORE data per group than the meet name, and needs no name matching.
#            floor(doy/14) buckets the calendar into ~26 fortnights, stable across
#            years, so "this venue, this time of year" collapses across seasons.
def _eventKey(venue_col, date_col):
    fortnight = f"floor(extract(doy from ({date_col}))::int / 14)"
    return f"(coalesce({venue_col}, '') || '|f' || {fortnight})"


def xcQuery():
    clat, clon = _snapSql("mv.gps_lat", "mv.gps_long")
    event = _eventKey("mv.course_name", "r.date::date")
    return f"""
        WITH meet_venues AS (
            -- collapse meets (keyed on div_id, many rows per meet) to one row per
            -- meet_id, or the results join fans out ~5x per division.
            SELECT DISTINCT ON (meet_id)
                   meet_id, course_name, gps_lat, gps_long,
                   COALESCE(distance, 5000.0)::float8 AS dist
            FROM   meets
            WHERE  meet_id IS NOT NULL AND course_name IS NOT NULL
              AND  gps_lat IS NOT NULL AND gps_long IS NOT NULL
            ORDER  BY meet_id
        ),
        need AS MATERIALIZED (
            SELECT DISTINCT {clat} AS cell_lat, {clon} AS cell_lon, rd.d AS date
            FROM   (SELECT DISTINCT r.meet_id, r.date::date AS d
                    FROM   results r
                    WHERE  r.source = 'anet' AND r.normalized_time IS NOT NULL
                      AND  r.date ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$') rd
            JOIN   meet_venues mv ON mv.meet_id = rd.meet_id
        ),
        {_weatherCte("need")}
        SELECT COALESCE(r.person_id, r.athlete_id) AS ath,
               mv.course_name                      AS course,
               {event}                             AS event,
               r.date::date                        AS date,
               r.normalized_time                   AS nt,
               mv.dist                             AS dist,
               {_wxSelect()}
        FROM   results r
        JOIN   meet_venues mv ON mv.meet_id = r.meet_id
        JOIN   wx ON wx.cell_lat = {clat} AND wx.cell_lon = {clon}
                 AND wx.date = r.date::date
        WHERE  r.source = 'anet'
          AND  r.normalized_time IS NOT NULL
          AND  COALESCE(r.person_id, r.athlete_id) IS NOT NULL
          AND  r.date ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$'
    """


# ★ BOTH TRACK FEEDS (2026-09-06). anet's meets carry their coordinates
#   in meets_tf_meta; tfrrs's carry them in meets_tfrrs (sport 'TF',
#   55,846 of 60,462 geocoded) with the indoor flag and the venue id on the
#   geometry stamp. The fit and the backfill read the same union, so a
#   college row is corrected the way it was fitted. A tfrrs meet with no
#   stamp has no venue key and stays out of the fit (a NULL event would
#   pool every such row into one fixed effect); the backfill still
#   corrects it against the global reference.
_TF_META = """
        tfmeta AS (
            SELECT meet_id, 'anet' AS source, gps_lat, gps_long, location_id,
                   COALESCE(is_indoor, 0) AS is_indoor,
                   substr(meet_date::text, 1, 10)::date AS d
            FROM   meets_tf_meta
            WHERE  gps_lat IS NOT NULL AND gps_long IS NOT NULL
              AND  meet_date::text ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
            UNION ALL
            SELECT mt.meet_id, 'tfrrs', mt.gps_lat, mt.gps_long, g.location_id,
                   COALESCE(g.is_indoor, 0),
                   substr(mt.date::text, 1, 10)::date
            FROM   meets_tfrrs mt
            LEFT JOIN tfrrs_meet_geometry g
                   ON g.meet_id = mt.meet_id AND g.sport = 'TF'
            WHERE  mt.sport = 'TF'
              AND  mt.gps_lat IS NOT NULL AND mt.gps_long IS NOT NULL
              AND  mt.date::text ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}')"""


def tfQuery():
    clat, clon = _snapSql("mm.gps_lat", "mm.gps_long")
    venue = ("'TF:loc:' || mm.location_id || "
             "CASE WHEN mm.is_indoor = 1 THEN ':in' ELSE ':out' END")
    event = _eventKey(venue, "mm.d")
    return f"""
        WITH {_TF_META.strip()},
        need AS MATERIALIZED (
            SELECT DISTINCT {clat} AS cell_lat, {clon} AS cell_lon, mm.d AS date
            FROM   tfmeta mm
            WHERE  mm.is_indoor = 0 AND mm.location_id IS NOT NULL
        ),
        {_weatherCte("need")}
        SELECT COALESCE(r.person_id, r.athlete_id) AS ath,
               {venue}                             AS course,
               {event}                             AS event,
               mm.d                                AS date,
               r.normalized_time                   AS nt,
               r.event_short                       AS dist,
               {_wxSelect()}
        FROM   results_tf r
        JOIN   tfmeta mm ON mm.meet_id = r.meet_id AND mm.source = r.source
        JOIN   wx ON wx.cell_lat = {clat} AND wx.cell_lon = {clon}
                 AND wx.date = mm.d
        WHERE  mm.is_indoor = 0
          AND  mm.location_id IS NOT NULL
          AND  r.normalized_time IS NOT NULL
          AND  COALESCE(r.person_id, r.athlete_id) IS NOT NULL
    """


# ------------------------------------------------------------------ #
# CHUNK 2 — LOAD (streamed, generic over FEATURES).
# ------------------------------------------------------------------ #

def _distMeters(sport, raw):
    """Resolve one race's distance (m) for the interaction. TF: parse the free-text
       event_short with the SHARED parser (miles/gender/steeple all handled). XC:
       the numeric meets.distance. Unparseable/missing -> DIST_REF (dc=0, no-op)."""
    if sport == "TF":
        m, _gender = distanceFromEventShort(raw)      # (metres|None, gender)
        return float(m) if m is not None else DIST_REF
    try:
        return float(raw) if raw is not None else DIST_REF
    except (TypeError, ValueError):
        return DIST_REF


def _cellRealismMask(snow):
    # Purpose   : which races have a weather cell that describes THEIR COURSE?
    # Arguments : snow -- the merged depth+snowfall column, metres.
    # Output    : a boolean numpy mask; True = keep.
    # Note      : two separate refusals, and they must not be conflated --
    #   np.isfinite() drops NaN (7 rows in weather_grid carry NaN across
    #     apparent_temperature/precipitation/snowfall from one bad sync batch).
    #     NaN poisons lstsq silently: it is not large, it is ABSENT, and it
    #     propagates through every comparison by losing all of them.
    #   snow <= MAX drops the GLACIER CELLS -- a resolution problem, not a data
    #     problem. See normalize_distance.MAX_RACE_SNOW_M for the Juneau trace.
    #   `snow > MAX` is False for NaN, so the finite test must come FIRST or the
    #   NaN rows ride through the plausibility test unchallenged.
    return np.isfinite(snow) & (snow <= MAX_RACE_SNOW_M)


def _gateImplausibleCells(cols):
    # Purpose   : drop races whose weather cell is describing terrain.
    # Arguments : cols -- the loadRaces dict of parallel arrays.
    # Output    : the same dict, filtered.
    # ★ EXCLUSION BY RULE, NEVER BY ACCIDENT (3.2): an explicit guard AND a
    #   ledger line, every run. An accidental guard dies when someone extends a
    #   regex; a silent one was never a guard.
    # ★ APPLIED AT STREAM *AND* CACHE, exactly as 3.1's MAX_DISTANCE ceiling is:
    #   "Applied at stream AND cache (nonzero cache drops on a post-ceiling cache
    #   = the loader gate leaked)." A NONZERO DROP ON THE CACHE PATH MEANS THE
    #   CACHE PREDATES THIS GATE -- re-run with --refresh.
    snow = cols.get("snow")
    if snow is None:
        return cols

    keep = _cellRealismMask(snow)
    finite = np.isfinite(snow)
    n_nan = int((~finite).sum())
    n_cell = int((finite & (snow > MAX_RACE_SNOW_M)).sum())

    if n_nan or n_cell:
        print(f"  [cell-realism] dropped {n_cell:,} races whose cell reports "
              f">{MAX_RACE_SNOW_M} m of snow (25 km grid: the cell holds a "
              f"mountain, the course does not) + {n_nan:,} non-finite")
    if keep.all():
        return cols
    return {k: v[keep] for k, v in cols.items()}


def loadRaces(conn, sql, sport, limit=None):

    if limit:
        sql = sql + f"\n        LIMIT {int(limit)}"
    import time as _time
    names = ["ath", "course", "event", "date", "nt", "dist", *QUERIED_FEATURES]
    acc = {k: [] for k in names}
    t0 = _time.time()
    # ★★ PLAN FOR THE WHOLE RESULT, NOT THE FIRST ROW (2026-09-28: 34M rows
    #    at ~5,000 a second, 6,518 s, the first row after 0 s). A named
    #    cursor plans for cursor_tuple_fraction (default 0.1) of its rows,
    #    so Postgres picked a fast-start nested loop that re-aggregated a
    #    day of hourly weather for every result row. At 1.0, and with the
    #    weather CTE materialised, it is one aggregate and hash joins.
    with conn.cursor() as c:
        for stmt in ("SET cursor_tuple_fraction = 1.0", "SET work_mem = '1GB'",
                     "SET max_parallel_workers_per_gather = 4"):
            c.execute(stmt)
    # ★ COLUMNS A BATCH AT A TIME (2026-09-28): zip(*batch) transposes in C;
    #   the old per-row, per-column append was 25M x 11 Python calls.
    with conn.cursor(name="weather_stream") as cur:
        cur.itersize = 200_000
        cur.execute(sql)
        print(f"[load] query started returning rows after {_time.time() - t0:.0f}s",
              flush=True)
        n = 0
        while True:
            batch = cur.fetchmany(200_000)
            if not batch:
                break
            for k, col in zip(names, zip(*batch)):
                acc[k].extend(col)
            n += len(batch)
            if n % 2_000_000 < 200_000:
                print(f"[load] {n:,} rows ({_time.time() - t0:.0f}s)", flush=True)
    memo = {}

    def dist(raw):
        m = memo.get(raw)
        if m is None:
            m = memo[raw] = _distMeters(sport, raw)
        return m
    out = {"ath":    np.asarray(acc["ath"], dtype=object),
           "course": np.asarray(acc["course"], dtype=object),
           "event":  np.asarray(acc["event"], dtype=object),
           "date":   np.asarray([str(x) for x in acc["date"]], dtype=object),
           "nt":     np.asarray(acc["nt"], dtype=np.float64),
           "dist":   np.asarray([dist(r) for r in acc["dist"]], dtype=np.float64)}
    for f in QUERIED_FEATURES:
        out[f] = np.asarray([np.nan if v is None else v for v in acc[f]], dtype=np.float64)
    print(f"[load] {out['nt'].size:,} rows loaded in {_time.time() - t0:.0f}s", flush=True)
    return _gateImplausibleCells(out)          # STREAM path


# ------------------------------------------------------------------ #
# CHUNK 2b — DISK CACHE + index creation.
# ------------------------------------------------------------------ #

def _cachePath(sport, limit, sql=""):
    lo, hi = RACE_LOCAL_HOURS
    # hash the column set + query so ANY change to what's loaded picks a new file
    # (kills the "stale cache after a query change" KeyError class of bug).
    import hashlib
    sig = hashlib.md5(("|".join(["ath","course","event","date","nt","dist", *FEATURES])
                       + sql).encode()).hexdigest()[:8]
    return os.path.join(CACHE_DIR,
                        f"weather_cache_{sport}_{limit or 'all'}_{lo}-{hi}_{sig}.npz")


def loadCache(path):
    d = np.load(path, allow_pickle=True)
    return {k: d[k] for k in d.files}


def saveCache(path, cols):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez_compressed(path, **cols)


def _ensureIndex(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_weather_grid_cell_date "
                    "ON weather_grid (cell_lat, cell_lon, date)")
    conn.commit()


def getColumns(sport, sql, limit, refresh):
    path = _cachePath(sport, limit, sql)
    if os.path.exists(path) and not refresh:
        print(f"[cache] hit {path}  (use --refresh to re-query)")
        # CACHE path. A nonzero [cell-realism] drop HERE means this cache file
        # predates the gate -- the numbers are still correct, but re-run with
        # --refresh so the cache stops carrying glaciers around. (3.1)
        return _gateImplausibleCells(loadCache(path))
    initPool()
    try:
        with getConn() as conn:
            _ensureIndex(conn)
            print(f"[load] cache miss -- streaming {sport} races from DB...")
            cols = loadRaces(conn, sql, sport, limit)
    finally:
        closePool()
    saveCache(path, cols)
    print(f"[cache] saved {path}")
    return cols


# ------------------------------------------------------------------ #
# CHUNK 3 — TWO-WAY FIXED-EFFECTS ESTIMATOR (athlete + EVENT).
# ------------------------------------------------------------------ #

def _denseCode(labels):
    _, codes = np.unique(labels, return_inverse=True)
    n = int(codes.max()) + 1 if codes.size else 0
    return codes, n


def _subtractGroupMean(v, codes, n):
    s = np.bincount(codes, weights=v, minlength=n)
    c = np.bincount(codes, minlength=n)
    return v - (s / c)[codes]


DEMEAN_TOL = 1e-9     # stop when a pass moves no value by more than this


def _demeanColumn(v, ac, na, ec, ne):
    """Alternate the two group demeanings until a pass moves nothing by more
    than DEMEAN_TOL (at most DEMEAN_ITERS passes). The fixed 25 passes ran
    on every column whether it had converged at pass 3 or not."""
    v = v.astype(float).copy()
    for _ in range(DEMEAN_ITERS):
        prev = v
        v = _subtractGroupMean(_subtractGroupMean(v, ac, na), ec, ne)
        if np.max(np.abs(v - prev)) < DEMEAN_TOL:
            break
    return v


def _twoWayWithin(y, X, ac, na, ec, ne):
    y = _demeanColumn(y, ac, na, ec, ne)
    X = np.column_stack([_demeanColumn(X[:, j], ac, na, ec, ne)
                         for j in range(X.shape[1])]) if X.shape[1] else X.astype(float)
    return y, X


def _twoWayDemeanY(y, ac, na, ec, ne):
    return _demeanColumn(y, ac, na, ec, ne)


# _nsBasis: restricted (natural) cubic spline basis. K knots -> K-1 columns, and
#           LINEAR beyond the outer knots, so it can't wildly extrapolate in the
#           sparse hot/cold tails. (Harrell's restricted-cubic-spline form.)
def _nsBasis(x, knots):
    k = np.unique(np.asarray(knots, float))          # drop duplicate knots
    x = x.astype(float)
    if len(k) < 3:                                   # too little variation for a curve
        return x.reshape(-1, 1)                      # -> plain linear column, no crash
    K = len(k)

    def cube(u):
        u = np.maximum(u, 0.0)
        return u * u * u

    denom = (k[-1] - k[0]) ** 2
    cols = [x]
    for j in range(K - 2):
        tj = k[j]
        term = (cube(x - tj)
                - cube(x - k[-2]) * (k[-1] - tj) / (k[-1] - k[-2])
                + cube(x - k[-1]) * (k[-2] - tj) / (k[-1] - k[-2]))
        cols.append(term / denom)
    return np.column_stack(cols)


def _featureColumn(cols, f):
    return cols[f]


# _designMatrix: [ spline block per SPLINE_FEATURES | centered LINEAR features ].
#                layout maps "<feat>_spline" -> col indices, and each linear feat -> col.
def _designMatrix(cols, knots_by):
    dc = cols["dist"] / DIST_REF - 1.0               # 0 at the reference distance
    blocks, layout, idx = [], {}, 0
    for f in SPLINE_FEATURES:
        B = _nsBasis(cols[f], knots_by[f])
        layout[f + "_spline"] = list(range(idx, idx + B.shape[1]))
        idx += B.shape[1]
        blocks.append(B)
    for f in LINEAR_FEATURES:
        blocks.append((_featureColumn(cols, f) - REFERENCE[f]).reshape(-1, 1))
        layout[f] = idx
        idx += 1
    # DISTANCE INTERACTION.
    #  - SPLINE features: interact EVERY basis column with dc, so the whole CURVE
    #    reshapes with distance (the hot-end bend can steepen at 10k independently
    #    of the low end -- not just a uniform tilt).
    #  - LINEAR features: one (feature-ref)*dc column (no curve to reshape).
    for f in SPLINE_FEATURES:
        Bd = _nsBasis(cols[f], knots_by[f]) * dc[:, None]
        layout[f + "_spline_dist"] = list(range(idx, idx + Bd.shape[1]))
        idx += Bd.shape[1]
        blocks.append(Bd)
    for f in LINEAR_FEATURES:
        blocks.append(((_featureColumn(cols, f) - REFERENCE[f]) * dc).reshape(-1, 1))
        layout[f + "_dist"] = idx
        idx += 1
    return np.column_stack(blocks), layout


def _featureCounts(cols):
    return {f: int((_featureColumn(cols, f) != REFERENCE[f]).sum())
            for f in QUERIED_FEATURES}


def _collinearity(cols):
    """Within-event demeaned correlation among the weather features -- the tangle
       the fit actually sees. Strong pairs (|r|>~0.3) mean a solo beta is suspect
       (e.g. rain & temp: rainy days are cool, so temp can eat rain's signal)."""
    ac, na = _denseCode(athleteSeason(cols)); ec, ne = _denseCode(cols["event"])
    dem = {f: _twoWayDemeanY(_featureColumn(cols, f).astype(float), ac, na, ec, ne)
           for f in QUERIED_FEATURES}
    # drop features with ~no within-event variation (e.g. snow in a snowless set)
    # -- they have no correlation to report and would divide-by-zero to nan.
    feats = [f for f in dem if np.std(dem[f]) > 1e-9]
    flat = [f for f in QUERIED_FEATURES if f not in feats]
    print("\n[collin] within-event correlations (|r|>0.3 => TANGLED, solo beta suspect):")
    if flat:
        print(f"    (skipped, no within-event variation: {', '.join(flat)})")
    for i, a in enumerate(feats):
        for b in feats[i + 1:]:
            r = float(np.corrcoef(dem[a], dem[b])[0, 1])
            flag = "  <-- TANGLED" if abs(r) > 0.30 else ""
            print(f"    corr({a:14},{b:14}) = {r:+.3f}{flag}")


def _eventCoverage(cols):
    """How much data survives into RECURRING events (>=2 distinct runnings). Races
       in singleton events contribute nothing to the weather fit."""
    ec, ne = _denseCode(cols["event"])
    dc, _ = _denseCode(cols["date"])
    pairs = np.unique(np.stack([ec, dc], axis=1), axis=0)
    runnings = np.bincount(pairs[:, 0], minlength=ne)
    usable = (runnings[ec] >= 2)
    return ne, float(usable.mean())


# ★★ THE FIT READS TIMES THAT ALREADY CARRY THE LAST CORRECTION (2026-09-28,
#    owner: "weather corrections aren't doing enough"). normalized_time is
#    written by 05_backfill with the weather artifact of THAT run divided in,
#    and 04f runs before 05 -- so every refit measured only what the previous
#    artifact had left over, and 05 then applied that leftover INSTEAD of the
#    effect. Refits alternate between the effect and about nothing: the snow
#    beta was +0.748/m, then -0.399/m; this run's rain and wind betas came
#    out NEGATIVE (rain making runners faster), and diag_weather_credit's
#    in-course column had rain races short-changed by 0.2-2.6%. The fix is
#    to divide the applied correction back OUT before fitting: the artifact
#    the backfill applied is recorded beside it (weather_applied_<sport>.pkl,
#    backfill_normalize.recordAppliedWeather), and without that record the
#    current artifact is the one the last backfill used.
APPLIED_PATH = os.path.join(CACHE_DIR, "weather_applied_{sport}.pkl")


def appliedArtifact(sport):
    """(artifact or None, where it came from): the weather correction the
    rows' normalized_time was written with."""
    rec = APPLIED_PATH.format(sport=sport)
    if os.path.exists(rec):
        with open(rec, "rb") as f:
            art = pickle.load(f)
        return (art.get("artifact") if isinstance(art, dict) and "artifact" in art
                else art), f"the backfill's record {rec}"
    cur = os.path.join(CACHE_DIR, f"weather_correction_{sport}.pkl")
    if os.path.exists(cur):
        with open(cur, "rb") as f:
            return pickle.load(f), f"{cur} (no backfill record yet: assumed applied)"
    return None, "no artifact: the rows carry no weather"


def undoAppliedWeather(cols, sport, art=None, source=""):
    """cols with nt multiplied back by the weather multiplier the backfill
    divided in, so the fit sees the whole weather effect. One multiplier per
    (course, date, distance): a race shares its weather."""
    import datetime as _dt
    import normalize_distance as nd
    if art is None:
        print(f"[undo] {sport}: {source}; nothing to undo")
        return cols
    saved = nd._WEATHER.get(sport)
    nd._WEATHER[sport] = art
    try:
        n = cols["nt"].size
        feats = [f for f in QUERIED_FEATURES if f in cols]
        # one multiplier per (course, date, distance): the codes are found
        # vectorised, and _applyWeather runs once per race, not per row
        cc, _ = _denseCode(cols["course"].astype(str))
        dcode, _ = _denseCode(cols["date"])
        dd, _ = _denseCode(np.round(cols["dist"], 1))
        key = (cc.astype(np.int64) * (int(dcode.max()) + 1) + dcode) * (int(dd.max()) + 1) + dd
        _, first, inverse = np.unique(key, return_index=True, return_inverse=True)
        per_race = np.ones(first.size)
        for k, i in enumerate(first):
            wx = {f: (None if not np.isfinite(cols[f][i]) else float(cols[f][i]))
                  for f in feats}
            try:
                wx["doy"] = _dt.date.fromisoformat(str(cols["date"][i])[:10]).timetuple().tm_yday
            except ValueError:
                wx["doy"] = None
            course = cols["course"][i] if sport == "TF" else (cols["course"][i] or None)
            inv = nd._applyWeather(1.0, wx, course, sport, float(cols["dist"][i]))
            per_race[k] = (1.0 / inv) if inv else 1.0
        mult = per_race[inverse]
        memo = first
    finally:
        nd._WEATHER[sport] = saved
    out = dict(cols)
    out["nt"] = cols["nt"] * mult
    lm = np.log(mult)
    print(f"[undo] {sport}: the applied correction divided back out ({source}): "
          f"{len(memo):,} races, median |effect| {100 * float(np.median(np.abs(lm))):.2f}%, "
          f"p99 {100 * float(np.percentile(np.abs(lm), 99)):.2f}%")
    return out


def athleteSeason(cols):
    """★ THE ATHLETE EFFECT IS PER SEASON (2026-09-28). Keyed on the person
    alone, the effect spans a career, and the event effect (one venue and
    fortnight across YEARS) then leaves each runner's year-to-year
    improvement in the residual -- a high schooler is 2-4% faster every
    year, so any drift of the weather across the years of the data leaks
    into the betas. Per (person, year) the athlete is compared only with
    themselves that season; the event effect still holds the course and
    the calendar week."""
    return np.asarray([f"{a}|{str(d)[:4]}" for a, d in zip(cols["ath"], cols["date"])],
                      dtype=object)


def fitWeather(cols):
    ac, na = _denseCode(athleteSeason(cols))
    ec, ne = _denseCode(cols["event"])                # RACE-EVENT, not venue
    knots_by = {f: np.quantile(cols[f], KNOT_Q) for f in SPLINE_FEATURES}  # data-placed
    y = np.log(cols["nt"])
    X, layout = _designMatrix(cols, knots_by)
    yd, Xd = _twoWayWithin(y, X, ac, na, ec, ne)
    coef, *_ = np.linalg.lstsq(Xd, yd, rcond=None)
    betas = {f: float(coef[layout[f]]) for f in LINEAR_FEATURES}
    splines = {f: {"knots": knots_by[f].tolist(),
                   "coef": [float(coef[j]) for j in layout[f + "_spline"]],
                   "coef_dist": [float(coef[j]) for j in layout[f + "_spline_dist"]],
                   "ref": REFERENCE[f]} for f in SPLINE_FEATURES}
    # ★ THE OPTIMUM, SO COLD IS NEVER DOCKED (2026-09-06). The fitted
    #   temperature curve fell through the cold end -- a 40F 1500 read 1.3%
    #   easier than a 55F one and lost it -- which is backwards for a race
    #   and is most likely a leak. The apply side treats every temperature
    #   below the curve's minimum as the minimum, on both the day and its
    #   baseline, so nothing colder than the optimum moves a rating.
    if "apparent_temp" in splines:
        splines["apparent_temp"]["optimum"] = splineOptimum(splines["apparent_temp"])
    # linear features: scalar distance slope (effect grows by this * dc).
    dist_betas = {f: float(coef[layout[f + "_dist"]]) for f in LINEAR_FEATURES}
    return (betas, splines, dist_betas, coef, cols["nt"].size,
            _featureCounts(cols), yd, Xd, layout)


def fitCourseSoil(cols, coef, yd, Xd, layout):
    """
    Per-COURSE mud sensitivity s_c: applied mud correction = beta_soil*s_c*(soil-ref).
    s_c scales this course's share of the global mud CURVE: grass ~1, blacktop ~0
    (mutes itself). Thin/quiet courses shrink toward 1 (the global mean).
    """
    scols = layout["soil_spline"]
    m = Xd[:, scols] @ coef[scols]                   # global mud-CURVE prediction (demeaned)
    if not np.any(m):
        return {}
    full_pred = Xd @ coef                            # everything the model explains
    r_soil = yd - full_pred + m                      # add the mud curve back -> mud + noise
    labels, cc = np.unique(cols["course"], return_inverse=True)
    nc = labels.size
    num = np.bincount(cc, weights=r_soil * m, minlength=nc)
    den = np.bincount(cc, weights=m * m, minlength=nc)   # ~ how much this course's mud varied
    n_c = np.bincount(cc, minlength=nc)
    safe_den = np.where(den > 0, den, 1.0)           # guard 0/0 -> NaN
    s_raw = np.where(den > 0, num / safe_den, 1.0)   # course's share of the global curve
    # EMPIRICAL-BAYES shrinkage toward 1.0. The per-course slope has variance
    # sigma^2 / den, so its reliability is den / (den + sigma^2/tau^2): a course
    # needs enough MUD-VARYING history (den) to overcome noise before it moves off
    # the global mean. This fixes the old median-K bug where dry courses dragged K
    # to ~0 and let noisy courses escape to the clip ceiling.
    sigma2 = float(np.var(yd - full_pred))           # race-to-race residual noise
    tau2 = 0.25                                       # prior: s_c ~ N(1, 0.5^2)
    v = den > 0
    # ★ THE SPREAD OF TRUE SENSITIVITIES, MEASURED (2026-10-02). tau2 = 0.25 is
    #   a stated prior. Method of moments: the raw slopes' spread about 1 is
    #   the true spread PLUS each course's own sampling noise (sigma2 / den),
    #   so tau2 = var(s_raw) - mean(sigma2 / den), over courses with mud
    #   history. Printed every fit; used under XCP_MUD_SENS=eb.
    tau2_mm = float(np.mean((s_raw[v] - 1.0) ** 2) - np.mean(sigma2 / den[v])) if v.any() else 0.0
    mode = (os.environ.get("XCP_MUD_SENS") or "capped").strip().lower()
    if mode == "eb" and tau2_mm > 0:
        tau2 = tau2_mm
    K = sigma2 / tau2
    w = den / (den + K)                              # signal-to-noise weight
    # Clip to [0, 1], NOT [0, 2]. The diagnostic showed per-course slopes with
    # p05/p95 of ~-24/+31: the "which grass course is muddiest" question is noise
    # (needs many wet AND dry runnings most courses lack). But "does this course
    # respond to mud at ALL" is reliably detectable at the LOW end (blacktop/firm
    # read a clean ~0). So a course may MUTE the global mud curve, not amplify it.
    #
    # ★ XCP_MUD_SENS=eb LIFTS THE CEILING (owner, 2026-10-02: muddy races
    #   under-credited; 9,294 of 17,667 courses read 1.00). The shrinkage
    #   above IS the noise control: a course with little wet history is held
    #   near 1 by its weight w, and only one with a lot can move. Capping the
    #   shrunk value at 1 as well says no course is ever muddier than the
    #   average, which biases every genuinely soft course down. Under 'eb' the
    #   floor stays (a course cannot get FASTER in mud) and the ceiling goes;
    #   tau2 is the measured one. Default 'capped' is the shipped behaviour.
    shrunk = w * s_raw + (1.0 - w) * 1.0
    s_c = np.clip(shrunk, 0.0, None if mode == "eb" else 1.0)
    above = v & (shrunk > 1.0)
    print(f"[soil-diag] mode {mode}: tau2 used {tau2:.4g} (stated 0.25, measured "
          f"{tau2_mm:.4g})  |  {int(above.sum()):,} courses' shrunk sensitivity is "
          f"above 1 (median {np.median(shrunk[above]) if above.any() else float('nan'):.2f}, "
          f"p95 {np.quantile(shrunk[above], .95) if above.any() else float('nan'):.2f}) -- "
          + ("kept" if mode == "eb" else "CUT to 1 (XCP_MUD_SENS=eb keeps them)"))
    print(f"[soil-diag] of the courses at 1.00: {int(((s_c >= 0.999) & ~v).sum()):,} have no "
          f"mud history (the prior), {int(((s_c >= 0.999) & v).sum()):,} have some")
    # DIAGNOSTIC: is it shrinking, or slamming to the rails? These numbers say which.
    print(f"[soil-diag] sigma2={sigma2:.4g}  K={K:.4g}  |  den p50={np.median(den[v]):.4g} "
          f"p95={np.quantile(den[v], .95):.4g}  |  w p50={np.median(w[v]):.2f} "
          f"p95={np.quantile(w[v], .95):.2f}")
    print(f"[soil-diag] s_raw p05/p50/p95 = {np.quantile(s_raw[v], .05):+.2f} / "
          f"{np.median(s_raw[v]):+.2f} / {np.quantile(s_raw[v], .95):+.2f}  |  s_c: "
          f"{int((s_c <= 0.001).sum())} at floor, {int((s_c >= 0.999).sum())} at or above 1.0, "
          f"{v.sum()} varying")
    return {str(labels[i]): {"s": float(s_c[i]), "n": int(n_c[i]),
                             "mv": float(den[i])}     # mud-variation, for the readout filter
            for i in range(nc)}


# ------------------------------------------------------------------ #
# CHUNK 4 — GATE REPORT + course eyeball + shape.
# ------------------------------------------------------------------ #

# step + label for the LINEAR features (temp is reported via the spline curve).
_STEP = {"wind": 5.0, "precip": 5.0, "snow": 0.05}
_UNIT = {"wind": "+5 m/s wind", "precip": "+5 mm rain",
         "snow": "+5 cm snow underfoot"}


def _curveDistPct(spline, x, dist):
    """Spline effect at value x AND race distance, as % time. The curve's
       COEFFICIENTS shift with dc = dist/DIST_REF - 1, so its whole SHAPE changes
       with distance (not just a uniform tilt)."""
    dc = dist / DIST_REF - 1.0
    knots = np.array(spline["knots"])
    c = np.array(spline["coef"]) + np.array(spline["coef_dist"]) * dc
    b = _nsBasis(np.array([float(x), spline["ref"]]), knots)
    return (np.exp((b[0] - b[1]) @ c) - 1) * 100


def _curvePct(spline, x):
    """Fitted % time at value x vs the spline's reference."""
    knots = np.array(spline["knots"]); c = np.array(spline["coef"])
    b = _nsBasis(np.array([float(x), spline["ref"]]), knots)
    return (np.exp((b[0] - b[1]) @ c) - 1) * 100


def reportGate(betas, splines, dist_betas, n_used, counts, coverage):
    print(f"\n[gate] two-way FE on {n_used:,} races (athlete + RACE-EVENT absorbed)")
    ne, usable = coverage
    print(f"[gate] {ne:,} events; {usable:.0%} of races recur (>=2 runnings).")
    print(f"[gate] reference (no-op point): {REFERENCE}")
    _DVIEW = (1500.0, 5000.0, 10000.0)          # distances to show the interaction
    tsp = splines["apparent_temp"]
    print(f"    apparent_temp (SPLINE, deg C; ref {tsp['ref']:.0f}C) "
          f"@ {'/'.join(str(int(d)) for d in _DVIEW)}m:")
    for t in (0, 5, 13, 20, 27, 33):
        cells = "  ".join(f"{_curveDistPct(tsp, t, d):+6.2f}%"
                          for d in _DVIEW)
        print(f"        {t:>3}C => {cells}")
    if tsp.get("optimum") is not None:
        print(f"        optimum {tsp['optimum']:.1f}C: nothing colder moves a rating")
    ssp = splines["soil"]
    print(f"    soil / MUD (SPLINE; ref {ssp['ref']:.2f}) @ {'/'.join(str(int(d)) for d in _DVIEW)}m:")
    for sv in (0.10, 0.30, 0.50):
        cells = "  ".join(f"{_curveDistPct(ssp, sv, d):+6.2f}%"
                          for d in _DVIEW)
        print(f"        {sv:.2f} => {cells}")
    for f in LINEAR_FEATURES:
        base = (np.exp(betas[f] * _STEP[f]) - 1) * 100
        far = (np.exp((betas[f] + dist_betas[f] * (10000.0 / DIST_REF - 1)) * _STEP[f]) - 1) * 100
        warn = "  <-- thin, noisy" if counts[f] < 500 else ""
        print(f"    {_UNIT[f]:<22} => {base:+6.2f}% @5k / {far:+6.2f}% @10k   "
              f"(beta {betas[f]:+.5f}, dist {dist_betas[f]:+.5f}){warn}")
    print("\n[gate] READ THIS: temp should bend UP hot; mud should bend UP wet "
          "(threshold). Per-course s_c then mutes mud on firm/paved courses.")


def reportCourse(cols, needle):
    mask = np.array([needle.lower() in str(c).lower() for c in cols["course"]])
    if not mask.any():
        print(f"\n[course] no course matched '{needle}'.")
        return
    ac, na = _denseCode(athleteSeason(cols))
    adj = np.log(cols["nt"]) - _groupMeanFull(np.log(cols["nt"]), ac, na)
    d_, a_, t_, p_ = (cols["date"][mask], adj[mask],
                      cols["apparent_temp"][mask], cols["precip"][mask])
    seen = {}
    for d, a, t, p in zip(d_, a_, t_, p_):
        rec = seen.setdefault(d, [0.0, 0, 0.0, 0.0])
        rec[0] += a; rec[1] += 1; rec[2] += t; rec[3] += p
    print(f"\n[course] runnings matching '{needle}'  (slow% > 0 = field ran SLOW)")
    print(f"    {'date':<12}{'n':>5}{'peak F':>9}{'rain mm':>9}{'slow %':>9}")
    for d in sorted(seen):
        s, n, tsum, psum = seen[d]
        print(f"    {d:<12}{n:>5}{tsum/n:>9.1f}{psum/n:>9.2f}"
              f"{(np.exp(s / n) - 1) * 100:>+9.2f}")


def _groupMeanFull(v, codes, n):
    s = np.bincount(codes, weights=v, minlength=n)
    c = np.bincount(codes, minlength=n)
    return (s / c)[codes]


# reportShape: the linear-vs-curve test, read correctly for EVENT FE. Remove
#   athlete+event from BOTH the time residual and the temperature, so we bin by
#   WITHIN-EVENT temperature deviation (the variation the fit actually uses) --
#   binning by absolute temp would smear it (a meet's cold year sits at low
#   absolute temp but is really "colder than usual for that meet").
def reportShape(cols, feature, nbins=8):
    if feature not in FEATURES:
        print(f"\n[shape] '{feature}' not a feature ({', '.join(FEATURES)}).")
        return
    ac, na = _denseCode(athleteSeason(cols))
    ec, ne = _denseCode(cols["event"])
    resid = _twoWayDemeanY(np.log(cols["nt"]), ac, na, ec, ne)          # weather+noise
    xdev = _twoWayDemeanY(_featureColumn(cols, feature).astype(float), ac, na, ec, ne)
    edges = np.quantile(xdev, np.linspace(0, 1, nbins + 1))
    edges[-1] += 1e-9
    print(f"\n[shape] {feature}: adjusted slow% by WITHIN-EVENT deviation "
          f"(athlete+event removed)")
    print(f"    {'dev band':<18}{'n':>9}{'slow %':>9}")
    for i in range(nbins):
        m = (xdev >= edges[i]) & (xdev < edges[i + 1])
        if not m.any():
            continue
        slow = (np.exp(resid[m].mean()) - 1) * 100
        print(f"    {f'{edges[i]:+.1f}..{edges[i+1]:+.1f}':<18}"
              f"{int(m.sum()):>9}{slow:>+9.2f}")
    print("    ^ 0 = this meet's typical. Straight => linear; steepening at the "
          "hot (positive-dev) end => nonlinear, add a hinge/squared term.")


# reportSoil: eyeball the per-course sensitivities. Only show courses with real
#             MUD-VARYING history (top half by "mv"), so the extremes are trustworthy
#             -- a course that's never muddy has no meaningful sensitivity to show.
def reportSoil(soil_map, top=12):
    eligible = {c: v for c, v in soil_map.items() if v["n"] >= 50}
    if not eligible:
        print("\n[soil] no courses with >=50 races -- skipping per-course readout.")
        return
    mv_cut = float(np.median([v["mv"] for v in eligible.values()]))
    solid = {c: v for c, v in eligible.items() if v["mv"] >= mv_cut and v["mv"] > 0}
    if not solid:
        print("\n[soil] no courses with enough mud variation to rank.")
        return
    ordered = sorted(solid.items(), key=lambda kv: kv[1]["s"])
    print(f"\n[soil] per-course mud sensitivity s_c (1.0 = avg grass, 0 = no "
          f"response). {len(solid):,} courses with >=50 races AND real mud history.")
    print("  MOST mud-immune (blacktop/track/firm -> mute the correction):")
    for c, v in ordered[:top]:
        print(f"    s={v['s']:.2f}  n={v['n']:>6}  {c[:48]}")
    print("  MOST mud-sensitive (soft grass -> full correction):")
    for c, v in ordered[-top:][::-1]:
        print(f"    s={v['s']:.2f}  n={v['n']:>6}  {c[:48]}")


# ------------------------------------------------------------------ #
# CHUNK 5 — SAVE.
# ------------------------------------------------------------------ #

# buildVenueNormals
# Purpose:   the climatological normal each race's weather is measured AGAINST.
# Arguments: cols -- loaded race columns; needs "event" plus every feature in
#                    QUERIED_FEATURES.
# Output:    {"by_event": {event_key: {feature: value}}, "global": {feature: value}}
#
# WHY THIS EXISTS -- THE FIT AND THE APPLICATION DISAGREED ON THE BASELINE.
#   beta is estimated by _twoWayWithin, which demeans by athlete AND event,
#   where event = (venue, fortnight). So beta is a WITHIN-VENUE coefficient: it
#   measures what a hot year at Woodward in early November does relative to a
#   cold year at Woodward in early November.
#
#   normalize_distance then applied it as (v - REFERENCE[f]) against a global
#   55F. A 95F race in Phoenix was charged 40 degrees of correction, when the
#   model only ever learned what an UNUSUAL 95F does. The residue landed in
#   course difficulty.
#
#   Storing the group means here lets the applier subtract the same baseline
#   the coefficient was fitted within.
#
# THE KEY IS cols["event"], NOT THE VENUE. A venue-only normal would fold
# August and November together, and the correction would then partly re-absorb
# the seasonal cycle it has no business touching.
#
# CONSEQUENCE, DELIBERATE: a venue's persistent climate stops being corrected
# and flows into its course difficulty. That is where a standing venue property
# belongs, and it stops weather and difficulty double-counting the same effect.
def buildVenueNormals(cols):
    events, codes = np.unique(cols["event"], return_inverse=True)
    n_events = events.size
    counts = np.bincount(codes, minlength=n_events).astype(np.float64)

    by_event = {ev: {} for ev in events.tolist()}
    global_means = {}

    for feature in QUERIED_FEATURES:
        v = np.asarray(cols[feature], dtype=np.float64)

        # NaN-safe: a feature can be missing on some rows, and one NaN would
        # poison a whole group's mean through bincount.
        ok = np.isfinite(v)
        sums = np.bincount(codes[ok], weights=v[ok], minlength=n_events)
        n_ok = np.bincount(codes[ok], minlength=n_events).astype(np.float64)

        g = float(v[ok].mean()) if ok.any() else 0.0
        global_means[feature] = g
        shrunk = (sums + VENUE_NORM_SHRINK_K * g) / (n_ok + VENUE_NORM_SHRINK_K)

        for i, ev in enumerate(events.tolist()):
            by_event[ev][feature] = float(shrunk[i])

    print(f"\n[norms] {n_events:,} (venue, fortnight) groups, "
          f"median {np.median(counts):.0f} races each")
    for feature, g in global_means.items():
        print(f"[norms]   {feature:>16}  global mean {g:.2f}")

    return {"by_event": by_event, "global": global_means}


def splineOptimum(spline, n=400):
    """The x at which the fitted curve is lowest, over the knot range."""
    knots = sorted(set(spline["knots"]))
    xs = np.linspace(knots[0], knots[-1], n)
    vals = [_curvePct(spline, float(x)) for x in xs]
    return float(xs[int(np.argmin(vals))])


def saveArtifact(betas, splines, dist_betas, soil_map, venue_norms, sport):
    path = ARTIFACT_TMPL.format(sport=sport)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump({"reference": REFERENCE,
                     "betas": betas,                 # linear features (wind/precip/soil/snow)
                     "splines": splines,             # {feat:{knots,coef,ref}} temp+soil
                     "dist_betas": dist_betas,       # {feat: slope} distance interaction
                     "dist_ref": DIST_REF,
                     # The baseline the correction subtracts. Keyed exactly as
                     # _eventKey builds it, so normalize_distance can rebuild
                     # the key from (course_name, day-of-year) with no lookup.
                     "venue_norms": venue_norms,
                     "venue_norm_shrink_k": VENUE_NORM_SHRINK_K,
                     "linear_features": list(LINEAR_FEATURES),
                     "wx_agg": WX_AGG, "race_local_hours": RACE_LOCAL_HOURS,
                     "linear_features_used": list(LINEAR_FEATURES),
                     "soil_sensitivity": soil_map,   # {course:{"s","n"}}; apply beta_soil*s
                     "note": "apparent_temp modelled by restricted cubic SPLINE (deg C); "
                             "race-event FE; mud = beta_soil * s_c(course)"}, f)
    print(f"\n[save] wrote {path}  (+{len(soil_map):,} per-course soil s_c)")


# ------------------------------------------------------------------ #
# CHUNK 6 — CLI
# ------------------------------------------------------------------ #

def _parseArgs():
    p = argparse.ArgumentParser(description="Measure + fit the weather correction "
                                            "(athlete + race-event fixed effects).")
    p.add_argument("--sport", choices=["XC", "TF"], default="XC")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--course", type=str, default=None,
                   help="print one venue's runnings by eye, e.g. --course Woodward")
    p.add_argument("--shape", type=str, nargs="?", const="apparent_temp",
                   default=None,
                   help="curve test for a feature (default apparent_temp).")
    p.add_argument("--refresh", action="store_true",
                   help="ignore the cache and re-query the DB.")
    p.add_argument("--measure-only", action="store_true",
                   help="print the gate but do NOT save the artifact.")
    return p.parse_args()


def main():
    args = _parseArgs()
    global RACE_LOCAL_HOURS, LINEAR_FEATURES, WX_AGG
    RACE_LOCAL_HOURS = RACE_LOCAL_HOURS_BY_SPORT[args.sport]
    LINEAR_FEATURES = LINEAR_FEATURES_BY_SPORT[args.sport]
    WX_AGG = dict(WX_AGG_BY_SPORT[args.sport])
    # ★ TRACK'S TEMPERATURE SUMMARY IS MEASURED, NOT CHOSEN (owner, 2026-10-04:
    #   Hammerand's WashU 10k, 29:20, got +7.03% for a 33C 3pm peak while the
    #   race ran at night in about 22C; his 29:27 at NCAA got nothing -- an
    #   8.7-point gap for 0.4% of time). Without start times neither summary
    #   is right for every race: the 9am-8pm mean understated a 3pm race in
    #   heat (why it became the max on 2026-09-06), the max credits evening
    #   races with afternoon heat. Both are queried on the same rows and the
    #   one that explains more of the same meet's year-to-year slowdowns
    #   (lower residual sum of squares after the athlete-season and meet
    #   effects) is fitted and saved. XCP_TF_TEMP_AGG=max|avg forces one.
    tf_compare = args.sport == "TF"
    forced = os.environ.get("XCP_TF_TEMP_AGG", "").strip().lower()
    if tf_compare and forced in ("max", "avg"):
        WX_AGG["apparent_temp"] = f"{forced}(apparent_temperature)"
        tf_compare = False
    if tf_compare:
        global QUERIED_FEATURES
        WX_AGG["apparent_temp_max"] = "max(apparent_temperature)"
        WX_AGG["apparent_temp_avg"] = "avg(apparent_temperature)"
        QUERIED_FEATURES = tuple(WX_AGG.keys())
    print(f"[config] {args.sport}: window {RACE_LOCAL_HOURS} local, "
          f"linear features {LINEAR_FEATURES}, "
          f"temperature {WX_AGG['apparent_temp']}")
    sql = xcQuery() if args.sport == "XC" else tfQuery()
    cols = getColumns(args.sport, sql, args.limit, args.refresh)
    if cols["nt"].size == 0:
        print("[load] no rows matched -- check the weather join / filters.")
        return
    if tf_compare:
        # ! THE TWO CANDIDATES ARE LOAD-ONLY COLUMNS. Every reader after the
        #   load (the undo, _featureCounts, _collinearity) walks
        #   QUERIED_FEATURES and looks each up in REFERENCE, which has no
        #   entry for them: left in, the first fit raised KeyError
        #   'apparent_temp_max' (04f_weather_fit_tf, run 20261004_125010).
        #   The columns stay in `cols` for the comparison below.
        QUERIED_FEATURES = tuple(k for k in WX_AGG
                                 if k not in ("apparent_temp_max", "apparent_temp_avg"))
    import time as _time
    print(f"[load] {cols['nt'].size:,} races joined to weather.", flush=True)
    t0 = _time.time()
    art, src = appliedArtifact(args.sport)
    if tf_compare:
        # the undo must read the temperature the APPLIED correction read
        import normalize_distance as _nd
        applied = _nd.weatherTempAgg(art) if art is not None else "max(apparent_temperature)"
        cols = dict(cols)
        cols["apparent_temp"] = cols["apparent_temp_" + applied.split("(", 1)[0]]
    cols = undoAppliedWeather(cols, args.sport, art, src)
    if tf_compare:
        rss = {}
        for cand in ("max", "avg"):
            c_ = dict(cols)
            c_["apparent_temp"] = cols["apparent_temp_" + cand]
            *_r, yd_c, Xd_c, _lay = fitWeather(c_)
            coef_c, *_ = np.linalg.lstsq(Xd_c, yd_c, rcond=None)
            res = yd_c - Xd_c @ coef_c
            rss[cand] = (float(res @ res), float(yd_c @ yd_c), int(yd_c.size))
        best = min(rss, key=lambda k: rss[k][0])
        for cand, (r, tot, n_) in rss.items():
            print(f"[temp] TF temperature as the {cand} over {RACE_LOCAL_HOURS}: weather explains "
                  f"{100 * (1 - r / tot):.3f}% of the within-meet variance (rss {r:.4f}, "
                  f"{n_:,} rows){'   <- used' if cand == best else ''}", flush=True)
        WX_AGG["apparent_temp"] = f"{best}(apparent_temperature)"
        cols["apparent_temp"] = cols["apparent_temp_" + best]
        for k in ("apparent_temp_max", "apparent_temp_avg"):
            WX_AGG.pop(k, None)
    print(f"[time] undo {_time.time() - t0:.0f}s", flush=True)
    t0 = _time.time()
    betas, splines, dist_betas, coef, n_used, counts, yd, Xd, layout = fitWeather(cols)
    print(f"[time] fit {_time.time() - t0:.0f}s", flush=True)
    coverage = _eventCoverage(cols)
    reportGate(betas, splines, dist_betas, n_used, counts, coverage)
    _collinearity(cols)
    soil_map = fitCourseSoil(cols, coef, yd, Xd, layout)   # per-course mud sensitivity
    if args.course:
        reportCourse(cols, args.course)
    if args.shape:
        reportShape(cols, args.shape)
    reportSoil(soil_map)

    if not args.measure_only:
        venue_norms = buildVenueNormals(cols)
        saveArtifact(betas, splines, dist_betas, soil_map, venue_norms,
                     args.sport)
        print("\n[apply] in normalize_distance.py, after era:")
        print("    temp term  = tempspline(temp) - tempspline(ref)")
        print("    mud  term  = s_c(course) * (soilspline(soil) - soilspline(ref))")
        print("    lin terms  = sum_f beta[f]*(weather[f]-ref[f])  for wind/precip/snow")
        print("    wmult = exp(temp term + lin terms)")
        print("    normalized_time = normalized_time / wmult   # 1.0 in mild weather")


if __name__ == "__main__":
    main()