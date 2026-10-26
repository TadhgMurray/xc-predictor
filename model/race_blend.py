#!/usr/bin/env python3
"""
race_blend.py -- several different models on the race table, and the blend
of them. Order in the race (places, team scores) only: the race's level
stays race_trees'. No database; CPU.

    export OMP_NUM_THREADS=$(( $(nproc) / 2 )); P=/srv/venv/bin/python
    PYTHONPATH=/srv/pred/pylib $P -u model/race_blend.py --rows /srv/pred/rows6.npz
    ... --members hgb,raw,lgbm,rank,xgb,mlp      # the default: all that import

★ WHY (owner, 2026-10-26: "MORE MODELS"). A blend of models whose mistakes
  differ beats the best of them. The members, each predicting a runner's
  ln(time) relative to the race:
    hgb    race_trees' trees on the ratings (what the page would serve)
    raw    the same trees with nothing the engine made (race_trees
           --no-ratings): different information, so different mistakes
    lgbm   LightGBM, deeper and row/column-sampled, on the ratings
    rank   LightGBM LambdaRank: trained on the finishing ORDER of each race
           directly, not on times (its score is not in seconds; the blend
           weight takes care of the scale)
    xgb    XGBoost, on the ratings
    mlp    a neural net (torch) on the same inputs, missing ones flagged
  lgbm, rank and xgb need /srv/pred/pylib on PYTHONPATH (installed there,
  never into the site's venv); a member that does not import is skipped.

★ THE BLEND IS FITTED ON HALF THE VALIDATION RACES AND SCORED ON THE OTHER
  HALF (even / odd race ids, as race_trees' ranges): least squares on the
  centred times, one weight per member. Every member and the blend are
  scored on the same odd races, by lead time.
"""
import argparse
import datetime
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import race_trees as RT                                       # noqa: E402

MEMBERS = ("hgb", "raw", "lgbm", "rank", "xgb", "mlp")


# ------------------------------------------------------------------ #
#  members: fit(T) -> predictions on the validation rows, centred
# ------------------------------------------------------------------ #

def _stopRaces(T, frac=0.1):
    """A tenth of the TRAINING races (by race id), for early stopping."""
    tr_race = T["race"][~T["val"]]
    return (tr_race % 10) == 0 if frac == 0.1 else (tr_race % int(1 / frac)) == 0


def fitHgb(T, seed, big=False):
    m = RT.runnerModel(seed, big).fit(T["Xt"], T["r"][~T["val"]])
    return T["base"][T["val"]] + m.predict(T["X"][T["val"]])


def fitLgbm(T, seed):
    import lightgbm as lgb
    stop = _stopRaces(T)
    Xt, rt = T["Xt"], T["r"][~T["val"]]
    m = lgb.LGBMRegressor(n_estimators=8000, learning_rate=0.04, num_leaves=255,
                          min_child_samples=200, subsample=0.8, subsample_freq=1,
                          colsample_bytree=0.8, reg_lambda=1.0, random_state=seed,
                          n_jobs=int(os.environ.get("OMP_NUM_THREADS") or 0) or -1,
                          verbose=-1)
    m.fit(Xt[~stop], rt[~stop], eval_set=[(Xt[stop], rt[stop])],
          callbacks=[lgb.early_stopping(60, verbose=False)])
    print(f"    lgbm: {m.best_iteration_} rounds", flush=True)
    return T["base"][T["val"]] + m.predict(T["X"][T["val"]], num_iteration=m.best_iteration_)


def _groups(race):
    import numpy as np
    starts = np.flatnonzero(np.r_[True, race[1:] != race[:-1]])
    return np.diff(np.r_[starts, len(race)])


def rankLabels(y, race):
    """Relevance 0..30 by finishing order within each race (30 = the
    winner), for LambdaRank. Pure but for numpy."""
    import numpy as np
    out = np.zeros(len(y), dtype=np.int32)
    for a, b in RT.raceSlices(race):
        n = b - a
        order = np.argsort(y[a:b], kind="stable")
        rank = np.empty(n)
        rank[order] = np.arange(n)
        out[a:b] = np.round(30 * (1 - rank / max(n - 1, 1))).astype(np.int32)
    return out


def fitRank(T, seed):
    import lightgbm as lgb
    import numpy as np
    tr = ~T["val"]
    race_t, y_t = T["race"][tr], T["y"][tr]
    stop = _stopRaces(T)
    lab = rankLabels(y_t, race_t)
    m = lgb.LGBMRanker(objective="lambdarank", n_estimators=4000, learning_rate=0.05,
                       num_leaves=127, min_child_samples=200, subsample=0.8, subsample_freq=1,
                       colsample_bytree=0.8, random_state=seed, label_gain=list(range(31)),
                       lambdarank_truncation_level=60,
                       n_jobs=int(os.environ.get("OMP_NUM_THREADS") or 0) or -1, verbose=-1)
    # the ratings' own order as a feature is what it corrects
    Xt = np.hstack([T["Xt"], T["base"][tr][:, None]])
    m.fit(Xt[~stop], lab[~stop], group=_groups(race_t[~stop]),
          eval_set=[(Xt[stop], lab[stop])], eval_group=[_groups(race_t[stop])],
          eval_at=[10], callbacks=[lgb.early_stopping(60, verbose=False)])
    print(f"    rank: {m.best_iteration_} rounds", flush=True)
    Xv = np.hstack([T["X"][T["val"]], T["base"][T["val"]][:, None]])
    return -m.predict(Xv, num_iteration=m.best_iteration_)     # higher score = faster


def fitXgb(T, seed):
    import xgboost as xgb
    stop = _stopRaces(T)
    Xt, rt = T["Xt"], T["r"][~T["val"]]
    m = xgb.XGBRegressor(n_estimators=8000, learning_rate=0.04, tree_method="hist",
                         grow_policy="lossguide", max_leaves=255, max_depth=0,
                         min_child_weight=50, subsample=0.8, colsample_bytree=0.8,
                         reg_lambda=1.0, random_state=seed, early_stopping_rounds=60,
                         n_jobs=int(os.environ.get("OMP_NUM_THREADS") or 0) or None)
    m.fit(Xt[~stop], rt[~stop], eval_set=[(Xt[stop], rt[stop])], verbose=False)
    print(f"    xgb: {m.best_iteration} rounds", flush=True)
    return T["base"][T["val"]] + m.predict(T["X"][T["val"]],
                                           iteration_range=(0, m.best_iteration + 1))


def fitMlp(T, seed, epochs=6, batch=8192):
    """A plain neural net: standardised inputs, a missing-flag per input
    that can be missing, three layers. Trained on the same clipped residual."""
    import numpy as np
    import torch
    torch.manual_seed(seed)
    tr = ~T["val"]
    X = T["X"]
    med = np.nanmedian(T["Xt"], axis=0)
    sd = np.nanstd(T["Xt"], axis=0)
    sd[~(sd > 1e-9)] = 1.0
    holes = np.isnan(T["Xt"]).any(axis=0)

    def prep(M):
        nan = np.isnan(M)
        Z = np.where(nan, med, M)
        Z = (Z - med) / sd
        return np.hstack([Z, nan[:, holes]]).astype(np.float32)

    Xt = torch.from_numpy(prep(T["Xt"]))
    rt = torch.from_numpy(T["r"][tr].astype(np.float32))
    net = torch.nn.Sequential(
        torch.nn.Linear(Xt.shape[1], 256), torch.nn.SiLU(), torch.nn.Dropout(0.1),
        torch.nn.Linear(256, 256), torch.nn.SiLU(), torch.nn.Dropout(0.1),
        torch.nn.Linear(256, 128), torch.nn.SiLU(), torch.nn.Linear(128, 1))
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-4)
    steps = epochs * (len(rt) // batch + 1)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=2e-3, total_steps=steps)
    loss_fn = torch.nn.HuberLoss(delta=0.03)
    net.train()
    for ep in range(epochs):
        perm = torch.randperm(len(rt))
        tot = 0.0
        for k in range(0, len(rt), batch):
            idx = perm[k:k + batch]
            opt.zero_grad()
            loss = loss_fn(net(Xt[idx]).squeeze(1), rt[idx])
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss) * len(idx)
        print(f"    mlp epoch {ep + 1}/{epochs}: loss {tot / len(rt):.6f}", flush=True)
    net.eval()
    out = []
    Xv = prep(X[T["val"]])
    with torch.no_grad():
        for k in range(0, len(Xv), 65536):
            out.append(net(torch.from_numpy(Xv[k:k + 65536])).squeeze(1).numpy())
    return T["base"][T["val"]] + np.concatenate(out)


# ------------------------------------------------------------------ #
#  the blend
# ------------------------------------------------------------------ #

def blendWeights(P, y, rows):
    """Least-squares weights of the members' centred predictions (columns
    of P) for the centred truth y, on `rows`. Pure but for numpy."""
    import numpy as np
    w, *_ = np.linalg.lstsq(P[rows], y[rows], rcond=None)
    return w


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--val-from", default="2024-08-01")
    ap.add_argument("--members", default=",".join(MEMBERS))
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    want = [m.strip() for m in a.members.split(",") if m.strip()]

    import numpy as np
    t0 = datetime.datetime.now()
    T = RT.loadTable(a.rows, a.val_from, own=True, no_ratings=False, seed=a.seed)
    val = T["val"]
    yv = T["y"][val]
    sl = RT.raceSlices(T["race"][val])
    rid = np.array([int(T["race"][val][s]) for s, _e in sl])
    lead_r = np.array([int(T["lead"][val][s]) for s, _e in sl])
    sizes = [e - s for s, e in sl]

    preds = {"ratings": T["base"][val]}
    fits = {"hgb": lambda: fitHgb(T, a.seed), "lgbm": lambda: fitLgbm(T, a.seed),
            "rank": lambda: fitRank(T, a.seed), "xgb": lambda: fitXgb(T, a.seed),
            "mlp": lambda: fitMlp(T, a.seed)}
    for name in want:
        if name == "raw":
            continue
        t1 = datetime.datetime.now()
        try:
            preds[name] = RT.centre(fits[name](), sl)
        except ImportError as exc:
            print(f"  {name}: skipped ({exc}; is /srv/pred/pylib on PYTHONPATH?)")
            continue
        print(f"  {name}: fitted ({datetime.datetime.now() - t1})", flush=True)
    if "raw" in want:
        t1 = datetime.datetime.now()
        T2 = RT.loadTable(a.rows, a.val_from, own=True, no_ratings=True, seed=a.seed)
        preds["raw"] = RT.centre(fitHgb(T2, a.seed), sl)
        del T2
        print(f"  raw: fitted ({datetime.datetime.now() - t1})", flush=True)

    names = [n for n in preds if n != "ratings"]
    P = np.stack([preds[n] for n in names], axis=1)
    even = np.repeat(rid % 2 == 0, sizes)
    w = blendWeights(P, yv, even)
    preds["BLEND"] = RT.centre(P @ w, sl)
    print("\nblend weights (fitted on even races): "
          + ", ".join(f"{n} {x:+.3f}" for n, x in zip(names, w)))

    scores = {n: RT.orderScores(p, yv, sl) for n, p in preds.items()}
    for ld in sorted(set(lead_r.tolist())):
        keep = [k for k in range(len(sl)) if rid[k] % 2 == 1 and lead_r[k] == ld]
        title = "race morning" if ld == 0 else f"forecasts made {ld} days ahead"
        print(f"\nORDER, odd validation races, {title}: {len(keep):,} races")
        print(f"  {'':10} {'pairs':>7} {'rho':>6} {'within':>7}   vs ratings, pairs [90%]")
        for n, s in scores.items():
            pr = [s[k] for k in keep]
            got = RT.paired([100 * x[0] if x[0] is not None else None for x in pr],
                            [100 * scores["ratings"][k][0] if scores["ratings"][k][0] is not None
                             else None for k in keep])
            vs = f"{got[0]:+.2f} [{got[1]:+.2f}, {got[2]:+.2f}]" if got and n != "ratings" else ""
            print(f"  {n:10} {100 * RT._mean(x[0] for x in pr):6.2f}% "
                  f"{RT._mean(x[1] for x in pr):6.3f} "
                  f"{float(np.median([x[2] for x in pr])):6.2f}%   {vs}")
    print(f"\n({datetime.datetime.now() - t0})")


if __name__ == "__main__":
    main()
