"""
joint_golive.py -- turn a joint fit into the tables the site reads.

    course_difficulties      one row per solved cell, difficulty = expm1(delta)
                             anchored to the row-weighted mean (display; the
                             site's difficulty_view re-expresses it per sport)
    athlete_ratings          one row per (person, pool): the season rating of
                             the collapsed season (best by default)
    results.speed_rating /   one rating per result row, the number a race
    results_tf.speed_rating  page shows
    engine/data/pair_difficulty.npz
                             the shape every diagnostic reads, so
                             check_phase_year, ability_slope, pair_report and
                             the rest keep working off the joint solve

★ WHAT A RATING IS, UNCHANGED:   rating = 100 * pool_mean / adjusted
  with adjusted = normalized_time / exp(effect). The definitions are the
  sequential engine's (pair_ratings.buildRatings, pair_write_results), so a
  season rating and a race rating stay on one gauge: the abilities carry the
  reference level (XC, mu[XC] = 0) and the per-row effect uses the SAME raw
  delta, not the display-anchored one.

★ WHAT IS IN THE PER-RACE EFFECT, AND WHAT IS NOT.
  IN   h * (mu + d)[cell]   the cell difficulty, tilted at the athlete's own
                            rating -- so apply_tilt must NOT run after this
  IN   u[race]              the race-day effect (default; --no-race-effect
                            drops it). Everyone on the course that day gets
                            the same credit, so finishing order inside a race
                            is untouched; across days at one venue a muddy
                            Saturday no longer reads as a slow field. This is
                            the term the sequential engine did not have.
  OUT  the form curve, the opener rust, beta. All nuisance: a November race
       SHOULD rate higher than a September one if it was better (the owner's
       definition -- skill at that moment), and a runner's specialisation is
       a property of the runner, not of the run (issue 64's monotonicity).

⚠ THE IRREVERSIBLE ONE, like linkage_check.golive. backupSpeedRatings runs
  first and restoreSql prints the undo. Nothing here reads
  data/sport_gap_bbar.json, and nothing downstream should apply a tilt.
"""

import os

import numpy as np

import pair_ratings as pr
from pair_write_results import poolMeanPerGroup, ratedMask


# buildLive
# Purpose:   every array the writers need, from a joint fit. PURE: no database.
# Arguments: out -- solveJoint's output; D -- the Design it was fitted on;
#            cols, keep -- the pack and its row mask; anchor -- "career" or
#            "seasonal" for the per-result rating; use_race_effect.
# Output:    dict: diffs (course_difficulties dict), athletes (the
#            saveAthleteRatings dict), per-sport (result_id, rating) pairs,
#            the pair_difficulty-shaped arrays, and a summary.
def buildLive(out, D, cols, keep, collapse="best", anchor="career",
              use_race_effect=True):
    import pair_golive as pg

    keys = [str(k) for k in cols["course_keys"]]
    athlete_raw = np.asarray(cols["athlete"][keep])
    year = np.asarray(cols["year"][keep])
    norm = np.asarray(cols["norm"][keep], dtype=np.float64)
    result_id = np.asarray(cols["result_id"][keep])
    sport = np.asarray(cols["sport"][keep])

    # --- season ratings, exactly as the sequential path builds them ------ #
    attrs = pr.groupAttributes(D.athlete, athlete_raw, year, D.n_ath,
                               pr.poolPerAthlete(cols["athlete_keys"]))
    rat = pr.buildRatings(out["ability"], attrs)

    # --- per-result ratings, both anchors -------------------------------- #
    pm_c, pm_s = poolMeanPerGroup(rat["ability"], attrs, rat["valid"],
                                  anchor=rat.get("anchor"))
    eff = out["h"] * out["delta"][D.cell]
    if use_race_effect:
        eff = eff + out["race_effect"][D.race]
    # ★ THE TRACK DISTANCE OFFSET IS IN THE RATING (issue 148): it corrects
    #   the normalisation the row arrived with, exactly as the cell corrects
    #   the course. Untilted. XC rows carry none (e_w = 0).
    if out.get("dist_offset") is not None and getattr(D, "n_e", 0):
        eff = eff + D.e_w * out["dist_offset"][D.e_idx]
    adjusted = norm / np.exp(eff)
    with np.errstate(divide="ignore", invalid="ignore"):
        rc = 100.0 * pm_c[D.athlete] / adjusted
        rs = 100.0 * pm_s[D.athlete] / adjusted
    rows_per_cell = np.bincount(D.cell, minlength=D.n_cell)
    solved = rows_per_cell > 0
    rated = ratedMask(rc, rs, D.athlete, rat["valid"], cell_solved=solved,
                      course=D.cell)
    chosen = rs if anchor == "seasonal" else rc

    # --- the difficulty, display-anchored ---------------------------------- #
    w = rows_per_cell.astype(np.float64)
    raw = out["delta"]
    anchored = raw - np.average(raw[solved], weights=w[solved])
    difficulty = np.where(solved, np.expm1(anchored), 0.0)
    diffs = pg.buildDifficultyDict(keys, difficulty, D.cell, athlete_raw,
                                   solved)

    # --- athlete_ratings -------------------------------------------------- #
    r = {"valid": rat["valid"],
         "person_id": np.array([str(cols["athlete_keys"][a][0])
                                for a in attrs["athlete"]]),
         "pool": np.array([attrs["pool_names"][c] if c >= 0 else ""
                           for c in attrs["pool"]]),
         "season": attrs["season"], "races": attrs["races"],
         "rating_seasonal": rat["seasonal"]}
    athletes = pg.buildAthleteDict(r, collapse)

    per_sport = {}
    for code, name in ((0, "XC"), (1, "TF")):
        m = rated & (sport == code)
        if m.any():
            per_sport[name] = (result_id[m], np.round(chosen[m], 2))

    # --- the diagnostics' file shape --------------------------------------- #
    degree = np.bincount(D.cell, minlength=D.n_cell)     # rows, not days
    npz = dict(difficulty=difficulty, difficulty_raw=np.expm1(raw),
               solved=solved, degree=degree,
               weight=np.where(out["cell_var"] > 0,
                               out["tau2"][D.group_of_cell]
                               / (out["tau2"][D.group_of_cell]
                                  + out["cell_var"]), 0.0),
               cell_var=out["cell_var"], course_keys=np.array(keys),
               joint=np.array([1]), mu=out["mu"],
               race_effect_in_rating=np.array([int(use_race_effect)]))

    dist_rows = []
    if out.get("dist_offset") is not None and getattr(D, "n_e", 0):
        n_e_rows = np.bincount(D.e_idx, weights=D.e_w, minlength=D.n_e)
        for i, lab in enumerate(getattr(D, "dist_labels", [])):
            p, d = lab.rsplit(":", 1)
            dist_rows.append((p, "TF", int(d), float(out["dist_offset"][i]),
                              int(n_e_rows[i])))
        for p, d in getattr(D, "dist_refs", {}).items():
            dist_rows.append((p, "TF", int(d), 0.0, 0))

    u_pts = (130.0 * (np.exp(np.abs(out["race_effect"][D.race][rated])) - 1)
             if use_race_effect else np.zeros(1))
    summary = {
        "n_rated": int(rated.sum()), "n_rows": int(rated.size),
        "n_cells": int(solved.sum()), "n_athletes": len(athletes),
        "n_seasons_valid": int(rat["valid"].sum()),
        "race_effect_points_at_130_median": float(np.median(u_pts)),
        "race_effect_points_at_130_p95": float(np.percentile(u_pts, 95)),
    }
    return {"diffs": diffs, "athletes": athletes, "per_sport": per_sport,
            "npz": npz, "summary": summary, "rated": rated, "chosen": chosen,
            "r_career": rc, "r_seasonal": rs, "attrs": attrs, "rat": rat,
            "dist_rows": dist_rows}


# The fitted track distance offsets, for the readers that do not run the
# solve: the conversions page, distance_bake (to fold into the pickle so the
# backfill and every reader carry it), diag scripts. One row per (pool,
# sport, distance); log_offset 0 on the pinned reference event.
_DIST_DDL = """
    CREATE TABLE IF NOT EXISTS distance_offset (
        pool         text    NOT NULL,
        sport        text    NOT NULL,
        distance_m   integer NOT NULL,
        log_offset   real    NOT NULL,
        n_rows       bigint  NOT NULL,
        last_updated text,
        PRIMARY KEY (pool, sport, distance_m)
    )
"""


def writeDistOffsets(rows):
    from datetime import date
    from database import getConn
    if not rows:
        print("[joint/live] distance_offset: nothing to write (block off)")
        return
    today = date.today().isoformat()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_DIST_DDL)
        cur.execute("DELETE FROM distance_offset")
        cur.executemany(
            "INSERT INTO distance_offset (pool, sport, distance_m, log_offset,"
            " n_rows, last_updated) VALUES (%s, %s, %s, %s, %s, %s)",
            [(p, s, d, v, n, today) for p, s, d, v, n in rows])
        conn.commit()
    print(f"[joint/live] distance_offset: {len(rows):,} rows written")


def report(live):
    s = live["summary"]
    print(f"[joint/live] {s['n_rated']:,}/{s['n_rows']:,} rows rated "
          f"({100.0 * s['n_rated'] / max(s['n_rows'], 1):.1f}%), "
          f"{s['n_cells']:,} cells, {s['n_athletes']:,} athlete rows, "
          f"{s['n_seasons_valid']:,} rated athlete-seasons")
    print(f"[joint/live] race-day effect in the rating: median "
          f"{s['race_effect_points_at_130_median']:.2f} points at 130, "
          f"p95 {s['race_effect_points_at_130_p95']:.2f}")


def writeNpz(live, path):
    np.savez(path, **live["npz"])
    print(f"[joint/live] wrote {path} (pair_difficulty shape, from the joint "
          f"solve)")


def writeLive(live):
    """⚠ Writes course_difficulties, athlete_ratings and results.speed_rating."""
    import pair_golive as pg
    from database import getConn
    from speed_ratings_db import (saveCourseDifficulties, saveAthleteRatings,
                                  saveResultSpeedRatings)

    with getConn() as conn:
        pg.backupSpeedRatings(conn)

    print(f"[joint/live] course_difficulties: {len(live['diffs']):,} cells")
    saveCourseDifficulties(live["diffs"], ("XC", "TF"))

    print(f"[joint/live] athlete_ratings: {len(live['athletes']):,} rows")
    saveAthleteRatings(live["athletes"], ("XC", "TF"))

    for name, (rid, rating) in live["per_sport"].items():
        saveResultSpeedRatings(name, (rid, rating))
        print(f"[joint/live] {name}: {rid.size:,} result ratings written")
    writeDistOffsets(live.get("dist_rows", []))
    print("\n[joint/live] LIVE. To undo:\n" + pg.restoreSql())
    print("[joint/live] ⚠ do NOT run apply_tilt after this: the tilt is "
          "inside these ratings already.")
