# Project: xc-predictor
# Subset:  Pair Engine -- GO LIVE
#
# Everything the safe writers deliberately leave undone:
#
#   1. BACK UP results.speed_rating and results_tf.speed_rating.
#   2. COMPARE the pair engine against what is currently live.
#   3. WRITE course_difficulties  (was ALS)
#   4. WRITE athlete_ratings      (was ALS)
#   5. MERGE results.speed_rating (was ALS)
#
# ★ ALL THREE OR NONE. pair_write and pair_write_results write only pair_*
#   tables, so running them leaves results.speed_rating on the ALS engine while
#   nothing else moves. Doing ONLY the merge would be worse: a race page would
#   show a pair-engine number for an athlete whose profile still carried an ALS
#   rating, and rust_fitness.ratingPerAthlete would then read ALS ratings to
#   correct pair-engine data on the next run. This script moves all three
#   together.
#
# ★ THE EXISTING WRITERS ARE CALLED, NOT REIMPLEMENTED. saveCourseDifficulties
#   already does the canonical_id -> "XC:<name>" translation via _splitVenueKey
#   and loadCanonicalNames; saveAthleteRatings already knows the merged-vs-per-
#   sport DELETE rule; saveResultSpeedRatings already owns the staging table and
#   the heap rebuild. Writing that SQL again would be a second source of truth
#   for the most destructive operation in the project.
#
# ⚠ DRY RUN BY DEFAULT. Nothing is written without --yes. The dry run does the
#   full solve and prints exactly what would change, which is also the only way
#   to see the comparison before committing to it.

import os
import sys

import numpy as np

import pair_engine as pe
import pair_validate as pv
import pair_ratings as pr

_BACKUP = {"results": "results_speed_rating_backup",
           "results_tf": "results_tf_speed_rating_backup"}
_SPORT_TABLE = {0: "results", 1: "results_tf"}
_SPORT_NAME = {0: "XC", 1: "TF"}


# ------------------------------------------------------------------ #
# CHUNK 1 -- BACKUP
# ------------------------------------------------------------------ #

# backupSpeedRatings
# Purpose:   a restorable copy of the column about to be overwritten.
# Arguments: conn; force -- rebuild an existing backup rather than keeping it.
#
# ★ WHY THIS IS NOT OPTIONAL. mergeColumn leaves <table>_old behind and then
#   saveResultSpeedRatings DROPS it on success -- its own comment says
#   "⚠ THIS REMOVES THE ONLY ROLLBACK." Two columns over 61M rows costs a few
#   minutes and buys back reversibility.
#
# An existing backup is KEPT unless --force-backup: if a previous go-live went
# wrong, the backup from BEFORE that run is the one worth having, not a copy of
# the damage.
def backupSpeedRatings(conn, force=False):
    with conn.cursor() as cur:
        for table, backup in _BACKUP.items():
            cur.execute("SELECT to_regclass(%s)", (backup,))
            exists = cur.fetchone()[0] is not None
            if exists and not force:
                cur.execute(f"SELECT count(*) FROM {backup}")
                print(f"    {backup}: already exists "
                      f"({cur.fetchone()[0]:,} rows) -- kept")
                continue
            cur.execute(f"DROP TABLE IF EXISTS {backup}")
            cur.execute(f"CREATE TABLE {backup} AS SELECT result_id, "
                        f"speed_rating FROM {table} "
                        f"WHERE speed_rating IS NOT NULL")
            cur.execute(f"CREATE INDEX ON {backup} (result_id)")
            cur.execute(f"SELECT count(*) FROM {backup}")
            print(f"    {backup}: {cur.fetchone()[0]:,} rows saved")
    conn.commit()


# restoreSql
# Purpose:   the exact statements that undo a merge, printed so they exist
#            somewhere other than my memory.
def restoreSql():
    lines = []
    for table, backup in _BACKUP.items():
        lines.append(
            f"UPDATE {table} r SET speed_rating = b.speed_rating\n"
            f"  FROM {backup} b WHERE b.result_id = r.result_id;")
    return "\n".join(lines)


# ------------------------------------------------------------------ #
# CHUNK 2 -- COMPARE
# ------------------------------------------------------------------ #

# compareToLive
# Purpose:   how far the pair engine moves each result, against what is live.
# Arguments: conn.
#
# Reads pair_result_rating, which pair_write_results already wrote. If it is
# absent the comparison is skipped rather than fatal -- the swap does not depend
# on it, but you should not do the swap without having looked.
def compareToLive(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('pair_result_rating')")
        if cur.fetchone()[0] is None:
            print("    pair_result_rating not found -- run "
                  "pair_write_results.py first to see the comparison")
            return
        for code, table in _SPORT_TABLE.items():
            cur.execute(f"""
                SELECT count(*),
                       corr(p.rating_career, r.speed_rating),
                       avg(p.rating_career - r.speed_rating),
                       stddev(p.rating_career - r.speed_rating)
                FROM pair_result_rating p
                JOIN {table} r ON r.result_id = p.result_id
                WHERE p.sport = %s AND r.speed_rating IS NOT NULL
            """, (_SPORT_NAME[code],))
            n, c, mean, sd = cur.fetchone()
            if not n:
                continue
            print(f"    {_SPORT_NAME[code]}: {n:,} shared rows   "
                  f"corr {c:.4f}   mean diff {mean:+.3f}   sd {sd:.3f}")


# ------------------------------------------------------------------ #
# CHUNK 3 -- BUILD WHAT THE EXISTING WRITERS EXPECT
# ------------------------------------------------------------------ #

# buildDifficultyDict
# Purpose:   {course_key: {difficulty, n_results, n_athletes}}, the shape
#            saveCourseDifficulties consumes.
# Arguments: course_keys, difficulty; course, athlete -- per row.
#
# n_athletes counts distinct ATHLETES, not athlete-seasons: the column means "how
# many people raced here", and one person over four years is one person. Encoding
# (cell, athlete) as one int64 and taking unique is cheaper than a groupby at
# 55M rows.
def buildDifficultyDict(course_keys, difficulty, course, athlete, solved):
    n_cells = len(course_keys)
    n_results = np.bincount(course, minlength=n_cells)

    key = course.astype(np.int64) * (int(athlete.max()) + 1) + athlete
    uniq = np.unique(key)
    n_athletes = np.bincount(
        (uniq // (int(athlete.max()) + 1)).astype(np.int64), minlength=n_cells)

    out = {}
    for i, k in enumerate(course_keys):
        if not solved[i]:
            continue
        out[str(k)] = {"difficulty": float(difficulty[i]),
                       "n_results": int(n_results[i]),
                       "n_athletes": int(n_athletes[i])}
    return out


# buildAthleteDict
# Purpose:   {(person_id, pool): {speed_rating, n_races}} for
#            saveAthleteRatings, collapsing season down to one row.
# Arguments: r -- pair_ratings arrays; collapse -- "best"|"recent"|"weighted".
#
# ★ THE COLLAPSE IS A PRODUCT DECISION AND IT IS EXPOSED, NOT BURIED.
#     best     peak season -- closest to what athlete_ratings meant before.
#     recent   latest season -- what a current-form list wants.
#     weighted race-count weighted mean -- most stable, least meaningful for an
#              athlete who improved a lot.
#   The season-grain table pair_athlete_season keeps everything, so this
#   collapse is recoverable.
def buildAthleteDict(r, collapse="best"):
    valid = r["valid"].astype(bool)
    pid = r["person_id"].astype(str)
    pool = r["pool"].astype(str)
    season = r["season"].astype(np.int64)
    races = r["races"].astype(np.int64)
    rating = r["rating_seasonal"]

    best = {}
    for i in np.nonzero(valid)[0]:
        k = (pid[i], pool[i])
        cur = best.get(k)
        if collapse == "recent":
            score = season[i]
        elif collapse == "weighted":
            score = None                       # handled below
        else:
            score = rating[i]
        if collapse == "weighted":
            acc = best.setdefault(k, {"num": 0.0, "den": 0.0, "n": 0})
            acc["num"] += rating[i] * races[i]
            acc["den"] += races[i]
            acc["n"] += races[i]
        elif cur is None or score > cur["score"]:
            best[k] = {"score": float(score),
                       "speed_rating": float(rating[i]),
                       "n_races": int(races[i])}

    if collapse == "weighted":
        return {k: {"speed_rating": v["num"] / max(v["den"], 1e-9),
                    "n_races": int(v["n"])}
                for k, v in best.items()}
    return {k: {"speed_rating": v["speed_rating"], "n_races": v["n_races"]}
            for k, v in best.items()}


# ------------------------------------------------------------------ #
# CHUNK 4 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(pack_path, ratings_path, live=False, collapse="best",
         force_backup=False, anchor="career"):
    from database import getConn

    # ---- solve once; every table below comes from this one fit ------
    cols = pe.loadPack(pack_path)
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    y_all, _form = pe.buildResponse(cols)
    course = cols["course"][keep].astype(np.int64)
    y, norm = y_all[keep], cols["norm"][keep]
    athlete, year = cols["athlete"][keep], cols["year"][keep]
    result_id, sport = cols["result_id"][keep], cols["sport"][keep]
    group, n_groups = pe.athleteSeasonCodes(athlete, year)
    n_cells = len(cols["course_keys"])

    print(f"[live] {y.size:,} rows, {n_groups:,} athlete-seasons")
    T = pv.solveSubset(course, group, y, n_cells, n_groups, quiet=False)
    delta, solved = T["delta"], T["degree"] >= 2

    alpha, _c = pv.refitAlpha(y, delta, course, group, n_groups)
    attrs = pr.groupAttributes(group, athlete, year, n_groups,
                               pr.poolPerAthlete(cols["athlete_keys"]))
    rat = pr.buildRatings(alpha, attrs)

    from pair_write_results import poolMeanPerGroup, ratedMask
    pm_c, pm_s = poolMeanPerGroup(rat["ability"], attrs, rat["valid"])
    r_career = pr.perRaceRatings(norm, delta, course, group, pm_c)
    r_seasonal = pr.perRaceRatings(norm, delta, course, group, pm_s)
    ok = ratedMask(r_career, r_seasonal, group, rat["valid"])
    chosen = r_seasonal if anchor == "seasonal" else r_career

    # ---- the payloads ----------------------------------------------
    diffs = buildDifficultyDict(cols["course_keys"], np.expm1(delta),
                                course, athlete, solved)
    r = dict(np.load(ratings_path, allow_pickle=False)) \
        if os.path.exists(ratings_path) else None
    if r is None:
        print(f"[live] {ratings_path} missing -- run pair_ratings.py first")
        return
    ath = buildAthleteDict(r, collapse)

    print(f"\n[live] would write:")
    print(f"    course_difficulties : {len(diffs):,} cells")
    print(f"    athlete_ratings     : {len(ath):,} rows "
          f"(collapse='{collapse}')")
    print(f"    results.speed_rating: {int(ok.sum()):,} rows "
          f"(anchor='{anchor}')")

    with getConn() as conn:
        print("\n[live] backup:")
        backupSpeedRatings(conn, force=force_backup)
        print("\n[live] pair vs live, per result:")
        compareToLive(conn)

    if not live:
        print("\n[live] DRY RUN -- nothing written. Re-run with --yes to swap.")
        print("       To undo a swap afterwards:\n")
        print(restoreSql())
        return

    # ---- the swap ---------------------------------------------------
    from speed_ratings_db import (saveCourseDifficulties, saveAthleteRatings,
                                  saveResultSpeedRatings)

    print("\n[live] writing course_difficulties...")
    saveCourseDifficulties(diffs, ("XC", "TF"))
    print("[live] writing athlete_ratings...")
    saveAthleteRatings(ath, ("XC", "TF"))

    print("[live] merging results.speed_rating (heap rebuild, ~35 min)...")
    for code, name in _SPORT_NAME.items():
        m = ok & (sport == code)
        if m.any():
            saveResultSpeedRatings(name, (result_id[m],
                                          np.round(chosen[m], 2)))
            print(f"[live] {name}: {int(m.sum()):,} written")

    print("\n[live] LIVE. All three tables now come from the pair engine.")
    print("       To undo:\n")
    print(restoreSql())


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    argv = sys.argv[1:]
    pos = [a for a in argv if not a.startswith("--")]

    def opt(name, default):
        for a in argv:
            if a.startswith(f"--{name}="):
                return a.split("=", 1)[1]
        return default

    main(pos[0] if pos else os.path.join(here, "packed_XC_TF.npz"),
         pos[1] if len(pos) > 1 else os.path.join(here, "pair_ratings.npz"),
         live="--yes" in argv,
         collapse=opt("collapse", "best"),
         force_backup="--force-backup" in argv,
         anchor=opt("anchor", "career"))