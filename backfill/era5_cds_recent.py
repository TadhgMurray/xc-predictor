#!/usr/bin/env python3
"""
era5_cds_recent.py -- weather for the months the free ERA5 store has not
reached yet, from the Copernicus Climate Data Store (CDS). Pipeline step
04e2, in the background beside 04e.

    /srv/wxvenv/bin/python backfill/era5_cds_recent.py --dry-run
    /srv/wxvenv/bin/python backfill/era5_cds_recent.py

★ WHY (owner, 2026-10-25: weather_grid stopped at 2026-06-30). The free
  Earthmover store that atmost_era5_zarr.py reads is updated monthly with a
  3-4 month lag, so every meet since the summer had no weather, and 04e
  skipped them in silence. The CDS serves the same ERA5 (its early release,
  ERA5T) about five days behind real time: same model, same grid, same
  variables and units, so its rows go into the same table through the same
  conversion code and the weather fit reads them like any other.

  Work: every (cell, day) the meets need that weather_grid lacks, after the
  free store's last day and at least LAG_DAYS old. One CDS request per day
  per region (the lower 48 one box; Alaska, Hawaii and anything else by
  connected 10-degree tiles), sampled with the
  zarr script's own fetchTileWeather and written with its writeWeatherRows.
  Rows already there are skipped (ON CONFLICT DO NOTHING), so it resumes.

! NEEDS A FREE CDS ACCOUNT (cds.climate.copernicus.eu): accept the ERA5
  single-levels licence on the dataset page, then put the key in
  /etc/xc-predictor.env as
      CDSAPI_URL=https://cds.climate.copernicus.eu/api
      CDSAPI_KEY=<your key>
  and `/srv/wxvenv/bin/pip install cdsapi netCDF4`. Without a key the step
  says so and exits 0. The licence asks for attribution ("Contains modified
  Copernicus Climate Change Service information").
"""
import argparse
import datetime
import os
import shutil
import sys
import tempfile
import time
import zipfile

import atmost_era5_zarr as Z                       # same dir; puts scripts/ on the path
from database import getConn, initPool, closePool

DATASET = "reanalysis-era5-single-levels"
LAG_DAYS = 6                 # ERA5T is ~5 days behind; one day of slack
TILE_DEG = 10.0              # region grouping: cells in touching 10-degree tiles
MARGIN = 0.25                # one grid step around each region's box

# The store's short names (Z.VAR_MAP's values) -> the CDS request names.
CDS_VARS = {
    "t2m":   "2m_temperature",
    "d2m":   "2m_dewpoint_temperature",
    "tp":    "total_precipitation",
    "sp":    "surface_pressure",
    "tcc":   "total_cloud_cover",
    "u10":   "10m_u_component_of_wind",
    "v10":   "10m_v_component_of_wind",
    "ssrd":  "surface_solar_radiation_downwards",
    "fg10":  "10m_wind_gust_since_previous_post_processing",
    "swvl1": "volumetric_soil_water_layer_1",
    "sd":    "snow_depth",
    "sf":    "snowfall",
}
assert set(CDS_VARS) == set(Z.VAR_MAP.values()), "CDS_VARS must cover VAR_MAP"


# ------------------------------------------------------------------ #
#  pure parts
# ------------------------------------------------------------------ #

def lon180(lon):
    """0..360 grid longitude -> -180..180, the CDS area's convention."""
    return round(((lon + 180.0) % 360.0) - 180.0, 2)


def _lower48(cl, cn):
    lon = lon180(cn)
    return 24.0 <= cl <= 50.0 and -125.5 <= lon <= -66.0


def regions(cells, tile=TILE_DEG):
    """Group (cellLat, cellLon) cells into request regions: every lower-48
    cell in ONE box (a summer day's 14 scattered cells were six requests,
    each queued at the CDS), the rest by touching tiles (8 neighbours).
    Returns [(cells, [N, W, S, E])], the box in -180..180 with MARGIN
    around it. Pure."""
    by_tile = {}
    for cl, cn in cells:
        key = ("lower48",) if _lower48(cl, cn) else (int(cl // tile), int(lon180(cn) // tile))
        by_tile.setdefault(key, []).append((cl, cn))
    seen, out = set(), []
    for start in by_tile:
        if start in seen:
            continue
        stack, group = [start], []
        seen.add(start)
        while stack:
            t = stack.pop()
            group.extend(by_tile[t])
            if t == ("lower48",):
                continue
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    n = (t[0] + dy, t[1] + dx)
                    if n in by_tile and n not in seen:
                        seen.add(n)
                        stack.append(n)
        lats = [c[0] for c in group]
        lons = [lon180(c[1]) for c in group]
        box = [min(90.0, max(lats) + MARGIN), max(-180.0, min(lons) - MARGIN),
               max(-90.0, min(lats) - MARGIN), min(180.0, max(lons) + MARGIN)]
        out.append((sorted(group), box))
    return out


def request(day, box):
    """The CDS request for one day over one box."""
    y, m, d = day.split("-")
    return {"product_type": ["reanalysis"],
            "variable": sorted(CDS_VARS.values()),
            "year": [y], "month": [m], "day": [d],
            "time": [f"{h:02d}:00" for h in range(24)],
            "area": box,
            "data_format": "netcdf",
            "download_format": "unarchived"}


def normalise(ds):
    """A CDS download -> the shape fetchTileWeather reads: hourly axis named
    valid_time, longitude 0..360 ascending, no ensemble or expver dims."""
    if "time" in ds.dims and Z.TIME_DIM not in ds.dims:
        ds = ds.rename({"time": Z.TIME_DIM})
    if "expver" in ds.dims:              # the old format split ERA5 / ERA5T
        ds = ds.max("expver")
    for c in ("number", "expver"):
        if c in ds.coords and c not in ds.dims:
            ds = ds.drop_vars(c)
    ds = ds.assign_coords(longitude=(ds["longitude"] % 360).round(2)).sortby("longitude")
    missing = [v for v in CDS_VARS if v not in ds.data_vars]
    if missing:
        raise KeyError(f"CDS download lacks {missing}; has {sorted(ds.data_vars)}")
    return ds


# ------------------------------------------------------------------ #
#  the work
# ------------------------------------------------------------------ #

def _haveKey():
    return bool(os.environ.get("CDSAPI_KEY")) or os.path.exists(
        os.environ.get("CDSAPI_RC", os.path.expanduser("~/.cdsapirc")))


def _storeEnd():
    """The free store's last day, so the two steps never fetch the same days."""
    ds = Z.openEra5Store()
    return str(ds[Z.TIME_DIM].values[-1])[:10]


def _openDownload(path, tmp):
    """One .nc, or a zip of them (the CDS splits instant and accumulated
    fields into two files when it has to) -> one in-memory Dataset."""
    xr = Z.xr
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp)
        files = sorted(os.path.join(tmp, f) for f in os.listdir(tmp) if f.endswith(".nc"))
        parts = [xr.open_dataset(f).load() for f in files]
        return xr.merge(parts, compat="override", join="outer")
    return xr.open_dataset(path).load()


def fetchDay(client, day, cells):
    """Rows for every cell of one day, region by region."""
    rows = []
    for group, box in regions(cells):
        tmp = tempfile.mkdtemp(prefix="cds_")
        try:
            target = os.path.join(tmp, "dl")
            client.retrieve(DATASET, request(day, box), target)
            ds = normalise(_openDownload(target, tmp))
            rows.extend(Z.fetchTileWeather(ds, [(c, [day]) for c in group]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--dry-run", action="store_true", help="list the work, fetch nothing")
    p.add_argument("--after", help="fetch days after this one (default: the free store's last day)")
    p.add_argument("--limit", type=int, help="at most this many days (a smoke test)")
    args = p.parse_args()

    if not args.dry_run and not _haveKey():
        print("no CDS key (CDSAPI_KEY in /etc/xc-predictor.env, or ~/.cdsapirc): "
              "recent weather skipped -- see backfill/era5_cds_recent.py", flush=True)
        return
    after = args.after or _storeEnd()
    until = str(datetime.date.today() - datetime.timedelta(days=LAG_DAYS))
    initPool()
    try:
        with getConn() as conn:
            work = Z.loadMeetGrid(conn)
            done = Z.loadDoneKeys(conn)
        days = {}
        for cl, cn, d in work.itertuples(index=False):
            d = str(d)[:10]
            if after < d <= until and (cl, cn, d) not in done:
                days.setdefault(d, []).append((cl, cn))
        order = sorted(days)[:args.limit] if args.limit else sorted(days)
        print(f"free store ends {after}; {len(order)} days to fetch through {until} "
              f"({sum(len(days[d]) for d in order)} cell-days)", flush=True)
        if args.dry_run or not order:
            for d in order[:10]:
                print(f"    {d}: {len(days[d])} cells, {len(regions(days[d]))} region(s)")
            return
        import cdsapi
        client = cdsapi.Client(quiet=True, progress=False)
        failed, t0 = 0, time.time()
        for i, d in enumerate(order, 1):
            ts = time.time()
            try:
                rows = fetchDay(client, d, days[d])
            except Exception as exc:                       # noqa: BLE001 -- resumable
                failed += 1
                print(f"  ! {d} failed ({type(exc).__name__}: {exc}); retried next run", flush=True)
                continue
            with getConn() as conn:
                Z.writeWeatherRows(conn, rows)
                conn.commit()
            print(f"  [{i}/{len(order)}] {d}: {len(days[d])} cells, {len(rows)} rows "
                  f"({time.time() - ts:.0f}s)", flush=True)
        if failed == len(order):
            print(f"  !! every day failed ({failed}); step failed", flush=True)
            raise SystemExit(2)
        print(f"done: {len(order) - failed} days in {time.time() - t0:.0f}s"
              f"{f' ({failed} failed -- rerun to retry)' if failed else ''}", flush=True)
    finally:
        closePool()


if __name__ == "__main__":
    main()
