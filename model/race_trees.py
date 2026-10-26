#!/usr/bin/env python3
"""
race_trees.py -- the race model, version 1: gradient-boosted trees that
predict what every runner in a race will run. Trains on the table
model/race_rows.py writes and scores the held-out season against the
ratings. No database; CPU, minutes.

    P=/srv/venv/bin/python
    $P -u model/race_trees.py --rows /srv/pred/rows.npz
    $P -u model/race_trees.py --rows /srv/pred/rows.npz --importance --out /srv/pred/race_trees.pkl

★ TWO PARTS THAT ADD UP (docs/PLAN-MODEL-2026-10.md):
    runner  each runner's ln(time) relative to the race's mean: the
            ratings' answer (base) plus a learned correction. It decides
            the places and team scores.
    level   the race's mean ln(time) against the ratings' 5K-equivalent
            mean: distance, course, race type and week of the season. It
            turns relative times into clock times.
  predicted ln(time) = mean base_abs + level + (base + correction), centred.

★ JUDGED ON THE SEASON IT NEVER SAW (races from --val-from), against:
    ratings   base alone: the served rating form (the page's order)
    model     base + the trees' correction
  Order: pairs in the right order, Spearman, within-race miss -- per race,
  paired by race with a 90% interval, by runner band (season opener or not,
  days since the last race), by level, by month, college alone. Times:
  median and mean |% miss| (Kailo's statistic) of the whole prediction,
  against a level table by (level, distance) fitted on the same training
  races.

⚠ TODAY'S RATINGS AND COURSE CELLS (the plan's Tier 1). They flatter both
  sides of the order comparison alike. The LEVEL numbers are flattered
  more, because a thin course's cell has seen the race itself; read them
  as an upper bound until the as-of check.
"""
import argparse
import datetime
import math
import pickle
import random
import statistics

CLIP = 0.10               # ln: a fall or a walk-in is not form
BANDS = ("in season, <=21d", "in season, >21d", "season opener, <=120d",
         "season opener, 121-365d", "season opener, >1yr")
LEVEL_FEATURES = ("distance", "season_week", "meet_type", "is_tfrrs", "course_cell",
                  "has_cell", "level", "female", "form_ln", "field_n",
                  # the day's weather (race_rows 1.3): the page's clock leaves it out
                  "temp_c", "dew_point_c", "precip_mm", "wind_kmh")


# ------------------------------------------------------------------ #
#  pure parts (numpy)
# ------------------------------------------------------------------ #

def raceSlices(race):
    """[(start, stop)] of each race in an array sorted by race id."""
    import numpy as np
    starts = np.flatnonzero(np.r_[True, race[1:] != race[:-1]])
    stops = np.r_[starts[1:], len(race)]
    return list(zip(starts.tolist(), stops.tolist()))


def pairShare(pred, actual):
    """Share of pairs in the actual order; equal times are no evidence,
    equal predictions count one half. Vectorised; None with no pairs."""
    import numpy as np
    dp = np.sign(pred[:, None] - pred[None, :])
    da = np.sign(actual[:, None] - actual[None, :])
    iu = np.triu_indices(len(pred), 1)
    dp, da = dp[iu], da[iu]
    ok = da != 0
    n = int(ok.sum())
    if not n:
        return None
    return float(((dp[ok] == da[ok]).sum() + 0.5 * (dp[ok] == 0).sum()) / n)


def spearman(a, b):
    import numpy as np
    if len(a) < 3:
        return None
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean()
    rb -= rb.mean()
    den = math.sqrt(float((ra * ra).sum() * (rb * rb).sum()))
    return float((ra * rb).sum() / den) if den else None


def bandOf(opener, last_days):
    if opener < 0.5:
        return BANDS[0] if last_days <= 21 else BANDS[1]
    if last_days <= 120:
        return BANDS[2]
    return BANDS[3] if last_days <= 365 else BANDS[4]


def centre(x, slices):
    import numpy as np
    out = np.empty_like(x)
    for a, b in slices:
        out[a:b] = x[a:b] - x[a:b].mean()
    return out


def orderScores(pred, y, slices):
    """Per race (pairs, rho, median |within-race miss| %) for one
    predictor of the centred ln times."""
    import numpy as np
    out = []
    for a, b in slices:
        p, t = pred[a:b], y[a:b]
        e = p - t
        out.append((pairShare(p, t), spearman(p, t),
                    100.0 * float(np.median(np.abs(e - np.median(e))))))
    return out


def paired(xs, ys, draws=2000, seed=0):
    d = [x - y for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(d) < 5:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choice(d) for _k in d) for _i in range(draws))
    return statistics.fmean(d), means[int(0.05 * draws)], means[int(0.95 * draws) - 1]


# ★ EVERYTHING THE ENGINE MADE (owner, 2026-10-26: "why does the model
#   really need the ratings?"). --no-ratings leaves these out and starts
#   from race_rows' raw clock (Riegel only) instead: the ratings, every
#   input read off them, the field inputs ordered by them, the course cell
#   and the page's clock flag. What is left is dates, counts, grade and
#   level, the race, teammates' counts, meetings won, and the raw times.
RATING_INPUTS = frozenset((
    "form_ln", "form_sigma", "form_n", "stale_years", "last_minus_form",
    "ewma30_minus_form", "ewma120_minus_form", "best2_minus_form",
    "season_mean_minus_form", "season_max_minus_form", "season_slope",
    "prev_final_minus_form", "prev_best_minus_form", "prev_mean_minus_form",
    "prev_late_minus_early", "tf_last_minus_form", "tf_best365_minus_form",
    "team_slope", "team_summer", "h2h_vs_base", "course_cell", "has_cell", "page_base"))
# the field inputs that are ORDERS of the base: rebuilt on the raw clock
FIELD_FROM_BASE = ("rank_pct", "gap_to_median", "gap_ahead", "gap_behind")


# ★ EACH RUNNER'S OWN RECORD AGAINST THE RATINGS (owner, 2026-10-26: "there
#   must be more we can get"). Some runners reliably beat their rating --
#   kickers, championship racers, a steep climber -- and some reliably fall
#   short. The table already holds every earlier race's answer and the
#   ratings' guess for it, so each row can carry the runner's own residual
#   history from races STRICTLY BEFORE its day. Shrunk toward 0 (no record
#   = the ratings' own answer).
OWN_FEATURES = ("own_n", "own_mean", "own_ewma", "own_last", "own_season",
                "own_champ", "own_champ_n", "own_spread")
OWN_HALF_LIFE = 60.0      # days
OWN_SHRINK = 3.0          # pseudo-races at 0
OWN_RECENT = 8


def _seasonOfOrdinal(d):
    dd = datetime.date.fromordinal(int(d))
    return dd.year if dd.month >= 8 else dd.year - 1


def ownNew():
    return {"n": 0, "s": 0.0, "day": None, "last": math.nan, "S": 0.0, "W": 0.0,
            "season": None, "sn": 0, "ss": 0.0, "cn": 0, "cs": 0.0, "recent": []}


def ownFeatures(st, day):
    """OWN_FEATURES at `day` (an ordinal) from a state holding only earlier
    races. Pure."""
    nan = math.nan
    if not st or not st["n"]:
        return [0.0, 0.0, 0.0, nan, nan, 0.0, 0.0, nan]
    decay = math.exp(-math.log(2.0) / OWN_HALF_LIFE * (day - st["day"]))
    same = st["season"] == _seasonOfOrdinal(day)
    rec = st["recent"]
    return [float(st["n"]), st["s"] / (st["n"] + OWN_SHRINK),
            st["S"] * decay / (st["W"] * decay + 1.0), st["last"],
            st["ss"] / (st["sn"] + OWN_SHRINK) if same and st["sn"] else nan,
            st["cs"] / (st["cn"] + OWN_SHRINK), float(st["cn"]),
            float(statistics.pstdev(rec)) if len(rec) > 1 else nan]


def ownUpdate(st, day, resids, champs):
    """Fold one day's races into the state. In place."""
    if st["day"] is not None:
        decay = math.exp(-math.log(2.0) / OWN_HALF_LIFE * (day - st["day"]))
        st["S"] *= decay
        st["W"] *= decay
    season = _seasonOfOrdinal(day)
    if st["season"] != season:
        st["season"], st["sn"], st["ss"] = season, 0, 0.0
    for r, c in zip(resids, champs):
        st["n"] += 1
        st["s"] += r
        st["S"] += r
        st["W"] += 1.0
        st["sn"] += 1
        st["ss"] += r
        if c:
            st["cn"] += 1
            st["cs"] += r
        st["recent"] = (st["recent"] + [r])[-OWN_RECENT:]
        st["last"] = r
    st["day"] = day


def ownHistory(pid, day, resid, champ):
    """(features [n, len(OWN_FEATURES)], {pid: state after every row}). A
    row's features read only the runner's races on EARLIER days."""
    import numpy as np
    n = len(pid)
    out = np.full((n, len(OWN_FEATURES)), np.nan, dtype=np.float32)
    order = np.lexsort((day, pid))
    states = {}
    i = 0
    while i < n:
        j = i
        p, d = pid[order[i]], day[order[i]]
        while j < n and pid[order[j]] == p and day[order[j]] == d:
            j += 1
        st = states.get(int(p))
        f = ownFeatures(st, int(d))
        idx = order[i:j]
        out[idx] = f
        if st is None:
            st = states[int(p)] = ownNew()
        ownUpdate(st, int(d), [float(resid[k]) for k in idx], [bool(champ[k]) for k in idx])
        i = j
    return out, states


_OWN_SCALARS = ("n", "s", "day", "last", "S", "W", "season", "sn", "ss", "cn", "cs")


def ownPack(states, min_day):
    """The states of runners who raced on or after min_day, as arrays, for
    the page (race_model): it carries them forward to a race's day."""
    import numpy as np
    keep = [(p, st) for p, st in states.items() if st["day"] is not None and st["day"] >= min_day]
    pack = {"pid": np.array([p for p, _st in keep], dtype=np.int64)}
    for k in _OWN_SCALARS:
        pack[k] = np.array([np.nan if st[k] is None else st[k] for _p, st in keep],
                           dtype=np.float64)
    rec = np.full((len(keep), OWN_RECENT), np.nan, dtype=np.float32)
    for i, (_p, st) in enumerate(keep):
        rec[i, :len(st["recent"])] = st["recent"]
    pack["recent"] = rec
    return pack


def ownState(pack, index, pid):
    """One runner's state back out of ownPack's arrays, or None."""
    import numpy as np
    i = index.get(int(pid))
    if i is None:
        return None
    st = {k: (None if k == "season" and np.isnan(pack[k][i]) else
              int(pack[k][i]) if k in ("n", "day", "season", "sn", "cn") else float(pack[k][i]))
          for k in _OWN_SCALARS}
    st["recent"] = [float(x) for x in pack["recent"][i] if not np.isnan(x)]
    return st


def fieldFromBase(X, base, slices, fidx):
    """FIELD_FROM_BASE recomputed from `base` (race_rows.raceFeatures' own
    definitions), in place. Pure but for numpy."""
    import numpy as np
    for a, b in slices:
        bs = base[a:b]
        n = b - a
        order = np.argsort(bs, kind="stable")
        rank = np.empty(n)
        rank[order] = np.arange(n)
        srt = bs[order]
        ahead = np.full(n, np.nan)
        behind = np.full(n, np.nan)
        ahead[order[1:]] = srt[1:] - srt[:-1]
        behind[order[:-1]] = srt[1:] - srt[:-1]
        X[a:b, fidx["rank_pct"]] = rank / max(n - 1, 1)
        X[a:b, fidx["gap_to_median"]] = bs - np.median(bs)
        X[a:b, fidx["gap_ahead"]] = ahead
        X[a:b, fidx["gap_behind"]] = behind


# ------------------------------------------------------------------ #
#  fitting
# ------------------------------------------------------------------ #

def runnerModel(seed=0, big=False):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(
        loss="squared_error", learning_rate=0.05, max_iter=8000 if big else 4000,
        max_leaf_nodes=255 if big else 63, min_samples_leaf=200 if big else 400,
        l2_regularization=1.0, early_stopping=True,
        validation_fraction=0.1, n_iter_no_change=40, random_state=seed)


def levelModel(seed=0):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(
        loss="absolute_error", learning_rate=0.05, max_iter=1500, max_leaf_nodes=31,
        min_samples_leaf=40, l2_regularization=1.0, early_stopping=True,
        validation_fraction=0.1, n_iter_no_change=40, random_state=seed)


def raceTable(X, y_abs, base_abs, slices, fidx, level_features=LEVEL_FEATURES):
    """One row per race: the level inputs (race constants, and the field's
    mean level, sex and form) and the level target."""
    import numpy as np
    cols = [fidx[k] for k in level_features]
    R = np.empty((len(slices), len(cols)), dtype=np.float32)
    L = np.empty(len(slices), dtype=np.float32)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)     # an input empty for a race
        for i, (a, b) in enumerate(slices):
            R[i] = np.nanmean(X[a:b][:, cols], axis=0)
            L[i] = float(y_abs[a:b].mean() - base_abs[a:b].mean())
    return R, L


def levelTable(R, L, fidx_level):
    """The naive level: median target by (level, distance to 100 m)."""
    import numpy as np
    lv = R[:, fidx_level["level"]]
    dist = np.round(R[:, fidx_level["distance"]] / 100.0)
    cells = {}
    for k, v in zip(zip(np.round(lv, 1).tolist(), dist.tolist()), L.tolist()):
        cells.setdefault(k, []).append(v)
    med = {k: statistics.median(v) for k, v in cells.items()}
    overall = float(np.median(L))
    return lambda Rv: np.array([med.get((round(float(a), 1), float(b)), overall)
                                for a, b in zip(Rv[:, fidx_level["level"]],
                                                np.round(Rv[:, fidx_level["distance"]] / 100.0))])


# ------------------------------------------------------------------ #
#  reporting
# ------------------------------------------------------------------ #

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else float("nan")


def orderReport(name, base_s, model_s, keep=None, labels=("ratings", "model"), extra=()):
    keep = keep if keep is not None else list(range(len(base_s)))
    if not keep:
        return [f"\n{name}: no races"]
    b = [base_s[i] for i in keep]
    m = [model_s[i] for i in keep]
    lines = [f"\n{name}: {len(keep):,} races",
             f"  {'':11} {'pairs':>7} {'rho':>6} {'within':>7}"]
    rows = [(labels[0], b), (labels[1], m)] + [(lab, [x[i] for i in keep]) for lab, x in extra]
    for lab, s in rows:
        lines.append(f"  {lab:11} {100 * _mean(x[0] for x in s):6.2f}% "
                     f"{_mean(x[1] for x in s):6.3f} "
                     f"{statistics.median(x[2] for x in s):6.2f}%")
    got = paired([100 * x[0] if x[0] is not None else None for x in m],
                 [100 * x[0] if x[0] is not None else None for x in b])
    if got:
        lines.append(f"  {labels[1]} - {labels[0]}, pairs: {got[0]:+.2f} points "
                     f"[{got[1]:+.2f}, {got[2]:+.2f}]")
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--val-from", default="2024-08-01")
    ap.add_argument("--out", default=None, help="save both fitted models here (pickle)")
    ap.add_argument("--importance", action="store_true",
                    help="permutation importance of the runner inputs (a few minutes)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-own", dest="own", action="store_false",
                    help="leave out each runner's own record against the ratings")
    ap.add_argument("--big", action="store_true",
                    help="bigger trees (255 leaves, up to 8,000 rounds): slower, more capacity")
    ap.add_argument("--no-ratings", action="store_true",
                    help="a TEST: the same trees with nothing the engine made, from raw "
                         "times on a 5K by Riegel (RATING_INPUTS); the ratings print beside")
    a = ap.parse_args()
    if a.no_ratings and a.out:
        raise SystemExit("--no-ratings is a test, not a model to serve: drop --out")

    import numpy as np
    t0 = datetime.datetime.now()
    z = np.load(a.rows, allow_pickle=False)
    feats = [str(f) for f in z["features"]]
    fidx = {f: i for i, f in enumerate(feats)}
    order = np.argsort(z["race"], kind="stable")
    X = z["X"][order]
    y, base = z["y"][order].astype(np.float64), z["base"][order].astype(np.float64)
    y_abs, base_abs = z["y_abs"][order].astype(np.float64), z["base_abs"][order].astype(np.float64)
    race, day, tfrrs = z["race"][order], z["day"][order], z["tfrrs"][order]
    base_page, base_page_abs = base, base_abs    # the ratings on the page's clock, always
    if a.no_ratings:
        if "base_raw" not in z.files:
            raise SystemExit(f"{a.rows} has no raw times: build it with race_rows 1.4")
        base = z["base_raw"][order].astype(np.float64)
        base_abs = z["base_raw_abs"][order].astype(np.float64)
        X = X.copy()
        fieldFromBase(X, base, raceSlices(race), fidx)
        excluded = set(RATING_INPUTS)
        labels = ("raw times", "no ratings")
    else:
        # ★ THE RAW TIMES ARE FOR THE TEST ONLY: the served model is ratings-based
        excluded = {f for f in feats if f.startswith("raw_")}
        labels = ("ratings", "model")
    own_pack = None
    if a.own:
        t1 = datetime.datetime.now()
        champ = np.nan_to_num(X[:, fidx["meet_type"]], nan=0.0) >= 4
        own, states = ownHistory(z["pid"][order], day, np.clip(y - base, -CLIP, CLIP), champ)
        X = np.hstack([X, own])
        feats = feats + list(OWN_FEATURES)
        fidx = {f: i for i, f in enumerate(feats)}
        own_pack = ownPack(states, int(day.max()) - 730)
        del states
        print(f"  each runner's own record: {len(own_pack['pid']):,} runners kept for the "
              f"page ({datetime.datetime.now() - t1})")
    cut = datetime.date.fromisoformat(a.val_from).toordinal()
    val = day >= cut
    if not (~val).any() or not val.any():
        raise SystemExit(f"{a.rows}: {int((~val).sum()):,} training and {int(val.sum()):,} "
                         f"validation rows around --val-from {a.val_from}; both are needed")
    print(f"{len(y):,} runner-races in {len(np.unique(race)):,} races; "
          f"train {int((~val).sum()):,}, validation {int(val.sum()):,} (from {a.val_from})")

    # ---- the runner part ------------------------------------------- #
    # ! AN INPUT THE TRAINING SEASONS NEVER FILL is dropped, not binned
    #   (sklearn cannot bin a column with no values)
    used = ~np.all(np.isnan(X[~val]), axis=0)
    if not used.all():
        print("  inputs with no training values, left out: "
              + ", ".join(f for f, u in zip(feats, used) if not u))
    used &= np.array([f not in excluded for f in feats])
    X_all, X = X, X[:, used]
    feats_all, feats = feats, [f for f, u in zip(feats, used) if u]
    r = np.clip(y - base, -CLIP, CLIP)
    rm = runnerModel(a.seed, a.big).fit(X[~val], r[~val])
    print(f"runner trees: {rm.n_iter_} rounds ({datetime.datetime.now() - t0})")
    Xv, yv, bv = X[val], y[val], base[val]
    XvA = X_all[val]                     # every input, for the report's bands
    rv, dv, tv = race[val], day[val], tfrrs[val]
    sl = raceSlices(rv)
    pv = centre(bv + rm.predict(Xv), sl)
    base_s, model_s = orderScores(bv, yv, sl), orderScores(pv, yv, sl)
    extra = ([("ratings", orderScores(base_page[val], yv, sl))] if a.no_ratings else [])

    lines = orderReport("ORDER, the validation season", base_s, model_s,
                        labels=labels, extra=extra)
    coll = [i for i, (s, _e) in enumerate(sl) if tv[s] == 1]
    lines += orderReport("ORDER, college (tfrrs) only", base_s, model_s, coll,
                         labels=labels, extra=extra)
    names = {0: "elem", 1: "ms", 2: "hs", 3: "college", 4: "pro"}

    def level_of(s, e):
        v = XvA[s:e, fidx["level"]]
        v = v[~np.isnan(v)]
        return names.get(int(np.median(v)), "?") if len(v) else "?"
    for title, key in (("by month", lambda s, e: datetime.date.fromordinal(int(dv[s])).strftime("%Y-%m")),
                       ("by level", level_of)):
        groups = {}
        for i, (s, e) in enumerate(sl):
            groups.setdefault(key(s, e), []).append(i)
        lines.append(f"\n{title}: races, pairs {labels[0]} -> {labels[1]}")
        for g, ix in sorted(groups.items()):
            if len(ix) >= 20:
                lines.append(f"  {g:10} {len(ix):>6}  "
                             f"{100 * _mean(base_s[i][0] for i in ix):5.1f}% -> "
                             f"{100 * _mean(model_s[i][0] for i in ix):5.1f}%")
    # per runner, by band
    eb = np.empty_like(bv)
    em = np.empty_like(bv)
    for s, e in sl:
        for src, dst in ((bv, eb), (pv, em)):
            d = src[s:e] - yv[s:e]
            dst[s:e] = d - np.median(d)
    bands = np.array([bandOf(o, ld) for o, ld in zip(XvA[:, fidx["opener"]],
                                                       XvA[:, fidx["last_days"]])])
    lines.append(f"\nby runner: median |within-race miss| %, {labels[0]} -> {labels[1]}")
    for band in BANDS:
        m = bands == band
        if m.sum() >= 50:
            lines.append(f"  {band:26} {int(m.sum()):>8}  "
                         f"{100 * np.median(np.abs(eb[m])):.2f}% -> "
                         f"{100 * np.median(np.abs(em[m])):.2f}%")

    # ---- the level part -------------------------------------------- #
    sl_all = raceSlices(race)
    lf = [k for k in LEVEL_FEATURES if k in fidx and k not in excluded]
    if a.no_ratings and "raw_form" in fidx:
        lf.append("raw_form")
    R, L = raceTable(X_all, y_abs, base_abs, sl_all, fidx, lf)
    race_val = np.array([val[s] for s, _e in sl_all])
    # ! THE LEVEL IS LEARNED ONLY WHERE THE RACE HAS A REAL DISTANCE (1.3): a
    #   half-marathon filed as cross country, a track 800, a "Test Race" --
    #   no clock can be right there, and fitting them teaches the trees noise
    known = (np.array([not X_all[s, fidx["page_base"]] == 0 for s, _e in sl_all])
             if "page_base" in fidx else np.ones(len(sl_all), dtype=bool))
    if not (~race_val & known).any():
        known = np.ones(len(sl_all), dtype=bool)        # a table that never says
    lidx = {k: i for i, k in enumerate(lf)}
    fit_l = ~race_val & known
    lused = ~np.all(np.isnan(R[fit_l]), axis=0)
    lm = levelModel(a.seed).fit(R[fit_l][:, lused], L[fit_l])
    naive = levelTable(R[fit_l], L[fit_l], lidx)
    Rv = R[race_val]
    lvl_model, lvl_naive = lm.predict(Rv[:, lused]), naive(Rv)
    # the whole prediction, per runner: race mean of base_abs + level + centred runner part
    tot_m, tot_n = np.empty_like(yv), np.empty_like(yv)
    yav = y_abs[val]
    bav = base_abs[val]
    for k, (s, e) in enumerate(sl):
        mb = bav[s:e].mean()
        tot_m[s:e] = mb + lvl_model[k] + pv[s:e]
        tot_n[s:e] = mb + lvl_naive[k] + bv[s:e]
    # ★ THE PAGE ITSELF (2026-10-25): base_abs is what the site would print
    #   (race_rows.pageBase) wherever page_base is 1, so "page" is the served
    #   clock with no correction at all
    page = base_page_abs[val].copy()
    lines.append("\nTIMES, the validation season: mean |% miss| is Kailo's statistic")
    lines.append(f"  {'':18} {'median':>7} {'mean':>7} {'college':>8} {'clean races':>12} "
                 f"{'known dist':>11} {'college 100+':>13}")
    known_v = np.repeat(known[race_val], [e - s for s, e in sl])
    big = np.repeat(np.array([tv[s] == 1 and e - s >= 100 for s, e in sl]),
                    [e - s for s, e in sl])
    preds = (("page (ratings)", page),
             ("table + " + labels[0], tot_n), ("trees + " + labels[1], tot_m))
    race_miss = {lab: np.array([float(np.mean(tot[s:e] - yav[s:e])) for s, e in sl])
                 for lab, tot in preds}
    # ! ONE SET OF CLEAN RACES FOR EVERY ROW: the trees' own verdict
    clean = np.repeat(np.abs(race_miss["trees + " + labels[1]]) < 0.10, [e - s for s, e in sl])
    for lab, tot in preds:
        miss = np.abs(np.exp(tot - yav) - 1.0) * 100.0
        c = tv == 1
        lines.append(f"  {lab:18} {np.median(miss):6.2f}% {miss.mean():6.2f}% "
                     f"{miss[c].mean() if c.any() else float('nan'):7.2f}% "
                     f"{miss[clean].mean():11.2f}% "
                     f"{miss[known_v].mean() if known_v.any() else float('nan'):10.2f}% "
                     f"{miss[big].mean() if big.any() else float('nan'):12.2f}%")
    lines.append("  (clean races: those the trees put within 10% as a whole -- the rest "
                 "are mostly a wrong distance or a mixed field; listed below)")
    rmm = np.abs(race_miss["trees + " + labels[1]])
    lines.append(f"\nrace level miss, trees: median {100 * np.median(rmm):.2f}%, p90 "
                 f"{100 * np.percentile(rmm, 90):.2f}%, p99 {100 * np.percentile(rmm, 99):.2f}%; "
                 f"{100 * (rmm > 0.05).mean():.1f}% of races over 5%, "
                 f"{100 * (rmm > 0.10).mean():.1f}% over 10%")
    names = z["race_names"] if "race_names" in z.files else None
    keys = z["race_keys"]
    vidx = [int(rv[s]) for s, _e in sl]
    worst = np.argsort(-rmm)[:12]
    lines.append("  the worst races (miss, runners, distance, name, key):")
    for k in worst:
        ri = vidx[k]
        nm = str(names[ri]) if names is not None else "?"
        dist = float(XvA[sl[k][0], fidx["distance"]])
        lines.append(f"    {100 * race_miss['trees + ' + labels[1]][k]:+7.1f}%  {sl[k][1] - sl[k][0]:>4}  "
                     f"{dist:>7.0f}  {nm[:40]:40}  {keys[ri]}")

    # ★ RANGES THAT MEAN WHAT THEY SAY: the place spread by runner band and
    #   the race-level spread, fitted on half the validation races (even
    #   race ids) and checked on the other half; the saved model carries the
    #   ones fitted on all of it.
    def sigmas(mask_races):
        rows = np.repeat(mask_races, [e - s for s, e in sl])
        out = {}
        # ★ BY QUANTILE, NOT BY MAD (1.4, owner's run: 66% inside a band
        #   claiming 68, 86% inside one claiming 90 -- the tails are heavier
        #   than a normal's). The 68.27th percentile of |miss| IS the 1-sigma
        #   half-width that covers 68%; _z90 is the multiple of the time
        #   sigma that covers 90%, measured rather than assumed 1.645.
        for band in BANDS:
            m = rows & (bands == band)
            if m.sum() >= 200:
                out[band] = float(np.percentile(np.abs(em[m]), 68.27))
        allm = em[rows]
        out["_all"] = float(np.percentile(np.abs(allm), 68.27)) if len(allm) else 0.03
        lv = race_miss["trees + " + labels[1]][mask_races]
        out["_level"] = (float(np.percentile(np.abs(lv - np.median(lv)), 68.27))
                         if len(lv) else 0.03)
        ps = np.array([out.get(b, out["_all"]) for b in bands[rows]])
        ts = np.sqrt(ps ** 2 + out["_level"] ** 2)
        out["_z90"] = float(np.percentile(np.abs((tot_m - yav)[rows]) / ts, 90)) if rows.any() else 1.645
        return out
    even = np.array([vidx[k] % 2 == 0 for k in range(len(sl))])
    sg = sigmas(even)
    rows_odd = np.repeat(~even, [e - s for s, e in sl])
    place_sig = np.array([sg.get(b, sg["_all"]) for b in bands])
    time_sig = np.sqrt(place_sig ** 2 + sg["_level"] ** 2)
    err_abs = tot_m - yav
    lines.append("\nRANGES (fitted on half the races, checked on the other half; honest = "
                 "68 / 90)")
    lines.append(f"  place, inside +-1 sigma: {100 * (np.abs(em[rows_odd]) <= place_sig[rows_odd]).mean():.0f}%"
                 f"   time, inside +-1 sigma / the 90% band: "
                 f"{100 * (np.abs(err_abs[rows_odd]) <= time_sig[rows_odd]).mean():.0f}% / "
                 f"{100 * (np.abs(err_abs[rows_odd]) <= sg['_z90'] * time_sig[rows_odd]).mean():.0f}% "
                 f"(the 90% band is {sg['_z90']:.2f} sigma)")
    lines.append("  sigmas: " + ", ".join(f"{k} {100 * v:.2f}%" for k, v in sg.items()
                                          if k != "_z90"))
    sig_all = sigmas(np.ones(len(sl), dtype=bool))
    print("\n".join(lines))

    if a.importance:
        from sklearn.inspection import permutation_importance
        rng = np.random.default_rng(a.seed)
        pick = rng.choice(len(yv), size=min(200_000, len(yv)), replace=False)
        pi = permutation_importance(rm, Xv[pick], np.clip(yv - bv, -CLIP, CLIP)[pick],
                                    n_repeats=2, random_state=a.seed,
                                    scoring="neg_mean_squared_error")
        top = sorted(zip(pi.importances_mean, feats), reverse=True)[:20]
        print("\nrunner inputs by importance (rise in squared error when shuffled, x1e6)")
        for v, f in top:
            print(f"  {f:24} {1e6 * v:8.1f}")
    if a.out:
        with open(a.out, "wb") as fh:
            pickle.dump({"runner": rm, "level": lm, "features": feats,
                         "sigmas": sig_all, "own": own_pack,
                         "all_features": feats_all,
                         "level_features": [f for f, u in zip(lf, lused) if u],
                         "clip": CLIP,
                         "val_from": a.val_from, "made": datetime.date.today().isoformat()}, fh)
        print(f"\nwrote {a.out}")
    print(f"\n({datetime.datetime.now() - t0})")


if __name__ == "__main__":
    main()
