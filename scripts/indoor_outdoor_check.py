#!/usr/bin/env python3
"""
indoor_outdoor_check.py -- is an indoor track slower than an outdoor one, in
this corpus, for the same people? From the pack and the solve file; no
database and no rerun.

    scripts/indoor_outdoor_check.py
    scripts/indoor_outdoor_check.py --windows 21,35,49 --no-curve

Two measurements, both same-athlete-season, same distance, log time
indoor minus outdoor (+ = indoor slower), with the season form curve taken
out of both rows when the solve file carries one (owner, 2026-09-12: "use
the fitness curve to make 21 days more clean"):

  transition   the athlete's LAST indoor race against their FIRST outdoor
               race, within the window: the NCAA facility study's design.
               A peaked last indoor race biases it down, fitness gained in
               between biases it up; the curve removes the second.
  all pairs    every indoor row against the mean of the athlete's outdoor
               rows at the same distance within the window, either side;
               one number per athlete-season, then the median over them,
               so a prolific racer does not outvote the rest.

Per pool, per window, and per distance. The asserted level in the solve
is +1.2% (joint_solve.IND_LEVEL_DEFAULT); the literature says +0.8 to
+1.8% for 800-5000 on a 200 m oval.
"""
import argparse
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket as bk                                            # noqa: E402
import joint_solve as js                                        # noqa: E402
import run_joint as rj                                          # noqa: E402


def _stats(d):
    d = np.asarray(d, dtype=np.float64)
    d = d[np.isfinite(d)]
    if d.size == 0:
        return 0, np.nan, np.nan
    lo, hi = np.percentile(d, [10, 90])
    t = d[(d >= lo) & (d <= hi)]
    return d.size, float(np.median(d)), float(t.mean()) if t.size else np.nan


def measure(cols, npz, windows=(21, 35, 49), dists=(800, 1600, 3200, 5000),
            use_curve=True, sample_pct=100.0, seed=11, codes=None):
    """Returns {(pool, window, dist or 'all'): {'transition': (n, median,
    trimmed), 'pairs': (n, median, trimmed)}} plus the same without the
    curve under the key ('raw', ...). codes: bracket.packCodes' whole-pack
    codes, computed here when not given (only the season codes are used)."""
    keys = [str(k) for k in cols["course_keys"]]
    base_in = np.array([k.split("@", 1)[0].endswith(":in") for k in keys], dtype=bool)
    base_tf = np.array([k.startswith("TF:") for k in keys], dtype=bool)
    # ! ONLY THE ATHLETE-SEASONS WITH AN INDOOR ROW, all their rows, and a
    #   sample of them: the comparison is within an athlete-season, so the
    #   rest of the corpus cannot change it (2026-09-12: "way too long")
    if codes is None or "_season" not in cols:
        cols, codes = bk.packCodes(cols, npz, 0, cells=False)
    n_season = int(codes["n_season"])
    course0 = np.asarray(cols["course"]).astype(np.int64)
    has_in = (course0 >= 0) & base_in[np.maximum(course0, 0)]
    keep = bk.rowsOfSeasons(cols, has_in) & bk.athleteSample(cols, sample_pct, seed)
    print(f"[indoor] {int(keep.sum()):,} rows of {keep.size:,}: the athlete-seasons "
          f"with an indoor race ({sample_pct:g}% of athletes)", flush=True)
    cols = bk.subsetCols(cols, keep)
    course = np.asarray(cols["course"]).astype(np.int64)
    days = np.round(np.asarray(cols["days"], dtype=np.float64)).astype(np.int64)
    dist = np.asarray(cols["dist_m"], dtype=np.float64)
    ath_raw = np.asarray(cols["athlete"]).astype(np.int64)
    season = np.asarray(cols["_season"]).astype(np.int64)
    pool_of_raw, pool_names = codes["pool_of_raw"], codes["pool_names"]
    pool = pool_of_raw[ath_raw]
    ln = np.log(np.asarray(cols["norm"], dtype=np.float64))
    curve = np.zeros(ln.size)
    if use_curve and npz is not None and "curve" in npz and "doy" in cols:
        rating = bk.ratingOnRows(cols, npz, codes)
        curve = bk.curveOnRows(npz, pool, cols["doy"], rating)
    ok = (course >= 0) & base_tf[np.maximum(course, 0)] & np.isfinite(ln) & np.isfinite(dist) & (dist > 0)
    flag = np.zeros(ln.size, dtype=bool)
    flag[ok] = base_in[course[ok]]
    indoor = ok & flag
    outdoor = ok & ~flag
    dcode = np.round(dist / 100.0).astype(np.int64)              # 800 -> 8
    # ! COMPACT IDS FOR (athlete-season, distance). The first cut sized
    #   arrays by n_season * 1000 -- ten billion entries on the corpus -- and
    #   the kernel killed it (2026-09-12). Only the pairs that occur get an id.
    both = indoor | outdoor
    pair_key = season * 1000 + dcode
    uniq_pair, pair_id_all = np.unique(pair_key[both], return_inverse=True)
    n_pair = uniq_pair.size
    pid = np.full(ln.size, -1, dtype=np.int64)
    pid[both] = pair_id_all
    print(f"[indoor] {int(indoor.sum()):,} indoor and {int(outdoor.sum()):,} outdoor "
          f"track rows, {n_pair:,} (athlete-season, distance) pairs", flush=True)
    out = {}
    for variant, z in (("curve", ln - curve), ("raw", ln)):
        if variant == "curve" and not use_curve:
            continue
        for W in windows:
            print(f"[indoor] {variant}, window {W} days", flush=True)
            # ---- all pairs: indoor rows against outdoor rows, same season
            #      and distance, within W days ------------------------------
            s_out, n_out = bk.windowSumsAt(pid[outdoor], days[outdoor], z[outdoor],
                                           pid[indoor], days[indoor], W)
            has = n_out > 0
            diff_row = np.full(indoor.sum(), np.nan)
            diff_row[has] = z[indoor][has] - s_out[has] / n_out[has]
            # one number per athlete-season, then the median over them
            sea_in = season[indoor]
            p_in = pool[indoor]
            d_in = dcode[indoor]
            # ---- transition: last indoor vs first outdoor, same distance --
            last_in = rj.groupExtreme(pid[indoor], days[indoor], n_pair)
            first_out = rj.groupExtreme(pid[outdoor], days[outdoor], n_pair, largest=True)
            kq = pid[indoor]
            is_last = days[indoor] == last_in[kq]
            gap = last_in[kq] - first_out[kq]
            pair_ok = is_last & np.isfinite(first_out[kq]) & (gap > 0) & (gap <= W)
            # the outdoor row(s) at first_out for that pair: mean z there
            kref = pid[outdoor]
            at_first = days[outdoor] == first_out[kref]
            cnt = np.bincount(kref[at_first], minlength=n_pair)
            sm = np.bincount(kref[at_first], weights=z[outdoor][at_first], minlength=n_pair)
            z_first = np.where(cnt > 0, sm / np.maximum(cnt, 1), np.nan)
            trans = np.full(indoor.sum(), np.nan)
            trans[pair_ok] = z[indoor][pair_ok] - z_first[kq[pair_ok]]
            for p_i, pname in enumerate(pool_names):
                for dsel, dlab in [(None, "all")] + [(d, str(d)) for d in dists]:
                    m = (p_in == p_i) if dsel is None else ((p_in == p_i) & (d_in == round(dsel / 100)))
                    if not m.any():
                        continue
                    # per athlete-season mean of the all-pairs diff
                    mm = m & np.isfinite(diff_row)
                    if mm.any():
                        u, inv = np.unique(sea_in[mm], return_inverse=True)
                        per = np.bincount(inv, weights=diff_row[mm]) / np.bincount(inv)
                    else:
                        per = np.zeros(0)
                    out[(variant, pname, W, dlab)] = {
                        "pairs": _stats(per),
                        "transition": _stats(trans[m & np.isfinite(trans)])}
    return out, pool_names


def report(res, pool_names, windows, dists, min_n=100):
    print(f"\nindoor minus outdoor, log-time %, same athlete-season and distance "
          f"(+ = indoor slower). Asserted level {100 * js.IND_LEVEL_DEFAULT:+.2f}%; "
          f"literature +0.8 to +1.8%.")
    for variant in ("curve", "raw"):
        if not any(k[0] == variant for k in res):
            continue
        print(f"\n== {'with the form curve taken out' if variant == 'curve' else 'raw log times'} ==")
        print(f"  {'pool':<10} {'dist':>5} {'window':>6} | {'all pairs: n':>13} {'median':>8} {'trim':>8} "
              f"| {'transition: n':>14} {'median':>8} {'trim':>8}")
        for pname in pool_names:
            for dlab in ["all"] + [str(d) for d in dists]:
                for W in windows:
                    r = res.get((variant, pname, W, dlab))
                    if r is None:
                        continue
                    (n1, m1, t1), (n2, m2, t2) = r["pairs"], r["transition"]
                    if n1 < min_n and n2 < min_n:
                        continue
                    f = lambda v: "      " if not np.isfinite(v) else f"{100 * v:+6.2f}"
                    print(f"  {pname:<10} {dlab:>5} {W:>6} | {n1:>13,} {f(m1):>8} {f(t1):>8} "
                          f"| {n2:>14,} {f(m2):>8} {f(t2):>8}")
    print("\n  read: 'all pairs' is one number per athlete-season (their indoor rows "
          "against their outdoor rows at that distance inside the window), then "
          "the median over athlete-seasons; 'transition' is each athlete-season's "
          "last indoor race against its first outdoor one. A peaked last indoor "
          "race pulls transition down; a longer window lets more fitness in, which "
          "the curve variant removes. If both sit near +1%, the assertion stands.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    d = rj.buildParser()
    ap.add_argument("--pack", default=d.get_default("pack"))
    ap.add_argument("--npz", default=d.get_default("out"))
    ap.add_argument("--windows", default="21,35,49")
    ap.add_argument("--dists", default="800,1600,3200,5000")
    ap.add_argument("--no-curve", action="store_true")
    ap.add_argument("--sample-pct", type=float, default=30.0,
                    help="percent of athletes (whole athletes; default 30)")
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()
    windows = tuple(int(x) for x in args.windows.split(","))
    dists = tuple(int(x) for x in args.dists.split(","))
    cols, npz = bk.loadInputs(args.pack, args.npz)
    if npz is None:
        print(f"(no solve file at {args.npz}: raw log times only)")
    print(f"[indoor] {np.asarray(cols['norm']).size:,} rows loaded", flush=True)
    res, pool_names = measure(cols, npz, windows, dists, use_curve=not args.no_curve,
                              sample_pct=args.sample_pct, seed=args.seed)
    report(res, pool_names, windows, dists)


if __name__ == "__main__":
    main()
