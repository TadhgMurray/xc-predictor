#!/usr/bin/env python3
"""
diag_mixed_pool_races.py -- in races that mix high-school (or middle-school)
runners with college/pro runners, does one side rate high against its own
form? READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_mixed_pool_races.py
    /srv/venv/bin/python scripts/diag_mixed_pool_races.py --since 2015 --show 15

★ WHY (owner, 2026-10-19, World XC 2026 u20 men, 8000 m). Every high
  schooler rated about 5 HS-scale points above the college/pro runner with
  the same time, and above their own season: Jackson Spencer 141.8-142.9 in
  every HS race, 146.5 here; the college freshman beside him read his usual
  118.4 -> 118.7. Suspect: the high-school pool's distance conversion at a
  distance high schoolers almost never race, extrapolated from 5K.

★ NO SCALE CONVERSION. Each runner is measured against their OWN season in
  their OWN pool: resid = ln(race rating) - ln(season mean rating without
  this race). Within one race the course and the day are shared, so the
  mean resid of the school-age side minus the college/pro side is the
  pool conversion's error at that distance (0 = the pools agree; +0.03 =
  the school-age side rates 3% high). Medians over races, by distance.
"""
import argparse
import math
import os
import sys
import time
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

# ! ONE PASS OVER THE TABLE (the joins hung before on this server, see
#   diag_course_credit): each mixed race comes back with its runners packed
#   in arrays, and the seasons are read by person id afterwards.
_RACES_SQL = """
    SELECT meet_id, div_id, round(distance)::int AS dist, min(race_date)::date,
           array_agg(person_id), array_agg(split_part(pool, '|', 1)),
           array_agg(year), array_agg(speed_rating)
    FROM   ranking_results
    -- ! NO result_id > 0 (2026-10-20, first run: 591 races, not one at
    --   8k). tfrrs rows carry NEGATIVE result ids, and most college and
    --   pro runners are tfrrs rows -- the filter kept only athletic.net's
    --   college runners and lost the races this check exists for.
    WHERE  sport = 'XC' AND speed_rating > 0
      AND  person_id IS NOT NULL AND year >= %(since)s
    GROUP  BY meet_id, div_id, round(distance)
    HAVING count(*) FILTER (WHERE pool LIKE 'college%%' OR pool LIKE 'pro%%') >= %(min_adult)s
       AND count(*) FILTER (WHERE pool LIKE 'hs%%' OR pool LIKE 'ms%%') >= %(min_young)s
"""
_SEASON_SQL = """
    SELECT person_id, split_part(pool, '|', 1), year, mean_rating, n_races
    FROM   athlete_season
    WHERE  sport = 'XC' AND person_id = ANY(%s) AND mean_rating > 0
"""


def level(pool):
    return (pool or "").split("_", 1)[0]


def leaveOneOut(mean, n, r):
    """The season mean without this race; None when nothing is left."""
    if not mean or not n or n < 3:
        return None
    rest = (float(mean) * n - float(r)) / (n - 1)
    return rest if rest > 0 else None


def raceGap(rows, seasons):
    """{young level: (gap, n_young, n_adult)} for one race. rows: (person,
    pool, year, rating). gap = mean resid of that level - mean resid of the
    college/pro runners. Pure."""
    by = defaultdict(list)
    for pid, pool, yr, r in rows:
        s = seasons.get((pid, pool, yr))
        if not s:
            continue
        base = leaveOneOut(s[0], s[1], r)
        if not base:
            continue
        lv = level(pool)
        key = "adult" if lv in ("college", "pro") else lv
        by[key].append(math.log(float(r) / base))
    adult = by.get("adult") or []
    if len(adult) < 3:
        return {}
    a = sum(adult) / len(adult)
    return {lv: (sum(v) / len(v) - a, len(v), len(adult))
            for lv, v in by.items() if lv != "adult" and len(v) >= 2}


def bucket(d):
    for lo, hi, lab in ((0, 4500, "<4.5k"), (4500, 5500, "5k"), (5500, 6500, "6k"),
                        (6500, 7500, "7k"), (7500, 8500, "8k"), (8500, 99999, "10k")):
        if lo <= d < hi:
            return lab
    return "?"


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else (xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2]))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=int, default=2012)
    ap.add_argument("--min-adult", type=int, default=3)
    ap.add_argument("--min-young", type=int, default=2)
    ap.add_argument("--show", type=int, default=10, help="biggest-gap races to list")
    a = ap.parse_args()
    from database import getConn
    t0 = time.time()
    say = lambda m: print(f"{m}  ({time.time() - t0:.0f}s)", flush=True)
    print(f"reading mixed XC races since {a.since} (one pass over ranking_results) ...",
          flush=True)
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '20min'")
        cur.execute(_RACES_SQL, {"since": a.since, "min_adult": a.min_adult,
                                 "min_young": a.min_young})
        races = cur.fetchall()
        say(f"  {len(races):,} races mix school-age and college/pro runners")
        people = sorted({p for r in races for p in r[4]})
        seasons = {}
        for i in range(0, len(people), 20000):
            cur.execute(_SEASON_SQL, (people[i:i + 20000],))
            for pid, pool, yr, mean, n in cur.fetchall():
                seasons[(pid, pool, yr)] = (mean, n)
        say(f"  {len(seasons):,} runner-seasons")
        conn.rollback()

    cells = defaultdict(list)            # (level, bucket) -> [(gap, race)]
    for meet, div, dist, day, pids, pools, yrs, rs in races:
        for lv, (gap, ny, na) in raceGap(list(zip(pids, pools, yrs, rs)), seasons).items():
            cells[(lv, bucket(dist or 0))].append((gap, (meet, div, dist, day, ny, na)))

    print("\ngap = school-age runners' rating vs their own season, minus the college/pro "
          "runners' in the same race\n(+0.030 = the school-age side rates 3% high at that "
          "distance; 0 = the pools agree)\n")
    print(f"  {'level':<6}{'dist':<7}{'races':>7}{'median gap':>12}{'mean':>9}{'± se':>8}")
    order = ["<4.5k", "5k", "6k", "7k", "8k", "10k", "?"]
    for lv in sorted({k[0] for k in cells}):
        for b in order:
            xs = [g for g, _r in cells.get((lv, b), [])]
            if len(xs) < 3:
                continue
            m = sum(xs) / len(xs)
            sd = math.sqrt(sum((x - m) ** 2 for x in xs) / max(len(xs) - 1, 1))
            print(f"  {lv:<6}{b:<7}{len(xs):>7,}{median(xs):>+12.4f}{m:>+9.4f}"
                  f"{sd / math.sqrt(len(xs)):>8.4f}")
    big = sorted(((g, r, lv) for (lv, _b), v in cells.items() for g, r in v),
                 key=lambda t: -abs(t[0]))[:a.show]
    if big:
        print(f"\n  the {len(big)} biggest gaps (race page: /race/xc/<meet>/<div>):")
        for g, (meet, div, dist, day, ny, na), lv in big:
            print(f"    {g:+.3f}  {lv}  /race/xc/{meet}/{div}  {dist} m  {day}  "
                  f"({ny} {lv}, {na} college/pro)")


if __name__ == "__main__":
    main()
