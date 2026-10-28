# Project: xc-predictor / racecast
# File:    build_clock_calibration.py
# Purpose: The 5K column checked against the track, run by hand -- no
#          pipeline step runs it (owner, 2026-10-23: "the 5k equivs on my
#          race were abt 15-20s faster").
#          For each pool and rating band: the time the site SHOWS for a
#          runner's cross country season, at the track distance that level
#          races in spring, against the best they actually ran there the
#          next spring. REPORT ONLY (owner, 2026-10-24: "There's too many
#          corrections" -- the gap it found was taken back out of the 5K
#          column for one day and removed; a gap here is a fault in the
#          scale to fix there, not a number to patch on top).
#
#     python racecast/build_clock_calibration.py
#
# ★ THE CLOCK THE PAGE SHOWS, NOT A COUSIN. diag_hs_to_college part B read
#   every pool through the TRACK curve; the high school 5K column is the
#   cross country scale at 5000 m (conversions.ratingClock), and the boards'
#   track5k is the track curve. Both are measured here, as kind 'XC' and
#   'TF', each through conversions.ratingSeconds -- the function the site
#   calls -- at the level's spring distance (high school and middle school
#   3200, college 5000), and stored apart.
# ★ THE SPRING BEFORE AND THE SPRING AFTER, AVERAGED (owner's first report,
#   2026-10-24: middle school +5-6%, high school +2%, the fastest bands
#   least -- improvement, not the clock). A fall rating against the NEXT
#   spring's best reads every runner's growth since as the clock being
#   slow; against the spring BEFORE it would read it the other way. The
#   geometric mean of the two springs' bests sits at the fall in time, so
#   steady improvement cancels, as the +-30 days do in diag_weather_credit.
#   A runner needs a serious best in BOTH springs.
# ★ A SERIOUS BEST ONLY. A runner who ran the distance once may never have
#   raced it hard (owner, 2026-10-22: "every conversion is fast?"), so only
#   runners with MIN_TRACK_RACES or more at it count.
# ★ BY RATING BAND. The gap grows with the rating (part B: the fastest third
#   of hs_f -1.8%, the slowest +0.8%), so one number per pool would be wrong
#   at both ends. EDGES quantile bands per pool and kind; a band with fewer
#   than MIN_BAND runners is left out, and the site interpolates between the
#   bands' median ratings and holds the end values beyond them.
import argparse
import datetime
import math
import random
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

from database import getConn                            # noqa: E402

POOLS = ("hs_m", "hs_f", "ms_m", "ms_f", "college_m", "college_f")
SPRING_M = {"hs": 3200.0, "ms": 3200.0, "college": 5000.0}
TIME_RANGE = {3200.0: (500, 1200), 5000.0: (780, 1500)}
DIST_TOL = 0.01
MIN_TRACK_RACES = 3
MIN_TRACK_RACES_EACH = 2     # in each of the two springs
MIN_SEASON_RACES = 3
BANDS = 5
# ★ THE TOP 5% ITS OWN BAND (owner, 2026-10-24: the HS boys' board, 143.8 =
#   13:59, "too slow"; the college men's 13:21). Five equal bands put the
#   elite inside one whose median is ~128 (hs_m 124.9-142.9), and the site
#   holds that band's value for everyone above it. The top fifth is split
#   at the 95th percentile; a pool too small for 150 runners there (middle
#   school) keeps the top fifth whole.
EDGES = (0.0, 0.2, 0.4, 0.6, 0.8, 0.95, 1.0)
MIN_BAND = 150
YEARS_BACK = 4
CAP_PER_POOL_YEAR = 25000
CLAMP_PCT = 6.0              # a correction past this is a broken input, not a clock

_SEASONS_SQL = """
    SELECT person_id, split_part(pool, '|', 1), year, mean_rating
    FROM   athlete_season
    WHERE  sport = 'XC' AND person_id IS NOT NULL AND mean_rating > 0
      AND  n_races >= %(min_races)s AND year BETWEEN %(lo)s AND %(hi)s
      AND  split_part(pool, '|', 1) = ANY(%(pools)s)
"""
_TRACK_SQL = """
    SELECT person_id, substr(date::text, 1, 4)::int, event_short, time_seconds
    FROM   results_tf
    WHERE  person_id = ANY(%(ids)s)
      AND  substr(date::text, 6, 2) BETWEEN '01' AND '07'
      AND  time_seconds BETWEEN 400 AND 1500
      AND  COALESCE(is_relay, 0) = 0 AND COALESCE(is_field, 0) = 0
"""


# ---- pure parts ----------------------------------------------------------------

def level(pool):
    return (pool or "").split("_", 1)[0]


def bestAndCount(rows, metres, distance_of):
    """(best seconds at `metres` within DIST_TOL, how many races at it)
    among (event_short, seconds). distance_of(event_short) -> metres."""
    lo, hi = TIME_RANGE[metres]
    best, n = None, 0
    for ev, sec in rows:
        try:
            d = distance_of(ev or "")
        except Exception:                               # noqa: BLE001
            d = None
        if not d or abs(float(d) - metres) > DIST_TOL * metres:
            continue
        s = float(sec)
        if lo <= s <= hi:
            n += 1
            if best is None or s < best:
                best = s
    return best, n


def bands(pairs, k=None, min_n=MIN_BAND, edges=EDGES):
    """pairs: [(rating, bias_pct)] -> [(mid, lo, hi, n, median bias)] for
    the quantile bands `edges` cuts (or k equal ones), those with min_n or
    more. A last band too small is merged into the one below it. Pure."""
    pairs = sorted(p for p in pairs if p[0] and math.isfinite(p[1]))
    out = []
    if len(pairs) < min_n:
        return out
    if k is not None:
        edges = tuple(i / k for i in range(k + 1))
    cuts = [round(e * len(pairs)) for e in edges]
    if len(cuts) > 2 and cuts[-1] - cuts[-2] < min_n:
        cuts.pop(-2)
    for a, b in zip(cuts, cuts[1:]):
        part = pairs[a:b]
        if len(part) < min_n:
            continue
        rs = [r for r, _ in part]
        b = statistics.median(x for _, x in part)
        out.append((round(statistics.median(rs), 2), rs[0], rs[-1], len(part),
                    round(max(-CLAMP_PCT, min(CLAMP_PCT, b)), 3)))
    return out


# ---- the run -----------------------------------------------------------------

def measure(cur, years_back=YEARS_BACK, cap=CAP_PER_POOL_YEAR, seed=7):
    """{(pool, kind): [(rating, bias_pct)]}."""
    import conversions as C
    from event_parse import distanceFromEventShort

    def dist(ev):
        return distanceFromEventShort(ev)[0]

    this = datetime.date.today().year
    lo, hi = this - years_back, this - 1          # a season whose next spring is over
    cur.execute(_SEASONS_SQL, {"min_races": MIN_SEASON_RACES, "lo": lo, "hi": hi,
                               "pools": list(POOLS)})
    by = defaultdict(list)                        # (pool, year) -> [(pid, rating)]
    for pid, pool, yr, r in cur.fetchall():
        by[(pool, int(yr))].append((pid, float(r)))
    rnd = random.Random(seed)
    clock = {}

    def shown(rating, pool, kind, metres):
        key = (round(rating, 1), pool, kind, metres)
        if key not in clock:
            clock[key] = C.ratingSeconds(rating, pool, kind, metres)
        return clock[key]

    out = defaultdict(list)
    for (pool, yr), rows in sorted(by.items()):
        if len(rows) > cap:
            rows = rnd.sample(rows, cap)
        metres = SPRING_M[level(pool)]
        rating = dict(rows)
        ids = list(rating)
        tracks = defaultdict(list)
        for i in range(0, len(ids), 20000):
            cur.execute(_TRACK_SQL, {"ids": ids[i:i + 20000]})
            for pid, ty, ev, sec in cur.fetchall():
                if ty in (yr, yr + 1):              # the spring before, the spring after
                    tracks[pid].append((ty, ev, sec))
        n0 = sum(len(v) for k, v in out.items() if k[0] == pool)
        for pid, rows_tf in tracks.items():
            before, n_b = bestAndCount([(e, t) for y, e, t in rows_tf if y == yr], metres, dist)
            after, n_a = bestAndCount([(e, t) for y, e, t in rows_tf if y == yr + 1], metres, dist)
            if not before or not after or min(n_b, n_a) < MIN_TRACK_RACES_EACH:
                continue
            ran = math.sqrt(before * after)
            for kind in ("XC", "TF"):
                t = shown(rating[pid], pool, kind, metres)
                if t and t > 0:
                    out[(pool, kind)].append((rating[pid], 100.0 * math.log(t / ran)))
        print(f"  {yr} {pool}: {sum(len(v) for k, v in out.items() if k[0] == pool) - n0:,} "
              f"runner-clocks with {MIN_TRACK_RACES_EACH}+ {int(metres)}s in the spring "
              f"before and the spring after", flush=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--years-back", type=int, default=YEARS_BACK)
    a = ap.parse_args(argv)
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '15min'")
        got = measure(cur, a.years_back)
        print("\npool       kind  band rating (lo-hi)        n    shown vs ran")
        for (pool, kind), pairs in sorted(got.items()):
            for mid, lo, hi, n, b in bands(pairs):
                print(f"{pool:<10} {kind:<4} {mid:>6.1f} ({lo:.1f}-{hi:.1f}) {n:>8,}   {b:+.2f}%"
                      + ("  (reads fast)" if b < 0 else ""))
        conn.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
