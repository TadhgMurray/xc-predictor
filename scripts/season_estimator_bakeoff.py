#!/usr/bin/env python3
"""
season_estimator_bakeoff.py -- which season number predicts the race an
athlete runs next? READ-ONLY.

    /srv/venv/bin/python scripts/season_estimator_bakeoff.py
    /srv/venv/bin/python scripts/season_estimator_bakeoff.py --sport XC --years 2022-2025

★ WHY (owner, 2026-10-07: "I think race importance could be good for deciding
  someone's rating"). The season number on every board is the athlete's
  80th-percentile race (build_ranking_results._SEASON_Q), every race counted
  alike. Whether an important race -- a championship, a deep field -- should
  count for more is a question about prediction, so it is answered the way
  XCRI-26A answers its own ("matched the next weekend's races"): hold out each
  athlete-season's LAST race, compute each candidate season number from the
  races before it, and score how well it orders the athletes who met in that
  held-out race.

  Scores, per sport and pool level:
    order   share of pairs of athletes in the same held-out race that the
            estimate puts in the order they finished (by the race's rating);
            the number that decides. A uniform taper or a hard course moves
            everyone in a race alike and does not touch it.
    mae     mean |held-out rating - estimate| after taking each held-out
            race's own mean error off (the same race-level invariance).

  Candidates: mean, median, the board's q80 (percentile_cont), the decayed
  mean the board also stores, and q80 / mean WEIGHTED by a race's importance:
    front   the race's field strength: mean rating of its top 5 rows,
            standardised within (pool, year) by median and IQR;
    share   the race's season-end share: of its athletes with 3+ races, the
            fraction for whom this race falls within 14 days of their own
            last race (run_joint.seasonEndShare's definition).
  weight = exp(beta * importance). beta is NOT set here: it is chosen on half
  the athlete-seasons (split by person id) from a search grid and scored on
  the other half, so the printed test number is out of sample. beta = 0 is
  every race alike, so "importance does not help" is always a possible answer.

! THE BOARD'S OWN ROWS AND RULES: ranking_results' rated rows, and the
  season-outlier cut the board applies (a race more than _SEASON_OUTLIER_PTS
  under the season median is not counted), read from build_ranking_results.
"""
import argparse
import ast
import io
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

SHARE_DAYS = 14
FRONT_K = 5
MIN_TRAIN = 3                 # the board's "3+ races"
BETA_GRID = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)
MAX_PER_RACE = 60             # pairs per held-out race: at most 60 athletes (1,770 pairs)


def boardConstants():
    """_SEASON_Q, _DECAY_K and _SEASON_OUTLIER_PTS from the board builder,
    read without importing it."""
    src = io.open(os.path.join(_ROOT, "racecast", "build_ranking_results.py"),
                  encoding="utf-8").read()
    ns = {}
    for node in ast.parse(src).body:
        if (isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", "") in
                ("_SEASON_Q", "_DECAY_K", "_SEASON_OUTLIER_PTS")):
            exec(ast.get_source_segment(src, node), ns)   # noqa: S102
    return float(ns["_SEASON_Q"]), float(ns["_DECAY_K"]), float(ns["_SEASON_OUTLIER_PTS"])


# ------------------------------------------------------------------ pure

def _groupStarts(g):
    return np.flatnonzero(np.r_[True, g[1:] != g[:-1]])


def groupedQuantile(g, x, w, q):
    """Weighted q-quantile of x per group g (rows sorted by g then x), the
    weighted form of percentile_cont: row i sits at (weight below it) /
    (weight of every row but the last), so the first row is at 0, the last
    at 1, and with equal weights it IS percentile_cont -- the board's q80 at
    beta = 0. A one-row group is its row."""
    starts = _groupStarts(g)
    cnt = np.diff(np.r_[starts, g.size])
    gi = np.repeat(np.arange(starts.size), cnt)
    cw = np.cumsum(w)
    before = cw - w - np.repeat(np.r_[0.0, cw[starts[1:] - 1]], cnt)
    ends = np.r_[starts[1:], g.size] - 1
    denom = np.repeat(before[ends], cnt)
    pos = np.where(denom > 0, before / np.where(denom > 0, denom, 1.0), 0.0)
    key = gi + np.minimum(pos, 1.0 - 1e-12) * (pos < 1.0) + (pos >= 1.0) * (1.0 - 1e-12)
    want = np.arange(starts.size) + min(q, 1.0 - 1e-12)
    hi = np.searchsorted(key, want, side="left")
    hi = np.minimum(np.maximum(hi, starts), ends)
    lo = np.maximum(hi - 1, starts)
    plo, phi = pos[lo], pos[hi]
    t = np.where(phi > plo, (q - plo) / np.where(phi > plo, phi - plo, 1.0), 0.0)
    t = np.clip(t, 0.0, 1.0)
    out = x[lo] + t * (x[hi] - x[lo])
    return np.where(cnt == 1, x[starts], out)


def groupedPercentileCont(g, x, q):
    """percentile_cont(q) per group (rows sorted by g then x)."""
    starts = _groupStarts(g)
    cnt = np.diff(np.r_[starts, g.size])
    pos = q * (cnt - 1)
    lo = np.floor(pos).astype(np.int64)
    hi = np.minimum(lo + 1, cnt - 1)
    return x[starts + lo] + (pos - lo) * (x[starts + hi] - x[starts + lo])


def groupedMean(g, x, w=None):
    starts = _groupStarts(g)
    if w is None:
        w = np.ones_like(x)
    return np.add.reduceat(x * w, starts) / np.add.reduceat(w, starts)


def orderAccuracy(race, est, target, max_per_race=MAX_PER_RACE, seed=0):
    """(share of concordant pairs, pairs) over athletes sharing a held-out
    race: a pair counts 1 if the estimate orders it as the race did, 0.5 on
    an estimate tie; target ties are skipped."""
    rng = np.random.default_rng(seed)
    order = np.argsort(race, kind="stable")
    race, est, target = race[order], est[order], target[order]
    starts = _groupStarts(race)
    ends = np.r_[starts[1:], race.size]
    good = 0.0
    n = 0
    for s, e in zip(starts, ends):
        if e - s < 2:
            continue
        idx = np.arange(s, e)
        if idx.size > max_per_race:
            idx = rng.choice(idx, max_per_race, replace=False)
        de = est[idx][:, None] - est[idx][None, :]
        dt = target[idx][:, None] - target[idx][None, :]
        iu = np.triu_indices(idx.size, 1)
        de, dt = de[iu], dt[iu]
        keep = dt != 0
        de, dt = de[keep], dt[keep]
        good += float(np.sum(np.sign(de) == np.sign(dt))) + 0.5 * float(np.sum(de == 0))
        n += int(dt.size)
    return (good / n if n else float("nan")), n


def raceMAE(race, est, target):
    """mean |error| after each held-out race's mean error is taken off."""
    err = target - est
    order = np.argsort(race, kind="stable")
    r, e = race[order], err[order]
    starts = _groupStarts(r)
    cnt = np.diff(np.r_[starts, r.size])
    m = np.repeat(np.add.reduceat(e, starts) / cnt, cnt)
    keep = np.repeat(cnt >= 2, cnt)
    return float(np.mean(np.abs(e - m)[keep])) if keep.any() else float("nan")


def raceImportance(season_key, race_key, rating, date_days, pool_year):
    """(front z, season-end share) per row's race. All arrays are per row."""
    # front: mean of the race's top FRONT_K ratings
    o = np.lexsort((-rating, race_key))
    rk, rt = race_key[o], rating[o]
    starts = _groupStarts(rk)
    cnt = np.diff(np.r_[starts, rk.size])
    pos = np.arange(rk.size) - np.repeat(starts, cnt)
    top = pos < FRONT_K
    front_r = (np.bincount(np.repeat(np.arange(starts.size), cnt)[top], weights=rt[top],
                           minlength=starts.size)
               / np.minimum(cnt, FRONT_K))
    race_of_row = np.empty(rk.size, dtype=np.int64)
    race_of_row[o] = np.repeat(np.arange(starts.size), cnt)
    front = front_r[race_of_row]
    # standardise within (pool, year) by median and IQR, per race not per row
    z = np.zeros_like(front)
    first_row = np.zeros(starts.size, dtype=np.int64)
    first_row[race_of_row[::-1]] = np.arange(rk.size)[::-1]
    py_race = pool_year[first_row]
    for py in np.unique(py_race):
        m = py_race == py
        v = front_r[m]
        q1, med, q3 = np.percentile(v, [25, 50, 75])
        iqr = q3 - q1 if q3 > q1 else 1.0
        zr = (v - med) / iqr
        rows = np.isin(race_of_row, np.flatnonzero(m))
        z[rows] = zr[np.searchsorted(np.flatnonzero(m), race_of_row[rows])]
    # season-end share: this athlete-season's last date, among seasons with
    # 3+ rows, within SHARE_DAYS after the race date
    so = np.argsort(season_key, kind="stable")
    sk = season_key[so]
    s_starts = _groupStarts(sk)
    s_cnt = np.diff(np.r_[s_starts, sk.size])
    last = np.maximum.reduceat(date_days[so], s_starts)
    last_row = np.empty(sk.size, dtype=np.int64)
    last_row[so] = np.repeat(last, s_cnt)
    voter = np.empty(sk.size, dtype=bool)
    voter[so] = np.repeat(s_cnt >= 3, s_cnt)
    ending = voter & (last_row - date_days <= SHARE_DAYS) & (last_row >= date_days)
    n_v = np.bincount(race_of_row, weights=voter.astype(float), minlength=starts.size)
    n_e = np.bincount(race_of_row, weights=ending.astype(float), minlength=starts.size)
    share = np.where(n_v > 0, n_e / np.maximum(n_v, 1), 0.0)[race_of_row]
    return z, share


def estimates(g, x, days, imp, beta, q, decay_k):
    """Every candidate per group (rows sorted by g then x). imp: dict of
    covariate -> per-row value; beta: dict covariate -> chosen beta."""
    out = {
        "mean": groupedMean(g, x),
        "median": groupedPercentileCont(g, x, 0.5),
        "q80": groupedPercentileCont(g, x, q),
    }
    starts = _groupStarts(g)
    last = np.repeat(np.maximum.reduceat(days, starts), np.diff(np.r_[starts, g.size]))
    out["decayed"] = groupedMean(g, x, np.power(decay_k, last - days))
    for name, z in imp.items():
        b = beta.get(name, 0.0)
        w = np.exp(b * z)
        out[f"q80_w_{name}"] = groupedQuantile(g, x, w, q)
        out[f"mean_w_{name}"] = groupedMean(g, x, w)
    return out


def prepare(rows, q, decay_k, outlier_pts):
    """rows: dict of arrays person_id, pool, year, rating, days, meet_id,
    div_id, event_id. Returns (train arrays sorted by group then rating,
    held-out per group, importance per training row)."""
    pid, pool, year = rows["person_id"], rows["pool"], rows["year"]
    pool_code = np.unique(pool.astype(str), return_inverse=True)[1].ravel()
    season = np.unique(np.stack([pid.astype(np.int64), pool_code, year.astype(np.int64)], 1),
                       axis=0, return_inverse=True)[1].ravel()
    race_key = np.unique(np.stack([rows["meet_id"], rows["div_id"], rows["event_id"]], 1)
                         .astype(np.int64), axis=0, return_inverse=True)[1].ravel()
    pool_year = np.unique(np.stack([pool_code, year.astype(np.int64)], 1),
                          axis=0, return_inverse=True)[1].ravel()
    z, share = raceImportance(season, race_key, rows["rating"], rows["days"], pool_year)
    # the held-out race: each season's last (date, then the row's own order)
    o = np.lexsort((np.arange(season.size), rows["days"], season))
    s_starts = _groupStarts(season[o])
    s_cnt = np.diff(np.r_[s_starts, season.size])
    last_idx = o[np.r_[s_starts[1:], season.size] - 1]
    eligible = s_cnt >= MIN_TRAIN + 1
    held = np.zeros(season.size, dtype=bool)
    held[last_idx[eligible]] = True
    elig_season = np.zeros(season.max() + 1, dtype=bool)
    elig_season[season[last_idx[eligible]]] = True
    train = (~held) & elig_season[season]
    # the board's outlier cut, on the training rows
    tg = season[train]
    tx = rows["rating"][train]
    oo = np.lexsort((tx, tg))
    med = groupedPercentileCont(tg[oo], tx[oo], 0.5)
    med_by_season = np.full(season.max() + 1, np.nan)
    med_by_season[np.unique(tg[oo])] = med
    train &= rows["rating"] >= med_by_season[season] - outlier_pts
    tr = np.flatnonzero(train)
    tr = tr[np.lexsort((rows["rating"][tr], season[tr]))]
    # a season must still have MIN_TRAIN training rows
    cnt = np.bincount(season[tr], minlength=season.max() + 1)
    tr = tr[cnt[season[tr]] >= MIN_TRAIN]
    groups = np.unique(season[tr])
    h = np.flatnonzero(held)
    h = h[np.isin(season[h], groups)]
    h = h[np.argsort(season[h])]
    return {
        "g": season[tr], "x": rows["rating"][tr], "days": rows["days"][tr],
        "imp": {"front": z[tr], "share": share[tr]},
        "groups": groups, "person": pid[h], "pool": pool[h],
        "held_race": race_key[h], "held_rating": rows["rating"][h],
    }


def score(prep, est_by_group, mask=None):
    m = np.ones(prep["groups"].size, dtype=bool) if mask is None else mask
    acc, n = orderAccuracy(prep["held_race"][m], est_by_group[m], prep["held_rating"][m])
    return acc, n, raceMAE(prep["held_race"][m], est_by_group[m], prep["held_rating"][m])


def chooseBeta(prep, q, decay_k, train_mask):
    """beta per covariate and estimator kind, by order accuracy on the
    training half."""
    chosen = {}
    for cov in prep["imp"]:
        for kind in ("q80", "mean"):
            best = (-1.0, 0.0)
            for b in BETA_GRID:
                est = estimates(prep["g"], prep["x"], prep["days"], {cov: prep["imp"][cov]},
                                {cov: b}, q, decay_k)[f"{kind}_w_{cov}"]
                acc, _n, _m = score(prep, est, train_mask)
                if acc > best[0] + 1e-12:
                    best = (acc, b)
            chosen[(cov, kind)] = best[1]
    return chosen


# ------------------------------------------------------------------ db

def load(sport, y0, y1):
    from database import getConn
    import pandas as pd
    buf = io.StringIO()
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.copy_expert(f"""
                COPY (SELECT person_id, pool, year, speed_rating,
                             (race_date - DATE '2000-01-01') AS days,
                             COALESCE(meet_id, -1), COALESCE(div_id, -1),
                             COALESCE(event_id, -1)
                      FROM   ranking_results
                      WHERE  sport = '{sport}' AND speed_rating IS NOT NULL
                        AND  person_id IS NOT NULL AND race_date IS NOT NULL
                        AND  year BETWEEN {int(y0)} AND {int(y1)}) TO STDOUT""", buf)
        conn.rollback()
    buf.seek(0)
    df = pd.read_csv(buf, sep="\t", header=None,
                     names=["person_id", "pool", "year", "rating", "days",
                            "meet_id", "div_id", "event_id"])
    return {c: df[c].to_numpy() for c in df.columns}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sport", choices=("XC", "TF", "both"), default="both")
    ap.add_argument("--years", default="2022-2025",
                    help="completed seasons to test on (default 2022-2025)")
    a = ap.parse_args()
    y0, y1 = (int(v) for v in a.years.split("-"))
    q, decay_k, outlier = boardConstants()
    print(f"season number on the boards: q{q:.2f}; decay {decay_k}; outlier cut {outlier} pts")
    print(f"held out: each athlete-season's last race; {MIN_TRAIN}+ races before it")
    for sport in (("XC", "TF") if a.sport == "both" else (a.sport,)):
        rows = load(sport, y0, y1)
        print(f"\n=== {sport} {y0}-{y1}: {rows['rating'].size:,} rated rows ===")
        prep = prepare(rows, q, decay_k, outlier)
        half = (prep["person"] % 2) == 0
        chosen = chooseBeta(prep, q, decay_k, half)
        print("  beta chosen on half the athletes (even person ids), scored on the other half:")
        for (cov, kind), b in sorted(chosen.items()):
            print(f"    {kind}_w_{cov:<6} beta {b}")
        betas = {cov: chosen[(cov, "q80")] for cov in prep["imp"]}
        est = estimates(prep["g"], prep["x"], prep["days"], prep["imp"], betas, q, decay_k)
        for cov in prep["imp"]:
            est[f"mean_w_{cov}"] = estimates(prep["g"], prep["x"], prep["days"],
                                             {cov: prep["imp"][cov]},
                                             {cov: chosen[(cov, "mean")]}, q, decay_k)[f"mean_w_{cov}"]
        levels = np.array([str(p).split("_", 1)[0] for p in prep["pool"]])
        for lvl in ["all"] + sorted(set(levels)):
            m = (~half) & ((levels == lvl) if lvl != "all" else True)
            if m.sum() < 200:
                continue
            print(f"\n  [{sport} {lvl}] {int(m.sum()):,} held-out athlete-seasons (test half)")
            print(f"    {'estimator':<16}{'order':>9}{'pairs':>12}{'mae':>8}")
            base = None
            for name in ("q80", "mean", "median", "decayed",
                         "q80_w_front", "q80_w_share", "mean_w_front", "mean_w_share"):
                acc, n, mae = score(prep, est[name], m)
                if name == "q80":
                    base = acc
                d = f"  {100 * (acc - base):+.2f} pts vs q80" if base is not None and name != "q80" else ""
                print(f"    {name:<16}{100 * acc:>8.2f}%{n:>12,}{mae:>8.2f}{d}")


if __name__ == "__main__":
    main()
