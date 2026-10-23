# Project: xc-predictor / racecast
# File:    build_clock_calibration.py
# Purpose: The 5K column checked against the track, every run (owner,
#          2026-10-23: "the 5k equivs on my race were abt 15-20s faster").
#          For each pool and rating band: the time the site SHOWS for a
#          runner's cross country season, at the track distance that level
#          races in spring, against the best they actually ran there the
#          next spring. The median gap is stored, and conversions.ratingClock
#          takes it back out of the "≈ 17:31 5K on a track" it prints.
#
#     python racecast/build_clock_calibration.py            # report only
#     python racecast/build_clock_calibration.py --write    # and store it
#
# ★ THE CLOCK THE PAGE SHOWS, NOT A COUSIN. diag_hs_to_college part B read
#   every pool through the TRACK curve; the high school 5K column is the
#   cross country scale at 5000 m (conversions.ratingClock), and the boards'
#   track5k is the track curve. Both are measured here, as kind 'XC' and
#   'TF', each through conversions.ratingSeconds -- the function the site
#   calls -- at the level's spring distance (high school and middle school
#   3200, college 5000), and stored apart.
# ★ A SERIOUS BEST ONLY. A runner who ran the distance once may never have
#   raced it hard (owner, 2026-10-22: "every conversion is fast?"), so only
#   runners with MIN_TRACK_RACES or more at it count.
# ★ BY RATING BAND. The gap grows with the rating (part B: the fastest third
#   of hs_f -1.8%, the slowest +0.8%), so one number per pool would be wrong
#   at both ends. BANDS quantile bands per pool and kind; a band with fewer
#   than MIN_BAND runners is left out, and the site interpolates between the
#   bands' median ratings and holds the end values beyond them.
# ! DISPLAY ONLY. No rating, board order or course number changes; the site
#   reads the table (XCP_CLOCK_CAL=0 turns the correction off), and a run
#   that cannot build it leaves the last table in place.
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
MIN_SEASON_RACES = 3
BANDS = 5
MIN_BAND = 150
YEARS_BACK = 4
CAP_PER_POOL_YEAR = 25000
CLAMP_PCT = 6.0              # a correction past this is a broken input, not a clock

DDL = """
CREATE TABLE IF NOT EXISTS clock_calibration (
    pool        text    NOT NULL,
    kind        text    NOT NULL,      -- 'XC' (the cross country scale) / 'TF' (the track curve)
    rating_mid  real    NOT NULL,      -- the band's median rating
    rating_lo   real,
    rating_hi   real,
    n           integer NOT NULL,
    bias_pct    real    NOT NULL,      -- 100 ln(shown / ran): - = the clock reads fast
    built_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (pool, kind, rating_mid))"""

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


def bands(pairs, k=BANDS, min_n=MIN_BAND):
    """pairs: [(rating, bias_pct)] -> [(mid, lo, hi, n, median bias)] for
    k quantile bands of the rating, those with min_n or more. Pure."""
    pairs = sorted(p for p in pairs if p[0] and math.isfinite(p[1]))
    out = []
    if len(pairs) < min_n:
        return out
    for i in range(k):
        part = pairs[(i * len(pairs)) // k:((i + 1) * len(pairs)) // k]
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
                if ty == yr + 1:
                    tracks[pid].append((ev, sec))
        n0 = sum(len(v) for k, v in out.items() if k[0] == pool)
        for pid, rows_tf in tracks.items():
            ran, n_at = bestAndCount(rows_tf, metres, dist)
            if not ran or n_at < MIN_TRACK_RACES:
                continue
            for kind in ("XC", "TF"):
                t = shown(rating[pid], pool, kind, metres)
                if t and t > 0:
                    out[(pool, kind)].append((rating[pid], 100.0 * math.log(t / ran)))
        print(f"  {yr} {pool}: {sum(len(v) for k, v in out.items() if k[0] == pool) - n0:,} "
              f"runner-clocks with {MIN_TRACK_RACES}+ {int(metres)}s the next spring", flush=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--years-back", type=int, default=YEARS_BACK)
    a = ap.parse_args(argv)
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '15min'")
        got = measure(cur, a.years_back)
        rows = []
        print("\npool       kind  band rating (lo-hi)        n    shown vs ran")
        for (pool, kind), pairs in sorted(got.items()):
            for mid, lo, hi, n, b in bands(pairs):
                rows.append((pool, kind, mid, lo, hi, n, b))
                print(f"{pool:<10} {kind:<4} {mid:>6.1f} ({lo:.1f}-{hi:.1f}) {n:>8,}   {b:+.2f}%"
                      + ("  (reads fast)" if b < 0 else ""))
        conn.rollback()
        if not a.write:
            print("\nreport only; --write stores it")
            return 0
        if not rows:
            print("\nnothing measured: the last table stays")
            return 0
        cur.execute(DDL)
        cur.execute("DELETE FROM clock_calibration")
        from psycopg2.extras import execute_values
        execute_values(cur, """INSERT INTO clock_calibration
                               (pool, kind, rating_mid, rating_lo, rating_hi, n, bias_pct)
                               VALUES %s""", rows)
        conn.commit()
        print(f"\nclock_calibration: {len(rows)} bands written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
