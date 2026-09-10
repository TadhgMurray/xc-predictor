#!/usr/bin/env python3
"""
season_reliability.py -- why is the top of the board 95% cross country when
track's mean rating is HIGHER?

    scripts/season_reliability.py                    # every pool
    scripts/season_reliability.py --pool college_m
    scripts/season_reliability.py --top 200 --pct 25

Run from the PROJECT ROOT. READ-ONLY: one UNLOGGED scratch table inside a
transaction that is rolled back.

★ THE FINDING THIS EXPLAINS (scripts/sport_gap_from_top.py, college_m):

      mean rating   XC 101.63   TF 104.32
      sd            XC  10.17   TF   7.61      (XC is 1.34x wider)
      top 200 is 95.0% XC
      balancing shift drifts 2.8x across depths -- 3.33 at the top 50,
      1.19 at the top 5000

  XC is NOT rated higher on average. Track's mean is higher. XC is rated
  WIDER, and the extremes of a wider distribution own the top of a board.
  That is why a single --sport-gap-delta cannot fix it: there is no offset
  to remove, and any number that balances one depth breaks another.

★ THE HYPOTHESIS, AND IT IS TESTABLE.

      var(observed) = var(true fitness) + var(measurement noise)

  Cross country measures fitness through mud, hills, weather, and a course
  difficulty that itself scatters by 4.24%. Track measures it on a flat
  400m oval whose difficulty scatters by 0.557%. If XC's extra width is
  NOISE rather than a genuinely wider spread of athletes, then the top of
  the board is a winner's curse: the XC seasons up there are the ones
  whose measurement error happened to be largest, not the fittest people.

★ THE TEST. Split each athlete-season's races into two halves, alternating
  by date, and take the season rating from each half independently.
  Correlate the halves within each (pool, sport). Spearman-Brown corrects
  for each half holding half the races:

      r_full = 2r / (1 + r)

  and then

      true sd = observed sd * sqrt(r_full)

  If XC's TRUE sd comes back close to TF's, the width is noise and the
  board is wrong. If XC's true sd really is 1.34x TF's, cross country
  genuinely spreads athletes further and the top 200 is telling the truth.

★ AND IT SIMULATES THE FIX. Section 3 rescales each athlete-season toward
  its own sport's mean and re-counts the top N. Unlike a gap shift this is
  not a free parameter -- the factor is measured.

⚠⚠ THE FACTOR IS sqrt(reliability), NOT reliability, AND THAT IS NOT A
   DETAIL. Kelley shrinkage (mean + r x deviation) gives the posterior
   MEAN, which is what you want for one athlete's best guess. But its
   spread is sqrt(r) x the true spread, so it UNDER-disperses -- and it
   under-disperses the noisier sport more. On a fixture with identical
   true spreads, XC reliability 0.507 and TF 0.997, shrinking by r took
   the top 200 from 89% XC straight past even to 15% XC. It replaced one
   lopsided board with the opposite one.

   Scaling by sqrt(r) instead puts BOTH sports on the true spread:

       adjusted sd = sqrt(r) x observed sd
                   = sqrt(r) x true sd / sqrt(r)
                   = true sd

   which is the whole point -- two sports measured with different amounts
   of noise, put back on one ruler. Section 3 prints both so the
   difference is visible rather than asserted.

⚠⚠ AND THE BOARD DOES NOT RANK ON A MEAN. athlete_season.mean_rating holds
   the athlete's 80th-PERCENTILE race, not their average one -- the column
   kept its old name (build_ranking_results._SEASON_Q, and the note above
   it). That choice is well argued: a mean rewards a thin championship-only
   season, and a quantile is invariant to how many times somebody raced.

   But a quantile is NOT invariant to how noisy each race is. p80 sits
   about 0.84 within-season standard deviations above the mean, so a sport
   whose races scatter more gets a bigger free uplift -- and cross country
   scatters more than track by construction (different courses, mud, hills,
   weather; track is the same oval). Section 4 measures that uplift per
   sport, because it is a cross-sport bias hiding inside a within-sport
   fix.

   This is not theoretical: ranking these same seasons by MEAN puts the top
   200 at 16.5% XC, and the board, ranking by p80, puts it at 95%.

! Season ratings in sections 1-3 are computed from the ROWS, not read from
  athlete_season, because the halves have to be computed the same way as
  the whole for the correlation to mean anything. Section 4 is where the
  board's own statistic is reproduced and compared.
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

_SCRATCH = "sr_rows"

# ! A CONSTANT, because `date` is TEXT on results_tf and this guard holds
#   {4}/{2}: doubling the braces is right in an f-string and wrong in a
#   plain one, and the wrong version renders a literal that matches
#   nothing and silently returns no rows. Substituting a value is correct
#   everywhere. See tests/test_tf_event_bias.py.
_ISO_DATE = "date::text ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'"

_SEASON = ("(CASE WHEN substr(race_date::text, 6, 2)::int >= 8 "
           "THEN substr(race_date::text, 1, 4)::int "
           "ELSE substr(race_date::text, 1, 4)::int - 1 END)")

# ⚠⚠ ranking_results, NOT results. athlete_season.mean_rating is
#    percentile_cont(0.80) over ranking_results -- the GATED set, after the
#    ceiling, the rowguard and the rest -- not over every rated row. Read
#    from `results` this script said the top 200 was 16.5 per cent XC while
#    the board said 95, and the whole difference was the gate. A diagnostic
#    that does not rank the rows the board ranks is measuring a different
#    board.
_PASS_A = f"""
CREATE UNLOGGED TABLE {_SCRATCH} AS
SELECT person_id, {_SEASON} AS season, pool,
       sport::text AS sport, race_date::text AS d,
       speed_rating::float AS rating
FROM   ranking_results
WHERE  speed_rating IS NOT NULL AND pool IS NOT NULL
  AND  race_date IS NOT NULL
  AND  abs(mod(hashint8(person_id::bigint), 10000)) < %(cut)s
"""

# ★ ALTERNATING BY DATE, not first-half/second-half of the season. An
#   athlete improves through a season, so a chronological split would read
#   as unreliable when it is simply improvement, and every sport would look
#   noisier than it is.
_HALVES = f"""
WITH ranked AS (
    SELECT person_id, season, pool, sport, rating,
           (row_number() OVER (PARTITION BY person_id, season, pool, sport
                               ORDER BY d, rating) %% 2)      AS half
    FROM   {_SCRATCH}
), agg AS (
    SELECT person_id, season, pool, sport, half,
           count(*) AS n, avg(rating) AS r
    FROM   ranked GROUP BY 1, 2, 3, 4, 5
)
SELECT a.pool, a.sport, a.n + b.n AS n_races, a.r AS r0, b.r AS r1
FROM   agg a
JOIN   agg b ON b.person_id = a.person_id AND b.season = a.season
            AND b.pool = a.pool AND b.sport = a.sport
            AND a.half = 0 AND b.half = 1
WHERE  a.n >= %(half_min)s AND b.n >= %(half_min)s
"""


# ★ THE BOARD'S OWN STATISTIC, per athlete-season, beside the mean. n is
#   kept because the uplift shrinks with races and the sports differ there
#   too.
_UPLIFT = f"""
SELECT pool, sport, count(*) AS n_races,
       avg(rating)                                          AS mean_r,
       percentile_cont(0.80) WITHIN GROUP (ORDER BY rating)  AS p80,
       coalesce(stddev_samp(rating), 0)                     AS sd_within
FROM   {_SCRATCH}
GROUP  BY person_id, season, pool, sport
HAVING count(*) >= %(half_min)s * 2
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


def _reliability(pairs):
    """pairs: [(r0, r1)] -> (r_full, observed_sd, true_sd, mean)"""
    d0 = [p[0] for p in pairs]
    d1 = [p[1] for p in pairs]
    r = _pearson(d0, d1)
    if r is None:
        return None
    r_full = max(0.0, min(1.0, 2 * r / (1 + r)))
    full = [(a + b) / 2 for a, b in zip(d0, d1)]
    sd = _sd(full)
    return r_full, sd, sd * math.sqrt(r_full), sum(full) / len(full)


def _mix(seasons, top):
    """seasons: [(rating, sport)] -> XC's share of the top `top`."""
    head = sorted(seasons, key=lambda t: -t[0])[:top]
    if not head:
        return None
    return sum(1 for _, s in head if s == "XC") / len(head)


def _report(pool, rows, top):
    by = {}
    for _, sport, n_races, r0, r1 in rows:
        by.setdefault(sport, []).append((float(r0), float(r1)))
    if len(by) < 2:
        print(f"\n{pool}: only one sport present, nothing to compare")
        return

    print(f"\n{'=' * 74}\n{pool}\n{'=' * 74}")
    print("1. IS THE EXTRA WIDTH REAL, OR IS IT MEASUREMENT NOISE?\n")
    print(f"   {'sport':<6} {'seasons':>9} {'reliability':>12} "
          f"{'observed sd':>12} {'TRUE sd':>9} {'mean':>8}")
    stats = {}
    for sport in ("XC", "TF"):
        if sport not in by:
            continue
        got = _reliability(by[sport])
        if got is None:
            continue
        rel, sd, true_sd, mean = got
        stats[sport] = got
        print(f"   {sport:<6} {len(by[sport]):>9,} {rel:>12.3f} "
              f"{sd:>12.2f} {true_sd:>9.2f} {mean:>8.2f}")
    if len(stats) < 2:
        return

    obs_ratio = stats["XC"][1] / stats["TF"][1]
    true_ratio = stats["XC"][2] / stats["TF"][2]
    print(f"\n   XC is {obs_ratio:.2f}x wider than TF as MEASURED, "
          f"{true_ratio:.2f}x wider in TRUTH.")
    if true_ratio < obs_ratio * 0.85:
        print("   => a real part of cross country's extra width is NOISE. "
              "The top of the\n      board is a winner's curse: the XC "
              "seasons up there are partly the\n      ones whose "
              "measurement error was largest.")
    else:
        print("   => the extra width SURVIVES the correction. Cross country "
              "really does\n      spread athletes further, and the top "
              "being XC-heavy is not an\n      artefact to remove.")

    # --- 2. the board as it stands, from these same rows ---------------- #
    seasons = []
    for sport, pairs in by.items():
        for a, b in pairs:
            seasons.append(((a + b) / 2, sport))
    n_xc = sum(1 for _, s in seasons if s == "XC")
    base = n_xc / len(seasons)
    now = _mix(seasons, top)
    print(f"\n2. THE BOARD AS IT STANDS\n")
    print(f"   seasons in this sample: {len(seasons):,} "
          f"({100 * base:.1f}% XC)")
    print(f"   top {top}: {100 * now:.1f}% XC")

    # --- 3. the same board after per-sport Kelley shrinkage ------------- #
    print(f"\n3. THE SAME BOARD, EACH SPORT PUT BACK ON THE TRUE SPREAD\n")
    print("   adjusted = sport mean + sqrt(reliability) x (season - mean)")
    print("   sqrt, NOT reliability itself -- see the header. Kelley gives")
    print("   the posterior mean, whose spread is sqrt(r) x the truth, so")
    print("   it under-disperses the noisier sport and flips the board.")
    fixed, kelley = [], []
    for sport, pairs in by.items():
        rel, _, _, mean = stats[sport]
        root = math.sqrt(rel)
        for a, b in pairs:
            full = (a + b) / 2
            fixed.append((mean + root * (full - mean), sport))
            kelley.append((mean + rel * (full - mean), sport))
    after = _mix(fixed, top)
    k_after = _mix(kelley, top)
    print(f"\n   for contrast, shrinking by r itself would give "
          f"{100 * k_after:.1f}% XC at the top {top}")
    print(f"\n   top {top}: {100 * now:.1f}% XC  ->  {100 * after:.1f}% XC"
          f"   (the pool itself is {100 * base:.1f}% XC)")
    for depth in (50, 200, 1000, 5000):
        if depth > len(seasons) // 2:
            continue
        b4 = _mix(seasons, depth)
        af = _mix(fixed, depth)
        print(f"     top {depth:>5}   {100 * b4:>5.1f}%  ->  {100 * af:>5.1f}%")
    gap_before = abs(now - base)
    gap_after = abs(after - base)
    print()
    if gap_before < 0.05:
        print("   => the board is already within 5 points of the pool's own "
              "mix. There is\n      nothing here to correct.")
        return
    if gap_after < gap_before * 0.6:
        print("   => SHRINKING FOR NOISE FIXES MOST OF IT, and it is not a "
              "free parameter:\n      both reliabilities are measured. This "
              "is the correction, not a\n      sport-gap shift.")
    elif gap_after < gap_before * 0.95:
        print("   => it helps but does not close the gap. Part of the "
              "imbalance is noise\n      and part is something else.")
    else:
        print("   => shrinking for noise does NOT fix it. The imbalance is "
              "not a variance\n      artefact, and this diagnosis is wrong.")


def _uplift(pool, rows, top):
    """Section 4: does the board's 80th-percentile statistic hand one sport
    a bigger free uplift than the other?"""
    by = {}
    for _p, sport, n, mean_r, p80, sd in rows:
        by.setdefault(sport, []).append((float(mean_r), float(p80),
                                         float(sd), int(n)))
    if len(by) < 2:
        return
    print(f"\n4. THE BOARD RANKS ON THE 80th PERCENTILE, NOT THE MEAN\n")
    print("   (build_ranking_results._SEASON_Q -- the column is still called")
    print("   mean_rating). p80 sits ~0.84 within-season sd above the mean,")
    print("   so the sport whose races scatter more gets a bigger free lift.")
    print()
    print(f"   {'sport':<6} {'seasons':>9} {'races/season':>13} "
          f"{'within-season sd':>17} {'p80 - mean':>11}")
    lift = {}
    for sport in ("XC", "TF"):
        if sport not in by:
            continue
        v = by[sport]
        sd = sum(x[2] for x in v) / len(v)
        up = sum(x[1] - x[0] for x in v) / len(v)
        nr = sum(x[3] for x in v) / len(v)
        lift[sport] = up
        print(f"   {sport:<6} {len(v):>9,} {nr:>13.1f} {sd:>17.2f} "
              f"{up:>11.2f}")
    if len(lift) < 2:
        return
    diff = lift["XC"] - lift["TF"]
    print(f"\n   Cross country is handed {diff:+.2f} rating points more than "
          f"track by the\n   quantile alone, before any athlete runs a step "
          f"differently.")

    # the board three ways, from the same seasons
    means = [(x[0], s) for s, v in by.items() for x in v]
    p80s = [(x[1], s) for s, v in by.items() for x in v]
    # equalised: keep each sport's uplift but rescale it to the smaller one
    target = min(lift.values())
    fair = []
    for sport, v in by.items():
        k = target / lift[sport] if lift[sport] > 0 else 1.0
        for mean_r, p80, _sd, _n in v:
            fair.append((mean_r + k * (p80 - mean_r), sport))
    base = sum(1 for _, s in means if s == "XC") / len(means)
    print(f"\n   top {top} XC share, same seasons, three statistics:")
    for label, board in (("mean          ", means),
                         ("p80 (the board)", p80s),
                         ("p80, uplift equalised", fair)):
        m = _mix(board, top)
        print(f"     {label:<24} {100 * m:>5.1f}%")
    print(f"     {'the pool itself':<24} {100 * base:>5.1f}%")
    gap_p80 = abs(_mix(p80s, top) - base)
    gap_fair = abs(_mix(fair, top) - base)
    if gap_fair < gap_p80 * 0.6:
        print("\n   => THE QUANTILE IS THE BIAS. Equalising the uplift moves "
              "the board most of\n      the way to the pool's own mix, and "
              "nothing about the athletes changed.")
    elif gap_fair < gap_p80 * 0.95:
        print("\n   => the quantile is part of it, not all of it.")
    else:
        print("\n   => the quantile is not the cause; the imbalance is "
              "elsewhere.")


def main():
    ap = argparse.ArgumentParser(
        description="Is cross country's wider rating spread real, or is it "
                    "measurement noise?")
    ap.add_argument("--pool", help="one rating pool, e.g. college_m")
    ap.add_argument("--top", type=int, default=200)
    ap.add_argument("--pct", type=float, default=15.0,
                    help="percent of ATHLETES to sample (default 15)")
    ap.add_argument("--half-min", type=int, default=2,
                    help="minimum races in EACH half (default 2, so a "
                         "season needs 4)")
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
            t0 = time.time()
            cur.execute(f"DROP TABLE IF EXISTS {_SCRATCH}")
            cur.execute(_PASS_A, {"cut": cut})
            cur.execute(f"SELECT count(*) FROM {_SCRATCH}")
            print(f"\n{cur.fetchone()[0]:,} rated results for {args.pct}% of "
                  f"athletes ({time.time() - t0:.0f}s)", flush=True)
            cur.execute(f"CREATE INDEX ON {_SCRATCH} "
                        f"(person_id, season, pool, sport)")
            cur.execute(_HALVES, {"half_min": args.half_min})
            rows = cur.fetchall()
            print(f"{len(rows):,} athlete-seasons with "
                  f"{2 * args.half_min}+ races ({time.time() - t0:.0f}s)")
            by_pool = {}
            for r in rows:
                if args.pool and r[0] != args.pool:
                    continue
                by_pool.setdefault(r[0], []).append(r)
            cur.execute(_UPLIFT, {"half_min": args.half_min})
            up_pool = {}
            for r in cur.fetchall():
                if args.pool and r[0] != args.pool:
                    continue
                up_pool.setdefault(r[0], []).append(r)
            for pool in sorted(by_pool, key=lambda p: -len(by_pool[p])):
                if len(by_pool[pool]) < 200:
                    continue
                _report(pool, by_pool[pool], args.top)
                if pool in up_pool:
                    _uplift(pool, up_pool[pool], args.top)
            cur.execute(f"DROP TABLE IF EXISTS {_SCRATCH}")
        finally:
            conn.rollback()
            cur.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
