#!/usr/bin/env python3
"""
difficulty_reliability.py -- how much of a course's difficulty is REAL, and
how much is the noise of the days it happened to be raced on?

    scripts/difficulty_reliability.py                # both sports
    scripts/difficulty_reliability.py --sport TF
    scripts/difficulty_reliability.py --pct 25       # more athletes, slower

Run from the PROJECT ROOT. READ-ONLY: one UNLOGGED scratch table inside a
transaction that is rolled back, so nothing is left behind.

★ THE QUESTION (owner, 2026-09-10: "I do wonder if we should shrink tf
  variance. Could there be a good diagnostics for that?"). Track difficulty
  measures sd 0.557% with p99 1.884%. A track is a flat 400m oval, so is
  that spread REAL -- altitude, banking, wind, surface -- or is it the
  residue of which days each oval happened to host?

  It is the same question as the owner's other one: "Mt. SAC and crystal
  springs keep ping ponging between 9% and 5%". A number that will not
  reproduce cannot be measured harder; it has to be shrunk.

★ THE TEST. Split each venue's races into two halves, ALTERNATING BY DATE
  so both halves span the same years, and measure the venue's difficulty
  from each half independently. Then correlate the two halves across
  venues.

    halves agree      the spread is real, and shrinking it throws away
                      signal
    halves disagree   the spread is the days, not the venue, and every
                      point of it is noise on the board

★ AND IT HANDS BACK THE SHRINKAGE FACTOR, WHICH IS THE POINT. Split-half
  correlation r underestimates the reliability of the FULL set of races
  (each half has half the data), and Spearman-Brown corrects it:

      r_full = 2r / (1 + r)

  r_full IS the fraction of the observed variance that is signal, so:

      true sd        = observed sd * sqrt(r_full)
      shrunk value   = mean + r_full * (observed - mean)          [Kelley]

  That is not a tuning knob, it is the measured answer. If TF comes back at
  r_full = 0.3, then 70% of that 0.557% is noise and the honest track
  spread is 0.557 * sqrt(0.3) = 0.30%.

⚠ WHAT THIS IS NOT. It is not the difficulty the SOLVER produced -- it
  re-measures each venue from the rows, using the same leave-own-venue-out
  benchmark as scripts/distance_between_venues.py (an athlete's races
  ELSEWHERE, which this venue cannot move). Two consequences: the numbers
  will not equal course_difficulties, and a venue whose athletes never race
  anywhere else drops out entirely rather than being guessed at.

! RESOURCE GUARDS: athlete-hash sampling so a season is in or out whole,
  one pass, no LATERAL, work_mem and statement_timeout capped, parallelism
  held to 2.
"""

import argparse
import math
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

_SCRATCH = "rel_rows"


def _cols(cur, table):
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s""",
                (table,))
    return {r[0] for r in cur.fetchall()}


# ★ THE TWO SPORTS ARE KEYED DIFFERENTLY AND THE QUERIES SAY SO. meets_tf
#   has no course_name and no distance -- a track venue is a location_id
#   plus the indoor flag, exactly as the 'TF:loc:<id>:<in|out>' cell key is
#   built. Assuming one shape for both is what crashed shrinkage_audit.
def _passA(cur, sport):
    if sport == "XC":
        have = _cols(cur, "meets")
        if "course_name" not in have:
            return None, "meets has no course_name"
        return ("""
            SELECT r.person_id,
                   (CASE WHEN EXTRACT(MONTH FROM r.date) >= 8
                         THEN EXTRACT(YEAR FROM r.date)
                         ELSE EXTRACT(YEAR FROM r.date) - 1 END)::int AS season,
                   m.course_name                                AS venue,
                   (r.meet_id::text || ':' || r.div_id::text
                    || ':' || COALESCE(r.source, ''))           AS race,
                   r.date                                       AS d,
                   ln(r.normalized_time)                        AS lnt
            FROM   results r
            JOIN   meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
                           AND m.source = r.source
            WHERE  r.normalized_time IS NOT NULL AND r.normalized_time > 0
              AND  m.course_name IS NOT NULL
              AND  r.date IS NOT NULL
              AND  abs(mod(hashint8(r.person_id::bigint), 10000)) < %(cut)s
        """, None)
    have = _cols(cur, "meets_tf")
    if "location_id" not in have:
        return None, "meets_tf has no location_id"
    indoor = ("CASE WHEN COALESCE(m.is_indoor, 0) = 1 THEN 'in' ELSE 'out' END"
              if "is_indoor" in have else "'out'")
    return ("""
        SELECT r.person_id,
               (CASE WHEN EXTRACT(MONTH FROM r.date) >= 8
                     THEN EXTRACT(YEAR FROM r.date)
                     ELSE EXTRACT(YEAR FROM r.date) - 1 END)::int AS season,
               ('loc:' || m.location_id::text || ':' || {ind})    AS venue,
               (r.meet_id::text || ':' || r.div_id::text)         AS race,
               r.date                                             AS d,
               ln(r.normalized_time)                              AS lnt
        FROM   results_tf r
        JOIN   meets_tf m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
        WHERE  r.normalized_time IS NOT NULL AND r.normalized_time > 0
          AND  m.location_id IS NOT NULL
          AND  r.date IS NOT NULL
          AND  abs(mod(hashint8(r.person_id::bigint), 10000)) < %(cut)s
    """.format(ind=indoor), None)


# ★ THE HALVES, AND WHY ALTERNATING BY DATE. Splitting a venue's races into
#   "first half of history" and "second half" would confound reliability
#   with drift -- a course that was re-routed in 2019 would read as
#   unreliable when it is simply two different courses. Alternating by date
#   rank gives both halves the same span, so what is left is noise.
_SPLIT = f"""
WITH per_season AS (
    SELECT person_id, season, count(*) AS n_all, sum(lnt) AS s_all
    FROM   {_SCRATCH} GROUP BY 1, 2
), per_venue AS (
    SELECT person_id, season, venue, count(*) AS n_own, sum(lnt) AS s_own
    FROM   {_SCRATCH} GROUP BY 1, 2, 3
), resid AS (
    SELECT b.venue, b.race,
           b.lnt - (p.s_all - v.s_own) / (p.n_all - v.n_own) AS res
    FROM   {_SCRATCH} b
    JOIN   per_season p ON p.person_id = b.person_id AND p.season = b.season
    JOIN   per_venue  v ON v.person_id = b.person_id AND v.season = b.season
                       AND v.venue = b.venue
    WHERE  p.n_all > v.n_own
), per_race AS (
    SELECT venue, race, count(*) AS n, avg(res) AS race_res,
           min(d) AS first_day
    FROM   resid
    JOIN   (SELECT DISTINCT venue AS v2, race AS r2, min(d) AS d
            FROM {_SCRATCH} GROUP BY 1, 2) dd
      ON   dd.v2 = resid.venue AND dd.r2 = resid.race
    GROUP  BY 1, 2
), halved AS (
    SELECT venue, race, n, race_res,
           (row_number() OVER (PARTITION BY venue ORDER BY first_day, race)
            %% 2)                                          AS half
    FROM   per_race
), agg AS (
    SELECT venue, half, count(*) AS races, sum(n) AS rows_n,
           -- weighted by finishers, so a 200-runner race is not outvoted
           sum(race_res * n) / sum(n)                      AS diff
    FROM   halved GROUP BY 1, 2
), paired AS (
    SELECT a.venue,
           a.races + b.races                               AS races,
           a.rows_n + b.rows_n                             AS rows_n,
           a.diff                                          AS d0,
           b.diff                                          AS d1
    FROM   agg a JOIN agg b ON b.venue = a.venue AND a.half = 0 AND b.half = 1
    WHERE  a.races >= %(half_races)s AND b.races >= %(half_races)s
      AND  a.rows_n >= %(half_rows)s AND b.rows_n >= %(half_rows)s
)
SELECT * FROM paired
"""


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _sd(vs):
    n = len(vs)
    if n < 2:
        return 0.0
    m = sum(vs) / n
    return math.sqrt(sum((v - m) ** 2 for v in vs) / (n - 1))


def _report(rows, label, indent="  "):
    """rows: [(venue, races, rows_n, d0, d1)]"""
    if len(rows) < 3:
        print(f"{indent}{label}: only {len(rows)} venues, not enough to judge")
        return None
    d0 = [float(r[3]) for r in rows]
    d1 = [float(r[4]) for r in rows]
    r = _pearson(d0, d1)
    if r is None:
        print(f"{indent}{label}: no spread to correlate")
        return None
    # Spearman-Brown: each half holds half the races, so the split-half
    # correlation understates what the WHOLE set of races supports
    r_full = 2 * r / (1 + r) if r > -1 else 0.0
    r_full = max(0.0, min(1.0, r_full))
    obs = [(a + b) / 2 for a, b in zip(d0, d1)]
    sd_obs = _sd(obs)
    sd_true = sd_obs * math.sqrt(r_full)
    print(f"{indent}{label:<14} venues {len(rows):>6}  "
          f"split-half r {r:>6.3f}  reliability {r_full:>5.3f}  "
          f"observed sd {100 * sd_obs:>6.2f}%  real sd {100 * sd_true:>6.2f}%")
    return r_full


def _verdict(r_full, sport):
    if r_full is None:
        return
    print()
    keep = f"{r_full:.2f}"
    if r_full >= 0.8:
        print(f"  => {sport} difficulty is REAL. {100 * r_full:.0f}% of the "
              f"spread reproduces on\n     independent races; shrinking it "
              f"would throw away signal.")
    elif r_full >= 0.5:
        print(f"  => {sport} difficulty is MOSTLY real but noisy. Multiply "
              f"each venue's\n     deviation from the mean by {keep} "
              f"(Kelley) and the board stops\n     moving between runs "
              f"without losing the courses that differ.")
    else:
        print(f"  => {sport} difficulty is MOSTLY NOISE. Only "
              f"{100 * r_full:.0f}% of the spread\n     reproduces. A venue's "
              f"published deviation should be about {keep} of\n     what it "
              f"is now; the rest is which days it happened to host.")
    print(f"     Shrinkage factor to apply: {keep}  (true = mean + "
          f"{keep} x (observed - mean))")


def main():
    ap = argparse.ArgumentParser(
        description="How much of course difficulty reproduces on "
                    "independent races?")
    ap.add_argument("--sport", choices=["XC", "TF", "both"], default="both")
    ap.add_argument("--pct", type=float, default=10.0,
                    help="percent of ATHLETES to sample (default 10)")
    ap.add_argument("--half-races", type=int, default=2,
                    help="minimum races in EACH half (default 2)")
    ap.add_argument("--half-rows", type=int, default=30,
                    help="minimum finishers in EACH half (default 30)")
    ap.add_argument("--work-mem", default="256MB")
    ap.add_argument("--timeout", default="30min")
    args = ap.parse_args()

    cut = int(round(args.pct * 100))
    if cut <= 0:
        print("--pct must be above 0")
        return 2

    from database import getConn
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(f"SET LOCAL work_mem = '{args.work_mem}'")
        cur.execute(f"SET LOCAL statement_timeout = '{args.timeout}'")
        cur.execute("SET LOCAL max_parallel_workers_per_gather = 2")
        try:
            for sport in (["XC", "TF"] if args.sport == "both"
                          else [args.sport]):
                sql, why = _passA(cur, sport)
                if sql is None:
                    print(f"\n! cannot read {sport}: {why}")
                    continue
                print("\n" + "=" * 74)
                print(f"{sport}: DOES A VENUE'S DIFFICULTY REPRODUCE ON "
                      f"INDEPENDENT RACES?")
                print("=" * 74)
                t0 = time.time()
                cur.execute(f"DROP TABLE IF EXISTS {_SCRATCH}")
                cur.execute(f"CREATE UNLOGGED TABLE {_SCRATCH} AS {sql}",
                            {"cut": cut})
                cur.execute(f"SELECT count(*) FROM {_SCRATCH}")
                n = cur.fetchone()[0]
                print(f"  {n:,} rows for {args.pct}% of athletes "
                      f"({time.time() - t0:.0f}s)", flush=True)
                if n == 0:
                    continue
                cur.execute(f"CREATE INDEX ON {_SCRATCH} (person_id, season)")
                cur.execute(_SPLIT, {"half_races": args.half_races,
                                     "half_rows": args.half_rows})
                rows = cur.fetchall()
                print(f"  {len(rows):,} venues have {args.half_races}+ races "
                      f"and {args.half_rows}+ finishers in BOTH halves "
                      f"({time.time() - t0:.0f}s)\n")
                overall = _report(rows, "all venues")
                print()
                # ★ BY EVIDENCE, because that is where the shrinkage should
                #   differ: a venue raced twice should be trusted less than
                #   one raced eighty times, and this says by how much.
                for lo, hi, name in ((4, 5, "4-5 races"), (6, 10, "6-10"),
                                     (11, 25, "11-25"), (26, 60, "26-60"),
                                     (61, 10 ** 9, "61+")):
                    band = [r for r in rows if lo <= int(r[1]) <= hi]
                    _report(band, name)
                _verdict(overall, sport)
                cur.execute(f"DROP TABLE IF EXISTS {_SCRATCH}")
        finally:
            conn.rollback()          # takes the scratch table with it
            cur.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
