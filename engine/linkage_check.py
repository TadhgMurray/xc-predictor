"""
pair_all.py -- the whole pair-engine chain in ONE process.

WHY THIS EXISTS
    Run separately, pair_engine / pair_validate / pair_ratings / pair_report /
    pair_write / pair_write_results / pair_golive each pay the same setup:

        loadPack            61.8M rows off disk        ~60s
        buildResponse       a lexsort over 61.8M rows  ~90s   (rust_fitness)
        solveSubset         296 CG iterations          ~6min

    Seven processes, seven setups. The solve is cached now, but the pack load
    and the form correction are not -- they happen before anything cacheable
    exists. That is ~17 minutes of repeated work before a single new number is
    computed.

★ THIS DOES ALL OF IT ON ONE SET OF ARRAYS. Load once, correct once, solve once,
  then every downstream step reads the same in-memory result. The functions
  themselves are imported from the original modules, not reimplemented, so there
  is still one definition of what a rating is.

★ AND IT SKIPS WHAT YOU ARE NOT USING. Two steps are off by default:
    --validate      split-half reliability + held-out A/B. THREE extra solves.
                    Diagnostics: worth running when something looks wrong, not
                    on every pass.
    --result-table  pair_result_rating, a 55M-row COPY whose only purpose is
                    comparing against results.speed_rating BEFORE a go-live. If
                    you are going live in the same run it is written and then
                    immediately superseded.

Nothing writes to the database unless you pass --write. Nothing touches
results.speed_rating unless you pass --golive.
"""

import os
import re
import sys
import time

# The repo's own modules live in engine/ and scripts/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, "scripts")
for _p in (_HERE, _ROOT,
           os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

import pair_engine as pe
import pair_ratings as pr
import pair_validate as pv


def _t(label, t0):
    print(f"[all] {label}: {time.time() - t0:.1f}s")
    return time.time()


# ------------------------------------------------------------------ #
# CHUNK 1 -- LOAD AND SOLVE, ONCE
# ------------------------------------------------------------------ #

def prepare(pack_path):
    """
    Everything downstream needs, computed exactly once.

    Returns a dict rather than a tuple because eight call sites take different
    subsets of it, and positional unpacking at that width is how the
    informativeMask argument swap happened.
    """
    t0 = time.time()
    cols = pe.loadPack(pack_path)
    t0 = _t("pack load", t0)

    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    y_all, _form = pe.buildResponse(cols)
    t0 = _t("form correction", t0)

    course = cols["course"][keep].astype(np.int64)
    group, n_groups = pe.athleteSeasonCodes(cols["athlete"][keep],
                                            cols["year"][keep])
    t0 = _t("athlete-season codes", t0)

    d = {"cols": cols, "keep": keep,
         "course": course, "group": group, "n_groups": n_groups,
         "y": y_all[keep], "norm": cols["norm"][keep],
         "athlete": cols["athlete"][keep], "year": cols["year"][keep],
         "n_cells": len(cols["course_keys"]),
         "keys": [str(k) for k in cols["course_keys"]]}
    for name in ("result_id", "sport"):
        if name in cols:
            d[name] = cols[name][keep]

    # ⚠ cell_days is indexed by CELL, not by row, so it must come through
    #   WITHOUT the `keep` mask. Masking it would silently reindex it against
    #   a row filter and shrinkByLinkage would then read one cell's day count
    #   as another's -- wrong difficulties, no error. It is deliberately not
    #   in the loop above for exactly that reason.
    #
    #   Absent (an older pack), shrinkByLinkage falls back to degree * 4, which
    #   is what made the null blind to big single-day fields.
    if "cell_days" in cols:
        d["cell_days"] = np.asarray(cols["cell_days"])
        n_one = int((d["cell_days"] == 1).sum())
        print(f"[all] cell_days present: {n_one:,} one-day cells")
    print(f"[all] {d['y'].size:,} rows, {n_groups:,} athlete-seasons, "
          f"{d['n_cells']:,} cells")
    return d


# ★ THE MEASURED ABILITY TILT. delta_eff = delta * (1 + K*(rating-100)/10).
#   Fitted over 51M rows in cells with degree >= 25; the tilt tracks difficulty
#   at corr = -0.993 across five difficulty quintiles. See ability_slope.py.
_TILT_K = -0.031


def abilityTilt(D, k=_TILT_K, lo=0.6, hi=1.5):
    """
    h per CELL, from the mean career rating of everyone who raced it.

    ★ IT USED TO BE PER ROW, AND THAT MADE A COURSE HARDER FOR ONE RUNNER
      THAN FOR THE PERSON BESIDE THEM.

          rating = D["rat"]["career"][D["group"]]   # <- per athlete

      A course's difficulty is a property of the course. Charging a different
      delta_eff to two athletes in the same race means the same race divides
      them by different constants, so the race stops sorting by time -- which
      is exactly what the boards show: at Gans Creek 2025, 18:38.9 rated 130.6
      and 18:52.6 rated 134.8.

      The tilt itself is real and stays: harder courses do cost faster runners
      proportionally less, measured at corr -0.993 across five difficulty
      quintiles. What was wrong was the GRAIN. It is now evaluated once per
      cell, on the mean career rating of that cell's field, and broadcast back
      to every row in it -- so it still varies by course, and no longer varies
      by who showed up.

    ⚠ CLAMPED, unchanged. The tilt was fitted over ratings ~70-140. A rating of
      200 would give h = -2.1 -- the course making an athlete FASTER the harder
      it is -- which is nonsense. The bounds are guard rails against
      extrapolation, not model features.

    ! VENUELESS ROWS (course < 0) GET h = 1, i.e. no tilt. They vote on no
      cell, so there is no cell mean to give them, and 1.0 leaves their delta
      untouched rather than inventing one.
    """
    rating = np.asarray(D["rat"]["career"][D["group"]], dtype=np.float64)
    rating = np.where(np.isfinite(rating), rating, 100.0)

    cells = np.asarray(D["course"])
    n_cells = int(D["n_cells"])
    real = cells >= 0

    # Mean career rating per cell, then broadcast back to that cell's rows.
    sums = np.bincount(cells[real], weights=rating[real], minlength=n_cells)
    cnts = np.bincount(cells[real], minlength=n_cells)
    cell_rating = np.divide(sums, np.maximum(cnts, 1),
                            out=np.full(n_cells, 100.0), where=cnts > 0)

    per_row = np.where(real, cell_rating[np.maximum(cells, 0)], 100.0)
    h = 1.0 + k * (per_row - 100.0) / 10.0
    h[~np.isfinite(h)] = 1.0
    return np.clip(h, lo, hi)


# ------------------------------------------------------------------ #
#  SPORT SPLIT WITH RECENTRING
# ------------------------------------------------------------------ #
#
#     y = alpha[athlete, season] + beta[athlete, season] * s + delta[cell]
#
#   s = +-0.5 for TF/XC, centred within the athlete-season, beta unpenalised.
#
# ★ WHY. Forcing one ability across both sports costs 2.47% held-out; TF alone
#   3.9%. A 4:05 miler who is mediocre on grass and a grass specialist with no
#   kick currently get the same number.
#
# ★ AND WHY IT NEEDS RECENTRING. The per-athlete sport offset and the XC-vs-TF
#   difficulty level are THE SAME PARAMETER. The operator's eigenvalue along the
#   "+1 on every TF cell, -1 on every XC cell" direction:
#
#       shared ability   90.81
#       ridge K = 5      60.46
#       ridge K = 1      26.75
#       full split        0.00
#
#   Every unit of freedom given to beta comes straight out of the level. A world
#   where every runner is 4% slower on grass and a world where grass is 4%
#   harder produce identical times.
#
#   So the mean offset is MOVED BACK into the difficulties:
#
#       beta -> beta - bbar ;  delta -> delta + bbar * s_cell
#
#   which imposes exactly one assumption -- that specialisation averages out
#   across athletes -- and nothing more. Measured on the corpus: bbar = -0.03924
#   from 2.07M dual-sport athlete-seasons, recovering a TF-XC gap of -0.0395
#   against the shared solve's -0.043, with held-out identical to six decimals.
#
# ⚠ WITHOUT recentring, minimum-norm CG leaves delta orthogonal to the sport
#   direction, which silently asserts XC and TF have EQUAL mean difficulty. That
#   is certainly false, so the split is not safe to ship un-recentred.


def sportOffset(D):
    """Centred sport indicator per row, or None when the pack has no sport."""
    if "sport" not in D:
        return None
    return pe.sportCentered(D["sport"], D["group"], D["n_groups"])


def recenterSport(D):
    """
    Move the mean sport offset out of beta and into delta. Predictions unchanged.
    """
    import pair_recenter as prc

    # ★ THE MEASURED CONSTANT APPLIES IN PRODUCTION. pair_recenter documents
    #   the loop (measure_sport_gap -> set MEASURED_BBAR -> iterate), and
    #   pair_all got the application on 2026-08-24 -- but the pipeline's
    #   step 08 runs THIS file, which never did. Ported 2026-08-27 so a
    #   pinned value cannot silently fail to reach the site. None (the
    #   default) keeps the solve's own estimate, exactly as before.
    delta, alpha, beta, bbar, n_ident = prc.recenter(
        D["delta"], D["alpha"], D["beta"], D["sc"], D["group"],
        D["sport"], D["course"], D["n_cells"], D["n_groups"],
        bbar=prc.MEASURED_BBAR)

    s_cell = prc.cellSport(D["course"], D["sport"], D["n_cells"])
    xc = D["solved"] & (s_cell < 0)
    tf = D["solved"] & (s_cell > 0)
    gap = float(delta[tf].mean() - delta[xc].mean())
    if prc.MEASURED_BBAR is not None:
        # The solve's own estimate still prints: its drift AWAY from the
        # measured constant is the telemetry that says when to re-measure.
        own, _ = prc.meanOffset(D["beta"], D["sc"], D["group"], D["n_groups"])
        print(f"[all] sport recentre: bbar {bbar:+.5f} MEASURED "
              f"(pair_recenter.MEASURED_BBAR; solve's own estimate {own:+.5f} "
              f"from {n_ident:,} dual-sport athlete-seasons)")
    else:
        print(f"[all] sport recentre: bbar {bbar:+.5f} from {n_ident:,} "
              f"dual-sport athlete-seasons")
    print(f"[all] TF - XC difficulty gap now {gap:+.5f} "
          f"(shared-ability solve gave -0.043)")

    D["delta"], D["alpha"], D["beta"] = delta, alpha, beta
    return D


def solve(D, min_degree=2, h=None):
    """The one solve. Cached, so a re-run of this script is nearly free."""
    t0 = time.time()
    T = pv.solveSubset(D["course"], D["group"], D["y"],
                       D["n_cells"], D["n_groups"],
                       min_degree=min_degree, quiet=False, h=h,
                       sc=D.get("sc"), ridge=D.get("ridge", 0.0))
    _t("solve", t0)
    D.update(T)
    # ⚠ THE OLD SHRUNK DELTAS BELONG TO THE OLD SOLVE. The tilt path calls
    #   solve() a second time, and between that call and the next shrink()
    #   ratingDelta would otherwise hand out the PREVIOUS solve's array --
    #   stale by exactly the amount h moved things. Dropping it makes the
    #   accessor fall back to the fresh raw delta until shrink() reruns.
    D.pop("delta_shrunk", None)
    D["solved"] = T["degree"] >= min_degree
    D["weights"] = np.bincount(D["course"],
                               minlength=D["n_cells"]).astype(np.float64)
    return D

# ------------------------------------------------------------------ #
#  EXTERNAL LINKAGE SHRINKAGE
# ------------------------------------------------------------------ #
#
# ★ THE DEFECT. delta and athlete ability are interchangeable within a tightly
#   connected group of cells: add a constant to every log-delta inside it and
#   subtract it from every log-ability, and only the FEW EXTERNAL residuals
#   change. When almost all of a cell's linkage stays inside its own cluster,
#   that costs the fit next to nothing and the scale drifts.
#
#   Mission Concepcion Park, one race day, 403 rows, delta = +1.18. Its
#   athletes' 359 cell-links go 278 to MLK Park and 96 to Olmos Soccer Fields
#   -- both also one-day cells with free scales -- and 59 to anything
#   well measured. Joel Harris ran 11:36.9 for 3218 m (5:48/mile) and rates
#   283.6; his TF ratings are 89 and 104. The times are ordinary. The cell is
#   not.
#
#   Measured over the corpus (engine/linkage_check.py): every cell with
#   |delta| > 0.23 has under 25% external linkage, and nearly all are one or
#   two race days. The two properties travel together, exactly as the
#   mechanism predicts.
#
# ★ WHY THE EXISTING SHRINKAGE MISSES IT. shrinkDelta works on precision --
#   cell variance against signal variance -- and a 403-runner cell has a small
#   variance, so it reads as PRECISELY measured. It is: precisely measured
#   RELATIVE TO ITS OWN CLUSTER. Precision and identification are different
#   things, and no variance-based rule can tell them apart.
#
#   Row count fails for the same reason. 403 runners in one race is ONE
#   observation about the course.
#
# ⚠ THIS SHRINKS, IT DOES NOT ZERO. A cell with thin external linkage may
#   still be genuinely hard; what is untrustworthy is the MAGNITUDE, not the
#   sign. Pulling toward zero in proportion to external linkage says "we
#   believe this direction, not this distance", which is what the evidence
#   supports.

# Below this many race days a cell cannot be an anchor for anyone else --
# one race day is one observation about the course however many ran it.
WELL_MEASURED_DAYS = 8


def externalFraction(D):
    """
    Per cell: the share of its athlete-links that reach a WELL MEASURED cell.

    ★ THE NUMERATOR COUNTS ONLY ANCHORED NEIGHBOURS. Mission Concepcion's 278
      links to MLK Park do not identify it -- MLK Park's own scale is free. A
      plain connectivity test called every cell in the corpus connected and
      found nothing; "connected to something SOLID" is the question that
      separates them.

    ★ LINKS ARE COUNTED PER GROUP, NOT PER ROW. One athlete-season racing a
      cell five times teaches the solver one fact about its scale, not five.

    Returns a float array over cells, 0.0 where a cell has no links at all.
    """
    course, group = D["course"], D["group"]
    n_cells = D["n_cells"]

    # Race days per cell -> which cells can anchor. `day` is derived from the
    # pack when present; degree is the fallback, since a cell with many
    # distinct groups spread over time behaves the same way for this purpose.
    days = D.get("cell_days")
    if days is None:
        days = D["degree"]
        thresh = WELL_MEASURED_DAYS * 4      # degree runs higher than days
    else:
        thresh = WELL_MEASURED_DAYS
    anchored = np.asarray(days) >= thresh

    # Unique (group, cell) pairs -- the membership list.
    order = np.lexsort((course, group))
    g, c = group[order], course[order]
    keep = np.ones(len(g), dtype=bool)
    keep[1:] = (g[1:] != g[:-1]) | (c[1:] != c[:-1])
    g, c = g[keep], c[keep]

    # Cells per group, and how many of a group's cells are anchored.
    starts = np.flatnonzero(np.r_[True, g[1:] != g[:-1]])
    ends = np.r_[starts[1:], len(g)]
    ncell = ends - starts
    nanch = np.add.reduceat(anchored[c].astype(np.int64), starts)

    # For each membership, links contributed = (cells in group - 1), of which
    # anchored ones = nanch - (this cell is itself anchored).
    per = np.repeat(ncell, ncell) - 1
    per_a = np.repeat(nanch, ncell) - anchored[c].astype(np.int64)

    tot = np.bincount(c, weights=per, minlength=n_cells)
    ext = np.bincount(c, weights=per_a, minlength=n_cells)
    frac = np.zeros(n_cells)
    nz = tot > 0
    frac[nz] = ext[nz] / tot[nz]
    return frac


# ★ NULLING, NOT JUST SHRINKING, FOR THE WORST CASE -- AND THE REASON IS THAT
#   THIS IS NOT NOISE. Shrinking toward a prior is the right treatment for a
#   NOISY estimate: there is a real signal buried in variance, so you pull it
#   toward the prior in proportion to how noisy it is. That is what
#   pe.shrinkDelta does and it is correct there.
#
#   An unidentified cell is different in kind. Within a tight cluster the
#   scale is FREE -- the data does not contain the answer at all. Keeping 40%
#   of delta = +1.18 does not keep 40% of a true value; it keeps 40% of an
#   arbitrary one. Mission Concepcion at +0.47 would still be a number the
#   data never supported, and arguably worse than +1.18 because a 180 rating
#   passes a glance where a 283 does not.
#
#   So: a cell that is BOTH poorly linked AND has little history of its own is
#   set to its sport's typical difficulty. Cells in the middle band are still
#   shrunk continuously -- there the estimate is merely weak, not absent.
#
# ⚠ THE CONJUNCTION IS WHAT MATTERS, NOT EITHER TEST ALONE. A cell with 16%
#   external linkage but thirty race days has pinned itself over time. A cell
#   with one race day but 80% external linkage is anchored by its athletes.
#   Only "no outside evidence AND no inside history" is fatal.
LINK_MIN = 0.22
DAYS_MIN = 3

# ★ THE SELF-REFERENCE GATE (owner's call, 2026-08-27). The days leg of the
#   conjunction below assumes race days pin a cell over time -- and they do,
#   but only when the returning athletes are measured anywhere else. A venue
#   that hosts its own population's whole season is self-reference stacked
#   deep: Cabell Midland's d3000 cell holds ~1,200 middle-school rows whose
#   careers live at that venue, eight race days of them, and the solve read
#   their below-pool-mean field strength as +0.155 of course difficulty --
#   ~19 fake points on the varsity race sharing the cell.
#
#   externalFraction cannot see this class: a fully venue-locked group has
#   (cells - 1) = 0 links and contributes NOTHING to the fraction, so the
#   cell's frac is computed from the handful of travelled athletes while the
#   vote mass is the locked crowd. bridgeFraction counts the crowd directly:
#   the share of a cell's DISTINCT GROUPS that race at any OTHER venue at
#   all. Below BRIDGE_MIN the cell's scale rests mostly on athletes the
#   corpus cannot place, and per the binary doctrine above it is replaced,
#   not shrunk.
#
# ⚠ XC ONLY, TODAY. TF cells are home tracks, where high locking is normal
#   and deltas are small; gating them needs its own measurement. The printed
#   distribution is the calibration evidence for both knobs.
BRIDGE_MIN = 0.35

# ★ THE SHORT-LABEL NUKE (owner's call, 2026-08-27: "nuke the difficulty,
#   as long as not too many athletes"). check_label_short found 133 venues
#   where two or more short cells' difficulty-implied true distances
#   converge on one longer value -- one course under several short labels,
#   each shortfall absorbed by the solve as fake terrain. The labels cannot
#   be overridden (downward-only forbids raising), so the DIFFICULTY is
#   nuked instead: convergence-proven cells go to the sport default, which
#   deflates their ratings by the shortfall -- the safe direction, per the
#   corrections doctrine -- and stops the solve laundering a lie into
#   plausible numbers.
#
#   Same thresholds as the detector, so the scan and the gate agree; the
#   athlete caps are the "not too many" rail: a cell with more
#   athlete-seasons than the cap is REPORTED and left alone, and if the
#   whole class exceeds the total cap something is miscalibrated and
#   nothing is nuked.
K_DIST = 1.06                  # the normaliser's distance exponent
SHORT_MIN_EXCESS = 0.05        # implied true >= 5% over the label
SHORT_CONVERGE_TOL = 0.04      # hot cells agree within 4%
SHORT_MAX_GROUPS_CELL = 2500   # per-cell athlete-season cap
SHORT_MAX_GROUPS_TOTAL = 60000  # class-wide abort bar


def shortLabelDead(D, delta_s):
    """(mask, skipped) -- convergence-proven short-label cells to nuke.

    Works in delta space, where the anchoring constant cancels in the
    within-venue difference: implied = label * exp((d_i - d_ref)/K).
    """
    keys = D.get("keys") or []
    n_cells = D["n_cells"]
    mask = np.zeros(n_cells, dtype=bool)
    skipped = []
    if len(keys) != n_cells:
        return mask, skipped
    venues = {}
    for i, k in enumerate(keys):
        m = re.match(r"^(XC:.+):d(\d+)$", str(k))
        if m and D["solved"][i]:
            venues.setdefault(m.group(1), []).append((float(m.group(2)), i))
    degree = np.asarray(D["degree"])
    for base, cells in venues.items():
        if len(cells) < 3:            # 2 hot + the reference, minimum
            continue
        cells.sort()
        _ref_label, ref_i = cells[-1]
        hot = []
        for label, i in cells[:-1]:
            if label <= 0:
                continue
            implied = label * np.exp((delta_s[i] - delta_s[ref_i]) / K_DIST)
            if implied / label - 1.0 >= SHORT_MIN_EXCESS:
                hot.append((label, i, implied))
        if len(hot) < 2:
            continue
        ts = np.array([t for _, _, t in hot])
        center = float(np.median(ts))
        if not np.all(np.abs(ts / center - 1.0) <= SHORT_CONVERGE_TOL):
            continue
        for label, i, implied in hot:
            if degree[i] > SHORT_MAX_GROUPS_CELL:
                skipped.append((str(keys[i]), int(degree[i])))
            else:
                mask[i] = True
    return mask, skipped


def bridgeFraction(D):
    """Per cell: share of its distinct groups that race any OTHER venue.

    Venue BASE, not cell: the :d<meters> suffix is stripped, so an athlete
    racing two distances at one park is still venue-locked there.
    """
    course, group = np.asarray(D["course"]), np.asarray(D["group"])
    n_cells, n_groups = D["n_cells"], D["n_groups"]
    keys = D.get("keys")
    if not keys:
        return np.ones(n_cells)
    bases = [re.sub(r":d\w+$", "", str(k)) for k in keys]
    base_id = {b: i for i, b in enumerate(sorted(set(bases)))}
    base_of = np.array([base_id[b] for b in bases], dtype=np.int64)
    n_bases = len(base_id)

    ok = course >= 0
    g, c = group[ok].astype(np.int64), course[ok].astype(np.int64)

    # distinct venues per group -> which groups bridge anywhere
    uniq = np.unique(g * n_bases + base_of[c])
    bases_per_group = np.bincount(uniq // n_bases, minlength=n_groups)
    bridged = bases_per_group >= 2

    # distinct (group, cell) memberships -> per-cell bridged share
    uniq2 = np.unique(g * n_cells + c)
    g2, c2 = uniq2 // n_cells, uniq2 % n_cells
    tot = np.bincount(c2, minlength=n_cells).astype(np.float64)
    num = np.bincount(c2, weights=bridged[g2].astype(np.float64),
                      minlength=n_cells)
    out = np.ones(n_cells)
    nz = tot > 0
    out[nz] = num[nz] / tot[nz]
    return out


def sportDefault(D, delta_s, identified):
    """
    Per sport, the weighted-mean delta over cells that ARE identified.

    ★ NOT ZERO. Zero asserts "exactly average for the whole corpus", which is
      a different claim from "typical for this sport" -- and XC and TF are
      anchored separately, so their typical cells sit in different places. An
      unidentifiable course is most likely an ordinary course, so the honest
      default is what an ordinary course of its kind looks like.

    Sport comes from the cell key prefix ('XC:...' / 'TF:...'). A key that
      matches neither falls back to the global mean rather than being skipped,
      because leaving it at its drifted value is the outcome this exists to
      prevent.
    """
    keys = D.get("keys") or []
    w = D["weights"]
    out = np.zeros(D["n_cells"])

    def meanOf(mask):
        m = mask & identified & (w > 0)
        return float(np.average(delta_s[m], weights=w[m])) if m.any() else 0.0

    is_xc = np.array([str(k).startswith("XC:") for k in keys], dtype=bool) \
        if keys else np.zeros(D["n_cells"], dtype=bool)
    is_tf = np.array([str(k).startswith("TF:") for k in keys], dtype=bool) \
        if keys else np.zeros(D["n_cells"], dtype=bool)

    xc_d, tf_d, all_d = meanOf(is_xc), meanOf(is_tf), meanOf(
        np.ones(D["n_cells"], dtype=bool))
    out[:] = all_d
    out[is_xc] = xc_d
    out[is_tf] = tf_d
    print(f"    [link] sport defaults: XC {xc_d:+.4f}  TF {tf_d:+.4f}  "
          f"(other {all_d:+.4f})")
    return out


def shrinkByLinkage(D, delta_s):
    """
    A cell is either identified or it is not. Nothing in between.

      unidentified   ext < LINK_MIN AND days < DAYS_MIN  -> sport default
      everything else                                    -> UNTOUCHED

    ★ NO CONTINUOUS SHRINKAGE HERE, AND THAT IS DELIBERATE. Shrinking toward a
      prior is the right treatment for a NOISY estimate -- real signal buried
      in variance, pulled toward the prior in proportion to the noise. That is
      what pe.shrinkDelta already does, on precision, and it is correct there.
      Applying a second continuous shrink on top of it would be discounting
      the same estimate twice for two different reasons, and would move
      thousands of cells that are perfectly well identified.
    
      An unidentified cell is a different kind of thing. Its scale is FREE --
      the data does not contain the answer. Keeping 40% of delta = +1.18 does
      not keep 40% of a true value, it keeps 40% of an arbitrary one, and
      +0.47 is arguably worse than +1.18 because a 180 rating passes a glance
      where a 283 does not. So the choice is binary: trust the estimate, or
      replace it.

    ⚠ THE CONJUNCTION IS WHAT MATTERS, NOT EITHER TEST ALONE. A cell with 16%
      external linkage but thirty race days has pinned itself over time. A
      cell with one race day but 80% external linkage is anchored by its
      athletes. Only "no outside evidence AND no inside history" is fatal.
    """
    frac = externalFraction(D)
    days = np.asarray(D.get("cell_days", D["degree"]))
    days_min = DAYS_MIN if D.get("cell_days") is not None else DAYS_MIN * 4

    dead_link = D["solved"] & (frac < LINK_MIN) & (days < days_min)

    # The self-reference gate -- see BRIDGE_MIN above. XC cells whose
    # distinct-group population mostly never leaves the venue are replaced,
    # race days notwithstanding: days of a locked population are the SAME
    # fact restated, not identification.
    keys = D.get("keys") or []
    bridge = bridgeFraction(D)
    is_xc = (np.array([str(k).startswith("XC:") for k in keys], dtype=bool)
             if len(keys) == D["n_cells"]
             else np.zeros(D["n_cells"], dtype=bool))
    dead_bridge = D["solved"] & is_xc & (bridge < BRIDGE_MIN) & ~dead_link

    # ! THE CALIBRATION EVIDENCE, printed every run: the deciles say where
    #   BRIDGE_MIN sits in the real distribution, and the worst list is
    #   checkable against pages (Cabell Midland is the reference case).
    solved_xc = D["solved"] & is_xc
    if solved_xc.any():
        q = np.percentile(bridge[solved_xc], [1, 5, 10, 25, 50])
        print(f"    [link] bridge fraction over {int(solved_xc.sum()):,} "
              f"solved XC cells: p1 {q[0]:.2f}  p5 {q[1]:.2f}  "
              f"p10 {q[2]:.2f}  p25 {q[3]:.2f}  p50 {q[4]:.2f}  "
              f"(gate at {BRIDGE_MIN:.2f})")
    if dead_bridge.any():
        worst = np.argsort(np.where(dead_bridge, -np.abs(delta_s), np.inf))
        print(f"    [link] {int(dead_bridge.sum()):,} XC cells "
              f"SELF-REFERENTIAL (bridge < {BRIDGE_MIN:.0%}) -> sport "
              f"default. Largest deltas replaced:")
        for i in worst[:12]:
            if not dead_bridge[i]:
                break
            print(f"        {str(keys[i]):<34} delta {delta_s[i]:+.3f}  "
                  f"bridge {bridge[i]:.2f}  days {int(days[i])}")

    # The short-label nuke -- see SHORT_MIN_EXCESS above. Runs after the
    # other two so its report never double-counts a cell already dead.
    dead_short, short_skipped = shortLabelDead(D, delta_s)
    dead_short &= ~(dead_link | dead_bridge)
    if dead_short.any() or short_skipped:
        n_groups_hit = int(np.asarray(D["degree"])[dead_short].sum())
        if n_groups_hit > SHORT_MAX_GROUPS_TOTAL:
            print(f"    [link] short-label nuke ABORTED: would touch "
                  f"{n_groups_hit:,} athlete-seasons (cap "
                  f"{SHORT_MAX_GROUPS_TOTAL:,}) -- thresholds need eyes")
            dead_short[:] = False
        else:
            print(f"    [link] {int(dead_short.sum()):,} SHORT-LABEL cells "
                  f"(convergence-proven, {n_groups_hit:,} athlete-seasons) "
                  f"-> sport default; their ratings deflate by the "
                  f"shortfall, the safe direction")
            worst = np.argsort(np.where(dead_short, -np.abs(delta_s),
                                        np.inf))
            for i in worst[:10]:
                if not dead_short[i]:
                    break
                print(f"        {str(keys[i]):<34} delta {delta_s[i]:+.3f}")
        for k, n in short_skipped[:8]:
            print(f"    [link] short-label SKIPPED over per-cell cap: "
                  f"{k} ({n:,} athlete-seasons)")

    dead = dead_link | dead_bridge | dead_short
    if not dead.any():
        print("    [link] no unidentified cells")
        return delta_s

    default = sportDefault(D, delta_s, D["solved"] & ~dead)
    out = delta_s.copy()
    out[dead] = default[dead]

    worst = np.abs(delta_s[dead]).max()
    print(f"    [link] {int(dead.sum()):,} of {int(D['solved'].sum()):,} "
          f"solved cells replaced by sport default: "
          f"{int(dead_link.sum()):,} unidentified "
          f"(ext < {LINK_MIN:.0%} AND days < {days_min}), "
          f"{int(dead_bridge.sum()):,} self-referential "
          f"(bridge < {BRIDGE_MIN:.0%}), "
          f"{int(dead_short.sum()):,} short-label")
    print(f"    [link] largest delta discarded {worst:+.3f}; "
          f"all other cells untouched")
    return out



def shrink(D):
    """Precision shrinkage, then the anchored difficulty in both forms."""
    v = pe.cellVariance(D["sigma2"], D["diag"])
    tau2, tau2_high = pe.signalVariance(D["delta"], v, D["solved"], D["diag"])
    delta_s, weight = pe.shrinkDelta(D["delta"], v, tau2, D["solved"])
    pe.reportShrinkage(D["sigma2"], tau2, tau2_high, weight,
                       D["degree"], D["solved"])

    # ★ SECOND SHRINKAGE, ON A DIFFERENT AXIS. shrinkDelta above handles
    #   PRECISION; this handles IDENTIFICATION. A cell can be precisely
    #   measured relative to its own cluster and still have a free scale.
    delta_s = shrinkByLinkage(D, delta_s)

    raw = np.expm1(pe.anchorToWeightedMean(D["delta"], D["weights"], D["solved"]))
    raw[~D["solved"]] = 0.0
    diff = pe.anchorToWeightedMean(np.expm1(delta_s), D["weights"], D["solved"])
    diff[~D["solved"]] = 0.0

    # ! HOW FAR THE RATINGS JUST MOVED. Both shrinkages now reach the rating
    #   path, so this number is the size of the correction rather than a
    #   diagnostic about a table nobody read.
    moved = np.abs(delta_s - D["delta"])[D["solved"]]
    if moved.size:
        print(f"    [link] ratings now use the shrunk deltas: "
              f"median move {float(np.median(moved)):.4f}, "
              f"max {float(moved.max()):.4f}, "
              f"{int((moved > 0.05).sum()):,} cells moved > 0.05")

    D.update({"delta_shrunk": delta_s, "difficulty": diff,
              "difficulty_raw": raw, "weight": weight, "cell_var": v})
    return D


# ------------------------------------------------------------------ #
# CHUNK 2 -- RATINGS
# ------------------------------------------------------------------ #

# ratingDelta
# Purpose:   The per-cell difficulty every RATING should be computed against.
# Arguments: D -- the solve dict.
# Output:    the shrunk delta array when shrink() has run, else the raw one.
#
# ★ shrink() PRODUCED THIS AND NOTHING READ IT. delta_shrunk was written at
#   the end of shrink() and used for exactly one thing -- building
#   D["difficulty"], which golive writes to course_difficulties. Every path
#   that produced a RATING read the raw D["delta"] instead: refitAlpha,
#   refitAlphaBeta, perRaceRatings and resultRatings, all four.
#
#   So the site displayed the shrunk value and the engine rated against the
#   unshrunk one, and the two silently disagreed on every cell shrinkage
#   touched.
#
#   Measured at La Feria (XC:20609:d1600), a 37-runner middle school mile
#   where every entrant had two or three races and the division shared at
#   most seven athletes with any other meet. shrinkByLinkage correctly ruled
#   the cell UNIDENTIFIED and replaced delta = +0.2849 with the sport
#   default; course_difficulties duly showed +0.022. The ratings used +0.2849
#   anyway -- exp(0.2849) = 1.3296 -- and a 5:37 seventh-grade mile came out
#   at 162.8, second on the national middle school board.
#
# ⚠ BOTH SHRINKAGES, NOT JUST THE IDENTIFICATION ONE. delta_shrunk is
#   pe.shrinkDelta (continuous, on precision) followed by shrinkByLinkage
#   (binary, on identification). Taking only the second would leave a cell
#   fitted from fifteen results carrying its full noise, and would keep the
#   site and the engine on different numbers -- which is half of what this
#   fixes.
#
#   An unidentified cell still lands on exactly the sport default, because
#   shrinkByLinkage runs last inside shrink() and overwrites.
#
# ! FALLS BACK TO THE RAW DELTA. ratings() is called once BEFORE shrink() on
#   the --split path, to give recenterSport an alpha and beta to work with.
#   That call has no shrunk array to read and must not fail.
def ratingDelta(D):
    d = D.get("delta_shrunk")
    return D["delta"] if d is None else d


def ratings(D, pool="hs_m", quiet=False):
    """alpha -> ability -> ratings, plus the consistency table and leaderboard."""
    t0 = time.time()
    if D.get("sc") is None:
        # ! REFIT AGAINST THE SAME DELTAS THE RATINGS WILL USE. alpha is
        #   whatever the model leaves over after the course effect; refitting
        #   against a delta the rating then does not use puts that difference
        #   into ability instead.
        alpha, _c = pv.refitAlpha(D["y"], ratingDelta(D), D["course"],
                                  D["group"], D["n_groups"], h=D.get("h"))
        D["alpha"], D["beta"] = alpha, None
    else:
        alpha, beta, _c = pv.refitAlphaBeta(D["y"], ratingDelta(D),
                                            D["course"], D["group"],
                                            D["n_groups"], D["sc"],
                                            D.get("ridge", 0.0))
        D["alpha"], D["beta"] = alpha, beta
    attrs = pr.groupAttributes(D["group"], D["athlete"], D["year"],
                               D["n_groups"],
                               pr.poolPerAthlete(D["cols"]["athlete_keys"]))
    rat = pr.buildRatings(alpha, attrs)
    _t("ratings", t0)

    n_pools = max(len(attrs["pool_names"]), 1)
    pmean, _ = pr._meanBy(rat["ability"], attrs["pool"].clip(0), n_pools,
                          rat["valid"])
    race_rating = pr.perRaceRatings(D["norm"], ratingDelta(D), D["course"],
                                    D["group"], pmean[attrs["pool"].clip(0)])

    if not quiet:
        pr.reportConsistency(race_rating, D["group"], attrs, rat, D["n_groups"])
        for key in ("seasonal", "career"):
            pr.reportLeaderboard(rat, attrs, D["cols"]["athlete_keys"], pool,
                                 key)

    D.update({"alpha": alpha, "attrs": attrs, "rat": rat})
    return D


# ------------------------------------------------------------------ #
# CHUNK 3 -- WRITES
# ------------------------------------------------------------------ #

def writeTables(D):
    """pair_athlete_season / pair_athlete / pair_course_difficulty."""
    import pair_write as pw
    from database import getConn

    r = {"person_id": np.array([str(D["cols"]["athlete_keys"][a][0])
                                for a in D["attrs"]["athlete"]]),
         "pool": np.array([D["attrs"]["pool_names"][c] if c >= 0 else ""
                           for c in D["attrs"]["pool"]]),
         "season": D["attrs"]["season"], "races": D["attrs"]["races"],
         "ability": D["rat"]["ability"],
         "rating_seasonal": D["rat"]["seasonal"],
         "rating_career": D["rat"]["career"],
         "valid": D["rat"]["valid"],
         "difficulty": D["difficulty"],
         "course_keys": np.array(D["keys"])}

    career = pw.collapseToCareer(r)
    with getConn() as conn:
        with conn.cursor() as cur:
            for t in pw._TABLES:
                cur.execute(f"DROP TABLE IF EXISTS {t}")
                cur.execute(pw._DDL[t])
            pw.copyRows(cur, "pair_athlete_season",
                        ("person_id", "pool", "season", "races", "ability",
                         "rating_seasonal", "rating_career"), pw.seasonRows(r))
            pw.copyRows(cur, "pair_athlete",
                        ("person_id", "pool", "rating_best", "best_season",
                         "rating_recent", "recent_season", "rating_weighted",
                         "n_seasons", "total_races"), pw.careerRows(career))
            pw.copyRows(cur, "pair_course_difficulty",
                        ("course_key", "canonical_id", "distance_m",
                         "difficulty", "degree"),
                        pw.difficultyRows(D["keys"], D["difficulty"],
                                          D["degree"]))
            for stmt in pw._INDEXES:
                cur.execute(stmt)
        conn.commit()
    print("[all] pair_* tables written")


def resultRatings(D, anchor="career"):
    """Per-result ratings, both anchors. Needed for --result-table or --golive."""
    from pair_write_results import poolMeanPerGroup, ratedMask

    pm_c, pm_s = poolMeanPerGroup(D["rat"]["ability"], D["attrs"],
                                  D["rat"]["valid"],
                                  anchor=D["rat"].get("anchor"))
    # ★ THE COURSE EFFECT ON THIS ROW. Per-cell, and ONLY per-cell.
    #   h scales delta by the ability tilt, which abilityTilt computes per CELL,
    #   so every row in one race gets the same h.
    #
    #   beta (the athlete-season sport offset) is deliberately NOT applied here.
    #   It is a nuisance parameter of the split solve -- it exists so alpha and
    #   delta come out clean -- and it varies from athlete to athlete. Folding
    #   it into eff gave two runners in the SAME race on the SAME course
    #   different effective difficulties, so ratings stopped being monotone in
    #   time: exp(+-0.045) is a ~4.6% spread, more than enough to invert
    #   adjacent finishers. A per-result rating answers "how fast was this run,
    #   on this course" -- a property of the row and the cell, not of the
    #   athlete's specialisation. beta stays in the solve (recenterSport); it
    #   just does not leak out into the published rating.
    eff = ratingDelta(D)[D["course"]]
    if D.get("h") is not None:
        eff = D["h"] * eff
    adjusted = D["norm"] / np.exp(eff)
    rc = 100.0 * pm_c[D["group"]] / adjusted
    rs = 100.0 * pm_s[D["group"]] / adjusted
    # cell must be solved; the athlete-season need not be rated -- see
    # ratedMask. This is what recovers one-race seasons.
    ok = ratedMask(rc, rs, D["group"], D["rat"]["valid"],
                   cell_solved=D["solved"], course=D["course"])
    print(f"[all] {int(ok.sum()):,}/{ok.size:,} rows rated "
          f"({100.0 * ok.mean():.1f}%)")
    D.update({"r_career": rc, "r_seasonal": rs, "rated": ok,
              "chosen": rs if anchor == "seasonal" else rc})
    return D


def golive(D, collapse="best", anchor="career"):
    """
    ⚠ THE IRREVERSIBLE ONE. Writes course_difficulties, athlete_ratings and
      results.speed_rating -- all three from THIS solve, so they cannot disagree.

      Calls the engine's own writers. saveResultSpeedRatings rebuilds both heaps
      (~35 min) and drops the _old copies on success.
    """
    import pair_golive as pg
    from database import getConn
    from speed_ratings_db import (saveCourseDifficulties, saveAthleteRatings,
                                  saveResultSpeedRatings)

    with getConn() as conn:
        pg.backupSpeedRatings(conn)

    diffs = pg.buildDifficultyDict(D["keys"], D["difficulty"], D["course"],
                                   D["athlete"], D["solved"])
    print(f"[all] course_difficulties: {len(diffs):,} cells")
    saveCourseDifficulties(diffs, ("XC", "TF"))

    r = {"valid": D["rat"]["valid"],
         "person_id": np.array([str(D["cols"]["athlete_keys"][a][0])
                                for a in D["attrs"]["athlete"]]),
         "pool": np.array([D["attrs"]["pool_names"][c] if c >= 0 else ""
                           for c in D["attrs"]["pool"]]),
         "season": D["attrs"]["season"], "races": D["attrs"]["races"],
         "rating_seasonal": D["rat"]["seasonal"]}
    ath = pg.buildAthleteDict(r, collapse)
    print(f"[all] athlete_ratings: {len(ath):,} rows (collapse={collapse})")
    saveAthleteRatings(ath, ("XC", "TF"))

    ok, sport, rid = D["rated"], D["sport"], D["result_id"]
    for code, name in ((0, "XC"), (1, "TF")):
        m = ok & (sport == code)
        if m.any():
            saveResultSpeedRatings(name, (rid[m],
                                          np.round(D["chosen"][m], 2)))
            print(f"[all] {name}: {int(m.sum()):,} result ratings written")
    print("\n[all] LIVE. To undo:\n" + pg.restoreSql())


# ------------------------------------------------------------------ #
# CHUNK 4 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(pack_path, write=False, do_golive=False, do_validate=False,
         do_result_table=False, collapse="best", anchor="career",
         pool="hs_m", tilt=False, split=False):
    t_start = time.time()

    D = prepare(pack_path)
    if split:
        D["sc"] = sportOffset(D)
        # ★ RIDGE ZERO, AND THE REASON IS COUNTER-INTUITIVE.
        #
        #   A small ridge was tried first, on the theory that it lifts the
        #   near-null sport direction off zero. Measured condition numbers over
        #   the operator's range space say the opposite:
        #
        #       ridge = infinity (shared)     6.9
        #       ridge = 1                    15.8
        #       ridge = 0.2                  50.6
        #       ridge = 0.05                181.0     <- what was shipped
        #       ridge = 0                     1.3
        #
        #   At ridge 0 the sport direction is EXACTLY null, so conjugate
        #   gradient never enters it and the remaining range is beautifully
        #   conditioned. At a small ridge it becomes NEARLY null -- a tiny
        #   positive eigenvalue against a maximum near 40 -- and that ratio is
        #   what wrecks the conditioning. 0.05 was the worst possible choice.
        #
        #   The exactly-null direction is then restored by recentring, which is
        #   what recenterSport does and why it is not optional.
        D["ridge"] = 0.0
        if D["sc"] is None:
            print("[all] --split asked for but the pack has no sport column")
        else:
            print("[all] per-athlete sport offsets ON (recentred after the "
                  "solve)")
    solve(D)
    if D.get("sc") is not None:
        # alpha and beta must exist before recentring, so ratings() runs first
        # on the un-recentred solve and again below on the recentred one.
        ratings(D, pool=pool, quiet=True)
        recenterSport(D)
    shrink(D)
    ratings(D, pool=pool)

    if tilt:
        # ★ ONE OUTER ITERATION. h depends on ratings, ratings depend on the
        #   solve. Pass 1 above gives ratings from the untilted fit; pass 2
        #   re-solves with h and re-derives everything from it. A third pass
        #   moves h by well under a rating point -- the correction is ~12% at
        #   the extremes and its input is already close.
        print(f"\n[all] ability tilt: re-solving with h "
              f"(K={_TILT_K:+.4f} per 10 rating points)...")
        D["h"] = abilityTilt(D)
        print(f"[all] h: min {D['h'].min():.3f} median "
              f"{float(np.median(D['h'])):.3f} max {D['h'].max():.3f}")
        solve(D, h=D["h"])
        shrink(D)
        ratings(D, pool=pool)

    out = os.path.join(os.path.dirname(os.path.abspath(pack_path)),
                       "pair_difficulty.npz")
    np.savez(out, difficulty=D["difficulty"], difficulty_raw=D["difficulty_raw"],
             solved=D["solved"], degree=D["degree"], weight=D["weight"],
             cell_var=D["cell_var"], course_keys=np.array(D["keys"]))
    print(f"[all] wrote {out}")

    if do_validate:
        print("\n[all] validation (three extra solves)...")
        te = pv.splitByRow(D["y"].size, frac=0.10, seed=1)
        T = pv.solveSubset(D["course"][~te], D["group"][~te], D["y"][~te],
                           D["n_cells"], D["n_groups"])
        a, gc = pv.refitAlpha(D["y"][~te], T["delta"], D["course"][~te],
                              D["group"][~te], D["n_groups"])
        pv.evaluateHoldout(T["delta"], a, gc, D["y"][te], D["course"][te],
                           D["group"][te], T["degree"] >= 2, "pair")
        z = np.zeros(D["n_cells"])
        a0, g0 = pv.refitAlpha(D["y"][~te], z, D["course"][~te],
                               D["group"][~te], D["n_groups"])
        pv.evaluateHoldout(z, a0, g0, D["y"][te], D["course"][te],
                           D["group"][te], T["degree"] >= 2, "zero")

    if do_result_table or do_golive:
        resultRatings(D, anchor=anchor)

    if write or do_golive:
        writeTables(D)

    if do_result_table:
        import pair_write_results as pwr
        from database import getConn
        with getConn() as conn:
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS pair_result_rating")
                cur.execute(pwr._DDL)
                ok = D["rated"]
                pwr.copyRows(cur, "pair_result_rating",
                             ("result_id", "sport", "rating_career",
                              "rating_seasonal"),
                             pwr.resultRows(D["result_id"][ok], D["sport"][ok],
                                            D["r_career"][ok],
                                            D["r_seasonal"][ok]))
            conn.commit()
        print("[all] pair_result_rating written")

    if do_golive:
        golive(D, collapse=collapse, anchor=anchor)

    print(f"\n[all] TOTAL {(time.time() - t_start) / 60:.1f} min")


if __name__ == "__main__":
    here = os.path.join(_HERE, "data")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    def opt(name, default):
        for a in sys.argv[1:]:
            if a.startswith(f"--{name}="):
                return a.split("=", 1)[1]
        return default

    main(args[0] if args else os.path.join(here, "packed_XC_TF.npz"),
         write="--write" in sys.argv,
         do_golive="--golive" in sys.argv,
         do_validate="--validate" in sys.argv,
         do_result_table="--result-table" in sys.argv,
         tilt="--tilt" in sys.argv,
         split="--split" in sys.argv,
         collapse=opt("collapse", "best"),
         anchor=opt("anchor", "career"),
         pool=opt("pool", "hs_m"))