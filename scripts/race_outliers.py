#!/usr/bin/env python3
"""
race_outliers.py -- races that ran far off the rest of their meet's day.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/race_outliers.py                    # last 120 days, 3%+ off, 15+ runners
    $P scripts/race_outliers.py --days 400 --min 4 --show 60

★ WHY (owner, 2026-11-04, /race/xc/273894/1152283: "This is so fake").
  Mt. SAC 2026, Race 53 (D1 & 2 Team Sweeps Green Boys): nearly every runner
  rated a career best, the winner 147.5 against 131-144 everywhere else this
  season. The solve's race-day term is one number per COURSE-DAY: Mt. SAC's
  Friday (5,205 runners) read "slow 3.7%", and Race 53 -- whose own field ran
  well FASTER than its runners' level -- was credited that day's slowness
  too. A race on another loop of the same venue (a rain course, a short
  course) is invisible to a term that averages the whole day.

★ THE READING (provisional_day's own): each runner's stored rating against
  their level coming in (median rating over the year before, same pool),
  in log terms; a race's reading is its field's median; its meet-day's is
  the median over every runner that day. A race whose reading sits --min
  percent or more from its meet-day's is listed. READ-ONLY.
"""
import argparse
import math
import os
import statistics
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MIN_FIELD = 15


def readings(runners):
    """runners: [(meet_day, race, log(rating/level))] -> (by race, by meet-day):
    {key: (median, n)}. Pure."""
    races, days = defaultdict(list), defaultdict(list)
    for day, race, x in runners:
        races[(day, race)].append(x)
        days[day].append(x)
    med = lambda d: {k: (statistics.median(v), len(v)) for k, v in d.items()}  # noqa: E731
    return med(races), med(days)


def outliers(runners, min_pct=3.0, min_field=MIN_FIELD):
    """[(meet_day, race, race reading - day reading, n)] at min_pct+ apart,
    biggest first; the day must hold another race's runners too. Pure."""
    by_race, by_day = readings(runners)
    n_races = defaultdict(int)
    for (day, _race) in by_race:
        n_races[day] += 1
    out = []
    for (day, race), (m, n) in by_race.items():
        if n < min_field or n_races[day] < 2:
            continue
        dev = m - by_day[day][0]
        if abs(dev) * 100 >= min_pct:
            out.append((day, race, dev, n))
    return sorted(out, key=lambda x: -abs(x[2]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--min", type=float, default=3.0, help="percent off the meet-day")
    ap.add_argument("--show", type=int, default=40)
    a = ap.parse_args(argv)
    import provisional_day as PD
    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(PD._ROWS_SQL, {"window": a.days})
            rows = cur.fetchall()
            print(f"[outliers] {len(rows):,} rated rows in the last {a.days} days", flush=True)
            # result_id, person_id, source, meet_id, div_id, nt, pool, rating_pool, day, dist, rating
            wanted = {(int(r[1]), r[8], r[6]) for r in rows if r[1] is not None and r[6]}
            levels = PD._levels(cur, wanted)
        conn.rollback()
    runners, seen = [], set()
    for rid, pid, src, meet, div, _nt, pool, _rp, day, _dist, rating in rows:
        if pid is None or not rating:
            continue
        lv = levels.get((int(pid), day, pool))
        lv = lv and (lv[1] or lv[0])
        if not lv or (pid, day) in seen:
            continue
        seen.add((pid, day))                   # a runner in both feeds' copies, once
        runners.append(((int(meet), day), (src, int(meet), int(div)),
                        math.log(float(rating) / float(lv))))
    got = outliers(runners, a.min)
    by_race, _ = readings(runners)
    big = sum(1 for (_d, _r), (_m, n) in by_race.items() if n >= MIN_FIELD)
    fast = sum(1 for x in got if x[2] > 0)
    print(f"[outliers] {big:,} races with {MIN_FIELD}+ runners with a level; {len(got):,} "
          f"sit {a.min:g}%+ off their meet-day ({fast:,} rated high, {len(got) - fast:,} low)")
    for (meet, day), (src, _m, div), dev, n in got[:a.show]:
        print(f"  {dev * 100:+5.1f}%  {day}  /race/xc/{meet}/{div}  ({src}, {n} runners)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
