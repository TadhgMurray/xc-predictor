#!/usr/bin/env python3
"""
diag_weather_credit.py -- are rain (and mud, heat, wind) races credited
enough? Measured on the ratings, not on the model.

    /srv/venv/bin/python scripts/diag_weather_credit.py                 # both sports, since 2025
    /srv/venv/bin/python scripts/diag_weather_credit.py --sport XC --since 2024-08-01
    /srv/venv/bin/python scripts/diag_weather_credit.py --sport TF --list 40

★ WHY (owner, 2026-09-26: "even just beyond the Celsius weather doesn't
  feel right. I see a ton of rain races not getting accurate benefit").
  The weather model can be read term by term (diag_row_weather.py), but that
  only says what the model GAVE a race, not what the race NEEDED. This
  measures the need: every rated row against the same runner's own ratings
  at OTHER races within +-30 days (same event on the track). Fitness cancels
  over a symmetric window, ability cancels because it is the same runner, so
  what is left is how the race itself was rated. A race whose runners all
  rate 2% under their own other races was under-credited by about 2%,
  whatever the reason. Bucketed by the race's weather, a rain bucket that
  sits below the dry bucket is rain not getting its benefit -- and the gap
  is how much it is short.

  Beside each bucket: what the weather model credited those races (the
  artifact in engine/data, split into the rain, mud and temperature terms),
  so the table says both "short by X" and "the model gave Y".

! THE RESIDUAL IS NET OF EVERYTHING. A rating already holds the weather
  term, the course difficulty and the race-day term. On a course raced once,
  the course difficulty IS that day (one race is all it has to go on), so a
  wet one-off course is credited through its difficulty, not its weather --
  which is why the tables split races on courses raced once from courses
  raced on several days.

READ-ONLY. Minutes: one pass over the rated rows since --since and one
weather_grid query for the race days.
"""
import argparse
import datetime as dt
import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "backfill")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_QUIET", "1")
# ! MEASURE WHAT WAS APPLIED. The stale-artifact guard would switch the
#   correction off in this process; the rows on the server were written with
#   whatever artifact is in engine/data, so that is the one to read.
os.environ.setdefault("XCP_WEATHER_ALLOW_STALE", "1")

import normalize_distance as nd                                   # noqa: E402
from database import getConn                                       # noqa: E402
from event_parse import distanceFromEventShort                     # noqa: E402

EPOCH = dt.date(1970, 1, 1)
_DATE_OK = r"^\d{4}-\d{2}-\d{2}"

BUCKETS = {
    "rain in the race window (mm)": ("precip", [0.0, 0.1, 1.0, 3.0, 8.0, 20.0]),
    "rain the day before + that morning (mm)": ("rain_before", [0.0, 1.0, 5.0, 15.0, 30.0]),
    "soil moisture (m3/m3)": ("soil", [0.0, 0.15, 0.25, 0.32, 0.38, 0.45]),
    "apparent temperature (C)": ("apparent_temp", [-50.0, 5.0, 12.0, 18.0, 24.0, 29.0]),
    "wind (m/s)": ("wind", [0.0, 2.0, 4.0, 6.0, 9.0]),
}


def _rows(cur, sport, since):
    """Rated rows: (person key, day number, source, meet_id, date iso,
    log rating, distance m). On the track the person key carries the event,
    so a 400 is compared with the same runner's 400s."""
    if sport == "XC":
        cur.execute(f"""
            SELECT r.person_id, r.date, r.source, r.meet_id, r.speed_rating, r.distance
            FROM   results r
            WHERE  r.speed_rating > 0 AND r.person_id IS NOT NULL
              AND  r.date ~ '{_DATE_OK}' AND r.date >= %s
        """, (since,))
    else:
        cur.execute(f"""
            SELECT r.person_id, r.date, r.source, r.meet_id, r.speed_rating, r.event_short
            FROM   results_tf r
            WHERE  r.speed_rating > 0 AND r.person_id IS NOT NULL
              AND  r.date ~ '{_DATE_OK}' AND r.date >= %s
        """, (since,))
    ev_code = {}
    pk, day, src, meet, iso, lr, dist = [], [], [], [], [], [], []
    for pid, d, s, m, rating, x in cur:
        iso_d = str(d)[:10]
        try:
            dd = dt.date.fromisoformat(iso_d)
        except ValueError:
            continue
        if sport == "XC":
            key = int(pid)
            dm = float(x) if x else 5000.0
        else:
            code = ev_code.setdefault(str(x), len(ev_code))
            key = int(pid) * 4096 + code
            try:
                dm = float(distanceFromEventShort(x)[0] or 0.0)
            except Exception:                                       # noqa: BLE001
                dm = 0.0
        pk.append(key)
        day.append((dd - EPOCH).days)
        src.append(s or "anet")
        meet.append(int(m))
        iso.append(iso_d)
        lr.append(math.log(float(rating)))
        dist.append(dm)
    return (np.array(pk, dtype=np.int64), np.array(day, dtype=np.int64), src,
            np.array(meet, dtype=np.int64), iso, np.array(lr), np.array(dist))


def ownOtherRaces(pk, day, race, lr, window):
    """Per row: the mean log rating of the same person key's rows at OTHER
    races within +-window days, and how many there were."""
    order = np.lexsort((day, pk))
    p, d, r, v = pk[order], day[order], race[order], lr[order]
    s = np.zeros(p.size)
    c = np.zeros(p.size, dtype=np.int64)
    k = 1
    while k < p.size:
        i = np.arange(p.size - k)
        j = i + k
        near = (p[i] == p[j]) & (d[j] - d[i] <= window)
        if not near.any():
            break
        ok = near & (r[i] != r[j])
        ii, jj = i[ok], j[ok]
        np.add.at(s, ii, v[jj])
        np.add.at(c, ii, 1)
        np.add.at(s, jj, v[ii])
        np.add.at(c, jj, 1)
        k += 1
    out_s = np.zeros(p.size)
    out_c = np.zeros(p.size, dtype=np.int64)
    out_s[order] = s
    out_c[order] = c
    return out_s, out_c


def raceWeather(cur, cells, hours, temp_agg):
    """{(clat, clon, iso): weather dict} for every (cell, day), the race window
    exactly as the backfill reads it, plus the rain of the day before and of
    that morning before the window opens."""
    lo, hi = hours
    cur.execute("""CREATE TEMP TABLE _wc (clat float8, clon float8, d date)
                   ON COMMIT DROP""")
    from psycopg2.extras import execute_values
    execute_values(cur, "INSERT INTO _wc VALUES %s",
                   [(a, b, c) for a, b, c in cells], page_size=5000)
    signed = "(CASE WHEN g.cell_lon > 180 THEN g.cell_lon - 360 ELSE g.cell_lon END)"
    local = f"mod(mod(g.hour + round({signed} / 15.0)::int, 24) + 24, 24)"
    win = f"g.date = w.d AND {local} BETWEEN {lo} AND {hi}"
    before = f"(g.date = w.d - 1 OR (g.date = w.d AND {local} < {lo}))"
    cur.execute(f"""
        SELECT w.clat, w.clon, w.d,
               {temp_agg.replace('(', '(g.', 1)} FILTER (WHERE {win}),
               avg(g.wind_speed_10m)  FILTER (WHERE {win}),
               sum(g.precipitation)   FILTER (WHERE {win}),
               avg(g.soil_moisture)   FILTER (WHERE {win}),
               avg(g.snow_depth)      FILTER (WHERE {win})
                 + coalesce(sum(g.snowfall) FILTER (WHERE {win}), 0),
               coalesce(sum(g.precipitation) FILTER (WHERE {before}), 0),
               count(*) FILTER (WHERE {win})
        FROM   _wc w
        JOIN   weather_grid g
               ON g.cell_lat = w.clat AND g.cell_lon = w.clon
              AND g.date BETWEEN w.d - 1 AND w.d
        GROUP  BY w.clat, w.clon, w.d
    """)
    out = {}
    for clat, clon, d, temp, wind, precip, soil, snow, rb, n in cur:
        if not n:
            continue
        out[(round(float(clat), 2), round(float(clon), 2), d.isoformat())] = dict(
            apparent_temp=temp, wind=wind, precip=precip, soil=soil, snow=snow,
            rain_before=float(rb or 0.0), doy=d.timetuple().tm_yday)
    return out


def credit(wx, course, sport, dist):
    """(total, rain, mud, temperature) credit in log time: positive = the
    time was treated as faster than run, i.e. the race was helped."""
    if not nd.isRaceWeatherPlausible(wx):
        return None

    def logc(w):
        f = nd._applyWeather(1.0, w, course, sport, dist)
        return -math.log(f) if f else 0.0
    tot = logc(wx)
    no_rain = logc({**wx, "precip": None})
    no_mud = logc({**wx, "soil": None})
    no_temp = logc({**wx, "apparent_temp": None})
    return tot, tot - no_rain, tot - no_mud, tot - no_temp


def _pct(x):
    return f"{100.0 * math.expm1(x):+6.2f}%"


def report(sport, races, list_n):
    print(f"\n{'=' * 78}\n{sport}: {len(races):,} races with weather and "
          f"at least 3 runners who have other races within the window\n"
          f"{'=' * 78}")
    print("  gap   = how the race's runners rated here against their own other "
          "races\n          (negative = rated LOWER here: the race was short-"
          "changed)\n  vs dry = the same, minus the driest bucket's gap\n"
          "  model = what the weather correction credited (rain / mud / temp "
          "terms)\n  rows weighted by runner count; 'once' = the course has one "
          "race day in this data")
    for title, (feat, edges) in BUCKETS.items():
        vals = np.array([r["wx"].get(feat) if r["wx"].get(feat) is not None
                         else np.nan for r in races], dtype=np.float64)
        gap = np.array([r["gap"] for r in races])
        n = np.array([r["n"] for r in races], dtype=np.float64)
        once = np.array([r["once"] for r in races])
        cr = np.array([r["credit"] for r in races])            # (k, 4)
        print(f"\n  {title}")
        print(f"    {'bucket':<14}{'races':>7}{'gap':>9}{'vs dry':>9}"
              f"{'model':>9}{'rain':>8}{'mud':>8}{'temp':>8}"
              f"{'once: races':>13}{'gap':>9}")
        base = None
        for b in range(len(edges)):
            lo = edges[b]
            hi = edges[b + 1] if b + 1 < len(edges) else np.inf
            m = np.isfinite(vals) & (vals >= lo) & (vals < hi)
            if m.sum() < 5:
                continue
            g = float(np.average(gap[m], weights=n[m]))
            if base is None:
                base = g
            c = np.average(cr[m], axis=0, weights=n[m])
            mo = m & once
            go = (f"{_pct(float(np.average(gap[mo], weights=n[mo])))}"
                  if mo.sum() >= 5 else "      -")
            lab = f"{lo:g}-{hi:g}" if np.isfinite(hi) else f"{lo:g}+"
            print(f"    {lab:<14}{int(m.sum()):>7,}{_pct(g):>9}{_pct(g - base):>9}"
                  f"{_pct(c[0]):>9}{_pct(c[1]):>8}{_pct(c[2]):>8}{_pct(c[3]):>8}"
                  f"{int(mo.sum()):>13,}{go:>9}")
    if list_n:
        wet = sorted(races, key=lambda r: -((r["wx"].get("precip") or 0.0)
                                            + 0.5 * r["wx"].get("rain_before", 0.0)))
        print(f"\n  The {list_n} wettest races (window rain, then the day before):")
        print(f"    {'date':<11}{'meet':>10} {'src':<6}{'rain':>6}{'before':>8}"
              f"{'soil':>6}{'temp':>6}{'runners':>8}{'gap':>9}{'model':>9}  course")
        for r in wet[:list_n]:
            w = r["wx"]
            print(f"    {r['iso']:<11}{r['meet']:>10} {r['src']:<6}"
                  f"{(w.get('precip') or 0):>6.1f}{w.get('rain_before', 0):>8.1f}"
                  f"{(w.get('soil') or 0):>6.2f}{(w.get('apparent_temp') or 0):>6.0f}"
                  f"{r['n']:>8}{_pct(r['gap']):>9}{_pct(r['credit'][0]):>9}  "
                  f"{(r['course'] or '')[:30]}{' (once)' if r['once'] else ''}")


def runSport(conn, sport, since, window, list_n):
    art = nd._weatherArtifactFor(sport)
    if art is None:
        print(f"\n{sport}: no weather artifact loaded -- nothing to compare against")
        return
    hours = tuple(art.get("race_local_hours", (8, 12)))
    temp_agg = nd.weatherTempAgg(art)
    print(f"\n{sport}: artifact reference {art.get('reference')}, hours {hours}, "
          f"betas {art.get('betas')}")
    # the backfill's own meet -> grid cell maps, so a race sits in the cell
    # its rows were corrected in (imported here: it needs the server's
    # corrections.py, which is not in git)
    import backfill_normalize as bn
    with conn.cursor() as cur:
        if sport == "XC":
            anet, tfrrs = bn._loadMeetCellsAnetXC(cur), bn._loadMeetCellsTfrrsXC(cur)
        else:
            anet, tfrrs = bn._loadMeetCellsAnetTF(cur), bn._loadMeetCellsTfrrsTF(cur)
    with conn.cursor(name="wc_rows") as cur:
        cur.itersize = 200_000
        pk, day, src, meet, iso, lr, dist = _rows(cur, sport, since)
    print(f"  {pk.size:,} rated rows since {since}")
    if not pk.size:
        return
    # a race = (source, meet, day)
    rkey = {}
    race = np.empty(pk.size, dtype=np.int64)
    for i in range(pk.size):
        race[i] = rkey.setdefault((src[i], int(meet[i]), iso[i]), len(rkey))
    s, c = ownOtherRaces(pk, day, race, lr, window)
    has = c > 0
    gap_row = np.where(has, lr - s / np.maximum(c, 1), np.nan)
    n_race = len(rkey)
    g_sum = np.bincount(race[has], weights=gap_row[has], minlength=n_race)
    g_n = np.bincount(race[has], minlength=n_race)
    d_med = {}
    for i in np.flatnonzero(has):
        d_med.setdefault(int(race[i]), []).append(dist[i])

    cells, meta = set(), {}
    course_days = {}
    for (s_, m_, iso_), k in rkey.items():
        if g_n[k] < 3:
            continue
        loc = (tfrrs if s_ == "tfrrs" else anet).get(m_)
        if loc is None:
            continue
        clat, clon, course = loc
        cells.add((clat, clon, iso_))
        meta[k] = (s_, m_, iso_, clat, clon, course)
        ck = course or f"cell:{clat},{clon}:{s_}:{m_}"
        course_days.setdefault(ck, set()).add(iso_)
    with conn.cursor() as cur:
        wx_all = raceWeather(cur, sorted(cells), hours, temp_agg)
    races = []
    for k, (s_, m_, iso_, clat, clon, course) in meta.items():
        wx = wx_all.get((clat, clon, iso_))
        if wx is None:
            continue
        dm = float(np.median(d_med.get(k, [5000.0]))) or None
        cr = credit(wx, course, sport, dm)
        if cr is None:
            continue
        ck = course or f"cell:{clat},{clon}:{s_}:{m_}"
        races.append(dict(src=s_, meet=m_, iso=iso_, course=course, wx=wx,
                          n=int(g_n[k]), gap=float(g_sum[k] / g_n[k]),
                          credit=cr, once=len(course_days.get(ck, ())) == 1))
    report(sport, races, list_n)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sport", choices=("XC", "TF", "both"), default="both")
    ap.add_argument("--since", default="2025-01-01", help="first race date (ISO)")
    ap.add_argument("--window", type=int, default=30,
                    help="days either side for the runner's own other races")
    ap.add_argument("--list", type=int, default=25, help="wettest races to list")
    a = ap.parse_args()
    sports = ("XC", "TF") if a.sport == "both" else (a.sport,)
    with getConn() as conn:
        for sp in sports:
            runSport(conn, sp, a.since, a.window, a.list)
        conn.rollback()


if __name__ == "__main__":
    main()
