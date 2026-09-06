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
import joint_solve as js
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
              use_race_effect=True, gain_bands=None, pack_date=None):
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
    u_row = out["race_effect"][D.race]
    capped = np.abs(u_row) > js.RACE_DAY_CAP
    if use_race_effect:
        # tilted like the course (issue 156): the solve fitted h * u --
        # and CLIPPED for the rating (issue 187): a day beyond the cap is
        # a broken result sheet, not a credit
        eff = eff + out["h"] * np.clip(u_row, -js.RACE_DAY_CAP, js.RACE_DAY_CAP)
    eff_venue = eff.copy()           # the venue's share, for engine_scale (177)
    # ★ THE TRACK DISTANCE OFFSET IS IN THE RATING (issue 148): it corrects
    #   the normalisation the row arrived with, exactly as the cell corrects
    #   the course. Untilted. XC rows carry none (e_w = 0).
    if out.get("dist_offset") is not None and getattr(D, "n_e", 0):
        eff = eff + D.e_w * out["dist_offset"][D.e_idx]
    # the altitude term (issue 172): credit at altitude, for everyone in
    # the race alike -- the venue's km less the field's acclimatisation
    # (issue 192, js.altitudeCredit)
    if out.get("altitude_coef") is not None and getattr(D, "n_k", 0):
        credit = js.altitudeCredit(D)
        eff = eff + out["altitude_coef"][D.group_row] * credit
        at_alt = getattr(D, "alt_venue", D.alt) > 0
        if at_alt.any():
            share = credit[at_alt] / np.maximum(getattr(D, "alt_venue", D.alt)[at_alt], 1e-9)
            print(f"[joint/live] altitude: {int(at_alt.sum()):,} rows at venues "
                  f"above the floor; the field's acclimatisation leaves them "
                  f"a median {100 * float(np.median(share)):.0f}% of the full "
                  f"credit (issue 192)")
    # ★ THE WINTER GAIN PER ABILITY (issue 194): the shift on every track
    #   row that makes the dual-sport page gap in each band the stated one
    gain_rows = []
    if gain_bands is not None:
        n_pool = len(attrs["pool_names"])
        shift, gap, n_g = js.sportGainShift(
            np.log(norm) - eff, sport, D.athlete, rat["career"],
            attrs["pool"], n_pool, gain_bands)
        pool_of_row = attrs["pool"][D.athlete]
        eff = eff + np.where(sport == 1,
                             js.sportGainRow(rat["career"][D.athlete],
                                             pool_of_row, shift), 0.0)
        print("[joint/live] winter gain per band (issue 194), track rows "
              "shifted so the dual-sport page gap per band is the stated "
              f"gain {tuple(float(g) for g in gain_bands)} at ratings "
              f"{js.SPORT_GAIN_ANCHORS}:")
        print(f"    {'pool':<10}{'band':>6}{'athletes':>10}{'gap read':>10}"
              f"{'target':>9}{'shift':>9}")
        for p in range(n_pool):
            for b in range(len(gain_bands)):
                if n_g[p, b] == 0:
                    continue
                gain_rows.append((attrs["pool_names"][p], "TF", b,
                                  float(js.SPORT_GAIN_ANCHORS[b]),
                                  float(gain_bands[b]),
                                  float(gap[p, b]) if np.isfinite(gap[p, b]) else None,
                                  float(shift[p, b]), int(n_g[p, b])))
                print(f"    {attrs['pool_names'][p]:<10}{b:>6}{int(n_g[p, b]):>10,}"
                      f"{gap[p, b]:>+10.4f}{-float(gain_bands[b]):>+9.4f}"
                      f"{shift[p, b]:>+9.4f}")
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

    # ★ THE ENGINE'S SCALE, WRITTEN DOWN (issue 177): the pool mean the
    #   ratings hang on, the median venue effect the rated rows carried
    #   per (pool, sport), and the shift between the raw cell scale and the
    #   displayed one. The conversions page converts on this scale; without
    #   it, a 9:01 converted to a 3200 came back as a 9:30.
    # the days beyond the cap, for the rowguard (issue 187): cell key,
    # the day (days ago from the pack), the term, the rows on it
    suspect_days = []
    if capped.any():
        days_ago = np.asarray(cols["days"][keep])
        for r_idx in np.unique(D.race[capped]):
            m = D.race == r_idx
            c = int(D.cell[m][0])
            suspect_days.append((keys[c], int(np.median(days_ago[m])),
                                 float(out["race_effect"][r_idx]), int(m.sum())))
        suspect_days.sort(key=lambda t: -abs(t[2]))
        print(f"[joint/live] race-day cap {js.RACE_DAY_CAP:+.2f}: "
              f"{len(suspect_days):,} race days beyond it on {int(capped.sum()):,} "
              f"rows; the worst: "
              + "; ".join(f"{k} {u:+.3f} ({n} rows)" for k, _d, u, n in suspect_days[:5]))
    # ★ EVERY RACE DAY'S TERM, FOR THE PAGE (2026-09-06, the owner's hover):
    #   per race (a cell on a day) the solve's day term and its rows; the
    #   writer turns days-ago into a date from the pack's own date.
    day_rows = None
    if "days" in cols:
        days_all = np.asarray(cols["days"][keep])
        race_cell = np.zeros(D.n_race, dtype=np.int64)
        race_cell[D.race] = D.cell
        race_days = np.zeros(D.n_race, dtype=np.int64)
        race_days[D.race] = days_all
        race_n = np.bincount(D.race, minlength=D.n_race)
        seen_r = np.flatnonzero(race_n > 0)
        day_rows = (keys, race_cell[seen_r], race_days[seen_r],
                    out["race_effect"][seen_r].astype(np.float32),
                    race_n[seen_r], pack_date)
    scale_rows = []
    shift = float(np.average(raw[solved], weights=w[solved]))
    pool_row = attrs["pool"][D.athlete]
    pm_row = pm_c[D.athlete]
    for p in np.unique(pool_row):
        if p < 0:
            continue
        for code, name in ((0, "XC"), (1, "TF")):
            m = rated & (pool_row == p) & (sport == code)
            if m.sum() < 500:
                continue
            scale_rows.append((attrs["pool_names"][p], name,
                               float(np.median(pm_row[m])),
                               float(np.median(eff_venue[m])),
                               shift, int(m.sum())))

    dist_rows = []
    if out.get("dist_offset") is not None and getattr(D, "n_e", 0):
        n_e_rows = np.bincount(D.e_idx, weights=D.e_w, minlength=D.n_e)
        for i, lab in enumerate(getattr(D, "dist_labels", [])):
            parts = lab.split(":")
            band = int(parts[2][1:]) if len(parts) > 2 else 1
            dist_rows.append((parts[0], "TF", int(parts[1]), band,
                              float(out["dist_offset"][i]), int(n_e_rows[i])))
        for p, d in getattr(D, "dist_refs", {}).items():
            for band in range(js.DIST_N_BAND if getattr(D, "dist_banded", False) else 1):
                dist_rows.append((p, "TF", int(d), band if getattr(D, "dist_banded", False) else 1, 0.0, 0))

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
            "dist_rows": dist_rows, "scale_rows": scale_rows,
            "suspect_days": suspect_days, "gain_rows": gain_rows,
            "day_rows": day_rows}


_SUSPECT_DDL = """
    CREATE TABLE IF NOT EXISTS race_day_suspect (
        cell_key     text    NOT NULL,
        race_date    date    NOT NULL,
        day_effect   real    NOT NULL,
        n_rows       integer NOT NULL,
        last_updated text,
        PRIMARY KEY (cell_key, race_date)
    )
"""


def writeSuspectDays(rows):
    """The race days whose term exceeded RACE_DAY_CAP: a list for the
    rowguard and for a person -- the result sheet, not the course, is
    what is wrong on those days."""
    from datetime import date, timedelta
    from database import getConn
    today = date.today()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SUSPECT_DDL)
        cur.execute("DELETE FROM race_day_suspect")
        cur.executemany(
            "INSERT INTO race_day_suspect (cell_key, race_date, day_effect, "
            "n_rows, last_updated) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            [(k, today - timedelta(days=d), u, n, today.isoformat())
             for k, d, u, n in rows])
        conn.commit()
    print(f"[joint/live] race_day_suspect: {len(rows):,} days written")


# The race-day term of every race the solve saw, keyed the way the site
# resolves a cell (course_difficulties' columns) plus the date, so an
# athlete's row or a race page can show "course +4.6%, that day -1.2%".
_DAY_DDL = """
    CREATE TABLE race_day_effect (
        course_name   text    NOT NULL,
        canonical_id  bigint,
        distance_m    integer,
        race_date     date    NOT NULL,
        day_effect    real    NOT NULL,
        n_rows        integer NOT NULL
    )
"""


def writeRaceDays(day_rows):
    """Replace race_day_effect from buildLive's day_rows: (cell keys, the
    cell per race, days ago per race, the term, rows, the pack's date)."""
    from datetime import date, timedelta
    from database import getConn
    from speed_ratings_db import (_splitVenueKey, _escape, _copyInto,
                                  loadCanonicalNames)
    keys, cell, days, u, n, pack_date = day_rows
    pack_date = pack_date or date.today()
    names = loadCanonicalNames()
    split = {}
    rows = []
    for c, d, uu, nn in zip(cell.tolist(), days.tolist(), u.tolist(), n.tolist()):
        if c not in split:
            split[c] = _splitVenueKey(keys[c], names)
        name, cid, dist = split[c]
        rows.append((_escape(name), cid, dist, (pack_date - timedelta(days=int(d))).isoformat(),
                     round(float(uu), 5), int(nn)))
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS race_day_effect_new")
        cur.execute(_DAY_DDL.replace("race_day_effect", "race_day_effect_new"))
        total = _copyInto(cur, "race_day_effect_new",
                          ("course_name", "canonical_id", "distance_m",
                           "race_date", "day_effect", "n_rows"), rows)
        cur.execute("CREATE INDEX ON race_day_effect_new (canonical_id, distance_m, race_date)")
        cur.execute("CREATE INDEX ON race_day_effect_new (course_name, race_date)")
        cur.execute("DROP TABLE IF EXISTS race_day_effect")
        cur.execute("ALTER TABLE race_day_effect_new RENAME TO race_day_effect")
        conn.commit()
    print(f"[joint/live] race_day_effect: {total:,} race days written "
          f"(dates from the pack of {pack_date})")


_SCALE_DDL = """
    CREATE TABLE IF NOT EXISTS engine_scale (
        pool           text NOT NULL,
        sport          text NOT NULL,
        pool_mean      real NOT NULL,
        median_effect  real NOT NULL,
        anchor_shift   real NOT NULL,
        n_rows         bigint NOT NULL,
        last_updated   text,
        PRIMARY KEY (pool, sport)
    )
"""


def writeEngineScale(rows):
    from datetime import date
    from database import getConn
    if not rows:
        return
    today = date.today().isoformat()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SCALE_DDL)
        cur.execute("DELETE FROM engine_scale")
        cur.executemany(
            "INSERT INTO engine_scale (pool, sport, pool_mean, median_effect,"
            " anchor_shift, n_rows, last_updated) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(p, s, pm, me, sh, n, today) for p, s, pm, me, sh, n in rows])
        conn.commit()
    print(f"[joint/live] engine_scale: {len(rows):,} (pool, sport) rows")


# The fitted track distance offsets, for the readers that do not run the
# solve: the conversions page, distance_bake (to fold into the pickle so the
# backfill and every reader carry it), diag scripts. One row per (pool,
# sport, distance); log_offset 0 on the pinned reference event.
_DIST_DDL = """
    CREATE TABLE IF NOT EXISTS distance_offset (
        pool         text    NOT NULL,
        sport        text    NOT NULL,
        distance_m   integer NOT NULL,
        band         integer NOT NULL DEFAULT 1,
        log_offset   real    NOT NULL,
        n_rows       bigint  NOT NULL,
        last_updated text,
        PRIMARY KEY (pool, sport, distance_m, band)
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
        # the table predates the band column (2026-09-04): rebuild it
        cur.execute("DROP TABLE IF EXISTS distance_offset")
        cur.execute(_DIST_DDL)
        cur.executemany(
            "INSERT INTO distance_offset (pool, sport, distance_m, band,"
            " log_offset, n_rows, last_updated) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(p, s, d, b, v, n, today) for p, s, d, b, v, n in rows])
        conn.commit()
    print(f"[joint/live] distance_offset: {len(rows):,} rows written")


# The stated winter gain per band and the shift the track rows carry
# (issue 194), for the conversions page: one row per (pool, band).
_GAIN_DDL = """
    CREATE TABLE IF NOT EXISTS sport_gain (
        pool           text    NOT NULL,
        sport          text    NOT NULL,
        band           integer NOT NULL,
        anchor_rating  real    NOT NULL,
        target_gain    real    NOT NULL,
        realised_gap   real,
        log_shift      real    NOT NULL,
        n_athletes     integer NOT NULL,
        last_updated   text,
        PRIMARY KEY (pool, sport, band)
    )
"""


def writeSportGain(rows):
    """Replace sport_gain. An empty list clears it: no shift is applied,
    and the conversions page applies none."""
    from datetime import date
    from database import getConn
    today = date.today().isoformat()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_GAIN_DDL)
        cur.execute("DELETE FROM sport_gain")
        if rows:
            cur.executemany(
                "INSERT INTO sport_gain (pool, sport, band, anchor_rating,"
                " target_gain, realised_gap, log_shift, n_athletes,"
                " last_updated) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [tuple(r) + (today,) for r in rows])
        conn.commit()
    print(f"[joint/live] sport_gain: {len(rows):,} rows written")


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
    writeEngineScale(live.get("scale_rows", []))
    writeSuspectDays(live.get("suspect_days", []))
    writeSportGain(live.get("gain_rows", []))
    if live.get("day_rows") is not None:
        writeRaceDays(live["day_rows"])
    print("\n[joint/live] LIVE. To undo:\n" + pg.restoreSql())
    print("[joint/live] ⚠ do NOT run apply_tilt after this: the tilt is "
          "inside these ratings already.")
