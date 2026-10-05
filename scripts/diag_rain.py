#!/usr/bin/env python3
"""
diag_rain.py -- does rain still read as a slow course day AFTER the weather
correction? READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_rain.py              # XC and track
    /srv/venv/bin/python scripts/diag_rain.py --sport XC

★ WHY (owner, 2026-10-05: "can we pls fix rain, not doing enough"). The
  weather correction is fitted, so "not enough" is a measurable claim: the
  race-day term (race_day_effect.day_effect, the solve's leave-out reading
  of how fast a course ran that day, everyone on it alike) is what is LEFT
  after the weather correction. If rain were fully credited, a rainy day's
  leftover would sit at zero like a dry day's. A leftover that rises with
  the rain is the part the correction misses, and its size is the fix.

  Three readings per race day, from the same hourly grid the fit uses:
    window rain   precipitation summed over the race window (XC 8am-noon
                  local, track 9am-8pm) -- the feature the fit has
    rain before   the 24 hours BEFORE the window (the night before): the fit
                  sees it only through soil moisture
    soil          mean soil moisture in the window (the fit's mud spline)
  Each is binned; per bin the median leftover (in % of time, + = slow day)
  and the weighted mean, then the weighted slope per unit. A monotone rise
  in "rain before" with a flat "window rain" says the night-before rain is
  what is missing -- a feature the fit does not have.

! The leftover is a residual of the published model: it carries everything
  the model misses (course changes, a wrong distance, a fast field), so read
  the RISE across bins, not one bin's level.
"""
import argparse
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from database import getConn                                   # noqa: E402

GRID = 0.25
WINDOW = {"XC": (8, 12), "TF": (9, 20)}
BINS = {
    "window rain (mm)": (0.0, 0.1, 1.0, 3.0, 8.0, 1e9),
    "rain before (mm)": (0.0, 0.1, 2.0, 8.0, 20.0, 1e9),
    "soil (m3/m3)":     (0.0, 0.15, 0.25, 0.32, 0.40, 1e9),
}


def _snap(col):
    return f"round(({col})::float8 / {GRID}) * {GRID}"


def _lonSnap(col):
    return _snap(f"CASE WHEN ({col}) < 0 THEN ({col})::float8 + 360 ELSE ({col})::float8 END")


# the race days with a place on the grid: XC by canonical course, track by the
# venue id in its key ('TF:loc:<id>:out')
_DAYS = {
    "XC": f"""
        SELECT d.race_date::date AS date, d.day_effect, d.n_rows,
               {_snap('c.lat')} AS cell_lat, {_lonSnap('c.lon')} AS cell_lon
        FROM   race_day_effect d
        JOIN   (SELECT canonical_id, avg(gps_lat) AS lat, avg(gps_long) AS lon
                FROM course_canonical WHERE gps_lat IS NOT NULL GROUP BY 1) c
               ON c.canonical_id = d.canonical_id
        WHERE  d.course_name NOT LIKE 'TF:%%'""",
    "TF": f"""
        SELECT d.race_date::date AS date, d.day_effect, d.n_rows,
               {_snap('l.lat')} AS cell_lat, {_lonSnap('l.lon')} AS cell_lon
        FROM   race_day_effect d
        JOIN   (SELECT location_id, avg(gps_lat) AS lat, avg(gps_long) AS lon
                FROM meets_tf_meta WHERE gps_lat IS NOT NULL AND location_id IS NOT NULL
                GROUP BY 1) l
               ON d.course_name = 'TF:loc:' || l.location_id || ':out'""",
}


def load(cur, sport):
    lo, hi = WINDOW[sport]
    cur.execute(f"CREATE TEMP TABLE rd AS {_DAYS[sport]}")
    cur.execute("SELECT count(*) FROM rd")
    n = cur.fetchone()[0]
    if not n:
        return None
    cur.execute("CREATE TEMP TABLE rd_cells AS SELECT DISTINCT cell_lat, cell_lon, date, "
                "round((CASE WHEN cell_lon > 180 THEN cell_lon - 360 ELSE cell_lon END) / 15.0)::int AS off "
                "FROM rd")
    cur.execute("ANALYZE rd_cells")
    # local time = UTC + round(lon / 15) h, the fit's own clock; the grid's
    # rows for the race day and the day before, placed on that clock
    cur.execute(f"""
        CREATE TEMP TABLE rd_wx AS
        SELECT c.cell_lat, c.cell_lon, c.date,
               sum(g.precipitation) FILTER (WHERE t.lh >= {lo} AND t.lh <= {hi}
                                             AND t.ld = c.date)              AS rain_window,
               sum(g.precipitation) FILTER (WHERE (t.ld = c.date AND t.lh < {lo})
                                             OR (t.ld = c.date - 1 AND t.lh >= {lo})) AS rain_before,
               avg(g.soil_moisture) FILTER (WHERE t.lh >= {lo} AND t.lh <= {hi}
                                             AND t.ld = c.date)              AS soil
        FROM   rd_cells c
        JOIN   weather_grid g
               ON g.cell_lat = c.cell_lat AND g.cell_lon = c.cell_lon
              AND g.date BETWEEN c.date - 1 AND c.date + 1
        CROSS JOIN LATERAL (
               SELECT (g.date + make_interval(hours => g.hour + c.off))::date AS ld,
                      extract(hour FROM g.date + make_interval(hours => g.hour + c.off))::int AS lh
        ) t
        GROUP  BY 1, 2, 3""")
    cur.execute("""
        SELECT r.day_effect, r.n_rows, w.rain_window, w.rain_before, w.soil
        FROM   rd r JOIN rd_wx w USING (cell_lat, cell_lon, date)""")
    rows = cur.fetchall()
    cur.execute("DROP TABLE rd, rd_cells, rd_wx")
    return rows


def report(sport, rows):
    a = np.array([[np.nan if v is None else float(v) for v in r] for r in rows])
    eff = 100.0 * np.expm1(a[:, 0])               # leftover, % of time, + = slow
    w = a[:, 1]
    feats = {"window rain (mm)": a[:, 2], "rain before (mm)": a[:, 3], "soil (m3/m3)": a[:, 4]}
    print(f"\n== {sport}: {len(rows):,} race days with weather "
          f"(leftover = the published race-day term, % of time, + = slow day)")
    for name, x in feats.items():
        ok = np.isfinite(x) & np.isfinite(eff)
        edges = BINS[name]
        print(f"\n  {name}")
        print(f"    {'bin':<16}{'days':>8}{'median':>9}{'w. mean':>9}")
        for lo, hi in zip(edges, edges[1:]):
            m = ok & (x >= lo) & (x < hi)
            if m.sum() < 20:
                continue
            label = f"{lo:g}-{hi:g}" if hi < 1e8 else f"{lo:g}+"
            print(f"    {label:<16}{int(m.sum()):>8,}{np.median(eff[m]):>+8.2f}%"
                  f"{np.average(eff[m], weights=w[m]):>+8.2f}%")
        if ok.sum() > 50:
            xm = np.average(x[ok], weights=w[ok])
            ym = np.average(eff[ok], weights=w[ok])
            slope = (np.sum(w[ok] * (x[ok] - xm) * (eff[ok] - ym))
                     / np.sum(w[ok] * (x[ok] - xm) ** 2))
            unit = "per 0.1" if name.startswith("soil") else "per mm"
            scale = 0.1 if name.startswith("soil") else 1.0
            print(f"    slope: {slope * scale:+.3f}% {unit} (runner-weighted)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sport", choices=("XC", "TF"), default=None)
    a = ap.parse_args()
    t0 = time.time()
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("SET statement_timeout = 0")
        cur.execute("SET work_mem = '512MB'")
        for sport in ((a.sport,) if a.sport else ("XC", "TF")):
            rows = load(cur, sport)
            if not rows:
                print(f"\n== {sport}: no race days on the grid (race_day_effect has none?)")
                continue
            report(sport, rows)
        conn.rollback()
    print(f"\n[rain] read-only; {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
