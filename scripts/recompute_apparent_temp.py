#!/usr/bin/env python3
"""recompute_apparent_temp.py -- rewrite weather_grid.apparent_temperature
with the corrected sun term, from the raw inputs every row already stores.

    /srv/venv/bin/python scripts/recompute_apparent_temp.py           # measure
    /srv/venv/bin/python scripts/recompute_apparent_temp.py --apply   # rewrite

The grid's feels-like fed raw downward sunlight into Steadman's formula
where it wants the radiation a body absorbs (SITE_NOTES #4): every sunny
hour read 20-35 C too hot. backfill/atmost_era5_zarr.py now uses
SUN_ABSORBED_FRACTION of the irradiance; this applies the same rule to
the rows already fetched, in SQL, in place. No cloud reads. Run it ONCE,
before the weather refit, and after the fetcher has pulled the new
season (the fetcher writes the corrected value itself).

The dry run prints, over a sample of daytime hours, the old value, the
new value and the gap, so the 20-35 C is seen leaving before anything is
written. ⚠ AFTER --apply THE WEATHER ARTIFACTS ARE STALE: they were
fitted on the old numbers. Refit both sports (fit_weather_correction
--sport XC / TF) before the next backfill.
"""
import argparse
import os
import sys
import time

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from database import getConn                          # noqa: E402

# ! MIRRORS backfill/atmost_era5_zarr.SUN_ABSORBED_FRACTION. Not imported:
#   that module needs xarray and pandas, which live in the fetcher's own
#   venv and not the pipeline's. tests/test_weather_fixes.py pins the two.
SUN_ABSORBED_FRACTION = 0.175

# The same arithmetic as _deriveApparentTemperature, in SQL. Units as
# stored: degC, %, km/h, W/m2.
_AT = f"""
    (temperature_2m
     + 0.348 * ((relative_humidity_2m / 100.0) * 6.105
                * exp((17.27 * temperature_2m) / (237.7 + temperature_2m)))
     - 0.70 * (wind_speed_10m / 3.6)
     + 0.70 * ({SUN_ABSORBED_FRACTION} * solar_radiation / ((wind_speed_10m / 3.6) + 10.0))
     - 4.25)
"""

_WHERE = ("temperature_2m IS NOT NULL AND relative_humidity_2m IS NOT NULL "
          "AND wind_speed_10m IS NOT NULL AND solar_radiation IS NOT NULL")


def measure(cur):
    cur.execute(f"""
        WITH s AS (
            SELECT apparent_temperature AS old_at, {_AT} AS new_at, solar_radiation
            FROM   weather_grid TABLESAMPLE SYSTEM (1)
            WHERE  {_WHERE}
        )
        SELECT count(*),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY old_at - new_at)
                   FILTER (WHERE solar_radiation > 300),
               percentile_cont(0.9) WITHIN GROUP (ORDER BY old_at - new_at)
                   FILTER (WHERE solar_radiation > 300),
               max(old_at - new_at),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY old_at) FILTER (WHERE solar_radiation > 300),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY new_at) FILTER (WHERE solar_radiation > 300),
               max(old_at), max(new_at)
        FROM s
    """)
    n, med, p90, mx, old_med, new_med, old_max, new_max = cur.fetchone()
    print(f"  sample {n:,} hourly rows (1% of the grid)")
    print(f"  daytime (sun > 300 W/m2): old feels-like median {old_med:.1f} C, "
          f"new {new_med:.1f} C; the sun term shrinks by median {med:.1f} C, "
          f"90th pct {p90:.1f} C, max {mx:.1f} C")
    print(f"  hottest hour in the sample: old {old_max:.1f} C, new {new_max:.1f} C")


def apply(conn):
    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute(f"UPDATE weather_grid SET apparent_temperature = {_AT} WHERE {_WHERE}")
        n = cur.rowcount
    conn.commit()
    print(f"  rewrote {n:,} rows in {time.time() - t0:.0f}s")
    # an UPDATE of every row leaves a table twice its size: vacuum it now,
    # outside a transaction
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            t0 = time.time()
            cur.execute("VACUUM ANALYZE weather_grid")
        print(f"  VACUUM ANALYZE weather_grid ({time.time() - t0:.0f}s)")
    finally:
        conn.autocommit = False
    print("  now refit: fit_weather_correction --sport XC and --sport TF "
          "(XCP_WEATHER_FIT=1 in the pipeline), then a full backfill")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="rewrite the column")
    a = ap.parse_args()
    with getConn() as conn:
        with conn.cursor() as cur:
            measure(cur)
        if a.apply:
            apply(conn)
        else:
            print("  (dry run; --apply rewrites the column)")


if __name__ == "__main__":
    main()
