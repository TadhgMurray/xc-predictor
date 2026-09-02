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

    # ⚠⚠ A MEASURED bbar IS ONLY VALID AT THE RIDGE IT WAS MEASURED AT.
    #    bbar is the weighted mean of beta, and the ridge decides how far
    #    beta is shrunk toward zero -- so the SAME corpus yields a different
    #    bbar at every K. sport_gap_bbar.json holds -0.04503, measured
    #    2026-08-26 when --split ran at ridge 0 (the full split). At ridge
    #    0.5 this solve's own estimate is +0.00371 over 2.9M dual-sport
    #    athlete-seasons -- the OPPOSITE SIGN. Applying the stale constant
    #    anyway moved the TF-XC gap to -0.109 against the shared solve's
    #    -0.043, i.e. it credited XC courses as ~6.5% harder than the data
    #    says, which inflates every XC rating. That is issue #64's symptom,
    #    manufactured by the recentring step rather than found in the corpus.
    #
    # ! THE GUARD IS THE RIDGE THE FILE RECORDS (pair_recenter.measuredFor),
    #   not the sign of the solve's own estimate: at ridge 0.5 that estimate
    #   is ~0 by construction and its sign is noise. See linkage_check.
    own_bbar, _n = prc.meanOffset(D["beta"], D["sc"], D["group"],
                                  D["n_groups"])
    use_bbar, note = prc.measuredFor(D.get("ridge", 0.0), own_bbar)
    if note:
        print(note)


    delta, alpha, beta, bbar, n_ident = prc.recenter(
        D["delta"], D["alpha"], D["beta"], D["sc"], D["group"],
        D["sport"], D["course"], D["n_cells"], D["n_groups"],
        bbar=use_bbar)

    s_cell = prc.cellSport(D["course"], D["sport"], D["n_cells"])
    xc = D["solved"] & (s_cell < 0)
    tf = D["solved"] & (s_cell > 0)
    gap = float(delta[tf].mean() - delta[xc].mean())
    if use_bbar is not None:
        # The solve's own estimate still prints: its drift AWAY from the
        # measured constant is the telemetry that says when to re-measure.
        print(f"[all] sport recentre: bbar {bbar:+.5f} MEASURED "
              f"(pair_recenter.MEASURED_BBAR; solve's own estimate "
              f"{own_bbar:+.5f} from {n_ident:,} dual-sport athlete-seasons)")
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
    D["solved"] = T["degree"] >= min_degree
    D["weights"] = np.bincount(D["course"],
                               minlength=D["n_cells"]).astype(np.float64)
    return D


def shrink(D):
    """Precision shrinkage, then the anchored difficulty in both forms."""
    v = pe.cellVariance(D["sigma2"], D["diag"])
    tau2, tau2_high = pe.signalVariance(D["delta"], v, D["solved"], D["diag"])
    delta_s, weight = pe.shrinkDelta(D["delta"], v, tau2, D["solved"])
    pe.reportShrinkage(D["sigma2"], tau2, tau2_high, weight,
                       D["degree"], D["solved"])

    raw = np.expm1(pe.anchorToWeightedMean(D["delta"], D["weights"], D["solved"]))
    raw[~D["solved"]] = 0.0
    diff = pe.anchorToWeightedMean(np.expm1(delta_s), D["weights"], D["solved"])
    diff[~D["solved"]] = 0.0

    D.update({"delta_shrunk": delta_s, "difficulty": diff,
              "difficulty_raw": raw, "weight": weight, "cell_var": v})
    return D


# ------------------------------------------------------------------ #
# CHUNK 2 -- RATINGS
# ------------------------------------------------------------------ #

def ratings(D, pool="hs_m", quiet=False):
    """alpha -> ability -> ratings, plus the consistency table and leaderboard."""
    t0 = time.time()
    if D.get("sc") is None:
        alpha, _c = pv.refitAlpha(D["y"], D["delta"], D["course"], D["group"],
                                  D["n_groups"], h=D.get("h"))
        D["alpha"], D["beta"] = alpha, None
    else:
        alpha, beta, _c = pv.refitAlphaBeta(D["y"], D["delta"], D["course"],
                                            D["group"], D["n_groups"],
                                            D["sc"], D.get("ridge", 0.0))
        D["alpha"], D["beta"] = alpha, beta
    attrs = pr.groupAttributes(D["group"], D["athlete"], D["year"],
                               D["n_groups"],
                               pr.poolPerAthlete(D["cols"]["athlete_keys"]))
    rat = pr.buildRatings(alpha, attrs)
    _t("ratings", t0)

    n_pools = max(len(attrs["pool_names"]), 1)
    pmean, _ = pr._meanBy(rat["ability"], attrs["pool"].clip(0), n_pools,
                          rat["valid"])
    race_rating = pr.perRaceRatings(D["norm"], D["delta"], D["course"],
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
    eff = D["delta"][D["course"]]
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

# The per-athlete sport-offset ridge used by --split. See the table in main().
# Chosen by held-out prediction, which is what pair_sportoffset exists to
# measure -- not by taste and not by conditioning.
SPLIT_RIDGE = 0.5


def main(pack_path, write=False, do_golive=False, do_validate=False,
         do_result_table=False, collapse="best", anchor="career",
         pool="hs_m", tilt=False, split=False, split_ridge=SPLIT_RIDGE):
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
        #
        # ⚠⚠ AND THE SWEEP SAYS RIDGE 0 IS NOT THE BEST PREDICTOR. Conditioning
        #    is not the objective; held-out error is. Measured 2026-08-31 by
        #    pair_sportoffset over 5,947,709 held-out rows:
        #
        #        K = inf (shared)   0.047039      --
        #        K = 5             0.045503   -3.27%
        #        K = 1             0.044464   -5.48%
        #        K = 0.5           0.044325   -5.77%   <- best
        #        K = 0.2           0.044349   -5.72%
        #        K = 0 (full)      0.045670   -2.91%
        #
        #    A full split is WORSE than a mild ridge by 3%, because at K = 0
        #    every thin athlete-season gets an unpenalised offset it has no
        #    evidence for. The conditioning argument above is still true --
        #    K = 0.5 needed 368 CG iterations against 245 for the shared fit --
        #    but CG still reached 9e-11, so worse conditioning cost time, not
        #    accuracy. Recentring is unchanged and still required: a ridge
        #    shrinks beta toward zero without forcing its MEAN there, which is
        #    the component that trades against the sport level.
        D["ridge"] = split_ridge
        if D["sc"] is None:
            print("[all] --split asked for but the pack has no sport column")
        else:
            print(f"[all] per-athlete sport offsets ON, ridge "
                  f"{split_ridge:g} (recentred after the solve)")
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
        # ⚠ THE VALIDATION MUST FIT THE MODEL THE RUN FITTED. It used to solve
        #   without the sport offset regardless of --split, so a split run
        #   reported EXACTLY the shared number (0.047039 on 2026-08-31, equal
        #   to pair_sportoffset's K=inf row to six decimals) and looked like
        #   the split had done nothing. It is the same blind spot that got
        #   --tilt wrongly rejected -- apply_tilt.py: "the validation had read
        #   an untilted cached solve". A validation that silently scores a
        #   different model is worse than no validation, because it is
        #   believed.
        sc_all = D.get("sc")
        if sc_all is not None:
            sc_tr, sc_te = sc_all[~te], sc_all[te]
            T = pv.solveSubset(D["course"][~te], D["group"][~te], D["y"][~te],
                               D["n_cells"], D["n_groups"],
                               sc=sc_tr, ridge=D.get("ridge", 0.0))
            a, b, gc = pv.refitAlphaBeta(D["y"][~te], T["delta"],
                                         D["course"][~te], D["group"][~te],
                                         D["n_groups"], sc_tr,
                                         D.get("ridge", 0.0))
            pv.evaluateHoldout(T["delta"], a, gc, D["y"][te], D["course"][te],
                               D["group"][te], T["degree"] >= 2,
                               f"pair/split", beta=b, sc_te=sc_te)
        else:
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
         split_ridge=float(opt("split-ridge", SPLIT_RIDGE)),
         collapse=opt("collapse", "best"),
         anchor=opt("anchor", "career"),
         pool=opt("pool", "hs_m"))