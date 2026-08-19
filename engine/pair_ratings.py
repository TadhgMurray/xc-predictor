# Project: xc-predictor
# Subset:  Pair Engine -- ratings
#
# WHAT THIS PRODUCES, AND WHY IT DIFFERS FROM THE ALS ENGINE
#
#   ability = exp(alpha[athlete, season])          a 5K-equivalent time
#   rating  = 100 * pool_mean / ability            >100 means faster than the pool
#
# ★ ONE RATING PER SEASON, NOT ONE PER CAREER. The ALS engine holds a single
#   ability per athlete for all time. A high schooler improving 4%/year for four
#   years has ~15% of variation forced into one scalar, and the remainder lands
#   in whichever venues they raced -- which correlates with WHEN. Per season is
#   both a better model and a more useful product: it lets a leaderboard rank a
#   SEASON rather than a career.
#
# ★ TWO ANCHORS, AND THE CHOICE MATTERS MORE THAN IT LOOKS.
#     'career'  -- pool_mean over every athlete-season in the pool. Comparable
#                  across years, and therefore exposed to the era curve. The
#                  ALS top-40 came back with a median year of 1991.
#     'seasonal'-- pool_mean within (pool, season). Era CANNOT enter: everyone
#                  is measured against the peers they actually raced. The cost is
#                  that a 1991 rating and a 2026 rating are no longer on one
#                  scale, so cross-era claims become meaningless by construction.
#   Both are computed. Neither is "right" -- they answer different questions.
#
# THE CHECK THAT MATTERS
#   Per-race ratings within one athlete-season should cluster. Their spread is
#   the direct measure of "same performance at a different course gives the same
#   rating", and it is reported against the floor: an athlete varies 3.3-4.5
#   points race to race at the SAME venue in the SAME season, which no model can
#   go below.

import os
import sys
from collections import defaultdict

import numpy as np

import pair_engine as pe
import pair_validate as pv

_MIN_RACES_LEADERBOARD = 20      # display filter; see reportLeaderboard


# ------------------------------------------------------------------ #
# CHUNK 1 -- POOLS
# ------------------------------------------------------------------ #

# poolPerAthlete
# Purpose:   bare pool name per athlete CODE.
# Detail:    merged runs carry "hs_m", per-sport runs "hs_m|XC". Split on "|"
#            exactly as rust_fitness does, so one convention serves both.
def poolPerAthlete(athlete_keys):
    out = []
    for key in athlete_keys:
        raw = key[1] if (key is not None and len(key) > 1) else ""
        out.append(str(raw).split("|", 1)[0] if raw else "unknown")
    return out


# groupAttributes
# Purpose:   per athlete-season: its athlete code, season, pool and race count.
# Arguments: group -- athlete-season code per row; athlete, year per row;
#            n_groups; pool_of_athlete.
# Output:    dict of arrays, all indexed by athlete-season code.
#
# Syntax: np.zeros then scatter-assign rather than a groupby -- every row of a
# group carries the same athlete and season, so a plain assignment leaves the
# right value behind and costs one pass instead of a sort.
def groupAttributes(group, athlete, year, n_groups, pool_of_athlete):
    ath = np.zeros(n_groups, dtype=np.int64)
    season = np.zeros(n_groups, dtype=np.int64)
    ath[group] = athlete
    season[group] = year
    races = np.bincount(group, minlength=n_groups)

    pool_code = np.full(n_groups, -1, dtype=np.int32)
    names, index = [], {}
    for g in range(n_groups):
        if races[g] == 0:
            continue
        p = pool_of_athlete[ath[g]]
        if p not in index:
            index[p] = len(names)
            names.append(p)
        pool_code[g] = index[p]
    return {"athlete": ath, "season": season, "races": races,
            "pool": pool_code, "pool_names": names}


# ------------------------------------------------------------------ #
# CHUNK 2 -- RATINGS
# ------------------------------------------------------------------ #

# _meanBy
# Purpose:   mean of `values` grouped by integer `by`, ignoring masked-out rows.
# Output:    (means ndarray, counts ndarray)
def _meanBy(values, by, n, valid):
    total = np.bincount(by[valid], weights=values[valid], minlength=n)
    count = np.bincount(by[valid], minlength=n)
    return np.where(count > 0, total / np.maximum(count, 1), 0.0), count


# buildRatings
# Purpose:   career-anchored and season-anchored ratings per athlete-season.
# Arguments: alpha -- per athlete-season; attrs -- from groupAttributes;
#            min_races.
# Output:    dict with 'career' and 'seasonal' rating arrays.
#
# ★ THE ANCHOR IS A MEAN OF ABILITIES IN SECONDS, not of ratings. Averaging
#   ratings would anchor on a ratio of a ratio and the 100 point would drift with
#   the shape of the distribution rather than sitting at the pool's middle.
def buildRatings(alpha, attrs, min_races=2, min_races_anchor=3):
    """
    ★ TWO THRESHOLDS, NOT ONE.

      min_races        who gets a rating at all.
      min_races_anchor who counts toward pool_mean, the 100 point.

      They were one number, and that conflated two different decisions. Raising
      it to 3 blanked 10.6M of 39.3M XC rows -- 27%, against ALS's 13% -- because
      ALS counted races per CAREER while this counts them per SEASON. An athlete
      who raced twice one autumn was rated by ALS and not here, and
      saveResultSpeedRatings runs preserve_unmatched=False, so those rows were
      actively set to NULL rather than left alone.

      But lowering the threshold should NOT lower the bar for the anchor. A
      2-race athlete-season has a noisy alpha; admitting it to the output is a
      coverage decision, admitting it to pool_mean shifts the scale for
      everybody. The anchor stays at 3.

    ⚠ A 2-RACE RATING IS GENUINELY NOISIER. Its alpha rests on two observations,
      so expect held-out error to worsen slightly while coverage improves a lot.
      That trade is a product call: run pair_all --validate at each setting if
      you want the number rather than the argument.
    """
    ability = np.exp(alpha)
    finite = np.isfinite(ability) & (ability > 0) & (attrs["pool"] >= 0)
    valid = (attrs["races"] >= min_races) & finite
    anchor = (attrs["races"] >= min_races_anchor) & finite

    n_pools = max(len(attrs["pool_names"]), 1)
    pool_mean, _ = _meanBy(ability, attrs["pool"].clip(0), n_pools, anchor)

    # (pool, season) key -> dense code, for the seasonal anchor.
    combo = attrs["pool"].clip(0).astype(np.int64) * 10000 + attrs["season"]
    _u, combo_code = np.unique(combo, return_inverse=True)
    season_mean, _ = _meanBy(ability, combo_code, combo_code.max() + 1, anchor)

    with np.errstate(divide="ignore", invalid="ignore"):
        career = 100.0 * pool_mean[attrs["pool"].clip(0)] / ability
        seasonal = 100.0 * season_mean[combo_code] / ability
    career[~valid] = np.nan
    seasonal[~valid] = np.nan
    return {"ability": ability, "career": career, "seasonal": seasonal,
            "valid": valid, "anchor": anchor}


# perRaceRatings
# Purpose:   one rating per RESULT, the quantity a user actually sees.
# Arguments: norm -- normalized time per row; delta -- per cell; course, group;
#            pool_mean_of_group.
# Output:    per-row rating.
#
# Matches the ALS convention: adjusted = norm / (1 + d), then
# rating = 100 * pool_mean / adjusted. The form correction is deliberately NOT
# applied here -- form is a nuisance term for estimating difficulty, and a
# November race SHOULD rate higher than a September one if it was better.
def perRaceRatings(norm, delta, course, group, pool_mean_of_group):
    adjusted = norm / np.exp(delta[course])
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100.0 * pool_mean_of_group[group] / adjusted


# ------------------------------------------------------------------ #
# CHUNK 3 -- THE CONSISTENCY CHECK
# ------------------------------------------------------------------ #

# reportConsistency
# Purpose:   ★ THE USER'S OWN CRITERION. Spread of per-race ratings inside one
#            athlete-season, by ability band.
# Arguments: race_rating per row; group; attrs; ratings.
#
# A rating is "indicative of ability" exactly when this spread is small. The
# floor is not zero: the same athlete at the SAME venue in the same season varies
# 3.3-4.5 points, so anything near that is as good as the data allows.
def reportConsistency(race_rating, group, attrs, ratings, n_groups):
    ok = np.isfinite(race_rating)
    total = np.bincount(group[ok], weights=race_rating[ok], minlength=n_groups)
    total2 = np.bincount(group[ok], weights=race_rating[ok] ** 2,
                         minlength=n_groups)
    cnt = np.bincount(group[ok], minlength=n_groups).astype(np.float64)

    with np.errstate(divide="ignore", invalid="ignore"):
        mean = total / np.maximum(cnt, 1)
        var = np.maximum(total2 / np.maximum(cnt, 1) - mean ** 2, 0.0)
    sd = np.sqrt(var)

    use = (cnt >= 4) & ratings["valid"] & np.isfinite(ratings["career"])
    print("\n[rate] ---- per-race rating spread within an athlete-season ----")
    print("    rating band   athlete-seasons     mean_sd     cv%")
    for lo, hi in ((0, 80), (80, 95), (95, 105), (105, 120), (120, 135),
                   (135, 1000)):
        m = use & (ratings["career"] >= lo) & (ratings["career"] < hi)
        if m.sum() < 50:
            continue
        label = f"{lo}-{hi}" if hi < 1000 else f"{lo}+"
        print(f"    {label:>11} {int(m.sum()):>17,} {sd[m].mean():>11.2f} "
              f"{100.0 * (sd[m] / np.maximum(mean[m], 1)).mean():>7.2f}")
    print("    floor: same athlete, SAME venue, same season varies 3.3-4.5")
    print("[rate] ----------------------------------------------------------")


# ------------------------------------------------------------------ #
# CHUNK 4 -- LEADERBOARD
# ------------------------------------------------------------------ #

# fetchNames
# Purpose:   person_id -> display name, or {} if the table is unreachable.
# Detail:    athlete_named is the only table carrying both person_id and a name.
#            Wrapped: a leaderboard without names is still useful, a crash is not.
def fetchNames(person_ids):
    if not person_ids:
        return {}
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT person_id, name FROM athlete_named "
                        "WHERE person_id = ANY(%s)", (list(person_ids),))
            return {r[0]: r[1] for r in cur.fetchall()}
    except Exception as exc:
        print(f"    (names unavailable: {exc})")
        return {}


# reportLeaderboard
# Purpose:   top athlete-seasons for one pool, both anchors.
# Arguments: ratings, attrs, athlete_keys, pool, season filter, top.
#
# ⚠ THE min_races FILTER IS NOT OPTIONAL. The ALS top-40 had a median of 4
#   races and a median year of 1991 -- thin records at the extreme of a
#   distribution, plus era inflation. Any published list needs this gate
#   regardless of which engine produced it.
def reportLeaderboard(ratings, attrs, athlete_keys, pool="hs_m",
                      key="seasonal", top=25, min_races=None):
    # Resolved here, not in the signature: a module-level default arg is bound
    # at def time, so setting the constant afterwards would silently do nothing.
    if min_races is None:
        min_races = _MIN_RACES_LEADERBOARD
    names_list = attrs["pool_names"]
    if pool not in names_list:
        print(f"[rate] pool {pool} not present")
        return
    pcode = names_list.index(pool)

    m = (ratings["valid"] & (attrs["pool"] == pcode)
         & (attrs["races"] >= min_races) & np.isfinite(ratings[key]))
    if not m.any():
        print(f"[rate] no {pool} athlete-seasons with >= {min_races} races")
        return

    idx = np.nonzero(m)[0]
    order = idx[np.argsort(-ratings[key][idx])][:top]
    pids = [athlete_keys[attrs["athlete"][g]][0] for g in order]
    names = fetchNames(set(pids))

    print(f"\n[rate] ---- top {top} {pool} athlete-seasons "
          f"({key} anchor, >= {min_races} races) ----")
    print("    rating  season  races  name")
    for g, pid in zip(order, pids):
        print(f"    {ratings[key][g]:>6.1f}  {int(attrs['season'][g]):>6}  "
              f"{int(attrs['races'][g]):>5}  {names.get(pid, f'person {pid}')}")
    print("[rate] --------------------------------------------------------")


# ------------------------------------------------------------------ #
# CHUNK 5 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(pack_path, pool="hs_m"):
    cols = pe.loadPack(pack_path)
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    y_all, _form = pe.buildResponse(cols)

    course = cols["course"][keep].astype(np.int64)
    y = y_all[keep]
    norm = cols["norm"][keep]
    athlete = cols["athlete"][keep]
    year = cols["year"][keep]
    group, n_groups = pe.athleteSeasonCodes(athlete, year)
    n_cells = len(cols["course_keys"])

    print(f"[rate] {y.size:,} rows, {n_groups:,} athlete-seasons")
    T = pv.solveSubset(course, group, y, n_cells, n_groups, quiet=False)
    delta = T["delta"]

    # alpha from the FORM-CORRECTED response, so ability is not contaminated by
    # season position; per-race ratings below use raw norm, which is correct --
    # a better November race should rate higher.
    alpha, _cnt = pv.refitAlpha(y, delta, course, group, n_groups)

    pool_of_athlete = poolPerAthlete(cols["athlete_keys"])
    attrs = groupAttributes(group, athlete, year, n_groups, pool_of_athlete)
    ratings = buildRatings(alpha, attrs)

    n_pools = max(len(attrs["pool_names"]), 1)
    pmean, _ = _meanBy(ratings["ability"], attrs["pool"].clip(0), n_pools,
                       ratings["valid"])
    race_rating = perRaceRatings(norm, delta, course, group,
                                pmean[attrs["pool"].clip(0)])

    reportConsistency(race_rating, group, attrs, ratings, n_groups)
    for key in ("seasonal", "career"):
        reportLeaderboard(ratings, attrs, cols["athlete_keys"], pool, key)

    saveRatings(pack_path, ratings, attrs, cols, delta)


# saveRatings
# Purpose:   one row per athlete-season, keyed by person_id so the output can be
#            joined straight onto athlete_named or written to a table.
# Detail:    person_id is stored as a string array -- the ids are not guaranteed
#            to be integers across sources, and np.savez cannot hold objects.
def saveRatings(pack_path, ratings, attrs, cols, delta):
    keys = cols["athlete_keys"]
    pool_names = attrs["pool_names"]
    pid = np.array([str(keys[a][0]) for a in attrs["athlete"]])
    pool = np.array([pool_names[c] if c >= 0 else "" for c in attrs["pool"]])

    out = os.path.join(os.path.dirname(os.path.abspath(pack_path)),
                       "pair_ratings.npz")
    np.savez(out,
             person_id=pid, pool=pool, season=attrs["season"],
             races=attrs["races"], ability=ratings["ability"],
             rating_career=ratings["career"],
             rating_seasonal=ratings["seasonal"],
             valid=ratings["valid"], difficulty=np.expm1(delta),
             course_keys=np.array(cols["course_keys"], dtype=str))
    print(f"\n[rate] wrote {out} ({int(ratings['valid'].sum()):,} rated "
          f"athlete-seasons)")


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    main(sys.argv[1] if len(sys.argv) > 1
         else os.path.join(here, "packed_XC_TF.npz"),
         sys.argv[2] if len(sys.argv) > 2 else "hs_m")