# Project: xc-predictor
# File:    scripts/fit_critical_speed.py
# Purpose: Fit and VALIDATE the critical-speed model on the corpus, before any
#          of it reaches a page.
#
#     d = CS * t + D'
#
#   CS  the asymptote of an athlete's distance-time line: the speed above
#       which they fatigue and below which, in principle, they do not. This is
#       the quantity a threshold pace is an estimate of.
#   D'  a fixed distance credit spendable above CS. Published values for
#       distance runners sit around 100-350m, which is a check this model can
#       FAIL rather than one imposed on it.
#
# ★ WHY THIS EXISTS RATHER THAN A COACHING OFFSET. "Tempo = mile pace + 60-80
#   s/mi" is a decent population average and it is blind to what kind of
#   runner you are: three athletes with the same 4:10 1600 and 5K bests of
#   14:10, 14:36 and 15:30 have critical speeds of 4:44, 4:56 and 5:22 per
#   mile -- 38 seconds apart -- and the offset rule hands all three the same
#   number. Two real performances separate them, and this database has two
#   real performances for most athletes.
#
# ⚠ RAW time AND real distance. NOT normalized_time. normalized_time already
#   contains the distance correction, so a distance-time line built from it
#   would be fitting this project's own exponent back to itself -- defect #1
#   in fit_distance_exponent's rewrite notes, for exactly this reason.
#
# ⚠ AND THE VALIDATION IS THE POINT, NOT THE FIT. Leave one race out, fit on
#   the rest, predict the held-out one. A model that cannot predict a race it
#   has not seen has no business setting anybody's training pace. The
#   comparison that decides the page's design is two-race CS against
#   population-D' CS: if the fallback is much worse, the page must ask for a
#   second race instead of guessing.
#
# USAGE
#   python scripts/fit_critical_speed.py                 # TF, the clean case
#   python scripts/fit_critical_speed.py --sport XC      # terrain uncorrected
#   python scripts/fit_critical_speed.py --pool hs_m --sample 40000
import argparse
import os
import statistics as st
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
from database import getConn, initPool                    # noqa: E402

MILE_M = 1609.344

# ⚠ THE SAME ILL-CONDITIONING GUARD fit_distance_exponent NEEDED. CS is a
#   slope: (d2-d1)/(t2-t1). When the two distances are close the numerator and
#   denominator are both small and the ratio explodes. A season whose races
#   are all 5Ks says nothing about an asymptote.
MIN_SPREAD = 1.6           # longest race / shortest race
MIN_RACES = 3              # two to fit, at least one to predict

# ! A SEASON, NOT A CAREER. CS is a fitness, and fitness moves. Pairing a
#   freshman 3200 with a senior 5K measures growing up, not an asymptote.
#   ranking_results.year is already the academic season.
_SQL = """
    SELECT person_id, year, pool, distance, time_seconds
    FROM   ranking_results
    WHERE  sport = %(sport)s
      AND  person_id IS NOT NULL
      AND  time_seconds > 0
      AND  distance > 0
      AND  (%(pool)s = '' OR pool = %(pool)s)
    ORDER  BY person_id, year
"""


def fitCS(races):
    """(CS m/s, D' m) by least squares on d = CS*t + D'. None if degenerate."""
    n = len(races)
    if n < 2:
        return None
    ts = [t for _, t in races]
    ds = [d for d, _ in races]
    mt, md = sum(ts) / n, sum(ds) / n
    sxx = sum((t - mt) ** 2 for t in ts)
    if sxx <= 0:
        return None
    cs = sum((t - mt) * (d - md) for (d, t) in races) / sxx
    return cs, md - cs * mt


def _spread(races):
    ds = [d for d, _ in races]
    return max(ds) / min(ds)


def main():
    ap = argparse.ArgumentParser(description="Fit and validate critical speed.")
    ap.add_argument("--sport", default="TF", choices=["TF", "XC"])
    ap.add_argument("--pool", default="")
    ap.add_argument("--sample", type=int, default=0,
                    help="stop after this many usable athlete-seasons")
    args = ap.parse_args()

    initPool()
    print(f"\n  reading {args.sport} results"
          + (f" for {args.pool}" if args.pool else "") + "...")
    with getConn() as conn:
        with conn.cursor(name="cs_stream") as cur:
            cur.itersize = 50_000
            cur.execute(_SQL, {"sport": args.sport, "pool": args.pool})

            seasons, cur_key, buf, pool_of = [], None, [], None
            for pid, year, pool, dist, secs in cur:
                key = (pid, year)
                if key != cur_key:
                    if buf and len(buf) >= MIN_RACES:
                        seasons.append((cur_key, pool_of, list(buf)))
                    cur_key, buf, pool_of = key, [], pool
                buf.append((float(dist), float(secs)))
                if args.sample and len(seasons) >= args.sample:
                    break
            if buf and len(buf) >= MIN_RACES and cur_key:
                seasons.append((cur_key, pool_of, list(buf)))
        conn.rollback()

    print(f"  {len(seasons):,} athlete-seasons with {MIN_RACES}+ races")
    usable = [(k, p, r) for k, p, r in seasons if _spread(r) >= MIN_SPREAD]
    print(f"  {len(usable):,} of them span {MIN_SPREAD}x in distance "
          f"({100.0 * len(usable) / max(len(seasons), 1):.0f}%) -- the rest "
          f"cannot pin an asymptote")
    if not usable:
        sys.exit("  nothing to fit.")

    # ---------------------------------------------------------------- #
    #  1. WHAT THE MODEL SAYS ABOUT ITSELF
    # ---------------------------------------------------------------- #
    fits = []
    for key, pool, races in usable:
        got = fitCS(races)
        if not got:
            continue
        cs, dprime = got
        # ! A NEGATIVE CS OR D' IS THE MODEL SAYING "NOT ME". Kept in the
        #   count so the failure rate is visible rather than filtered away.
        fits.append((pool, cs, dprime, races))

    sane = [f for f in fits if f[1] > 0 and 0 < f[2] < 1000]
    print(f"\n  fits: {len(fits):,}   physically sane "
          f"(CS>0, 0<D'<1000m): {len(sane):,} "
          f"({100.0 * len(sane) / max(len(fits), 1):.0f}%)")

    by_pool = {}
    for pool, cs, dp, _ in sane:
        by_pool.setdefault(pool, []).append((cs, dp))
    print(f"\n  {'pool':<14}{'n':>9}{'D-prime median':>17}{'IQR':>18}"
          f"{'CS pace median':>17}")
    for pool in sorted(by_pool):
        vals = by_pool[pool]
        dps = sorted(d for _, d in vals)
        css = sorted(c for c, _ in vals)
        q1, q3 = dps[len(dps) // 4], dps[3 * len(dps) // 4]
        med_cs = css[len(css) // 2]
        pace = MILE_M / med_cs
        print(f"  {pool:<14}{len(vals):>9,}{st.median(dps):>14.0f} m"
              f"{f'{q1:.0f}-{q3:.0f} m':>18}"
              f"{f'{int(pace)//60}:{int(pace)%60:02d}/mi':>17}")

    # ---------------------------------------------------------------- #
    #  2. THE ONLY TEST THAT MATTERS: PREDICT A RACE IT HAS NOT SEEN
    # ---------------------------------------------------------------- #
    print(f"\n  HOLD ONE RACE OUT, FIT ON THE REST, PREDICT IT")
    pop_dprime = {p: st.median([d for _, d in v]) for p, v in by_pool.items()}
    errs_two, errs_pop = [], []
    for pool, cs, dp, races in sane:
        for i in range(len(races)):
            train = races[:i] + races[i + 1:]
            d_out, t_out = races[i]
            if len(train) < 2 or _spread(train) < MIN_SPREAD:
                continue
            got = fitCS(train)
            if not got or got[0] <= 0:
                continue
            # d = CS*t + D'  ->  t = (d - D') / CS
            pred = (d_out - got[1]) / got[0]
            if pred > 0:
                errs_two.append(abs(pred - t_out) / t_out)
            # the one-input fallback: population D', one race to pin CS
            d1, t1 = train[0]
            dp_pop = pop_dprime.get(pool, 200.0)
            cs1 = (d1 - dp_pop) / t1
            if cs1 > 0:
                p2 = (d_out - dp_pop) / cs1
                if p2 > 0:
                    errs_pop.append(abs(p2 - t_out) / t_out)

    def band(e):
        e = sorted(e)
        if not e:
            return "no cases"
        return (f"median {e[len(e)//2]:.1%}   p90 {e[int(.9*len(e))]:.1%}"
                f"   n={len(e):,}")
    print(f"    two races, fitted D'   {band(errs_two)}")
    print(f"    one race, population D' {band(errs_pop)}")
    if errs_two and errs_pop:
        a = sorted(errs_two)[len(errs_two) // 2]
        b = sorted(errs_pop)[len(errs_pop) // 2]
        print(f"\n    the fallback is {b / a:.1f}x the error of the real fit.")
        print("    -> a single input is good enough" if b / a < 1.5 else
              "    -> the page should ASK FOR A SECOND RACE, not guess")


if __name__ == "__main__":
    main()
