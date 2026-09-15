"""diag_conversion_gain.py -- does the conversions page agree with the
ratings the engine actually stored, for TRACK rows?

    /srv/venv/bin/python scripts/diag_conversion_gain.py
    /srv/venv/bin/python scripts/diag_conversion_gain.py --n 600 --pool hs_m --year 2025

★ WHY THIS EXISTS. joint_golive can shift every track row by a per-band
  or per-level "winter gain" (issue 194): it adds the shift into the row's
  applied effect, so the STORED speed_rating carries it, and it writes the
  same shift to the sport_gain table. conversions.venueEffect adds the same
  shift back, so the page and the engine sit on one scale for track.

  When the two disagree the page is quietly wrong by exactly that shift --
  the owner's report on 2026-09-15 was "when we changed tf to get a sports
  gain it made the conversions too fast", and issue 306 on 2026-09-08 was
  the SAME disagreement pointing the other way. Neither is visible in any
  test: the round trip still closes, because both legs of a conversion
  carry the same error and it cancels. It only shows against the engine's
  own stored number, which is what this measures.

★ WHAT IT DOES. Samples real rated track rows, and for each one asks the
  conversions code the question the page asks -- "what time is this rating
  worth, at this row's own distance?" -- then compares that with the time
  the athlete actually ran. It runs the comparison BOTH ways, with the
  sport gain applied and without, and says which is closer.

  A row's own distance is used on purpose: any error in the distance curve
  cancels, leaving the sport gain as the thing being measured.

! READ THE SIGN. "conversions read FAST" means the page turns the stored
  rating into a time quicker than the athlete ran, which is the complaint
  this was written for.
"""
import argparse
import math
import os
import statistics
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

import psycopg2.extras                                   # noqa: E402

from database import getConn                             # noqa: E402

SAMPLE_SQL = """
    SELECT r.speed_rating, r.time_seconds, m.distance_meters, r.person_id
    FROM   results_tf r
    JOIN   meets_tf   m ON m.meet_id = r.meet_id
    WHERE  r.speed_rating IS NOT NULL
      AND  r.time_seconds  > %(min_time)s
      AND  m.distance_meters IS NOT NULL
      AND  m.distance_meters BETWEEN 800 AND 5000
      AND  r.date >= %(since)s
      {pool_clause}
    ORDER BY random()
    LIMIT %(n)s
"""


def sample(cur, n, pool, since, min_time):
    """n random rated track rows, newest seasons, at real distances."""
    clause = ""
    params = {"n": n, "since": since, "min_time": min_time}
    if pool:
        # the rating pool is stamped on ranking_results, so join through it
        clause = ("AND EXISTS (SELECT 1 FROM ranking_results rr "
                  "WHERE rr.result_id = r.result_id AND rr.pool = %(pool)s)")
        params["pool"] = pool
    cur.execute(SAMPLE_SQL.format(pool_clause=clause), params)
    return cur.fetchall()


def roundTrip(cv, rating, distance, pool):
    """The time the page would show for this rating at this distance, or
    None when the scale for this pool is not loaded."""
    norm = cv._norm_from_rating(rating, pool, sport="TF")
    if norm is None:
        return None
    ctx = {"distance": float(distance), "pool": pool, "sport": "TF",
           "difficulty": 0.0}
    return cv.normalized_to_time(norm, ctx)


def measure(rows, pool, want_gain):
    """Median and quartile signed error, in percent of the real time.
    Positive = the page reads SLOW, negative = the page reads FAST."""
    # ⚠ THE MODULE CACHES THE TABLE AND THE ENV IS READ PER CALL, so the
    #   switch below is enough -- but the caches must not be rebuilt
    #   between the two measurements or they are not comparable.
    os.environ["XCP_CONVERT_SPORT_GAIN"] = "1" if want_gain else "0"
    import conversions as cv
    errs = []
    for r in rows:
        got = roundTrip(cv, float(r["speed_rating"]), r["distance_meters"], pool)
        if not got or got <= 0:
            continue
        errs.append(100.0 * (got - float(r["time_seconds"])) / float(r["time_seconds"]))
    if not errs:
        return None
    errs.sort()
    return {"n": len(errs), "median": statistics.median(errs),
            "p25": errs[len(errs) // 4], "p75": errs[3 * len(errs) // 4],
            "mean_abs": statistics.fmean(abs(e) for e in errs)}


def describe(label, st):
    if st is None:
        print(f"  {label:<18} no rows could be converted")
        return
    way = "SLOW" if st["median"] > 0 else "FAST"
    print(f"  {label:<18} median {st['median']:+7.3f}%  ({way})   "
          f"IQR {st['p25']:+.3f}..{st['p75']:+.3f}   "
          f"mean |err| {st['mean_abs']:.3f}%   n={st['n']:,}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--n", type=int, default=400, help="rows to sample")
    ap.add_argument("--pool", default="hs_m", help="rating pool, or '' for any")
    ap.add_argument("--year", type=int, default=2025, help="rows from this season on")
    ap.add_argument("--min-time", type=float, default=60.0)
    args = ap.parse_args(argv)

    pool = args.pool or None
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT count(*) AS n FROM sport_gain")
            n_gain = cur.fetchone()["n"]
            rows = sample(cur, args.n, pool, f"{args.year}-01-01", args.min_time)

    print(f"\nsport_gain table: {n_gain:,} rows "
          f"({'a shift WAS applied by the go-live' if n_gain else 'NO shift was applied'})")
    print(f"sampled {len(rows):,} rated track rows, pool {pool or 'any'}, "
          f"{args.year} onwards\n")
    if not rows:
        print("  nothing to measure -- widen --year or clear --pool")
        return 1

    print("the page's time for the stored rating, against the time actually run:")
    with_gain = measure(rows, pool or "hs_m", True)
    without = measure(rows, pool or "hs_m", False)
    describe("with sport gain", with_gain)
    describe("without", without)

    print()
    if with_gain and without:
        better = "with" if with_gain["mean_abs"] < without["mean_abs"] else "without"
        print(f"  -> the stored ratings agree with conversions {better.upper()} the "
              f"sport gain applied.")
        if better == "with":
            print("     That is the default now (the table is non-empty, so the "
                  "rows carry the shift). Nothing to change.")
        else:
            print("     Set XCP_CONVERT_SPORT_GAIN=0 in /etc/xc-predictor.env and "
                  "restart, then open an issue: the go-live wrote a sport_gain "
                  "table whose shift its own rows do not carry, which is the "
                  "issue-306 state and a bug in the run rather than in the page.")
    # a residual of a few tenths is the distance curve and the tilt; a
    # residual near the size of the shift is the thing this looks for
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
