#!/usr/bin/env python3
"""
diag_day_terms.py -- two questions about the race-day term. READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_day_terms.py --season-trend          # all XC
    /srv/venv/bin/python scripts/diag_day_terms.py --season-trend --year 2021
    /srv/venv/bin/python scripts/diag_day_terms.py --person 29603086       # missing dropdowns

1. DOES PEAKING LEAK INTO THE DAY TERM? (owner, 2026-10-05: NESCAC 2021,
   rain and 85% humidity, day +2.7% slow while nearly the whole field ran
   3-8% above its season level.) The solve reads a day against each runner's
   SEASON ability, which is one number for the season. A championship field
   is peaking, so a slow day and a fast field cancel and the term sees the
   net. If that is happening across the corpus, day terms drift FAST as the
   season goes on, by roughly the season's improvement. --season-trend
   prints the runner-weighted mean day term by week of the season (week 0 =
   the season's first week with races), per year, so the drift -- or its
   absence -- is a number, not a guess. A flat line says the day terms are
   clean of form and the NESCAC gap is the runners; a falling line says the
   ability model needs a within-season trend before the day term can be
   trusted at championships.

2. WHY A RACE HAS NO RACE-DAY DROPDOWN. The athlete page joins
   race_day_effect on (race_date = results.date, canonical_id, distance_m),
   or by the championship cell's name. --person lists that athlete's XC
   races that find no row, and the race_day_effect rows nearest to each
   (same venue any distance, +-7 days; same date any venue nearby by name),
   so the mismatch -- the date, the distance cell or the venue -- is visible.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from database import getConn                                   # noqa: E402

# the athlete page's own resolution of a result to its course cell
# (app.get_races' XC half, trimmed to the keys)
_RACES = """
SELECT r.result_id, r.date, r.meet_id, r.div_id, r.source,
       COALESCE(NULLIF(btrim(m.meet_name), ''), '?') AS meet,
       m.course_name, m.distance, cc.canonical_id, cd.distance_m AS cell_m
FROM   results r
LEFT JOIN meets m ON m.div_id = r.div_id AND m.meet_id = r.meet_id AND m.source = r.source
LEFT JOIN course_canonical cc
       ON cc.course_name = m.course_name
      AND round(cc.gps_lat::numeric, 5) = round(m.gps_lat::numeric, 5)
      AND round(cc.gps_long::numeric, 5) = round(m.gps_long::numeric, 5)
LEFT JOIN course_difficulties cd
       ON cd.canonical_id = cc.canonical_id
      AND cd.distance_m = (round(m.distance / 100.0) * 100)::int
WHERE  r.person_id = %s AND r.time_seconds IS NOT NULL
ORDER  BY r.date DESC
"""


def person(cur, pid):
    cur.execute(_RACES, (pid,))
    rows = cur.fetchall()
    print(f"\n== person {pid}: {len(rows)} XC results")
    n_miss = 0
    for (rid, date, mid, div, src, meet, course, dist, cid, cell) in rows:
        cur.execute("""SELECT 1 FROM race_day_effect
                       WHERE race_date::text = %s AND canonical_id = %s AND distance_m = %s""",
                    (date, cid, cell))
        if cur.fetchone():
            continue
        n_miss += 1
        print(f"\n  NO DAY ROW  {date}  {meet}  (meet {mid}, div {div}, {src})")
        print(f"    course {course!r}  distance {dist}  -> canonical {cid}, cell {cell}")
        if cid is None:
            print("    ! no course_canonical match: the page cannot find the venue at all")
            continue
        if cell is None:
            print("    ! no course_difficulties cell at this distance")
        cur.execute("""SELECT race_date, distance_m, round(100 * day_effect::numeric, 2), n_rows
                       FROM race_day_effect
                       WHERE canonical_id = %s
                         AND race_date BETWEEN %s::date - 7 AND %s::date + 7
                       ORDER BY abs(race_date - %s::date), distance_m""",
                    (cid, date, date, date))
        near = cur.fetchall()
        if near:
            for d, m, u, n in near[:6]:
                print(f"    same venue: {d}  {m} m  day {u:+}%  n={n}")
        else:
            print("    same venue: no day row within 7 days -- the solve wrote none "
                  "(race dropped, below the row floor, or a cell the solve did not have)")
        cur.execute("""SELECT course_name, canonical_id, distance_m, n_rows
                       FROM race_day_effect WHERE race_date::text = %s
                         AND (course_name ILIKE %s OR course_name ILIKE %s)
                       LIMIT 5""",
                    (date, f"%{(course or '')[:12]}%", f"%{(meet or '')[:12]}%"))
        for c, k, m, n in cur.fetchall():
            print(f"    same date, similar name: {c!r} canonical {k} {m} m n={n}")
    print(f"\n  {n_miss} of {len(rows)} races have no day row the page can join")


def seasonTrend(cur, year=None):
    """Runner-weighted mean XC day term by week of season, per season."""
    where = "WHERE course_name NOT LIKE 'TF:%%'"
    params = []
    if year:
        where += " AND extract(year FROM race_date) = %s"
        params.append(year)
    cur.execute(f"""
        WITH d AS (
            SELECT extract(year FROM race_date)::int AS season, race_date,
                   day_effect, n_rows
            FROM race_day_effect {where}
              AND extract(month FROM race_date) BETWEEN 8 AND 12),
        s AS (SELECT season, min(race_date) AS first FROM d GROUP BY season)
        SELECT d.season, ((d.race_date - s.first) / 7)::int AS week,
               count(*) AS races, sum(n_rows) AS runners,
               100 * sum(day_effect * n_rows) / nullif(sum(n_rows), 0) AS mean_u
        FROM d JOIN s USING (season)
        GROUP BY 1, 2 ORDER BY 1, 2""", params)
    rows = cur.fetchall()
    by = {}
    for season, week, races, runners, mean_u in rows:
        by.setdefault(season, []).append((week, races, runners, float(mean_u or 0)))
    print("\n== XC day term by week of season (runner-weighted, % of time, + = slow)")
    print("   A line falling through the season = form leaking into the day term.")
    for season in sorted(by):
        cells = [f"w{w}:{u:+.1f}" for w, r, n, u in by[season] if n and n >= 500]
        print(f"  {season}: " + "  ".join(cells))
    # the pooled slope: weeks 0-12, runner-weighted
    import numpy as np
    pts = [(w, u, n) for s in by.values() for w, r, n, u in s if n and w <= 12]
    if pts:
        w = np.array([p[0] for p in pts], float)
        u = np.array([p[1] for p in pts], float)
        n = np.array([p[2] for p in pts], float)
        wm, um = np.average(w, weights=n), np.average(u, weights=n)
        slope = np.sum(n * (w - wm) * (u - um)) / np.sum(n * (w - wm) ** 2)
        print(f"\n  pooled slope over weeks 0-12: {slope:+.3f}% per week "
              f"({12 * slope:+.2f}% from the first week to week 12)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--person", type=int)
    ap.add_argument("--season-trend", action="store_true")
    ap.add_argument("--year", type=int)
    a = ap.parse_args()
    if not (a.person or a.season_trend):
        ap.error("give --person and/or --season-trend")
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("SET statement_timeout = 0")
        if a.person:
            person(cur, a.person)
        if a.season_trend:
            seasonTrend(cur, a.year)
        conn.rollback()


if __name__ == "__main__":
    main()
