#!/usr/bin/env python3
"""
diag_weather_window.py -- which hours of the day explain how fields really
ran, and is the heat credit the right size? READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_weather_window.py                    # XC since 2022
    /srv/venv/bin/python scripts/diag_weather_window.py --since 2019-08-01 --hot 0.02

★ WHY (owner, 2026-10-23: Falfurrias, Sep 1 2018, credited +8.2% for a
  43 C feels-like averaged over 8am-12pm; "if conditions are that extreme
  it was obviously changed to a different time"). Start times are unknown,
  so the model reads one fixed window (the artifact's race_local_hours).
  Rather than guess a better window, this lets the races choose it.

★ THE MEASURE IS diag_weather_credit's. A race's gap is how its runners
  rated there against the same runners' ratings at OTHER races within
  +-30 days: fitness and ability cancel, what is left is how the race was
  rated. A rating already carries the credit the model applied, so what the
  race NEEDED is

        needed = applied credit - gap      (log time; + = the race was slow)

  and only the TEMPERATURE part moves with the window. For each window W
  the temperature credit is re-priced with the artifact's own curve on that
  window's feels-like (c_W), and

        needed - (applied credit - applied temperature credit)  ~  a + b c_W

  is fitted, runner-weighted. The window whose c_W explains it best (R^2)
  is where races really happened; b is how big the heat credit should be
  (1 = the curve is right, 0.6 = it credits heat 40% too much).

★ TWICE: across all races, and WITHIN COURSE (each course's days against
  its own mean, courses with two or more days) -- a hot southern venue's
  course number can carry part of its usual heat, which the across-course
  fit would read as the window's doing. Then the hot days alone (applied
  temperature credit past --hot), where the choice matters.

★ AND BY ABILITY (owner, 2026-10-24: "one race is really bad, and that's
  the issue" -- the average is right, Falfurrias is not). The heat credit is
  one percentage for the whole field, and the race-day term then corrects
  the field's AVERAGE; if heat costs slower runners more (longer in it),
  the front of a hot field is left over-credited and the back under. Within
  each race: every runner's gap against how fast they are relative to the
  field (their own level less the race's median level), slope by race heat
  band. Mild races give the baseline slope (regression to the mean puts one
  there too); a hot band steeper than mild is the front over-credited, and
  the difference is how much per 10% of ability.

! Nothing is written. A window or a scale is changed in the weather fit
  (engine/fit_weather_correction.py, RACE_LOCAL_HOURS_BY_SPORT) only after
  the owner has read this.
"""
import argparse
import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_weather_credit as W                                   # noqa: E402  (sets paths)
import normalize_distance as nd                                   # noqa: E402
from database import getConn                                      # noqa: E402

WINDOWS = ((6, 8), (6, 9), (7, 10), (8, 11), (8, 12), (9, 12), (10, 13), (11, 14), (13, 16))


def tempCredit(wx, course, sport, dist):
    """The temperature part of the model's credit for this weather, in log
    time, or None when the weather is not plausible."""
    if wx is None or not nd.isRaceWeatherPlausible(wx):
        return None

    def logc(w):
        f = nd._applyWeather(1.0, w, course, sport, dist)
        return -math.log(f) if f else 0.0
    return logc(wx) - logc({**wx, "apparent_temp": None})


def fit(y, x, w):
    """Weighted least squares y ~ a + b x -> (a, b, R^2, n). Pure."""
    y, x, w = np.asarray(y, float), np.asarray(x, float), np.asarray(w, float)
    ok = np.isfinite(y) & np.isfinite(x) & (w > 0)
    y, x, w = y[ok], x[ok], w[ok]
    if y.size < 10:
        return None
    mx, my = np.average(x, weights=w), np.average(y, weights=w)
    sxx = np.sum(w * (x - mx) ** 2)
    if sxx <= 0:
        return None
    b = np.sum(w * (x - mx) * (y - my)) / sxx
    a = my - b * mx
    res = y - a - b * x
    sst = np.sum(w * (y - my) ** 2)
    r2 = 1.0 - np.sum(w * res ** 2) / sst if sst > 0 else float("nan")
    return a, b, r2, int(y.size)


def withinCourse(vals, courses, w):
    """Each value less its course's weighted mean; NaN for courses seen once."""
    vals, w = np.asarray(vals, float), np.asarray(w, float)
    out = np.full(vals.size, np.nan)
    idx = {}
    for i, c in enumerate(courses):
        idx.setdefault(c, []).append(i)
    for ii in idx.values():
        if len(ii) < 2:
            continue
        ii = np.array(ii)
        ok = np.isfinite(vals[ii])
        if ok.sum() < 2:
            continue
        m = np.average(vals[ii][ok], weights=w[ii][ok])
        out[ii] = vals[ii] - m
    return out


def abilityByHeat(keys, applied_temp, race, has, gap_row, own, bands=(-1, 0.0, 0.01, 0.02, 0.04, 1)):
    """Within-race slope of a runner's gap on their relative ability, by the
    race's applied heat credit band (log). Prints the table."""
    heat = dict(zip(keys, applied_temp))
    rows = np.flatnonzero(has)
    rows = rows[np.isin(race[rows], np.fromiter(heat.keys(), dtype=np.int64))]
    by = {}
    for i in rows:
        by.setdefault(int(race[i]), []).append(i)
    print("\nBY ABILITY: within each race, a runner's gap vs how fast they are against the field")
    print(f"  {'heat credit band':<20} {'races':>7} {'runners':>9} {'slope':>8}   "
          f"(+ = the faster runners rate above their other races)")
    out = []
    for lo, hi in zip(bands, bands[1:]):
        xs, ys = [], []
        nr = 0
        for k, ii in by.items():
            h = heat.get(k)
            if h is None or not (lo <= h < hi) or len(ii) < 8:
                continue
            ii = np.array(ii)
            rel = own[ii] - np.median(own[ii])
            g = gap_row[ii] - np.mean(gap_row[ii])
            xs.append(rel)
            ys.append(g)
            nr += 1
        if not xs:
            continue
        x, y = np.concatenate(xs), np.concatenate(ys)
        sl = float(np.sum(x * y) / np.sum(x * x)) if np.sum(x * x) > 0 else float("nan")
        label = "no heat credit" if hi <= 0.0 else f"{100 * max(lo, 0):.0f}% to {100 * hi:.0f}%" if hi < 1 else f"{100 * lo:.0f}% and up"
        print(f"  {label:<20} {nr:>7,} {x.size:>9,} {sl:>8.3f}")
        out.append((label, nr, x.size, sl))
    if len(out) >= 2:
        base = out[0][3] if out[0][0] == "no heat credit" else out[1][3]
        top = out[-1]
        print(f"  hottest band minus the baseline: {top[3] - base:+.3f} -- per 10% faster than the "
              f"field, {100 * 0.1 * (top[3] - base):+.2f}% more rating in the hottest races")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--sport", choices=("XC", "TF"), default="XC")
    ap.add_argument("--since", default="2022-08-01")
    ap.add_argument("--window", type=int, default=30, help="days either side (the gap)")
    ap.add_argument("--hot", type=float, default=0.02,
                    help="the hot-day subset: applied temperature credit at least this (log)")
    a = ap.parse_args()
    sport = a.sport
    art = nd._weatherArtifactFor(sport)
    if art is None:
        print("no weather artifact: nothing to measure against")
        return 1
    hours = tuple(art.get("race_local_hours", (8, 12)))
    temp_agg = nd.weatherTempAgg(art)
    print(f"{sport}: the artifact reads hours {hours} ({temp_agg})")
    import backfill_normalize as bn
    with getConn() as conn:
        with conn.cursor() as cur:
            if sport == "XC":
                anet, tfrrs = bn._loadMeetCellsAnetXC(cur), bn._loadMeetCellsTfrrsXC(cur)
            else:
                anet, tfrrs = bn._loadMeetCellsAnetTF(cur), bn._loadMeetCellsTfrrsTF(cur)
        with conn.cursor(name="ww_rows") as cur:
            cur.itersize = 200_000
            pk, day, src, meet, iso, lr, dist = W._rows(cur, sport, a.since)
        print(f"  {pk.size:,} rated rows since {a.since}", flush=True)
        rkey = {}
        race = np.empty(pk.size, dtype=np.int64)
        for i in range(pk.size):
            race[i] = rkey.setdefault((src[i], int(meet[i]), iso[i]), len(rkey))
        s, c = W.ownOtherRaces(pk, day, race, lr, a.window)
        has = c > 0
        gap_row = np.where(has, lr - s / np.maximum(c, 1), np.nan)
        g_sum = np.bincount(race[has], weights=gap_row[has], minlength=len(rkey))
        g_n = np.bincount(race[has], minlength=len(rkey))
        d_med = {}
        for i in np.flatnonzero(has):
            d_med.setdefault(int(race[i]), []).append(dist[i])
        cells, meta = set(), {}
        for (s_, m_, iso_), k in rkey.items():
            if g_n[k] < 5:
                continue
            loc = (tfrrs if s_ == "tfrrs" else anet).get(m_)
            if loc is None:
                continue
            clat, clon, course = loc
            cells.add((clat, clon, iso_))
            meta[k] = (s_, m_, iso_, clat, clon, course)
        cells = sorted(cells)
        print(f"  {len(meta):,} races with 5+ runners who raced elsewhere within "
              f"{a.window} days, {len(cells):,} cell-days", flush=True)
        wx_by = {}
        for win in sorted(set(WINDOWS) | {hours}):
            with conn.cursor() as cur:
                wx_by[win] = W.raceWeather(cur, cells, win, temp_agg)
            conn.commit()                               # its temp table goes with it
            print(f"  weather for {win[0]:02d}-{win[1]:02d}h: {len(wx_by[win]):,} cell-days",
                  flush=True)
        conn.rollback()

    own = np.where(has, s / np.maximum(c, 1), np.nan)      # each row's own level (log)
    keys, n, need_temp, course_of, lat, month, ct = [], [], [], [], [], [], {w: [] for w in wx_by}
    applied_temp = []
    for k, (s_, m_, iso_, clat, clon, course) in meta.items():
        wx0 = wx_by[hours].get((clat, clon, iso_))
        if wx0 is None:
            continue
        dm = float(np.median(d_med.get(k, [5000.0]))) or None
        cr = W.credit(wx0, course, sport, dm)
        if cr is None:
            continue
        tot, _rain, _mud, temp = cr
        gap = float(g_sum[k] / g_n[k])
        # what the race needed, less the parts of the credit the window does not move
        need_temp.append((tot - gap) - (tot - temp))
        applied_temp.append(temp)
        n.append(int(g_n[k]))
        course_of.append(course or f"cell:{clat},{clon}:{s_}:{m_}")
        lat.append(float(clat))
        month.append(int(iso_[5:7]))
        for win in wx_by:
            ct[win].append(tempCredit(wx_by[win].get((clat, clon, iso_)), course, sport, dm))
        keys.append(k)
    n = np.array(n, float)
    need_temp = np.array(need_temp)
    applied_temp = np.array(applied_temp)
    lat, month = np.array(lat), np.array(month)
    print(f"\n{len(keys):,} races measured (runner-weighted)")

    def table(title, mask, within=False):
        print(f"\n{title}  ({int(mask.sum()):,} races)")
        print(f"  {'hours':<8} {'R^2':>7} {'scale b':>8} {'a':>8}    "
              f"(b: 1 = the heat curve is right; under 1 = it credits heat too much)")
        y = need_temp
        best = None
        for win in sorted(wx_by):
            x = np.array([v if v is not None else np.nan for v in ct[win]])
            yy, xx = (withinCourse(y, course_of, n), withinCourse(x, course_of, n)) if within else (y, x)
            f = fit(yy[mask], xx[mask], n[mask])
            if f is None:
                print(f"  {win[0]:02d}-{win[1]:02d}h   too few races")
                continue
            a_, b_, r2, _cnt = f
            mark = "  <- the model's window" if win == hours else ""
            print(f"  {win[0]:02d}-{win[1]:02d}h   {r2:7.3f} {b_:8.2f} {100 * a_:+7.2f}%{mark}")
            if best is None or r2 > best[1]:
                best = (win, r2, b_)
        if best:
            print(f"  best: {best[0][0]:02d}-{best[0][1]:02d}h (R^2 {best[1]:.3f}, scale {best[2]:.2f})")

    allm = np.ones(len(keys), bool)
    table("ALL RACES, across courses", allm)
    table("ALL RACES, within course (each course against its own days)", allm, within=True)
    hot = applied_temp >= a.hot
    table(f"HOT DAYS (applied heat credit >= {100 * a.hot:.0f}%), across courses", hot)
    table(f"HOT DAYS, within course", hot, within=True)
    south = lat < 35.0
    table("SOUTH (below 35 N), across courses", south)
    early = (month >= 8) & (month <= 9)
    table("AUGUST-SEPTEMBER, across courses", early)
    abilityByHeat(keys, applied_temp, race, has, gap_row, own)
    # the size of the hot-day credit as applied, against what the hot days needed
    if hot.any():
        w = n[hot]
        print(f"\nhot days: applied heat credit {100 * np.average(applied_temp[hot], weights=w):+.2f}% "
              f"on average; what they needed (temperature part) "
              f"{100 * np.average(need_temp[hot], weights=w):+.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
