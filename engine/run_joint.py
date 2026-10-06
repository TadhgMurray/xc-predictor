"""
run_joint.py -- drive joint_solve over the packed cache.

    python engine/run_joint.py                      # solve, write, compare
    python engine/run_joint.py --holdout            # + a 10% held-out score
    python engine/run_joint.py --holdout-only --holdout-kind forward
                                                    # 2026-09-29: the validation
                                                    #   season from the past
                                                    #   (engine/forward_holdout.py)
    python engine/run_joint.py --outer 8 --probes 32
    python engine/run_joint.py --no-curve --no-sport-offset --no-rust
    python engine/run_joint.py --no-robust          # plain least squares
    python engine/run_joint.py --season-tie         # 2026-09-29, off by default:
    python engine/run_joint.py --tilt-scale hs      #   js.SEASON_TIE_BANDS, js.TILT_SCALES

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

import forward_holdout as fh                                    # noqa: E402
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
    """Dense id per (cell or venue, day), numbered in (unit, day) order.

    ! ONE COMPOSITE int64 KEY (2026-09-12). np.unique(axis=0) on the stacked
      pair took 19 s per ten million rows, ninety on the corpus, in every
      script that needed a race id. The composite key takes under two and
      numbers the races identically (unit first, then day), so the
      holdout split, which draws by race id, is the same split."""
    unit = (np.asarray(course).astype(np.int64) if venue_of_cell is None
            else np.asarray(venue_of_cell)[np.asarray(course).astype(np.int64)])
    d = np.asarray(day).astype(np.int64)
    if d.size == 0:
        return np.zeros(0, dtype=np.int64), 0
    d0 = int(d.min())
    span = int(d.max()) - d0 + 1
    _, inv = np.unique(unit * span + (d - d0), return_inverse=True)
    inv = inv.reshape(-1).astype(np.int64)
    return inv, int(inv.max()) + 1


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
    # the pack's own row number per sorted row, so anything written per
    # row (the holdout's predictions) can be read back against the file
    out["_row_order"] = order.astype(np.int64)
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


SEASON_END_DAYS = 14          # a race in the last two weeks of the athlete's season
SEASON_END_MIN_RACES = 3      # athlete-seasons that vote on a race's share
SEASON_CLOSED_DAYS = 21       # a season whose last race is more recent may still be running


def groupExtreme(group, value, n, largest=False):
    """Per group, the smallest (or largest) value; +inf (-inf) for a group
    with no rows. A sort and a first-of-run, not ufunc.at, which is many
    times slower on tens of millions of rows."""
    group = np.asarray(group, dtype=np.int64)
    value = np.asarray(value, dtype=np.float64)
    out = np.full(n, -np.inf if largest else np.inf)
    if group.size == 0:
        return out
    order = np.lexsort((-value if largest else value, group))
    g = group[order]
    first = np.flatnonzero(np.r_[True, g[1:] != g[:-1]])
    out[g[first]] = value[order][first]
    return out


def seasonEndShare(cols, keep, athlete, n_ath, pool_of_athlete, pool_names):
    """Per row, the meet-importance covariate WITHOUT LABELS (issue #22;
    owner, 2026-09-11: the name-based classes were "so easy to go bad").

    The covariate is the race's SEASON-END SHARE: the fraction of the
    race's field (athlete-seasons with SEASON_END_MIN_RACES or more races)
    for whom this race falls within SEASON_END_DAYS of the last race of
    their own season. A state final is a race where nearly everyone's
    season ends, a mid-season invitational one where nearly nobody's does,
    a league meet sits wherever its field puts it -- and none of it is read
    off a name. Everyone in a race carries the same share, so within a race
    there is no selection; across races the share is what identifies the
    term.

    ⚠ A SEASON STILL RUNNING HAS NO LAST RACE YET. Every recent race of the
      current season would look like a season end, and the term would hand
      this week's invitational a taper. So an athlete-season whose last race
      is within SEASON_CLOSED_DAYS of the pack's date does not vote and its
      rows carry no share; a current championship at a venue with history
      is handled by the day term, as before.

    Returns (index per kept row: pool * 2 + sport; weight per row: the
    share; n_imp; the prior mean per coefficient, zero; labels; and the
    per-race share for the log)."""
    days = np.asarray(cols["days"])[keep].astype(np.float64)       # days ago
    sport = np.asarray(cols["sport"])[keep].astype(np.int64)
    course = np.asarray(cols["course"])[keep].astype(np.int64)
    pool_row = pool_of_athlete[np.asarray(cols["athlete"])[keep]]
    athlete = np.asarray(athlete, dtype=np.int64)
    n_ath = int(n_ath)
    # the athlete-season's last race (fewest days ago) and its race count
    last = groupExtreme(athlete, days, n_ath)          # fewest days ago
    n_races = np.bincount(athlete, minlength=n_ath)
    closed = last > SEASON_CLOSED_DAYS
    votes = (n_races >= SEASON_END_MIN_RACES) & closed
    ending = (days - last[athlete] <= SEASON_END_DAYS) & votes[athlete]
    # the race: (cell, day) as raceCodes keys it
    race, n_race = raceCodes(course, days)
    n_vote = np.bincount(race, weights=votes[athlete].astype(np.float64),
                         minlength=n_race)
    n_end = np.bincount(race, weights=ending.astype(np.float64), minlength=n_race)
    share = np.where(n_vote > 0, n_end / np.maximum(n_vote, 1), 0.0)
    w = share[race]
    w = np.where(course >= 0, w, 0.0)
    idx = np.where(w > 0, pool_row * 2 + sport, -1)
    n_imp = len(pool_names) * 2
    prior = np.full(n_imp, float(js.IMP_PRIOR_MEAN))
    labels = [f"{name}:{sname}" for name in pool_names
              for sname in ("XC", "TF")]
    seen = n_vote > 0
    print(f"[joint] season-end taper: {int((w > 0).sum()):,} of {w.size:,} rows "
          f"carry a share (median share over races {np.median(share[seen]):.2f}, "
          f"races with share >= 0.8: {int((share[seen] >= 0.8).sum()):,} of "
          f"{int(seen.sum()):,}); {int(votes.sum()):,} closed athlete-seasons "
          f"with {SEASON_END_MIN_RACES}+ races vote; {n_imp} coefficients, "
          f"prior mean 0, sd {js.IMP_PRIOR_SD}; a healthy fit reads about "
          f"{js.IMP_EXPECTED:+.3f} per unit share")
    # the diagnostic cross-tab: what the NAME classes say about the same
    # races, so the two can be checked against each other in the log
    if "meet_class" in cols:
        import meet_class as mcl
        mc = np.clip(np.asarray(cols["meet_class"])[keep].astype(np.int64), 0, mcl.N_CLASS)
        for c in range(mcl.N_CLASS + 1):
            m = (mc == c) & (course >= 0) & (n_vote[race] > 0)
            if m.any():
                print(f"        by name class {c} ({mcl.CLASS_NAMES[c]:>9}): "
                      f"{int(m.sum()):>10,} rows, mean season-end share "
                      f"{float(w[m].mean()):.2f}")
    return idx.astype(np.int64), w, n_imp, prior, labels, share


def fieldTermRows(course, sport, pool_row, pool_names):
    """The field-strength term's rows (owner, 2026-09-11: "if there is a
    race that is very top-heavy ... those races should get some refund to
    their difficulty"): every row with a cell, one coefficient per (pool,
    sport). The covariate itself -- the race's front, the mean rating of
    its top five, centred at the median race of its pool and sport -- is
    computed inside the solve from the model's own ratings each pass
    (joint_solve.fieldStrength), because ratings are what the solve is
    for. Returns (index per row, n_imp, prior means (zero), labels)."""
    idx = np.where(course >= 0, pool_row * 2 + sport, -1).astype(np.int64)
    n_imp = len(pool_names) * 2
    labels = [f"{name}:{sname}" for name in pool_names
              for sname in ("XC", "TF")]
    print(f"[joint] field strength: the taper term's covariate is each race's "
          f"FRONT (mean rating of its top {js.FIELD_TOP_K}, centred at the "
          f"median race of its pool and sport, per {js.FIELD_UNIT:g} rating "
          f"points, clipped to {js.FIELD_CLIP}); {n_imp} coefficients, zero "
          f"prior, sd {js.IMP_PRIOR_SD}; a healthy fit reads about "
          f"{js.FIELD_EXPECTED:+.3f} per unit. Zero on the first pass, live "
          f"from the second, like the tilt; not in a rating")
    return idx, n_imp, np.zeros(n_imp), labels


INDOOR_PAIR_MAX_DAYS = 42


def indoorTransitionCheck(cols, keep, athlete, ind_cell, pool_row, pool_names, level=None,
                          max_gap_days=INDOOR_PAIR_MAX_DAYS):
    """★ THE INDOOR LEVEL MEASURED THE NCAA WAY, AS A CHECK ON THE ASSERTED
    ONE (the 2012 facility-indexing study: same-athlete pairs close in
    time). Each athlete-season's LAST indoor race and FIRST outdoor race
    at the same distance, within max_gap_days of each other: the
    difference in log normalized time, indoor minus outdoor, + = indoor
    slower. Fitness gained between the two biases it up, a peaked last
    indoor race (a conference or national final) biases it down, so it
    brackets the level rather than fixing it. Printed per pool; nothing
    feeds back."""
    try:
        y = np.log(np.asarray(cols["norm"])[keep].astype(np.float64))
        days = np.asarray(cols["days"])[keep].astype(np.float64)
        dist = np.asarray(cols["dist_m"])[keep].astype(np.float64)
        course = np.asarray(cols["course"])[keep].astype(np.int64)
        sport = np.asarray(cols["sport"])[keep].astype(np.int64)
        athlete = np.asarray(athlete, dtype=np.int64)
        ok = (course >= 0) & (sport == 1) & np.isfinite(y) & np.isfinite(dist)
        # ! FLAGS PER BASE COURSE, from the pack's own keys. `course` here is
        #   the pack's base id; under --era-years the design's flags are per
        #   (course, era) cell and indexing them by base id read every row
        #   as outdoor, and this check returned without a word (run 20).
        base_flag = np.array([str(k).split("@", 1)[0].endswith(":in")
                              for k in cols["course_keys"]], dtype=bool)
        flag = np.zeros(ok.size, dtype=bool)
        flag[ok] = base_flag[course[ok]]
        indoor = ok & flag
        outdoor = ok & ~flag
        if not indoor.any() or not outdoor.any():
            print(f"[joint] indoor check: {int(indoor.sum()):,} indoor and "
                  f"{int(outdoor.sum()):,} outdoor track rows -- nothing to pair")
            return
        n_ath = int(athlete.max()) + 1
        last_in = groupExtreme(athlete[indoor], days[indoor], n_ath)      # fewest days ago
        first_out = groupExtreme(athlete[outdoor], days[outdoor], n_ath,
                                 largest=True)                            # most days ago
        gap = last_in - first_out                      # indoor before outdoor
        pair = (np.isfinite(last_in) & np.isfinite(first_out) & (gap > 0)
                & (gap <= max_gap_days))
        ri = np.flatnonzero(indoor & pair[athlete] & (days == last_in[athlete]))
        ro = np.flatnonzero(outdoor & pair[athlete] & (days == first_out[athlete]))
        ki = athlete[ri] * 100_000 + np.round(dist[ri]).astype(np.int64)
        ko = athlete[ro] * 100_000 + np.round(dist[ro]).astype(np.int64)
        ki_u, ii = np.unique(ki, return_index=True)
        ko_u, io = np.unique(ko, return_index=True)
        common, a_i, a_o = np.intersect1d(ki_u, ko_u, return_indices=True)
        if common.size < 50:
            print(f"[joint] indoor check: only {common.size} last-indoor / "
                  f"first-outdoor pairs within {max_gap_days} days; skipped")
            return
        diff = y[ri[ii[a_i]]] - y[ro[io[a_o]]]
        pool = pool_row[ri[ii[a_i]]]
        print(f"[joint] indoor check, the NCAA way: each athlete-season's last "
              f"indoor race against its first outdoor race at the same "
              f"distance within {max_gap_days} days, log-time indoor minus "
              f"outdoor (+ = indoor slower; fitness gained in between biases "
              f"it up, a peaked last indoor race biases it down). The asserted "
              f"level is {100 * (js.IND_LEVEL_DEFAULT if level is None else level):+.2f}%; "
              f"the literature says +0.8 to +1.8%; the corpus's zero-gap reading "
              f"(scripts/diagnose.py --only indoor) is the number to hold it to")
        for p, name in enumerate(pool_names):
            m = pool == p
            if m.sum() < 50:
                continue
            d = diff[m]
            lo, hi = np.percentile(d, [10, 90])
            trimmed = d[(d >= lo) & (d <= hi)]
            print(f"    {name:<10} {int(m.sum()):>8,} pairs   median "
                  f"{100 * float(np.median(d)):+.2f}%   trimmed mean "
                  f"{100 * float(trimmed.mean()):+.2f}%")
    except Exception as exc:                                 # noqa: BLE001
        print(f"[joint] indoor check: skipped ({type(exc).__name__}: {exc})")


def indoorCells(course_keys):
    """Per cell, is it an indoor track ('TF:loc:<id>:in', era suffix or
    not)? None when no cell is."""
    flags = np.array([str(k).split("@", 1)[0].endswith(":in")
                      for k in course_keys], dtype=bool)
    if not flags.any():
        return None
    print(f"[joint] indoor: {int(flags.sum()):,} of {flags.size:,} cells are "
          f"indoor tracks; one shared coefficient per pool, prior "
          f"{js.IND_PRIOR_MEAN:+.4f} sd {js.IND_PRIOR_SD}")
    return flags


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


# ★ THE SEASON TIE'S PAIRS (js.SEASON_TIE_BANDS; owner, 2026-09-29). By
#   PERSON, not by athlete key. The pack keys an athlete as (person, pool),
#   so seasonLinks above can only tie seasons inside one pool -- and the
#   thin season that started this is exactly the one a pool change makes:
#   the first college autumn after a spring of high-school track. Here every
#   athlete-season of one person is put in date order and each is tied to
#   the next, whatever the pools; the TRANSITION (earlier pool > later pool)
#   is what the walk's mean and width are fitted per, so hs_m>college_m
#   carries its own change of units and level and is never averaged with
#   hs_m>hs_m.
#
# ! TIME IS THE SEASONS' MEAN RACE DATES, not the year label. The engine's
#   season is the CALENDAR year (speed_ratings, "ACADEMIC YEAR, NOT CALENDAR
#   YEAR"), so a person's spring hs_m track and autumn college_m XC are two
#   seasons of ONE year, half a year apart; under --split-ability a year's
#   TF and XC are two seasons too, and the sport pair joins the transition.
# ! AND FLOORED AT A SEASON'S OWN LENGTH. Two fragments of one season (the
#   same months, two pool labels) have mean dates days apart, and a walk
#   that short would weld them. The floor is the median spread (sd of the
#   race dates) of the athlete-seasons with POOL_MEAN_MIN_RACES races:
#   closer than that, two seasons' centres are not distinguishable times.
def seasonTiePairs(cols, keep, athlete, n_ath, pool_of_athlete, pool_names,
                   split_sport=False):
    """(k0, k1, dt_years, transition code, transition names, dt floor):
    consecutive athlete-seasons of one person, in date order."""
    athlete = np.asarray(athlete, dtype=np.int64)
    raw_row = np.asarray(cols["athlete"])[keep].astype(np.int64)
    days = np.asarray(cols["days"])[keep].astype(np.float64)
    # person per raw athlete code: the key's first field
    keys = cols["athlete_keys"]
    if isinstance(keys, np.ndarray) and keys.ndim == 2:      # the pack's shape
        _pid = keys[:, 0].astype(str)
    else:
        _pid = np.array([str(k[0]) if (k is not None and len(k) > 0) else ""
                         for k in keys])
    _, person_of_raw = np.unique(_pid, return_inverse=True)
    n_row = np.bincount(athlete, minlength=n_ath).astype(np.float64)
    has = n_row > 0
    t_mean = -np.bincount(athlete, weights=days, minlength=n_ath) / np.maximum(n_row, 1)
    t_sq = np.bincount(athlete, weights=days * days, minlength=n_ath) / np.maximum(n_row, 1)
    t_sd = np.sqrt(np.maximum(t_sq - t_mean ** 2, 0.0))
    person = np.full(n_ath, -1, dtype=np.int64)
    person[athlete] = person_of_raw[raw_row]
    pool = np.full(n_ath, -1, dtype=np.int64)
    pool[athlete] = np.asarray(pool_of_athlete)[raw_row]
    # one small code per season: its pool (and, split, its sport)
    labels = [str(n) for n in pool_names] + ["?"]
    code = np.where(pool >= 0, pool, len(pool_names))
    if split_sport and "sport" in cols:
        sp = np.zeros(n_ath, dtype=np.int64)
        sp[athlete] = (np.asarray(cols["sport"])[keep] != 0).astype(np.int64)
        code = code * 2 + sp
        labels = [f"{p}:{s}" for p in labels for s in ("XC", "TF")]
    idx = np.flatnonzero(has & (person >= 0))
    order = idx[np.lexsort((t_mean[idx], person[idx]))]
    same = person[order][1:] == person[order][:-1]
    k0 = order[:-1][same]
    k1 = order[1:][same]
    full = has & (n_row >= js.POOL_MEAN_MIN_RACES)
    floor = float(np.median(t_sd[full])) / 365.25 if full.any() else 0.0
    dt = np.maximum((t_mean[k1] - t_mean[k0]) / 365.25, max(floor, 1e-3))
    n_code = len(labels)
    uniq, typ = np.unique(code[k0] * n_code + code[k1], return_inverse=True)
    names = [f"{labels[u // n_code]}>{labels[u % n_code]}" for u in uniq]
    return (k0.astype(np.int64), k1.astype(np.int64), dt,
            typ.reshape(-1).astype(np.int64), names, floor)


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


def distWalkPairs(dist_labels, dist_refs, n_band):
    """The random walk over distance classes (js.DIST_WALK_SD): for each
    pool, its free classes and its pinned reference sorted by distance;
    consecutive free classes form a pair weighted 1/(gap in log-distance),
    a free class beside the reference is tied to the reference's zero by
    the same weight. Expanded per band (index = class * n_band + band).
    Returns (pairs (2, M), pair weights, zero weight per e index)."""
    n_base = len(dist_labels)
    per_pool = {}
    for i, lab in enumerate(dist_labels):
        pool, d = lab.split(":")[0], int(float(lab.split(":")[1]))
        per_pool.setdefault(pool, []).append((d, i))
    for pool, d in dist_refs.items():
        per_pool.setdefault(pool, []).append((int(d), -1))
    n_e = n_base * max(int(n_band), 1)
    pa, pb, pw = [], [], []
    zero_w = np.zeros(n_e)
    nb = max(int(n_band), 1)
    for pool, items in per_pool.items():
        items = sorted(set(items))
        for (d1, i1), (d2, i2) in zip(items, items[1:]):
            if d1 <= 0 or d2 <= 0 or d1 == d2:
                continue
            w = 1.0 / abs(np.log(d2) - np.log(d1))
            for band in range(nb):
                if i1 >= 0 and i2 >= 0:
                    pa.append(i1 * nb + band); pb.append(i2 * nb + band); pw.append(w)
                elif i1 >= 0:
                    zero_w[i1 * nb + band] += w
                elif i2 >= 0:
                    zero_w[i2 * nb + band] += w
    pairs = np.array([pa, pb], dtype=np.int64).reshape(2, -1)
    return pairs, np.asarray(pw, dtype=np.float64), zero_w


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
    # ! VECTORISED (2026-09-12). The first cut remapped through a Python
    #   dict, one lookup per row: 51M rows, twice per holdout rung.
    seen, inv = np.unique(key[ok], return_inverse=True)
    new = np.full(course.shape, -1, dtype=np.int64)
    new[ok] = inv.astype(np.int64)

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
                dist_bands=True, split_ability=False, era_years=0,
                sport_level=None, importance="field", indoor=True,
                dist_table=True, indoor_level=js.IND_LEVEL_DEFAULT,
                season_tie=False):
    """A Design over the rows in `keep`, plus the per-athlete-season pool
    codes and names. `sizes` (from a full design) keeps a subset aligned.
    The distance classes ride on the Design as `dist_labels` / `dist_refs`.

    sport_level: None estimates the XC/TF level (mu), a number ASSERTS it
    (log-time, the track this much faster: XC_TRACK_GAP). importance /
    indoor / dist_table switch the 2026-09-11 shared terms."""
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

    # the asserted sport level (XC_TRACK_GAP): one level per group, XC 0
    mu_fixed = None
    if sport_level is not None:
        if sport is None:
            print("[joint] --sport-level needs a two-sport pack; ignored")
        else:
            mu_fixed = np.array([0.0, -float(sport_level)])
    # the field / taper term (issue #22): the race's front strength by
    # default (--importance field), the season-end share on request, or none
    imp_row, imp_w, n_imp, imp_prior, imp_labels = None, None, None, None, []
    imp_kind = None
    kind = {True: "field", False: "none", None: "none"}.get(importance, importance)
    if kind == "season-end" and sport is not None and "days" in cols:
        imp_row, imp_w, n_imp, imp_prior, imp_labels, _share = seasonEndShare(
            cols, keep, athlete, n_ath, pool_of_athlete, pool_names)
        imp_kind = "share"
    elif kind == "field" and sport is not None:
        imp_row, n_imp, imp_prior, imp_labels = fieldTermRows(
            course, sport.astype(np.int64), pool_row, pool_names)
        imp_kind = "field"
    # indoor as a shared term: from the cell keys (':in'); its level is
    # ASSERTED (js.IND_LEVEL_DEFAULT) unless --indoor-level fit
    ind_cell = indoorCells(course_keys) if indoor else None
    ind_fixed = None
    if ind_cell is not None and indoor_level is not None:
        ind_fixed = np.full(max(n_pool, 1), float(indoor_level))
        print(f"[joint] indoor level ASSERTED at {100 * float(indoor_level):+.2f}% "
              f"log-time for every pool (--indoor-level; 'fit' estimates it): "
              f"indoor is season, so the indoor cells' mean deviation is held "
              f"at zero each pass and the curve, not the ovals, carries the "
              f"winter")
        if sport is not None and "dist_m" in cols and "days" in cols:
            indoorTransitionCheck(cols, keep, athlete, ind_cell, pool_row,
                                  pool_names, level=indoor_level)
    # the published tables as the prior mean of an uncalibrated event
    # offset (distance_tables): NaN where no curve is reachable
    e_table = None
    if dist_table and dist_row is not None and dist_labels and dist_bands:
        import distance_tables as dtab
        e_table = dtab.tablePrior(dist_labels, dist_refs, js.DIST_N_BAND)
        if e_table is None:
            print("[joint] distance tables: no reachable curve -- event "
                  "offsets keep the zero prior")

    # the walk over distance classes (js.DIST_WALK_SD, distWalkPairs)
    d_pairs = d_pair_w = d_zero_w = None
    if dist_row is not None and dist_labels:
        d_pairs, d_pair_w, d_zero_w = distWalkPairs(
            dist_labels, dist_refs, js.DIST_N_BAND if dist_bands else 1)
        print(f"[joint] event offsets: {len(dist_labels):,} classes"
              f"{' x ' + str(js.DIST_N_BAND) + ' bands' if dist_bands else ''}; "
              f"{d_pairs.shape[1]:,} neighbouring pairs and {int((d_zero_w > 0).sum()):,} "
              f"ties to a reference form the random walk in log-distance "
              f"(sd {js.DIST_WALK_SD:g} per unit; --dist-walk)")
    D = js.Design(athlete, course, race, group_of_cell=group, sc=sc,
                  pool_row=pool_row if (curve or rust) else None,
                  day=day, first=first,
                  n_ath=n_ath, n_cell=n_cells, n_race=n_race, n_pool=n_pool,
                  dist=dist_row, n_e=len(dist_labels) if dist_row is not None
                  else None, lz=lz, link=links, alt=alt,
                  dist_banded=dist_bands, dist_ref=dist_ref_row,
                  dist_pairs=d_pairs, dist_pair_w=d_pair_w, dist_zero_w=d_zero_w,
                  alt_home=alt_home,
                  era_pairs=era_pairs, era_w=era_w,
                  eras_per_base=eras_per_base,
                  mu_fixed=mu_fixed, imp=imp_row, n_imp=n_imp,
                  imp_prior=imp_prior, imp_w=imp_w, imp_kind=imp_kind,
                  ind=ind_cell, ind_fixed=ind_fixed,
                  e_table=e_table,
                  # the event's share of the 5000's altitude cost (an 800 a
                  # fifth, a 10k a bit more), 1.0 without a distance
                  alt_dist=(js.altDistanceFactor(cols["dist_m"][keep])
                            if alt is not None and "dist_m" in cols else None))
    D.course_keys = course_keys
    D.imp_labels = imp_labels
    D.dist_labels = (bandLabels(dist_labels) if D.dist_banded
                     else dist_labels)
    D.dist_refs = dist_refs
    D.alt_known, D.alt_cells = alt_known, alt_cells
    # the season tie (js.SEASON_TIE_BANDS): its pairs ride on the design,
    # its walk is fitted inside the solve
    if season_tie and "days" in cols:
        k0, k1, dt, typ, names, floor = seasonTiePairs(
            cols, keep, athlete, n_ath, pool_of_athlete, pool_names,
            split_sport=split_ability)
        js.attachSeasonTie(D, k0, k1, dt, typ, names)
        # the floor on dt, so a season carried past the training cut
        # (forward_holdout.carryForward) is timed exactly as the pairs were
        D.tie_floor = floor
        cross = np.array([n.split(">")[0] != n.split(">")[1] for n in names], dtype=bool)
        n_cross = int(np.bincount(typ, minlength=len(names))[cross].sum()) if names else 0
        print(f"[joint] season tie: {k0.size:,} consecutive-season pairs of one "
              f"person ({n_cross:,} across a pool{' or sport' if split_ability else ''} "
              f"change), {len(names)} transitions; elapsed time between the "
              f"seasons' mean race dates, floored at {365.25 * floor:.0f} days "
              f"(the median spread of a full season's race dates)")
    elif season_tie:
        print("[joint] season tie: the pack has no race dates ('days') -- untied")
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
    bands_txt = (", by rating band (<" + " / ".join(f"{b:.0f}" for b in js.DIST_BANDS)
                 + f" / >={js.DIST_BANDS[-1]:.0f})" if banded else "")
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


TILT_REPORT_BANDS = (70.0, 80.0, 90.0, 100.0, 110.0, 120.0, 130.0, 140.0,
                     150.0, 160.0)


def reportTiltByBand(out, D, y):
    """★ MEASURE THE TILT THE CORPUS SHOWS, PER RATING BAND (owner,
    2026-09-11: "why not just measure further?"). The solve applies
    h(rating) * delta to every row. Within a band, regressing the residual
    on the row's raw course effect gives how much steeper or flatter the
    band's true multiplier is than the one applied: implied h = applied h
    + slope. Printed for every band including those above 140, where the
    line used to be clamped and is now extrapolated -- so the next run
    says whether the extrapolation holds. Read-only; nothing feeds back."""
    if out.get("rating") is None or out.get("theta") is None:
        return
    try:
        b = D.unpack(out["theta"])
        pred = js.rowPrediction(b, D, out["h"], out["amp"])
        pred = pred + D.fixedOffset(out["h"])
        resid = np.asarray(y, dtype=np.float64) - pred
        x = out["delta"][D.cell]
        r = out["rating"][D.athlete]
        h = np.asarray(out["h"], dtype=np.float64)
        w = np.asarray(out["weights"], dtype=np.float64)
        edges = (-np.inf,) + TILT_REPORT_BANDS + (np.inf,)
        print("[joint] tilt by rating band: the applied course multiplier h "
              "against the one the residuals imply (implied = applied + "
              "slope of residual on the course effect; a line past 140 "
              "means the extrapolation holds)")
        print(f"    {'band':>10} {'rows':>11} {'applied h':>10} {'implied h':>10} {'se':>7}")
        for lo, hi in zip(edges, edges[1:]):
            m = (r >= lo) & (r < hi) & (np.abs(x) > 1e-9)
            n = int(m.sum())
            if n < 2000:
                continue
            xm = x[m] - np.average(x[m], weights=w[m])
            rm = resid[m] - np.average(resid[m], weights=w[m])
            sxx = float(np.sum(w[m] * xm * xm))
            if sxx <= 0:
                continue
            slope = float(np.sum(w[m] * xm * rm) / sxx)
            res2 = float(np.sum(w[m] * (rm - slope * xm) ** 2) / max(n - 2, 1))
            se = float(np.sqrt(res2 / sxx))
            ha = float(np.average(h[m], weights=w[m]))
            lab = (f"<{hi:.0f}" if lo == -np.inf else
                   f"{lo:.0f}+" if hi == np.inf else f"{lo:.0f}-{hi:.0f}")
            print(f"    {lab:>10} {n:>11,} {ha:>10.3f} {ha + slope:>10.3f} {se:>7.3f}")
    except Exception as exc:                                 # noqa: BLE001
        print(f"[joint] tilt by band: skipped ({type(exc).__name__}: {exc})")


FIELD_REPORT_BANDS = (-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 3.0, 4.0)


def reportFieldByBand(out, D, y):
    """★ THE SHAPE OF THE FIELD EFFECT, READ OFF THE RESIDUALS (owner,
    2026-09-11: "idk how much though, need to decide"). Rows binned by
    their race's front strength; per band, the term applied and the mean
    residual left after the fit. A flat zero means the linear term is
    enough; a bend says where it is not, and by how much. The coefficient
    itself is in reportSharedTerms. Read-only; nothing feeds back."""
    if (getattr(D, "imp_kind", None) != "field" or not getattr(D, "n_imp", 0)
            or out.get("theta") is None
            or getattr(D, "field_strength", None) is None):
        return
    try:
        b = D.unpack(out["theta"])
        pred = js.rowPrediction(b, D, out["h"], out["amp"]) + D.fixedOffset(out["h"])
        resid = np.asarray(y, dtype=np.float64) - pred
        s = D.field_strength[D.race]
        w = np.asarray(out["weights"], dtype=np.float64)
        edges = (-np.inf,) + FIELD_REPORT_BANDS + (np.inf,)
        print("[joint] field strength by band: rows by their race's front "
              f"(units of {js.FIELD_UNIT:g} rating points above the median "
              "race of the pool and sport), the term applied, and the mean "
              "residual left (log-time; a flat zero means the line fits)")
        print(f"    {'band':>10} {'races':>9} {'rows':>11} {'applied':>9} "
              f"{'resid':>9} {'se':>8}")
        for lo, hi in zip(edges, edges[1:]):
            m = D.imp_mask & (s >= lo) & (s < hi)
            n = int(m.sum())
            if n < 2000:
                continue
            applied = float(np.average(D.imp_w[m] * b["imp"][D.imp_idx[m]],
                                       weights=w[m]))
            mr = float(np.average(resid[m], weights=w[m]))
            se = float(np.sqrt(np.average((resid[m] - mr) ** 2, weights=w[m]) / n))
            races = int(np.unique(D.race[m]).size)
            lab = (f"<{lo + 0:.1f}" if lo == -np.inf else
                   f"{lo:.1f}+" if hi == np.inf else f"{lo:.1f}..{hi:.1f}")
            if lo == -np.inf:
                lab = f"<{hi:.1f}"
            print(f"    {lab:>10} {races:>9,} {n:>11,} {applied:>+9.4f} "
                  f"{mr:>+9.4f} {se:>8.4f}")
    except Exception as exc:                                 # noqa: BLE001
        print(f"[joint] field strength by band: skipped ({type(exc).__name__}: {exc})")


def sharedTermKwargs(args):
    """The 2026-09-11 terms, for EVERY buildDesign call site: a holdout
    scored on a design without them would score a different model."""
    # ★ AND THE SEASON TIE (2026-09-29): its pairs are part of the design, so
    #   the held-out solve has to carry them or it scores the untied model.
    #   getattr, because other scripts build their namespaces without it.
    return dict(sport_level=args.sport_level,
                importance=("none" if args.no_importance else args.importance),
                indoor=not args.no_indoor,
                indoor_level=args.indoor_level,
                dist_table=not args.no_dist_table,
                season_tie=bool(getattr(args, "season_tie", False)))


# ★ THE GO-LIVE'S DESIGN, AS ONE CALL (2026-09-29). main() used to spell its
#   buildDesign arguments out inline, so a diagnostic that wanted "the design
#   the solve fitted" had to copy them -- and scripts/explain_joint_row.py
#   copied an OLD set: no era split, altitude always on. Under
#   XCP_ERA_YEARS=2 the solve has 223,097 (course, era) cells and the copy
#   had 76,010 base ones, so every row it explained would have read a
#   different course's difficulty had the size check not stopped it.
#   main() and the explainer now take these from the same function.
# ! THE HOLDOUT TAKES THESE TOO since 2026-09-29, with ONE exception kept:
#   the random splits still build without --altitude, as their explicit
#   argument lists always did, so what 08a scores does not move (see the
#   note in holdout). The forward split scores exactly this design.
def designKwargs(args):
    """buildDesign's keyword arguments for the full solve, from parsed (and
    applyImplications'd) run_joint arguments."""
    return dict(sport_offset=not args.no_sport_offset,
                curve=not args.no_curve, rust=not args.no_rust,
                dist=not args.no_dist, slope=not args.no_slope,
                link=args.link and not args.no_link, altitude=args.altitude,
                dist_bands=not args.no_dist_bands,
                split_ability=args.split_ability,
                era_years=args.era_years,
                **sharedTermKwargs(args))


# ★ WHAT THE SOLVE FILE SAYS ABOUT ITS OWN DESIGN. Written beside the numbers
#   so a reader rebuilding the design can take the settings from the file it
#   is about to index, not from an environment that may have changed since.
#   era_base_year is the pack's, over ALL rows with a course, which is what
#   eraCells numbers eras from.
# ! split_ability IS RECORDED BECAUSE NOTHING ELSE SAYS IT. Every other
#   setting leaves a trace (the '@e' keys, mu_fixed, beta, altitude_coef);
#   a split ability only changes how many athlete-seasons there are.
def designRecord(args, cols, D):
    rec = {"course_keys": np.array([str(k) for k in
                                    (getattr(D, "course_keys", None)
                                     or cols["course_keys"])]),
           "split_ability": np.array([bool(args.split_ability)]),
           "sport_offset": np.array([bool(D.sc is not None)]),
           # the 2026-09-29 switches (both off by default)
           "season_tie": np.array([bool(getattr(D, "n_tie", 0))]),
           "tilt_scale": np.array([str(getattr(args, "tilt_scale", "own"))])}
    if args.era_years:
        # the readers (course_bracket, track_variance, explain_joint_row)
        # rebuild (course, era) ids from this, not from whatever subset of
        # rows they hold
        _c = np.asarray(cols["course"]); _y = np.asarray(cols["year"])
        rec["era_years"] = np.array([int(args.era_years)])
        rec["era_base_year"] = np.array([int(_y[_c >= 0].min())])
    return rec


def reportSharedTerms(out, D, pool_names):
    """The asserted level, the meet-importance coefficients and the indoor
    coefficients, for the log."""
    if out.get("mu_fixed") is not None:
        mu = out["mu_fixed"]
        print(f"[joint] sport level ASSERTED at TF - XC = {mu[1] - mu[0]:+.5f} "
              f"log-time (XC_TRACK_GAP {js.XC_TRACK_GAP:.4f} = ln 1.06): the "
              f"average track is the zero, the average XC course reads "
              f"{100 * np.expm1(mu[0] - mu[1]):+.2f}% by construction, and the "
              f"fall-to-spring change is in the form curve")
    imp = out.get("importance")
    labels = getattr(D, "imp_labels", [])
    if imp is not None and labels and getattr(D, "n_imp", 0):
        share_sum = np.bincount(D.imp_idx, weights=np.abs(D.imp_w),
                                minlength=D.n_imp)
        if getattr(D, "imp_kind", None) == "field":
            print("[joint] field strength, log-time per unit of front strength "
                  f"({js.FIELD_UNIT:g} rating points of the race's top "
                  f"{js.FIELD_TOP_K} above the median race), per (pool, sport); "
                  "negative = a stacked field runs faster than the same "
                  "athletes in an ordinary one. Fitted, prior mean 0. NOT in "
                  "a rating; it keeps the venues that host only stacked fields "
                  f"honest. A healthy fit reads about {js.FIELD_EXPECTED:+.3f}. "
                  "(sum of |strength| over rows in brackets)")
        else:
            print("[joint] season-end taper, log-time per unit share, per (pool, "
                  "sport); negative = a field at the end of its season runs "
                  "faster than the same athletes mid-season. Fitted, prior mean "
                  "0. NOT in a rating; it keeps the championship-only venues "
                  "honest. (sum of share over rows in brackets)")
        by_pool = {}
        for i, lab in enumerate(labels):
            p, s = lab.split(":")
            by_pool.setdefault(p, []).append(
                f"{s} {100 * float(imp[i]):+.2f}% ({share_sum[i]:,.0f})")
        for p in pool_names:
            if p in by_pool:
                print(f"    {p:<10}" + "   ".join(by_pool[p]))
    ind = out.get("indoor")
    if ind is not None and getattr(D, "ind_w", None) is not None:
        ind = np.asarray(ind, dtype=np.float64).ravel()
        rows = np.bincount(D.ind_idx, weights=D.ind_w, minlength=ind.size)
        names = pool_names if ind.size == len(pool_names) else ["all"]
        how = "ASSERTED" if out.get("indoor_fixed") else "fitted"
        print(f"[joint] indoor level {how}, log-time per pool (+ = an indoor "
              "track is slower than the average outdoor one; inside delta, so "
              "it IS in the rating and on the board; asserted, the indoor "
              "cells' mean deviation from it is zero by construction): "
              + ", ".join(f"{n} {100 * float(v):+.2f}% ({int(r):,})"
                          for n, v, r in zip(names, ind, rows)))


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
        dist_walk_sd=float(getattr(args, "dist_walk", js.DIST_WALK_SD) or 0.0),
        identified_priors=not args.priors_from_all_cells,
        sigma_u_floor=_sigmaUFloor(args.sigma_u_floor),
        ability_weight=args.ability_weight,
        top_frac=args.top_frac,
        nested_var=not args.diag_var,
        season_tie_sd=getattr(args, "season_tie_sd", None),
        # set by main() once the pool names are known (tiltScaleInputs);
        # None is the own-pool tilt, the shipped behaviour
        tilt_scale=getattr(args, "_tilt_scale_inputs", None),
    )


# ★ WHAT THE ENGINE NEEDS TO READ A RATING ON THE HS SCALE (js.TILT_SCALES).
#   pool_view.hsFactor is C(hs)/C(pool) x F(d, pool)/F(d, hs), one number per
#   pool: the geometric mean of the two sports' ratios at REP_DIST (XC 5000,
#   TF 1600). The C ratio comes from the solve's own ratings as it runs
#   (js.tiltPoolFactors); this supplies the rest -- each pool's same-gender
#   HS twin and the F ratio -- from normalize_distance's factor function, the
#   one pool_view reaches through conversions._forward_factor. No database.
# ! A pool without a gender suffix, without its HS twin in the pack, or
#   whose factor cannot be evaluated gets f_ratio NaN and KEEPS ITS OWN
#   SCALE, and the log says which -- pool_view's None, not a 1.0 guess.
_TILT_REP_DIST = {"XC": 5000.0, "TF": 1600.0}      # pool_view._REP_DIST


def tiltScaleInputs(pool_names, factor_fn=None, verbose=True):
    """{hs_of_pool, f_ratio, names} for js.solveJoint(tilt_scale=...).
    factor_fn(distance_m, pool, sport) -> the normaliser's multiplier;
    default normalize_distance._normalizationFactorCached."""
    names = [str(n).split("|", 1)[0] for n in pool_names]
    if factor_fn is None:
        import normalize_distance as nd

        # ★ BY ABILITY THE F RATIO IS 1 (pool_view.hsFactor, 2026-09-29):
        #   every anchor is 5000 m, and the time-free factor carries each
        #   pool's residual, which is not an anchor to convert
        if nd.abilityMode():
            def factor_fn(d, pool, sport):
                return 1.0
        else:
            def factor_fn(d, pool, sport):
                return nd._normalizationFactorCached(d, pool, None, None, None,
                                                     sport, None)
    index = {n: i for i, n in enumerate(names)}
    hs_of = np.full(len(names), -1, dtype=np.int64)
    fr = np.full(len(names), np.nan)
    why = {}
    for p, name in enumerate(names):
        suffix = name.rsplit("_", 1)[-1]
        twin = f"hs_{suffix}"
        if suffix not in ("m", "f"):
            why[name] = "no gender suffix"
            continue
        if twin not in index:
            why[name] = f"{twin} is not in the pack"
            continue
        hs_of[p] = index[twin]
        if name == twin:
            fr[p] = 1.0
            continue
        ratios = []
        for sp, d in _TILT_REP_DIST.items():
            try:
                f_own, f_hs = factor_fn(d, name, sp), factor_fn(d, twin, sp)
            except Exception as exc:                          # noqa: BLE001
                why[name] = f"{sp} factor raised {type(exc).__name__}"
                continue
            if f_own and f_hs and f_own > 0 and f_hs > 0:
                ratios.append(float(f_own) / float(f_hs))
        if ratios:
            fr[p] = float(np.exp(np.mean(np.log(ratios))))
        else:
            why.setdefault(name, "no distance factor")
    if verbose:
        print("[joint] tilt scale HS: each pool's rating is read on its HS "
              "twin's scale, C(hs)/C(pool) from the solve's ratings x the "
              "distance factors' ratio: "
              + ", ".join(f"{n} F x{fr[i]:.4f}" for i, n in enumerate(names)
                          if np.isfinite(fr[i]) and n != f"hs_{n.rsplit('_', 1)[-1]}")
              + ("; OWN SCALE: " + ", ".join(f"{n} ({w})" for n, w in why.items())
                 if why else ""))
    return {"hs_of_pool": hs_of, "f_ratio": fr, "names": names}


HOLDOUT_SEASON_BUCKETS = ((1, 1), (2, 2), (3, 5), (6, None))   # for reading


# ★ WHAT THE TIE MOVED, EVERY RUN THAT CARRIES IT. At the solution the rows'
#   pull on an ability and the tie's balance: den_i (a_i - mean_i) =
#   -tie_gradient_i, so the season sits tie_gradient_i / den_i away from
#   where its own rows alone (against the same courses) would put it. That
#   is the move, exactly, and by season size it is the owner's test: a full
#   season barely moves, a two-race season moves toward its neighbours.
def seasonTieMoves(a, D, w, tie_w, tie_mean):
    """Per athlete-season, the tie's displacement in log-time (positive =
    the tie made it slower), 0 for untied seasons."""
    a = np.asarray(a, dtype=np.float64)
    g = js.seasonTieApply(a, D, tie_w, tie_mean)
    lm = tie_w * tie_mean
    g = g + (np.bincount(D.tie_k0, weights=lm, minlength=D.n_ath)
             - np.bincount(D.tie_k1, weights=lm, minlength=D.n_ath))
    den = np.bincount(D.athlete, weights=np.asarray(w, dtype=np.float64),
                      minlength=D.n_ath)
    return np.where(den > 0, -g / np.maximum(den, 1e-12), 0.0)


def reportSeasonTie(out, D):
    if out.get("tie_w") is None or not getattr(D, "n_tie", 0):
        return
    for line in (out.get("season_tie_lines") or []):
        print(line)
    a = out.get("ability_raw", out["ability"])
    mv = seasonTieMoves(a, D, out["weights"], np.asarray(out["tie_w"]),
                        np.asarray(out["tie_mean"]))
    tied = np.zeros(D.n_ath, dtype=bool)
    tied[D.tie_k0[np.asarray(out["tie_w"]) > 0]] = True
    tied[D.tie_k1[np.asarray(out["tie_w"]) > 0]] = True
    n = np.asarray(out["n_races"])
    print("[joint] season tie: how far it moved each tied season from its own "
          "rows (log-time %, about rating points at 100; + = slower):")
    print(f"    {'races':>7}{'seasons':>12}{'median |move|':>15}{'p90 |move|':>12}"
          f"{'mean move':>11}")
    for lo, hi in ((1, 1), (2, 2), (3, 5), (6, 9), (10, None)):
        m = tied & (n >= lo) & (True if hi is None else n <= hi)
        if not m.any():
            continue
        lab = f"{lo}" if lo == hi else (f"{lo}+" if hi is None else f"{lo}-{hi}")
        x = 100 * mv[m]
        print(f"    {lab:>7}{int(m.sum()):>12,}{np.median(np.abs(x)):>14.2f}%"
              f"{np.percentile(np.abs(x), 90):>11.2f}%{x.mean():>+10.3f}%")


def reportTiltScale(out, pool_names):
    """The HS factors the tilt read each pool through (--tilt-scale hs), to
    set beside the site's (scripts/diag_hs_factor.py): the same formula, the
    solve's pool means against the published rows' medians."""
    fac = out.get("tilt_pool_factor")
    if fac is None:
        return
    print("[joint] tilt read on the HS scale; per-pool factor own -> HS "
          "(the site's pool_view.hsFactor, on the solve's own pool means): "
          + ", ".join(f"{n} x{float(f):.4f}" for n, f in zip(pool_names, fac)))


def holdoutBreakdown(err, season_rows, pool, min_rows=200, rating=None):
    """Lines: the held-out error sd and mean by the athlete-season's
    training rows, by pool and, given each row's rating, by rating band.
    Pure; a bucket under min_rows is skipped."""
    err = np.asarray(err, dtype=np.float64)
    season_rows = np.asarray(season_rows)
    pool = np.asarray(pool, dtype=object)
    out = ["        by the athlete-season's TRAINING rows (what the season tie "
           "acts on):"]
    for lo, hi in HOLDOUT_SEASON_BUCKETS:
        m = (season_rows >= lo) & (True if hi is None else season_rows <= hi)
        if int(m.sum()) < min_rows:
            continue
        lab = f"{lo}" if lo == hi else (f"{lo}+" if hi is None else f"{lo}-{hi}")
        out.append(f"          {lab:>5} rows: sd {err[m].std():.6f}  mean "
                   f"{err[m].mean():+.5f}  ({int(m.sum()):,} held-out rows)")
    out.append("        by pool (what the HS tilt acts on):")
    for name in sorted(set(pool.tolist())):
        m = pool == name
        if int(m.sum()) < min_rows:
            continue
        out.append(f"          {str(name):>10}: sd {err[m].std():.6f}  mean "
                   f"{err[m].mean():+.5f}  ({int(m.sum()):,})")
    # ★ IS IT AS GOOD AT THE EXTREMES? (owner, 2026-10-06: "I wonder if our
    #   accuracy changes as ability goes to extremes.") The same score by
    #   the athlete's own-pool rating, on the tilt report's bands. A mean
    #   that runs + at one end and - at the other is the model mis-scaling
    #   that end (the tilt table's applied-vs-implied h says where).
    # ! THE BAND IS THE TRAINING RATING, so regression to the mean is in
    #   it: a rating from one or two races is partly luck, and its held-out
    #   row drifts back -- top bands read +, bottom bands -, with no model
    #   fault. The second column keeps athlete-seasons in the top
    #   training-rows bucket, where that luck is small; a slope that
    #   survives there is the model's.
    if rating is not None:
        r = np.asarray(rating, dtype=np.float64)
        lo_full = HOLDOUT_SEASON_BUCKETS[-1][0]
        full = season_rows >= lo_full
        edges = (-np.inf,) + tuple(TILT_REPORT_BANDS) + (np.inf,)
        out.append(f"        by the athlete's rating (own pool; + = ran slower "
                   f"than predicted)      all rows  |  {lo_full}+ training rows:")
        for a, b in zip(edges[:-1], edges[1:]):
            m = np.isfinite(r) & (r >= a) & (r < b)
            if int(m.sum()) < min_rows:
                continue
            lab = (f"<{b:.0f}" if not np.isfinite(a) else
                   f">={a:.0f}" if not np.isfinite(b) else f"{a:.0f}-{b:.0f}")
            line = (f"          {lab:>7}: sd {err[m].std():.6f}  mean "
                    f"{err[m].mean():+.5f}  ({int(m.sum()):,})")
            mf = m & full
            if int(mf.sum()) >= min_rows:
                line += (f"  |  sd {err[mf].std():.6f}  mean "
                         f"{err[mf].mean():+.5f}  ({int(mf.sum()):,})")
            out.append(line)
    return out


CROSS_SPORT_DISTANCES = (800, 1500, 1600, 3000, 3200, 4000, 5000, 6000, 8000, 10000)


def crossSportBreakdown(err, pool, dist, rating=None, athlete=None, min_rows=200):
    """Lines for the cross-sport holdout: the BIAS (mean and median of
    held-out log time minus prediction; + = they ran SLOWER than the model
    said from their other sport) by pool x distance, and by the athlete's
    rating quartile within pool. This is the number a conversion card gets
    wrong or right. Pure."""
    err = np.asarray(err, dtype=np.float64)
    pool = np.asarray(pool, dtype=object)
    dist = np.asarray(dist, dtype=np.float64)
    std = np.asarray(CROSS_SPORT_DISTANCES, dtype=np.float64)
    near = std[np.abs(dist[:, None] - std[None, :]).argmin(axis=1)] if dist.size else dist
    ok_d = np.abs(dist - near) <= near * 0.04
    out = ["        cross-sport BIAS (+ = ran SLOWER than predicted from the other "
           "sport), % of time:",
           f"          {'pool':<11}{'distance':>9}{'rows':>10}{'mean':>8}{'median':>8}"]
    for name in sorted(set(pool.tolist())):
        for d in std:
            m = (pool == name) & ok_d & (near == d)
            if int(m.sum()) < min_rows:
                continue
            out.append(f"          {str(name):<11}{int(d):>9}{int(m.sum()):>10,}"
                       f"{100 * err[m].mean():>+7.2f}%{100 * np.median(err[m]):>+7.2f}%")
    if rating is not None and athlete is not None:
        r = np.asarray(rating, dtype=np.float64)[np.asarray(athlete)]
        out.append("          by the athlete's rating quartile within the pool "
                   "(median bias):")
        for name in sorted(set(pool.tolist())):
            m = (pool == name) & np.isfinite(r)
            if int(m.sum()) < 4 * min_rows:
                continue
            q = np.quantile(r[m], [0.25, 0.5, 0.75])
            band = np.digitize(r[m], q)
            cells = "  ".join(f"Q{b + 1} {100 * np.median(err[m][band == b]):+.2f}%"
                              f"@{np.median(r[m][band == b]):.0f}" for b in range(4))
            out.append(f"          {str(name):<11}{cells}")
    return out


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
    fwd = None
    if kind == "forward":
        # ★ FORWARD IN TIME (forward_holdout): fit on the rows before the
        #   window, predict every rated row in it, and leave everything on or
        #   after its end out of both -- the sealed season is refused unless
        #   --sealed says otherwise
        fwd = (getattr(args, "_forward", None)          # main() checked it early
               or fh.forwardWindow(cols, getattr(args, "holdout_from", None),
                                   getattr(args, "holdout_until", None),
                                   bool(getattr(args, "sealed", False))))
        keep_tr, keep_te, keep_x = fh.forwardSplit(fwd["dates"], keep,
                                                   fwd["from"], fwd["until"])
        te_local = keep_te[idx]
        print(f"[forward] {int(keep_tr.sum()):,} training rows, "
              f"{int(keep_te.sum()):,} held out, {int(keep_x.sum()):,} after "
              f"the window left out of both")
        if not keep_tr.any() or not keep_te.any():
            raise SystemExit("[forward] nothing to fit or nothing to score in "
                             "this window")
    else:
        _season = _sport = None
        if kind in ("sport", "sport-xc"):
            # one code per athlete and academic year: fall XC and the next
            # spring's track share it (pe.athleteSeasonCodes without sport)
            if "sport" not in cols:
                raise SystemExit("[joint] --holdout-kind sport needs a merged pack")
            _season, _ = pe.athleteSeasonCodes(np.asarray(cols["athlete"])[idx],
                                               np.asarray(cols["year"])[idx])
            _sport = np.asarray(cols["sport"])[idx]
        te_local = pv.splitFor(kind, idx.size,
                               race=race_all[idx],
                               athlete=np.asarray(cols["athlete"])[idx],
                               cell=np.asarray(cols["course"])[idx],
                               frac=0.10, seed=1, season=_season, sport=_sport)
        keep_tr = np.zeros(keep.size, dtype=bool); keep_tr[idx[~te_local]] = True
        keep_te = np.zeros(keep.size, dtype=bool); keep_te[idx[te_local]] = True
    # ★ THE GO-LIVE'S DESIGN (designKwargs), one call for both kinds.
    # ⚠ BUT THE RANDOM SPLITS KEEP THEIR OLD BLIND SPOT: they have always
    #   built without --altitude (the explicit argument lists this replaced
    #   never passed it), while 08a and every ladder rung pass it -- so the
    #   'no-altitude' rung has scored the same model as 'base'. Kept, so a
    #   race-holdout number stays comparable with every one before it
    #   (2026-09-29); the FORWARD split scores the design as shipped.
    dkw = designKwargs(args)
    if fwd is None:
        dkw["altitude"] = False
    D_tr, _, _ = buildDesign(cols, keep_tr, **dkw)
    # the held-out rows are predicted, not fitted: the tie's pairs live on
    # the training design
    D_te, _, _ = buildDesign(cols, keep_te, **dict(dkw, season_tie=False))
    t0 = time.time()
    out = js.solveJoint(y_all[keep_tr], design=D_tr, n_probe=0,
                        **solveKwargs(args, athlete_pool, verbose=False))
    # the walk the held-out solve fitted, so the rung's log says what it tied
    if out.get("season_tie_lines"):
        print("[joint] season tie on the training rows (walk per transition "
              "and band; log-time, negative m = faster the next year):")
        for line in out["season_tie_lines"]:
            print(line)
    _pool_of_raw, _pool_names = poolCodes(cols["athlete_keys"])
    if fwd is not None:
        # every held-out season is one the fit never saw: carry the athlete's
        # latest fitted season across the cut (fh.carryForward), then the
        # engine's own prediction; a cold start is not in the number
        out_c, info = fh.carryForward(out, D_tr, D_te, cols, keep_tr, keep_te,
                                      fwd["dates"], _pool_of_raw, _pool_names,
                                      split_ability=args.split_ability)
        pred, _cov = js.predictHeldOut(out_c, D_tr, D_te, athlete_pool=athlete_pool,
                                       tilt=not args.no_tilt)
        pred = np.where(info["how"] != fh.HOW_NONE, pred, np.nan)
        cov = (info["status"] == fh.STATUS_KNOWN) & np.isfinite(pred)
        # for the forward split, the SOURCE season's training rows: the
        # season the ability came from is the one the tie would have moved
        n_tr_season = info["source_rows"]
        status, train_races = info["status"], info["train_races"]
        cell_seen = info["cell_seen"]
        print(f"[forward] held-out athlete-seasons: {info['n_own']:,} fitted in "
              f"their own season (a cut inside one), {info['n_carried']:,} "
              f"carried unmoved, {info['n_tied']:,} moved by the season tie, "
              f"{info['n_uncarried']:,} cold")
    else:
        pred, cov = js.predictHeldOut(out, D_tr, D_te, athlete_pool=athlete_pool,
                                      tilt=not args.no_tilt)
        # per held-out row: how many TRAINING rows its athlete-season kept --
        # with the pool, the two axes the 2026-09-29 switches act on (a thin
        # season is what the season tie moves; the pool is what the HS tilt
        # equalises)
        n_tr_season = np.bincount(D_tr.athlete, minlength=D_tr.n_ath)[D_te.athlete]
        status, train_races = fh.rowStatus(cols, keep_tr, keep_te)
        cell_seen = (np.bincount(D_tr.cell, minlength=D_tr.n_cell) > 0)[D_te.cell]
    y_te = y_all[keep_te]
    err = y_te[cov] - pred[cov]
    pool_te = np.asarray(_pool_names, dtype=object)[
        _pool_of_raw[np.asarray(cols["athlete"])[keep_te]]]
    # ★ THE SCORECARD'S UNIT OF HEAD-TO-HEAD, AND ITS BOOTSTRAP CLUSTER: the
    #   (cell, day) race whatever --race-key says, split by pool and distance
    #   (fh.pairGroups says why)
    race_cd = (race_all if _RACE_KEY["by"] != "venue"
               else raceCodes(cols["course"], cols["days"])[0])[keep_te]
    sport_te = (np.asarray(cols["sport"])[keep_te].astype(np.int8)
                if "sport" in cols else np.zeros(int(keep_te.sum()), dtype=np.int8))
    group_te = fh.pairGroups(race_cd, pool_te,
                             np.asarray(cols["dist_m"])[keep_te] if "dist_m" in cols
                             else None)
    # ★ THE HELD-OUT ROWS, PREDICTION BY PREDICTION (2026-09-12), so another
    #   engine can be scored on exactly these rows: the bracket engine
    #   covers 59% of them and the joint model 89%, and two error sds on
    #   different rows are not a comparison. The ladder sets the path per
    #   rung (ladder_logs/<rung>_holdout.npz); scripts/bracket_holdout.py
    #   reads the base rung's.
    dump = os.environ.get("XCP_HOLDOUT_DUMP")
    if dump:
        os.makedirs(os.path.dirname(dump) or ".", exist_ok=True)
        rows_here = idx[te_local].astype(np.int64)
        # ! ROWS IN THE PACK FILE'S ORDER. main() sorts the rows by athlete
        #   and year before anything runs (sortRowsByAthlete), so an index
        #   into these arrays is not a row of the file; the sort keeps the
        #   file's row number per sorted row, and that is what is written.
        orig = (np.asarray(cols["_row_order"])[rows_here] if "_row_order" in cols
                else rows_here)
        np.savez(dump, row=orig, pred=pred.astype(np.float64),
                 covered=cov.astype(bool), y=y_te.astype(np.float64),
                 kind=np.array([kind]), sample_pct=np.array([float(args.sample_pct)]),
                 sample_seed=np.array([int(args.sample_seed)]),
                 row_space=np.array(["file"]),
                 season_train_rows=n_tr_season.astype(np.int32),
                 pool=np.asarray(pool_te, dtype=str),
                 # the scorecard's (forward_holdout; switch_scorecard reads it)
                 race=race_cd.astype(np.int64), group=group_te,
                 sport=sport_te, train_races=train_races.astype(np.int32),
                 cell_seen=np.asarray(cell_seen, dtype=bool),
                 status=np.asarray(status, dtype=np.int8),
                 window=np.array([str(fwd["from"]), str(fwd["until"])]
                                 if fwd else ["", ""]),
                 sealed=np.array([bool(fwd and fwd["sealed"])]))
        print(f"[joint] held-out predictions -> {dump} ({int(cov.sum()):,} covered "
              f"of {cov.size:,})")
    # ⚠ SAY WHICH RUNG. "error sd 0.044" means nothing without it: holding
    #   out rows scores interpolation, holding out races scores prediction,
    #   and the two are not comparable numbers.
    _what = {"row": "10% of ROWS -- optimistic, the same race is in train",
             "race": "10% of RACES -- a whole new race at a known course",
             "athlete": "10% of ATHLETES -- rating a newcomer",
             "course": "10% of COURSES -- a course never seen before",
             "sport": "the TRACK season of 10% of dual-sport athlete-seasons, "
                      "predicted from their cross country alone -- the "
                      "conversion, on races never seen",
             "sport-xc": "the CROSS COUNTRY season of 10% of dual-sport "
                         "athlete-seasons, predicted from their track alone",
             "forward": (f"every rated row in [{fwd['from']}, {fwd['until']}) "
                         f"from a fit on the rows before it -- a whole "
                         f"{'SEALED ' if fwd['sealed'] else ''}season forward"
                         if fwd else "")}[kind]
    print(f"\n[joint] HELD OUT: {_what}")
    print(f"[joint] error sd {err.std():.6f}   covered {cov.mean():.1%}"
          f"   [{time.time() - t0:.0f}s]")
    # ★ THE SCORECARD, ON EVERY SPLIT (forward_holdout.scoreLines): bias,
    #   median and p90 |error|, head-to-head per race, by sport, pool,
    #   training races and new-vs-seen course. A switch is compared on it,
    #   row for row, by scripts/switch_scorecard.py --holdout.
    for line in fh.scoreLines({"y": y_te, "pred": pred, "covered": cov,
                               "group": group_te, "sport": sport_te,
                               "pool": np.asarray(pool_te, dtype=str),
                               "train_races": train_races,
                               "cell_seen": cell_seen, "status": status}):
        print(line)
    if "sport" in cols:
        for code, name in ((0, "XC"), (1, "TF")):
            m = cov & (sport_te == code)
            if m.sum() > 1000:
                e = y_te[m] - pred[m]
                print(f"        {name}: {e.std():.6f}  ({int(m.sum()):,} rows)")
    # ★ WHERE A SWITCH ACTS, NOT ONLY THE HEADLINE. The season tie moves thin
    #   seasons and should leave full ones alone; the HS tilt moves the
    #   non-HS pools. One sd over every row hides both, so the same score by
    #   the athlete-season's TRAINING rows and by pool. The bucket edges are
    #   for reading only. scripts/switch_scorecard.py --holdout compares two
    #   rungs' dumps row for row on the rows both covered.
    _r = out.get("rating")
    for line in holdoutBreakdown(
            err, n_tr_season[cov], pool_te[cov],
            rating=(None if _r is None
                    else np.asarray(_r, dtype=np.float64)[D_te.athlete[cov]])):
        print(line)
    if kind in ("sport", "sport-xc") and "dist_m" in cols:
        for line in crossSportBreakdown(err, pool_te[cov],
                                        np.asarray(cols["dist_m"])[keep_te][cov],
                                        out.get("rating"), D_te.athlete[cov]):
            print(line)
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
    # ! IMPORTED HERE, LIKE EVERY OTHER USE OF IT IN THIS FILE. bracket_engine
    #   is not a module-level import (it pulls numpy work nothing else needs),
    #   so the two bracket flags below have to reach it the same way.
    import bracket_engine as be
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
                    choices=list(pv_kinds()) + ["forward"],
                    help="what to hold out together (default race); "
                         "'forward' fits on the past and predicts a whole "
                         "later season (engine/forward_holdout.py)")
    # ★ THE FORWARD SPLIT'S WINDOW (2026-09-29). Unset, it is the pack's
    #   validation season (the one before the last complete season), and
    #   the last complete season is SEALED: refused without --sealed.
    ap.add_argument("--holdout-from", default=None, metavar="YYYY-MM-DD",
                    help="forward: fit on rows before this date, score from "
                         "it (default: the validation season's first day)")
    ap.add_argument("--holdout-until", default=None, metavar="YYYY-MM-DD",
                    help="forward: score rows before this date; rows on or "
                         "after it are left out of the fit and the score")
    ap.add_argument("--sealed", action="store_true",
                    help="forward: score the SEALED test season (the last "
                         "complete one). Once, deliberately")
    # ★★ TIME-VARYING COURSE DIFFICULTY (owner, asked three times). 0 keeps
    #    one difficulty for all time; 2 or 3 splits each course into eras
    #    that width and ties adjacent ones with a random walk. See
    #    joint_solve.ERA_DRIFT_SD for the tying, which is the whole point:
    #    a well-measured venue moves between eras, a thin one does not.
    ap.add_argument("--era-years", type=int, default=js.ERA_YEARS_DEFAULT,
                    metavar="N",
                    help="split each course into N-year eras tied by a "
                         "random walk (0 = one difficulty for all time)")
    ap.add_argument("--dist-walk", type=float, default=js.DIST_WALK_SD,
                    help="sd per unit log-distance of the random walk tying an event "
                         "offset to its neighbouring classes (js.DIST_WALK_SD; 0 = off)")
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
    # ★ THE SEASON TIE (owner, 2026-09-29: "try to be safe and test it"). OFF
    #   by default; see js.SEASON_TIE_BANDS for what it is and why it is not
    #   the link above. Score it before it ships: the ladder's 'season-tie'
    #   rung against 'base', then scripts/switch_scorecard.py on two
    #   full solves.
    ap.add_argument("--season-tie", action="store_true",
                    help="tie consecutive athlete-seasons of one person by a "
                         "random walk whose mean change and width per "
                         "transition (pool > pool, rating band) are fitted on "
                         "the untied first pass (js.seasonTieFit). Off by "
                         "default")
    ap.add_argument("--season-tie-sd", type=float, default=None, metavar="SD",
                    help="STATE the walk's sd per sqrt(year) for every "
                         "transition instead of fitting it (the mean is still "
                         "fitted); for pricing the fit, not for go-live")
    # ★ THE TILT ON ONE SCALE (owner, 2026-09-29). 'own' is the shipped tilt;
    #   'hs' reads it at the HS-equivalent rating (js.TILT_SCALES). The
    #   pipeline passes XCP_TILT_SCALE here and the site reads the same
    #   variable (racecast/tilt.py), so set it in /etc/xc-predictor.env,
    #   where both see it -- a board tilted one way and converted the other
    #   is two scales again.
    ap.add_argument("--tilt-scale", choices=js.TILT_SCALES, default="own",
                    help="the rating the course tilt reads: own (the athlete's "
                         "own pool's scale, default) or hs (the HS-equivalent: "
                         "own x the pool's HS factor)")
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
    # ★ THE LEVEL AS A NUMBER (2026-09-11). Every practical rating system
    #   that spans two surfaces states the conversion (Tully's speed-rating
    #   to track chart, the NCAA facility factors, WMA's log-distance
    #   interpolation); none estimates it from the results, because sport
    #   is season and the data cannot. --merge-sports asserted ZERO; this
    #   asserts the stated grass cost instead, and unlike merge it takes
    #   the level OUT of theta (Design.mu_fixed) rather than leaving an
    #   unpenalised mu for CG to park along the curve's null direction.
    #   Implies everything --merge-sports implies.
    ap.add_argument("--sport-level", type=float, default=None, metavar="G",
                    help="ASSERT the XC/TF level: the track is G log-time "
                         f"faster than the average XC course (the stated "
                         f"grass cost is {js.XC_TRACK_GAP:.4f} = ln 1.06). "
                         "Implies --no-sport-offset, --winter-gain 0, "
                         "--curve-gap 0 and drops --winter-gain-bands. "
                         "Unset: the level is estimated, as before")
    ap.add_argument("--importance", choices=("field", "season-end", "none"),
                    default="field",
                    help="the taper / field term's covariate (issue #22): "
                         "'field' (default) the race's front strength, the "
                         "mean rating of its top five relative to the median "
                         "race, from the model's own ratings; 'season-end' "
                         "the share of the field at the end of their own "
                         "season; 'none' no term")
    ap.add_argument("--no-importance", action="store_true",
                    help="the same as --importance none")
    ap.add_argument("--no-indoor", action="store_true",
                    help="no indoor term at all; indoor cells carry the "
                         "surface alone, as before 2026-09-11")
    ap.add_argument("--indoor-level", default=str(js.IND_LEVEL_DEFAULT),
                    metavar="G|fit",
                    help="the indoor level, log-time, ASSERTED (default "
                         f"{js.IND_LEVEL_DEFAULT}: an indoor oval is that much "
                         "slower than the average outdoor track, the NCAA "
                         "facility factors); indoor is season, so the data "
                         "cannot identify it from the winter form. 'fit' "
                         "estimates it as before")
    ap.add_argument("--no-dist-table", action="store_true",
                    help="event-offset classes the season-best pairs cannot "
                         "calibrate keep a zero prior instead of the "
                         "published tables' relation (distance_tables)")
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
    ap.add_argument("--diag-var", action="store_true",
                    help="the variance E-step from the information diagonal "
                         "(sigma2 / A_ii) instead of the exact cell + races "
                         "block -- the pre-2026-09-11 behaviour, a ladder rung")
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
    ap.add_argument("--from-state", default=None, metavar="FILE",
                    help="skip the solve: load its output from <out>_state.npz "
                         "written by an earlier run with the same pack, sample and "
                         "flags, and run the swap, the reports, the save and the "
                         "go-live from there")
    ap.add_argument("--difficulty", default="joint", choices=("joint", "bracket"),
                    help="whose course numbers the go-live publishes: the joint "
                         "solve's, or the bracket engine's fitted on the solve's "
                         "residual (bracketDifficulties)")
    ap.add_argument("--bracket-window", type=float, default=21.0)
    ap.add_argument("--bracket-top", type=float, default=0.5)
    ap.add_argument("--bracket-place-radius", type=float, default=None,
                    help="metres within which courses of one kind form a PLACE the "
                         "bracket engine pulls them toward (bracket_engine.PLACE_RADIUS_M, "
                         "%s; 0 = no place prior)" % 400)
    ap.add_argument("--bracket-sibling-tol", type=float, default=None,
                    help="a course's cells at distances within this fraction of "
                         "each other share one history in the bracket engine "
                         "(off by default; bracket_engine.SIBLING_DIST_TOL is 0.03 "
                         "when asked for)")
    ap.add_argument("--bracket-place-venue", type=int, default=None, choices=(0, 1),
                    help="1 (default): one canonical XC course's cells at other "
                         "distances are a place too (bracket_engine VENUE PRIOR); 0 off")
    ap.add_argument("--bracket-place-prior", type=float, default=None,
                    help="races' worth of pull of a course toward its place "
                         "(bracket_engine.PRIOR_PLACE, 2)")
    # ! A STRING, NOT AN int, SO "force" SURVIVES argparse. It used to be
    #   type=int and the call site then wrapped it in bool(), which would have
    #   collapsed "force" to True and lost the distinction the gauge needs.
    ap.add_argument("--track-level-by-pool", default="1",
                    choices=("0", "1", "force"),
                    help="1 (default): under --difficulty bracket, each host population's "
                         "outdoor tracks (hs, college, ms, ...) are recentred to the same "
                         "zero (run_joint.trackPopulationShift), EXCEPT under "
                         "--gauge flat400, where it would move the cells the gauge pinned "
                         "at 0.0; 0 leaves the level the linkage gave them; force applies "
                         "it even under flat400")
    ap.add_argument("--course-scale", default="fit",
                    help="under --difficulty bracket, the per-sport multiplier on the "
                         "course effects: 'fit' (default, from the tilt-by-band table so "
                         "implied = applied), 'off', a number, or 'XC=1.1,TF=1' "
                         "(run_joint.courseScales)")
    ap.add_argument("--sport-level-pools", default=None, metavar="SPEC",
                    help="the fall-to-spring gain per POOL LEVEL, log-time, applied at "
                         "go-live to the track rows of each level's pools so the "
                         "dual-sport gap reads that gain: 'college=0,hs=0.008,ms=0.012'. "
                         "Measured by scripts/sport_level_fit.py. A level not named is "
                         "left as the solve put it. Kept alongside --sport-level (the "
                         "solve's level stays; this corrects what the boards show)")
    ap.add_argument("--bracket-prior", default="fit",
                    help="the bracket engine's course prior in races: 'fit' (per "
                         "group -- XC, outdoor track, indoor track -- from the "
                         "courses with 2+ races), one number for every group, or "
                         "'XC=1,TF:out=2.5,TF:in=1' (bracket_engine.parsePrior)")
    ap.add_argument("--golive", action="store_true")
    ap.add_argument("--golive-dry", action="store_true")
    ap.add_argument("--anchor", default="career",
                    choices=("career", "seasonal"),
                    help="pool mean for the per-result rating")
    ap.add_argument("--collapse", default="best",
                    choices=("best", "recent", "weighted"),
                    help="season -> athlete_ratings row")
    # ★★ THE GAUGE: WHAT THE ZERO IS (plan §2). Only read under --difficulty
    #    bracket. "flat400" is the owner's design -- every flat outdoor 400m
    #    track at 0.0 exactly, so the zero is a fixed reference rather than a
    #    vote-weighted mean that a redistribution can push around. It needs the
    #    pack's track geometry and says so and falls back if it is missing.
    ap.add_argument("--gauge", default=None, choices=list(be.GAUGE_CHOICES),
                    help="the bracket engine's zero: flat400 (flat outdoor "
                         "400s at 0.0 each), outdoor (their mean at 0.0), or "
                         "all (the whole group's mean, pre-2026-09-18)")
    # ★ AND WHERE INDOOR SITS (plan §3). The same number XCP_INDOOR_LEVEL
    #   asserts to the joint solve, now reaching the bracket engine too: it is
    #   the TF:in group's shrinkage target, not a clamp.
    # ★ §4. Only meaningful with --gauge flat400, which is what makes the
    #   reference races unconfounded.
    ap.add_argument("--day-noise", default=None,
                    choices=list(be.DAY_NOISE_CHOICES),
                    help="the bracket course prior's numerator: fitted "
                         "(historic) or reference (the pinned cells)")
    # ★★ CROSS COUNTRY'S LEVEL AGAINST THE TRACK (owner, 2026-09-21: "the xc
    #    difficutly is fucked ngl"). The published table read XC - TF =
    #    +1.27% where every reader assumes +5.83%; the bracket engine had no
    #    term for the gap at all, while XCP_SPORT_LEVEL has been asserting it
    #    for the JOINT model all along. See bracket_engine.XC_LEVEL_MODES.
    ap.add_argument("--bracket-xc-level", type=float, default=None,
                    metavar="G",
                    help="ASSERT cross country's level: an average XC course "
                         f"is G log-time above the track zero (default "
                         f"{js.XC_TRACK_GAP:.4f} = ln 1.06, the stated grass "
                         f"cost). One additive shift, so the spread is "
                         f"untouched")
    ap.add_argument("--bracket-xc-level-mode", choices=be.XC_LEVEL_MODES,
                    default=None,
                    help="pin (default): shift the XC group so its "
                         "vote-weighted mean IS that level. off: leave it "
                         "wherever the gauge left it -- 0 by construction "
                         "under --gauge-scope sport, or inferred from a "
                         "0.2-0.5%%-of-rows bridge under merge")
    ap.add_argument("--bracket-indoor-centre", type=float, default=None,
                    help="log-time centre asserted for the indoor cells "
                         f"(bracket_engine.INDOOR_CENTRE, {be.INDOOR_CENTRE})")
    # ★ HOW THE CENTRE IS ENFORCED (owner, 2026-09-20: "indoor is still way
    #   too 'easy'"). pin sets the indoor group's vote-weighted MEAN to the
    #   centre -- "on avg +0.3 slower" as arithmetic; shrink is the old
    #   target-only behaviour, which heavily-raced ovals simply ignored.
    # ★ XC's ZERO: its own (sport) or shared with track (merge). See
    #   bracket_engine.GAUGE_SCOPES -- the bridge merge rests on is 0.2-0.5%
    #   of XC rows at the bracket window, so this is a flag, not a default.
    # ★ THE GATES: enforced (clamp, default) or merely counted (report).
    ap.add_argument("--bracket-indoor-gates", default=None,
                    choices=list(be.INDOOR_GATE_MODES),
                    help="clamp (default): hold every indoor cell inside the "
                         "gates; report: count and publish anyway")
    ap.add_argument("--gauge-scope", default=None, choices=list(be.GAUGE_SCOPES),
                    help="sport (default): each (sport, era) keeps its own "
                         "zero; merge: XC and track share one, so the "
                         "flat-400 reference anchors cross country")
    ap.add_argument("--bracket-indoor-mode", default=None,
                    choices=list(be.INDOOR_MODES),
                    help="pin the indoor group's mean to the centre (pin, "
                         "default), merely shrink toward it (shrink), or pin "
                         "each indoor geometry class on its measured level "
                         "(geometry; --bracket-indoor-levels)")
    ap.add_argument("--bracket-indoor-levels", default=None,
                    help="the measured per-geometry-class indoor levels for "
                         "--bracket-indoor-mode geometry (default "
                         "engine/data/indoor_geometry_levels.json, written by "
                         "scripts/indoor_outdoor_check.py --by geometry "
                         "--write-levels)")
    ap.add_argument("--no-race-effect", action="store_true",
                    help="leave the race-day effect out of per-result ratings")
    ap.add_argument("--race-effect-own", default="leave-out",
                    choices=("leave-out", "in"),
                    help="when --race-effect-sports puts the day in a rating: "
                         "'leave-out' (default) credits each runner the day "
                         "as the REST of the field ran it, so nobody's own "
                         "time moves their own day; 'in' the race's u as "
                         "fitted (js.raceEffectLeaveOneOut)")
    ap.add_argument("--race-effect-sports", default="",
                    help="the sports whose per-result ratings carry the "
                         "race-day term (comma list; default NONE, owner "
                         "2026-09-06: a slow race is a slow race, and the "
                         "model cannot tell mud from a jog -- measured "
                         "weather is credited in the normalisation instead). "
                         "The solve keeps the term for both sports, the "
                         "hover shows it. 'XC:fast' applies only a FAST "
                         "day (u < 0): a good-weather year at a hard course "
                         "is charged less, a slow day stays out "
                         "(joint_golive.dayModes)")
    return ap


def applyImplications(args, ap):
    # ! THE IMPLICATIONS ARE APPLIED HERE, NOT DOCUMENTED AND LEFT TO THE
    #   OPERATOR. Every one of them is part of the same assumption, and a run
    #   that carried three of the four would be measuring nothing anybody
    #   could name.
    # ! PARSED INTO THE {group: cap} SHAPE solveJoint already takes for
    #   --tau-tf-max, so there is one mechanism rather than two.
    # --indoor-level: a number asserts the indoor level; 'fit' estimates it
    lvl = getattr(args, "indoor_level", None)
    if isinstance(lvl, str):
        if lvl.strip().lower() in ("fit", "free", "estimate"):
            args.indoor_level = None
        else:
            try:
                args.indoor_level = float(lvl)
            except ValueError:
                ap.error(f"--indoor-level wants a log-time number or 'fit', "
                         f"not {lvl!r}")
    # ★ ONE INDOOR LEVEL FOR BOTH ENGINES UNDER --bracket-indoor-mode geometry
    #   (2026-09-29): see bracket_engine.geometryJointLevel. Read here, before
    #   the solve, so a missing levels file stops the run in seconds, not after it.
    if (getattr(args, "difficulty", "joint") == "bracket"
            and getattr(args, "bracket_indoor_mode", None) == "geometry"
            and not getattr(args, "no_indoor", False)):
        import bracket_engine as be
        try:
            args.indoor_level = be.geometryJointLevel(
                getattr(args, "bracket_indoor_levels", None), args.indoor_level)
        except (OSError, ValueError) as exc:
            ap.error(str(exc))
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
    if args.sport_level is not None:
        # the same five places --merge-sports settles, with a number in
        # the level instead of zero (see Design.mu_fixed). After the merge
        # block on purpose: given both, the number wins.
        args.no_sport_offset = True
        args.winter_gain = 0.0
        args.curve_gap = 0.0
        if args.sport_gap_delta:
            print("[joint] --sport-gap-delta is meaningless with "
                  "--sport-level (the level is asserted); ignoring it")
            args.sport_gap_delta = 0.0
        if args.winter_gain_bands:
            print(f"[joint] --winter-gain-bands {args.winter_gain_bands} is "
                  f"a post-solve shift of the track rows; with --sport-level "
                  f"it would move the asserted level. Dropping it.")
            args.winter_gain_bands = None
        if getattr(args, "merge_sports", False):
            print("[joint] --merge-sports and --sport-level both given; the "
                  "level is the number, not zero")
            args.merge_sports = False
        print(f"[joint] SPORT LEVEL ASSERTED: TF - XC = {-args.sport_level:+.5f} "
              f"log-time. No beta, no winter-gain pin, curve free: the "
              f"fall-to-spring change is fitness.")
    return args


# ------------------------------------------------------------------ #
# THE COURSE ORDERING FROM THE OWNER'S METHOD (2026-09-13)
# ------------------------------------------------------------------ #
#
# ★ WHY. On the same 537,107 held-out rows the bracket engine predicts a
#   new race at a known course with error sd 0.0423 against the joint
#   solve's 0.0472, in both sports, in every band of course thickness
#   (scripts/bracket_holdout.py, "SAME ROWS"). The owner: "the bracket
#   engine could help ... specifically the course ordering, so we'd only
#   have to worry about the cross-sport ordering and the distance spline".
#   So under --difficulty bracket the joint solve still fits everything it
#   fits -- the curve, the tilt, the event offsets, the altitude credit,
#   the sport level, the race-day terms -- and then the COURSE numbers are
#   replaced by the bracket engine's, and the abilities recomputed to
#   match. The site's ratings, boards and conversions read the swapped
#   numbers through the same go-live.
#
# ★ HOW. Every term of the solve except the course, the day and the
#   ability is taken off each row (rowPrediction less those three), so
#   the engine brackets z = ability + tilt * (course + day) + noise: the
#   curve, the event offsets and the altitude are not left for a course to
#   absorb, and a track hosting 800s is not "easier" than one hosting
#   3200s. The engine's cells are the design's cells, its ratings and
#   tilt the solve's. Its difficulties are recentred exactly as
#   recentreLevels centres d (the outdoor cells' unweighted mean per sport
#   is the zero; indoor cells keep their level), the asserted or fitted
#   sport level is added back, and each ability becomes the solve's own
#   weighted mean of its rows' residuals against the new courses -- the
#   ability block of the normal equations has no penalty, so that IS the
#   solve's ability given these courses. Ratings follow from abilities as
#   in the solve. The cell variance is the fitted race-day variance over
#   the races behind the cell plus the prior, which is what the engine
#   averaged.
# ★ COLLEGE TRACKS READ MORE NEGATIVE THAN HIGH SCHOOL TRACKS (owner,
#   2026-09-13: "track difficulties are a lot more negative for college
#   than for hs ... this makes me think track difficulty is carrying
#   something else ... they're separate pops track wise ... no linkage").
#   A 400 m track is a 400 m track, so two populations' average tracks
#   are the same level by physics. The engine cannot see that: a college
#   athlete-season references only college races and a high-school one
#   only high-school races, so the LEVEL between the college-only ovals
#   and the high-school-only ovals rests on whatever links them -- the
#   tracks that host both, where the college rows are the conference or
#   NCAA final (tapered, stacked: fast) against the same athletes'
#   invitational races elsewhere. Nothing in the engine names a taper or
#   a field, so that difference lands in the venues, and the college
#   cluster reads "easy". Ratings inside a pool do not move with a
#   constant shift of that pool's cells (the pool mean moves with them),
#   so what the shift costs is the board's number for every college track
#   and every conversion read off it.
#
#   So each host population's outdoor tracks are recentred to the same
#   zero: a cell's population is the level (hs, college, ms, elem) of the
#   majority of its rows, 'mixed' under TRACK_POP_MIN_SHARE, and each
#   population's unweighted outdoor mean is taken off its cells (indoor
#   cells move with their population, keeping the indoor level). The
#   shift per population is printed with the championship-class share
#   of its rows, which is the "something else" made visible. Off with
#   --track-level-by-pool 0.
TRACK_POP_MIN_SHARE = 0.6
TRACK_POP_MIN_CELLS = 20


def courseScales(spec, tilt_rows, race_rows=None):
    """{0: XC scale, 1: TF scale} from --course-scale: 'fit' (from the
    tilt-by-races rows of courses with 4+ races,
    bracket_engine.courseScaleFromRaces), 'bands' (the old reading, every
    band's voters: see WELL_KNOWN_RACES for why it is not the default),
    'off' or '1' (1.0 both), one number (both), or 'XC=1.1,TF=1'."""
    import bracket_engine as be
    spec = (str(spec) if spec is not None else "fit").strip().lower()
    if spec in ("", "fit"):
        return {0: be.courseScaleFromRaces(race_rows, "XC"),
                1: be.courseScaleFromRaces(race_rows, "TF")}
    if spec == "bands":
        return {0: be.courseScaleFromBands(tilt_rows, "XC"),
                1: be.courseScaleFromBands(tilt_rows, "TF")}
    if spec in ("off", "none", "1", "1.0"):
        return {0: 1.0, 1: 1.0}
    if "=" not in spec:
        v = float(spec)
        return {0: v, 1: v}
    out = {0: 1.0, 1: 1.0}
    for part in spec.split(","):
        k, v = part.split("=", 1)
        out[1 if k.strip().upper() == "TF" else 0] = float(v)
    return out


def parseLevelGains(spec):
    """{level: gain} from 'college=0,hs=0.008,ms=0.012,elem=0.015' (log-time
    fall-to-spring gain per pool level; None for nothing). 'off' / 'none'
    is nothing too, now that solve_env.sh sets a default to turn off."""
    if not spec or str(spec).strip().lower() in ("off", "none", "0"):
        return None
    out = {}
    for part in str(spec).split(","):
        if not part.strip():
            continue
        k, v = part.split("=", 1)
        out[k.strip().lower()] = float(v)
    return out or None


def trackPopulationShift(D_b, cell_keys, cell_row, level_row, meet_class_row=None,
                         min_share=TRACK_POP_MIN_SHARE, min_cells=TRACK_POP_MIN_CELLS):
    """(shift per cell, rows for the report). D_b: the courses (already
    centred per sport); cell_row / level_row: per row, the cell and the
    host level name ('hs', 'college', ...); meet_class_row: the pack's
    name-based class per row (>= 2 is a championship round), optional.
    Track cells only; each population with min_cells outdoor cells is
    recentred to zero over its outdoor cells, indoor cells of the
    population move with it."""
    D_b = np.asarray(D_b, dtype=np.float64)
    n_cell = D_b.size
    keys = [str(k) for k in cell_keys]
    is_tf = np.array([k.startswith("TF:") for k in keys])
    is_in = np.array([k.split("@", 1)[0].endswith(":in") for k in keys])
    cell_row = np.asarray(cell_row, dtype=np.int64)
    levels = sorted(set(str(x) for x in level_row))
    code = {name: i for i, name in enumerate(levels)}
    lv = np.array([code[str(x)] for x in level_row], dtype=np.int64)
    counts = np.zeros((n_cell, len(levels)))
    np.add.at(counts, (cell_row, lv), 1.0)
    total = counts.sum(axis=1)
    top = counts.argmax(axis=1)
    share = np.where(total > 0, counts[np.arange(n_cell), top] / np.maximum(total, 1), 0.0)
    pop = np.array([levels[t] if total[i] > 0 and share[i] >= min_share else "mixed"
                    for i, t in enumerate(top)], dtype=object)
    champ = None
    if meet_class_row is not None:
        mc = (np.asarray(meet_class_row, dtype=np.float64) >= 2).astype(np.float64)
        champ = np.bincount(cell_row, weights=mc, minlength=n_cell) / np.maximum(total, 1)
    shift = np.zeros(n_cell)
    rows = []
    for name in list(levels) + ["mixed"]:
        m_pop = is_tf & (pop == name) & (total > 0)
        m_out = m_pop & ~is_in
        n_out = int(m_out.sum())
        if n_out == 0:
            continue
        before = float(D_b[m_out].mean())
        applied = n_out >= min_cells
        if applied:
            shift[m_pop] = before
        rows.append(dict(population=name, cells=n_out, indoor=int((m_pop & is_in).sum()),
                         mean=before, sd=float(D_b[m_out].std()),
                         champ_share=(float(champ[m_out].mean()) if champ is not None else np.nan),
                         shift=before if applied else 0.0, applied=applied))
    return shift, rows


def centreDaysBySeason(u, D, season_row, sport_row):
    """(u centred, the shift taken out per row): each (sport, season)'s
    runner-weighted mean day term removed. Pure.

    ★ A SEASON IS NOT A DAY (owner, 2026-10-05, Newhall's own history: the
      field "ran" +0.6% in 2002, -0.1% in 2010, -1.4% in 2018, -3.4% in 2023,
      -4.8% in 2025, beside a course number that barely moved). The day term
      is how one day ran against its course's normal; a shift shared by every
      race of a season is the season's level -- the population getting
      faster, the shoes -- and in u it was read as a run of fast days. XC
      ratings carry u and track ratings do not, so a 2025 XC race lost ~3-5%
      against track (published means: XC -3.24%, TF +1.40%, runner-weighted),
      which is the direction of every XC -> track conversion complaint. The
      shift belongs to the abilities; the caller moves it there."""
    u = np.asarray(u, dtype=np.float64)
    key_row = (np.asarray(season_row, dtype=np.int64) * 2
               + np.asarray(sport_row, dtype=np.int64))
    keys, inv = np.unique(key_row, return_inverse=True)
    row_u = u[D.race]
    mean_g = (np.bincount(inv, weights=row_u, minlength=keys.size)
              / np.maximum(np.bincount(inv, minlength=keys.size), 1))
    shift_row = mean_g[inv]
    shift_race = np.zeros(D.n_race)
    shift_race[D.race] = shift_row               # one season per race: a race is one day
    return u - shift_race, shift_row


def dayLeanReport(u, D, cell_keys, min_races=5, top=15):
    """Lines: the courses whose race days all lean one way -- the mean day
    term over a course's races, with its standard error from the days'
    own spread. A course read as harder or easier than its runners run it
    shows here as days that are all slow or all fast. Pure."""
    u = np.asarray(u, dtype=np.float64)
    race_cell = js.cellOfRace(D)
    n_r = np.bincount(D.race, minlength=D.n_race) > 0
    rc = race_cell[n_r]
    uu = u[n_r]
    cnt = np.bincount(rc, minlength=D.n_cell)
    s1 = np.bincount(rc, weights=uu, minlength=D.n_cell)
    s2 = np.bincount(rc, weights=uu * uu, minlength=D.n_cell)
    ok = cnt >= min_races
    if not ok.any():
        return []
    mean = np.where(ok, s1 / np.maximum(cnt, 1), 0.0)
    var = np.where(ok, s2 / np.maximum(cnt, 1) - mean ** 2, 0.0)
    se = np.sqrt(np.maximum(var, 0.0) / np.maximum(cnt - 1, 1))
    t = np.where(ok & (se > 0), mean / np.maximum(se, 1e-12), 0.0)
    lean = np.flatnonzero(ok & (np.abs(t) >= 3.0))
    out = [f"[joint] courses whose days lean one way ({len(lean):,} of "
           f"{int(ok.sum()):,} courses with {min_races}+ races at |mean/se| >= 3; "
           f"+ = runners run them SLOWER than the course number says):"]
    for i in lean[np.argsort(-np.abs(mean[lean]))][:top]:
        out.append(f"        {str(cell_keys[i]):<34} {int(cnt[i]):>4} races  mean day "
                   f"{100 * mean[i]:+.2f}%  (se {100 * se[i]:.2f}%)")
    return out


def bracketDifficulties(out, D, cols, keep, y, athlete_pool, pool_names,
                        window=21.0, top=0.5, verbose=True, prior_group="fit",
                        track_level_by_pool=True, place_radius=None, prior_place=None,
                        course_scale="fit", gauge=None, indoor_centre=None,
                        indoor_mode=None, indoor_gate_mode=None,
                        indoor_levels=None, gauge_scope=None, xc_level=None,
                        xc_level_mode=None, day_noise=None, sibling_tol=None,
                        place_venue=None):
    """Swap the joint solve's course difficulties for the bracket engine's,
    in place in `out` (delta, d, ability, rating, cell_var/se; the joint's
    delta kept as delta_joint). Returns a dict of what happened."""
    import bracket as bk
    import bracket_engine as be
    t0 = time.time()
    b = D.unpack(out["theta"])
    h = np.asarray(out["h"], dtype=np.float64)
    if h.ndim == 0 or h.size != D.n:
        h = np.full(D.n, float(np.mean(h)))
    pred = js.rowPrediction(b, D, h, out["amp"])          # fits y less the asserted offsets
    u_row = (np.asarray(b["u"], dtype=np.float64)[D.race] if b["u"].size == D.n_race
             else np.zeros(D.n))
    mu_full = np.asarray(out["mu"], dtype=np.float64)
    g_row = np.asarray(D.group_row)
    # everything but the ability, the course and the day; the fitted indoor
    # term stays IN z so an indoor cell's number carries its level, as delta does
    rest = pred - b["a"][D.athlete] - h * (b["d"][D.cell] + u_row)
    if b.get("ind") is not None and getattr(D, "n_ind", 0):
        rest = rest - h * D.ind_w * b["ind"][D.ind_idx]
    rest = rest + h * (mu_full[g_row] - b["mu"][g_row])   # the sport level, asserted or fitted
    y = np.asarray(y, dtype=np.float64)
    z = y - rest                     # ability + h*(course + day) + indoor level + noise; no sport level
    # the design's rows and codes, for the engine
    sub = bk.subsetCols(cols, keep)
    sub["_season"] = np.asarray(D.athlete, dtype=np.int64)
    sub["_race"] = np.asarray(D.race, dtype=np.int64)
    sub["_cell"] = np.asarray(D.cell, dtype=np.int64)
    keys = [str(k) for k in cols["course_keys"]]
    cell_keys = [str(k) for k in (getattr(D, "course_keys", None) or keys)]
    key_to_base = {k: i for i, k in enumerate(keys)}
    base_of_cell = np.array([key_to_base[k.rpartition("@e")[0] if "@e" in k else k]
                             for k in cell_keys], dtype=np.int64)
    pool_of_raw, _names = poolCodes(cols["athlete_keys"])
    codes = dict(n_season=int(D.n_ath), n_race=int(D.n_race), n_cell=int(D.n_cell),
                 cell_keys=cell_keys, base_of_cell=base_of_cell,
                 era_years=int(any("@e" in k for k in cell_keys)), pool_of_raw=pool_of_raw,
                 pool_names=list(pool_names), n_base=len(keys), keys=keys)
    npz_like = {"rating": out["rating"]} if out.get("rating") is not None else None
    place_kw = {}
    if place_radius is not None:
        place_kw["place_radius"] = float(place_radius)
    if prior_place is not None:
        place_kw["prior_place"] = float(prior_place)
    if place_venue is not None:
        place_kw["place_venue"] = bool(int(place_venue))
    # ⚠⚠ THE GAUGE WAS NEVER PASSED HERE, AND THAT IS HALF OF WHY THE ASSERTED
    #    INDOOR LEVEL NEVER LANDED. This call took the engine's default
    #    ("outdoor") whatever the pipeline asked for, so --gauge could not reach
    #    the engine at all and the flat-400 pin would have been unreachable from
    #    a solve. The other half is the recentring immediately below.
    if gauge is not None:
        place_kw["gauge"] = gauge
    if indoor_centre is not None:
        place_kw["indoor_centre"] = float(indoor_centre)
    # ! THE SAME LESSON AS THE GAUGE, ONE LINE DOWN. An option the engine
    #   accepts but this call never forwards is an option that silently takes
    #   the default -- which is exactly how --gauge could not reach the engine.
    if indoor_mode is not None:
        place_kw["indoor_mode"] = indoor_mode
    if indoor_gate_mode is not None:
        place_kw["indoor_gate_mode"] = indoor_gate_mode
    if indoor_levels is not None:
        place_kw["indoor_levels"] = indoor_levels
    if gauge_scope is not None:
        place_kw["gauge_scope"] = gauge_scope
    # ! AND THE XC LEVEL, FORWARDED FOR THE SAME REASON AS THE GAUGE ABOVE.
    #   An option the engine accepts but this call never forwards is an
    #   option that silently takes the default -- which is exactly how
    #   --gauge could not reach the engine.
    if xc_level is not None:
        place_kw["xc_level"] = float(xc_level)
    if xc_level_mode is not None:
        place_kw["xc_level_mode"] = xc_level_mode
    if day_noise is not None:
        place_kw["day_noise"] = day_noise
    # ★ A COURSE'S NEAR DISTANCES SHARE ONE HISTORY (bracket_engine.
    #   SIBLING_DIST_TOL; Mt. SAC d4700/d4800). None keeps the engine's default, off.
    if sibling_tol is not None:
        place_kw["sibling_tol"] = float(sibling_tol)
    f = be.fit(sub, npz_like, train=None, window=window, top=top, codes=codes, z=z,
               h_row=h, verbose=verbose, prior_group=be.parsePrior(prior_group), **place_kw)
    D_b = np.asarray(f["D"], dtype=np.float64).copy()
    # recentre as recentreLevels centres d: the outdoor cells' unweighted
    # mean per sport is the zero; indoor cells keep their level
    g = np.asarray(D.group_of_cell)
    is_indoor = np.array([k.split("@", 1)[0].endswith(":in") for k in cell_keys], dtype=bool)
    # ⚠⚠ AND THIS IS THE OTHER HALF OF THE INDOOR CONTRADICTION. The engine has
    #    ALREADY set the zero -- that is what its gauge does -- and this loop
    #    then subtracted the outdoor cells' unweighted mean from every cell
    #    again. Applied twice it is nearly harmless for outdoor, but for indoor
    #    it is not: whatever level the engine's asserted centre put indoor at,
    #    this shift moved it by a second, differently-weighted amount, so
    #    XCP_INDOOR_LEVEL=0.003 could assert +0.3% all it liked and the
    #    published indoor cells came out at -1.68%. Under a gauge the engine
    #    itself enforces, re-centring is not a correction, it is a second
    #    opinion -- and under gauge=flat400 it would move the cells that were
    #    just pinned to 0.0 exactly, undoing the pin.
    skip_recentre = str(f.get("gauge") or "") == "flat400"
    shift_cell = np.zeros(D.n_cell)
    if skip_recentre:
        print("[joint] bracket: gauge=flat400 set the zero (flat outdoor 400s "
              "at 0.0 exactly), so the outdoor-mean recentring is SKIPPED -- "
              "it would move the cells that were just pinned", flush=True)
    else:
        for gg in range(int(g.max()) + 1):
            m = (g == gg) & ~is_indoor
            if not m.any():
                m = g == gg
            if m.any():
                shift_cell[g == gg] = float(D_b[m].mean())
    D_b = D_b - shift_cell
    # the track level by host population (see trackPopulationShift)
    #
    # ⚠⚠ AND IT IS THE SAME SECOND OPINION skip_recentre REFUSES, ONE FUNCTION
    #    LOWER DOWN (2026-09-20, owner: "track difficulty is not set to 0 for
    #    all outdoor 400m tracks"). trackPopulationShift takes the MEAN of each
    #    host population's outdoor cells and subtracts it from every cell of
    #    that population -- which includes the flat outdoor 400s the engine has
    #    just held at 0.0 EXACTLY, and the indoor cells whose mean it has just
    #    asserted at +0.3%. A cell pinned to zero then publishes at minus that
    #    population's mean, so the engine's own census line ("held at 0.0
    #    exactly") was true of the fit and false of the table.
    #
    #    That is the second half of the owner's complaint, and it is why
    #    widening the reference class alone would not have fixed it: even the
    #    cells that WERE in the class were moved afterwards.
    #
    # ★ SO UNDER gauge=flat400 THE ZERO IS ALREADY SET, AND RE-CENTRING ANY
    #   SUBSET OF IT IS NOT A CORRECTION. The gauge is an identity statement --
    #   a flat outdoor 400 IS 0.0 -- and no later shift may contradict it. This
    #   is the identical argument skip_recentre makes thirty lines above, and
    #   it is applied here by the identical test.
    #
    # ! FORCEABLE, because the per-population level it removes may be real and
    #   is a separate question from the gauge: XCP_TRACK_LEVEL_BY_POOL=force
    #   applies it anyway and says so. Plain 1 (the default) no longer beats
    #   the gauge.
    pop_rows = []
    # ! "0" IS A NON-EMPTY STRING AND THEREFORE TRUTHY. The flag arrives as
    #   text now (see --track-level-by-pool), so "off" is decided explicitly
    #   rather than by Python's idea of truth -- otherwise
    #   --track-level-by-pool 0 would silently turn the shift ON.
    tlbp = str(track_level_by_pool).strip().lower()
    force_pop = tlbp == "force"
    want_pop = tlbp not in ("0", "false", "none", "")
    track_level_by_pool = want_pop
    if skip_recentre and want_pop and not force_pop:
        print("[joint] bracket: gauge=flat400 pinned the reference cells at "
              "0.0 and asserted indoor's centre, so the track-level-by-"
              "population shift is SKIPPED -- it would move both. "
              "XCP_TRACK_LEVEL_BY_POOL=force applies it anyway.", flush=True)
        track_level_by_pool = 0
    elif force_pop and skip_recentre:
        print("[joint] bracket: ⚠ XCP_TRACK_LEVEL_BY_POOL=force -- the "
              "population shift runs and WILL move the cells gauge=flat400 "
              "pinned at 0.0. The published zero is then not the gauge's.",
              flush=True)
    if track_level_by_pool and athlete_pool is not None:
        names = [str(n) for n in pool_names]
        level_of_pool = np.array([n.split("_", 1)[0] for n in names], dtype=object)
        level_row = level_of_pool[np.asarray(athlete_pool)[np.asarray(D.athlete)]]
        mc_row = (np.asarray(sub["meet_class"]) if "meet_class" in sub
                  and np.asarray(sub["meet_class"]).size == D.n else None)
        pop_shift, pop_rows = trackPopulationShift(D_b, cell_keys, D.cell, level_row, mc_row)
        D_b = D_b - pop_shift
        shift_cell = shift_cell + pop_shift
    # ★ THE COURSE SCALE PER SPORT (owner, 2026-09-15). Run 23's tilt-by-band
    #   table: XC's implied multiplier sat a tenth above the applied one in
    #   EVERY band while TF's matched within 1% -- the voters' own brackets
    #   saying an XC course costs them a tenth more than the engine charges.
    #   The fitted prior shrinks a sport's courses toward its average, and
    #   this is what that shrinkage looks like from the athletes' side. So
    #   each sport's course effects are multiplied by the scale its bands
    #   agree on (bracket_engine.courseScaleFromBands; TF comes out at 1.0
    #   by its own table), AFTER the recentring so the zero stays a track.
    #   'fit' measures it, 'off' leaves 1.0, 'XC=1.1,TF=1' states it. The
    #   tilt-by-races table says whether the ratio falls with races per
    #   cell (the prior) or is flat (the scale): read it before trusting
    #   the number, and the tilt-by-band table after the scale is applied
    #   is the acceptance test -- implied should equal applied.
    #   2026-09-28: 'fit' now reads the scale from the courses with 4+ races
    #   only (bracket_engine.WELL_KNOWN_RACES says why); 'bands' is the old.
    scale_sport = courseScales(course_scale, f.get("tilt_bands"), f.get("tilt_races"))
    cell_is_tf = np.array([1 if str(k).startswith("TF:") else 0 for k in cell_keys], dtype=np.int8)
    scale_cell = np.where(cell_is_tf == 1, scale_sport[1], scale_sport[0])
    D_b = D_b * scale_cell
    delta_b = mu_full[g] + D_b
    # abilities given the new courses: the solve's own weighted means
    w = np.asarray(out["weights"], dtype=np.float64)
    resid = z - h * (D_b[D.cell] + u_row)                  # what is left for the ability
    den = np.bincount(D.athlete, weights=w, minlength=D.n_ath)
    # ! UNDER THE SEASON TIE THE ABILITY BLOCK HAS A PENALTY (2026-09-29), so
    #   "the solve's own weighted mean" is no longer the solve's ability: a
    #   plain mean here would publish the untied abilities under the bracket
    #   courses and the switch would never reach a board. tiedAbilities is
    #   the same block with the tie in it, and exactly the mean without one.
    a_new = js.tiedAbilities(den, np.bincount(D.athlete, weights=w * resid,
                                              minlength=D.n_ath),
                             D, out.get("tie_w"), out.get("tie_mean"), b["a"])
    # ★ THE DAY TERMS, REFITTED AGAINST THE COURSES THAT PUBLISH (owner,
    #   2026-10-04: "I wonder if some of the courses are having their race
    #   day tilt being the same for every race and not being put into course
    #   difficulty"). u was fitted by the joint solve against ITS course
    #   numbers and kept unchanged when the bracket engine replaced them, so
    #   wherever the two engines disagree about a course the gap went into no
    #   term at all -- straight into the ratings -- while the day terms
    #   described days at a course number nobody publishes. Now u is the
    #   solve's own shrunk estimate (u_j = sum c_i r_i / P_j, the prior from
    #   the solve's sigma2 and sigma_u2) re-taken against the published
    #   courses, alternating with the abilities. dayLeanReport prints the
    #   courses whose days all lean one way: a course number the runners
    #   disagree with.
    pen = None
    if b["u"].size == D.n_race and out.get("sigma_u2") is not None:
        s_u2 = np.asarray(out["sigma_u2"], dtype=np.float64)
        if s_u2.ndim == 0:
            s_u2 = np.full(int(D.group_of_cell.max()) + 1, float(s_u2))
        pen = float(out["sigma2"]) / np.maximum(s_u2[js.groupOfRace(D)], 1e-12)
        c = w * h
        P = np.bincount(D.race, weights=w * h * h, minlength=D.n_race) + pen
        u_new = np.asarray(b["u"], dtype=np.float64).copy()
        for _ in range(4):
            r_pre = z - a_new[D.athlete] - h * D_b[D.cell]
            u_new = np.bincount(D.race, weights=c * r_pre, minlength=D.n_race) / P
            resid = z - h * (D_b[D.cell] + u_new[D.race])
            a_new = js.tiedAbilities(den, np.bincount(D.athlete, weights=w * resid,
                                                      minlength=D.n_ath),
                                     D, out.get("tie_w"), out.get("tie_mean"), b["a"])
        # each runner's day without themselves, in the same frame: u_new is
        # the day the abilities were taken against; the row moves it by the
        # exact drop-one difference (as js.raceEffectLeaveOneOut does)
        r_pre = z - a_new[D.athlete] - h * D_b[D.cell]
        num = np.bincount(D.race, weights=c * r_pre, minlength=D.n_race)
        u_ref = num / P
        u_loo = (num[D.race] - c * r_pre) / (P[D.race] - w * h * h)
        out["race_effect_row_bracket"] = u_new[D.race] + (u_loo - u_ref[D.race])
        # ★ THE SEASON'S LEVEL OUT OF THE DAYS, INTO THE ABILITIES (see
        #   centreDaysBySeason). Every fitted value stays where it was --
        #   h*shift leaves the day and the same amount, row-weighted, joins
        #   the ability -- only the split between "the athlete" and "the day"
        #   changes, and with it what the ratings carry. XCP_DAY_CENTRE=0
        #   publishes the uncentred terms (the 2026-10-04 behaviour).
        if os.environ.get("XCP_DAY_CENTRE", "1") != "0":
            season_row = np.asarray(cols["year"])[keep]
            sport_row = np.array([1 if str(cell_keys[c]).startswith("TF:") else 0
                                  for c in range(len(cell_keys))],
                                 dtype=np.int64)[D.cell]
            u_new, shift_row = centreDaysBySeason(u_new, D, season_row, sport_row)
            out["race_effect_row_bracket"] = out["race_effect_row_bracket"] - shift_row
            a_new = a_new + (np.bincount(D.athlete, weights=w * h * shift_row,
                                         minlength=D.n_ath) / np.maximum(den, 1e-12))
            xc = sport_row == 0
            print(f"[joint] bracket: day terms centred per (sport, season) -- the "
                  f"seasons' level moved to the abilities: XC "
                  f"{100 * float(np.average(shift_row[xc])) if xc.any() else 0.0:+.2f}%, "
                  f"TF {100 * float(np.average(shift_row[~xc])) if (~xc).any() else 0.0:+.2f}% "
                  f"(row-weighted); per season, XC: "
                  + ", ".join(f"{int(yy)} {100 * float(shift_row[xc & (season_row == yy)].mean()):+.1f}%"
                              for yy in sorted(set(season_row[xc].tolist()))[-6:]),
                  flush=True)
        moved = np.abs(u_new - np.asarray(b["u"], dtype=np.float64))
        print(f"[joint] bracket: day terms refitted against the published courses "
              f"-- median move {100 * float(np.median(moved)):.2f}%, p95 "
              f"{100 * float(np.percentile(moved, 95)):.2f}%", flush=True)
        out["race_effect_joint"] = np.asarray(b["u"], dtype=np.float64).copy()
        out["race_effect"] = u_new
        for line in dayLeanReport(u_new, D, cell_keys):
            print(line, flush=True)
    gauge = np.asarray(out["ability"], dtype=np.float64) - b["a"]   # the curve's gauge shift
    delta_joint = np.asarray(out["delta"], dtype=np.float64).copy()
    a_joint = b["a"].copy()
    out["delta_joint"] = delta_joint
    out["delta"] = delta_b
    out["d"] = D_b - (out["indoor_cell"] if out.get("indoor_cell") is not None else 0.0)
    out["ability"] = a_new + gauge
    if "ability_raw" in out:
        out["ability_raw"] = a_new
    if athlete_pool is not None:
        out["rating"] = js.ratingsFromAbility(a_new, np.asarray(athlete_pool),
                                              out["n_races"], len(pool_names))
    sig_u2 = np.atleast_1d(np.asarray(out["sigma_u2"], dtype=np.float64))
    su2 = sig_u2[g] if sig_u2.size == int(g.max()) + 1 else np.full(D.n_cell, float(sig_u2.mean()))
    votes = np.asarray(f["votes"], dtype=np.float64)
    k_cell = np.asarray(f["prior_group"], dtype=np.float64)[np.asarray(f["cell_prior_group"])]
    out["cell_var"] = su2 / (votes + k_cell)
    out["bracket_prior_group"] = np.asarray(f["prior_group"], dtype=np.float64)
    out["cell_se"] = np.sqrt(out["cell_var"])
    out["bracket_votes"] = votes
    out["bracket_races_per_cell"] = np.asarray(f["races_per_cell"])
    # ★ THE ENGINE'S ARITHMETIC, KEPT (owner, 2026-09-13: "it kind of seems
    #   like we made diagnostics that capture course difficulty and then we
    #   aren't using it"). Per cell: the era's own vote-mean of its races,
    #   the course's history after the group prior, the history's votes,
    #   and the recentring taken off -- so scripts/course_bracket.py can
    #   print, race by race and step by step, how a venue's bracket became
    #   its published number (Foot Locker against Glendoveer).
    b_of = np.asarray(f["base_of_cell"], dtype=np.int64)
    out["bracket_cell_raw"] = np.asarray(f["D_cell_raw"], dtype=np.float64)
    out["bracket_base"] = np.asarray(f["D_base"], dtype=np.float64)[b_of]
    out["bracket_base_votes"] = np.asarray(f["base_votes"], dtype=np.float64)[b_of]
    out["bracket_place"] = np.asarray(f["place_of_base"], dtype=np.int64)[b_of]
    out["bracket_pin"] = np.asarray(f["pin"], dtype=np.float64)
    out["bracket_shift"] = shift_cell
    out["bracket_scale"] = scale_cell
    out["bracket_course_scale"] = np.array([float(scale_sport[0]), float(scale_sport[1])])
    # the band table as numbers, for the acceptance check (board_sanity):
    # rows of (sport code, band lower edge, voters, applied h, implied h, se)
    out["bracket_tilt_bands"] = be.tiltBandArray(f.get("tilt_bands"))
    # ★ AND ON THE WELL-KNOWN COURSES ONLY (2026-10-03): the scale is fitted
    #   on courses with WELL_KNOWN_RACES+ races (courseScaleFromRaces), so
    #   that is the table the acceptance check must hold it to -- on every
    #   course, the prior's in-sample signature on thin courses reads
    #   implied/applied 1.10-1.16 by construction and failed the run
    out["bracket_tilt_bands_known"] = be.tiltBandArray(f.get("tilt_bands_known"))
    out["bracket_cell_fit"] = np.asarray(f["D_fit"], dtype=np.float64)
    out["bracket_level_shift"] = np.asarray(f["level_shift"], dtype=np.float64)
    out["difficulty_source"] = "bracket"
    # the report: what moved
    solved = np.bincount(D.cell, minlength=D.n_cell) > 0
    no_votes = solved & (votes <= 0)
    m = solved & (votes > 0)
    corr = float(np.corrcoef(delta_b[m], delta_joint[m])[0, 1]) if m.sum() > 2 else np.nan
    move = delta_b[m] - delta_joint[m]
    info = dict(seconds=time.time() - t0, n_cells=int(solved.sum()), n_voted=int(m.sum()),
                n_no_votes=int(no_votes.sum()), corr=corr,
                median_move=float(np.median(np.abs(move))) if m.any() else np.nan,
                p95_move=float(np.percentile(np.abs(move), 95)) if m.any() else np.nan,
                ability_corr=float(np.corrcoef(a_new, a_joint)[0, 1]),
                ability_shift=float(np.median(np.abs(a_new - a_joint))))
    for ln in f.get("prior_lines", []):
        print(f"[joint] bracket prior: {ln}")
    if pop_rows:
        print("[joint] track level by host population (the level of the majority of a "
              "cell's rows; each population's outdoor tracks recentred to the same "
              "zero, --track-level-by-pool 0 leaves them):")
        print(f"        {'population':<11}{'outdoor':>8}{'indoor':>7}{'mean before':>12}"
              f"{'sd':>7}{'champ share':>12}{'shift':>8}")
        for r in pop_rows:
            cs = f"{100 * r['champ_share']:.0f}%" if np.isfinite(r["champ_share"]) else "-"
            print(f"        {r['population']:<11}{r['cells']:>8,}{r['indoor']:>7,}"
                  f"{100 * r['mean']:>+11.2f}%{100 * r['sd']:>6.2f}%{cs:>12}"
                  f"{(100 * r['shift']):>+7.2f}%{'' if r['applied'] else '  (too few cells, left)'}")
        out["bracket_population_shift"] = np.array(
            [[float(r["mean"]), float(r["shift"]), float(r["cells"])] for r in pop_rows])
        out["bracket_population_names"] = np.array([r["population"] for r in pop_rows])
    tl = be.tiltLines(f.get("tilt_bands"))
    if tl:
        print("[joint] bracket tilt by band, BEFORE the course scale: the course "
              "multiplier the voters' own brackets imply against the one applied (a "
              "hard venue read by a band whose implied h is below its applied h is "
              "overstated by the ratio):")
        for ln in tl:
            print("        " + ln)
    tk = be.tiltLines(f.get("tilt_bands_known"))
    if tk:
        print(f"[joint] bracket tilt by band, courses with {be.WELL_KNOWN_RACES}+ races "
              "only, before the course scale (the prior barely touches these, so this "
              "is the tilt's own test: implied/applied the same in every band = the "
              "slope is right):")
        for ln in tk:
            print("        " + ln)
    tr = be.tiltRaceLines(f.get("tilt_races"))
    if tr:
        print("[joint] bracket tilt by races per cell, before the course scale:")
        for ln in tr:
            print("        " + ln)
    print(f"[joint] course scale per sport (--course-scale {course_scale}): "
          f"XC x{scale_sport[0]:.3f}, TF x{scale_sport[1]:.3f} -- every course effect of "
          f"the sport multiplied after the recentring; implied/applied should read 1.0 "
          f"in every band on the next run")
    print(f"[joint] difficulty = BRACKET ENGINE (--difficulty bracket): {info['n_voted']:,} of "
          f"{info['n_cells']:,} cells with votes in {info['seconds']:.0f}s; {info['n_no_votes']:,} "
          f"cells without a race of 3+ voters sit at their sport's average. Against the "
          f"joint solve's courses: corr {corr:.3f}, median |move| {100 * info['median_move']:.2f}%, "
          f"p95 {100 * info['p95_move']:.2f}%. Abilities recomputed: corr "
          f"{info['ability_corr']:.4f}, median |shift| {100 * info['ability_shift']:.2f}%.")
    races = np.asarray(f["races_per_cell"])
    for lo, hi, lab in ((1, 1, "1"), (2, 3, "2-3"), (4, 9, "4-9"), (10, 10**9, "10+")):
        mm = m & (races >= lo) & (races <= hi)
        if mm.sum() >= 10:
            print(f"        courses on {lab:>4} races: {int(mm.sum()):>8,}   "
                  f"median |move| {100 * float(np.median(np.abs(delta_b[mm] - delta_joint[mm]))):.2f}%   "
                  f"sd of the number {100 * float(np.std(delta_b[mm])):.2f}% (joint {100 * float(np.std(delta_joint[mm])):.2f}%)")
    return info


# ------------------------------------------------------------------ #
# THE SOLVE'S STATE ON DISK (2026-09-13: "so I don't have to wait hours")
# ------------------------------------------------------------------ #
#
# ★ THE SOLVE IS THE HOURS; EVERYTHING AFTER IT IS MINUTES. Twice in two
#   days a report after the solve died and took 2.6 hours with it. Now the
#   solve's whole output is written to <out>_state.npz the moment it
#   exists, and `--from-state <file>` re-enters main() there: the design
#   is rebuilt from the pack (minutes), the state is loaded in place of
#   solveJoint, and the swap, the reports, the save and the go-live run
#   as usual. A bracket-engine knob, a go-live fix, or a crash after the
#   solve costs minutes, not a re-solve. The state must come from the same
#   pack, flags and sample; the sizes are checked before anything runs.
_STATE_F32 = ("weights", "h", "amp")          # per row; float32 keeps 1e-7 of them


def saveState(out, path):
    import json
    arrays, meta = {}, {}
    for k, v in out.items():
        if v is None:
            meta[k] = None
        elif isinstance(v, np.ndarray):
            arrays[k] = (v.astype(np.float32) if k in _STATE_F32 and v.dtype == np.float64
                         else v)
        elif isinstance(v, np.generic):
            meta[k] = v.item()
        elif isinstance(v, (bool, int, float, str)):
            meta[k] = v
        elif isinstance(v, (list, tuple)):
            try:
                arrays[k] = np.asarray(v)
            except Exception:                                    # noqa: BLE001
                meta[k] = list(v)
        else:
            meta[k] = str(v)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    np.savez(path, __meta__=np.array([json.dumps(meta)]), **arrays)
    return path


def loadState(path):
    import json
    with np.load(path, allow_pickle=False) as d:
        out = {k: d[k] for k in d.files if k != "__meta__"}
        meta = json.loads(str(d["__meta__"][0])) if "__meta__" in d.files else {}
    for k in _STATE_F32:
        if k in out:
            out[k] = out[k].astype(np.float64)
    for k, v in out.items():
        if isinstance(v, np.ndarray) and v.ndim == 0:
            out[k] = v.item() if v.dtype.kind in "biuf" else v
    out.update(meta)
    return out


def checkState(out, D):
    """The state must be this design's: same rows, athlete-seasons, cells."""
    h = np.asarray(out.get("h"))
    n_rows = h.size if h.ndim else None
    n_ath = np.asarray(out["ability"]).size
    n_cell = np.asarray(out["delta"]).size
    problems = []
    if n_rows not in (None, 1) and n_rows != D.n:
        problems.append(f"{n_rows:,} rows in the state, {D.n:,} in this design")
    if n_ath != D.n_ath:
        problems.append(f"{n_ath:,} athlete-seasons in the state, {D.n_ath:,} here")
    if n_cell != D.n_cell:
        problems.append(f"{n_cell:,} cells in the state, {D.n_cell:,} here")
    if problems:
        raise SystemExit("[joint] --from-state does not match this pack, sample and flags: "
                         + "; ".join(problems) + ". Use the same --sample-pct, --sample-seed, "
                         "--era-years and term flags the solve ran with.")


def guardedReport(name, fn):
    """Run a report; a failure is printed, never raised (a solve is hours)."""
    import traceback
    try:
        fn()
        return True
    except Exception:                                            # noqa: BLE001
        print(f"[joint] report '{name}' FAILED (the solve is unaffected):")
        traceback.print_exc()
        return False


def pairEngineGap(old_path, solved, keys):
    """The sequential engine's TF-minus-XC level, for the level report;
    None when its file is absent, keyed differently, or one-sided.

    ! keys ARE THE DESIGN'S CELL KEYS, one per (course, era) under
      --era-years, the same length as `solved`. The pack's base keys are
      a third as many and broadcast against `solved` killed run 21's
      go-live (2026-09-13)."""
    if not os.path.exists(old_path):
        return None
    try:
        with np.load(old_path, allow_pickle=False) as old:
            if "difficulty_raw" not in old.files:
                return None
            old_delta = np.log1p(old["difficulty_raw"])
        solved = np.asarray(solved, dtype=bool)
        if old_delta.size != solved.size or len(keys) != solved.size:
            print(f"[joint] vs pair_difficulty: skipped, {old_delta.size:,} cells in the "
                  f"old file, {solved.size:,} solved cells, {len(keys):,} keys")
            return None
        is_xc = np.array([str(k).startswith("XC:") for k in keys])
        is_tf = np.array([str(k).startswith("TF:") for k in keys])
        ok = np.isfinite(old_delta) & (old_delta != 0) & solved
        if (ok & is_xc).any() and (ok & is_tf).any():
            return float(old_delta[ok & is_tf].mean() - old_delta[ok & is_xc].mean())
    except Exception as exc:                                     # noqa: BLE001
        print(f"[joint] vs pair_difficulty: skipped ({type(exc).__name__}: {exc})")
    return None


def pairEngineDelta(old_path, solved):
    """The sequential engine's per-cell log difficulty, aligned to `solved`,
    or None.

    ⚠ WHY THIS EXISTS. pairEngineGap loads this array and returns only the
      scalar gap, but the end-of-run comparison needs the array too -- and
      after that refactor it was still reading an `old_delta` local that no
      longer existed anywhere. NameError at the END of a 2.6-hour solve,
      which is precisely what "a report must never kill a solve" was
      written to stop. Both callers load through here now.
    """
    if not os.path.exists(old_path):
        return None
    try:
        with np.load(old_path, allow_pickle=False) as old:
            if "difficulty_raw" not in old.files:
                return None
            delta = np.log1p(old["difficulty_raw"])
        return delta if delta.size == np.asarray(solved).size else None
    except Exception as exc:                                     # noqa: BLE001
        print(f"[joint] vs pair_difficulty: skipped ({type(exc).__name__}: {exc})")
        return None


def main():
    ap = buildParser()
    args = applyImplications(ap.parse_args(), ap)

    if not os.path.exists(args.pack):
        sys.exit(f"[joint] no pack at {args.pack} -- run 07_pack first")

    print(f"[joint] loading {args.pack}")
    cols = pe.loadPack(args.pack)
    cols = sortRowsByAthlete(cols)
    # ! THE FORWARD WINDOW IS RESOLVED BEFORE ANY DESIGN IS BUILT, so a run
    #   aimed at the sealed season without --sealed stops in seconds, not
    #   after the full design (forward_holdout.resolveWindow)
    if (args.holdout or args.holdout_only) and args.holdout_kind == "forward":
        args._forward = fh.forwardWindow(cols, args.holdout_from,
                                         args.holdout_until, args.sealed)
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
    D, athlete_pool, pool_names = buildDesign(cols, keep, **designKwargs(args))
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
          f"tilt {'off' if args.no_tilt else 'ON (' + ('HS-equivalent' if args.tilt_scale == 'hs' else 'own') + ' ability)'}, "
          f"season tie {'ON' if getattr(D, 'n_tie', 0) else 'off'}, "
          f"robust {'off' if args.no_robust else 'ON'}")
    if args.tilt_scale == "hs" and not args.no_tilt:
        args._tilt_scale_inputs = tiltScaleInputs(pool_names)

    if args.holdout or args.holdout_only:
        holdout(cols, keep, args, athlete_pool, D)
    if args.holdout_only:
        print("[joint] --holdout-only: no full solve, nothing written")
        return

    t0 = time.time()
    state_path = os.path.splitext(args.out)[0] + "_state.npz"
    if args.from_state:
        out = loadState(args.from_state)
        checkState(out, D)
        print(f"[joint] --from-state {args.from_state}: the solve's output loaded "
              f"({np.asarray(out['delta']).size:,} cells), no solve run")
    else:
        out = js.solveJoint(y, design=D, n_probe=args.probes,
                            **solveKwargs(args, athlete_pool, verbose=True))
        print(f"[joint] solved in {time.time() - t0:.0f}s")
        try:
            saveState(out, state_path)
            print(f"[joint] solve state -> {state_path} (rerun the rest with "
                  f"--from-state {state_path}: minutes, not a solve)")
        except Exception as exc:                                 # noqa: BLE001
            print(f"[joint] solve state NOT saved ({type(exc).__name__}: {exc}); carrying on")
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

    if getattr(args, "difficulty", "joint") == "bracket":
        try:
            bracketDifficulties(out, D, cols, keep, y, athlete_pool, pool_names,
                                window=args.bracket_window, top=args.bracket_top,
                                prior_group=getattr(args, "bracket_prior", "fit"),
                                track_level_by_pool=getattr(args, "track_level_by_pool", "1"),
                                place_radius=getattr(args, "bracket_place_radius", None),
                                prior_place=getattr(args, "bracket_place_prior", None),
                                place_venue=getattr(args, "bracket_place_venue", None),
                                course_scale=getattr(args, "course_scale", "fit"),
                                gauge=getattr(args, "gauge", None),
                                indoor_centre=getattr(args, "bracket_indoor_centre",
                                                      None),
                                indoor_mode=getattr(args, "bracket_indoor_mode",
                                                    None),
                                indoor_gate_mode=getattr(
                                    args, "bracket_indoor_gates", None),
                                indoor_levels=getattr(args, "bracket_indoor_levels", None),
                                gauge_scope=getattr(args, "gauge_scope", None),
                                xc_level=getattr(args, "bracket_xc_level", None),
                                xc_level_mode=getattr(args, "bracket_xc_level_mode", None),
                                day_noise=getattr(args, "day_noise", None),
                                sibling_tol=getattr(args, "bracket_sibling_tol", None))
        except Exception:                                        # noqa: BLE001
            import traceback
            traceback.print_exc()
            out["difficulty_source"] = "joint (bracket swap FAILED)"
            print(f"[joint] ⚠⚠ THE BRACKET SWAP FAILED (above). The JOINT solve's courses "
                  f"are published so the run completes; the solve is saved in "
                  f"{state_path}. Fix, then rerun with --from-state {state_path} "
                  f"--difficulty bracket --golive: minutes, no solve.")
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
    old_gap = pairEngineGap(os.path.join(os.path.dirname(args.out), "pair_difficulty.npz"),
                            solved, [str(k) for k in (getattr(D, "course_keys", None)
                                                      or cols["course_keys"])])
    # ! A REPORT MUST NEVER KILL A SOLVE (run 20: the go-live's keys; run
    #   21: this report's keys, 2.6 hours each). Every report runs guarded;
    #   one that fails prints its traceback and the save still happens.
    for _name, _fn in (("level and curve", lambda: reportLevelAndCurve(out, D, pool_names, old_gap)),
                       ("shared terms", lambda: reportSharedTerms(out, D, pool_names)),
                       ("tilt by band", lambda: reportTiltByBand(out, D, y)),
                       ("season tie", lambda: reportSeasonTie(out, D)),
                       ("tilt scale", lambda: reportTiltScale(out, pool_names)),
                       ("field by band", lambda: reportFieldByBand(out, D, y))):
        guardedReport(_name, _fn)

    save = dict(delta=delta, delta_anchored=anchored, mu=out["mu"],
                cell_se=out["cell_se"], cell_var=out["cell_var"],
                race_effect=out["race_effect"], sigma2=out["sigma2"],
                sigma_u2=out["sigma_u2"], tau2=out["tau2"],
                rows_per_cell=rows_per_cell,
                ability=out["ability"].astype(np.float32),
                athlete_pool=athlete_pool.astype(np.int16),
                n_races=out["n_races"].astype(np.int32),
                pool_names=np.array(pool_names))
    # the design's keys (one per (course, era) cell under --era-years), the
    # era width and base year, split ability: see designRecord
    save.update(designRecord(args, cols, D))
    save["difficulty_source"] = np.array([str(out.get("difficulty_source") or "joint")])
    if out.get("delta_joint") is not None:
        save["delta_joint"] = out["delta_joint"]
        save["bracket_votes"] = out["bracket_votes"]
        save["bracket_races_per_cell"] = out["bracket_races_per_cell"]
        import bracket_engine as be
        for k in ("bracket_cell_raw", "bracket_base", "bracket_base_votes",
                  "bracket_pin", "bracket_shift", "bracket_cell_fit",
                  "bracket_level_shift",
                  "bracket_prior_group", "bracket_scale", "bracket_course_scale",
                  "bracket_tilt_bands", "bracket_tilt_bands_known"):
            if out.get(k) is not None:
                save[k] = np.asarray(out[k], dtype=np.float64)
        if out.get("bracket_place") is not None:
            save["bracket_place"] = np.asarray(out["bracket_place"], dtype=np.int64)
        save["bracket_prior_group_names"] = np.array(list(be.PRIOR_GROUP_NAMES))
        save["bracket_prior_races"] = np.array([float(be.PRIOR_RACES)])
        save["bracket_race_sat"] = np.array([float(be.RACE_SAT)])
    if out.get("rating") is not None:
        save["rating"] = out["rating"].astype(np.float32)
    # the 2026-09-29 switches, so a reader (bracket_engine, the scorecard)
    # knows what this file's ratings and tilt were made with
    if out.get("tilt_pool_factor") is not None:
        save["tilt_pool_factor"] = np.asarray(out["tilt_pool_factor"], dtype=np.float64)
    if out.get("tie_w") is not None:
        save["season_tie_lines"] = np.array(list(out.get("season_tie_lines") or []))
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
    if out.get("mu_fixed") is not None:
        save["mu_fixed"] = out["mu_fixed"]
    if out.get("importance") is not None and getattr(D, "n_imp", 0):
        save["importance"] = out["importance"]
        save["importance_labels"] = np.array(getattr(D, "imp_labels", []))
        save["importance_kind"] = np.array([str(out.get("importance_kind"))])
        if out.get("field_centre") is not None:
            save["field_centre"] = np.asarray(out["field_centre"], dtype=np.float64)
    if out.get("indoor") is not None and out.get("indoor_cell") is not None:
        save["indoor"] = np.asarray(out["indoor"], dtype=np.float64)
        save["indoor_cell"] = out["indoor_cell"]
        save["indoor_fixed"] = np.array([bool(out.get("indoor_fixed"))])
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
        # ★ EACH RUNNER'S DAY WITHOUT THEMSELVES (owner, 2026-10-01): when
        #   the day term reaches a rating, the rating gets the day as the
        #   rest of the field ran it (js.raceEffectLeaveOneOut)
        if (args.race_effect_sports.strip() and args.race_effect_own == "leave-out"
                and out.get("race_effect_row_bracket") is not None):
            # refitted against the published courses (bracketDifficulties)
            out["race_effect_row"] = out["race_effect_row_bracket"]
            print("[joint/live] race-day term: each rating carries its day "
                  "without its own row, refitted against the published courses")
        elif args.race_effect_sports.strip() and args.race_effect_own == "leave-out":
            u_loo, info = js.raceEffectLeaveOneOut(out, D, y)
            if u_loo is None:
                print(f"[joint/live] race-day term: leave-self-out NOT applied "
                      f"({info}); ratings carry the race's own day")
            else:
                out["race_effect_row"] = u_loo
                print(f"[joint/live] race-day term: each rating carries its day "
                      f"without its own row -- the runner's own pull, median "
                      f"{100 * info['own_pull_median']:.3f}%, p99 "
                      f"{100 * info['own_pull_p99']:.2f}% (conditional check "
                      f"{info['max_conditional_gap']:.1e})")
        live = jg.buildLive(out, D, cols, keep, collapse=args.collapse,
                            anchor=args.anchor,
                            use_race_effect=not args.no_race_effect,
                            gain_bands=gain_bands, pack_date=pack_date,
                            gain_levels=parseLevelGains(getattr(args, "sport_level_pools", None)),
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
    def _vs_pair():
        old_delta = pairEngineDelta(
            os.path.join(os.path.dirname(args.out), "pair_difficulty.npz"), solved)
        if old_delta is None:
            return
        m = np.isfinite(old_delta) & (old_delta != 0) & solved
        if m.sum() > 100:
            d = anchored[m] - old_delta[m]
            print(f"\n[joint] vs pair_difficulty over {int(m.sum()):,} cells: "
                  f"median |move| {np.median(np.abs(d)):.4f}, p95 "
                  f"{np.percentile(np.abs(d), 95):.4f}, corr "
                  f"{np.corrcoef(anchored[m], old_delta[m])[0, 1]:.4f}")
    guardedReport("vs pair_difficulty", _vs_pair)     # after the go-live: a report, guarded


if __name__ == "__main__":
    main()
