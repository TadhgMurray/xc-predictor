#!/usr/bin/env python3
"""
pool_equal_time.py -- does one time in one race give one public rating,
whatever the runner's pool? READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/pool_equal_time.py                  # since 2018, both sports
    $P scripts/pool_equal_time.py --since 2010-01-01 --sport TF

★ WHY (owner, 2026-11-07, Cooper Lutkenhaus: 1:42.27 as a high schooler
  rated 153.8, 1:42.08 as a pro 147.5 -- "maybe we should test if it's more
  accurate"). The public (HS-equivalent) scale maps each pool onto the high
  school one by EQUAL TIME AT ONE DISTANCE (pool_view.hsMap: 3200-5000 m).
  At other distances the pools' own curves differ, so equal times may not
  give equal public ratings.

★ THE TEST NEEDS NO MODEL: RUNNERS OF TWO POOLS IN ONE RACE. Same race,
  same day, same course -- two runners within NEAR of each other's time ran
  the same performance, so their public ratings should match. Per sport,
  distance and pool pair: the median gap in public rating at equal time
  (first pool less second, in %). About 0 everywhere = the one-distance map
  is right; a gap that grows away from the map's distance = matching each
  distance would be more accurate, and by how much.
"""
import argparse
import math
import os
import random
import statistics
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

NEAR = 0.005          # two times within 0.5% are one performance
PAIRS_PER_RACE = 200  # no one big race outvotes the rest
MIN_PAIRS = 30        # a cell with fewer is not printed

_SQL = """
    WITH r AS (
        SELECT sport, meet_id, COALESCE(event_id, 0) AS ev, race_date,
               round(distance::numeric, -1)::int AS dist,
               split_part(pool, '|', 1) AS p, speed_rating AS s, time_seconds AS t
        FROM   ranking_results
        WHERE  speed_rating > 0 AND time_seconds > 0 AND distance > 0
          AND  race_date >= %(since)s::date AND sport = ANY(%(sports)s)),
    mixed AS (
        SELECT sport, meet_id, ev, race_date, dist
        FROM   r GROUP BY 1, 2, 3, 4, 5
        HAVING count(DISTINCT p) >= 2)
    SELECT r.sport, r.meet_id, r.ev, r.race_date, r.dist, r.p, r.s, r.t
    FROM   r JOIN mixed USING (sport, meet_id, ev, race_date, dist)
"""


def band(sport, metres):
    """A distance band's label."""
    if sport == "XC":
        return "XC <4.5k" if metres < 4500 else ("XC 5k" if metres < 5600 else
                                                 ("XC 6k" if metres < 7000 else "XC 8-10k"))
    for lo, hi, name in ((700, 900, "800"), (1400, 1700, "1500/mile"), (2900, 3300, "3000/3200"),
                         (4900, 5100, "5000"), (9900, 10100, "10000")):
        if lo <= metres <= hi:
            return name
    return None


def gaps(rows, near=NEAR, per_race=PAIRS_PER_RACE, seed=1):
    """rows of one race: [(pool, public rating, seconds)] -> [(pool a, pool b,
    100 * ln(public a / public b))] for pairs of two pools within `near` of
    each other's time, a < b. Pure (seeded)."""
    rows = sorted(rows, key=lambda r: r[2])
    out = []
    for i, (pa, ha, ta) in enumerate(rows):
        for pb, hb, tb in rows[i + 1:]:
            if math.log(tb / ta) > near:
                break
            if pa == pb or ha <= 0 or hb <= 0:
                continue
            if pa > pb:
                pa_, pb_, ha_, hb_ = pb, pa, hb, ha
            else:
                pa_, pb_, ha_, hb_ = pa, pb, ha, hb
            out.append((pa_, pb_, 100.0 * math.log(ha_ / hb_)))
    if len(out) > per_race:
        out = random.Random(seed).sample(out, per_race)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2018-01-01")
    ap.add_argument("--sport", choices=("XC", "TF", "both"), default="both")
    a = ap.parse_args(argv)
    import pool_view
    from database import getConn
    sports = ["XC", "TF"] if a.sport == "both" else [a.sport]
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = 0")
        cur.execute(_SQL, {"since": a.since, "sports": sports})
        raw = cur.fetchall()
        conn.rollback()
    races = defaultdict(list)
    for sport, meet, ev, day, dist, pool, s, t in raw:
        b = band(sport, dist or 0)
        if not b:
            continue
        hs = pool_view.toHs(float(s), pool, sport)
        if hs is None:
            continue
        races[(sport, b, meet, ev, day)].append((pool, float(hs), float(t)))
    cells = defaultdict(list)
    n_races = defaultdict(set)
    for (sport, b, meet, ev, day), rows in races.items():
        for pa, pb, g in gaps(rows):
            cells[(sport, b, pa, pb)].append(g)
            n_races[(sport, b, pa, pb)].add((meet, ev, day))
    print(f"[equal time] {len(raw):,} rows in {len(races):,} races run by two pools or more, "
          f"since {a.since}. Public rating gap at equal time (within {NEAR:.1%}), first pool "
          f"less second; 0 = one time, one rating.")
    order = {"XC <4.5k": 0, "XC 5k": 1, "XC 6k": 2, "XC 8-10k": 3, "800": 10, "1500/mile": 11,
             "3000/3200": 12, "5000": 13, "10000": 14}
    print(f"  {'sport':<5} {'distance':<10} {'pools':<22} {'pairs':>7} {'races':>6} "
          f"{'median gap':>11} {'middle half':>16}")
    for key in sorted(cells, key=lambda k: (k[0], order.get(k[1], 99), k[2], k[3])):
        v = cells[key]
        if len(v) < MIN_PAIRS:
            continue
        q = statistics.quantiles(v, n=4) if len(v) >= 4 else [min(v), 0, max(v)]
        sport, b, pa, pb = key
        print(f"  {sport:<5} {b:<10} {pa + ' / ' + pb:<22} {len(v):>7,} {len(n_races[key]):>6,} "
              f"{statistics.median(v):>+10.2f}% {q[0]:>+7.2f} to {q[2]:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
