"""
run_joint.py -- drive joint_solve over the packed cache.

    python engine/run_joint.py                      # solve, write, compare
    python engine/run_joint.py --holdout            # + a 10% held-out score
    python engine/run_joint.py --outer 8 --probes 32
    python engine/run_joint.py --no-curve --no-sport-offset --no-rust
    python engine/run_joint.py --no-robust          # plain least squares

★ NON-DESTRUCTIVE BY CONSTRUCTION. Writes engine/data/joint_difficulty.npz and
  touches NOTHING else -- not results.speed_rating, not course_difficulties,
  not pair_difficulty.npz. Run it beside the existing solve and compare; it
  cannot change what the site serves. Step 08b in the pipeline (XCP_JOINT=1).

THE RESPONSE IS THE RAW ONE: ln(normalized_time), with NO form correction
subtracted. The season form -- the whole-year curve per pool and the opener
rust -- is fitted inside the model, so subtracting rust_fitness's constants
first would count it twice. The sequential engine's y differs from this one
by exactly that correction; the held-out score is comparable because both
are scored on ln(normalized_time).

WHAT IS WIRED
  ability (athlete-season), the ridged per-athlete sport offset (K=0.5),
  the sport level as a parameter (XC is the reference group, mu[TF] is the
  level), cell difficulty about that level with hierarchical tau2 by sport,
  the race-day effect at (cell, day), the year form curve per pool with the
  ability-tilted amplitude, opener rust per pool, asymmetric robust weights,
  the ability tilt at the model's own ratings, posterior variance by probing.

  Pool per athlete-season comes from the pack's athlete_keys, so the tilt
  and the amplitude need no file. Ratings inside the solve are
  100 * pool_mean / exp(a) over athlete-seasons with >= 3 races, the same
  anchor pair_ratings uses; they are gauge-free, so the reference-group and
  reference-knot conventions never reach them.

WHAT TO READ IN THE LOG
  [joint] level        mu[TF] - mu[XC]: the SURFACE level, seasonal part
                       removed. Beside it, the old engine's applied gap.
  [joint] curve        per pool: the anchored curve at the knots and the
                       Nov -> Mar move. That move plus the level is what
                       the old bbar carried as one number.
  [joint] held-out     error sd on 10% of rows, by sport, with coverage.
                       Compare with pair_all --validate's 'pair/split' line.

⚠ COST. Each outer iteration is one conjugate-gradient solve over every row
  (~61M); each posterior probe is another. --probes 16 for a first run.
"""

import argparse
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import joint_solve as js                                        # noqa: E402
import pair_engine as pe                                        # noqa: E402


# Purpose:   a race is one running of one cell on one day.
# ★ THE GRAIN IS (cell, day), NOT (meet, division). Weather, mud and how the
#   pace went out are shared by everyone on the course that day, across
#   divisions; and the pack carries `days` (days ago) per row where a meet id
#   is not available. Coarser than a division, right for conditions.
def raceCodes(course, day):
    key = np.stack([np.asarray(course).astype(np.int64),
                    np.asarray(day).astype(np.int64)], axis=1)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    return inv.astype(np.int64), int(inv.max()) + 1


# Purpose:   one shrinkage group per CELL. XC is group 0 -- the REFERENCE
#            level the abilities carry -- and TF is group 1, whose mu is the
#            track-versus-XC surface level.
def cellGroups(course, sport, n_cells):
    if sport is None:
        return np.zeros(n_cells, dtype=np.int64), 1
    s = np.where(np.asarray(sport) > 0, 1, 0).astype(np.int64)
    tot = np.bincount(course, weights=s, minlength=n_cells)
    cnt = np.maximum(np.bincount(course, minlength=n_cells), 1)
    return (tot / cnt >= 0.5).astype(np.int64), 2


def poolCodes(athlete_keys):
    """Bare pool name -> code, per ATHLETE code, plus the name list."""
    names, index, out = [], {}, np.zeros(len(athlete_keys), dtype=np.int64)
    for code, key in enumerate(athlete_keys):
        raw = key[1] if (key is not None and len(key) > 1 and key[1]) else ""
        name = str(raw).split("|", 1)[0] or "unknown"
        if name not in index:
            index[name] = len(names)
            names.append(name)
        out[code] = index[name]
    return out, names


def openers(athlete_raw, year, sport, days):
    """is_first per row: the earliest race of each (athlete, year, sport)."""
    from rust_fitness import seasonPosition
    season = (year.astype(np.int64) * 2
              + (sport.astype(np.int64) if sport is not None else 0))
    _days_since, is_first = seasonPosition(athlete_raw.astype(np.int64),
                                           season, -days.astype(np.float64))
    return is_first


def sortRowsByAthlete(cols):
    """The pack's rows, reordered so one athlete-season's rows sit together.

    ★ THE SOLVE IS MEMORY-BOUND, NOT ARITHMETIC-BOUND (2026-09-02). Every
      CG iteration scatters 59M weighted rows into 13M athlete slots and
      gathers 13M abilities back out; in the pack's arrival order those
      are cache misses, one per row. Sorted by athlete they are sequential
      and each of the two costs about half. The cells and races are small
      enough to live in cache either way.

    ! EVERY PER-ROW ARRAY MOVES TOGETHER, including result_id and norm, so
      nothing downstream -- the design, the held-out split, the go-live
      writer -- can tell the rows were reordered. Per-key arrays (course
      and athlete keys) are not per-row and stay put."""
    n = cols["norm"].shape[0]
    order = np.lexsort((np.asarray(cols["year"]), np.asarray(cols["athlete"])))
    out = {}
    for k, v in cols.items():
        arr = v if isinstance(v, np.ndarray) else None
        if arr is not None and arr.ndim >= 1 and arr.shape[0] == n \
                and k not in ("course_keys", "athlete_keys"):
            out[k] = arr[order]
        else:
            out[k] = v
    return out


# ★ THE TRACK DISTANCE CLASSES (issue 148). One class per (pool, track
#   distance rounded to 100 m) over EVERY row of the pack, so a held-out
#   split shares the code space; the pool's reference event (DIST_REF where
#   the pool has it, else its most-raced event) is pinned and reads -1, as
#   does every XC row and every row without a distance. Returns the per-row
#   FREE class index, the labels of the free classes, and the reference
#   distance per pool name.
DIST_REF = 1600.0


def distClasses(cols, pool_of_athlete, pool_names, ref=DIST_REF):
    n = cols["norm"].shape[0]
    out = np.full(n, -1, dtype=np.int64)
    if "dist_m" not in cols:
        return out, [], {}
    dist = np.asarray(cols["dist_m"], dtype=np.float64)
    sport = np.asarray(cols["sport"])
    pool_row = pool_of_athlete[np.asarray(cols["athlete"])]
    m = (sport == 1) & (dist > 0)
    if not m.any():
        return out, [], {}
    bucket = (np.round(dist[m] / 100.0) * 100).astype(np.int64)
    key = pool_row[m] * 1_000_000 + bucket
    uniq, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    u_pool = (uniq // 1_000_000).astype(np.int64)
    u_dist = (uniq % 1_000_000).astype(np.int64)
    ref_of = {}
    for p in np.unique(u_pool):
        mine = np.flatnonzero(u_pool == p)
        at_ref = mine[u_dist[mine] == int(round(ref / 100.0)) * 100]
        pick = at_ref[0] if at_ref.size else mine[np.argmax(cnt[mine])]
        ref_of[int(p)] = int(pick)
    is_ref = np.zeros(uniq.size, dtype=bool)
    is_ref[list(ref_of.values())] = True
    free = np.full(uniq.size, -1, dtype=np.int64)
    free[~is_ref] = np.arange(int((~is_ref).sum()))
    out[m] = free[inv]
    labels = [f"{pool_names[int(u_pool[i])]}:{int(u_dist[i])}"
              for i in np.flatnonzero(~is_ref)]
    refs = {pool_names[p]: int(u_dist[i]) for p, i in ref_of.items()}
    return out, labels, refs


def logDistCentered(cols, keep, athlete, n_ath):
    """The row's log distance minus its athlete-season's mean over the
    rows that have one; 0 where the row has none. Issue 154."""
    dist = np.asarray(cols["dist_m"], dtype=np.float64)[keep]
    has = dist > 0
    lz = np.zeros(dist.size)
    lz[has] = np.log(dist[has])
    tot = np.bincount(athlete, weights=lz, minlength=n_ath)
    cnt = np.bincount(athlete[has], minlength=n_ath)
    mean = np.where(cnt > 0, tot / np.maximum(cnt, 1), 0.0)
    out = np.where(has, lz - mean[athlete], 0.0)
    return out


def seasonLinks(athlete_raw, year, athlete, n_ath):
    """Consecutive athlete-seasons of one athlete key (person, pool):
    (k0, k1, 1 / years apart). Issue 154."""
    raw_of = np.zeros(n_ath, dtype=np.int64)
    yr_of = np.zeros(n_ath, dtype=np.int64)
    raw_of[athlete] = athlete_raw
    yr_of[athlete] = year
    order = np.lexsort((yr_of, raw_of))
    r, y = raw_of[order], yr_of[order]
    same = r[1:] == r[:-1]
    k0 = order[:-1][same]
    k1 = order[1:][same]
    dt = np.maximum(y[1:][same] - y[:-1][same], 1).astype(np.float64)
    return k0, k1, 1.0 / dt


def venueAltitude(cols, keep, floor_m=js.ALT_FLOOR_M):
    """Per row, km of the cell's venue elevation above the floor (issue
    172), from venue_elevation keyed by the cell key's venue part; 0 where
    unknown. Returns (x, n_cells_known, n_cells)."""
    from database import getConn
    keys = [str(k) for k in cols["course_keys"]]
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.venue_elevation')")
        if cur.fetchone()[0] is None:
            return None, 0, len(keys)
        cur.execute("SELECT key, elevation_m FROM venue_elevation")
        elev = {k: float(v) for k, v in cur.fetchall()}
    per_cell = np.zeros(len(keys))
    known = 0
    for i, k in enumerate(keys):
        if k.startswith("XC:"):
            venue = ":".join(k.split(":")[:2])            # XC:<canonical>
        else:
            venue = ":".join(k.split(":")[:3])            # TF:loc:<id>
        e = elev.get(venue)
        if e is not None:
            per_cell[i] = max(e - floor_m, 0.0) / 1000.0
            known += 1
    course = cols["course"][keep].astype(np.int64)
    return per_cell[course], known, len(keys)


def bandLabels(base_labels):
    """'hs_m:3200' -> 'hs_m:3200:b0', ':b1', ':b2', in class order."""
    return [f"{lab}:b{b}" for lab in base_labels for b in range(js.DIST_N_BAND)]


def buildDesign(cols, keep, sport_offset=True, curve=True, rust=True,
                sizes=None, dist=True, slope=True, link=True, altitude=False,
                dist_bands=True):
    """A Design over the rows in `keep`, plus the per-athlete-season pool
    codes and names. `sizes` (from a full design) keeps a subset aligned.
    The distance classes ride on the Design as `dist_labels` / `dist_refs`."""
    athlete_raw = cols["athlete"][keep]
    year = cols["year"][keep]
    course = cols["course"][keep].astype(np.int64)
    days = cols["days"][keep]
    sport = cols["sport"][keep] if "sport" in cols else None

    athlete, n_ath = pe.athleteSeasonCodes(cols["athlete"], cols["year"])
    athlete = athlete[keep]                       # codes over ALL rows: aligned
    race_all, n_race = raceCodes(cols["course"], cols["days"])
    race = race_all[keep]
    n_cells = len(cols["course_keys"])
    group, _n_grp = cellGroups(cols["course"][cols["course"] >= 0],
                               None if sport is None
                               else cols["sport"][cols["course"] >= 0],
                               n_cells)

    sc = (pe.sportCentered(sport, athlete, n_ath)
          if sport_offset and sport is not None else None)

    pool_of_athlete, pool_names = poolCodes(cols["athlete_keys"])
    pool_row = pool_of_athlete[athlete_raw]
    athlete_pool = np.zeros(n_ath, dtype=np.int64)
    athlete_pool[athlete] = pool_row
    n_pool = len(pool_names)

    day = cols["doy"][keep] if curve else None
    first = (openers(athlete_raw, year, sport, days) if rust else None)

    dist_row, dist_labels, dist_refs = None, [], {}
    if dist and "dist_m" in cols:
        classes, dist_labels, dist_refs = distClasses(cols, pool_of_athlete,
                                                      pool_names)
        if dist_labels:
            dist_row = classes[keep]

    lz = (logDistCentered(cols, keep, athlete, n_ath)
          if slope and "dist_m" in cols else None)
    links = (seasonLinks(athlete_raw, year, athlete, n_ath)
             if link else None)
    alt, alt_known, alt_cells = None, 0, 0
    if altitude:
        alt, alt_known, alt_cells = venueAltitude(cols, keep)
        if alt is None:
            print("[joint] altitude: venue_elevation is absent -- run "
                  "scripts/build_venue_elevation.py; the term is OFF")

    D = js.Design(athlete, course, race, group_of_cell=group, sc=sc,
                  pool_row=pool_row if (curve or rust) else None,
                  day=day, first=first,
                  n_ath=n_ath, n_cell=n_cells, n_race=n_race, n_pool=n_pool,
                  dist=dist_row, n_e=len(dist_labels) if dist_row is not None
                  else None, lz=lz, link=links, alt=alt,
                  dist_banded=dist_bands)
    D.dist_labels = (bandLabels(dist_labels) if D.dist_banded
                     else dist_labels)
    D.dist_refs = dist_refs
    D.alt_known, D.alt_cells = alt_known, alt_cells
    return D, athlete_pool, pool_names


def reportLevelAndCurve(out, D, pool_names, old_gap=None):
    if D.n_group > 1:
        level = float(out["mu"][1] - out["mu"][0])
        line = f"[joint] level: TF - XC surface level mu {level:+.5f}"
        if old_gap is not None:
            line += (f"   (the sequential engine's applied TF - XC gap, "
                     f"season included: {old_gap:+.5f})")
        print(line)
    if "curve_anchored" in out:
        knots = out["curve_knot_days"]
        print("[joint] curve: anchored log-time offset by academic day "
              "(0 = 1 Aug), per pool; negative = fitter than the pool's "
              "own year mean")
        head = "".join(f"{int(k):>7d}" for k in knots)
        print(f"    {'pool':<10}{head}   Nov->Mar")
        for p, name in enumerate(pool_names):
            c = out["curve_anchored"][p]
            nov = np.interp(92, knots, c)
            mar = np.interp(212, knots, c)
            vals = "".join(f"{v:>+7.3f}" for v in c)
            print(f"    {name:<10}{vals}   {mar - nov:+.4f}")
    if out.get("rust") is not None:
        print("[joint] opener rust per pool: " + ", ".join(
            f"{n} {r:+.4f}" for n, r in zip(pool_names, out["rust"])))
    if out.get("beta") is not None:
        b = out["beta"]
        print(f"[joint] sport offset: |beta| mean {np.abs(b).mean():.4f}, "
              f"share > 0.02: {(np.abs(b) > 0.02).mean():.1%}, "
              f"unweighted mean {b.mean():+.5f}")
    reportDistOffsets(out, D)
    if out.get("slope") is not None:
        g = out["slope"]
        print(f"[joint] endurance slope: |g| mean {np.abs(g).mean():.4f}, "
              f"share > 0.02: {(np.abs(g) > 0.02).mean():.1%}, "
              f"share exactly 0 (one distance raced): "
              f"{(g == 0).mean():.1%}")
    if getattr(D, "has_link", False):
        print(f"[joint] season link: {D.link_k0.size:,} consecutive-season "
              f"pairs at weight {js.LINK_WEIGHT} / year")
    if out.get("altitude_coef") is not None:
        k = out["altitude_coef"]
        rows_alt = int((D.alt > 0).sum())
        print(f"[joint] altitude ({'fitted around' if args_altitude_fit() else 'held at'} "
              f"{js.ALT_PRIOR_MEAN}): k {np.round(k, 4)} log-time per km above "
              f"{js.ALT_FLOOR_M:.0f} m [XC TF]; at 1500 m that is "
              f"{', '.join(f'{100 * v * 0.9:+.1f}%' for v in k)}; "
              f"{D.alt_known:,} of {D.alt_cells:,} cells have an elevation, "
              f"{rows_alt:,} rows above the floor")


_ALT_FIT = {"on": False}


def args_altitude_fit():
    return _ALT_FIT["on"]


def reportDistOffsets(out, D, pools=("hs_m", "hs_f", "ms_m", "ms_f",
                                     "college_m", "college_f")):
    """The fitted track distance offsets, per pool: log-time against the
    pool's reference event, + = that event normalises SLOW (its ratings
    were reading low, and now rise by that much)."""
    e = out.get("dist_offset")
    labels = getattr(D, "dist_labels", [])
    if e is None or not labels:
        print("[joint] track distance offsets: OFF (pack has no dist_m, or "
              "--no-dist)")
        return
    rows = np.bincount(D.e_idx, weights=D.e_w, minlength=D.n_e)
    by_pool = {}
    for i, lab in enumerate(labels):
        parts = lab.split(":")
        p, d = parts[0], int(parts[1])
        band = int(parts[2][1:]) if len(parts) > 2 else 1
        by_pool.setdefault(p, {}).setdefault(d, {})[band] = (float(e[i]), int(rows[i]))
    banded = getattr(D, "dist_banded", False)
    bands_txt = (f", by rating band (<{js.DIST_BANDS[0]:.0f} / mid / "
                 f">={js.DIST_BANDS[1]:.0f})" if banded else "")
    print("[joint] track distance offsets, log-time vs the pool's reference "
          f"event (+ = that event was normalising slow){bands_txt}:")
    for p in list(pools) + sorted(k for k in by_pool if k not in pools):
        if p not in by_pool:
            continue
        ref = D.dist_refs.get(p, "?")
        cells = []
        for d in sorted(by_pool[p]):
            bb = by_pool[p][d]
            if sum(n for _v, n in bb.values()) < 1000:
                continue
            if banded:
                cells.append(f"{d}: " + "/".join(
                    f"{bb[b][0]:+.4f}" if b in bb and bb[b][1] >= 200 else "  --  "
                    for b in range(js.DIST_N_BAND))
                    + f" ({sum(n for _v, n in bb.values()):,})")
            else:
                v, n = bb.get(1, (0.0, 0))
                cells.append(f"{d}: {v:+.4f} ({n:,})")
        print(f"    {p:<10} ref {ref}   {'  '.join(cells)}")


def holdout(cols, keep, args, athlete_pool, D_full):
    import pair_validate as pv
    y_all = np.log(cols["norm"])
    idx = np.flatnonzero(keep)
    te_local = pv.splitByRow(idx.size, frac=0.10, seed=1)
    keep_tr = np.zeros(keep.size, dtype=bool); keep_tr[idx[~te_local]] = True
    keep_te = np.zeros(keep.size, dtype=bool); keep_te[idx[te_local]] = True
    D_tr, _, _ = buildDesign(cols, keep_tr, not args.no_sport_offset,
                             not args.no_curve, not args.no_rust,
                             dist=not args.no_dist, slope=not args.no_slope,
                             link=args.link and not args.no_link,
                             dist_bands=not args.no_dist_bands)
    D_te, _, _ = buildDesign(cols, keep_te, not args.no_sport_offset,
                             not args.no_curve, not args.no_rust,
                             dist=not args.no_dist, slope=not args.no_slope,
                             link=args.link and not args.no_link,
                             dist_bands=not args.no_dist_bands)
    t0 = time.time()
    out = js.solveJoint(y_all[keep_tr], design=D_tr, athlete_pool=athlete_pool,
                        n_outer=args.outer, robust=not args.no_robust,
                        tilt=not args.no_tilt, n_probe=4,
                        curve_smooth=args.curve_smooth, curve_gap=args.curve_gap, winter_gain=args.winter_gain, verbose=False)
    pred, cov = js.predictHeldOut(out, D_tr, D_te, athlete_pool=athlete_pool,
                                  tilt=not args.no_tilt)
    y_te = y_all[keep_te]
    err = y_te[cov] - pred[cov]
    print(f"\n[joint] held-out (10% of rows, seed 1): error sd {err.std():.6f}"
          f"   covered {cov.mean():.1%}   [{time.time() - t0:.0f}s]")
    sport_te = cols["sport"][keep_te] if "sport" in cols else None
    if sport_te is not None:
        for code, name in ((0, "XC"), (1, "TF")):
            m = cov & (sport_te == code)
            if m.sum() > 1000:
                e = y_te[m] - pred[m]
                print(f"        {name}: {e.std():.6f}  ({int(m.sum()):,} rows)")
    print("        compare: pair_all --validate 'pair/split' (ridge 0.5) "
          "scored 0.044325 on 2026-08-31's corpus")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default=os.path.join(_HERE, "data",
                                                   "packed_XC_TF.npz"))
    ap.add_argument("--out", default=os.path.join(_HERE, "data",
                                                  "joint_difficulty.npz"))
    ap.add_argument("--outer", type=int, default=6)
    ap.add_argument("--probes", type=int, default=16)
    ap.add_argument("--curve-smooth", type=float, default=js.CURVE_SMOOTH,
                    help="second-difference weight on the year curve, as a "
                         "multiple of rows-per-knot (a prior, not tunable "
                         "by held-out error)")
    ap.add_argument("--holdout", action="store_true",
                    help="also fit on 90%% of rows and score the rest")
    ap.add_argument("--curve-gap", type=float, default=js.CURVE_GAP_WEIGHT,
                    help="weight (x rows per pool) pinning the curve's "
                         "track-window mean to its XC-window mean, so the "
                         "level mu carries the whole between-sport "
                         "difference (issue 143); 0 = the smoothness prior "
                         "alone decides the split")
    ap.add_argument("--winter-gain", type=float, default=js.WINTER_GAIN,
                    help="stated fall-to-spring fitness gain of the average "
                         "athlete, log-time (0.03 = 3%%): the curve's track "
                         "window sits this far below its XC window and the "
                         "level carries the rest. 0 books it all into the "
                         "level (issue 113). An assumption, not a measurement")
    ap.add_argument("--no-curve", action="store_true")
    ap.add_argument("--no-rust", action="store_true")
    ap.add_argument("--no-dist", action="store_true",
                    help="no per-(pool, track distance) offset (issue 148)")
    ap.add_argument("--altitude", action="store_true",
                    help="the altitude term (issue 172): one coefficient per "
                         "sport on the venue's elevation above 600 m, from "
                         "venue_elevation (scripts/build_venue_elevation.py). "
                         "Off by default until the owner has read k.")
    ap.add_argument("--altitude-fit", action="store_true",
                    help="let the bridge athletes move the altitude "
                         "coefficient (prior sd ~0.01 around 0.035/km); "
                         "default holds it at the physiology")
    ap.add_argument("--tau-tf-max", type=float, default=None,
                    help="cap the track cells' prior sd (log time), e.g. 0.02: "
                         "more shrinkage toward the track level than the "
                         "data estimate (issue 161). Unset = the estimate.")
    ap.add_argument("--no-dist-bands", action="store_true",
                    help="one offset per (pool, event) instead of three by "
                         "rating band (issue 167)")
    ap.add_argument("--no-slope", action="store_true",
                    help="no per-athlete endurance slope (issue 154)")
    # ★ OFF BY DEFAULT (2026-09-04). The one run with it on (run9) put the
    #   Woodbridge 2025 day at u = -0.27 against a cell of +0.19 and took
    #   7% off the elite ratings there, with the same pack and gain as the
    #   run before. Mechanism: the link is zero-mean, the population
    #   improves ~5% a year, and a thin season is most of any big field --
    #   pulled toward last year, every young runner reads slower than they
    #   ran, the day looks fast, u goes negative, and the ratings on that
    #   day carry it. A smoothing prior on a drifting population is a bias.
    ap.add_argument("--link", action="store_true",
                    help="the consecutive-season link (issue 154); off by "
                         "default, see the note above")
    ap.add_argument("--no-link", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-sport-offset", action="store_true")
    ap.add_argument("--no-tilt", action="store_true")
    ap.add_argument("--no-robust", action="store_true")
    # ⚠ THE LIVE SWITCH (issue 116). --golive writes course_difficulties,
    #   athlete_ratings, results.speed_rating and pair_difficulty.npz from
    #   THIS solve, through joint_golive. --golive-dry builds all of it and
    #   writes only the npz, for a look before the irreversible part.
    ap.add_argument("--golive", action="store_true")
    ap.add_argument("--golive-dry", action="store_true")
    ap.add_argument("--anchor", default="career",
                    choices=("career", "seasonal"),
                    help="pool mean for the per-result rating")
    ap.add_argument("--collapse", default="best",
                    choices=("best", "recent", "weighted"),
                    help="season -> athlete_ratings row")
    ap.add_argument("--no-race-effect", action="store_true",
                    help="leave the race-day effect out of per-result ratings")
    args = ap.parse_args()


    if not os.path.exists(args.pack):
        sys.exit(f"[joint] no pack at {args.pack} -- run 07_pack first")

    print(f"[joint] loading {args.pack}")
    cols = pe.loadPack(args.pack)
    cols = sortRowsByAthlete(cols)
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    y = np.log(cols["norm"][keep])

    _ALT_FIT["on"] = bool(args.altitude_fit)
    D, athlete_pool, pool_names = buildDesign(
        cols, keep, not args.no_sport_offset, not args.no_curve,
        not args.no_rust, dist=not args.no_dist, slope=not args.no_slope,
        link=args.link and not args.no_link, altitude=args.altitude,
        dist_bands=not args.no_dist_bands)
    print(f"[joint] {D.n:,} rows | {D.n_ath:,} athlete-seasons | "
          f"{D.n_cell:,} cells | {D.n_race:,} races | {D.n_group} sport "
          f"groups | {D.n_pool} pools {pool_names}")
    print(f"[joint] blocks: sport offset {'ON' if D.n_beta else 'off'}, "
          f"year curve {'ON' if D.n_c else 'off'} "
          f"({D.n_knot} knots x {D.knot_days:g} days, smooth "
          f"{args.curve_smooth:g}, window gap weight {args.curve_gap:g}, "
          f"stated winter gain {args.winter_gain:g}), "
          f"rust {'ON' if D.n_r else 'off'}, "
          f"track distance offsets {f'ON ({D.n_e} classes' + (', by rating band)' if D.dist_banded else ')') if D.n_e else 'off'}, "
          f"endurance slope {'ON' if D.n_g else 'off'}, "
          f"season link {'ON' if getattr(D, 'has_link', False) else 'off'}, "
          f"altitude {'ON' if getattr(D, 'n_k', 0) else 'off'}, "
          f"tilt {'off' if args.no_tilt else 'ON (own ability)'}, "
          f"robust {'off' if args.no_robust else 'ON'}")

    if args.holdout:
        holdout(cols, keep, args, athlete_pool, D)

    t0 = time.time()
    out = js.solveJoint(y, design=D, athlete_pool=athlete_pool,
                        n_outer=args.outer, robust=not args.no_robust,
                        tilt=not args.no_tilt, n_probe=args.probes,
                        curve_smooth=args.curve_smooth, curve_gap=args.curve_gap, winter_gain=args.winter_gain, verbose=True,
                        tau_max={1: args.tau_tf_max} if args.tau_tf_max else None,
                        alt_prior_pen=(js.ALT_PRIOR_PEN_FIT if args.altitude_fit
                                       else js.ALT_PRIOR_PEN_FIXED))
    print(f"[joint] solved in {time.time() - t0:.0f}s")

    delta = out["delta"]
    rows_per_cell = np.bincount(D.cell, minlength=D.n_cell).astype(np.float64)
    solved = rows_per_cell > 0
    anchored = delta - np.average(delta[solved], weights=rows_per_cell[solved])
    print(f"\n[joint] sigma {np.sqrt(out['sigma2']):.5f} | race-day sigma_u "
          f"{np.sqrt(out['sigma_u2']):.5f} | tau {np.sqrt(out['tau2'])}")
    print(f"[joint] {out['n_downweighted']:,} rows down-weighted, 0 dropped")
    print(f"[joint] cell SE: median {np.median(out['cell_se']):.4f}, "
          f"p95 {np.percentile(out['cell_se'], 95):.4f}")

    old_gap = None
    old_path = os.path.join(os.path.dirname(args.out), "pair_difficulty.npz")
    old_delta = None
    if os.path.exists(old_path):
        with np.load(old_path, allow_pickle=False) as old:
            if "difficulty_raw" in old.files:
                old_delta = np.log1p(old["difficulty_raw"])
                keys = [str(k) for k in cols["course_keys"]]
                is_xc = np.array([k.startswith("XC:") for k in keys])
                is_tf = np.array([k.startswith("TF:") for k in keys])
                ok = np.isfinite(old_delta) & (old_delta != 0) & solved
                if (ok & is_xc).any() and (ok & is_tf).any():
                    old_gap = float(old_delta[ok & is_tf].mean()
                                    - old_delta[ok & is_xc].mean())
    reportLevelAndCurve(out, D, pool_names, old_gap)

    save = dict(delta=delta, delta_anchored=anchored, mu=out["mu"],
                cell_se=out["cell_se"], cell_var=out["cell_var"],
                race_effect=out["race_effect"], sigma2=out["sigma2"],
                sigma_u2=out["sigma_u2"], tau2=out["tau2"],
                rows_per_cell=rows_per_cell,
                course_keys=np.array([str(k) for k in cols["course_keys"]]),
                ability=out["ability"].astype(np.float32),
                athlete_pool=athlete_pool.astype(np.int16),
                n_races=out["n_races"].astype(np.int32),
                pool_names=np.array(pool_names))
    if out.get("rating") is not None:
        save["rating"] = out["rating"].astype(np.float32)
    if out.get("beta") is not None:
        save["beta"] = out["beta"].astype(np.float32)
    if "curve_anchored" in out:
        save.update(curve=out["curve"], curve_anchored=out["curve_anchored"],
                    curve_knot_days=out["curve_knot_days"],
                    curve_lambda=out["curve_lambda"])
    if out.get("rust") is not None:
        save["rust"] = out["rust"]
    if out.get("slope") is not None and D.n_g:
        save["slope"] = out["slope"]
    if out.get("altitude_coef") is not None and getattr(D, "n_k", 0):
        save["altitude_coef"] = out["altitude_coef"]
        save["altitude_floor_m"] = np.array([js.ALT_FLOOR_M])
    if out.get("dist_offset") is not None and D.n_e:
        save["dist_offset"] = out["dist_offset"]
        save["dist_labels"] = np.array(D.dist_labels)
        save["dist_bands"] = np.array(js.DIST_BANDS if D.dist_banded else [])
        save["dist_rows"] = np.bincount(D.e_idx, weights=D.e_w,
                                        minlength=D.n_e).astype(np.int64)
    np.savez(args.out, **save)
    print(f"[joint] wrote {args.out}")

    if args.golive or args.golive_dry:
        import joint_golive as jg
        live = jg.buildLive(out, D, cols, keep, collapse=args.collapse,
                            anchor=args.anchor,
                            use_race_effect=not args.no_race_effect)
        jg.report(live)
        jg.writeNpz(live, os.path.join(os.path.dirname(args.out),
                                       "pair_difficulty.npz"))
        if args.golive:
            jg.writeLive(live)
        else:
            print("[joint/live] --golive-dry: nothing written to the database")

    # ★ THE COMPARISON IS THE POINT. Same cells -- so a large move is the

    #   race-day term, the robust weights and the curve, and it should be
    #   biggest exactly where the old engine's SE was least trustworthy.
    if old_delta is not None:
        m = np.isfinite(old_delta) & (old_delta != 0) & solved
        if m.sum() > 100:
            d = anchored[m] - old_delta[m]
            print(f"\n[joint] vs pair_difficulty over {int(m.sum()):,} cells: "
                  f"median |move| {np.median(np.abs(d)):.4f}, p95 "
                  f"{np.percentile(np.abs(d), 95):.4f}, corr "
                  f"{np.corrcoef(anchored[m], old_delta[m])[0, 1]:.4f}")


if __name__ == "__main__":
    main()
