#!/usr/bin/env python3
"""
event_check.py -- is the 800 (or the 10k) overrated? The same athlete, two
events, a few weeks apart, on the rating's own scale.

    scripts/event_check.py --era-years 2
    scripts/event_check.py --era-years 2 --pool hs_m --pool college_m --window 21

Run from the PROJECT ROOT. Reads the pack and the solve file; no database.

★ THE QUESTION (owner, 2026-09-14: "if I had to guess for the spline, the
  extremes (10k and 800m) are getting slightly overrated. What should I
  do to check it?"). A rating is one number for every distance, so an
  athlete's 800 and their 1600 three weeks apart should rate the same
  once fitness is held still. Per row this takes the FULLY adjusted log
  time -- log normalized time, less the form curve, less the tilted
  course, less the solve's event offset for the row's rating band: the
  rating's own arithmetic, minus nothing -- and for every ordered pair of
  distance classes compares an athlete-season's rows at the longer one
  to the mean of their rows at the shorter one within +-window days.
  The median over athletes, per pool and per rating band, is the answer:
  POSITIVE means the longer race rates WORSE than the shorter one for the
  same person (the shorter event is overrated, or the longer underrated),
  in log % and in rating points at 130. A pair whose median is near zero
  in every band is right; a pair whose median grows with the band says
  the offsets' band shape is wrong; a pair off in every band says the
  spline (or its reference) is.

  --no-offsets shows the same table with the solve's event offsets left
  OUT, which is what the spline alone does -- read both, the gap between
  them is what the offsets are buying.
"""
import argparse
import math
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

CLASSES = (800, 1000, 1500, 1600, 3000, 3200, 5000, 10000)
BANDS = (105.0, 120.0, 135.0)                                   # js.DIST_BANDS


def offsetTable(npz):
    """{(pool, distance bucket): [offset per band]} from the solve file,
    and the band count (1 for an unbanded solve)."""
    if npz is None or "dist_offset" not in npz or "dist_labels" not in npz:
        return {}, 0
    e = np.asarray(npz["dist_offset"], dtype=np.float64).reshape(-1)
    labels = [str(x) for x in np.asarray(npz["dist_labels"]).reshape(-1)]
    n_band = 1
    for lab in labels:
        parts = lab.split(":")
        if len(parts) > 2 and parts[2].startswith("b"):
            n_band = max(n_band, int(parts[2][1:]) + 1)
    table = {}
    for lab, val in zip(labels, e):
        parts = lab.split(":")
        key = (parts[0], int(float(parts[1])))
        band = int(parts[2][1:]) if len(parts) > 2 and parts[2].startswith("b") else 0
        table.setdefault(key, [0.0] * n_band)[band] = float(val)
    return table, n_band


def offsetRows(pool_row_name, bucket, rating, table, n_band):
    """The event offset each row's rating applies (js.distOffsetRow's
    interpolation between the band anchors, flat beyond), from the table;
    zero for a reference or unkeyed distance."""
    n = bucket.size
    out = np.zeros(n)
    if not table:
        return out
    anchors = np.asarray(js.DIST_BAND_ANCHORS[:n_band] if n_band > 1 else [100.0])
    r = np.clip(np.nan_to_num(rating, nan=100.0), anchors[0], anchors[-1])
    keys = {}
    for i in range(n):
        k = (pool_row_name[i], int(bucket[i]))
        v = table.get(k)
        if v is None:
            continue
        keys.setdefault(k, []).append(i)
    for k, idx in keys.items():
        vals = np.asarray(table[k], dtype=np.float64)
        idx = np.asarray(idx)
        if n_band <= 1:
            out[idx] = vals[0]
            continue
        x = r[idx]
        j = np.clip(np.searchsorted(anchors, x, side="right") - 1, 0, n_band - 2)
        t = (x - anchors[j]) / (anchors[j + 1] - anchors[j])
        out[idx] = vals[j] + t * (vals[j + 1] - vals[j])
    return out


def adjustedLogTime(cols, npz, codes, use_offsets=True, use_day=False):
    """Per row: the rating's own adjusted log time (NaN where the row has
    no cell or no rating), the pool name, the rating, the distance bucket.
    Track rows only."""
    n = np.asarray(cols["norm"]).size
    sport = np.asarray(cols["sport"]).astype(np.int64)
    dist = np.asarray(cols["dist_m"], dtype=np.float64)
    cell = np.asarray(cols["_cell"]).astype(np.int64)
    season = np.asarray(cols["_season"]).astype(np.int64)
    rating = bk.ratingOnRows(cols, npz, codes)
    if rating is None:
        raise SystemExit("the solve file carries no ratings for this pack")
    ath = np.asarray(cols["athlete"]).astype(np.int64)
    pool_row = np.asarray(codes["pool_names"], dtype=object)[codes["pool_of_raw"][ath]]
    ln = np.log(np.asarray(cols["norm"], dtype=np.float64))
    curve = bk.curveOnRows(npz, codes["pool_of_raw"][ath], cols["doy"], rating)
    delta = np.asarray(npz["delta"], dtype=np.float64)
    r_clip = np.clip(np.nan_to_num(rating, nan=100.0), js.TILT_RATING_LO, js.TILT_RATING_HI)
    h = 1.0 + js.TILT_K * (r_clip - 100.0) / 10.0
    ok = (sport == 1) & (dist > 0) & (cell >= 0) & np.isfinite(rating)
    adj = np.full(n, np.nan)
    c = np.maximum(cell, 0)
    adj[ok] = ln[ok] - curve[ok] - h[ok] * delta[c[ok]]
    if use_day and "race_effect" in npz:
        u = np.asarray(npz["race_effect"], dtype=np.float64)
        race = np.asarray(cols["_race"]).astype(np.int64)
        if u.size == codes["n_race"]:
            adj[ok] -= h[ok] * u[race[ok]]
    bucket = (np.round(dist / 100.0) * 100).astype(np.int64)
    if use_offsets:
        table, n_band = offsetTable(npz)
        adj[ok] -= offsetRows(pool_row[ok], bucket[ok], rating[ok], table, n_band)
    return adj, pool_row, rating, bucket, season, ok


def pairs(adj, pool_row, rating, bucket, season, ok, days, window=21,
          classes=CLASSES, bands=BANDS, min_rows=200):
    """Per pool, per (shorter, longer) class pair: the median of (adjusted
    at the longer - mean adjusted at the shorter within the window) over
    the longer rows, overall and per rating band. Returns rows of dicts."""
    days = np.round(np.asarray(days, dtype=np.float64)).astype(np.int64)
    cls_code = {c: i for i, c in enumerate(classes)}
    code = np.array([cls_code.get(int(b), -1) for b in bucket], dtype=np.int64)
    m = ok & (code >= 0) & np.isfinite(adj)
    K = len(classes)
    key = season * K + code
    idx = np.flatnonzero(m)
    out = []
    edges = (-np.inf,) + tuple(bands) + (np.inf,)
    for pool in sorted(set(str(p) for p in pool_row[idx])):
        pm = idx[pool_row[idx] == pool]
        for i1, c1 in enumerate(classes):
            for c2 in classes[i1 + 1:]:
                ref = pm[code[pm] == cls_code[c1]]
                q = pm[code[pm] == cls_code[c2]]
                if ref.size < 20 or q.size < 20:
                    continue
                s, cnt = bk.windowSumsAt(key[ref], days[ref], adj[ref],
                                         season[q] * K + cls_code[c1], days[q], window)
                got = cnt > 0
                if got.sum() < min_rows:
                    continue
                d = adj[q][got] - s[got] / cnt[got]
                r = rating[q][got]
                row = dict(pool=pool, short=c1, long=c2, n=int(got.sum()),
                           median=float(np.median(d)), by_band=[])
                for lo, hi in zip(edges, edges[1:]):
                    bm = (r >= lo) & (r < hi)
                    row["by_band"].append((int(bm.sum()),
                                           float(np.median(d[bm])) if bm.sum() >= 50 else np.nan))
                out.append(row)
    return out


def report(rows, bands=BANDS, out=print):
    labs = [f"<{bands[0]:.0f}"] + [f"{a:.0f}-{b:.0f}" for a, b in zip(bands, bands[1:])] + [f"{bands[-1]:.0f}+"]
    out("same athlete, two events within the window: median of (adjusted log time at the "
        "LONGER event - at the shorter), log % and rating points at 130. + = the longer "
        "race rates worse for the same person (the shorter is overrated, or the longer "
        "underrated); - = the reverse.")
    out(f"  {'pool':<10}{'pair':>13}{'rows':>8}{'median':>9}{'pts@130':>9}  "
        + "  ".join(f"{lab:>9}" for lab in labs))
    for r in rows:
        bands_s = "  ".join(f"{100 * v:>+8.2f}%" if np.isfinite(v) else f"{'':>9}"
                            for _n, v in r["by_band"])
        out(f"  {r['pool']:<10}{r['short']:>6}->{r['long']:<6}{r['n']:>8,}"
            f"{100 * r['median']:>+8.2f}%{130 * r['median']:>+9.2f}  {bands_s}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    defaults = rj.buildParser()
    ap.add_argument("--pack", default=defaults.get_default("pack"))
    ap.add_argument("--npz", default=defaults.get_default("out"))
    ap.add_argument("--era-years", type=int, default=0)
    ap.add_argument("--window", type=float, default=21.0)
    ap.add_argument("--pool", action="append", default=[])
    ap.add_argument("--no-offsets", action="store_true",
                    help="leave the solve's event offsets out: the spline alone")
    ap.add_argument("--day", action="store_true",
                    help="take the race-day term out too (the rating does not, for TF)")
    args = ap.parse_args()
    cols, npz = bk.loadInputs(args.pack, args.npz, only=bk.PACK_COLUMNS)
    if npz is None:
        sys.exit(f"no solve file at {args.npz}")
    cols, codes = bk.packCodes(cols, npz, args.era_years)
    adj, pool_row, rating, bucket, season, ok = adjustedLogTime(
        cols, npz, codes, use_offsets=not args.no_offsets, use_day=args.day)
    if args.pool:
        ok = ok & np.isin(pool_row.astype(str), args.pool)
    rows = pairs(adj, pool_row, rating, bucket, season, ok, cols["days"], window=args.window)
    print(f"[event] {'spline alone' if args.no_offsets else 'spline + the solve offsets'}, "
          f"window {args.window:g} days, {int(ok.sum()):,} track rows")
    report(rows)


if __name__ == "__main__":
    main()
