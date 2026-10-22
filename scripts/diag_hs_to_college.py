#!/usr/bin/env python3
"""
diag_hs_to_college.py -- when a runner moves from high school to college,
do their RATING and their 5K CLOCK tell the same story, and which one does
the track agree with? READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_hs_to_college.py
    /srv/venv/bin/python scripts/diag_hs_to_college.py --since 2019 --until 2025 --cap 30000

★ WHY (owner, 2026-10-22, a Notre Dame freshman: 2022 HS 149.3 = 16:50,
  2023 college 149.2 = 16:02). The athlete page shows each season's rating
  on the one public scale (HS-equivalent: own-pool rating x the pool's
  factor, pool_view.repFactor) and beside it the track 5K that rating is
  worth on its OWN pool's clock (conversions.fiveKForRating). Equal
  ratings, clocks 5% apart: they cannot both be right. Two readings:

    1. COLLEGE COURSES OVER-CREDITED (diag_course_credit: +0.11 within
       college seasons): college times read too fast, so the college clock
       is fast and her "16:02" flatters her; the ratings are nearer right.
    2. THE HS FACTOR FOR COLLEGE POOLS IS LOW: she did get ~5% faster and
       her college rating should read ~156 on the public scale.

★ THREE PARTS
  A  SAME RUNNERS, HS -> COLLEGE. Last HS cross country season (year Y)
     and first college season (Y+1), same person. Median change of the
     public-scale rating, median change of the 5K clock, and the gap
     between them (0 = they agree; +5 = the clock says 5% more improvement
     than the rating). This only measures the DISAGREEMENT.
  B  EACH POOL'S CLOCK AGAINST THE TRACK. A runner's fall XC season rating,
     as the track time it is worth at the pool's own race (high school
     3200, college 5000), against the best they actually ran at that
     distance the next spring. Median (predicted - actual), % of time; -
     = the clock says FASTER than they ran. This is the JUDGE: a pool
     whose clock is right reads ~0.
  C  THE VERDICT. College clock fast by about A's gap and HS clock right:
     reading 1 (the courses). Both clocks right: reading 2 (the factor).

! The 5K column and the public scale are display code on top of the solve;
  this reads them exactly as the athlete page does (fiveKForRating,
  repFactor), so what it measures is what readers see.
"""
import argparse
import math
import os
import statistics
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TRACK_DIST = {"hs": 3200.0, "college": 5000.0}   # each level's own distance race
TIME_RANGE = {3200.0: (500, 1100), 5000.0: (780, 1500),   # seconds, sane bests
              1500.0: (210, 420), 1600.0: (230, 450), 1609.344: (230, 450),
              3000.0: (470, 900)}
RIEGEL = 1.06       # only across distances a few % apart (part D), where it barely matters
DIST_TOL = 0.01

_SEASONS_SQL = """
    SELECT person_id, split_part(pool, '|', 1) AS pool, year, mean_rating, n_races
    FROM   athlete_season
    WHERE  sport = 'XC' AND person_id IS NOT NULL AND mean_rating > 0
      AND  n_races >= %(min_races)s AND year BETWEEN %(lo)s AND %(hi)s
      AND  split_part(pool, '|', 1) IN ('hs_m', 'hs_f', 'college_m', 'college_f')
"""
_TRACK_SQL = """
    SELECT person_id, substr(date::text, 1, 4)::int AS yr, event_short, time_seconds
    FROM   results_tf
    WHERE  person_id = ANY(%(ids)s)
      AND  substr(date::text, 6, 2) BETWEEN '01' AND '07'
      AND  time_seconds BETWEEN 500 AND 1500
      AND  COALESCE(is_relay, 0) = 0 AND COALESCE(is_field, 0) = 0
"""


# ---- pure parts ----------------------------------------------------------

def level(pool):
    return (pool or "").split("_", 1)[0]


def logPct(a, b):
    """100 * ln(a / b). Pure."""
    return 100.0 * math.log(a / b)


def robustSd(xs):
    xs = sorted(xs)
    if len(xs) < 4:
        return float("nan")
    q1, q3 = xs[len(xs) // 4], xs[(3 * len(xs)) // 4]
    return (q3 - q1) / 1.349


def movers(seasons):
    """[(person, hs_pool, hs_rating, col_pool, col_rating)] for runners
    whose LAST HS season is year Y and FIRST college season Y+1. Pure.
    seasons: {person: [(pool, year, rating)]}."""
    out = []
    for pid, rows in seasons.items():
        hs = [r for r in rows if level(r[0]) == "hs"]
        col = [r for r in rows if level(r[0]) == "college"]
        if not hs or not col:
            continue
        last_hs = max(hs, key=lambda r: r[1])
        first_col = min(col, key=lambda r: r[1])
        if first_col[1] == last_hs[1] + 1 and last_hs[0][-1] == first_col[0][-1]:
            out.append((pid, last_hs[0], last_hs[2], first_col[0], first_col[2]))
    return out


def bestAt(rows, metres):
    """The best time at `metres` (within 1%) among (event_short, seconds)
    rows, or None. distance from event_parse. Pure apart from the parser."""
    return bestAndCount(rows, metres)[0]


def bestAndCount(rows, metres):
    """(best time at `metres`, how many races at it). Pure apart from the
    parser."""
    from event_parse import distanceFromEventShort
    lo, hi = TIME_RANGE[metres]
    best, n = None, 0
    for ev, sec in rows:
        try:
            d, _g = distanceFromEventShort(ev or "")
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


def summarize(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    if not xs:
        return None
    return {"n": len(xs), "median": statistics.median(xs), "sd": robustSd(xs)}


# ---- the run ---------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=int, default=2019)
    ap.add_argument("--until", type=int, default=2025)
    ap.add_argument("--min-races", type=int, default=3)
    ap.add_argument("--cap", type=int, default=25000,
                    help="runners per pool and year in part B (a random sample above it)")
    a = ap.parse_args()

    import random
    import conversions as C
    from pool_view import repFactor
    from database import getConn

    clock_cache = {}

    def track(rating, pool, metres):
        key = (round(float(rating), 1), pool, metres)
        if key not in clock_cache:
            r = C.fiveKForRating(float(rating), pool, metres)
            clock_cache[key] = r.get("track") if r else None
        return clock_cache[key]

    factor = {p: (repFactor(p, "XC") or 1.0) for p in ("hs_m", "hs_f", "college_m", "college_f")}
    print("public-scale factors (own -> HS-equivalent): "
          + ", ".join(f"{p} {f:.4f}" for p, f in factor.items()))

    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '30min'")
        cur.execute(_SEASONS_SQL, {"lo": a.since, "hi": a.until + 1, "min_races": a.min_races})
        seasons = defaultdict(list)
        for pid, pool, yr, mean, _n in cur.fetchall():
            seasons[pid].append((pool, int(yr), float(mean)))
        print(f"{sum(len(v) for v in seasons.values()):,} XC athlete-seasons, "
              f"{len(seasons):,} runners, {a.since}-{a.until + 1}")

        # ---- A: same runners, HS -> college ----
        mv = movers(seasons)
        partA = defaultdict(lambda: {"rating": [], "clock": [], "gap": []})
        for _pid, hp, hr, cp, cr in mv:
            g = hp[-1]
            r_hs, r_col = hr * factor[hp], cr * factor[cp]
            c_hs, c_col = track(hr, hp, 5000.0), track(cr, cp, 5000.0)
            if not (c_hs and c_col):
                continue
            dr = logPct(r_col, r_hs)                 # + = rated faster in college
            dc = logPct(c_hs, c_col)                 # + = clock faster in college
            for k in (g, "all"):
                partA[k]["rating"].append(dr)
                partA[k]["clock"].append(dc)
                partA[k]["gap"].append(dc - dr)
        print(f"\n== A. Same runners, last HS season -> first college season ({len(mv):,} runners)")
        print("   change in the public-scale rating vs change in the track-5K clock, % "
              "(+ = faster in college)")
        print(f"   {'':<8}{'runners':>9}{'rating':>10}{'clock':>10}{'gap':>10}{'gap sd':>9}")
        for k in ("f", "m", "all"):
            d = partA.get(k)
            if not d or not d["gap"]:
                continue
            print(f"   {('women' if k == 'f' else 'men' if k == 'm' else 'all'):<8}"
                  f"{len(d['gap']):>9,}{statistics.median(d['rating']):>+9.2f}%"
                  f"{statistics.median(d['clock']):>+9.2f}%{statistics.median(d['gap']):>+9.2f}%"
                  f"{robustSd(d['gap']):>8.2f}%")

        # ---- B: each pool's clock against the next spring's track ----
        rnd = random.Random(7)
        partB = defaultdict(list)                     # pool -> [bias %]
        serious = defaultdict(list)                   # the same, 3+ races at the distance
        bands = defaultdict(list)                     # (pool, tercile) -> [bias %]
        for yr in range(a.since, a.until + 1):
            for pool in ("hs_f", "hs_m", "college_f", "college_m"):
                metres = TRACK_DIST[level(pool)]
                rows = [(pid, r) for pid, ss in seasons.items()
                        for p, y, r in ss if p == pool and y == yr]
                if len(rows) > a.cap:
                    rows = rnd.sample(rows, a.cap)
                if not rows:
                    continue
                rating = dict(rows)
                ids = list(rating)
                tracks = defaultdict(list)
                for i in range(0, len(ids), 20000):
                    cur.execute(_TRACK_SQL, {"ids": ids[i:i + 20000]})
                    for pid, ty, ev, sec in cur.fetchall():
                        if ty == yr + 1:
                            tracks[pid].append((ev, sec))
                rs = sorted(rating.values())
                cuts = (rs[len(rs) // 3], rs[(2 * len(rs)) // 3]) if len(rs) >= 3 else (0, 0)
                n0 = len(partB[pool])
                for pid, rows_tf in tracks.items():
                    actual, n_at = bestAndCount(rows_tf, metres)
                    pred = track(rating[pid], pool, metres)
                    if not actual or not pred:
                        continue
                    b = logPct(pred, actual)          # - = clock faster than they ran
                    partB[pool].append(b)
                    # ★ A ONE-OFF BEST IS SOFT (owner, 2026-10-22: "every
                    #   conversion is fast?"): a runner who raced the distance
                    #   once may never have run it hard. The same test on
                    #   runners with 3+ races at it says how much of "fast"
                    #   is the clock and how much the soft best.
                    if n_at >= 3:
                        serious[pool].append(b)
                    t = 0 if rating[pid] < cuts[0] else 1 if rating[pid] < cuts[1] else 2
                    bands[(pool, t)].append(b)
                print(f"   {yr} {pool}: {len(partB[pool]) - n0:,} runners with a "
                      f"{int(metres)} the next spring", flush=True)
        # ---- D: the same runners' own track times, HS senior -> college freshman ----
        # ★ NO SCALE IN IT (owner, 2026-10-22: "most athletes barely moving
        #   seems very off"). A's "rating change" and "clock change" are both
        #   our numbers; this is the stopwatch. The spring after the last HS
        #   cross country season (year Y+1) against the spring after the first
        #   college one (Y+2): 1600 against the college mile or 1500, 3200
        #   against the 3000 -- distances a few % apart, put on one with
        #   Riegel's 1.06, which over 1-7% of distance moves a time by well
        #   under the effects measured here.
        last_hs = {pid: max(y for p, y, _r in seasons[pid] if level(p) == "hs") for pid, *_x in mv}
        rated_change = {pid: logPct(cr * factor[cp], hr * factor[hp]) for pid, hp, hr, cp, cr in mv}
        sex = {pid: hp[-1] for pid, hp, *_x in mv}
        ids = list(last_hs)
        springs = defaultdict(lambda: defaultdict(list))   # pid -> year -> [(ev, sec)]
        for i in range(0, len(ids), 20000):
            cur.execute(_TRACK_SQL, {"ids": ids[i:i + 20000]})
            for pid, ty, ev, sec in cur.fetchall():
                if ty in (last_hs[pid] + 1, last_hs[pid] + 2):
                    springs[pid][ty].append((ev, sec))
        partD = defaultdict(lambda: {"actual": [], "rated": []})
        for pid, by in springs.items():
            y = last_hs[pid]
            for event, hs_d, col_ds in (("mile", 1600.0, (1609.344, 1500.0)),
                                        ("3000", 3200.0, (3000.0,))):
                t_hs = bestAt(by.get(y + 1, []), hs_d)
                if not t_hs:
                    continue
                cands = []
                for d in col_ds:
                    t = bestAt(by.get(y + 2, []), d)
                    if t:
                        cands.append(t * (hs_d / d) ** RIEGEL)    # on the HS distance
                if not cands:
                    continue
                actual = logPct(t_hs, min(cands))                # + = faster in college
                for k in (sex[pid], "all"):
                    partD[(event, k)]["actual"].append(actual)
                    partD[(event, k)]["rated"].append(rated_change[pid])
        conn.rollback()

    print("\n== D. The same runners' own track times, HS senior spring -> college freshman spring")
    print("   (+ = faster in college; 'rating' is A's public-scale XC rating change for the same runners)")
    print(f"   {'':<14}{'runners':>9}{'stopwatch':>11}{'rating':>9}{'faster':>9}")
    for event in ("mile", "3000"):
        for k in ("f", "m", "all"):
            d = partD.get((event, k))
            if not d or not d["actual"]:
                continue
            up = 100.0 * sum(1 for x in d["actual"] if x > 0) / len(d["actual"])
            name = {"f": "women", "m": "men", "all": "all"}[k]
            print(f"   {(('1600 -> mile' if event == 'mile' else '3200 -> 3000') + ' ' + name):<22}"
                  f"{len(d['actual']):>5,}{statistics.median(d['actual']):>+10.2f}%"
                  f"{statistics.median(d['rated']):>+8.2f}%{up:>8.0f}%")

    print("\n== B. Each pool's clock against the next spring's track "
          "(predicted - actual, % of time; - = the clock says faster than they ran)")
    print(f"   {'pool':<11}{'distance':>9}{'runners':>9}{'median':>9}{'sd':>8}"
          f"{'  slowest third':>16}{'middle':>9}{'fastest third':>15}")
    verdict = {}
    for pool in ("hs_f", "college_f", "hs_m", "college_m"):
        s = summarize(partB.get(pool) or [])
        if not s:
            continue
        verdict[pool] = s["median"]
        t = [summarize(bands.get((pool, i)) or []) for i in range(3)]
        print(f"   {pool:<11}{int(TRACK_DIST[level(pool)]):>9}{s['n']:>9,}{s['median']:>+8.2f}%"
              f"{s['sd']:>7.2f}%" + "".join(f"{(x['median'] if x else float('nan')):>+15.2f}%"
                                              if i == 0 else
                                              f"{(x['median'] if x else float('nan')):>+8.2f}%"
                                              if i == 1 else
                                              f"{(x['median'] if x else float('nan')):>+14.2f}%"
                                              for i, x in enumerate(t)))

    print("\n   the same, only runners with 3+ races at the distance that spring (a real best):")
    for pool in ("hs_f", "college_f", "hs_m", "college_m"):
        s = summarize(serious.get(pool) or [])
        if s:
            print(f"   {pool:<11}{int(TRACK_DIST[level(pool)]):>9}{s['n']:>9,}{s['median']:>+8.2f}%"
                  f"{s['sd']:>7.2f}%")

    print("\n== C. Reading it")
    for g, name in (("f", "women"), ("m", "men")):
        hs, col = verdict.get(f"hs_{g}"), verdict.get(f"college_{g}")
        gap = partA.get(g)
        gap = statistics.median(gap["gap"]) if gap and gap["gap"] else None
        if hs is None or col is None or gap is None:
            continue
        rel = col - hs                    # - = the college clock reads faster than the HS one
        print(f"   {name}: HS -> college, the clock says {gap:+.2f}% more improvement than the "
              f"rating. Against the track, the college clock reads {rel:+.2f}% vs the HS clock.")
        print(f"     {verdictText(gap, rel)}")


def verdictText(gap, rel):
    """Which reading the numbers favour. gap: A's clock-minus-rating change
    (+ = the clock claims more improvement); rel: B's college-minus-HS clock
    bias against the track (- = college clock faster than its track). If
    the college clock is fast by exactly the gap, rel = -gap: reading 1.
    Pure."""
    if abs(gap) < 1.0:
        return "-> the rating and the clock agree to within 1%: nothing to settle."
    share = -rel / gap                    # the part of the gap the college clock explains
    if share >= 0.67:
        return (f"-> mostly reading 1 ({100 * share:.0f}% of the gap): the college clock is fast "
                "against the track -- college times read too fast (course credit); the "
                "public-scale ratings are nearer the truth.")
    if share <= 0.33:
        return (f"-> mostly reading 2 (the clock explains only {100 * max(share, 0):.0f}% of the "
                "gap): the clocks match the track, so runners did improve as the clock says and "
                "the public-scale factor for college pools is too low.")
    return (f"-> both: the college clock explains {100 * share:.0f}% of the gap, the "
            "public-scale factor the rest.")


if __name__ == "__main__":
    main()
