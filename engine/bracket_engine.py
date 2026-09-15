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
  the top fraction of its field by rating; a race weighs n/(n+RACE_SAT)
  voters; a course is the weighted mean of its races pulled toward its
  group's average course by a prior in races (fitted per group: XC,
  outdoor track, indoor track -- see PRIOR_GROUP_BY); a (course, era)
  cell is pulled toward the course's history by PRIOR_RACES races, so a
  two-race era is not one strange race. On the planted era world the
  engine follows a course that changed as far as the joint solve does.

  The reference races have their own difficulties, so this is a fixed
  point: iterate D -> a_s -> D with damping until it stops moving. That
  is the whole solve. No race-day term, no field term, no taper.

★ WHAT IT IS FOR. A second engine scored on the SAME held-out races as the
  joint model (scripts/bracket_holdout.py), so the two are compared with a
  number. predict() gives a held-out row's ln(norm) as a_s + h * D + curve.
"""
import math

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

# ★ THE PRIOR IS PER GROUP, AND IT IS THE RATIO OF TWO VARIANCES (owner,
#   2026-09-13: "TF difficulty weirder now, I think too much just variance.
#   Maybe we should shrink variance for outdoor courses and let indoor keep
#   its difficulty"). One race's worth of prior is right where the day-to-
#   day spread of a course equals the course-to-course spread: on grass
#   they are (the joint solve fits race-day sd 3.05% against a course
#   prior of 3.64%). On an outdoor track they are not: race-day sd 1.45%
#   against a course spread of 0.89%, so a one-race track kept half of one
#   day when it should keep a quarter, and the board's scatter was days,
#   not tracks. The prior in races is sigma_day^2 / tau^2 -- the number of
#   readings whose average is as informative as "it is an ordinary
#   course". Indoors the surface differences are real (banked against
#   flat, 160 m against 300 m) and the weather is not, so the oval keeps
#   more of its own number; and each group is pulled toward ITS OWN
#   average, so a thin oval sits at the indoor level, not the outdoor one.
#
#   "fit" (the default) estimates the two variances per group from the
#   courses with two or more races -- pooled within-course variance of the
#   race readings against the between-course variance of their means, the
#   same identified-priors idea as the joint solve's -- after PRIOR_FIT_WARMUP
#   passes on the stated values, then holds them. The stated values are
#   the fallback for a group with too few multi-race courses, and can be
#   given whole (`prior_group=2.0`, every group) or per group
#   (`{"XC": 1.0, "TF:out": 2.5, "TF:in": 1.0}`, or the string
#   "XC=1,TF:out=2.5,TF:in=1"). Whatever is used is printed.
PRIOR_GROUP_BY = {"XC": 1.0, "TF:out": 2.5, "TF:in": 1.0}
PRIOR_GROUP_NAMES = ("XC", "TF:out", "TF:in")
PRIOR_FIT = "fit"
PRIOR_FIT_WARMUP = 6           # passes on the stated priors before the estimate
PRIOR_FIT_RANGE = (0.25, 8.0)  # races; outside it the estimate is not believed
PRIOR_FIT_MIN_COURSES = 30     # multi-race courses a group needs to be fitted


# ★ THE PLACE PRIOR (owner, 2026-09-14: "build the place prior too,
#   coordinates in the pack and all"). One venue keyed in pieces is several
#   thin cells, each pulled toward the sport's average when its own place
#   is standing right there with twenty race days. So cells within
#   PLACE_RADIUS_M of each other, at one distance (XC) or on one surface
#   (TF), form a PLACE, and a course rests on its place before it rests on
#   the average course: the place's reading is its members' vote-weighted
#   mean shrunk toward the group by the group prior, and each member is
#   pulled toward that by PRIOR_PLACE races' worth. A place of one (most
#   courses) is the group prior exactly as before. A deliberate split at
#   one coordinate (Mt. SAC's rain course) is still its own cell -- the
#   pull is a prior, and twenty race days of its own override it -- and
#   the era prior sits under all of this unchanged. Needs the pack's
#   course_lat / course_lon (speed_ratings.attachCourseCoords); without
#   them there are no places and the run says so.
PLACE_RADIUS_M = 400.0
PRIOR_PLACE = 2.0


def placeClusters(keys, lat, lon, radius_m=PLACE_RADIUS_M):
    """Per base course: its place id (-1 for a course alone, or without
    coordinates). Courses cluster only within one kind -- an XC key's
    distance suffix, a TF key's surface -- through a KD-tree on local
    metres, connected components over pairs within radius_m."""
    keys = [str(k) for k in keys]
    n = len(keys)
    place = np.full(n, -1, dtype=np.int64)
    if lat is None or lon is None or n == 0 or not radius_m or radius_m <= 0:
        return place, 0
    lat = np.asarray(lat, dtype=np.float64)
    lon = np.asarray(lon, dtype=np.float64)
    ok = np.isfinite(lat) & np.isfinite(lon)
    kind = np.empty(n, dtype=object)
    for i, k in enumerate(keys):
        if k.startswith("XC:"):
            kind[i] = "XC:d" + k.rpartition(":d")[2] if ":d" in k else "XC"
        else:
            kind[i] = "TF:" + ("in" if k.split("@", 1)[0].endswith(":in") else "out")
    try:
        from scipy.spatial import cKDTree
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
    except Exception:                                            # noqa: BLE001
        return place, 0
    next_id = 0
    for kd in sorted(set(kind[ok])):
        idx = np.flatnonzero(ok & (kind == kd))
        if idx.size < 2:
            continue
        lat0 = float(np.mean(lat[idx]))
        x = np.radians(lon[idx]) * 6_371_000.0 * math.cos(math.radians(lat0))
        y = np.radians(lat[idx]) * 6_371_000.0
        pts = np.c_[x, y]
        prs = cKDTree(pts).query_pairs(float(radius_m), output_type="ndarray")
        if prs.size == 0:
            continue
        m = idx.size
        adj = coo_matrix((np.ones(prs.shape[0]), (prs[:, 0], prs[:, 1])), shape=(m, m))
        n_comp, lab = connected_components(adj, directed=False)
        sizes = np.bincount(lab, minlength=n_comp)
        for c in np.flatnonzero(sizes >= 2):
            place[idx[lab == c]] = next_id
            next_id += 1
    return place, next_id


def parsePrior(spec):
    """A prior spec from the command line: 'fit', a number (every group),
    or 'XC=1,TF:out=2.5,TF:in=1' (a group left out keeps its stated
    value). Returns what fit() takes."""
    if spec is None:
        return PRIOR_FIT
    if isinstance(spec, (int, float, dict)):
        return spec
    s = str(spec).strip()
    if s.lower() == PRIOR_FIT:
        return PRIOR_FIT
    if "=" not in s:
        return float(s)
    out = dict(PRIOR_GROUP_BY)
    for part in s.split(","):
        k, _, v = part.partition("=")
        k = k.strip()
        if k not in PRIOR_GROUP_NAMES:
            raise ValueError(f"unknown prior group {k!r}; one of {PRIOR_GROUP_NAMES}")
        out[k] = float(v)
    return out


def priorGroupOfKeys(cell_keys):
    """Per cell: 0 XC, 1 outdoor track, 2 indoor track, from the key."""
    out = np.zeros(len(cell_keys), dtype=np.int64)
    for i, k in enumerate(cell_keys):
        if k.startswith("TF:"):
            out[i] = 2 if k.split("@", 1)[0].endswith(":in") else 1
    return out


def _statedPriors(prior_group):
    """The stated prior per group (an array over PRIOR_GROUP_NAMES) and
    whether the groups are to be fitted."""
    if isinstance(prior_group, str):
        if prior_group.lower() != PRIOR_FIT:
            return _statedPriors(parsePrior(prior_group))
        return np.array([PRIOR_GROUP_BY[g] for g in PRIOR_GROUP_NAMES], dtype=np.float64), True
    if isinstance(prior_group, dict):
        d = dict(PRIOR_GROUP_BY); d.update(prior_group)
        return np.array([float(d[g]) for g in PRIOR_GROUP_NAMES], dtype=np.float64), False
    return np.full(len(PRIOR_GROUP_NAMES), float(prior_group)), False


def fitPriors(D_race, w_race, ok_race, race_base, base_pg, n_base,
              stated, lo=PRIOR_FIT_RANGE[0], hi=PRIOR_FIT_RANGE[1],
              min_courses=PRIOR_FIT_MIN_COURSES):
    """Per prior group: (prior in races, sigma_day, tau, n multi-race
    courses). The within-course variance of the race readings (weighted
    as the engine weights them) against the between-course variance of
    the courses' means, on courses with two or more races; the prior is
    their ratio, clipped to [lo, hi]; a group with fewer than min_courses
    such courses keeps its stated value (n reported, sigma and tau NaN)."""
    n_g = len(stated)
    r = np.flatnonzero(ok_race)
    b = race_base[r]
    w = w_race[r]
    d = D_race[r]
    sw = np.bincount(b, weights=w, minlength=n_base)
    sw2 = np.bincount(b, weights=w * w, minlength=n_base)
    n_r = np.bincount(b, minlength=n_base)
    mean_b = np.where(sw > 0, np.bincount(b, weights=w * d, minlength=n_base) / np.maximum(sw, 1e-12), 0.0)
    dev2 = (d - mean_b[b]) ** 2
    ss_b = np.bincount(b, weights=w * dev2, minlength=n_base)
    df_b = np.where(sw > 0, sw - sw2 / np.maximum(sw, 1e-12), 0.0)   # weighted degrees of freedom
    multi = n_r >= 2
    out = []
    for g in range(n_g):
        m = multi & (base_pg == g)
        n_c = int(m.sum())
        if n_c < min_courses:
            out.append((float(stated[g]), np.nan, np.nan, n_c))
            continue
        s_w2 = float(ss_b[m].sum() / max(df_b[m].sum(), 1e-12))
        means = mean_b[m]
        # the between-course variance, less what the readings' own noise
        # puts into the means (each mean is s_w2 / effective races)
        eff = sw[m] ** 2 / np.maximum(sw2[m], 1e-12)
        tau2 = float(means.var() - np.mean(s_w2 / np.maximum(eff, 1e-12)))
        if not np.isfinite(s_w2) or s_w2 <= 0:
            out.append((float(stated[g]), np.nan, np.nan, n_c))
            continue
        k = hi if tau2 <= 0 else s_w2 / tau2
        k = float(np.clip(k, lo, hi))
        out.append((k, float(np.sqrt(s_w2)), float(np.sqrt(max(tau2, 0.0))), n_c))
    return out


def fit(cols, npz=None, train=None, window=21, top=0.5, era_years=0,
        n_iter=60, damping=0.5, prior_races=PRIOR_RACES, prior_group=PRIOR_FIT,
        race_sat=RACE_SAT, min_voters=3, tilt=True, use_curve=True, tol=1e-5,
        verbose=False, codes=None, prior_rows=None, z=None, h_row=None,
        prior_warmup=PRIOR_FIT_WARMUP, place_radius=PLACE_RADIUS_M,
        prior_place=PRIOR_PLACE):
    """Fit on the rows where `train` is True (all rows when None); every
    row, held out or not, gets its local level and a prediction.

    prior_races / prior_group / race_sat: see the notes above; prior_group
    is "fit" (per group, estimated after prior_warmup passes), one number
    for every group, or a dict / "XC=1,TF:out=2.5,TF:in=1" per group;
    prior_rows is the old name of prior_races and still accepted. min_voters: a race
    with fewer voters casts no vote (3: with top=0.5 a race of six counts,
    at weight 3/8 of a full race; a course with no such race sits at its
    sport's average). z: the response per
    row, given instead of ln(norm) less the curve (run_joint hands in the
    joint solve's residual with every term but the course and the day
    taken off); h_row: the tilt per row, given instead of computed.

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
    # the athlete's rating (for the voters and the tilt) and curve point
    rating = None
    if npz is not None and "rating" in npz and np.asarray(npz["rating"]).size == n_season:
        rating = np.asarray(npz["rating"], dtype=np.float64)[season]
    curve = np.zeros(n)
    if z is not None:
        z = np.asarray(z, dtype=np.float64)
        assert z.size == n, "z must be one value per row"
    else:
        ln = np.log(np.asarray(cols["norm"], dtype=np.float64))
        if use_curve and npz is not None and "curve" in npz and "doy" in cols:
            if pool_of_raw is None:
                pool_of_raw, _names = rj.poolCodes(cols["athlete_keys"])
            curve = bk.curveOnRows(npz, pool_of_raw[ath_raw], cols["doy"], rating)
        z = ln - curve
    if h_row is not None:
        h = np.asarray(h_row, dtype=np.float64)
        assert h.size == n, "h_row must be one value per row"
    else:
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
    vote = np.zeros(n, dtype=bool)
    # the prior groups: XC, outdoor track, indoor track, per cell and course
    cell_pg = priorGroupOfKeys(cell_keys)
    base_pg = np.zeros(n_base, dtype=np.int64)
    base_pg[base_of_cell] = cell_pg
    prior_stated, fit_priors = _statedPriors(prior_group)
    prior_g = prior_stated.copy()
    prior_report = None
    # the places: courses within place_radius of each other, of one kind
    place_of_base, n_place = placeClusters(
        keys, cols.get("course_lat"), cols.get("course_lon"), place_radius)
    in_place = place_of_base >= 0
    k_place = float(prior_place or 0.0)

    def levels(D_now):
        """The athletes' local levels against the courses D_now."""
        v = z - h * D_now[np.maximum(cell, 0)]
        s_, _c = W.sums(v[ref])
        s_ = s_ - np.where(self_ref, v[q], 0.0)
        a = np.full(n, np.nan)
        a[q] = np.where(has, s_ / np.maximum(n_other, 1), np.nan)
        return a

    def cellStep(a, k_g):
        """From the local levels: every race's reading, the course history
        after the group prior k_g, the era cell after the era prior, and
        the (sport, era) pin. Returns a dict of the pieces; D_new is the
        full step."""
        vote_ = voters & np.isfinite(a)
        r_i = (z - a) / h
        num = np.bincount(race[vote_], weights=r_i[vote_], minlength=n_race)
        cnt = np.bincount(race[vote_], minlength=n_race).astype(np.float64)
        ok = cnt >= min_voters
        D_r = np.where(ok, num / np.maximum(cnt, 1), 0.0)
        # a race's weight saturates in its voters: one reading, many witnesses
        w_r = np.where(ok, cnt / (cnt + race_sat), 0.0)
        # the course's history, shrunk toward ITS GROUP's average course by
        # the group's prior (in races); then each era cell pulled toward that
        num_c_ = np.bincount(race_cell, weights=w_r * D_r, minlength=n_cell)
        w_c_ = np.bincount(race_cell, weights=w_r, minlength=n_cell)
        num_b_ = np.bincount(base_of_cell, weights=num_c_, minlength=n_base)
        w_b_ = np.bincount(base_of_cell, weights=w_c_, minlength=n_base)
        g_num = np.bincount(base_pg, weights=num_b_, minlength=len(k_g))
        g_w = np.bincount(base_pg, weights=w_b_, minlength=len(k_g))
        g_mean_ = np.where(g_w > 0, g_num / np.maximum(g_w, 1e-12), 0.0)
        k_b = k_g[base_pg]
        D_base_ = np.where(w_b_ > 0,
                           (num_b_ + k_b * g_mean_[base_pg]) / np.maximum(w_b_ + k_b, 1e-9), 0.0)
        if n_place and k_place > 0:
            # the place's reading, shrunk to the group by the group prior;
            # each member pulled toward it by prior_place races' worth
            pl = place_of_base[in_place]
            pl_num = np.bincount(pl, weights=num_b_[in_place], minlength=n_place)
            pl_w = np.bincount(pl, weights=w_b_[in_place], minlength=n_place)
            pl_kg = np.zeros(n_place); pl_kg[pl] = k_b[in_place]
            pl_gm = np.zeros(n_place); pl_gm[pl] = g_mean_[base_pg[in_place]]
            m_place = np.where(pl_w > 0, (pl_num + pl_kg * pl_gm) / np.maximum(pl_w + pl_kg, 1e-9), 0.0)
            D_pl = (num_b_[in_place] + k_place * m_place[pl]) / np.maximum(w_b_[in_place] + k_place, 1e-9)
            D_base_[in_place] = np.where(pl_w[pl] > 0, D_pl, D_base_[in_place])
        D_pre = np.where(w_c_ + prior_races > 0,
                         (num_c_ + prior_races * D_base_[base_of_cell]) / (w_c_ + prior_races),
                         0.0)
        D_pre = np.where(w_b_[base_of_cell] > 0, D_pre, 0.0)
        pin = np.zeros(n_cell)
        for g in np.unique(cell_group[w_c_ > 0]):
            m_g = (cell_group == g) & (w_c_ > 0)
            pin[m_g] = np.average(D_pre[m_g], weights=w_c_[m_g])
        return dict(vote=vote_, D_race=D_r, w_race=w_r, ok_race=ok, num_c=num_c_,
                    w_c=w_c_, w_b=w_b_, g_mean=g_mean_, D_base=D_base_,
                    D_pre=D_pre, pin=pin, D_new=D_pre - pin)

    for it in range(n_iter):
        a_local = levels(D)
        st = cellStep(a_local, prior_g)
        if fit_priors and prior_report is None and it >= prior_warmup:
            race_base = base_of_cell[race_cell]
            prior_report = fitPriors(st["D_race"], st["w_race"], st["ok_race"], race_base,
                                     base_pg, n_base, prior_stated)
            prior_g = np.array([p[0] for p in prior_report], dtype=np.float64)
            st = cellStep(a_local, prior_g)
        D_new, w_c, ok_race = st["D_new"], st["w_c"], st["ok_race"]
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
        # ! NOT BEFORE THE PRIORS ARE FITTED: a small world settles in
        #   fewer passes than the warm-up, and stopping there would leave
        #   the stated priors in place with the log saying they were fitted
        if step < tol and not (fit_priors and prior_report is None):
            break
    # the final local levels against the final D, and the engine's
    # arithmetic evaluated there: every piece of the trace rebuilds
    # D_fit = D_new of this pass, which is D itself to within the tolerance
    a_local = levels(D)
    st = cellStep(a_local, prior_g)
    vote, D_race, w_race, ok_race = st["vote"], st["D_race"], st["w_race"], st["ok_race"]
    num_c, w_c, w_b, g_mean, D_base = st["num_c"], st["w_c"], st["w_b"], st["g_mean"], st["D_base"]
    races_per_cell = np.bincount(race_cell[ok_race], minlength=n_cell)
    races_per_base = np.bincount(base_of_cell, weights=races_per_cell,
                                 minlength=n_base).astype(np.int64)
    prior_lines = priorReport(prior_g, prior_stated, prior_report, fit_priors,
                              races_per_base, w_b, base_pg)
    # the engine's arithmetic per cell, for the trace (scripts/course_bracket):
    # the era's own vote-mean, the course's shrunk history and its votes,
    # the (sport, era) pin, and the total they rebuild
    D_cell_raw = np.where(w_c > 0, num_c / np.maximum(w_c, 1e-12), np.nan)
    tilt_bands = tiltByBand(z, a_local, h, D, cell, vote, rating, cell_sport)
    tilt_races = tiltByRaces(z, a_local, h, D, cell, vote, races_per_cell, cell_sport)
    if verbose:
        print(f"[bracket] priors: a race weighs n/(n+{race_sat:g}) voters; an era is "
              f"pulled to the course's history by {prior_races:g} races; a course to "
              f"its group's average course by (in races):", flush=True)
        for ln in prior_lines:
            print("        " + ln, flush=True)
        if cols.get("course_lat") is None:
            print("[bracket] place prior: the pack carries no course coordinates "
                  "(rebuild it at 07_pack); no places", flush=True)
        elif n_place == 0:
            print(f"[bracket] place prior: no two courses of one kind within "
                  f"{place_radius:g} m; no places", flush=True)
        else:
            n_in = int(in_place.sum())
            n_voted = int((in_place & (w_b > 0)).sum())
            print(f"[bracket] place prior: {n_place:,} places of 2+ courses within "
                  f"{place_radius:g} m ({n_in:,} courses, {n_voted:,} with votes); a course "
                  f"rests on its place by {k_place:g} races' worth before the group prior",
                  flush=True)
    return dict(D=D, votes=w_c, D_race=D_race, votes_race=w_race, race=race,
                cell=cell, cell_keys=cell_keys, base_of_cell=base_of_cell,
                races_per_cell=races_per_cell, races_per_base=races_per_base,
                a_local=a_local, h=h, z=z, curve=curve, season=season,
                n_season=n_season, train=train, voters=voters, window=window,
                top=top, era_years=era_years, prior_races=prior_races,
                prior_group=prior_g, prior_group_names=PRIOR_GROUP_NAMES,
                prior_group_stated=prior_stated, prior_group_fitted=fit_priors,
                prior_report=prior_report, prior_lines=prior_lines,
                cell_prior_group=cell_pg, race_sat=race_sat,
                D_cell_raw=D_cell_raw, D_base=D_base, base_votes=w_b,
                group_mean=g_mean, base_prior_group=base_pg, tilt_bands=tilt_bands,
                tilt_races=tilt_races,
                pin=st["pin"], D_fit=st["D_new"],
                place_of_base=place_of_base, n_place=int(n_place),
                place_radius=float(place_radius or 0.0), prior_place=k_place)


TILT_BANDS = (100.0, 120.0, 130.0, 140.0, 150.0, 160.0)


def tiltByBand(z, a_local, h, D, cell, vote, rating, cell_sport, bands=TILT_BANDS,
               min_rows=2000, min_course=0.02):
    """★ THE TILT THE BRACKETS IMPLY, PER RATING BAND AND SPORT (owner,
    2026-09-13: the hardest venues -- Mt. SAC, Crystal Springs, Glendoveer
    -- "seem overstated"). Those venues are read through elite runners,
    and every reading is divided by the applied tilt h(rating): if the
    line is too steep above 140, every course measured by 140s and 150s
    is inflated by the same fraction, and the ones that are ALSO hard
    show it most. Here each voter's untilted reading b = z - a_local is
    regressed through the origin on the course's fitted D within the
    band (courses within +-min_course excluded, they carry no signal):
    implied h = sum(D b) / sum(D^2), against the h applied. A line past
    140 means the extrapolation holds; implied below applied there means
    the elite pay less of a course than charged and the hard venues are
    overstated by the ratio. Returns rows (sport, band, n, applied,
    implied, se) or None without ratings."""
    if rating is None:
        return None
    b = z - a_local
    d = D[np.maximum(cell, 0)]
    r = np.nan_to_num(np.asarray(rating, dtype=np.float64), nan=100.0)
    sp = cell_sport[np.maximum(cell, 0)]
    edges = (-np.inf,) + tuple(bands) + (np.inf,)
    rows = []
    base = vote & np.isfinite(b) & (np.abs(d) >= min_course)
    for s_code, s_name in ((0, "XC"), (1, "TF")):
        for lo, hi in zip(edges, edges[1:]):
            m = base & (sp == s_code) & (r >= lo) & (r < hi)
            n = int(m.sum())
            if n < min_rows:
                continue
            sxx = float(np.sum(d[m] * d[m]))
            if sxx <= 0:
                continue
            implied = float(np.sum(d[m] * b[m]) / sxx)
            res = b[m] - implied * d[m]
            se = float(np.sqrt(np.sum(res * res) / max(n - 1, 1) / sxx))
            lab = (f"<{hi:.0f}" if lo == -np.inf else
                   f"{lo:.0f}+" if hi == np.inf else f"{lo:.0f}-{hi:.0f}")
            rows.append((s_name, lab, n, float(h[m].mean()), implied, se))
    return rows


RACE_BUCKETS = ((1, 1, "1"), (2, 3, "2-3"), (4, 9, "4-9"), (10, 10**9, "10+"))


def tiltByRaces(z, a_local, h, D, cell, vote, races_per_cell, cell_sport,
                buckets=RACE_BUCKETS, min_rows=2000, min_course=0.02):
    """★ THE SAME REGRESSION BY HOW WELL THE COURSE IS KNOWN (2026-09-15).
    A shrunk course reads small, and the voters' brackets then imply a
    multiplier ABOVE the applied one by the shrinkage -- so if the ratio
    falls with races per cell, the gap in tiltByBand is the prior pulling
    thin courses in; if it is flat, the whole sport's course scale is
    short. Rows (sport, bucket, n, applied, implied, se)."""
    b = z - a_local
    d = D[np.maximum(cell, 0)]
    sp = cell_sport[np.maximum(cell, 0)]
    rc = np.asarray(races_per_cell)[np.maximum(cell, 0)]
    base = vote & np.isfinite(b) & (np.abs(d) >= min_course)
    rows = []
    for s_code, s_name in ((0, "XC"), (1, "TF")):
        for lo, hi, lab in buckets:
            m = base & (sp == s_code) & (rc >= lo) & (rc <= hi)
            n = int(m.sum())
            if n < min_rows:
                continue
            sxx = float(np.sum(d[m] * d[m]))
            if sxx <= 0:
                continue
            implied = float(np.sum(d[m] * b[m]) / sxx)
            res = b[m] - implied * d[m]
            se = float(np.sqrt(np.sum(res * res) / max(n - 1, 1) / sxx))
            rows.append((s_name, lab, n, float(h[m].mean()), implied, se))
    return rows


def courseScaleFromBands(rows, sport, max_se=0.02, min_rows=5000):
    """★ THE COURSE SCALE A SPORT'S BANDS AGREE ON (owner, 2026-09-15). Run
    23: XC's implied multiplier sat above the applied one by the same
    tenth in every band while TF's matched -- the voters' own brackets
    saying XC courses cost a tenth more than charged. The scale is the
    voter-weighted mean of implied / applied over the sport's bands whose
    standard error is small enough to trust; 1.0 without such a band.
    Multiplying the sport's course effects by it makes implied = applied,
    which is the definition of the courses being on the voters' scale."""
    num = den = 0.0
    for s_name, _lab, n, applied, implied, se in rows or ():
        if s_name != sport or n < min_rows or se > max_se or applied <= 0:
            continue
        num += n * (implied / applied)
        den += n
    return (num / den) if den > 0 else 1.0


def tiltRaceLines(rows):
    """The tilt-by-races rows as printable lines."""
    if not rows:
        return []
    out = [f"{'sport':<6}{'races':>9}{'voters':>10}{'applied h':>11}{'implied h':>11}{'se':>7}"
           "   (implied/applied falling with races: the prior; flat: the sport's scale)"]
    for s_name, lab, n, ha, hi_, se in rows:
        out.append(f"{s_name:<6}{lab:>9}{n:>10,}{ha:>11.3f}{hi_:>11.3f}{se:>7.3f}")
    return out


def tiltBandArray(rows):
    """The tilt-by-band rows as a float array (sport code, band lower edge
    or -inf, voters, applied, implied, se), for the npz; empty without rows."""
    out = []
    for s_name, lab, n, applied, implied, se in rows or ():
        lo = (-np.inf if lab.startswith("<") else float(lab.rstrip("+").split("-")[0]))
        out.append([0.0 if s_name == "XC" else 1.0, lo, float(n), applied, implied, se])
    return np.asarray(out, dtype=np.float64).reshape(-1, 6)


def tiltLines(rows):
    """The tilt-by-band rows as printable lines."""
    if not rows:
        return []
    out = [f"{'sport':<6}{'band':>9}{'voters':>10}{'applied h':>11}{'implied h':>11}{'se':>7}"
           "   (implied < applied: the band pays less of a course than charged)"]
    for s_name, lab, n, ha, hi_, se in rows:
        out.append(f"{s_name:<6}{lab:>9}{n:>10,}{ha:>11.3f}{hi_:>11.3f}{se:>7.3f}")
    return out


def priorReport(prior_g, stated, report, fitted, races_per_base, w_b, base_pg):
    """One line per group: the prior used, where it came from, and what a
    one-race course keeps of its one reading."""
    lines = []
    for g, name in enumerate(PRIOR_GROUP_NAMES):
        k = float(prior_g[g])
        n_courses = int(((w_b > 0) & (base_pg == g)).sum())
        thin = int(((races_per_base == 1) & (w_b > 0) & (base_pg == g)).sum())
        if n_courses == 0:
            continue
        keep = 100.0 / (1.0 + k)
        src = f"stated {stated[g]:g}"
        if fitted and report is not None:
            _k, s_day, tau, n_c = report[g]
            if np.isfinite(s_day):
                src = (f"FITTED on {n_c:,} courses with 2+ races: race-day sd "
                       f"{100 * s_day:.2f}%, course sd {100 * tau:.2f}%, ratio "
                       f"{s_day ** 2 / max(tau ** 2, 1e-12):.2f} (stated {stated[g]:g})")
            else:
                src = f"stated {stated[g]:g} ({n_c:,} courses with 2+ races, too few to fit)"
        lines.append(f"{name:<7} {k:5.2f} races  [{src}]; {n_courses:,} courses, "
                     f"{thin:,} on one race keeping about {keep:.0f}% of it")
    return lines


def predict(f):
    """Per row: predicted ln(norm) = a_local + h * D[cell] + curve, and the
    covered mask (a local level from training rows, and a cell with votes)."""
    cell = np.maximum(f["cell"], 0)
    covered = np.isfinite(f["a_local"]) & (f["cell"] >= 0) & (f["votes"][cell] > 0)
    pred = f["a_local"] + f["h"] * f["D"][cell] + f["curve"]
    return pred, covered
