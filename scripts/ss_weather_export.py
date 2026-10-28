#!/usr/bin/env python3
"""
ss_weather_export.py -- each pack row's race-day weather, and the weather
multiplier the backfill put into its normalized_time, for the state-space
engine to fit the weather itself. READ-ONLY on the database.

    cd /srv/ss-review && set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/ss_weather_export.py --repo /srv/xc-predictor \\
        --out /srv/ss-review/weather.npz

★ WHY (owner, 2026-10-24: "weather inside"). Today the weather is a step
  in front of the engine: backfill_normalize divides each time by a fitted
  multiplier (normalize_distance._applyWeather) and the engine never sees
  the weather again, so what the fitter got wrong (a night race charged the
  day's 35 C peak, Hammerand) stays in the rating. With this file
  scripts/ss_eval.py --weather takes the multiplier back off and fits
  temperature, wind and wet ground as shared conditions inside the model,
  through the runners' own trajectories, the race effect taking what a
  grid cell misses.

★ THE SAME LOOKUP AS THE BACKFILL: backfill_normalize.WeatherIndex per sport
  (the meet's place, the race's date, the artifact's race hours) and
  _applyWeather on 1.0 for the multiplier, so the multiplier is the one the
  stored row carries whenever the stored row was normalised with today's
  artifact. Written: result_id, sport (0 XC, 1 track), temp (apparent, the
  artifact's units), wind, wet (soil moisture on XC, precipitation on
  track), log_mult (ln of the multiplier; 0 where none), all NaN where the
  race has no weather.
"""
import argparse
import os
import sys
import time
from datetime import date

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _repo():
    for i, a in enumerate(sys.argv):
        if a == "--repo" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
        if a.startswith("--repo="):
            return os.path.abspath(a.split("=", 1)[1])
    return os.path.abspath(os.path.join(HERE, ".."))


REPO = _repo()
for _p in (os.path.join(REPO, "backfill"), os.path.join(REPO, "scripts"),
           os.path.join(REPO, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BATCH = 200_000


def streamKeys(conn, table, want):
    """(result_id, source, meet_id, date) for the rows of `table` in `want`
    (a sorted int64 array), by one server-side pass."""
    out = []
    with conn.cursor(name=f"wx_{table}") as cur:
        cur.itersize = BATCH
        cur.execute(f"SELECT result_id, source, meet_id, date FROM {table}")
        while True:
            rows = cur.fetchmany(BATCH)
            if not rows:
                break
            rid = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
            j = np.searchsorted(want, rid)
            hit = (j < want.size) & (want[np.minimum(j, want.size - 1)] == rid)
            for k in np.flatnonzero(hit):
                out.append(rows[k])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None)
    ap.add_argument("--pack", default=None, help="default <repo>/engine/data/packed_XC_TF.npz")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    pack = a.pack or os.path.join(REPO, "engine", "data", "packed_XC_TF.npz")
    with np.load(pack, allow_pickle=False) as z:
        rid_p = z["result_id"].astype(np.int64)
        sport_p = z["sport"].astype(np.int64)
        dist_p = z["dist_m"].astype(np.float64)
    print(f"[wx] pack {pack}: {rid_p.size:,} rows")
    cwd = os.getcwd()
    os.chdir(REPO)                         # backfill_normalize's relative paths
    try:
        import backfill_normalize as bn
        import normalize_distance as nd
        from database import getConn
    finally:
        os.chdir(cwd)
    n = rid_p.size
    temp = np.full(n, np.nan); wind = np.full(n, np.nan); wet = np.full(n, np.nan)
    logm = np.zeros(n)
    with getConn() as conn:
        for code, sp, table in ((0, "XC", "results"), (1, "TF", "results_tf")):
            on = np.flatnonzero(sport_p == code)
            if on.size == 0 or not bn._weatherEnabled(sp):
                print(f"[wx] {sp}: no rows or no weather artifact; skipped")
                continue
            t0 = time.time()
            with conn.cursor() as cur:
                os.chdir(REPO)
                try:
                    wx = bn.WeatherIndex.build(cur, sp)
                finally:
                    os.chdir(cwd)
            want = np.unique(rid_p[on])
            keys = streamKeys(conn, table, want)
            print(f"[wx] {sp}: index and {len(keys):,} rows' keys in {time.time() - t0:.0f}s",
                  flush=True)
            pos = {int(r): i for i, r in zip(on, rid_p[on])}
            cache = {}
            for rid, src, meet, d in keys:
                i = pos.get(int(rid))
                if i is None or d is None:
                    continue
                try:
                    dd = date.fromisoformat(str(d)[:10])
                except ValueError:
                    continue
                k = (src, meet, dd)
                if k not in cache:
                    cache[k] = wx.lookup(src, meet, dd)
                w, course = cache[k]
                if not w:
                    continue
                temp[i] = w.get("apparent_temp", np.nan)
                wind[i] = w.get("wind", np.nan)
                wet[i] = w.get("soil" if sp == "XC" else "precip", np.nan)
                r = nd._applyWeather(1.0, w, course, sp, float(dist_p[i]) or None)
                logm[i] = np.log(r) if r and r > 0 else 0.0
            got = np.isfinite(temp[on])
            print(f"[wx] {sp}: weather for {int(got.sum()):,} of {on.size:,} rows; multiplier "
                  f"median |ln| {100 * np.median(np.abs(logm[on][got])) if got.any() else 0:.2f}%",
                  flush=True)
        conn.rollback()
    np.savez(a.out, result_id=rid_p, sport=sport_p.astype(np.int8), temp=temp, wind=wind,
             wet=wet, log_mult=logm)
    print(f"[wx] wrote {a.out}; nothing written to the database")


if __name__ == "__main__":
    main()
