"""
forward_holdout.py -- the validation scorecard: fit on the past, predict a
whole later season, and score it the way the boards are read.

    # the validation season, today's production flags (scripts/scorecard.py
    # builds this command; run_joint prints the windows it picked)
    python engine/run_joint.py --holdout-only --holdout-kind forward \
        --sample-pct 15 --outer 5 --probes 0 --altitude ...
    # the sealed test season -- ONCE, deliberately
    python engine/run_joint.py ... --holdout-kind forward --sealed
    # two flag sets on the same held-out rows, paired, race-bootstrapped
    python scripts/scorecard.py --base production --try "... --season-tie"

★ WHY (owner, 2026-09-29: keep a validation set for hyperparameters, or cut
  parameters?). The plan agreed: (1) estimate everything the data can
  identify inside the joint solve; (2) tune the few real hyperparameters and
  on/off switches on ONE fixed validation scorecard; (3) look at a sealed
  final test season once. This file is (2)'s scorecard. It changes no model
  behaviour: the fit is run_joint.holdout's own solve on fewer rows.

★ FORWARD IN TIME, NOT RANDOM RACES. The race holdout (pair_validate
  splitFor 'race') holds out a tenth of the races at random, so an
  athlete's LATER races -- the rest of the same season, and the seasons
  after it -- inform the prediction of an EARLIER one. That scores
  interpolation of an ability the fit has already seen from both sides.
  Here the fit sees only rows dated before the cutoff and predicts every
  rated row of a whole later season, which is what the site is asked.
  engine/holdout_eval.py did the same date cut for the pre-joint estimator
  (database rows, meet/division races); this is the joint solve's.

★ THE WINDOWS ARE THE PACK'S, NOT TYPED (defaultWindows). A season is
  season_year's academic year (ACADEMIC_START_MONTH opens one). The season
  holding the pack's latest race is still running; the one before it is the
  last COMPLETE season, the SEALED test window; the one before that is the
  VALIDATION window. Rows on or after the window's end are excluded from the
  fit AND the score, so a validation run never touches the sealed season.
⚠ THE SEALED WINDOW IS REFUSED WITHOUT --sealed (resolveWindow), and a run
  that passes it prints a loud line: it is meant to be looked at once, on
  purpose, and the log should show when it was.

★ THE PREDICTION OF A HELD-OUT ROW (carryForward, then js.predictHeldOut):
    ability   the athlete's latest FITTED season, moved across the gap by
              what the model already uses between seasons -- the season
              tie's walk (c + m * dt for that transition and band) when the
              run carries --season-tie, else nothing: the last season's
              ability as it stands;
    course    the cell's fitted difficulty; a cell with no training rows
              reads what the solve gave it with none -- its group's level
              (the prior mean), or under --era-years the walk from its last
              fitted era. The joint solve has no place prior (that is the
              bracket engine's), so there is nothing else to read;
    tilt      at the carried rating; the curve, rust, distance, altitude and
              indoor terms as the fit has them (all known before the race);
    race day  NONE. A race after the cutoff has no fitted day, and the day
              cannot be known in advance.
  A row whose athlete (the pack's key: person and pool) has NO training row
  is a COLD START: counted, never in the main number. A cold row of a
  person known in another pool is predicted when the tie carries that
  transition, and scored on its own line.

★ THE NUMBERS, ALL ON THE SAME HELD-OUT ROWS (scoreLines, pairedLines):
    time error    e = log(actual / predicted): bias (mean e), median |e|,
                  p90 |e|;
    head-to-head  per race, the share of pairs of scored runners whose
                  predicted order is the finishing order; ONE NUMBER PER
                  RACE, then the mean over races, so a 300-runner race
                  (44,850 pairs) counts once, like a dual meet. The pooled
                  share over all pairs is printed beside it;
    by pool, by sport (XC/TF), and by the athlete's training races
                  (TRAIN_BUCKETS).
  A "race" for head-to-head is (cell, day, pool, distance): the pack carries
  no meet or division id, a (cell, day) pools the boys' and girls' races,
  and a track cell is a venue, so the day's 800 and 3200 share one. Pairs
  with equal times are no evidence either way and are left out; equal
  PREDICTIONS count one half.

★ THE STANDARD ERROR IS BY RACE (raceBootstrap). Rows of one race share
  its day, its field and its course; resampling rows as if independent
  shrinks the SE by roughly the square root of the field size. Races are
  resampled whole, with replacement, and every statistic -- both runs' --
  is recomputed on the same resample, so the SE is of the PAIRED change.
"""

import numpy as np

from season_year import ACADEMIC_START_MONTH

# the season tie's clock (run_joint.seasonTiePairs divides by the same)
DAYS_PER_YEAR = 365.25

# the athlete's training races, for reading (the owner's buckets)
TRAIN_BUCKETS = ((1, 1), (2, 2), (3, 5), (6, 10), (11, None))

STATUS_KNOWN = 0         # the athlete (person, pool) has training rows
STATUS_NEW_PERSON = 1    # the person has none at all
STATUS_NEW_POOL = 2      # the person has, in another pool only

HOW_OWN = 0              # the row's own athlete-season was fitted (a cut
                         # inside a season)
HOW_CARRIED = 1          # the key's latest season, not moved
HOW_TIED = 2             # moved by the season tie's fitted walk
HOW_NONE = -1

# ★ THE BOOTSTRAP'S SIZE IS A PRECISION, NOT A NUMBER PICKED. The relative
#   Monte-Carlo error of an SE estimated from B resamples is about
#   1 / sqrt(2 (B - 1)); asking it to be 5% of the SE gives B = 201. The
#   5% is the definition: an SE known to a twentieth of itself, which is
#   finer than any "beyond 2 se" call reads it.
BOOT_REL_PRECISION = 0.05


def bootReps(rel=BOOT_REL_PRECISION):
    return int(np.ceil(1.0 / (2.0 * rel * rel))) + 1


# ------------------------------------------------------------------ #
# DATES AND WINDOWS
# ------------------------------------------------------------------ #

def _jan1(year):
    return (np.asarray(year, dtype=np.int64) - 1970).astype("datetime64[Y]") \
        .astype("datetime64[D]")


def seasonOf(dates):
    """The academic season (season_year) of datetime64[D] dates."""
    d = np.asarray(dates, dtype="datetime64[D]")
    year = d.astype("datetime64[Y]").astype(np.int64) + 1970
    month = d.astype("datetime64[M]").astype(np.int64) % 12 + 1
    return year - (month < ACADEMIC_START_MONTH).astype(np.int64)


def seasonStart(season):
    """The first day of an academic season, as datetime64[D]."""
    s = np.asarray(season, dtype=np.int64)
    return ((s - 1970) * 12 + (ACADEMIC_START_MONTH - 1)).astype("datetime64[M]") \
        .astype("datetime64[D]")


def rowDates(cols):
    """(date per row as datetime64[D], the pack's build date, agreement).

    The pack stores `days` (days before it was built) and the season and
    day of year. The date is pack date - days, exact; the pack date is the
    one (season, doy) and days agree on for most rows -- the date whose
    academic season is the row's season, plus its days. `agreement` is the
    share of rows that agree, so a pack on another clock shows itself."""
    # ! BY TABLE, NOT BY DATE OBJECTS: 61M rows span a dozen seasons, so
    #   each season's 1 January and its season-opening day of year are
    #   looked up, and the mode is a bincount over a few days' offsets.
    # ! (season, doy) IS AMBIGUOUS ONCE A LEAP YEAR: day 213 is 1 August of
    #   a common year and 31 July of a leap one, and both belong to the
    #   same season. The mode does not care, and the dates are pack - days.
    year = np.asarray(cols["year"], dtype=np.int64)
    doy = np.asarray(cols["doy"], dtype=np.int64)
    days = np.rint(np.asarray(cols["days"], dtype=np.float64)).astype(np.int64)
    y0 = int(year.min())
    span = np.arange(y0, int(year.max()) + 2)
    jan1 = _jan1(span).astype(np.int64)
    opens = (seasonStart(span).astype(np.int64) - jan1) + 1     # doy of day 1
    i = year - y0
    cal = np.where(doy >= opens[i], i, i + 1)
    ref = jan1[cal] + (doy - 1) + days
    lo = int(ref.min())
    cnt = np.bincount(ref - lo)
    pack_i = lo + int(np.argmax(cnt))
    pack = np.datetime64(pack_i, "D")
    return pack - days, pack, float(cnt[pack_i - lo] / max(ref.size, 1))


def defaultWindows(dates):
    """The validation and sealed windows from the rated rows' dates.

    current    the season holding the latest race: still running;
    sealed     the season before it, the last COMPLETE one (the data runs
               past its end, by construction of 'current');
    validation the season before that.
    Each window is (from, until, season), until exclusive."""
    d = np.asarray(dates, dtype="datetime64[D]")
    last, first = d.max(), d.min()
    cur = int(seasonOf(last))
    sealed, valid = cur - 1, cur - 2
    if valid <= int(seasonOf(first)):
        raise SystemExit(
            f"[forward] the pack runs {first} to {last}: seasons "
            f"{int(seasonOf(first))}..{cur}, and a validation season "
            f"({valid}) needs a whole season of training before it and a "
            "sealed season after it")

    def win(s):
        return (seasonStart(s), seasonStart(s + 1), s)
    return {"first": first, "last": last, "current": cur,
            "validation": win(valid), "sealed": win(sealed)}


def _date(text):
    return None if not text else np.datetime64(str(text), "D")


def resolveWindow(windows, frm=None, until=None, sealed=False):
    """(from, until) for this run. Without `sealed`, nothing the run touches
    -- fitted or scored -- may reach the sealed window; with it the default
    IS the sealed window."""
    frm, until = _date(frm), _date(until)
    s_from, s_until = windows["sealed"][0], windows["sealed"][1]
    if sealed:
        f = frm if frm is not None else s_from
        u = until if until is not None else s_until
    else:
        f = frm if frm is not None else windows["validation"][0]
        u = (until if until is not None
             else windows["validation"][1] if frm is None
             else s_from if f < s_from else s_until)
        if f >= s_from or u > s_from:
            raise SystemExit(
                f"[forward] REFUSED: [{f}, {u}) reaches the SEALED test "
                f"season {windows['sealed'][2]} ([{s_from}, {s_until})). The "
                "sealed season is scored once, deliberately: pass --sealed.")
    if u <= f:
        raise SystemExit(f"[forward] an empty window: [{f}, {u})")
    return f, u


def forwardSplit(dates, keep, frm, until):
    """(train, test, excluded) row masks: train before `frm`, test in
    [frm, until), excluded on or after `until` -- in neither."""
    d = np.asarray(dates, dtype="datetime64[D]")
    keep = np.asarray(keep, dtype=bool)
    train = keep & (d < frm)
    test = keep & (d >= frm) & (d < until)
    excluded = keep & (d >= until)
    return train, test, excluded


def forwardWindow(cols, frm=None, until=None, sealed=False, verbose=True):
    """Dates per row and this run's window: {dates, from, until, windows,
    sealed}. The windows come from EVERY rated row of the pack (course and a
    time), so an athlete sample never moves them."""
    dates, pack, agree = rowDates(cols)
    rated = (np.asarray(cols["course"]) >= 0) & (np.asarray(cols["norm"]) > 0)
    windows = defaultWindows(dates[rated])
    f, u = resolveWindow(windows, frm, until, sealed)
    if verbose:
        for line in windowLines(windows, f, u, sealed, pack, agree):
            print(line, flush=True)
    return {"dates": dates, "from": f, "until": u, "windows": windows,
            "sealed": bool(sealed)}


def windowLines(windows, frm, until, sealed, pack_date=None, agree=None):
    v, s = windows["validation"], windows["sealed"]
    out = []
    if pack_date is not None:
        out.append(f"[forward] pack built {pack_date}; rated rows run "
                   f"{windows['first']} .. {windows['last']}"
                   + (f" (dates agree with the seasons on {100 * agree:.2f}% "
                      "of rows)" if agree is not None else ""))
    out.append(f"[forward] seasons (academic, from month "
               f"{ACADEMIC_START_MONTH}): {windows['current']} is still running; "
               f"SEALED test = {s[2]} [{s[0]}, {s[1]}); VALIDATION = {v[2]} "
               f"[{v[0]}, {v[1]})")
    out.append(f"[forward] this run: fit on rows before {frm}, score every "
               f"rated row in [{frm}, {until}), nothing on or after {until}")
    if sealed:
        bar = "!" * 78
        out += [bar,
                f"!!! SEALED TEST SEASON {s[2]} IS BEING SCORED (--sealed). This is the",
                "!!! final look. Tuning on this number turns it into another "
                "validation set.",
                bar]
    return out


# ------------------------------------------------------------------ #
# CARRYING AN ABILITY ACROSS THE CUT
# ------------------------------------------------------------------ #

def personCodes(athlete_keys):
    """Per raw athlete code, a person code: the key's first field (the
    pack keys an athlete as (person, pool))."""
    keys = athlete_keys
    if isinstance(keys, np.ndarray) and keys.ndim == 2:
        pid = keys[:, 0].astype(str)
    else:
        pid = np.array([str(k[0]) if (k is not None and len(k) > 0) else ""
                        for k in keys])
    _, inv = np.unique(pid, return_inverse=True)
    return inv.reshape(-1).astype(np.int64)


def rowStatus(cols, keep_tr, keep_te, person_of_raw=None):
    """Per held-out row: (STATUS_*, the athlete key's training rows). The
    athlete is the pack's key, (person, pool)."""
    raw_all = np.asarray(cols["athlete"]).astype(np.int64)
    raw_tr, raw_te = raw_all[keep_tr], raw_all[keep_te]
    if person_of_raw is None:
        person_of_raw = personCodes(cols["athlete_keys"])
    n_raw = person_of_raw.size
    key_rows = np.bincount(raw_tr, minlength=n_raw)[raw_te]
    person_rows = np.bincount(person_of_raw[raw_tr],
                              minlength=int(person_of_raw.max()) + 1 if n_raw else 1)
    status = np.where(key_rows > 0, STATUS_KNOWN,
                      np.where(person_rows[person_of_raw[raw_te]] > 0,
                               STATUS_NEW_POOL, STATUS_NEW_PERSON)).astype(np.int8)
    return status, key_rows.astype(np.int32)


def _latestBy(key, t, codes):
    """For each distinct key among `codes`, the code with the latest t.
    Returns a lookup(keys) -> code (-1 where the key has none)."""
    codes = np.asarray(codes, dtype=np.int64)
    if codes.size == 0:
        return lambda q: np.full(np.asarray(q).shape, -1, dtype=np.int64)
    k = key[codes]
    order = np.lexsort((t[codes], k))
    ks = k[order]
    last = np.r_[ks[1:] != ks[:-1], True]
    u_key, u_code = ks[last], codes[order][last]

    def lookup(q):
        q = np.asarray(q, dtype=np.int64)
        i = np.clip(np.searchsorted(u_key, q), 0, u_key.size - 1)
        return np.where(u_key[i] == q, u_code[i], -1)
    return lookup


def _tieTables(out, D_tr):
    """(transition code per (from label, to label), c and m per (transition,
    band + 1)), NaN where untied; None when the run carries no tie."""
    mom = out.get("season_tie_moments")
    names = list(getattr(D_tr, "tie_type_names", []) or [])
    if mom is None or not names or np.asarray(mom).size == 0:
        return None
    return np.asarray(mom, dtype=np.float64), names


def carryForward(out, D_tr, D_te, cols, keep_tr, keep_te, dates, pool_of_raw,
                 pool_names, split_ability=False, tie_bands=None):
    """A copy of the fit with every held-out athlete-season given an ability.

    Returns (out2, info). out2 is `out` with theta, rating and tilt_rating
    copied and filled for the held-out athlete-seasons the fit never saw;
    js.predictHeldOut(out2, D_tr, D_te) then predicts them. info holds per
    held-out row: status (STATUS_*), how (HOW_*), train_races (the athlete
    key's training rows), source_rows (the training rows of the season the
    ability came from) and cell_seen; and the counts for the log."""
    import joint_solve as js
    tie_bands = js.SEASON_TIE_BANDS if tie_bands is None else tie_bands
    n_ath = int(D_tr.n_ath)
    raw_all = np.asarray(cols["athlete"]).astype(np.int64)
    raw_tr, raw_te = raw_all[keep_tr], raw_all[keep_te]
    code_tr = np.asarray(D_tr.athlete, dtype=np.int64)
    code_te = np.asarray(D_te.athlete, dtype=np.int64)
    person_of_raw = personCodes(cols["athlete_keys"])
    pool_of_raw = np.asarray(pool_of_raw, dtype=np.int64)

    raw_of = np.full(n_ath, -1, dtype=np.int64)
    raw_of[code_tr] = raw_tr
    raw_of[code_te] = raw_te
    sp_of = np.zeros(n_ath, dtype=np.int64)
    if split_ability and "sport" in cols:
        sport = (np.asarray(cols["sport"]) != 0).astype(np.int64)
        sp_of[code_tr] = sport[keep_tr]
        sp_of[code_te] = sport[keep_te]
    t_all = np.asarray(dates, dtype="datetime64[D]").astype(np.int64).astype(np.float64)
    n_tr = np.bincount(code_tr, minlength=n_ath)
    t_tr = np.bincount(code_tr, weights=t_all[keep_tr], minlength=n_ath) \
        / np.maximum(n_tr, 1)
    n_te = np.bincount(code_te, minlength=n_ath)
    t_te = np.bincount(code_te, weights=t_all[keep_te], minlength=n_ath) \
        / np.maximum(n_te, 1)
    trained = np.flatnonzero(n_tr > 0)

    safe_raw = np.maximum(raw_of, 0)
    person_of = np.where(raw_of >= 0, person_of_raw[safe_raw], -1)
    pool_of = np.where(raw_of >= 0, pool_of_raw[safe_raw], -1)
    by_key = _latestBy(raw_of * 2 + sp_of, t_tr, trained)
    by_raw = _latestBy(raw_of, t_tr, trained)
    by_person = _latestBy(person_of, t_tr, trained)

    theta = np.array(out["theta"], dtype=np.float64, copy=True)
    b = D_tr.unpack(theta)                       # views into theta
    a = b["a"]
    # ! A FIT WITHOUT RATINGS (no athlete pools: no tilt, no amplitude) has
    #   nothing to carry but the ability; predictHeldOut then reads h = 1
    has_rating = out.get("rating") is not None
    rating = (np.array(out["rating"], dtype=np.float64, copy=True) if has_rating
              else np.full(n_ath, np.nan))

    # ★ A RATING FOR A CARRIED ABILITY. ratingsFromAbility is
    #   100 * pool_mean / exp(a - a_bar), so rating * exp(a) is one number
    #   per pool; read it back off the fitted seasons (median, in logs) and
    #   the carried season's rating follows from its ability and its pool --
    #   including a pool the person never raced in before.
    ok = trained[np.isfinite(rating[trained]) & (rating[trained] > 0)]
    log_k = np.full(len(pool_names), np.nan)
    if ok.size:
        lk = np.log(rating[ok]) + a[ok]
        p_ok = pool_of[ok]
        for p in np.unique(p_ok[p_ok >= 0]):
            log_k[p] = float(np.median(lk[p_ok == p]))

    need = np.unique(code_te)
    how = np.full(n_ath, HOW_NONE, dtype=np.int64)
    src = np.full(n_ath, -1, dtype=np.int64)
    own = need[n_tr[need] > 0]
    how[own] = HOW_OWN
    src[own] = own
    rest = need[n_tr[need] == 0]

    # the key's latest season (same sport first under --split-ability)
    s_key = by_key(raw_of[rest] * 2 + sp_of[rest])
    s_key = np.where(s_key >= 0, s_key, by_raw(raw_of[rest]))
    shift = np.zeros(rest.size)
    src_rest = s_key.copy()
    how_rest = np.where(s_key >= 0, HOW_CARRIED, HOW_NONE)

    tie = _tieTables(out, D_tr)
    if tie is not None:
        mom, names = tie
        floor = float(getattr(D_tr, "tie_floor", 0.0) or 0.0)
        labels = [str(n) for n in pool_names] + ["?"]
        if split_ability:
            labels = [f"{p}:{s}" for p in labels for s in ("XC", "TF")]
        n_lab = len(labels)
        index = {lab: i for i, lab in enumerate(labels)}
        trans = np.full(n_lab * n_lab, -1, dtype=np.int64)
        for t_i, name in enumerate(names):
            lo, _, hi = name.partition(">")
            if lo in index and hi in index:
                trans[index[lo] * n_lab + index[hi]] = t_i
        nb = len(tie_bands) + 1
        c_tab = np.full((len(names), nb + 1), np.nan)      # col 0 = all-band
        m_tab = np.full((len(names), nb + 1), np.nan)
        for t_i, band, c, m, _s in mom:
            c_tab[int(t_i), int(band) + 1] = c
            m_tab[int(t_i), int(band) + 1] = m

        def label(code):
            p = np.where(pool_of[code] >= 0, pool_of[code], len(pool_names))
            return p * 2 + sp_of[code] if split_ability else p

        def walk(s, k):
            """(moved?, c + m dt) from season s to held-out season k."""
            t_code = trans[label(s) * n_lab + label(k)]
            band = np.digitize(np.nan_to_num(rating[s], nan=100.0), tie_bands)
            ti = np.maximum(t_code, 0)
            c = c_tab[ti, band + 1]
            m = m_tab[ti, band + 1]
            c = np.where(np.isfinite(m), c, c_tab[ti, 0])
            m = np.where(np.isfinite(m), m, m_tab[ti, 0])
            dt = np.maximum((t_te[k] - t_tr[s]) / DAYS_PER_YEAR, max(floor, 1e-3))
            moved = (t_code >= 0) & np.isfinite(m)
            return moved, np.where(moved, np.nan_to_num(c) + np.nan_to_num(m) * dt, 0.0)

        # ★ THE PERSON'S LATEST SEASON, AS THE TIE CHAINS THEM (seasonTiePairs
        #   links one person's seasons in date order, whatever the pools); a
        #   transition the fit left untied falls back to the key's own latest
        #   season, moved by its same-pool walk if that one is tied
        s_per = by_person(person_of[rest])
        has_p = s_per >= 0
        mv_p, d_p = walk(np.maximum(s_per, 0), rest)
        use_p = has_p & mv_p
        has_k = s_key >= 0
        mv_k, d_k = walk(np.maximum(s_key, 0), rest)
        src_rest = np.where(use_p, s_per, s_key)
        shift = np.where(use_p, d_p, np.where(has_k & mv_k, d_k, 0.0))
        how_rest = np.where(use_p | (has_k & mv_k), HOW_TIED,
                            np.where(has_k, HOW_CARRIED, HOW_NONE))

    go = src_rest >= 0
    k_go, s_go = rest[go], src_rest[go]
    a[k_go] = a[s_go] + shift[go]
    for blk in ("beta", "g"):
        if b.get(blk) is not None:
            b[blk][k_go] = b[blk][s_go]
    how[rest] = how_rest
    src[rest] = src_rest
    rating[rest] = np.nan
    unrated = np.zeros(0, dtype=np.int64)
    if has_rating:
        lk_new = log_k[np.maximum(pool_of[k_go], 0)]
        rating[k_go] = np.where(pool_of[k_go] >= 0, np.exp(lk_new - a[k_go]), np.nan)
        # a pool with no fitted season has no pool mean to rate against
        unrated = k_go[~np.isfinite(rating[k_go])]
        how[unrated] = HOW_NONE

    out2 = dict(out)
    out2["theta"] = theta
    out2["rating"] = rating if has_rating else None
    fac = out.get("tilt_pool_factor")
    if fac is not None and out.get("tilt_rating") is not None:
        tr = np.array(out["tilt_rating"], dtype=np.float64, copy=True)
        tr[rest] = rating[rest] * np.asarray(fac)[np.maximum(pool_of[rest], 0)]
        out2["tilt_rating"] = tr

    # per held-out row
    status, tr_rows_row = rowStatus(cols, keep_tr, keep_te, person_of_raw)
    how_row = how[code_te].astype(np.int8)
    src_row = src[code_te]
    source_rows = np.where(src_row >= 0, n_tr[np.maximum(src_row, 0)], 0)
    c_cnt = np.bincount(np.asarray(D_tr.cell, dtype=np.int64), minlength=D_tr.n_cell)
    cell_seen = c_cnt[np.asarray(D_te.cell, dtype=np.int64)] > 0
    info = {"status": status, "how": how_row,
            "train_races": tr_rows_row.astype(np.int32),
            "source_rows": source_rows.astype(np.int32),
            "cell_seen": cell_seen,
            "n_own": int(own.size), "n_carried": int((how_rest == HOW_CARRIED).sum()),
            "n_tied": int((how_rest == HOW_TIED).sum()),
            "n_uncarried": int((~go).sum()) + int(unrated.size)}
    return out2, info


# ------------------------------------------------------------------ #
# THE NUMBERS
# ------------------------------------------------------------------ #

def pairGroups(race, pool, dist_m=None):
    """One code per (race, pool, distance): the unit head-to-head is read
    in. race is the (cell, day) code."""
    race = np.asarray(race, dtype=np.int64)
    pool = np.asarray(pool)
    _, p_code = np.unique(pool.astype(str), return_inverse=True)
    parts = [race, p_code.reshape(-1).astype(np.int64)]
    if dist_m is not None:
        parts.append(np.rint(np.asarray(dist_m, dtype=np.float64)).astype(np.int64))
    key = np.stack(parts, axis=1)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    return inv.reshape(-1).astype(np.int64)


def pairwiseCounts(group, y, pred, labels=(), n_labels=()):
    """Per group: concordant pairs and pairs, over pairs of rows of one
    group with distinct y. Concordant = the predicted order is the actual
    order; equal predictions count one half.

    labels: optional per-row small-int arrays (with their n_labels); a pair
    is filed under the LOWER of its two labels (for the training-race
    buckets, the less-known runner). Returns (conc, pairs, per-label list of
    (conc, pairs) matrices [n_group, n_label])."""
    group = np.asarray(group, dtype=np.int64)
    y = np.asarray(y, dtype=np.float64)
    pred = np.asarray(pred, dtype=np.float64)
    n_group = int(group.max()) + 1 if group.size else 0
    conc = np.zeros(n_group)
    pairs = np.zeros(n_group)
    per = [(np.zeros((n_group, n)), np.zeros((n_group, n))) for n in n_labels]
    if not group.size:
        return conc, pairs, per
    order = np.argsort(group, kind="stable")
    g = group[order]
    starts = np.flatnonzero(np.r_[True, g[1:] != g[:-1]])
    ends = np.r_[starts[1:], g.size]
    big = np.flatnonzero(ends - starts >= 2)
    labs = [np.asarray(lab, dtype=np.int64)[order] for lab in labels]
    ys, ps = y[order], pred[order]
    for i in big:
        lo, hi = starts[i], ends[i]
        yy, pp = ys[lo:hi], ps[lo:hi]
        n = hi - lo
        iu, ju = np.triu_indices(n, k=1)
        dy = np.sign(yy[iu] - yy[ju])
        live = dy != 0
        if not live.any():
            continue
        dp = np.sign(pp[iu] - pp[ju])[live]
        dy = dy[live]
        score = np.where(dp == 0, 0.5, (dp == dy).astype(np.float64))
        gi = g[lo]
        conc[gi] = score.sum()
        pairs[gi] = score.size
        for (mc, mp), lab, nl in zip(per, labs, n_labels):
            ll = lab[lo:hi]
            pl = np.minimum(ll[iu], ll[ju])[live]
            okl = pl >= 0
            mc[gi] += np.bincount(pl[okl], weights=score[okl], minlength=nl)
            mp[gi] += np.bincount(pl[okl], minlength=nl)
    return conc, pairs, per


def pairwiseSummary(conc, pairs, weight=None):
    """(per-race mean accuracy, pooled accuracy, races, pairs). weight: per
    group (a bootstrap multiplicity), default 1."""
    has = pairs > 0
    if not has.any():
        return float("nan"), float("nan"), 0, 0
    w = np.ones(conc.size) if weight is None else np.asarray(weight, dtype=np.float64)
    acc = np.zeros(conc.size)
    acc[has] = conc[has] / pairs[has]
    wh = w * has
    per_race = float((wh * acc).sum() / max(wh.sum(), 1e-300))
    pooled = float((w * conc).sum() / max((w * pairs).sum(), 1e-300))
    return per_race, pooled, int(has.sum()), int(pairs[has].sum())


def _wquantile(v_sorted, w_sorted, q):
    cw = np.cumsum(w_sorted)
    if cw.size == 0 or cw[-1] <= 0:
        return float("nan")
    i = int(np.searchsorted(cw, q * cw[-1], side="left"))
    return float(v_sorted[min(i, v_sorted.size - 1)])


def errorStats(e, w=None, order=None):
    """bias (mean e), median |e|, p90 |e| and sd, weighted by multiplicity
    `w` (a bootstrap resample) or 1. The quantiles are the lower weighted
    ones, the same with w = 1 as on a replicated sample. `order`: argsort
    of |e|, when the caller asks many times of one e."""
    e = np.asarray(e, dtype=np.float64)
    w = np.ones(e.size) if w is None else np.asarray(w, dtype=np.float64)
    if e.size == 0 or w.sum() <= 0:
        return {"n": 0, "bias": np.nan, "med": np.nan, "p90": np.nan, "sd": np.nan}
    ae = np.abs(e)
    o = np.argsort(ae, kind="stable") if order is None else order
    mean = float((w * e).sum() / w.sum())
    sd = float(np.sqrt(max((w * (e - mean) ** 2).sum() / w.sum(), 0.0)))
    return {"n": int(e.size), "bias": mean,
            "med": _wquantile(ae[o], w[o], 0.5),
            "p90": _wquantile(ae[o], w[o], 0.9), "sd": sd}


def bucketOf(train_races):
    """TRAIN_BUCKETS index per row; -1 for none (a cold start)."""
    t = np.asarray(train_races, dtype=np.int64)
    out = np.full(t.size, -1, dtype=np.int64)
    for i, (lo, hi) in enumerate(TRAIN_BUCKETS):
        out[(t >= lo) & (True if hi is None else t <= hi)] = i
    return out


def bucketLabel(i):
    lo, hi = TRAIN_BUCKETS[i]
    return f"{lo}" if lo == hi else (f"{lo}+" if hi is None else f"{lo}-{hi}")


def _codes(values):
    names, inv = np.unique(np.asarray(values).astype(str), return_inverse=True)
    return list(names), inv.reshape(-1).astype(np.int64)


SPORT_NAMES = ("XC", "TF")


def _splits(d):
    """[(title, [(label, row mask, per-row label code or None)])] for the
    breakdowns, over the rows of dump-like dict d."""
    out = []
    if "sport" in d:
        sp = np.asarray(d["sport"]).astype(np.int64)
        out.append(("by sport", [(SPORT_NAMES[s] if s < 2 else str(s), sp == s)
                                 for s in np.unique(sp)]))
    if "pool" in d:
        names, pc = _codes(d["pool"])
        out.append(("by pool", [(nm, pc == i) for i, nm in enumerate(names)]))
    if "train_races" in d:
        bk = bucketOf(d["train_races"])
        out.append(("by the athlete's training races",
                    [(f"{bucketLabel(i)} races", bk == i)
                     for i in range(len(TRAIN_BUCKETS))]))
    if "cell_seen" in d:
        # in label order: 0 = new, 1 = seen (_pairLabelSets)
        cs = np.asarray(d["cell_seen"], dtype=bool)
        out.append(("by course", [("NEW (its prior)", ~cs),
                                  ("seen in training", cs)]))
    return out


def _pairLabelSets(d):
    """Per breakdown, the per-row label code and count for pairwiseCounts,
    in _splits' order (the pair is filed under its lower label)."""
    sets = []
    if "sport" in d:
        sp = np.asarray(d["sport"]).astype(np.int64)
        u = list(np.unique(sp))
        sets.append((np.searchsorted(u, sp), len(u)))
    if "pool" in d:
        names, pc = _codes(d["pool"])
        sets.append((pc, len(names)))
    if "train_races" in d:
        sets.append((bucketOf(d["train_races"]), len(TRAIN_BUCKETS)))
    if "cell_seen" in d:
        sets.append((np.asarray(d["cell_seen"], dtype=np.int64), 2))
    return sets


def _subset(d, m):
    return {k: (np.asarray(v)[m] if isinstance(v, np.ndarray) and v.ndim == 1
                and v.size == m.size else v) for k, v in d.items()}


def scoreLines(d, min_rows=200, prefix="        "):
    """The scorecard of ONE run's held-out rows (a dump-like dict: y, pred,
    covered, group, and optionally status, sport, pool, train_races,
    cell_seen). Pure."""
    y = np.asarray(d["y"], dtype=np.float64)
    pred = np.asarray(d["pred"], dtype=np.float64)
    cov = np.asarray(d["covered"], dtype=bool) & np.isfinite(pred)
    s = _subset(d, cov)
    e = y[cov] - pred[cov]
    st = errorStats(e)
    sets = _pairLabelSets(s)
    conc, pairs, per = pairwiseCounts(s["group"], s["y"], s["pred"],
                                      [x[0] for x in sets], [x[1] for x in sets])
    pr, pooled, n_r, n_p = pairwiseSummary(conc, pairs)
    out = [f"[joint] scorecard: bias {st['bias']:+.5f}  median |err| "
           f"{st['med']:.5f}  p90 |err| {st['p90']:.5f}  pairwise {pr:.4f} "
           f"(per race)  {int(cov.sum()):,} rows",
           f"{prefix}time error = log(actual / predicted), + = slower than "
           f"predicted; sd {st['sd']:.5f}",
           f"{prefix}head-to-head: {pr:.4f} per race (mean over {n_r:,} races "
           f"of each race's share), {pooled:.4f} pooled over {n_p:,} pairs",
           f"{prefix}{'':<30}{'rows':>10}{'bias':>10}{'med|e|':>9}{'p90|e|':>9}"
           f"{'pairwise':>10}{'races':>8}"]
    for (title, parts), (mc, mp) in zip(_splits(s), per):
        out.append(f"{prefix}{title}:")
        for j, (name, m) in enumerate(parts):
            if int(m.sum()) < min_rows:
                continue
            sj = errorStats(e[m])
            pj, _pp, rj, _np = pairwiseSummary(mc[:, j], mp[:, j])
            out.append(f"{prefix}  {name:<28}{int(m.sum()):>10,}{sj['bias']:>+10.5f}"
                       f"{sj['med']:>9.5f}{sj['p90']:>9.5f}{pj:>10.4f}{rj:>8,}")
    if "status" in d:
        status = np.asarray(d["status"])
        n_all = status.size
        out.append(f"{prefix}cold starts (NOT in the numbers above): "
                   f"{int((status == STATUS_NEW_PERSON).sum()):,} rows of people "
                   f"with no training row, {int((status == STATUS_NEW_POOL).sum()):,} "
                   f"of people known only in another pool, of {n_all:,} held-out rows")
        m = (status == STATUS_NEW_POOL) & np.isfinite(pred)
        if int(m.sum()) >= min_rows:
            sc = errorStats(y[m] - pred[m])
            out.append(f"{prefix}  new pool, carried by the season tie: "
                       f"{int(m.sum()):,} rows, bias {sc['bias']:+.5f}  median "
                       f"|err| {sc['med']:.5f}  p90 |err| {sc['p90']:.5f}")
    return out


# ------------------------------------------------------------------ #
# TWO RUNS, THE SAME ROWS, THE SE BY RACE
# ------------------------------------------------------------------ #

def raceBootstrap(race, n_rep=None, seed=0):
    """Yield per-race multiplicities: races drawn whole, with replacement.
    race: per-row cluster code (dense)."""
    race = np.asarray(race, dtype=np.int64)
    n_race = int(race.max()) + 1 if race.size else 0
    rng = np.random.default_rng(seed)
    for _ in range(bootReps() if n_rep is None else int(n_rep)):
        yield np.bincount(rng.integers(0, n_race, n_race), minlength=n_race)


STAT_NAMES = ("bias", "median |err|", "p90 |err|", "pairwise (per race)")


class _Subset:
    """One breakdown's rows and pairs, |err| sorted once for every resample."""

    def __init__(self, name, m, e_b, e_t, c_b, c_t, pairs):
        self.name, self.m = name, m
        self.e_b, self.e_t = e_b[m], e_t[m]
        self.o_b = np.argsort(np.abs(self.e_b), kind="stable")
        self.o_t = np.argsort(np.abs(self.e_t), kind="stable")
        self.c_b, self.c_t, self.pairs = c_b, c_t, pairs

    def stats(self, w_row=None, w_grp=None):
        """([bias, med, p90, pairwise] of base, the same of try)."""
        wr = None if w_row is None else w_row[self.m]
        sb = errorStats(self.e_b, wr, self.o_b)
        st = errorStats(self.e_t, wr, self.o_t)
        return (np.array([sb["bias"], sb["med"], sb["p90"],
                          pairwiseSummary(self.c_b, self.pairs, w_grp)[0]]),
                np.array([st["bias"], st["med"], st["p90"],
                          pairwiseSummary(self.c_t, self.pairs, w_grp)[0]]))


def pairedLines(base, test, n_rep=None, seed=0, min_rows=200, prefix="  "):
    """Two runs' scorecards on the rows BOTH scored, with the paired change
    and its race-bootstrap SE, overall and per breakdown: one table per
    number. base / test: dump-like dicts ALREADY ALIGNED row for row
    (switch_scorecard does the alignment). Pure.

    ! THE BIAS TABLE'S CHANGE IS |bias try| - |bias base| (nearer zero is
      better), and its SE is of that difference, on the same resamples.
    ! seed 0 IS A LABEL, NOT A SETTING: the resamples are fixed so a rerun
      prints the same SE, and B (bootReps) is what sets its precision."""
    y = np.asarray(base["y"], dtype=np.float64)
    pb = np.asarray(base["pred"], dtype=np.float64)
    pt = np.asarray(test["pred"], dtype=np.float64)
    cov = (np.asarray(base["covered"], dtype=bool) & np.asarray(test["covered"], dtype=bool)
           & np.isfinite(pb) & np.isfinite(pt))
    s = _subset(base, cov)
    e_b, e_t = y[cov] - pb[cov], y[cov] - pt[cov]
    _, grp = np.unique(np.asarray(s["group"], dtype=np.int64), return_inverse=True)
    grp = grp.reshape(-1)
    _, race = np.unique(np.asarray(s["race"], dtype=np.int64), return_inverse=True)
    race = race.reshape(-1)
    n_grp = int(grp.max()) + 1 if grp.size else 0
    race_of_grp = np.zeros(n_grp, dtype=np.int64)
    race_of_grp[grp] = race
    sets = _pairLabelSets(s)
    labs, nls = [x[0] for x in sets], [x[1] for x in sets]
    cb, pairs, per_b = pairwiseCounts(grp, y[cov], pb[cov], labs, nls)
    ct, _p2, per_t = pairwiseCounts(grp, y[cov], pt[cov], labs, nls)

    subs = [_Subset("all", np.ones(e_b.size, dtype=bool), e_b, e_t, cb, ct, pairs)]
    for (title, parts), (mcb, mp), (mct, _mp) in zip(_splits(s), per_b, per_t):
        for j, (name, m) in enumerate(parts):
            if int(m.sum()) >= min_rows:
                short = title.replace("by the athlete's ", "").replace("by ", "")
                subs.append(_Subset(f"{short}: {name}", m,
                                    e_b, e_t, mcb[:, j], mct[:, j], mp[:, j]))
    B = bootReps() if n_rep is None else int(n_rep)
    points = [sub.stats() for sub in subs]
    reps = [[] for _ in subs]
    for w_r in raceBootstrap(race, B, seed):
        w_row, w_g = w_r[race], w_r[race_of_grp]
        for i, sub in enumerate(subs):
            reps[i].append(sub.stats(w_row, w_g))
    n_race = int(race.max()) + 1 if race.size else 0
    n_races_of = [int(np.unique(race[sub.m]).size) for sub in subs]
    out = [f"{prefix}SCORECARD on {int(cov.sum()):,} held-out rows both runs scored "
           f"({n_race:,} races): change = try - base, ± its SE over {B} "
           f"resamples of whole races (* = beyond 2 SE)"]
    for k, stat in enumerate(STAT_NAMES):
        better = ("nearer 0 is better; change = |try| - |base|" if k == 0 else
                  "higher is better" if k == 3 else "lower is better")
        out.append(f"{prefix}  {stat} ({better})")
        out.append(f"{prefix}    {'':<36}{'rows':>10}{'races':>8}{'base':>10}"
                   f"{'try':>10}{'change':>11}{'se':>9}")
        for i, sub in enumerate(subs):
            b0, t0 = points[i]
            rb = np.array([r[0][k] for r in reps[i]])
            rt = np.array([r[1][k] for r in reps[i]])
            if k == 0:
                d, dr = abs(t0[k]) - abs(b0[k]), np.abs(rt) - np.abs(rb)
            else:
                d, dr = t0[k] - b0[k], rt - rb
            # ! ONE RACE HAS NOTHING TO RESAMPLE: its "SE" would be 0 and any
            #   change would star. Not estimated below two races.
            n_r = n_races_of[i]
            se = (float(np.nanstd(dr, ddof=1)) if dr.size > 1 and n_r >= 2
                  else float("nan"))
            star = " *" if np.isfinite(se) and abs(d) > 2 * se else ""
            out.append(f"{prefix}    {sub.name:<36}{int(sub.m.sum()):>10,}{n_r:>8,}"
                       f"{b0[k]:>10.5f}{t0[k]:>10.5f}{d:>+11.5f}{se:>9.5f}{star}")
    return out
