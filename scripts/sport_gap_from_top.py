#!/usr/bin/env python3
"""
sport_gap_from_top.py -- pin the XC/TF scale from the one thing that does
constrain it: who is actually at the top of the board.

    scripts/sport_gap_from_top.py                     # every pool
    scripts/sport_gap_from_top.py --pool college_m
    scripts/sport_gap_from_top.py --top 200 --year 2025

Run from the PROJECT ROOT. READ-ONLY -- one SELECT over athlete_season.

★ WHY THIS EXISTS (owner, 2026-09-10: "I think it should be lower bcs abt
  90% of the top 200 seasons are xc").

  The XC/TF level is NOT identified by the corpus. Sport is very nearly
  season -- autumn is cross country, spring is track -- so an athlete's
  autumn rating and spring rating differ by the winter's fitness change
  AND by whatever the two scales disagree about, and no amount of racing
  separates those two. Every attempt in this repo to measure it has come
  back with a closure error at 20-90 standard errors. It is a choice.

  But a choice can still be WRONG, and the composition of the top of the
  board is how you can tell. If the two scales mean the same thing, the
  best seasons in a pool should be split between the sports roughly the
  way the pool's seasons are split. Ninety percent one sport is not a fact
  about athletes; it is a scale with its thumb on one side.

★ WHAT IT REPORTS.

    1. the sport mix of the top N, and of the whole pool -- the gap
       between those two IS the symptom
    2. mean and sd per sport, because the fix depends on which is wrong:
       a LEVEL difference is fixed by a shift, a SPREAD difference is not
       fixed by any shift at all and says the scales are stretched
       differently
    3. the shift that would balance the top N, in rating points and as
       the change it implies to the stated XC/TF gap

⚠ READ POINT 2 BEFORE ACTING ON POINT 3. If XC's sd is much larger than
  TF's, no single number balances the board -- shifting TF up to fix the
  top 200 will overshoot the top 20 and undershoot the top 1000, and the
  right answer is a scale problem, not an offset. The printout says which
  case you are in rather than leaving it to be inferred from the shift.

! WHAT TO DO WITH THE ANSWER. The shift is applied with
  `run_joint --sport-gap-delta`, NOT by editing js.XC_TRACK_GAP -- that
  constant is only used in joint_golive's printed comparison and never
  reaches the solve, so editing it moves a warning line and no ratings.
"""

import argparse
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

_SQL = """
    SELECT pool, sport, mean_rating
    FROM   athlete_season
    WHERE  mean_rating IS NOT NULL
      AND  pool IS NOT NULL AND sport IS NOT NULL
      AND  COALESCE(n_races, 0) >= %(min_races)s
      {pool}{year}
"""


def _sd(vs):
    n = len(vs)
    if n < 2:
        return 0.0
    m = sum(vs) / n
    return math.sqrt(sum((v - m) ** 2 for v in vs) / (n - 1))


def _shareTop(xc, tf, delta, top):
    """XC's share of the top `top` seasons when every TF rating is raised
    by `delta`."""
    merged = [(r, 0) for r in xc] + [(r + delta, 1) for r in tf]
    merged.sort(key=lambda t: -t[0])
    head = merged[:top]
    if not head:
        return None
    return sum(1 for _, s in head if s == 0) / len(head)


def _solve(xc, tf, target, top):
    """The delta on TF that brings XC's share of the top down to `target`.
    Bisection: the share is monotone non-increasing in delta."""
    lo, hi = 0.0, 1.0
    if _shareTop(xc, tf, 0.0, top) <= target:
        return 0.0                      # already balanced or TF-heavy
    # grow the bracket until TF takes over
    for _ in range(40):
        if _shareTop(xc, tf, hi, top) <= target:
            break
        lo, hi = hi, hi * 2
        if hi > 1e4:
            return None
    else:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if _shareTop(xc, tf, mid, top) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _report(pool, rows, top, target_mode):
    xc = [float(r) for s, r in rows if s == "XC"]
    tf = [float(r) for s, r in rows if s == "TF"]
    if len(xc) < top // 4 or len(tf) < top // 4:
        print(f"\n{pool}: too few seasons (XC {len(xc):,}, TF {len(tf):,})")
        return
    n = len(xc) + len(tf)
    pop_share = len(xc) / n
    now = _shareTop(xc, tf, 0.0, top)

    print(f"\n{'=' * 72}\n{pool}\n{'=' * 72}")
    print(f"  seasons          XC {len(xc):>8,}   TF {len(tf):>8,}   "
          f"(XC is {100 * pop_share:.1f}% of the pool)")
    print(f"  top {top:<12} XC is {100 * now:.1f}% of it")
    print(f"  mean rating      XC {sum(xc)/len(xc):>8.2f}   "
          f"TF {sum(tf)/len(tf):>8.2f}")
    print(f"  sd               XC {_sd(xc):>8.2f}   TF {_sd(tf):>8.2f}")

    # ⚠ THE SPREAD TEST, FIRST, because it decides whether a shift is even
    #   the right shape of fix.
    sd_xc, sd_tf = _sd(xc), _sd(tf)
    ratio = sd_xc / sd_tf if sd_tf > 0 else float("inf")
    if ratio > 1.25 or ratio < 0.8:
        print(f"\n  ⚠ THE SPREADS DISAGREE (XC sd is {ratio:.2f}x TF's). No "
              f"single shift can\n    balance the board -- one that fixes "
              f"the top {top} will be wrong at the\n    top 20 and at the "
              f"top 1000. This is a scale being stretched,\n    not an "
              f"offset, and --sport-gap-delta cannot fix it.")

    # ★★ THE DEPTH TABLE, WHICH IS THE REAL TEST AND BEATS THE sd RATIO.
    #    If the two scales differ by an OFFSET, the delta that balances the
    #    top 50 also balances the top 1000. If they differ in SPREAD, the
    #    delta drifts with depth and no single number is right. Measured on
    #    a planted world: a true 4.00-point offset solved to 3.15 / 3.54 /
    #    4.02 across depths (noise), while a pure stretch solved to
    #    18.7 / 15.5 / 12.3 -- drifting one way, hard.
    tgt = pop_share if target_mode == "population" else 0.5
    print(f"\n  balancing shift by depth (target {100 * tgt:.1f}% XC):")
    seen = []
    for depth in (50, 200, 1000, 5000):
        if depth > n // 2:
            continue
        sh = _shareTop(xc, tf, 0.0, depth)
        dd = _solve(xc, tf, tgt, depth)
        seen.append(dd)
        print(f"    top {depth:>5}   XC now {100 * sh:>5.1f}%   "
              f"shift {'n/a' if dd is None else format(dd, '.2f')}")
    good = [d for d in seen if d is not None]
    if len(good) >= 3 and min(good) > 0:
        drift = max(good) / min(good)
        if drift > 1.6:
            print(f"\n  ⚠ THE SHIFT DRIFTS {drift:.1f}x ACROSS DEPTHS. That "
                  f"is a stretch, not an\n    offset -- --sport-gap-delta "
                  f"moves the whole sport by one number and\n    cannot fix "
                  f"it. Fixing the top 200 will break the top 20.")
        else:
            print(f"\n  the shift is stable across depths ({drift:.2f}x "
                  f"spread), so this really is\n  an offset and one number "
                  f"fixes it.")

    target = pop_share if target_mode == "population" else 0.5
    label = ("the pool's own mix" if target_mode == "population"
             else "an even split")
    if now <= target + 0.005:
        print(f"\n  => already at or below {label} "
              f"({100 * target:.1f}%). Nothing to correct.")
        return
    delta = _solve(xc, tf, target, top)
    if delta is None:
        print(f"\n  => no shift reaches {label}; the distributions barely "
              f"overlap.")
        return
    # rating = 100 * K / normalized_time, so a rating multiplied by f is a
    # normalized_time divided by f: the log-time gap moves by -ln(f).
    med = sorted(xc)[len(xc) // 2]
    f = (med + delta) / med
    print(f"\n  => raise TF by {delta:.2f} rating points to bring the top "
          f"{top} to {label}\n     ({100 * target:.1f}% XC). At a typical "
          f"rating of {med:.0f} that is x{f:.4f},")
    print(f"     i.e. the XC/TF gap narrows by {100 * math.log(f):.2f} "
          f"percentage points\n     (stated gap is currently "
          f"{100 * (math.exp(0.0583) - 1):.2f}%).")
    print(f"\n     Apply with:  run_joint --sport-gap-delta "
          f"{-math.log(f):.4f}")
    print(f"     NOT by editing js.XC_TRACK_GAP -- that constant never "
          f"reaches the solve.")


def main():
    ap = argparse.ArgumentParser(
        description="Pin the XC/TF scale from the sport mix at the top of "
                    "the board.")
    ap.add_argument("--pool", help="one pool, e.g. college_m")
    ap.add_argument("--year", type=int, help="one academic year")
    ap.add_argument("--top", type=int, default=200)
    ap.add_argument("--min-races", type=int, default=3,
                    help="seasons with fewer races are noise (default 3)")
    ap.add_argument("--target", choices=["population", "even"],
                    default="population",
                    help="balance the top against the pool's own sport mix "
                         "(default) or against an even split")
    args = ap.parse_args()

    pool_sql = " AND pool = %(pool)s" if args.pool else ""
    year_sql = " AND year = %(year)s" if args.year else ""
    sql = _SQL.format(pool=pool_sql, year=year_sql)

    from database import getConn
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("SET LOCAL statement_timeout = '10min'")
        try:
            cur.execute(sql, {"min_races": args.min_races,
                              "pool": args.pool, "year": args.year})
            by_pool = {}
            for pool, sport, rating in cur.fetchall():
                by_pool.setdefault(pool, []).append((sport, rating))
            if not by_pool:
                print("no seasons matched")
                return 1
            print(f"\nTop {args.top} seasons per pool, "
                  f"min {args.min_races} races"
                  f"{f', year {args.year}' if args.year else ''}")
            for pool in sorted(by_pool, key=lambda p: -len(by_pool[p])):
                _report(pool, by_pool[pool], args.top, args.target)
        finally:
            conn.rollback()
            cur.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
