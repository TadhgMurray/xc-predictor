#!/usr/bin/env python3
"""
indoor_outdoor_check.py -- is an indoor track slower than an outdoor one, in
this corpus, for the same people? From the pack and the solve file; no
database and no rerun.

    scripts/indoor_outdoor_check.py
    scripts/indoor_outdoor_check.py --windows 21,35,49 --no-curve
    scripts/indoor_outdoor_check.py --by geometry --write-levels
        (per indoor geometry class; writes engine/data/indoor_geometry_levels.json
         for XCP_BRACKET_INDOOR_MODE=geometry -- see classLevels)

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


# ★ BY THE TRACK, NOT ONLY BY THE RUNNER (owner, 2026-09-29: "I think indoor
#   difficulty is too easy generally"). One asserted indoor level
#   (INDOOR_CENTRE, +0.3%) stands for every oval, and ovals are not alike: a
#   flat 200 has tighter, unbanked turns than a banked 200, and a 300 m or
#   longer oval runs close to an outdoor track. So the same comparison is
#   offered grouped by the INDOOR track's geometry, from the pack's
#   track_length / track_type (speed_ratings.attachCourseGeometry). Unknown
#   length is its own class here: this is a measurement, and an assumption
#   would put the answer into the question.
#
# ! THE CLASSES LIVE IN engine/track_geometry.py NOW (2026-09-29), because the
#   bracket engine pins each class on the level this script measures for it
#   (bracket_engine.INDOOR_MODES "geometry"), and a split defined twice is two
#   splits. These names are kept so older callers still import them from here.
import track_geometry as _tg                                    # noqa: E402

GEO_CLASSES = _tg.GEO_CLASSES
geometryClass = _tg.geometryClass


def measure(cols, npz, windows=(21, 35, 49), dists=(800, 1600, 3200, 5000),
            use_curve=True, sample_pct=100.0, seed=11, codes=None, by="pool"):
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
    # the grouping: the runner's pool, or the INDOOR track's geometry class
    group, group_names = pool, list(pool_names)
    if by == "geometry":
        if "track_length" not in cols:
            raise SystemExit("[indoor] --by geometry needs a pack with track geometry "
                             "(07_pack from 2026-09-19 on)")
        tl = np.asarray(cols["track_length"], dtype=np.float64)
        cls_of_base = _tg.geometryClassIndex(tl, cols.get("track_type"))
        group = np.where(course >= 0, cls_of_base[np.maximum(course, 0)], -1)
        group_names = list(GEO_CLASSES)
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
            # the gap in days from the indoor race to its outdoor rows, so
            # the difference can be read at a gap of zero (see report)
            s_day, _n = bk.windowSumsAt(pid[outdoor], days[outdoor],
                                        days[outdoor].astype(np.float64),
                                        pid[indoor], days[indoor], W)
            gap_row = np.full(indoor.sum(), np.nan)
            gap_row[has] = np.abs(days[indoor][has] - s_day[has] / n_out[has])
            # one number per athlete-season, then the median over them
            sea_in = season[indoor]
            p_in = group[indoor]
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
            for p_i, pname in enumerate(group_names):
                for dsel, dlab in [(None, "all")] + [(d, str(d)) for d in dists]:
                    m = (p_in == p_i) if dsel is None else ((p_in == p_i) & (d_in == round(dsel / 100)))
                    if not m.any():
                        continue
                    # per athlete-season mean of the all-pairs diff
                    mm = m & np.isfinite(diff_row)
                    if mm.any():
                        u, inv = np.unique(sea_in[mm], return_inverse=True)
                        per = np.bincount(inv, weights=diff_row[mm]) / np.bincount(inv)
                        per_gap = np.bincount(inv, weights=gap_row[mm]) / np.bincount(inv)
                    else:
                        per = np.zeros(0); per_gap = np.zeros(0)
                    mt = m & np.isfinite(trans)
                    out[(variant, pname, W, dlab)] = {
                        "pairs": _stats(per),
                        "transition": _stats(trans[mt]),
                        "gap": (float(np.median(per_gap)) if per_gap.size else np.nan,
                                float(np.median(gap[mt])) if mt.any() else np.nan)}
    return out, group_names


# ★ THE NUMBER AT A GAP OF ZERO (owner, 2026-09-12: "can you justify the
#   indoor number?"). The raw difference grows with the window because
#   the outdoor race is later and the athlete fitter; the solve's curve
#   would take that out but was fit with the asserted level in it, so
#   it is not independent evidence. This is: the raw median against the
#   median gap in days, one point per window, a line through them, read
#   at zero days. That is what indoor costs the same athlete on the same
#   day, with no model in it.
#
# ! ONE FUNCTION SINCE 2026-09-29, because --write-levels uses the numbers
#   the report prints; computing them twice is how a written level and a
#   printed one come to disagree.
def zeroDay(res, min_n=100):
    """{(group, dist label, design): dict(at_zero, per_day, points, gap_min,
    gap_max)} -- the raw medians extrapolated to a gap of zero days. Only a
    (group, dist, design) with 2+ windows of min_n+ athlete-seasons and a gap
    range of a day or more is fitted."""
    pts_of = {}
    for k, r in res.items():
        variant, pname, W, dlab = k
        if variant != "raw" or "gap" not in r:
            continue
        for design, j in (("pairs", 0), ("transition", 1)):
            n, med, _t = r[design]
            g = r["gap"][j]
            if n >= min_n and np.isfinite(g) and np.isfinite(med):
                pts_of.setdefault((pname, dlab, design), []).append((g, med, n))
    out = {}
    for key, pts in pts_of.items():
        if len(pts) < 2:
            continue
        g = np.array([p[0] for p in pts]); m = np.array([p[1] for p in pts])
        w = np.sqrt(np.array([p[2] for p in pts], dtype=float))
        if np.ptp(g) < 1.0:
            continue
        b, a = np.polyfit(g, m, 1, w=w)
        out[key] = dict(at_zero=float(a), per_day=float(b), points=len(pts),
                        gap_min=float(g.min()), gap_max=float(g.max()))
    return out


# ★★ THE LEVELS THE ENGINE PINS EACH GEOMETRY CLASS ON (owner, 2026-09-29:
#    "indoor difficulty is too easy generally"). One asserted +0.3% for every
#    oval, with a -0.3% floor gate, under-credited the small and unrecorded
#    ovals (measured ~+0.7..+0.9%) and held banked 200s and 300m+ ovals ABOVE
#    what they measure (~-0.5..-0.6%, faster than a flat outdoor 400).
#
#  ★ A CLASS'S LEVEL IS THE MEDIAN OF ITS OWN PRINTED ESTIMATES at distance
#    'all': the curve-variant median for every window and both designs, plus
#    the zero-day reading for both designs -- eight numbers at the default
#    three windows. They are what a reader of this report weighs by eye; the
#    median is that weighing without a vote for any one design. An estimate
#    enters only when its design has min_n athlete-seasons, the report's own
#    rule for printing it.
#
#  ★ AND WHETHER THE CLASS HAS EARNED A LEVEL OF ITS OWN IS DECIDED BY ITS OWN
#    NUMBERS, NOT A PAIR-COUNT THRESHOLD. The estimates disagree with each
#    other by design (window, pairs vs transition, curve vs extrapolation), so
#    their IQR is how uncertain the class's level is. A class keeps its own
#    median when that IQR is SMALLER than its distance from the other classes'
#    pooled level (their medians, weighted by n_pairs) -- when its evidence can
#    tell it apart from the rest. Otherwise it takes the n_pairs-weighted level
#    of ALL classes. The fallback costs less than the class's own spread by
#    construction: a class fails only by sitting within its IQR of the pool.
#    Fewer than two estimates is no spread at all, and falls back.
def classLevels(res, group_names, min_n=100, zero=None):
    """{class: dict(level, own_level, estimates, iqr, distance, trusted,
    n_pairs)} from measure()'s result at distance 'all'. Needs the curve
    variant (a solve file): the raw medians carry the fitness gained between
    the two races, which the curve and the zero-day reading remove."""
    if not any(k[0] == "curve" for k in res):
        raise SystemExit("[indoor] --write-levels needs the form curve (the solve "
                         "file, --npz): the raw medians carry the fitness gained "
                         "between the two races")
    zero = zeroDay(res, min_n) if zero is None else zero
    out = {}
    for g in group_names:
        est, n_pairs = [], 0
        for k in sorted((k for k in res if k[0] == "curve" and k[1] == g
                         and k[3] == "all"), key=lambda k: k[2]):
            r = res[k]
            for design in ("pairs", "transition"):
                n, med, _t = r[design]
                if n >= min_n and np.isfinite(med):
                    est.append(float(med))
            n_pairs = max(n_pairs, int(r["pairs"][0]))
        for design in ("pairs", "transition"):
            z = zero.get((g, "all", design))
            if z is not None and np.isfinite(z["at_zero"]):
                est.append(float(z["at_zero"]))
        own = float(np.median(est)) if est else np.nan
        iqr = (float(np.subtract(*np.percentile(est, [75, 25])))
               if len(est) >= 2 else np.nan)
        out[g] = dict(own_level=own, estimates=est, iqr=iqr, n_pairs=n_pairs)
    have = [g for g in group_names if np.isfinite(out[g]["own_level"])
            and out[g]["n_pairs"] > 0]
    if not have:
        raise SystemExit("[indoor] no geometry class has an estimate; nothing to write")

    def _pooled(names):
        w = np.array([out[g]["n_pairs"] for g in names], dtype=float)
        v = np.array([out[g]["own_level"] for g in names], dtype=float)
        return float((w * v).sum() / w.sum()) if w.sum() > 0 else np.nan

    pooled_all = _pooled(have)
    for g in group_names:
        r = out[g]
        others = [h for h in have if h != g]
        dist = (abs(r["own_level"] - _pooled(others))
                if others and np.isfinite(r["own_level"]) else np.nan)
        r["distance"] = dist
        r["trusted"] = bool(np.isfinite(r["iqr"]) and np.isfinite(dist)
                            and r["iqr"] < dist)
        r["level"] = r["own_level"] if r["trusted"] else pooled_all
    return out


def writeLevels(levels, path, pack_path, npz_path=None, n_rows=None, min_n=100):
    """Write the levels file bracket_engine's indoor_mode="geometry" reads,
    and print the table it holds. Returns the document."""
    import datetime
    import json

    def _stamp(p):
        if p and os.path.exists(p):
            return datetime.datetime.fromtimestamp(os.path.getmtime(p)).isoformat(
                timespec="seconds")
        return None

    def _num(v):
        return float(v) if v is not None and np.isfinite(v) else None

    classes = {}
    for g in GEO_CLASSES:
        r = levels.get(g)
        if r is None or not np.isfinite(r["level"]):
            continue
        classes[g] = {"level": float(r["level"]), "own_level": _num(r["own_level"]),
                      "estimates": [float(x) for x in r["estimates"]],
                      "iqr": _num(r["iqr"]), "distance": _num(r["distance"]),
                      "trusted": bool(r["trusted"]), "n_pairs": int(r["n_pairs"]),
                      "n_rows": int((n_rows or {}).get(g, 0))}
    missing = [g for g in GEO_CLASSES if g not in classes]
    if missing:
        raise SystemExit(f"[indoor] no estimate for {', '.join(missing)}; the "
                         f"engine needs every class. Not written.")
    doc = {"measured": datetime.date.today().isoformat(),
           "pack": {"path": pack_path, "mtime": _stamp(pack_path)},
           "solve_file": {"path": npz_path, "mtime": _stamp(npz_path)},
           "distance": "all", "min_n": int(min_n),
           "rule": ("level = median of the class's curve medians (every window, "
                    "both designs) and zero-day readings (both designs); kept "
                    "when IQR(estimates) < |own level - n_pairs-weighted level "
                    "of the other classes|, else the n_pairs-weighted level of "
                    "all classes"),
           "classes": classes}
    jl, how = _tg.jointIndoorLevel(doc)
    doc["joint_level"] = jl
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    f = lambda v: "     -" if v is None else f"{100 * v:+6.2f}"
    print(f"\n== the indoor geometry levels (log-time %, + = slower than a flat "
          f"outdoor 400), written to {path} ==")
    print(f"  {'class':<12} {'level':>7} {'own':>7} {'IQR':>7} {'dist':>7} "
          f"{'kept':>5} {'n pairs':>9} {'n rows':>10}  estimates")
    for g, c in classes.items():
        print(f"  {g:<12} {f(c['level']):>7} {f(c['own_level']):>7} "
              f"{f(c['iqr']):>7} {f(c['distance']):>7} "
              f"{'yes' if c['trusted'] else 'NO':>5} {c['n_pairs']:>9,} "
              f"{c['n_rows']:>10,}  "
              + " ".join(f"{100 * x:+.2f}" for x in c["estimates"]))
    print(f"  the joint solve's one indoor level under the geometry mode "
          f"(weighted by {how}): {100 * jl:+.3f}%")
    print("  read: 'kept' = the class's estimates spread less (IQR) than it sits "
          "from the other classes (dist),\n        so it keeps its own median; "
          "'NO' = it takes the n_pairs-weighted level of all classes.")
    return doc


def indoorRowsByClass(cols):
    """Indoor track rows per geometry class over the WHOLE pack -- the
    weights of the joint solve's one indoor level (track_geometry.
    jointIndoorLevel)."""
    keys = [str(k) for k in cols["course_keys"]]
    base_in = np.array([k.startswith("TF:") and k.split("@", 1)[0].endswith(":in")
                        for k in keys], dtype=bool)
    cls = _tg.geometryClassIndex(np.asarray(cols["track_length"], dtype=np.float64),
                                 cols.get("track_type"))
    course = np.asarray(cols["course"]).astype(np.int64)
    c = course[course >= 0]
    c = c[base_in[c]]
    cnt = np.bincount(cls[c], minlength=len(GEO_CLASSES))
    return {g: int(cnt[i]) for i, g in enumerate(GEO_CLASSES)}


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
    zero = zeroDay(res, min_n)
    if zero:
        print("\n== read at a gap of zero days (raw medians against the median gap, "
              "one point per window) ==")
        print(f"  {'pool':<10} {'dist':>5} {'design':<11} {'points':>6} "
              f"{'gap range':>11} {'at 0 days':>10} {'per week':>9}")
        for (pname, dlab, design), z in sorted(zero.items()):
            print(f"  {pname:<10} {dlab:>5} {design:<11} {z['points']:>6} "
                  f"{z['gap_min']:5.1f}-{z['gap_max']:<5.1f} {100 * z['at_zero']:+9.2f}% "
                  f"{100 * 7 * z['per_day']:+8.2f}%")
        print("  read: 'at 0 days' is the indoor penalty with the fitness gain "
              "between the two races removed by extrapolation instead of by the "
              "solve's curve; 'per week' is that gain. Both designs should agree; "
              "the pairs design has more rows, the transition design a cleaner gap.")
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
    ap.add_argument("--by", choices=("pool", "geometry"), default="pool",
                    help="group by the runner's pool, or by the indoor track's "
                         "geometry (flat/banked 200, 300m+, ...)")
    # ★ THE MEASUREMENT, WRITTEN FOR THE ENGINE (2026-09-29). With --by
    #   geometry the per-class levels (classLevels) go to the file
    #   bracket_engine's indoor_mode="geometry" pins each class on.
    ap.add_argument("--write-levels", action="store_true",
                    help="with --by geometry: write each class's level (the "
                         "median of its printed estimates at 'all') to "
                         "--levels-out for XCP_BRACKET_INDOOR_MODE=geometry")
    ap.add_argument("--levels-out", default=_tg.INDOOR_LEVELS_FILE,
                    help="where --write-levels writes (default %(default)s)")
    args = ap.parse_args()
    if args.write_levels and args.by != "geometry":
        ap.error("--write-levels needs --by geometry")
    if args.write_levels and args.no_curve:
        ap.error("--write-levels needs the form curve; drop --no-curve")
    windows = tuple(int(x) for x in args.windows.split(","))
    dists = tuple(int(x) for x in args.dists.split(","))
    cols, npz = bk.loadInputs(args.pack, args.npz)
    if npz is None:
        print(f"(no solve file at {args.npz}: raw log times only)")
    print(f"[indoor] {np.asarray(cols['norm']).size:,} rows loaded", flush=True)
    if args.by == "geometry":
        import speed_ratings as sr
        if "track_length" not in cols:
            sr.attachCourseGeometry(cols)
    if args.write_levels and (npz is None or "curve" not in npz or "doy" not in cols):
        # ⚠ measure() LABELS A CURVE-LESS RUN "curve" ANYWAY (the curve is
        #   zeros without a solve file), so classLevels cannot tell; the
        #   levels would then carry the fitness gained between the races.
        raise SystemExit(f"[indoor] --write-levels needs the solve file's form "
                         f"curve and none was found at {args.npz}")
    n_rows = (indoorRowsByClass(cols) if args.write_levels and "track_length" in cols
              else None)
    res, pool_names = measure(cols, npz, windows, dists, use_curve=not args.no_curve,
                              sample_pct=args.sample_pct, seed=args.seed, by=args.by)
    report(res, pool_names, windows, dists)
    if args.write_levels:
        writeLevels(classLevels(res, pool_names), args.levels_out, args.pack,
                    npz_path=args.npz if npz is not None else None, n_rows=n_rows)


if __name__ == "__main__":
    main()
