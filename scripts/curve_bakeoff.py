#!/usr/bin/env python3
"""
curve_bakeoff.py -- which distance curve is right? Every candidate scored
on the same number: the same athlete, two events, weeks apart.

    scripts/curve_bakeoff.py --era-years 2
    scripts/curve_bakeoff.py --era-years 2 --pool hs_m --pool college_m --pct 100 --all

Run from the PROJECT ROOT. Reads the pack and the solve file; no database.

★ THE QUESTION (owner, 2026-09-14: "do you think that curve is actually
  correct? would there just be a better pre-generated one? let's find
  out"). A rating is one number for every distance, so an athlete's 800
  and their 1600 a few weeks apart should rate the same once fitness is
  held still. For each candidate curve, every track row's adjusted log
  time is rebuilt with THAT curve in place of the fitted spline (the
  pack's normalization is undone through the spline that made it and
  redone through the candidate; the curve, the tilted course and
  everything else stay as the solve fitted them), and the event check
  (scripts/event_check.py) is run on it: per pool and per pair of
  distance classes, the median over an athlete-season's rows at the
  longer event of (adjusted at the longer - mean adjusted at the shorter
  within the window). The candidate whose medians sit nearest zero over
  the pairs, weighted by rows, is the one that makes a rating mean the
  same thing at every distance.

  The candidates:
    fitted          the shipped distance potential (engine/data/distance_spline.pkl)
    fitted+offsets  the same plus the solve's per-band event offsets: what a rating applies today
    wa              the World Athletics 2025 scoring tables by sex and rating band
                    (engine/distance_tables.py), the published cross-event relation
    hybrid          the fitted curve between 800 and 3200 m, the tables outside it
    riegel          t2 = t1 (d2/d1)^1.06, the straw man
    vdot            Daniels-Gilbert: equal VDOT at the two distances

⚠ THE FITTED CURVE WAS FITTED ON PAIRS FROM THESE ROWS, so it has a
  structural edge on exactly this test; the tables are external. Read a
  narrow win for `fitted` as a tie and a loss as decisive.
"""
import argparse
import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket as bk                                            # noqa: E402
import distance_tables as dt                                    # noqa: E402
import event_check as ec                                        # noqa: E402
import joint_solve as js                                        # noqa: E402
import run_joint as rj                                          # noqa: E402

CANDIDATES = ("fitted", "fitted+offsets", "wa", "hybrid", "riegel", "vdot")
HYBRID_LO, HYBRID_HI = 800.0, 3200.0
RIEGEL_K = 1.06


# ---- the candidates: ln(t_target / t_d) per row ------------------------ #

def _fittedLogFactor(pool):
    """ln F(d) on the shipped potential for this pool's track curve, or
    None: the ratio the pack's normalization applied."""
    return dt.curveLogFactorFor(pool)


def lnRatioFitted(pool, d, target):
    f = _fittedLogFactor(pool)
    return None if f is None else f(d)


def lnRatioWA(sex, band, d, target):
    return dt.logTimeRatio(sex, band, d, target)


def lnRatioRiegel(d, target):
    return RIEGEL_K * math.log(target / d)


def lnRatioHybrid(pool, sex, band, d, target):
    """The fitted curve's relation inside [HYBRID_LO, HYBRID_HI], the
    tables' outside, integrated from d to target."""
    f = _fittedLogFactor(pool)
    if f is None:
        return None
    lo, hi = HYBRID_LO, HYBRID_HI

    def fitted(a, b):                       # ln(t_b / t_a) on the fitted curve
        return f(a) - f(b)

    def step(a, b):
        if b <= lo or a >= hi:
            return dt.logTimeRatio(sex, band, a, b)
        return fitted(a, b)

    if d == target:
        return 0.0
    a, b = (d, target) if d < target else (target, d)
    knots = [a] + [k for k in (lo, hi) if a < k < b] + [b]
    total = sum(step(x, y) for x, y in zip(knots, knots[1:]))
    return total if d < target else -total


def vdotOf(t_s, d_m):
    """Daniels-Gilbert VDOT for a time (s) over a distance (m)."""
    t_min = np.asarray(t_s, dtype=np.float64) / 60.0
    v = np.asarray(d_m, dtype=np.float64) / t_min
    vo2 = -4.60 + 0.182258 * v + 0.000104 * v * v
    pct = (0.8 + 0.1894393 * np.exp(-0.012778 * t_min)
           + 0.2989558 * np.exp(-0.1932605 * t_min))
    return vo2 / pct


def vdotEquivalentTime(t_s, d_m, target_m, iters=40):
    """The time at target_m with the same VDOT as t_s over d_m, by
    bisection (VDOT falls monotonically with time at a fixed distance)."""
    t_s = np.asarray(t_s, dtype=np.float64)
    d_m = np.asarray(d_m, dtype=np.float64)
    target = np.asarray(target_m, dtype=np.float64)
    want = vdotOf(t_s, d_m)
    guess = t_s * (target / d_m) ** 1.1
    lo, hi = guess * 0.6, guess * 1.6
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        v = vdotOf(mid, target)
        too_fast = v > want                    # mid too fast -> more time
        lo = np.where(too_fast, mid, lo)
        hi = np.where(too_fast, hi, mid)
    return 0.5 * (lo + hi)


# ---- the rows, rebuilt per candidate ------------------------------------ #

def candidateAdjusted(name, base, cols, npz, codes, ok, pool_row, rating, bucket):
    """The adjusted log time per row under `name`: `base` is the rating's
    adjusted log time WITHOUT the solve's offsets (event_check); the
    pack's fitted normalization is swapped for the candidate's."""
    import normalize_distance as nd
    n = base.size
    adj = base.copy()
    if name == "fitted":
        return adj
    if name == "fitted+offsets":
        table, n_band = ec.offsetTable(npz)
        adj[ok] -= ec.offsetRows(pool_row[ok], bucket[ok], rating[ok], table, n_band)
        return adj
    dist = np.asarray(cols["dist_m"], dtype=np.float64)
    band = np.digitize(np.nan_to_num(rating, nan=100.0), js.DIST_BANDS)
    ln_norm = np.log(np.asarray(cols["norm"], dtype=np.float64))
    idx = np.flatnonzero(ok)
    corr = np.zeros(n)
    cache = {}
    if name == "vdot":
        # raw time from the stored normalized time through the fitted factor
        f_fit = np.zeros(idx.size)
        for j, i in enumerate(idx):
            key = (str(pool_row[i]), int(bucket[i]))
            if key not in cache:
                cache[key] = lnRatioFitted(key[0], float(bucket[i]), None)
            f_fit[j] = cache[key] if cache[key] is not None else np.nan
        targets = np.array([nd.targetFor(str(pool_row[i]), "TF") for i in idx])
        t_raw = np.exp(ln_norm[idx] - f_fit)
        t_eq = vdotEquivalentTime(t_raw, dist[idx], targets)
        corr[idx] = np.log(t_eq) - ln_norm[idx]
        corr[idx] = np.where(np.isfinite(corr[idx]), corr[idx], 0.0)
        adj += corr
        return adj
    for j, i in enumerate(idx):
        key = (str(pool_row[i]), int(bucket[i]), int(band[i]))
        if key not in cache:
            pool, d, b = key
            target = nd.targetFor(pool, "TF")
            f_fit = lnRatioFitted(pool, float(d), target)
            sex = dt.sexOfPool(pool)
            if name == "wa":
                f_c = lnRatioWA(sex, b, float(d), target)
            elif name == "hybrid":
                f_c = lnRatioHybrid(pool, sex, b, float(d), target)
            elif name == "riegel":
                f_c = lnRatioRiegel(float(d), target)
            else:
                raise ValueError(name)
            cache[key] = (f_c - f_fit) if (f_fit is not None and f_c is not None) else 0.0
        corr[i] = cache[key]
    adj += corr
    return adj


def score(rows):
    """Per pool and overall: the row-weighted mean |median| over pairs."""
    out = {}
    tot_w = tot = 0.0
    for r in rows:
        w = r["n"]
        out.setdefault(r["pool"], [0.0, 0.0])
        out[r["pool"]][0] += w * abs(r["median"]); out[r["pool"]][1] += w
        tot += w * abs(r["median"]); tot_w += w
    per_pool = {p: v[0] / v[1] for p, v in out.items() if v[1] > 0}
    return (tot / tot_w if tot_w else np.nan), per_pool


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    defaults = rj.buildParser()
    ap.add_argument("--pack", default=defaults.get_default("pack"))
    ap.add_argument("--npz", default=defaults.get_default("out"))
    ap.add_argument("--era-years", type=int, default=0)
    ap.add_argument("--window", type=float, default=21.0)
    ap.add_argument("--pool", action="append", default=[])
    ap.add_argument("--pct", type=float, default=30.0,
                    help="percent of athletes (every row of each), default 30")
    ap.add_argument("--candidates", default=",".join(CANDIDATES))
    ap.add_argument("--all", action="store_true", help="print every candidate's pair table")
    args = ap.parse_args()
    cols, npz = bk.loadInputs(args.pack, args.npz, only=bk.PACK_COLUMNS)
    if npz is None:
        sys.exit(f"no solve file at {args.npz}")
    import normalize_distance as nd
    pk = os.path.getmtime(args.pack); sp = os.path.getmtime(nd._SPLINE_FILE)
    if sp > pk:
        print(f"⚠ the spline file is NEWER than the pack ({(sp - pk) / 3600:.1f} h): the pack "
              f"was normalised with an older curve, and 'fitted' below is the old one "
              f"undone through the new. Rerun after the backfill and the pack.")
    cols, codes = bk.packCodes(cols, npz, args.era_years)
    if args.pct < 100:
        m = bk.athleteSample(cols, args.pct)
        cols = bk.subsetCols(cols, m)
    base, pool_row, rating, bucket, season, ok = ec.adjustedLogTime(
        cols, npz, codes, use_offsets=False)
    if args.pool:
        ok = ok & np.isin(pool_row.astype(str), args.pool)
    names = [c.strip() for c in args.candidates.split(",") if c.strip()]
    results = {}
    for name in names:
        adj = candidateAdjusted(name, base, cols, npz, codes, ok, pool_row, rating, bucket)
        rows = ec.pairs(adj, pool_row, rating, bucket, season, ok, cols["days"],
                        window=args.window)
        results[name] = rows
        overall, per_pool = score(rows)
        print(f"[bakeoff] {name:<15} mean |median gap| {100 * overall:.3f}%  "
              + "  ".join(f"{p} {100 * v:.2f}%" for p, v in sorted(per_pool.items())))
    print("\nlower is better: the row-weighted mean over (pool, pair) of |median same-athlete "
          "gap|; read `fitted` with the caveat in the header. Per pair:")
    best = sorted(results, key=lambda k: score(results[k])[0])
    for name in (names if args.all else best[:2]):
        print(f"\n== {name} ==")
        ec.report(results[name])


if __name__ == "__main__":
    main()
