# Project: xc-predictor
# Author:  Tadhg Murray
# File:    scripts/trace_normalize.py
# Purpose: run the REAL normalizeResult on ONE row and find which input
#          produces the factor. No theories, no SQL.
#
#   THE ROW: Cole Hocker, Foot Locker Midwest Regionals, UW Parkside,
#   2018-11-24. 963.2s over 5000m, rated 158.3 -- the highest hs_m race
#   rating in the corpus, above every full athlete-SEASON on record.
#
#       normalized_time / time_seconds = 0.80512
#
#   A 19.5% speedup on a 5000m race whose course difficulty is +0.004.
#
#   ALREADY RULED OUT, each by measurement:
#     distance  the spline is EXACTLY -0.0 at log(5000)=8.5172 for both
#               hs_m|XC and hs_f|XC -- knot 44, zero-anchored as designed.
#               exp(0) = 1.0, so distance contributes nothing here.
#     geometry  would vary by venue; this factor is IDENTICAL to five
#               decimals across every boys' row in the division.
#     era       same date for all rows, and cannot be 19.5%.
#     weather   the girls' divisions at the SAME meet, same day, same grid
#               cell got 0.98857. One afternoon cannot be 19.5% for boys
#               and 1.1% for girls.
#     gender    fixed and verified this session -- every row now reads 'M'
#               and the old 0.83098 unknown-gender band is gone.
#
#   So the factor is per-POOL and applied OUTSIDE the distance spline, and
#   every named component has been eliminated by argument. That is the
#   signal to stop reasoning and RUN THE PRODUCING CODE.
#
#   ★ THE METHOD, from the house rules: "when a produced value is wrong and
#     inputs look identical, replicate the producing code in a trace script
#     -- don't run more SQL." Six hypotheses died to SQL before this.
#
#   HOW IT ISOLATES: call normalizeResult with everything, then ABLATE one
#   argument at a time. Whichever removal moves the factor toward 1.0 owns
#   the correction. This needs no knowledge of the internals and cannot be
#   fooled by a stale pickle, because it is the same code path the backfill
#   runs.

import os
import sys
import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE),
           os.path.join(os.path.dirname(_HERE), "engine"),
           os.path.join(os.path.dirname(_HERE), "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import normalize_distance as nd


# ------------------------------------------------------------------ #
# CHUNK 1 -- THE ROW
# ------------------------------------------------------------------ #
#
# ⚠ `date` MUST BE A date OBJECT, NOT A STRING. normalizeResult does
#   `season = date.year`, and `results.date` is TEXT in the database -- so
#   every caller has to convert. This is the loader-vs-schema seam that the
#   notes record as v1 defect #1 ("`.year` on TEXT `date` -> crash on row
#   1"), and it is still a live trap for anyone calling this directly.
ROW = dict(
    time_seconds = 963.2,
    distance     = 5000.0,
    grade        = "12",
    gender       = "M",
    date         = datetime.date(2018, 11, 24),
    track_length = None,
    track_type   = None,
    sport        = "XC",
    event_short  = None,
    weather      = None,     # filled in CHUNK 2 if the row has any
    course       = "UW Parkside",
)

EXPECTED = 0.80512          # what the database holds for this row


# ------------------------------------------------------------------ #
# CHUNK 2 -- WEATHER, PULLED THE WAY THE BACKFILL PULLS IT
# ------------------------------------------------------------------ #

def loadWeather(lat=42.645004, lon=-87.85174, date="2018-11-24"):
    """
    The race-day weather for this venue's grid cell.

    ⚠ LONGITUDES ARE STORED 0-360, NOT -180..180. Kenosha at -87.85 is
      stored as 272.15. Three earlier queries searched the negative range,
      got zero rows, and produced a confident and WRONG conclusion that the
      venue had no weather at all. The +360 is not cosmetic.
    """
    try:
        from database import getConn
    except ImportError:
        print("    (no database module -- skipping weather)")
        return None

    lon360 = lon + 360.0 if lon < 0 else lon
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT avg(temperature_2m), avg(apparent_temperature),
                       avg(dew_point_2m),  avg(relative_humidity_2m),
                       sum(precipitation), avg(soil_moisture),
                       avg(wind_speed_10m), avg(snow_depth),
                       avg(solar_radiation), avg(surface_pressure)
                FROM   weather_grid
                WHERE  date = %s
                  AND  cell_lat BETWEEN %s AND %s
                  AND  cell_lon BETWEEN %s AND %s
                  AND  hour BETWEEN 9 AND 14
            """, (date, lat - 0.2, lat + 0.2, lon360 - 0.2, lon360 + 0.2))
            r = cur.fetchone()
    if r is None or r[0] is None:
        print("    (no weather rows for that cell-day)")
        return None
    keys = ("temperature_2m", "apparent_temperature", "dew_point_2m",
            "relative_humidity_2m", "precipitation", "soil_moisture",
            "wind_speed_10m", "snow_depth", "solar_radiation",
            "surface_pressure")
    wx = {k: (float(v) if v is not None else None) for k, v in zip(keys, r)}
    print(f"    weather: temp {wx['temperature_2m']:.1f}C  "
          f"apparent {wx['apparent_temperature']:.1f}C  "
          f"soil {wx['soil_moisture']:.6f}  "
          f"precip {wx['precipitation']:.2f}")
    return wx


# ------------------------------------------------------------------ #
# CHUNK 3 -- CALL AND REPORT
# ------------------------------------------------------------------ #

def factorOf(result, raw):
    """
    normalized / raw, from whatever shape normalizeResult returns.

    It returns a dict and the key name is unknown, so this searches rather
    than assuming -- a wrong guess would silently print None and waste the
    run.
    """
    if isinstance(result, dict):
        for k in ("normalized_time", "normalized", "norm", "time"):
            if k in result and result[k]:
                return result[k] / raw, k
        # fall back: any numeric value in a plausible range
        for k, v in result.items():
            if isinstance(v, (int, float)) and 0.3 * raw < v < 3 * raw:
                return v / raw, k
        return None, None
    if isinstance(result, (int, float)):
        return result / raw, "(scalar)"
    return None, None


def call(**over):
    """One normalizeResult call with `over` merged onto ROW."""
    args = dict(ROW)
    args.update(over)
    try:
        out = nd.normalizeResult(**args)
    except Exception as exc:
        return None, f"ERROR {type(exc).__name__}: {exc}"
    f, key = factorOf(out, args["time_seconds"])
    return f, key


def main():
    print("=" * 66)
    print("TRACE: Cole Hocker, UW Parkside 2018-11-24, 963.2s / 5000m")
    print(f"       database says factor = {EXPECTED}")
    print("=" * 66)

    ROW["weather"] = loadWeather()

    # The full call first -- if this does not reproduce EXPECTED, the row's
    # inputs are not what we think and every ablation below is meaningless.
    base, key = call()
    print(f"\n  FULL CALL           factor = {base}   (key: {key})")
    if base is None:
        print("  cannot proceed -- normalizeResult did not return a usable value")
        try:
            raw = nd.normalizeResult(**ROW)
            print(f"  raw return type: {type(raw).__name__}")
            if isinstance(raw, dict):
                for k, v in raw.items():
                    print(f"      {k:<24}{v!r}")
            else:
                print(f"      {raw!r}")
        except Exception as exc:
            print(f"  and it raised: {type(exc).__name__}: {exc}")
        return
    if abs(base - EXPECTED) > 0.002:
        print(f"  ⚠ DOES NOT MATCH THE DATABASE ({EXPECTED}).")
        print("    The stored value came from different inputs or a different"
              " artifact.\n    Fix that before reading the ablations.")

    # ★ ONE ARGUMENT AT A TIME. Whichever removal moves the factor toward
    #   1.0 owns the correction. Removing several at once cannot attribute.
    print("\n  ABLATIONS (factor with that input removed):")
    print(f"    {'removed':<16}{'factor':>10}{'moved':>10}")
    for arg in ("weather", "date", "course", "grade", "gender",
                "sport", "event_short", "track_length", "track_type"):
        if ROW.get(arg) is None:
            continue
        f, _ = call(**{arg: None})
        if f is None:
            print(f"    {arg:<16}{'ERROR':>10}")
            continue
        print(f"    {arg:<16}{f:>10.5f}{f - base:>+10.5f}")

    # Distance is not ablatable (it is required), so vary it instead: the
    # spline is zero-anchored at 5000, so a factor that does NOT move with
    # distance proves the correction is not the distance term.
    print("\n  DISTANCE SWEEP (spline is -0.0 at log(5000), so 5000 should"
          " be ~1.0 from distance alone):")
    for d in (3000.0, 4000.0, 5000.0, 6000.0, 8000.0):
        f, _ = call(distance=d)
        print(f"    {d:>8.0f} m      {f:>10.5f}" if f is not None
              else f"    {d:>8.0f} m         ERROR")

    # Same row, other pools. The girls' divisions at this meet got 0.98857;
    # if switching gender alone reproduces that, the split is per-pool and
    # the culprit is whatever varies by pool.
    print("\n  POOL SWEEP (girls at this meet measured 0.98857):")
    for g, lbl in (("M", "hs_m"), ("F", "hs_f")):
        f, _ = call(gender=g)
        print(f"    {lbl:<12}{f:>10.5f}" if f is not None
              else f"    {lbl:<12}   ERROR")
    for gr, lbl in (("7", "ms"), ("Fr", "college")):
        f, _ = call(grade=gr)
        print(f"    {lbl:<12}{f:>10.5f}" if f is not None
              else f"    {lbl:<12}   ERROR")


if __name__ == "__main__":
    main()