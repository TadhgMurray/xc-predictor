"""
diag_sport_gap.py -- the XC-versus-track gap the ratings actually carry.

    python scripts/diag_sport_gap.py            # fall 2024 XC vs spring 2025 TF
    python scripts/diag_sport_gap.py --year 2023

For every person rated in both the fall XC season and the following spring
track season: mean TF rating minus mean XC rating. Under the sequential
engine the mean of this is about zero by construction (bbar recentred).
Under the joint solve it is amp x (f_spring - f_fall) plus whatever the
level got wrong (issue 143). Read-only.

    python scripts/diag_sport_gap.py --by-ability          # the question below
    python scripts/diag_sport_gap_ability.py               # the same thing

★★ IS THE GRASS-TO-TRACK GAP A FUNCTION OF ABILITY, NOT OF POOL? (owner,
   2026-09-29). The site converts a 0% course 5K to a track 5K at +2.4% for
   ms, +1.5% for hs and +0.4% for college: per-POOL constants (the go-live's
   XCP_SPORT_LEVEL_POOLS shift, the anchor shift, the event offset). But the
   pools are also ability bands on one HS scale -- middle schoolers slow,
   college fast -- and a slower runner improves more between November and
   May. If that is all the constants are, one curve in HS-equivalent
   ability replaces them and stops relabelling a runner from changing
   their conversion. --by-ability measures it, from athlete_season:

     gap      log-time TF minus XC, same person, pool and academic year
              (ln(xc / tf) of the season ratings; NEGATIVE = track rates
              higher), both seasons with --min-races races
     ability  the HS-equivalent of the two seasons' geometric mean
              (pool_view.repFactor): symmetric in the two sports, so
              regression to the mean does not tilt the slope
     unshift  (default on) the go-live's own per-pool track shift taken
              back out of the TF rating (sport_gain.log_shift, interpolated
              at the rating as conversions.sport_gain does), so the gap is
              what the SOLVE produced and not the constant under test

   and prints, per gender:
     1. per pool: n, median gap, and the OLS slope of gap on ability per
        10 HS-equivalent points (with its SE);
     2. the median gap per (10-point ability bin, pool) -- read ACROSS a
        row: at the same ability, do the pools agree?
     3. a median polish of that table (Tukey): gap = ability effect + pool
        effect. The pool effects are the per-pool constants AT MATCHED
        ABILITY, beside the raw pool medians.

   HOW TO READ IT. If the polished pool effects shrink to a few tenths of a
   percent while the raw medians spread by a percent or more, the gap is
   ability, and the per-pool constants are an ability curve sampled at
   three points. If the polished effects keep the raw spread, the pools
   differ at the same ability (surface mix, distance, the pool's own
   curve) and the constants are about the pool. A slope that differs by
   pool says the curve is not one curve.
"""
import argparse
import math
import os
import sys

import numpy as np

sys.path.insert(0, "racecast")
sys.path.insert(0, "scripts")

from database import getConn                                    # noqa: E402

_SQL = """
WITH x AS (
    SELECT person_id, avg(speed_rating) AS xc, count(*) AS n
    FROM   results
    WHERE  date BETWEEN %(xc0)s AND %(xc1)s
      AND  speed_rating IS NOT NULL AND person_id IS NOT NULL
    GROUP  BY person_id),
t AS (
    SELECT person_id, avg(speed_rating) AS tf, count(*) AS n
    FROM   results_tf
    WHERE  date BETWEEN %(tf0)s AND %(tf1)s
      AND  speed_rating IS NOT NULL AND person_id IS NOT NULL
      -- --tf-event: one track event class against the XC season, so the
      -- distance law (issue 109) can be told from the calendar
      AND  (%(tf_event)s = '' OR event_short ~* %(tf_event)s)
    GROUP  BY person_id),
-- --pool: the person's pool from athlete_ratings (bare pool names on a
-- merged run), so the band table is read within one pool and not across
-- middle school, high school and college at once
pp AS (
    SELECT DISTINCT athlete_id AS person_id FROM athlete_ratings
    WHERE  %(pool)s = '' OR pool = %(pool)s)
SELECT count(*)                                            AS people,
       round(avg(t.tf - x.xc)::numeric, 2)                 AS mean_tf_minus_xc,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY t.tf - x.xc))::numeric, 2)
                                                           AS median,
       round(avg(x.xc)::numeric, 2)                        AS mean_xc,
       round(avg(t.tf)::numeric, 2)                        AS mean_tf
FROM   x JOIN t USING (person_id) JOIN pp USING (person_id)
WHERE  x.n >= 2 AND t.n >= 2
  AND  x.xc >= %(min_xc)s
"""

# ★ BANDED ON THE AVERAGE OF THE TWO SEASONS, NOT ON ONE OF THEM. Selecting
#   people on their XC mean alone picks the ones whose fall was unusually
#   good (regression to the mean: the spring comes back down) AND the XC
#   specialists (beta > 0), and both read as "track is under XC at the top"
#   whether or not it is. --min-xc 125 read -4.5 on 2026-09-03 for exactly
#   that reason. (xc + tf) / 2 is symmetric in the two, so a band of it
#   shows how the gap moves with ability and nothing else.
_BANDS = _SQL.replace(
    "SELECT count(*)                                            AS people,",
    "SELECT (floor(((x.xc + t.tf) / 2.0) / 10.0) * 10)::int    AS band,\n"
    "       count(*)                                            AS people,"
) + "GROUP BY 1 ORDER BY 1"


# ------------------------------------------------------------------ #
#  --by-ability: the gap against HS-equivalent ability, pool by pool
# ------------------------------------------------------------------ #

_BY_ABILITY_SQL = """
    SELECT x.pool, x.mean_rating, t.mean_rating
    FROM   athlete_season x
    JOIN   athlete_season t ON t.person_id = x.person_id AND t.pool = x.pool
                           AND t.year = x.year AND t.sport = 'TF'
    WHERE  x.sport = 'XC' AND x.n_races >= %(k)s AND t.n_races >= %(k)s
      AND  x.mean_rating > 0 AND t.mean_rating > 0
      AND  (%(pool)s = '' OR x.pool = %(pool)s)
"""
BIN_WIDTH = 10.0            # HS-equivalent points per bin, for reading


def gapRows(rows, factor_of, shift_of=None):
    """(pool, gap, ability_hs) arrays from (pool, xc, tf) rows. gap is
    ln(xc / tf): log-time TF minus XC. shift_of(pool, rating) is the
    go-live's track shift to take back out (None = leave it in). A pool
    without an HS factor is dropped -- it has no place on the axis."""
    pools, gaps, abil = [], [], []
    for pool, xc, tf in rows:
        f = factor_of(pool)
        if not f or xc is None or tf is None or xc <= 0 or tf <= 0:
            continue
        xc, tf = float(xc), float(tf)
        own = math.sqrt(xc * tf)
        if shift_of is not None:
            tf = tf * math.exp(-float(shift_of(pool, own) or 0.0))
        pools.append(str(pool))
        gaps.append(math.log(xc / tf))
        abil.append(math.sqrt(xc * tf) * float(f))
    return (np.array(pools, dtype=object), np.array(gaps, dtype=np.float64),
            np.array(abil, dtype=np.float64))


def poolSlopes(pools, gaps, abil, ref=None):
    """{pool: (n, median gap, slope per 10 points, its SE, gap at ref)} by
    OLS of gap on ability within the pool. Pure."""
    ref = float(np.median(abil)) if ref is None and abil.size else ref
    out = {}
    for p in sorted(set(pools.tolist())):
        m = pools == p
        n = int(m.sum())
        if n < 3:
            continue
        x = (abil[m] - ref) / 10.0
        y = gaps[m]
        X = np.c_[np.ones(n), x]
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        s2 = float(resid @ resid) / max(n - 2, 1)
        cov = s2 * np.linalg.pinv(X.T @ X)
        out[p] = (n, float(np.median(y)), float(beta[1]),
                  float(np.sqrt(max(cov[1, 1], 0.0))), float(beta[0]))
    return out, ref


def binTable(pools, gaps, abil, width=BIN_WIDTH, min_n=50):
    """{(bin lower edge, pool): (n, median gap)} for cells with min_n. Pure."""
    lo = np.floor(abil / width) * width
    out = {}
    for b in np.unique(lo):
        for p in sorted(set(pools.tolist())):
            m = (lo == b) & (pools == p)
            if int(m.sum()) >= min_n:
                out[(float(b), p)] = (int(m.sum()), float(np.median(gaps[m])))
    return out


def medianPolish(table, iters=10):
    """Tukey's median polish of {(row, col): value}: value = overall + row
    effect + column effect + residual, missing cells skipped. Returns
    (overall, {row: effect}, {col: effect}). Pure."""
    rows = sorted({r for r, _ in table})
    cols = sorted({c for _, c in table})
    res = dict(table)
    row_eff = {r: 0.0 for r in rows}
    col_eff = {c: 0.0 for c in cols}
    overall = 0.0
    for _ in range(iters):
        for r in rows:
            vals = [res[(r, c)] for c in cols if (r, c) in res]
            if vals:
                med = float(np.median(vals))
                row_eff[r] += med
                for c in cols:
                    if (r, c) in res:
                        res[(r, c)] -= med
        med = float(np.median(list(row_eff.values())))
        overall += med
        for r in rows:
            row_eff[r] -= med
        for c in cols:
            vals = [res[(r, c)] for r in rows if (r, c) in res]
            if vals:
                med = float(np.median(vals))
                col_eff[c] += med
                for r in rows:
                    if (r, c) in res:
                        res[(r, c)] -= med
        med = float(np.median(list(col_eff.values())))
        overall += med
        for c in cols:
            col_eff[c] -= med
    return overall, row_eff, col_eff


def byAbilityLines(pools, gaps, abil, min_n=50, width=BIN_WIDTH):
    """The report for one gender family. Pure: arrays in, lines out."""
    out = []
    slopes, ref = poolSlopes(pools, gaps, abil)
    out.append(f"  1. per pool (gap = log-time TF - XC, %; negative = track "
               f"rates higher; slope per 10 HS-equivalent points; ref {ref:.0f}):")
    out.append(f"     {'pool':<12}{'athlete-yrs':>12}{'median':>9}{'slope/10':>10}"
               f"{'se':>8}{'at ' + format(ref, '.0f'):>9}")
    for p, (n, med, sl, se, at) in slopes.items():
        out.append(f"     {p:<12}{n:>12,}{100 * med:>+8.2f}%{100 * sl:>+9.3f}%"
                   f"{100 * se:>7.3f}%{100 * at:>+8.2f}%")
    tab = {k: v[1] for k, v in binTable(pools, gaps, abil, width, min_n).items()}
    cnt = binTable(pools, gaps, abil, width, min_n)
    cols = sorted({c for _, c in tab})
    rows = sorted({r for r, _ in tab})
    out.append(f"  2. median gap % by HS-equivalent ability bin (cells with "
               f"{min_n}+ athlete-years) -- read across: same ability, same gap?")
    out.append("     " + f"{'bin':>9}" + "".join(f"{c:>15}" for c in cols))
    for r in rows:
        cells = "".join(f"{100 * tab[(r, c)]:>+8.2f}/{cnt[(r, c)][0]:<6d}"
                        if (r, c) in tab else f"{'':>15}" for c in cols)
        out.append(f"     {r:>5.0f}-{r + width:<3.0f}" + cells)
    out.append("     (median gap % / athlete-years)")
    if len(cols) >= 2 and len(rows) >= 2:
        overall, row_eff, col_eff = medianPolish(tab)
        raw = {p: slopes[p][1] for p in cols if p in slopes}
        spread_raw = max(raw.values()) - min(raw.values()) if raw else float("nan")
        spread_pol = max(col_eff.values()) - min(col_eff.values())
        out.append("  3. median polish: gap = ability effect + POOL EFFECT AT "
                   "MATCHED ABILITY")
        out.append(f"     {'pool':<12}{'raw median':>12}{'pool effect':>13}")
        for c in cols:
            out.append(f"     {c:<12}{100 * raw.get(c, float('nan')):>+11.2f}%"
                       f"{100 * col_eff[c]:>+12.2f}%")
        out.append(f"     spread between pools: raw {100 * spread_raw:.2f}%, at "
                   f"matched ability {100 * spread_pol:.2f}% -- "
                   f"{'ability carries most of it' if spread_pol < 0.5 * spread_raw else 'the pools differ at the same ability'}")
        out.append("     ability effect by bin: " + ", ".join(
            f"{r:.0f} {100 * (overall + e):+.2f}%" for r, e in sorted(row_eff.items())))
    else:
        out.append("  3. median polish: needs two pools sharing two ability bins")
    return out


def mainByAbility(a):
    """--by-ability: see the module header."""
    import pool_view as PV
    factor_cache = {}

    def factor_of(pool):
        if pool not in factor_cache:
            try:
                factor_cache[pool] = PV.repFactor(pool, None)
            except Exception:                                  # noqa: BLE001
                factor_cache[pool] = None
        return factor_cache[pool]

    shift_of = None
    if not a.keep_shift:
        try:
            import conversions as cv

            def shift_of(pool, rating):
                return cv.sport_gain(pool, "TF", rating)
        except Exception as exc:                               # noqa: BLE001
            print(f"  (the go-live's track shift could not be read -- {exc}; "
                  f"the gaps carry it)")
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_BY_ABILITY_SQL, {"k": a.min_races, "pool": a.pool})
        rows = [tuple(r.values()) if isinstance(r, dict) else tuple(r)
                for r in cur.fetchall()]
    pools, gaps, abil = gapRows(rows, factor_of, shift_of)
    print(f"XC vs TF, same person, pool and academic year, {a.min_races}+ races "
          f"each: {pools.size:,} athlete-years with an HS factor "
          f"({'the go-live track shift taken out' if shift_of else 'as published'})")
    print("  factors: " + ", ".join(f"{p} x{f:.4f}" for p, f in sorted(factor_cache.items())
                                   if f))
    for g in ("m", "f"):
        m = np.array([p.rsplit("_", 1)[-1] == g for p in pools], dtype=bool)
        if m.sum() < a.min_n:
            continue
        print(f"\n== {'men' if g == 'm' else 'women'} ==")
        for line in byAbilityLines(pools[m], gaps[m], abil[m], min_n=a.min_n):
            print(line)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024,
                    help="the XC year; track is the following spring")
    ap.add_argument("--min-xc", type=float, default=0.0,
                    help="only people whose fall XC mean rating is at least "
                         "this: the corpus mean is ~105, so --min-xc 125 "
                         "asks whether the gap differs at the top (the "
                         "amplitude tilt says it may)")
    ap.add_argument("--pool", default="",
                    help="one pool only (hs_m, hs_f, college_m, ms_f ...), "
                         "from athlete_ratings")
    ap.add_argument("--tf-event", default="",
                    help="regex on results_tf.event_short for the track "
                         "side, e.g. '3200|2 mile', '1600|mile', '^800'. "
                         "A gap that moves with the event at one ability "
                         "band is the distance law (issue 109), not fitness")
    # --by-ability (module header): the gap against HS-equivalent ability
    ap.add_argument("--by-ability", action="store_true",
                    help="is the XC/TF gap a function of ability rather than "
                         "pool? (athlete_season, HS-equivalent bins, median "
                         "polish)")
    ap.add_argument("--min-races", type=int, default=3,
                    help="--by-ability: races in each sport's season")
    ap.add_argument("--min-n", type=int, default=50,
                    help="--by-ability: athlete-years per (bin, pool) cell")
    ap.add_argument("--keep-shift", action="store_true",
                    help="--by-ability: leave the go-live's per-pool track "
                         "shift in the TF ratings (default: take it out)")
    a = ap.parse_args()
    if a.by_ability:
        return mainByAbility(a)
    p = {"xc0": f"{a.year}-08-01", "xc1": f"{a.year}-12-31",
         "tf0": f"{a.year + 1}-01-01", "tf1": f"{a.year + 1}-06-30",
         "min_xc": a.min_xc, "pool": a.pool, "tf_event": a.tf_event}
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL, p)
        row = cur.fetchone()
        cur.execute(_BANDS, p)
        bands = [list(r.values()) if isinstance(r, dict) else list(r)
                 for r in cur.fetchall()]
    if isinstance(row, dict):
        row = list(row.values())
    people, mean_gap, median, mxc, mtf = row
    print(f"XC {a.year} fall vs TF {a.year + 1} spring, people rated 2+ in both"
          f"{f' with XC mean >= {a.min_xc:g}' if a.min_xc else ''}"
          f"{f', pool {a.pool}' if a.pool else ''}"
          f"{f', track events ~ {a.tf_event!r}' if a.tf_event else ''}: "
          f"{people:,}")
    print(f"  mean  TF - XC rating: {mean_gap:+}")
    print(f"  median              : {median:+}")
    print(f"  mean XC {mxc}   mean TF {mtf}")
    print("  A sequential-engine run reads about 0 here. +10 or more is the "
          "level or the curve on every XC row (issue 143).")
    print("\n  by band of (XC + TF) / 2, the symmetric cut:")
    print(f"  {'band':>6} {'people':>9} {'mean':>7} {'median':>7} "
          f"{'xc':>7} {'tf':>7}")
    for band, people, mean_gap, median, mxc, mtf in bands:
        if people < 200:
            continue
        print(f"  {band:>4d}+ {people:>9,} {mean_gap:>+7} {median:>+7} "
              f"{mxc:>7} {mtf:>7}")
    print("  A slope here is ability-shaped structure (the amplitude tilt, "
          "issue 109's distance curve), not the winter gain, which is one "
          "number for everyone.")


if __name__ == "__main__":
    main()
