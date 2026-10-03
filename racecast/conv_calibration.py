"""
conv_calibration.py -- the cross-sport step of a conversion, measured on the
runners who did both sports.

★ WHY (owner, 2026-10-03: "xc races I convert to tf just kind of seem wrong.
  Like a 25:30 8k at Keene State is not a 9:28 3200m"). A card converts a
  cross country result to a rating and the rating to a track time, so it is
  right exactly when a dual-sport runner's XC and track ratings agree. On
  2.5M (athlete, XC distance, track event) pairs (scripts/conversion_check.py,
  fall XC against the springs before and after, averaged so a year's
  improvement cancels) they do not:

      hs_m   5000 -> 3200   track rating 2.4 points under XC  (card 2.0% fast)
      college_m 8000 -> 3000  1.5 under                       (card 1.4% fast)
      college_m 8000 -> 800   2.1 OVER                        (card 2.1% slow)

  and the gap grows with ability. This is that gap, fitted and applied to
  the cross-sport leg of every conversion: the card says what runners who
  ran this XC actually ran on the track (and back).

★ ONE FIT, RE-MEASURED EVERY RUN, SELF-CANCELLING. It is measured on the
  published ratings, so when the engine's own cross-sport level moves, the
  next fit moves with it; if the ratings come to agree, every term here
  reads zero and the card is the ratings' own conversion again.

THE MODEL, per pool: ln(track rating / XC rating) of an athlete =
  cell(XC distance, track event) + tilt(ability quartile)
fitted by median polish (robust, additive: the 800's runners are slower XC
runners, and without the tilt beside it the event term would carry their
ability too). Ability is the mean of the athlete's XC and track ratings, so
sorting on it does not manufacture a gap the way sorting on XC alone does
(the top XC quartile's XC luck reads as a track shortfall). Each cell is
shrunk toward its pool's level by its own standard error against the
spread of true cell effects (method of moments) -- a thin cell borrows,
a thick one stands. No constants chosen here: medians, their errors, and
the cells' own spread.

Lookup order for a conversion: (pool, XC distance, track event), then
(pool, track event), then the pool; nothing for a pool never measured.
"""
import json
import math
import os

import numpy as np

XC_BANDS = (3000, 3200, 4000, 5000, 6000, 8000, 10000)
TF_BANDS = (800, 1500, 1600, 3000, 3200, 5000, 10000)
XC_TOL, TF_TOL = 0.04, 0.015
PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "engine", "data", "conv_calibration.json")


def band(dist, sport):
    """The standard distance `dist` is (a 2-mile is a 3200), or None."""
    try:
        d = float(dist)
    except (TypeError, ValueError):
        return None
    bands, tol = (XC_BANDS, XC_TOL) if sport == "XC" else (TF_BANDS, TF_TOL)
    best = min(bands, key=lambda b: abs(d - b))
    return best if abs(d - best) <= best * tol else None


# ------------------------------------------------------------------ #
# the fit (pure numpy)
# ------------------------------------------------------------------ #

def _groupMedian(key, val, n_key):
    """median of val per integer key in [0, n_key); NaN for an empty key."""
    out = np.full(n_key, np.nan)
    if key.size == 0:
        return out
    order = np.lexsort((val, key))
    k, v = key[order], val[order]
    starts = np.flatnonzero(np.r_[True, k[1:] != k[:-1]])
    ends = np.r_[starts[1:], k.size]
    for s, e in zip(starts, ends):
        m = (s + e - 1) / 2.0
        out[k[s]] = 0.5 * (v[int(math.floor(m))] + v[int(math.ceil(m))])
    return out


def _shrink(est, se, parent):
    """est toward parent by se against the spread of (est - parent):
    tau2 = mean((est-parent)^2) - mean(se^2), floored at 0."""
    ok = np.isfinite(est) & np.isfinite(se)
    if not ok.any():
        return est
    d = est[ok] - parent
    tau2 = max(0.0, float(np.mean(d * d) - np.mean(se[ok] ** 2)))
    w = np.where(ok, tau2 / np.maximum(tau2 + se * se, 1e-300), 0.0)
    return np.where(ok, parent + w * (est - parent), np.nan)


def fitPool(xc_band, tf_band, xc, tf, iters=10):
    """One pool's calibration from per-athlete pairs (arrays of equal
    length): XC band, track band, XC rating, track rating. Returns the
    pool's JSON entry."""
    xc_band = np.asarray(xc_band, dtype=np.int64)
    tf_band = np.asarray(tf_band, dtype=np.int64)
    xc = np.asarray(xc, dtype=np.float64)
    tf = np.asarray(tf, dtype=np.float64)
    ok = (xc > 0) & (tf > 0) & np.isfinite(xc) & np.isfinite(tf)
    xc_band, tf_band, xc, tf = xc_band[ok], tf_band[ok], xc[ok], tf[ok]
    n = xc.size
    if n == 0:
        return None
    y = np.log(tf / xc)
    ability = 0.5 * (xc + tf)
    edges = np.quantile(ability, [0.25, 0.5, 0.75])
    q = np.digitize(ability, edges)                       # 0..3
    cells = sorted(set(zip(xc_band.tolist(), tf_band.tolist())))
    cid = {c: i for i, c in enumerate(cells)}
    c = np.array([cid[(a, b)] for a, b in zip(xc_band.tolist(), tf_band.tolist())])
    nc = len(cells)
    tilt = np.zeros(4)
    eff = np.zeros(nc)
    for _ in range(iters):
        eff = _groupMedian(c, y - tilt[q], nc)
        t = _groupMedian(q, y - eff[c], 4)
        t = np.nan_to_num(t - np.nanmean(t))
        if np.allclose(t, tilt, atol=1e-6):
            tilt = t
            break
        tilt = t
    resid = y - eff[c] - tilt[q]
    level = float(np.median(y - tilt[q]))
    # standard error of a median: 1.2533 sd, sd from the residual MAD
    mad = _groupMedian(c, np.abs(resid - _groupMedian(c, resid, nc)[c]), nc)
    cnt = np.bincount(c, minlength=nc)
    se = 1.2533 * 1.4826 * mad / np.sqrt(np.maximum(cnt, 1))
    shrunk = _shrink(eff, se, level)
    # the track event alone, for an XC distance with no cell of its own
    tfs = sorted(set(tf_band.tolist()))
    tid = {b: i for i, b in enumerate(tfs)}
    ti = np.array([tid[b] for b in tf_band.tolist()])
    t_eff = _groupMedian(ti, y - tilt[q], len(tfs))
    t_mad = _groupMedian(ti, np.abs(resid), len(tfs))
    t_cnt = np.bincount(ti, minlength=len(tfs))
    t_se = 1.2533 * 1.4826 * t_mad / np.sqrt(np.maximum(t_cnt, 1))
    t_shrunk = _shrink(t_eff, t_se, level)
    mids = [float(np.median(ability[q == k])) for k in range(4)]
    return {
        "n": int(n), "level": round(level, 5),
        "tilt": [[round(m, 2), round(float(t), 5)] for m, t in zip(mids, tilt)],
        "tf": {str(b): {"g": round(float(t_shrunk[i]), 5), "n": int(t_cnt[i])}
               for b, i in tid.items() if np.isfinite(t_shrunk[i])},
        "cells": {f"{a}>{b}": {"g": round(float(shrunk[i]), 5), "n": int(cnt[i]),
                               "raw": round(float(eff[i]), 5), "se": round(float(se[i]), 5)}
                  for (a, b), i in cid.items() if np.isfinite(shrunk[i])},
    }


# ------------------------------------------------------------------ #
# the lookup (the site)
# ------------------------------------------------------------------ #

_CACHE = {"mtime": None, "data": None}


def load(path=PATH):
    """The fitted table, re-read when the file changes; {} without one."""
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {}
    if _CACHE["mtime"] != mt:
        try:
            with open(path, encoding="utf-8") as f:
                _CACHE["data"] = json.load(f)
        except Exception:                                 # noqa: BLE001
            _CACHE["data"] = {}
        _CACHE["mtime"] = mt
    return _CACHE["data"] or {}


def gap(pool, xc_dist, tf_dist, rating=None, table=None):
    """ln(track rating / XC rating) for runners of `pool` who ran this XC
    distance and track event, at this rating; 0.0 when unmeasured."""
    t = load() if table is None else table
    entry = (t.get("pools") or {}).get(str(pool or "").split("|", 1)[0])
    if not entry:
        return 0.0
    xb, tb = band(xc_dist, "XC"), band(tf_dist, "TF")
    g = None
    if xb and tb:
        cell = entry["cells"].get(f"{xb}>{tb}")
        g = cell["g"] if cell else None
    if g is None and tb:
        ev = entry["tf"].get(str(tb))
        g = ev["g"] if ev else None
    if g is None:
        g = entry["level"]
    tilt = entry.get("tilt") or []
    if rating is not None and len(tilt) >= 2:
        xs = [m for m, _ in tilt]
        ys = [v for _, v in tilt]
        g += float(np.interp(float(rating), xs, ys))
    return float(g)


def timeFactor(pool, source_sport, source_dist, target_sport, target_dist,
               rating=None, table=None):
    """The multiplier on a cross-sport conversion's TARGET time: XC -> track
    by exp(-gap) (a track rating under XC = a slower track time), track ->
    XC by exp(+gap). 1.0 within a sport or when unmeasured."""
    if source_sport == target_sport:
        return 1.0
    if source_sport == "XC":
        g = gap(pool, source_dist, target_dist, rating, table)
        return math.exp(-g)
    g = gap(pool, target_dist, source_dist, rating, table)
    return math.exp(g)
