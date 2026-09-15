#!/usr/bin/env python3
"""
sport_level_fit.py -- the sport level per pool, measured from the boards.

    set -a; . /etc/xc-predictor.env; set +a
    python scripts/sport_level_fit.py [--share 0.5] [--min-races 3]

★ WHAT IT MEASURES (owner, 2026-09-15). The boards' gap row (board_sanity)
  read college track 1.2-1.7 points under cross country and high school
  0.4-0.9 under, for the same athlete in the same academic year. The
  asserted level (XCP_SPORT_LEVEL) is one number for every pool, and the
  right target is not zero: a developing runner improves between
  November and May, so the spring should read a little above the fall.
  Two numbers per pool, from athlete_season:

    gap      median log-time TF minus XC, same athlete, pool and year
             (negative = track rates higher), n_races >= --min-races both
    growth   median log-time improvement from one XC season to the next,
             same athlete and pool (positive = faster next year)

  and the target: the spring sits --share of the way to next fall, so the
  stated fall-to-spring gain is share * growth. That is what
  --sport-level-pools / XCP_SPORT_LEVEL_POOLS takes, per pool LEVEL
  (the two genders' pools pooled by athlete-years); the go-live then
  shifts the track rows of each level so the gap reads exactly that.
  The line to paste is the last thing printed.

  Selection is stated, not hidden: growth is measured on athletes who
  raced XC two years running, which is the population that improves.
  --share 0.5 halves it; if you believe a spring gains the whole year's
  worth, --share 1.
"""
import argparse
import math
import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
from database import getConn                                    # noqa: E402

LEVELS = ("college", "hs", "ms", "elem")


def levelOf(pool):
    return (pool or "").split("|", 1)[0].split("_", 1)[0]


def summarise(gaps, growths, share):
    """{level: dict(n_gap, gap, n_growth, growth, gain)} from
    [(pool, log_gap)] and [(pool, log_growth)]. Pure."""
    by = {}
    for pool, v in gaps:
        by.setdefault(levelOf(pool), {"gap": [], "growth": []})["gap"].append(v)
    for pool, v in growths:
        by.setdefault(levelOf(pool), {"gap": [], "growth": []})["growth"].append(v)
    out = {}
    for level, d in by.items():
        gap = statistics.median(d["gap"]) if d["gap"] else float("nan")
        growth = statistics.median(d["growth"]) if d["growth"] else float("nan")
        gain = share * growth if math.isfinite(growth) else float("nan")
        out[level] = dict(n_gap=len(d["gap"]), gap=gap, n_growth=len(d["growth"]),
                          growth=growth, gain=gain)
    return out


def envLine(summary, min_pairs=1000):
    parts = []
    for level in LEVELS:
        d = summary.get(level)
        if not d or d["n_growth"] < min_pairs or not math.isfinite(d["gain"]):
            continue
        parts.append(f"{level}={max(d['gain'], 0.0):.4f}")
    return "XCP_SPORT_LEVEL_POOLS=" + ",".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--share", type=float, default=0.5)
    ap.add_argument("--min-races", type=int, default=3)
    args = ap.parse_args()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT x.pool, ln(x.mean_rating / t.mean_rating)
            FROM   athlete_season x
            JOIN   athlete_season t ON t.person_id = x.person_id AND t.pool = x.pool
                                    AND t.year = x.year AND t.sport = 'TF'
            WHERE  x.sport = 'XC' AND x.n_races >= %(k)s AND t.n_races >= %(k)s
              AND  x.mean_rating > 0 AND t.mean_rating > 0""", {"k": args.min_races})
        gaps = [(p, float(v)) for p, v in cur.fetchall()]
        cur.execute("""
            SELECT a.pool, ln(b.mean_rating / a.mean_rating)
            FROM   athlete_season a
            JOIN   athlete_season b ON b.person_id = a.person_id AND b.pool = a.pool
                                    AND b.year = a.year + 1 AND b.sport = 'XC'
            WHERE  a.sport = 'XC' AND a.n_races >= %(k)s AND b.n_races >= %(k)s
              AND  a.mean_rating > 0 AND b.mean_rating > 0""", {"k": args.min_races})
        growths = [(p, float(v)) for p, v in cur.fetchall()]
    summary = summarise(gaps, growths, args.share)
    print(f"[level] log-time; a point at 130 is about 0.77%. share {args.share:g} of the "
          f"year's XC-to-XC growth is the stated fall-to-spring gain.")
    print(f"  {'level':<9}{'athlete-yrs':>12}{'gap TF-XC':>11}{'yrs':>12}{'growth':>9}{'gain':>9}"
          f"{'shift to apply':>16}")
    for level in LEVELS:
        d = summary.get(level)
        if not d:
            continue
        shift = d["gap"] + d["gain"] if math.isfinite(d["gap"]) and math.isfinite(d["gain"]) else float("nan")
        print(f"  {level:<9}{d['n_gap']:>12,}{d['gap']:>+11.4f}{d['n_growth']:>12,}{d['growth']:>+9.4f}"
              f"{d['gain']:>+9.4f}{shift:>+16.4f}")
    print("\n  (gap: negative = track already rates higher; shift: what the go-live adds to "
          "the track rows' log time, negative = track rated up)")
    print("\n" + envLine(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
