"""diag_conversion_gain.py -- does the conversions page agree with the
ratings the engine actually stored? Both sports, and it says WHICH HALF
disagrees.

    /srv/venv/bin/python scripts/diag_conversion_gain.py
    /srv/venv/bin/python scripts/diag_conversion_gain.py --sport XC --pool hs_m
    /srv/venv/bin/python scripts/diag_conversion_gain.py --sport TF --n 800

★ WHY IT SPLITS IN TWO. The engine's relation between a stored rating and
  a stored normalized_time is one line of algebra; everything else -- the
  distance curve, the course difficulty, the weather, the winter gain --
  lives between normalized_time and the raw finish time. Joining
  ranking_results (rating, pool, adjudicated distance) to the source table
  (normalized_time) puts all three on one row, so the two halves can be
  measured apart instead of blamed together:

    A. rating  <-> normalized_time    the pool mean and the engine scale.
                                      No distance, no difficulty, no gain.
                                      Off here = every conversion in this
                                      pool is off by the same factor.

    B. time    <-> normalized_time    the distance curve, the difficulty,
                                      the sport gain. Off here with A
                                      clean = the curve or the difficulty,
                                      not the page's scale.

  That is the split worth having, because A is a page bug and B is
  usually an engine one -- and "the conversions look wrong" has meant
  both at different times (issue 306; the TF sport-gain scale break of
  2026-09-15).

★ THE SIGN, STATED ONCE. Errors are reported as a percentage of the
  engine's own number, and "FAST" always means the page produces a
  smaller time (or a bigger rating) than the engine stored.

! IT MEASURES AGREEMENT, NOT TRUTH. Both halves can agree perfectly and
  the ratings still be wrong, if the engine's curve is wrong -- this
  cannot see that. What it can see is the page and the engine drifting
  apart, which is the class of bug that keeps recurring because every
  round-trip test cancels it.
"""
import argparse
import os
import statistics
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import psycopg2.extras                                   # noqa: E402

from database import getConn                             # noqa: E402

# ⚠ TWO TABLES, ON PURPOSE. ranking_results has the RATING POOL and the
#   adjudicated distance; only the source table has normalized_time (it is
#   not in build_ranking_results._COLUMNS). Joining on result_id is what
#   puts rating, normalized_time and distance on one row, which is what
#   makes the A/B split below possible at all.
_SOURCE = {"XC": "results", "TF": "results_tf"}

SAMPLE_SQL = """
    SELECT rr.speed_rating, rr.time_seconds, rr.distance, rr.pool,
           src.normalized_time
    FROM   ranking_results rr
    JOIN   {source} src ON src.result_id = rr.result_id
    WHERE  rr.speed_rating IS NOT NULL
      AND  rr.time_seconds > %(min_time)s
      AND  rr.distance     IS NOT NULL
      AND  src.normalized_time IS NOT NULL
      AND  src.normalized_time > %(min_time)s
      AND  rr.sport = %(sport)s
      AND  rr.race_date >= %(since)s
      {pool_clause}
    ORDER BY random()
    LIMIT %(n)s
"""


def sample(cur, n, sport, pool, since, min_time):
    clause = "AND rr.pool = %(pool)s" if pool else ""
    params = {"n": n, "sport": sport, "since": since,
              "min_time": min_time, "pool": pool}
    cur.execute(SAMPLE_SQL.format(source=_SOURCE[sport], pool_clause=clause),
                params)
    return cur.fetchall()


def _stats(errs):
    if not errs:
        return None
    errs.sort()
    return {"n": len(errs), "median": statistics.median(errs),
            "p25": errs[len(errs) // 4], "p75": errs[3 * len(errs) // 4],
            "mean_abs": statistics.fmean(abs(e) for e in errs)}


def halfA(cv, rows, sport):
    """rating -> normalized_time, against the stored normalized_time.
    Pure pool mean and engine scale."""
    errs = []
    for r in rows:
        got = cv._norm_from_rating(float(r["speed_rating"]), r["pool"], sport=sport)
        if not got or got <= 0:
            continue
        want = float(r["normalized_time"])
        errs.append(100.0 * (got - want) / want)
    return _stats(errs)


def halfB(cv, rows, sport):
    """raw time -> normalized_time, against the stored normalized_time.
    The distance curve, the difficulty and the sport gain.

    ⚠ AT THE ROW'S OWN DISTANCE, and with the display difficulty, which is
      what the page uses when no venue is named. A residual here is the
      curve disagreeing with what the engine normalised at."""
    errs = []
    for r in rows:
        got = cv._norm_from_time(float(r["time_seconds"]), float(r["distance"]),
                                 r["pool"], sport=sport, chosen=None)
        if not got or got <= 0:
            continue
        want = float(r["normalized_time"])
        errs.append(100.0 * (got - want) / want)
    return _stats(errs)


def describe(label, st, fast_word="FAST"):
    if st is None:
        print(f"    {label:<28} nothing could be converted")
        return
    way = "SLOW" if st["median"] > 0 else fast_word
    print(f"    {label:<28} median {st['median']:+7.3f}%  ({way})   "
          f"IQR {st['p25']:+.3f}..{st['p75']:+.3f}   "
          f"mean |err| {st['mean_abs']:.3f}%   n={st['n']:,}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="conversions vs the stored ratings")
    ap.add_argument("--sport", default="both", choices=("XC", "TF", "both"))
    ap.add_argument("--n", type=int, default=500, help="rows sampled per sport")
    ap.add_argument("--pool", default="hs_m", help="rating pool, '' for any")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--min-time", type=float, default=60.0)
    args = ap.parse_args(argv)

    pool = args.pool or None
    sports = ("XC", "TF") if args.sport == "both" else (args.sport,)

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT count(*) AS n FROM sport_gain")
            n_gain = cur.fetchone()["n"]
            by_sport = {s: sample(cur, args.n, s, pool, f"{args.year}-01-01",
                                  args.min_time) for s in sports}

    gain_env = os.environ.get("XCP_CONVERT_SPORT_GAIN")
    print(f"\nsport_gain: {n_gain:,} rows "
          f"({'the go-live SHIFTED the track rows' if n_gain else 'no shift applied'})"
          f"{'  [XCP_CONVERT_SPORT_GAIN=' + gain_env + ']' if gain_env else ''}")
    print(f"pool {pool or 'any'}, {args.year} onwards\n")

    import conversions as cv
    worst = None
    for s in sports:
        rows = by_sport[s]
        print(f"  {s}  ({len(rows):,} rows)")
        if not rows:
            print("    nothing sampled -- widen --year, or clear --pool\n")
            continue
        a, b = halfA(cv, rows, s), halfB(cv, rows, s)
        describe("A  rating -> normalized", a, fast_word="FAST (rating reads high)")
        describe("B  time   -> normalized", b)
        print()
        for name, st in (("A", a), ("B", b)):
            if st and (worst is None or st["mean_abs"] > worst[2]):
                worst = (s, name, st["mean_abs"])

    print("  reading it:")
    print("    A off, B clean   -> the page's pool mean / engine scale for that pool.")
    print("    A clean, B off   -> the distance curve or the difficulty; an engine")
    print("                        job, not a page one.")
    print("    both off         -> start with A; B is measured through it.")
    print("    both under ~0.3% -> the page and the engine agree; anything still")
    print("                        wrong on screen is wrong in the ratings too.")
    if worst:
        print(f"\n  largest disagreement: {worst[0]} half {worst[1]}, "
              f"mean |err| {worst[2]:.3f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
