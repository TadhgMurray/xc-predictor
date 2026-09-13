"""
bracket_engine.py -- course difficulty the owner's way, as a second solve.

★ THE IDEA (owner, 2026-09-12). "Look at normalized time at the top x% at
  a race. For each person, compare it to what they ran the past couple of
  weeks and next couple of weeks. That's how hard the course is, after you
  account for fitness. Courses split every couple of years so one tactical
  race doesn't ... it up." It is Tully Runners' method, and Tully's numbers
  for NXN against Balboa are the most credible external ones we have.

★ THE MODEL.  z_i = ln(norm_i) - curve_i, the form curve from the joint
  solve's file taken out of every row ("use the fitness curve to make 21
  days more clean"). For row i of athlete-season s at race r on cell c(r):

      z_i  =  a_s(near day_i)  +  h_i * D[c(r)]  +  noise

  a_s(near day_i) is the athlete's own level at that time, read off their
  OTHER rows within +-window days in the same sport, each less the
  difficulty of the course it was run on. h_i is the tilt (a fast runner
  pays less of a course), from the joint file's rating when present.
  Every race's difficulty is the mean of (z_i - a_s) / h_i over its voters,
  the top fraction of its field by rating; a (course, era) cell is the
  vote-weighted mean of its races, pulled toward the course's all-era
  mean by `prior_rows` votes (20: an era with 30 votes keeps 60% of its
  own number, one with 200 keeps 90%) so a two-race era is not one
  strange race. On the planted era world the engine follows a course
  that changed as far as the joint solve does at that pull.

  The reference races have their own difficulties, so this is a fixed
  point: iterate D -> a_s -> D with damping until it stops moving. That
  is the whole solve. No race-day term, no field term, no taper.

★ WHAT IT IS FOR. A second engine scored on the SAME held-out races as the
  joint model (scripts/bracket_holdout.py), so the two are compared with a
  number. predict() gives a held-out row's ln(norm) as a_s + h * D + curve.
"""
import numpy as np

import bracket as bk
import joint_solve as js
import pair_engine as pe
import run_joint as rj


# ★ A RACE IS THE UNIT OF EVIDENCE, NOT A ROW (owner, 2026-09-13: "a 10
#   result venue should not have +17% difficulty"). Ten finishers on one
#   day are one reading of the course with ten witnesses: they share the
#   day's weather, field and tactics. So a race's weight saturates in its
#   voters -- n / (n + RACE_SAT) -- and a race of two hundred counts about
#   one, not twenty times a race of ten. The priors are then in RACES:
#     PRIOR_GROUP  pulls a COURSE toward the average course of its sport
#                  and era (zero, where the level is pinned) with the
#                  weight of one race: a course seen once keeps about half
#                  of what that day showed, seen three times about 75%,
#                  seen ten times 90%. That is tau against the race-day
#                  sd when the two are alike, which on this corpus they are.
#     PRIOR_RACES  pulls an ERA of a course toward the course's own
#                  (shrunk) history with the weight of two races, so one
#                  tactical race does not move an era but a season of them
#                  does.
#   Both are stated, printed, and cheap to change; scripts/bracket_holdout.py
#   prints the held-out error by the number of races behind a cell, which
#   is where a wrong prior shows.
RACE_SAT = 5.0
PRIOR_GROUP = 1.0
PRIOR_RACES = 2.0


def fit(cols, npz=None, train=None, window=21, top=0.5, era_years=0,
        n_iter=60, damping=0.5, prior_races=PRIOR_RACES, prior_group=PRIOR_GROUP,
        race_sat=RACE_SAT, min_voters=5, tilt=True, use_curve=True, tol=1e-5,
        verbose=False, codes=None, prior_rows=None):
    """Fit on the rows where `train` is True (all rows when None); every
    row, held out or not, gets its local level and a prediction.

    prior_races / prior_group / race_sat: see the note above; prior_rows
    is the old name of prior_races and still accepted.

    codes: bracket.packCodes' codes, with the pack carrying `_season`,
    `_race`, `_cell` numbered over the WHOLE pack. Then `cols` may be any
    subset of rows (the holdout's athlete sample) and its ratings, races
    and cells still line up with the solve file; without codes they are
    computed here over the rows given."""
    if prior_rows is not None:
        prior_races = float(prior_rows)
    keys = [str(k) for k in cols["course_keys"]]
    n_base = len(keys)
    course = np.asarray(cols["course"]).astype(np.int64)
    days = np.round(np.asarray(cols["days"], dtype=np.float64)).astype(np.int64)
    year = np.asarray(cols["year"]).astype(np.int64)
    n = course.size
    sport = (np.asarray(cols["sport"]).astype(np.int64) if "sport" in cols
             else np.zeros(n, dtype=np.int64))
    train = np.ones(n, dtype=bool) if train is None else np.asarray(train, dtype=bool)
    ath_raw = np.asarray(cols["athlete"]).astype(np.int64)
    pool_of_raw = None
    if codes is not None and "_cell" in cols:
        cell = np.asarray(cols["_cell"]).astype(np.int64)
        n_cell = int(codes["n_cell"])
        cell_keys = [str(k) for k in codes["cell_keys"]]
        base_of_cell = np.asarray(codes["base_of_cell"], dtype=np.int64)
        season = np.asarray(cols["_season"]).astype(np.int64)
        n_season = int(codes["n_season"])
        race = np.asarray(cols["_race"]).astype(np.int64)
        n_race = int(codes["n_race"])
        pool_of_raw = codes.get("pool_of_raw")
        era_years = int(codes.get("era_years", era_years) or 0)
    else:
        # cells: (course, era) under era_years, as the joint solve keys them
        if era_years:
            (cell, n_cell, _g, cell_keys, _p, _w, _e) = rj.eraCells(
                course, year, era_years, n_base, np.zeros(n_base, dtype=np.int64), keys)
        else:
            cell, n_cell, cell_keys = course, n_base, list(keys)
        cell_keys = [str(k) for k in cell_keys]
        # ! A DICT, NOT list.index: 220k era cells against 74k keys by linear
        #   scan is eight billion string compares (2026-09-12)
        key_to_base = {k: i for i, k in enumerate(keys)}
        base_of_cell = (np.array([key_to_base[k.rpartition("@e")[0]] for k in cell_keys],
                                 dtype=np.int64) if era_years else np.arange(n_cell))
        season, n_season = pe.athleteSeasonCodes(ath_raw, year)
        race, n_race = rj.raceCodes(course, days)
    ln = np.log(np.asarray(cols["norm"], dtype=np.float64))
    # the athlete's rating (for the voters and the tilt) and curve point
    rating = None
    if npz is not None and "rating" in npz and np.asarray(npz["rating"]).size == n_season:
        rating = np.asarray(npz["rating"], dtype=np.float64)[season]
    curve = np.zeros(n)
    if use_curve and npz is not None and "curve" in npz and "doy" in cols:
        if pool_of_raw is None:
            pool_of_raw, _names = rj.poolCodes(cols["athlete_keys"])
        curve = bk.curveOnRows(npz, pool_of_raw[ath_raw], cols["doy"], rating)
    z = ln - curve
    h = np.ones(n)
    if tilt and rating is not None:
        r_clip = np.clip(np.nan_to_num(rating, nan=100.0), js.TILT_RATING_LO,
                         js.TILT_RATING_HI)
        h = 1.0 + js.TILT_K * (r_clip - 100.0) / 10.0
    valid = (course >= 0) & np.isfinite(z)
    ref = valid & train
    # the voters: the top fraction of each race's TRAINING field
    if rating is not None:
        strength = -np.nan_to_num(rating, nan=100.0)             # smaller = faster
    else:
        cnt_s = np.bincount(season[ref], minlength=n_season)
        mean_z = np.bincount(season[ref], weights=z[ref], minlength=n_season) / np.maximum(cnt_s, 1)
        strength = mean_z[season]
    voters = np.zeros(n, dtype=bool)
    if top >= 1.0:
        voters = ref.copy()
    else:
        idx_ref = np.flatnonzero(ref)
        order = np.lexsort((strength[idx_ref], race[idx_ref]))
        rs = race[idx_ref][order]
        starts = np.flatnonzero(np.r_[True, rs[1:] != rs[:-1]])
        lengths = np.diff(np.r_[starts, rs.size])
        pos = np.arange(rs.size) - np.repeat(starts, lengths)
        size = np.repeat(lengths, lengths)
        take = pos < np.ceil(top * size)
        voters[idx_ref[order][take]] = True
    # the window over the athlete-season's training rows, for every valid row
    key = season * 2 + np.clip(sport, 0, 1)
    q = np.flatnonzero(valid)
    W = bk.WindowIndex(key[ref], days[ref], key[q], days[q], window)
    self_ref = ref[q]                       # a query that is also a reference
    n_other = W.count - self_ref.astype(np.int64)
    has = n_other > 0
    D = np.zeros(n_cell)
    votes_race = np.bincount(race[voters], minlength=n_race).astype(np.float64)
    race_cell = np.zeros(n_race, dtype=np.int64)
    race_cell[race[valid]] = cell[valid]
    a_local = np.full(n, np.nan)
    # ! THE LEVEL IS PINNED PER SPORT AND ERA, AS mu AND tau PIN IT IN THE
    #   JOINT SOLVE. Every difficulty of an era up by c and every athlete's
    #   level in those years down by c is invisible to the rows (references
    #   are within a season), so the iteration would wander along it; the
    #   vote-weighted mean of D per (sport, era) is held at zero each pass.
    #   It asserts that the average course is as hard in one era as another.
    cell_sport = np.array([1 if k.startswith("TF:") else 0 for k in cell_keys],
                          dtype=np.int64)
    cell_era = np.array([int(k.rpartition("@e")[2]) if "@e" in k else 0
                         for k in cell_keys], dtype=np.int64)
    cell_group = cell_sport * 100_000 + cell_era
    for it in range(n_iter):
        v = z - h * D[np.maximum(cell, 0)]
        s, _c = W.sums(v[ref])
        s = s - np.where(self_ref, v[q], 0.0)
        a_q = np.where(has, s / np.maximum(n_other, 1), np.nan)
        a_local[:] = np.nan
        a_local[q] = a_q
        # each race's difficulty from its voters with a level to compare to
        vote = voters & np.isfinite(a_local)
        r_i = (z - a_local) / h
        num = np.bincount(race[vote], weights=r_i[vote], minlength=n_race)
        cnt = np.bincount(race[vote], minlength=n_race).astype(np.float64)
        ok_race = cnt >= min_voters
        D_race = np.where(ok_race, num / np.maximum(cnt, 1), 0.0)
        # a race's weight saturates in its voters: one reading, many witnesses
        w_race = np.where(ok_race, cnt / (cnt + race_sat), 0.0)
        # the course's history, shrunk toward the average course by one
        # race's worth of prior; then each era cell pulled toward that
        num_c = np.bincount(race_cell, weights=w_race * D_race, minlength=n_cell)
        w_c = np.bincount(race_cell, weights=w_race, minlength=n_cell)
        num_b = np.bincount(base_of_cell, weights=num_c, minlength=n_base)
        w_b = np.bincount(base_of_cell, weights=w_c, minlength=n_base)
        D_base = np.where(w_b > 0, num_b / np.maximum(w_b + prior_group, 1e-9), 0.0)
        D_new = np.where(w_c + prior_races > 0,
                         (num_c + prior_races * D_base[base_of_cell]) / (w_c + prior_races),
                         0.0)
        D_new = np.where(w_b[base_of_cell] > 0, D_new, 0.0)
        for g in np.unique(cell_group[w_c > 0]):
            m_g = (cell_group == g) & (w_c > 0)
            D_new[m_g] -= np.average(D_new[m_g], weights=w_c[m_g])
        # ! DAMPING 0.5, NOT 1.0 (corpus, 2026-09-12: "max change 0.161" from
        #   pass 4 to pass 30, never converging). Two cells whose runners'
        #   only other races are at each other form an island: D_A = c + D_B
        #   and D_B = D_A - c, so a full step swaps them back and forth for
        #   ever, period two. A half step keeps the island's sum where it
        #   started (the group mean, its prior) and lets the difference
        #   settle. The step is judged on cells with votes, and how many
        #   are still moving is printed, so a stuck island is visible.
        moved = np.abs(damping * (D_new - D))
        has_votes = w_c > 0
        step = float(moved[has_votes].max()) if has_votes.any() else 0.0
        n_moving = int((moved[has_votes] > tol).sum())
        rms = float(np.sqrt(np.mean(moved[has_votes] ** 2))) if has_votes.any() else 0.0
        D = D + damping * (D_new - D)
        if verbose:
            print(f"[bracket] iteration {it + 1}: max change {step:.6f} "
                  f"({n_moving:,} cells over {tol:g}, rms {rms:.2e}), "
                  f"{int(ok_race.sum()):,} races with {min_voters}+ voters", flush=True)
        if step < tol:
            break
    # the final local levels against the final D
    v = z - h * D[np.maximum(cell, 0)]
    s, _c = W.sums(v[ref])
    s = s - np.where(self_ref, v[q], 0.0)
    a_local[:] = np.nan
    a_local[q] = np.where(has, s / np.maximum(n_other, 1), np.nan)
    races_per_cell = np.bincount(race_cell[ok_race], minlength=n_cell)
    races_per_base = np.bincount(base_of_cell, weights=races_per_cell,
                                 minlength=n_base).astype(np.int64)
    if verbose:
        thin = int(((races_per_base == 1) & (w_b > 0)).sum())
        print(f"[bracket] priors: a race weighs n/(n+{race_sat:g}) voters; a course is "
              f"pulled to its sport's average by {prior_group:g} race, an era to the "
              f"course's history by {prior_races:g}; {thin:,} courses rest on one race "
              f"and keep about {100 * (1 / (1 + prior_group)):.0f}% of it", flush=True)
    return dict(D=D, votes=w_c, D_race=D_race, votes_race=w_race, race=race,
                cell=cell, cell_keys=cell_keys, base_of_cell=base_of_cell,
                races_per_cell=races_per_cell, races_per_base=races_per_base,
                a_local=a_local, h=h, z=z, curve=curve, season=season,
                n_season=n_season, train=train, voters=voters, window=window,
                top=top, era_years=era_years, prior_races=prior_races,
                prior_group=prior_group, race_sat=race_sat)


def predict(f):
    """Per row: predicted ln(norm) = a_local + h * D[cell] + curve, and the
    covered mask (a local level from training rows, and a cell with votes)."""
    cell = np.maximum(f["cell"], 0)
    covered = np.isfinite(f["a_local"]) & (f["cell"] >= 0) & (f["votes"][cell] > 0)
    pred = f["a_local"] + f["h"] * f["D"][cell] + f["curve"]
    return pred, covered
