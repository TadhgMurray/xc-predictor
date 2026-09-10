#!/usr/bin/env python3
"""
tf_event_bias.py -- is the TRACK distance curve wrong? Does the same
athlete come out faster at 800 than at 10,000?

    scripts/tf_event_bias.py                   # every pool
    scripts/tf_event_bias.py --pool college_m
    scripts/tf_event_bias.py --pct 25          # more athletes, slower

Run from the PROJECT ROOT. READ-ONLY: one UNLOGGED scratch table inside a
transaction that is rolled back.

★ THE COMPLAINT (owner: "800/1600 still overrated, 10k under" -- and,
  2026-09-10, "800/10k are not reliability and are track things"). Right:
  this is not a venue question and no amount of shrinking course
  difficulty touches it. It is the distance normalisation inside track.

★ WHY THIS ONE IS CLEANLY IDENTIFIED, unlike almost everything else here.
  normalized_time converts a race to the pool's anchor distance, so if the
  curve is right it carries NO memory of which event was run. An athlete
  who races 800 and 5000 in the same season has one fitness; both races
  should normalise to the same number. Any systematic difference is the
  curve, and it is measured WITHIN one athlete-season -- no venue, no
  course difficulty, no sport gap, nothing to confound it.

  The benchmark is leave-own-event-out: an 800 is judged against that
  athlete's races at OTHER distances, so the 800s cannot define their own
  yardstick.

⚠ THE SIGN. normalized_time is a TIME, so smaller is better and an
  OVERRATED event has a normalized_time that is too SMALL -- a NEGATIVE
  residual. mean_pct below zero means that event is rated too fast. The
  printout says it in words.

! WHAT IS EXCLUDED AND WHY. Steeples, walks, hurdles and relays are not
  the flat event of their metres (the same rule speed_ratings_db applies),
  and indoor is kept separate from outdoor because a 200m banked oval is
  not a 400m one. Only athlete-seasons that raced at least two DIFFERENT
  distances count -- one distance carries no evidence about a curve.
"""

import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

_SCRATCH = "tfe_rows"

# ★ A CONSTANT, NOT A LITERAL IN THE SQL, AND THAT IS THE POINT. `date` is
#   TEXT on results_tf, so EXTRACT() cannot be used and a malformed row
#   would kill the cast -- hence the ISO guard. But the guard contains
#   {4} and {2}, and these queries are variously f-strings, .format()
#   templates and plain strings: doubling the braces is right in two of
#   those and WRONG in the third, where it renders a literal {{4}} that
#   matches nothing and silently returns zero rows. Substituting a
#   constant is correct in all three, because the value's braces are never
#   re-scanned.
_ISO_DATE = "r.date::text ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'"

# The same metres-from-event-name rule speed_ratings_db uses, so this
# measures the distance the ENGINE thinks was run, not a second opinion.
_EV = ("CASE WHEN r.event_short IS NULL THEN NULL "
       "WHEN lower(r.event_short) ~ '(steeple|walk|hurdle|relay)' THEN NULL "
       # ⚠ '%%mile%%', NOT '%mile%'. This SQL is executed WITH parameters,
       #   so psycopg2 reads a lone % as a placeholder and dies with
       #   "dict is not a sequence" -- a message that names nothing.
       "WHEN lower(r.event_short) LIKE '%%mile%%' "
       "THEN COALESCE(substring(regexp_replace(r.event_short, ',', '', 'g') "
       "from '[0-9]+(?:\\.[0-9]+)?')::real, 1) * 1609.34 "
       "WHEN substring(regexp_replace(r.event_short, ',', '', 'g') "
       "from '[0-9]+(?:\\.[0-9]+)?')::real IS NULL THEN NULL "
       "WHEN substring(regexp_replace(r.event_short, ',', '', 'g') "
       "from '[0-9]+(?:\\.[0-9]+)?')::real < 100 "
       "THEN substring(regexp_replace(r.event_short, ',', '', 'g') "
       "from '[0-9]+(?:\\.[0-9]+)?')::real * 1000 "
       "ELSE substring(regexp_replace(r.event_short, ',', '', 'g') "
       "from '[0-9]+(?:\\.[0-9]+)?')::real END")


# ★★ TWO THINGS TO MEASURE, AND THEY ANSWER DIFFERENT QUESTIONS.
#
#    norm    ln(normalized_time) -- the solve's INPUT. A tilt here is the
#            distance normalisation being wrong.
#
#    rating  -ln(speed_rating) -- the solve's OUTPUT, which is what the
#            board shows. Negated because a rating is better when LARGER,
#            so this keeps the sign convention: negative means overrated.
#
#    The distinction matters because the joint solve ALREADY fits a track
#    distance offset per (pool, 100m bucket, rating band) with a 3 per cent
#    prior -- joint_solve.DIST_PRIOR_SD, DIST_BANDS. A bias in `norm` that
#    is absent from `rating` has already been corrected downstream and
#    needs no fix at all; one that survives into `rating` is reaching the
#    board and does.
_MEASURES = {
    "norm": ("ln(r.normalized_time)",
             "r.normalized_time IS NOT NULL AND r.normalized_time > 0"),
    "rating": ("-ln(r.speed_rating)",
               "r.speed_rating IS NOT NULL AND r.speed_rating > 0"),
}


def _passA(cur, pool, measure="norm"):
    have = set()
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='meets_tf'""")
    have = {r[0] for r in cur.fetchall()}
    dist = (f"COALESCE(m.distance_meters::real, {_EV})"
            if "distance_meters" in have else _EV)
    indoor = ("COALESCE(m.is_indoor, 0)" if "is_indoor" in have else "0")
    join = ("LEFT JOIN meets_tf m ON m.meet_id = r.meet_id "
            "AND m.div_id = r.div_id")
    expr, nonnull = _MEASURES[measure]
    return f"""
        SELECT r.person_id,
               (CASE WHEN substr(r.date::text, 6, 2)::int >= 8
                     THEN substr(r.date::text, 1, 4)::int
                     ELSE substr(r.date::text, 1, 4)::int - 1 END) AS season,
               {dist}::float                              AS dist,
               {indoor}::int                              AS indoor,
               {expr}                                     AS lnt,
               r.rating_pool                              AS pool
        FROM   results_tf r
        {join}
        WHERE  {nonnull}
          AND  r.rating_pool IS NOT NULL
          AND  r.date IS NOT NULL
          -- ⚠ date IS TEXT on results_tf, so EXTRACT() cannot be
          --   used and a bad row would kill the cast. The regex
          --   is the guard: only ISO-shaped dates get parsed.
          AND  {_ISO_DATE}
          AND  {dist} BETWEEN 400 AND 15000
          AND  abs(mod(hashint8(r.person_id::bigint), 10000)) < %(cut)s
          {"AND r.rating_pool = %(pool)s" if pool else ""}
    """


# ★ LEAVE-OWN-EVENT-OUT. The benchmark for an athlete's 800 is the mean of
#   their races at every OTHER distance, so an athlete who only ever runs
#   800s contributes nothing (n_all > n_own fails) rather than defining
#   the 800 as correct by circular reasoning.
_BIAS = f"""
WITH per_season AS (
    SELECT person_id, season, indoor, count(*) AS n_all, sum(lnt) AS s_all
    FROM   {_SCRATCH} GROUP BY 1, 2, 3
), per_dist AS (
    SELECT person_id, season, indoor, dist,
           count(*) AS n_own, sum(lnt) AS s_own
    FROM   {_SCRATCH} GROUP BY 1, 2, 3, 4
), per_lz AS (
    SELECT person_id, season, indoor, sum(ln(dist)) AS s_lz
    FROM   tfe_rows GROUP BY 1, 2, 3
), resid AS (
    -- ⚠ SCALED BACK TO A DEVIATION FROM THE SEASON MEAN. Leaving a race
    --   out of its own benchmark INFLATES the residual by n/(n-k): with
    --   three races, x - mean(other two) is 1.5x the true deviation from
    --   the mean of all three. Unscaled, a planted 7.58-point tilt read
    --   9.54. The factor (n_all - n_own)/n_all undoes it exactly, so
    --   mean_pct is "percent off this athlete's own season", which is the
    --   number the rating is actually wrong by.
    SELECT b.dist, b.indoor, b.pool,
           (b.lnt - (p.s_all - d.s_own) / (p.n_all - d.n_own))
             * ((p.n_all - d.n_own)::float / p.n_all)         AS res,
           -- the same deviation, for the DISTANCE: ln d minus this
           -- athlete-season's own mean ln d. Regressing res on this
           -- recovers the curve error per e-fold regardless of which
           -- events the athlete happened to enter.
           ln(b.dist) - z.s_lz / p.n_all                      AS lz
    FROM   {_SCRATCH} b
    JOIN   per_season p ON p.person_id = b.person_id
                       AND p.season = b.season AND p.indoor = b.indoor
    JOIN   per_dist  d ON d.person_id = b.person_id
                       AND d.season = b.season AND d.indoor = b.indoor
                       AND d.dist = b.dist
    JOIN   per_lz    z ON z.person_id = b.person_id
                       AND z.season = b.season AND z.indoor = b.indoor
    WHERE  p.n_all > d.n_own
)
SELECT {{grp}}                                          AS grp,
       count(*)                                         AS n,
       round((100 * avg(res))::numeric, 2)              AS mean_pct,
       round((100 * stddev_samp(res)
              / sqrt(count(*)))::numeric, 3)            AS se_pct
FROM   resid
{{where}}
GROUP  BY 1
HAVING count(*) >= %(min_n)s
ORDER  BY 1
"""

# The standard track events, named, because "1609" on a row is a mile and
# a reader should not have to know that.
_BAND = """CASE WHEN dist < 900               THEN '800'
                WHEN dist < 1100              THEN '1000'
                WHEN dist < 1300              THEN '1200'
                WHEN dist < 1550              THEN '1500'
                WHEN dist < 1700              THEN '1600/Mile'
                WHEN dist < 2200              THEN '2000'
                WHEN dist < 2700              THEN '2400'
                WHEN dist < 3100              THEN '3000'
                WHEN dist < 3400              THEN '3200/2Mile'
                WHEN dist < 4500              THEN '4000'
                WHEN dist < 5500              THEN '5000'
                WHEN dist < 8500              THEN '8000'
                ELSE                               '10000' END"""


# ★★ THE MIX-INDEPENDENT NUMBER, AND THE ONE TO ACT ON. The per-band means
#    are a deviation from each athlete's own season, so how large they look
#    depends on which events that athlete entered -- on a fixture with a
#    planted 0.030 per e-fold, the 800-to-10000 band gap read 6.36 points
#    where the true span is 7.58. Regressing the residual on the athlete's
#    own centred ln(distance) removes the mix entirely and recovers 0.030.
_SLOPE = _BIAS.replace("""SELECT {grp}                                          AS grp,
       count(*)                                         AS n,
       round((100 * avg(res))::numeric, 2)              AS mean_pct,
       round((100 * stddev_samp(res)
              / sqrt(count(*)))::numeric, 3)            AS se_pct""",
"""SELECT {grp}                                          AS grp,
       count(*)                                         AS n,
       round(regr_slope(res, lz)::numeric, 5)           AS per_efold,
       round((100 * regr_slope(res, lz))::numeric, 2)   AS pct_per_efold""")


def _rows(cur, sql, args):
    cur.execute(sql, args)
    return [d[0] for d in cur.description], cur.fetchall()


def _table(cols, rows, indent="    "):
    if not rows:
        print(f"{indent}(no rows)")
        return
    body = [[("" if v is None else str(v)) for v in r] for r in rows]
    w = [max(len(c), *(len(b[i]) for b in body)) for i, c in enumerate(cols)]
    print(indent + "  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    print(indent + "  ".join("-" * w[i] for i in range(len(cols))))
    for b in body:
        print(indent + "  ".join(b[i].ljust(w[i]) for i in range(len(b))))


_ORDER = ["800", "1000", "1200", "1500", "1600/Mile", "2000", "2400",
          "3000", "3200/2Mile", "4000", "5000", "8000", "10000"]


def _verdict(rows):
    """rows: [(band, n, mean_pct, se_pct)] -- say it in words, with the
    sign spelled out, because a log residual on a TIME reads backwards."""
    got = {r[0]: (float(r[2]), float(r[3] or 0)) for r in rows}
    ordered = [(b, got[b]) for b in _ORDER if b in got]
    if len(ordered) < 3:
        print("    (too few events to judge)")
        return
    print("\n    mean_pct BELOW zero  = that event normalises TOO FAST, "
          "i.e. OVERRATED")
    print("    mean_pct ABOVE zero  = too slow, i.e. UNDERRATED")
    print("    se_pct is the standard error; ignore anything inside 2x it.\n")
    bad = [(b, m, se) for b, (m, se) in ordered if abs(m) > 2 * max(se, 1e-9)
           and abs(m) >= 0.25]
    if not bad:
        print("    => no event is off by as much as 0.25%. The track "
              "distance curve\n       is doing its job.")
        return
    over = [(b, m) for b, m, _ in bad if m < 0]
    under = [(b, m) for b, m, _ in bad if m > 0]
    if over:
        print("    => OVERRATED (too fast): "
              + ", ".join(f"{b} {m:+.2f}%" for b, m in over))
    if under:
        print("    => UNDERRATED (too slow): "
              + ", ".join(f"{b} {m:+.2f}%" for b, m in under))
    first, last = ordered[0], ordered[-1]
    tilt = last[1][0] - first[1][0]
    print(f"\n    The tilt from {first[0]} to {last[0]} is {tilt:+.2f} "
          f"percentage points.")
    if tilt > 0.5:
        print("    Short events are rated too fast RELATIVE to long ones: "
              "the curve\n    does not charge enough for distance. The "
              "exponent is too LOW.")
    elif tilt < -0.5:
        print("    Long events are rated too fast relative to short ones: "
              "the exponent\n    is too HIGH.")
    else:
        print("    No consistent tilt -- whatever is wrong is event by "
              "event, not a\n    curve that is the wrong shape.")


def main():
    ap = argparse.ArgumentParser(
        description="Does the same athlete rate differently at 800 and "
                    "10,000?")
    ap.add_argument("--on", choices=["norm", "rating", "both"],
                    default="both",
                    help="measure the solve's INPUT (normalized_time), its "
                         "OUTPUT (speed_rating, what the board shows), or "
                         "both. A bias present in norm and absent from "
                         "rating is already fixed by the solve's distance "
                         "offsets and needs no change")
    ap.add_argument("--pool", help="one rating pool, e.g. college_m")
    ap.add_argument("--pct", type=float, default=10.0,
                    help="percent of ATHLETES to sample (default 10)")
    ap.add_argument("--min-n", type=int, default=200)
    ap.add_argument("--indoor", choices=["out", "in", "both"], default="out",
                    help="a 200m banked oval is not a 400m one; default "
                         "outdoor only")
    ap.add_argument("--work-mem", default="256MB")
    ap.add_argument("--timeout", default="30min")
    args = ap.parse_args()

    cut = int(round(args.pct * 100))
    from database import getConn
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(f"SET LOCAL work_mem = '{args.work_mem}'")
        cur.execute(f"SET LOCAL statement_timeout = '{args.timeout}'")
        cur.execute("SET LOCAL max_parallel_workers_per_gather = 2")
        try:
          for measure in (["norm", "rating"] if args.on == "both"
                            else [args.on]):
              print("\n" + "#" * 70)
              print(f"# MEASURING: {measure}  "
                    + ("(the solve's INPUT -- a tilt here is the distance "
                       "normalisation)" if measure == "norm"
                       else "(the solve's OUTPUT -- what the board shows)"))
              print("#" * 70)
              t0 = time.time()
              cur.execute(f"DROP TABLE IF EXISTS {_SCRATCH}")
              cur.execute(f"CREATE UNLOGGED TABLE {_SCRATCH} AS "
                          + _passA(cur, args.pool, measure),
                          {"cut": cut, "pool": args.pool})
              cur.execute(f"SELECT count(*) FROM {_SCRATCH}")
              n = cur.fetchone()[0]
              print(f"\n{n:,} track rows for {args.pct}% of athletes "
                    f"({time.time() - t0:.0f}s)")
              if n == 0:
                  return 1
              cur.execute(f"CREATE INDEX ON {_SCRATCH} "
                          f"(person_id, season, indoor)")
              where = {"out": "WHERE indoor = 0", "in": "WHERE indoor = 1",
                       "both": ""}[args.indoor]

              print("\n" + "=" * 70)
              print("TRACK EVENT BIAS, WITHIN THE ATHLETE-SEASON")
              print("  each race judged against that athlete's races at OTHER "
                    "distances")
              print("=" * 70)
              cols, rows = _rows(cur, _BIAS.format(grp=_BAND, where=where),
                                 {"min_n": args.min_n})
              rows = sorted(rows, key=lambda r: _ORDER.index(r[0])
                            if r[0] in _ORDER else 99)
              _table(cols, rows)
              _verdict(rows)

              print("\n" + "=" * 70)
              print("THE CURVE ERROR ITSELF, PER e-FOLD OF DISTANCE")
              print("  regression of the residual on the athlete's own centred")
              print("  ln(distance). Independent of which events each athlete")
              print("  entered, so THIS is the number to act on.")
              print("=" * 70)
              cols, srows = _rows(cur, _SLOPE.format(grp="'all track'",
                                                     where=where),
                                  {"min_n": args.min_n})
              _table(cols, srows)
              if srows:
                  k = float(srows[0][2])
                  print(f"\n    A 5000 is {abs(100 * k * 1.83):.2f}% "
                        f"{'slower' if k > 0 else 'faster'} than an 800 says it "
                        f"should be\n    (ln 5000 - ln 800 = 1.83 e-folds).")
                  if abs(k) < 0.002:
                      print("    => the track distance curve is FINE.")
                  elif k > 0:
                      print("    => the exponent is TOO LOW: the curve does not "
                            "charge enough for\n       distance, so short "
                            "events rate too fast.")
                  else:
                      print("    => the exponent is TOO HIGH: long events rate "
                            "too fast.")
              cols, srows = _rows(cur, _SLOPE.format(grp="pool", where=where),
                                  {"min_n": args.min_n})
              print()
              _table(cols, srows)

              print("\n" + "=" * 70)
              print("THE SAME, BY POOL -- a curve can be right for college "
                    "and wrong for")
              print("middle school, and one number over both would hide it")
              print("=" * 70)
              grp = f"pool || ' ' || {_BAND}"
              cols, prows = _rows(cur, _BIAS.format(grp=grp, where=where),
                                  {"min_n": args.min_n})
              _table(cols, prows)
              cur.execute(f"DROP TABLE IF EXISTS {_SCRATCH}")
        finally:
            conn.rollback()
            cur.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
