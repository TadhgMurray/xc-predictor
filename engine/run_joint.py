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
# set from --no-race-term before the design is built; a module-level flag
# for the same reason _ALT_FIT is one -- buildDesign is called from three
# places and threading a bool through all of them buys nothing.
_NO_RACE_TERM = {"on": False}
_RACE_KEY = {"by": "cell"}          # or "venue"; see venueOfCell


# ★★ WHAT SHARES A RACE-DAY EFFECT, and it is a real modelling choice.
#
#    A race has always been (CELL, day), where a cell is (venue, distance).
#    That already pools divisions -- varsity, JV and the girls' race at the
#    same venue and distance on one day share a single u, which is Beyer's
#    "track variant" computed off a whole card rather than off one race.
#
#    What it does NOT pool is distance. Morley raced 4700, 4800, 4900 and
#    5000 on the same afternoon gets FOUR separate day effects for one
#    weather, one ground, one set of conditions.
#
#  ! AND THE OWNER'S CORRECTION MATTERS HERE: a venue can host several
#    GENUINELY DIFFERENT courses, so those four cells legitimately differ
#    in difficulty. That is an argument for keeping d per cell -- which
#    this does not touch -- and it is also the argument FOR pooling u: if
#    the routes really differ, we want the cell to carry the route and the
#    day term to carry only the day, and a day term estimated across the
#    whole venue is the cleaner separation.
#
#  ⚠ IT IS NOT OBVIOUSLY RIGHT. A "rain course" is a different route used
#    in bad weather, so two courses at one venue on one day can genuinely
#    face different conditions. Hence a flag and a ladder rung, not a
#    change to the shipped model.
def venueOfCell(course_keys):
    """Cell key -> venue id, dropping the distance. 'XC:Morley:d4800' and
    'XC:Morley:d5000' are one venue; TF keys carry no distance and are
    already venue-grained."""
    venue = []
    for k in course_keys:
        k = str(k)
        head, tag, _dist = k.rpartition(":d")
        venue.append(head if tag and _dist.isdigit() else k)
    _, inv = np.unique(np.array(venue), return_inverse=True)
    return inv.astype(np.int64)


def raceCodes(course, day, venue_of_cell=None):
    unit = (np.asarray(course).astype(np.int64) if venue_of_cell is None
            else np.asarray(venue_of_cell)[np.asarray(course).astype(np.int64)])
    key = np.stack([unit, np.asarray(day).astype(np.int64)], axis=1)
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


def distRefRows(cols, pool_of_athlete, refs, pool_names):
    """Per row, is it the pool's pinned reference event (the other side
    of every season-best pair, js.DIST_CAL_SHARE). False without dist_m."""
    n = cols["norm"].shape[0]
    out = np.zeros(n, dtype=bool)
    if "dist_m" not in cols or not refs:
        return out
    dist = np.asarray(cols["dist_m"], dtype=np.float64)
    sport = np.asarray(cols["sport"])
    pool_row = pool_of_athlete[np.asarray(cols["athlete"])]
    bucket = (np.round(dist / 100.0) * 100).astype(np.int64)
    ref_of_pool = np.full(len(pool_names), -1, dtype=np.int64)
    for p, name in enumerate(pool_names):
        if name in refs:
            ref_of_pool[p] = int(refs[name])
    out = (sport == 1) & (dist > 0) & (bucket == ref_of_pool[pool_row])
    return out


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
    cell_known = np.zeros(len(keys), dtype=bool)
    known = 0
    for i, k in enumerate(keys):
        if k.startswith("XC:"):
            venue = ":".join(k.split(":")[:2])            # XC:<canonical>
        else:
            venue = ":".join(k.split(":")[:3])            # TF:loc:<id>
        e = elev.get(venue)
        if e is not None:
            per_cell[i] = max(e - floor_m, 0.0) / 1000.0
            cell_known[i] = True
            known += 1
    course = cols["course"][keep].astype(np.int64)
    venueAltitude.known_row = cell_known[course]        # for homeAltitude
    return per_cell[course], known, len(keys)


def bandLabels(base_labels):
    """'hs_m:3200' -> 'hs_m:3200:b0', ':b1', ':b2', in class order."""
    return [f"{lab}:b{b}" for lab in base_labels for b in range(js.DIST_N_BAND)]


# ★★★ COURSES CHANGE. Mt. SAC started grooming before the meet and got
#     faster; Morley's route was altered; a groundsman moves a turn. One
#     difficulty for every year a venue ever existed averages those together
#     and is wrong in both directions at once.
#
#     eraCells splits each cell into (cell, era) with eras `years` wide and
#     hands back the adjacency the solver needs to tie them together. The
#     tying is the point: see joint_solve.ERA_DRIFT_SD. Splitting alone
#     would shatter every thin course into noise.
#
#  ! ONLY PAIRS THAT EXIST. A venue with races in 2014 and 2024 and nothing
#    between gets ONE pair spanning five era-steps, weighted 1/5 -- a random
#    walk's variance grows with elapsed time, so a decade of silence should
#    tie the two ends loosely, not pretend they are neighbours.
def eraCells(course, year, years, n_cells, group, course_keys):
    """(new_course_row, n_new, group_new, keys_new, era_pairs, era_w,
        eras_per_base).

    course: per-row cell id (-1 where the row has no cell). year: per row.
    """
    course = np.asarray(course, dtype=np.int64)
    year = np.asarray(year, dtype=np.int64)
    ok = course >= 0
    if not years or not ok.any():
        return (course, n_cells, group, course_keys, None, None, None)

    base_year = int(year[ok].min())
    era = np.zeros_like(course)
    era[ok] = (year[ok] - base_year) // int(years)
    # one id per (cell, era) that actually occurs
    key = np.where(ok, course * 10_000 + np.clip(era, 0, 9_999), -1)
    seen = np.unique(key[ok])
    remap = {int(k): i for i, k in enumerate(seen)}
    new = np.full(course.shape, -1, dtype=np.int64)
    new[ok] = np.array([remap[int(k)] for k in key[ok]], dtype=np.int64)

    base_of = (seen // 10_000).astype(np.int64)
    era_of = (seen % 10_000).astype(np.int64)
    n_new = seen.size
    group_new = np.asarray(group, dtype=np.int64)[base_of]
    keys_new = [f"{course_keys[b]}@e{e}" for b, e in zip(base_of, era_of)]

    # adjacency: consecutive eras of one base cell, in era order
    order = np.lexsort((era_of, base_of))
    bs, es = base_of[order], era_of[order]
    same = bs[1:] == bs[:-1]
    a = order[:-1][same]
    b = order[1:][same]
    gap = (es[1:] - es[:-1])[same].astype(np.float64)
    era_pairs = np.vstack([a, b]) if a.size else None
    era_w = 1.0 / np.maximum(gap, 1.0) if a.size else None
    eras_per_base = np.bincount(base_of, minlength=int(base_of.max()) + 1
                                )[base_of].astype(np.float64)
    return (new, n_new, group_new, keys_new, era_pairs, era_w, eras_per_base)


def buildDesign(cols, keep, sport_offset=True, curve=True, rust=True,
                sizes=None, dist=True, slope=True, link=True, altitude=False,
                dist_bands=True, split_ability=False, era_years=0):
    """A Design over the rows in `keep`, plus the per-athlete-season pool
    codes and names. `sizes` (from a full design) keeps a subset aligned.
    The distance classes ride on the Design as `dist_labels` / `dist_refs`."""
    athlete_raw = cols["athlete"][keep]
    year = cols["year"][keep]
    course = cols["course"][keep].astype(np.int64)
    days = cols["days"][keep]
    sport = cols["sport"][keep] if "sport" in cols else None

    # ★ ONE ABILITY PER SPORT-SEASON UNDER --split-ability. See
    #   athleteSeasonCodes: keyed (athlete, year) a single number has to
    #   serve an autumn 5k and a spring 800, which is what beta existed to
    #   patch. Split, the autumn-to-spring gain is the difference between two
    #   abilities and reaches the rating, because the rating IS the ability.
    athlete, n_ath = pe.athleteSeasonCodes(
        cols["athlete"], cols["year"],
        (cols["sport"] if split_ability and "sport" in cols else None))
    athlete = athlete[keep]                       # codes over ALL rows: aligned
    # see venueOfCell: --race-key venue pools the day effect across every
    #   distance raced at that venue on that day
    _voc = (venueOfCell(cols["course_keys"])
            if _RACE_KEY["by"] == "venue" else None)
    race_all, n_race = raceCodes(cols["course"], cols["days"], _voc)
    race = race_all[keep]
    # ★ A REAL ABLATION OF THE RACE-DAY TERM, which did not exist before.
    #   --no-race-effect only stops the term reaching the per-result RATING;
    #   the SOLVE kept it either way, so there was no way to ask "is the
    #   day effect earning its keep?" -- and an ablation rung using that
    #   flag would have scored the shipped model and been reported as a
    #   result. Collapsing every row to one race id leaves u as a single
    #   intercept, i.e. no day term at all.
    if _NO_RACE_TERM["on"]:
        race = np.zeros_like(race)
        n_race = 1
    n_cells = len(cols["course_keys"])
    group, _n_grp = cellGroups(cols["course"][cols["course"] >= 0],
                               None if sport is None
                               else cols["sport"][cols["course"] >= 0],
                               n_cells)
    # ★ ERA SPLIT, BEFORE ANYTHING READS n_cells. Done over ALL rows (not
    #   just `keep`) so a subset design keeps the same cell ids as the full
    #   one -- the holdout compares the two.
    era_pairs = era_w = eras_per_base = None
    course_keys = list(cols["course_keys"])
    if era_years:
        (_all_new, n_cells, group, course_keys,
         era_pairs, era_w, eras_per_base) = eraCells(
            cols["course"], cols["year"], era_years,
            n_cells, group, course_keys)
        course = _all_new[keep]
        print(f"[joint] eras: {era_years}-year cells -> {n_cells:,} "
              f"(course, era) cells, {0 if era_pairs is None else era_pairs.shape[1]:,} "
              f"adjacent pairs tied by a random walk "
              f"(drift sd {js.ERA_DRIFT_SD:g}/era)")

    sc = (pe.sportCentered(sport, athlete, n_ath)
          if sport_offset and sport is not None else None)

    pool_of_athlete, pool_names = poolCodes(cols["athlete_keys"])
    pool_row = pool_of_athlete[athlete_raw]
    athlete_pool = np.zeros(n_ath, dtype=np.int64)
    athlete_pool[athlete] = pool_row
    n_pool = len(pool_names)

    day = cols["doy"][keep] if curve else None
    first = (openers(athlete_raw, year, sport, days) if rust else None)

    dist_row, dist_labels, dist_refs, dist_ref_row = None, [], {}, None
    if dist and "dist_m" in cols:
        classes, dist_labels, dist_refs = distClasses(cols, pool_of_athlete,
                                                      pool_names)
        if dist_labels:
            dist_row = classes[keep]
            dist_ref_row = distRefRows(cols, pool_of_athlete, dist_refs,
                                       pool_names)[keep]

    lz = (logDistCentered(cols, keep, athlete, n_ath)
          if slope and "dist_m" in cols else None)
    links = (seasonLinks(athlete_raw, year, athlete, n_ath)
             if link else None)
    alt, alt_known, alt_cells, alt_home = None, 0, 0, None
    if altitude:
        alt, alt_known, alt_cells = venueAltitude(cols, keep)
        if alt is None:
            print("[joint] altitude: venue_elevation is absent -- run "
                  "scripts/build_venue_elevation.py; the term is OFF")
        else:
            # where each athlete-season lives, by the venues it raced (192)
            home = js.homeAltitude(alt, venueAltitude.known_row, athlete, n_ath)
            alt_home = home[athlete]
            n_res = int((home > 0).sum())
            print(f"[joint] altitude: {n_res:,} athlete-seasons live above "
                  f"{js.ALT_FLOOR_M:.0f} m (median home {np.median(home[home > 0]) if n_res else 0:.2f} km "
                  f"above it); a resident's exposure is venue km - "
                  f"{js.ALT_ACCLIM:g} x home km (issue 192)")

    D = js.Design(athlete, course, race, group_of_cell=group, sc=sc,
                  pool_row=pool_row if (curve or rust) else None,
                  day=day, first=first,
                  n_ath=n_ath, n_cell=n_cells, n_race=n_race, n_pool=n_pool,
                  dist=dist_row, n_e=len(dist_labels) if dist_row is not None
                  else None, lz=lz, link=links, alt=alt,
                  dist_banded=dist_bands, dist_ref=dist_ref_row,
                  alt_home=alt_home,
                  era_pairs=era_pairs, era_w=era_w,
                  eras_per_base=eras_per_base)
    D.course_keys = course_keys
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


# ! 'XC,TF' -> {0: xc, 1: tf}. The floor is per sport now because the
#   course/day split is the priors' alone; see js.SIGMA_U_FLOOR.
def _sigmaUFloor(spec):
    if spec is None or (isinstance(spec, str) and spec == "default"):
        return dict(js.SIGMA_U_FLOOR)
    parts = [p.strip() for p in str(spec).split(",")]
    if len(parts) == 1:                      # one number means both sports
        parts = parts * 2
    if len(parts) != 2:
        raise SystemExit("--sigma-u-floor wants XC,TF (or one number)")
    return {i: float(v) for i, v in enumerate(parts)}


def pv_kinds():
    import pair_validate as pv
    return pv.HOLDOUT_KINDS


def args_altitude_fit():
    return _ALT_FIT["on"]


def banded_labels(labels):
    return bool(labels) and labels[0].count(":") == 2


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
    cal_mean = out.get("dist_cal_mean")
    cal_n = out.get("dist_cal_n")
    cal_via = out.get("dist_cal_via")
    base_labels = [lab.rsplit(":", 1)[0] for lab in labels[::js.DIST_N_BAND]] \
        if banded_labels(labels) else labels
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
    if banded and cal_n is not None and int((cal_n > 0).sum()):
        print(f"    ({int((cal_n > 0).sum())} of {len(labels)} classes carry a "
              f"prior mean from season-best pairs, weight {js.DIST_CAL_SHARE}; "
              f"the table below is the solved e, the pairs' median and "
              f"the pairs follow per pool)")
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
        if banded and cal_n is not None:
            pairs = []
            for d in sorted(by_pool[p]):
                txt = []
                for b in range(js.DIST_N_BAND):
                    i = labels.index(f"{p}:{d}:b{b}") if f"{p}:{d}:b{b}" in labels else -1
                    if i >= 0 and cal_n[i] > 0:
                        via = ""
                        if cal_via is not None and cal_via[i] >= 0:
                            via = "<" + base_labels[int(cal_via[i])].split(":")[-1]
                        txt.append(f"{cal_mean[i]:+.4f}({int(cal_n[i]):,}{via})")
                    else:
                        txt.append("  --  ")
                if any(t.strip() != "--" for t in txt):
                    pairs.append(f"{d}: " + "/".join(txt))
            if pairs:
                print(f"    {'':<10} pairs      {'  '.join(pairs)}")


# ★★ ONE PLACE THAT SAYS WHAT THE MODEL IS. The holdout used to pass a
#    hand-picked seven of these while the real solve passed eighteen, so it
#    scored a DIFFERENT MODEL from the one being shipped -- which is why a
#    whole day of tau-versus-sigma_u argument had no number to settle it.
#    Both callers go through here now; a new flag reaches both or neither.
def solveKwargs(args, athlete_pool, verbose):
    return dict(
        athlete_pool=athlete_pool,
        n_outer=args.outer,
        robust=not args.no_robust,
        tilt=not args.no_tilt,
        verbose=verbose,
        curve_smooth=args.curve_smooth,
        curve_gap=args.curve_gap,
        winter_gain=args.winter_gain,
        # ! hasattr, not `or` -- an explicit None from --tau-max none means
        #   NO CAP, and `or` would turn it back into "default".
        tau_max=(args._tau_caps if hasattr(args, "_tau_caps")
                 else ({1: args.tau_tf_max} if args.tau_tf_max
                       else "default")),
        alt_prior_pen=(js.ALT_PRIOR_PEN_FIT if args_altitude_fit()
                       else js.ALT_PRIOR_PEN_FIXED),
        dist_cal=not args.no_dist_cal,
        sport_gap_delta=args.sport_gap_delta,
        merge_sports=args.merge_sports,
        centre_curve=args.centre_curve,
        era_drift_sd=args.era_drift,
        identified_priors=not args.priors_from_all_cells,
        sigma_u_floor=_sigmaUFloor(args.sigma_u_floor),
        ability_weight=args.ability_weight,
        top_frac=args.top_frac,
    )


def holdout(cols, keep, args, athlete_pool, D_full):
    import pair_validate as pv
    y_all = np.log(cols["norm"])
    idx = np.flatnonzero(keep)
    # ★ THE GROUPS THE LADDER HOLDS OUT TOGETHER. race is (cell, day) as the
    #   solve sees it; athlete and cell come straight off the pack.
    race_all, _ = raceCodes(cols["course"], cols["days"],
                            venueOfCell(cols["course_keys"])
                            if _RACE_KEY["by"] == "venue" else None)
    kind = getattr(args, "holdout_kind", "race")
    te_local = pv.splitFor(kind, idx.size,
                           race=race_all[idx],
                           athlete=np.asarray(cols["athlete"])[idx],
                           cell=np.asarray(cols["course"])[idx],
                           frac=0.10, seed=1)
    keep_tr = np.zeros(keep.size, dtype=bool); keep_tr[idx[~te_local]] = True
    keep_te = np.zeros(keep.size, dtype=bool); keep_te[idx[te_local]] = True
    D_tr, _, _ = buildDesign(cols, keep_tr, not args.no_sport_offset,
                             not args.no_curve, not args.no_rust,
                             dist=not args.no_dist, slope=not args.no_slope,
                             link=args.link and not args.no_link,
                             dist_bands=not args.no_dist_bands,
                             split_ability=args.split_ability,
                             era_years=args.era_years)
    D_te, _, _ = buildDesign(cols, keep_te, not args.no_sport_offset,
                             not args.no_curve, not args.no_rust,
                             dist=not args.no_dist, slope=not args.no_slope,
                             link=args.link and not args.no_link,
                             dist_bands=not args.no_dist_bands,
                             split_ability=args.split_ability,
                             era_years=args.era_years)
    t0 = time.time()
    out = js.solveJoint(y_all[keep_tr], design=D_tr, n_probe=0,
                        **solveKwargs(args, athlete_pool, verbose=False))
    pred, cov = js.predictHeldOut(out, D_tr, D_te, athlete_pool=athlete_pool,
                                  tilt=not args.no_tilt)
    y_te = y_all[keep_te]
    err = y_te[cov] - pred[cov]
    # ⚠ SAY WHICH RUNG. "error sd 0.044" means nothing without it: holding
    #   out rows scores interpolation, holding out races scores prediction,
    #   and the two are not comparable numbers.
    _what = {"row": "10% of ROWS -- optimistic, the same race is in train",
             "race": "10% of RACES -- a whole new race at a known course",
             "athlete": "10% of ATHLETES -- rating a newcomer",
             "course": "10% of COURSES -- a course never seen before"}[kind]
    print(f"\n[joint] HELD OUT: {_what}")
    print(f"[joint] error sd {err.std():.6f}   covered {cov.mean():.1%}"
          f"   [{time.time() - t0:.0f}s]")
    sport_te = cols["sport"][keep_te] if "sport" in cols else None
    if sport_te is not None:
        for code, name in ((0, "XC"), (1, "TF")):
            m = cov & (sport_te == code)
            if m.sum() > 1000:
                e = y_te[m] - pred[m]
                print(f"        {name}: {e.std():.6f}  ({int(m.sum()):,} rows)")
    # ⚠ THE COMPARISON IS ONLY LEGAL ON THE ROW RUNG. pair_all's 0.044325
    #   was a ROW split -- the same race was in train, so its race-day
    #   effect was already fitted and the score is an INTERPOLATION. A race
    #   split cannot see the held-out day at all, so its error carries the
    #   whole race-day term on top. Printing the two side by side under a
    #   race split reads as "we got worse" when it is a harder question.
    if kind == "row":
        print("        compare: pair_all --validate 'pair/split' (ridge 0.5) "
              "scored 0.044325 on 2026-08-31's corpus")
    else:
        # the floor: a race we have never seen carries its own day, and no
        # model can know it in advance. sqrt(sigma^2 + sigma_u^2) is the
        # best any model can do here -- print it beside the score.
        _s2 = float(out["sigma2"])
        _su = np.atleast_1d(np.asarray(out["sigma_u2"], dtype=np.float64))
        _floor = float(np.sqrt(_s2 + _su.mean()))
        print(f"        floor {_floor:.6f} = sqrt(sigma^2 + sigma_u^2): a "
              f"new race carries its own day and NO model can know it in "
              f"advance. {err.std() / _floor:.3f}x the floor.")
        print("        NOT comparable to pair_all's 0.044325 -- that was a "
              "ROW split, which had the held-out race's day in train.")


# ★ THE PARSER AND ITS IMPLICATIONS ARE FUNCTIONS, NOT main()'s LOCALS, so
#   they can be exercised without a pack. tests/test_ablation_ladder.py
#   parses every ladder rung through them and asserts each one actually
#   changes the solve configuration -- a rung that silently parses back to
#   the baseline is a control group masquerading as a treatment, which is
#   exactly what `--tau-max ,` used to be.
def buildParser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default=os.path.join(_HERE, "data",
                                                   "packed_XC_TF.npz"))
    ap.add_argument("--out", default=os.path.join(_HERE, "data",
                                                  "joint_difficulty.npz"))
    ap.add_argument("--outer", type=int, default=6)
    # ★ For the ablation ladder: solve a sample so a rung costs minutes.
    #   See the note where it is applied -- sampling is by ATHLETE.
    ap.add_argument("--sample-pct", type=float, default=0.0, metavar="PCT",
                    help="solve only this %% of ATHLETES (0 = everyone). "
                         "For comparing configurations, not for go-live")
    ap.add_argument("--sample-seed", type=int, default=11)
    ap.add_argument("--probes", type=int, default=16)
    ap.add_argument("--curve-smooth", type=float, default=js.CURVE_SMOOTH,
                    help="second-difference weight on the year curve, as a "
                         "multiple of rows-per-knot (a prior, not tunable "
                         "by held-out error)")
    # ★ WHICH RUNG OF THE LADDER. See pair_validate.splitFor -- holding out
    #   rows scores interpolation (the same race is in train), holding out
    #   races scores prediction. `race` is the default because it is what
    #   the site actually does when new results land.
    ap.add_argument("--holdout-kind", default="race",
                    choices=list(pv_kinds()),
                    help="what to hold out together (default race)")
    # ★★ TIME-VARYING COURSE DIFFICULTY (owner, asked three times). 0 keeps
    #    one difficulty for all time; 2 or 3 splits each course into eras
    #    that width and ties adjacent ones with a random walk. See
    #    joint_solve.ERA_DRIFT_SD for the tying, which is the whole point:
    #    a well-measured venue moves between eras, a thin one does not.
    ap.add_argument("--era-years", type=int, default=js.ERA_YEARS_DEFAULT,
                    metavar="N",
                    help="split each course into N-year eras tied by a "
                         "random walk (0 = one difficulty for all time)")
    ap.add_argument("--era-drift", type=float, default=js.ERA_DRIFT_SD,
                    metavar="SD",
                    help="log-time drift allowed between adjacent eras "
                         f"(default {js.ERA_DRIFT_SD:g})")
    ap.add_argument("--holdout", action="store_true",
                    help="also fit on 90%% of rows and score the rest")
    # ★ A REPORT STEP MUST NOT WRITE THE THING IT REPORTS ON. Without this,
    #   `--holdout --sample-pct 25` scores the holdout and then solves the
    #   FULL model on a quarter of the athletes and writes that over
    #   engine/data/joint_difficulty.npz -- the file explain_joint_row and
    #   the other diagnostics read. It also doubles the step's wall clock
    #   for a solve nobody looks at.
    ap.add_argument("--holdout-only", action="store_true",
                    help="stop after the held-out score: no full solve, "
                         "nothing written (implies --holdout)")
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
    ap.add_argument("--winter-gain-bands", default=None,
                    help="the stated fall-to-spring gain PER RATING BAND, "
                         "low,middle,top (below 105 / 105-120 / 120+), e.g. "
                         "0.03,0.02,0.03: the go-live shifts every track "
                         "row so a dual-sport athlete's page shows exactly "
                         "that gap in their band (issue 194). Unset: no "
                         "shift, the curve pin alone")
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
    ap.add_argument("--no-dist-cal", action="store_true",
                    help="do not set the banded offsets' prior means from "
                         "season-best pairs (js.DIST_CAL_SHARE); the rows "
                         "alone decide, as before 2026-09-05")
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
    # ★ THE MEASURED XC/TF GAP (owner, 2026-09-09: "mainly the fact that most
    #   of the best seasons of all time are tf"). scripts/measure_sport_gap.py
    #   interpolates each athlete's TF level across an XC season and back --
    #   2.4M sandwiches, SE 0.00004 -- and reports D, the error in the gap in
    #   log-rating. Pass that D here and the solve's own bbar is nudged by it
    #   on every outer pass, which moves TF by D/2 and XC by -D/2.
    #
    # ! OFF BY DEFAULT, and deliberately not read from a file. This is the
    #   number that decides which sport tops an all-time board, so it should
    #   appear in the command that produced the run and in its log, not sit
    #   in an artifact nobody re-measured. Re-measure after any --ridge
    #   change: bbar is a weighted mean of beta and the ridge decides how
    #   much of the level lives there.
    # ★★ OPTION (b), THE OWNER'S CALL 2026-09-09. One scale that means
    #    fitness, by asserting the one thing the data cannot see rather than
    #    trying to estimate it.
    #
    #    Sport is season -- XC is autumn, track is spring, and nobody races
    #    both close enough together for fitness to be held constant. So the
    #    mean offset between the two sports and the autumn-to-spring fitness
    #    rise are the SAME QUANTITY in this corpus, and no estimator can
    #    separate them. --merge-sports stops trying:
    #
    #      * the per-athlete sport offset (beta) is dropped -- there is no
    #        sport to be offset from;
    #      * recentreLevels DISCARDS each sport's mean course difficulty
    #        instead of banking it in mu, so the two sports' average course
    #        is equal by construction on the shared ruler;
    #      * the curve is FREED (no winter-gain pin, no gap penalty), so
    #        every bit of autumn-to-spring movement lands in the form curve.
    #
    #    Which is the property that was asked for: a rating means fitness,
    #    and the number moving means fitness moved.
    #
    # ⚠ IT IS AN ASSUMPTION AND IT REPLACES FOUR IMPLICIT ONES. Today the
    #   same scalar is set by XCP_WINTER_GAIN=0.02 pinning the curve at
    #   weight 100, tangled with beta, the ridge and mu. This is that choice
    #   made once, in the open.
    # ★★ THE RESTRUCTURE (owner, 2026-09-09). "fitness should have a mean of
    #    0 in season but fitness needs to apply to course difficulty."
    #
    #      ability   per (athlete, year, SPORT) -- carries the level, and it
    #                is what the rating reads, so nothing is deleted
    #      beta      gone; there is no single ability left for it to patch
    #      curve     shape only, still applied when difficulty is estimated
    #
    #    The autumn-to-spring gain stops being a curve term and becomes the
    #    difference between two abilities, which is where a reader would
    #    look for it.
    #
    # ⚠ IT DOES NOT MAKE THE XC/TF LEVEL IDENTIFIED, AND NOTHING CAN. Split
    #   by sport, autumn athlete-seasons touch only autumn courses and spring
    #   only spring: two components with nothing joining them. What changes
    #   is that the one free scalar is now ISOLATED -- see XC_TRACK_GAP --
    #   instead of leaking through beta, mu, the winter-gain pin and the
    #   go-live band shift at once.
    # ★ FITNESS KEEPS ITS SHAPE AND GIVES BACK ITS LEVEL. joint_solve already
    #   folds the curve's POOL-wide mean into the ability; this does it per
    #   athlete-season, which is the grain that matters, because the leftover
    #   is exactly the season-correlated part that go-live deletes.
    ap.add_argument("--centre-curve", action="store_true",
                    help="fold each athlete-season's own mean curve "
                         "contribution into its ability, so the season curve "
                         "keeps only its shape and its level reaches the "
                         "rating")
    # ★ MORE SHRINKAGE THAN THE DATA ASK FOR, PER GROUP. tau is the prior SD
    #   of a course's difficulty in log terms; pen_cell = sigma2/tau2, so a
    #   smaller tau shrinks every course harder and the thin ones hardest,
    #   which is where the noise is. Measured on this corpus, the spread of
    #   WELL-EVIDENCED courses is about 0.024 (2.4%) while courses under 50
    #   results scatter at 0.083 -- so a cap near the former pulls the latter
    #   in without touching the courses that earned their number.
    ap.add_argument("--tau-max", default=None, metavar="XC,TF",
                    help="cap the per-sport course-difficulty prior SD, e.g. "
                         "0.025,0.025. Empty for one side keeps it free "
                         "(e.g. ',0.02' caps TF only).")
    ap.add_argument("--split-ability", action="store_true",
                    help="one ability per (athlete, year, sport). Implies "
                         "--no-sport-offset: beta has nothing left to patch.")
    ap.add_argument("--merge-sports", action="store_true",
                    help="one scale for XC and TF: no sport offset, no sport "
                         "level, and the curve free to carry the season. "
                         "Implies --no-sport-offset, --winter-gain 0 and "
                         "--curve-gap 0.")
    ap.add_argument("--sport-gap-delta", type=float, default=0.0,
                    metavar="D",
                    help="add this to the solve's bbar every pass -- D from "
                         "scripts/measure_sport_gap.py (e.g. -0.02795). "
                         "Negative means TF currently rates too high.")
    # ★ ON BY DEFAULT (2026-09-10). With one race in a cell, d and u are the
    #   same number, and letting such cells vote on tau2/sigma_u2 collapses
    #   sigma_u to zero -- so the course kept 100% of one race's noise. See
    #   joint_solve.racesPerCell. The flag exists to measure the change,
    #   not because the old behaviour is defensible.
    ap.add_argument("--sigma-u-floor", default="default", metavar="XC,TF",

                    help="how slow a race day is worth at minimum, PER "
                         "SPORT as 'XC,TF' in log SD (default "
                         f"{js.SIGMA_U_FLOOR[0]},{js.SIGMA_U_FLOOR[1]}). "
                         "Within a race the course and the day are exactly "
                         "collinear, so this is what decides which of them "
                         "takes the common effect: a one-race course keeps "
                         "tau2/(tau2+sigma_u2). '0,0' uses the fitted "
                         "values, which are biased low")
    # ★ THE TWO WAYS TO SAY "THE BACK OF THE FIELD IS NOISE". See
    #   js.abilityWeights and js.topFractionWeights -- one weights by
    #   measured variance per rating band, the other cuts on finishing
    #   position the way Slaney does. They are rungs on the ladder; the
    #   held-out score decides, not the argument.
    ap.add_argument("--ability-weight", action="store_true",
                    help="inverse-variance row weights by rating band, "
                         "refreshed each outer")
    ap.add_argument("--top-frac", type=float, default=0.0, metavar="F",
                    help="keep only the fastest F of each race (Slaney's "
                         "top-25%% filter is 0.25); 0 keeps everyone")
    ap.add_argument("--priors-from-all-cells", action="store_true",
                    help="estimate tau2/sigma_u2 from every cell including "
                         "one-race cells -- the pre-2026-09-10 behaviour, "
                         "kept only for comparison")
    ap.add_argument("--race-key", default="cell", choices=("cell", "venue"),
                    help="what shares a race-day effect: the (venue, "
                         "distance) CELL and day as today, or the VENUE and "
                         "day, which pools every distance raced there that "
                         "day. See venueOfCell")
    ap.add_argument("--no-race-term", action="store_true",
                    help="collapse every row to one race id, removing the "
                         "race-day effect FROM THE SOLVE. Distinct from "
                         "--no-race-effect, which only keeps the term out "
                         "of the per-result rating")
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
    ap.add_argument("--race-effect-sports", default="",
                    help="the sports whose per-result ratings carry the "
                         "race-day term (comma list; default NONE, owner "
                         "2026-09-06: a slow race is a slow race, and the "
                         "model cannot tell mud from a jog -- measured "
                         "weather is credited in the normalisation instead). "
                         "The solve keeps the term for both sports, the "
                         "hover shows it")
    return ap


def applyImplications(args, ap):
    # ! THE IMPLICATIONS ARE APPLIED HERE, NOT DOCUMENTED AND LEFT TO THE
    #   OPERATOR. Every one of them is part of the same assumption, and a run
    #   that carried three of the four would be measuring nothing anybody
    #   could name.
    # ! PARSED INTO THE {group: cap} SHAPE solveJoint already takes for
    #   --tau-tf-max, so there is one mechanism rather than two.
    if args.tau_max:
        # ⚠ 'none' MEANS NO CAP AT ALL, and it needs to be sayable. An empty
        #   'XC,TF' parses to an empty dict, which silently fell through to
        #   js.TAU_MAX_DEFAULT -- so an ablation rung meant to REMOVE the
        #   caps quietly kept them and the ladder would have reported a
        #   no-op as a result.
        if args.tau_max.strip().lower() in ("none", "off"):
            args.tau_tf_max = None
            args._tau_caps = None
            print("[joint] course-difficulty prior caps OFF (--tau-max none)")
            parts = caps = None
        else:
            parts = [p.strip() for p in args.tau_max.split(",")]
            if len(parts) != 2:
                ap.error("--tau-max wants XC,TF, or 'none'")
            caps = {g: float(v) for g, v in enumerate(parts) if v}
            if not caps:
                ap.error("--tau-max got no numbers; use 'none' to disable")
        if caps:
            args.tau_tf_max = None      # the general form supersedes it
            args._tau_caps = caps
            print(f"[joint] course-difficulty prior capped at "
                  f"{ {('XC', 'TF')[g]: v for g, v in caps.items()} } "
                  f"(log SD) -- thin courses shrink toward their sport's "
                  f"level")
    if args.split_ability:
        # ! beta PATCHED A SHARED ABILITY. There is no shared ability now, so
        #   leaving it in would fit a sport offset on top of two separate
        #   sport levels -- the same quantity twice.
        args.no_sport_offset = True
        print("[joint] SPLIT ABILITY: one ability per (athlete, year, "
              "sport); beta off.\n        The autumn-to-spring gain is now "
              "the difference between two\n        abilities, and it reaches "
              "the rating.")
    if args.merge_sports:
        args.no_sport_offset = True
        args.winter_gain = 0.0
        args.curve_gap = 0.0
        if args.sport_gap_delta:
            print("[joint] --sport-gap-delta is meaningless with "
                  "--merge-sports (there is no sport level to shift); "
                  "ignoring it")
            args.sport_gap_delta = 0.0
        # ⚠⚠ AND THE FIFTH PLACE, WHICH IS NOT IN THE SOLVE AT ALL.
        #    --winter-gain-bands shifts the TRACK ROWS at go-live (issue
        #    194), per rating band. That is a sport level asserted AFTER the
        #    fit, so leaving it on would put back by hand exactly what
        #    recentreLevels(merge=True) refused. Four flags inside the solve
        #    and one outside it, all saying the same thing.
        if args.winter_gain_bands:
            print(f"[joint] --winter-gain-bands "
                  f"{args.winter_gain_bands} is a post-solve shift of the "
                  f"track rows; with --merge-sports it would reinstate the "
                  f"sport level. Dropping it.")
            args.winter_gain_bands = None
        print("[joint] MERGED SPORTS: no beta, no sport level, curve free.\n"
              "        The two sports' mean course difficulty is held equal "
              "by construction,\n        so all autumn-to-spring movement "
              "is carried by the form curve.")
    return args


def main():
    ap = buildParser()
    args = applyImplications(ap.parse_args(), ap)

    if not os.path.exists(args.pack):
        sys.exit(f"[joint] no pack at {args.pack} -- run 07_pack first")

    print(f"[joint] loading {args.pack}")
    cols = pe.loadPack(args.pack)
    cols = sortRowsByAthlete(cols)
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    # ★ THE LADDER RUNS ON A SAMPLE. Each rung is a full solve, and a
    #   comparison between rungs only needs the ORDER to be right, not the
    #   absolute value -- so a 15% athlete sample turns 40 minutes a rung
    #   into a few. Sampled by ATHLETE, never by row: half an athlete's
    #   season is a different (and biased) estimation problem.
    if args.sample_pct and args.sample_pct < 100:
        ath_all = np.asarray(cols["athlete"])
        uniq = np.unique(ath_all[keep])
        rng = np.random.default_rng(args.sample_seed)
        picked = uniq[rng.random(uniq.size) < args.sample_pct / 100.0]
        keep = keep & np.isin(ath_all, picked)
        print(f"[joint] SAMPLE: {args.sample_pct}% of athletes "
              f"({picked.size:,} of {uniq.size:,}), {int(keep.sum()):,} rows")
    y = np.log(cols["norm"][keep])

    _ALT_FIT["on"] = bool(args.altitude_fit)
    _NO_RACE_TERM["on"] = bool(args.no_race_term)
    _RACE_KEY["by"] = args.race_key
    D, athlete_pool, pool_names = buildDesign(
        cols, keep, not args.no_sport_offset, not args.no_curve,
        not args.no_rust, dist=not args.no_dist, slope=not args.no_slope,
        link=args.link and not args.no_link, altitude=args.altitude,
        dist_bands=not args.no_dist_bands,
        split_ability=args.split_ability,
        era_years=args.era_years)
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

    if args.holdout or args.holdout_only:
        holdout(cols, keep, args, athlete_pool, D)
    if args.holdout_only:
        print("[joint] --holdout-only: no full solve, nothing written")
        return

    t0 = time.time()
    out = js.solveJoint(y, design=D, n_probe=args.probes,
                        **solveKwargs(args, athlete_pool, verbose=True))
    print(f"[joint] solved in {time.time() - t0:.0f}s")
    if args.sport_gap_delta:
        # ⚠ SAID OUT LOUD, EVERY RUN THAT CARRIES IT. A run with a gap
        #   correction and one without produce different all-time boards
        #   from the same data, and the only difference is this number.
        print(f"[joint] sport gap: bbar carried a measured "
              f"{args.sport_gap_delta:+.5f} -- TF cells moved "
              f"{args.sport_gap_delta / 2:+.5f}, XC "
              f"{-args.sport_gap_delta / 2:+.5f} in log-rating "
              f"({abs(args.sport_gap_delta) * 50:.1f} points at a 100 rating, "
              f"{abs(args.sport_gap_delta) * 75:.1f} at 150)")

    delta = out["delta"]
    rows_per_cell = np.bincount(D.cell, minlength=D.n_cell).astype(np.float64)
    solved = rows_per_cell > 0
    anchored = delta - np.average(delta[solved], weights=rows_per_cell[solved])
    # ! sigma_u2 AND tau2 ARE PER-GROUP ARRAYS, not scalars. XC and TF get
    #   their own race-day and course spreads -- a shared pair let the TF
    #   floor eat XC's course difficulty. Format them group by group, or
    #   numpy raises on the ':.5f'.
    print(f"\n[joint] sigma {np.sqrt(out['sigma2']):.5f}")
    _su = np.atleast_1d(np.asarray(out["sigma_u2"], dtype=np.float64))
    _tau = np.atleast_1d(np.asarray(out["tau2"], dtype=np.float64))
    for _g in range(max(_su.size, _tau.size)):
        _name = ("XC", "TF")[_g] if _g < 2 else str(_g)
        _s = float(np.sqrt(_su[_g if _g < _su.size else -1]))
        _t = float(np.sqrt(_tau[_g if _g < _tau.size else -1]))
        print(f"[joint] {_name}: race-day sigma_u {_s:.5f} | tau {_t:.5f}")
    # ⚠ AND SAY IT AGAIN AT THE END. The solve prints this too, but that is
    #   3000 seconds up the log; a collapsed course prior belongs beside the
    #   summary a human actually reads.
    for _c in js.checkPriors(out["tau2"], out["sigma_u2"]):
        print(f"[joint] ⚠⚠ {_c}")
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
                # ! A REPORT MUST NEVER KILL A SOLVE. The sequential
                #   engine's file is keyed by the pack's cells as they were
                #   when it ran; a repack with a different cell count (the
                #   2026-09-05 run: 74,805 vs 74,834) makes the comparison
                #   meaningless, and it cost two hours of solve by crashing
                #   here before anything was written.
                if old_delta.size != solved.size:
                    print(f"[joint] vs pair_difficulty: skipped, {old_delta.size:,} "
                          f"cells in the old file vs {solved.size:,} in this pack")
                    old_delta = None
            if old_delta is not None:
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
        if out.get("dist_cal_mean") is not None:
            save["dist_cal_mean"] = out["dist_cal_mean"]
            save["dist_cal_n"] = out["dist_cal_n"]
            save["dist_cal_via"] = out["dist_cal_via"]
    np.savez(args.out, **save)
    print(f"[joint] wrote {args.out}")

    if args.golive or args.golive_dry:
        import joint_golive as jg
        gain_bands = None
        if args.winter_gain_bands:
            gain_bands = tuple(float(v) for v in args.winter_gain_bands.split(","))
            assert len(gain_bands) == len(js.SPORT_GAIN_ANCHORS), \
                "--winter-gain-bands wants low,middle,top"
        # the pack's own date: its days-ago are relative to the day it was
        # packed, which with a cached pack is not today
        from datetime import date as _date
        pack_date = _date.fromtimestamp(os.path.getmtime(args.pack))
        live = jg.buildLive(out, D, cols, keep, collapse=args.collapse,
                            anchor=args.anchor,
                            use_race_effect=not args.no_race_effect,
                            gain_bands=gain_bands, pack_date=pack_date,
                            race_effect_sports=tuple(
                                x.strip().upper() for x in
                                args.race_effect_sports.split(",") if x.strip()))
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
