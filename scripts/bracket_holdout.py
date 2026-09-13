#!/usr/bin/env python3
"""
bracket_holdout.py -- score the bracket engine on the SAME held-out races
the joint model's ladder scores itself on, and fit it on everything.

    scripts/bracket_holdout.py --pct 15 --seed 11 --era-years 2
    scripts/bracket_holdout.py --pct 15 --seed 11 --era-years 2 --top 0.25
    scripts/bracket_holdout.py --full --era-years 2      # fit on all rows, write the file

The athlete sample (--pct, --seed) and the race split (10%, seed 1) are
run_joint's own, so `error sd` here and the ladder's base rung
(engine/data/ladder_logs/base.log, "error sd ... covered ...") are the
same question asked of two engines: a whole race the model never saw, at
a course it knows, from athletes it knows.

★ THE ENGINE RUNS ON THE SAMPLE'S ROWS ONLY. The codes (athlete-season,
  race, the solve file's cell) are numbered over the whole pack first
  (bracket.packCodes), so the sample still indexes the file's ratings
  and cells; then every pass of the fixed point touches the sample's
  rows, not the corpus's (2026-09-12: "5 mins max, all of them together").
"""
import argparse
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket as bk                                            # noqa: E402
import bracket_engine as be                                     # noqa: E402
import pair_validate as pv                                      # noqa: E402
import run_joint as rj                                          # noqa: E402


def sampleAndSplit(cols, pct, seed, frac=0.10, split_seed=1):
    """run_joint's keep (an athlete sample) and holdout()'s race split."""
    ath_all = np.asarray(cols["athlete"]).astype(np.int64)
    keep = (np.asarray(cols["course"]) >= 0) & (np.asarray(cols["norm"]) > 0)
    if pct and pct < 100:
        uniq = np.unique(ath_all[keep])
        rng = np.random.default_rng(seed)
        picked = uniq[rng.random(uniq.size) < pct / 100.0]
        m = np.zeros(int(ath_all.max()) + 1, dtype=bool)
        m[picked] = True
        keep = keep & m[ath_all]
    idx = np.flatnonzero(keep)
    race_all = (np.asarray(cols["_race"]).astype(np.int64) if "_race" in cols
                else rj.raceCodes(cols["course"], cols["days"])[0])
    te_local = pv.splitFor("race", idx.size, race=race_all[idx],
                           athlete=ath_all[idx],
                           cell=np.asarray(cols["course"])[idx],
                           frac=frac, seed=split_seed)
    train = np.zeros(keep.size, dtype=bool); train[idx[~te_local]] = True
    test = np.zeros(keep.size, dtype=bool); test[idx[te_local]] = True
    return train, test


def score(cols, npz, codes=None, pct=15.0, seed=11, era_years=0, window=21.0,
          top=0.5, prior_races=be.PRIOR_RACES, prior_group=be.PRIOR_FIT, iters=30,
          tilt=True, use_curve=True, verbose=True, joint_dump=None):
    """Fit on the sample's training rows, score its held-out races.
    Returns dict(sd, covered, by_sport, n_train, n_test, seconds, base_line,
    same_rows). joint_dump: the joint model's per-row held-out predictions
    (run_joint.holdout's file; default the ladder's base rung)."""
    t0 = time.time()
    if codes is None or "_cell" not in cols:
        cols, codes = bk.packCodes(cols, npz, era_years)
    train, test = sampleAndSplit(cols, pct, seed)
    both = train | test
    sub = bk.subsetCols(cols, both)
    train_s, test_s = train[both], test[both]
    print(f"[bracket] {int(train_s.sum()):,} training rows, {int(test_s.sum()):,} "
          f"held-out rows ({pct:g}% of athletes, 10% of their races)", flush=True)
    f = be.fit(sub, npz, train=train_s, window=window, top=top, era_years=era_years,
               n_iter=iters, prior_races=prior_races, prior_group=prior_group, tilt=tilt,
               use_curve=use_curve, verbose=verbose, codes=codes)
    pred, cov = be.predict(f)
    y = np.log(np.asarray(sub["norm"], dtype=np.float64))
    m = test_s & cov
    err = y[m] - pred[m]
    out = dict(sd=float(err.std()), covered=float(cov[test_s].mean()),
               n_train=int(train_s.sum()), n_test=int(test_s.sum()), by_sport={},
               seconds=time.time() - t0, base_line=None)
    print(f"\n[bracket] HELD OUT: 10% of RACES -- a whole new race at a known course")
    print(f"[bracket] error sd {out['sd']:.6f}   covered {out['covered']:.1%}   "
          f"[{out['seconds']:.0f}s]   window {window:g} top {top:g} "
          f"era {era_years} tilt {'on' if tilt else 'off'} "
          f"curve {'on' if use_curve else 'off'}")
    if "sport" in sub:
        sport = np.asarray(sub["sport"])
        for code, name in ((0, "XC"), (1, "TF")):
            mm = m & (sport == code)
            if mm.sum() > 1000:
                e = y[mm] - pred[mm]
                out["by_sport"][name] = (float(e.std()), int(mm.sum()))
                print(f"        {name}: {e.std():.6f}  ({int(mm.sum()):,} rows)")
    base_log = os.path.join(_ROOT, "engine", "data", "ladder_logs", "base.log")
    line = None
    if os.path.exists(base_log):
        with open(base_log, errors="replace") as fh:
            for ln in fh:
                if "error sd" in ln and "covered" in ln:
                    line = ln.strip()
    out["base_line"] = line
    if line:
        print(f"        the joint model on the same sample and split (ladder base rung):\n"
              f"        {line}")
    else:
        print("        compare: the ladder's base rung (engine/data/ladder_logs/base.log; "
              "run `scripts/ablation_ladder.py --only base` if it is not there), "
              "same sample, same split, same question")
    out["same_rows"] = sameRows(sub, both, test_s, cov, pred, y, joint_dump,
                                full_ath=cols["athlete"], full_year=cols["year"],
                                full_norm=cols["norm"],
                                races_at_cell=f["races_per_base"][np.asarray(f["base_of_cell"])[
                                    np.maximum(f["cell"], 0)]])
    return out


def sameRows(sub, both, test_s, cov, pred, y, dump_path=None, full_ath=None,
             full_year=None, full_norm=None, races_at_cell=None):
    """★ ONE SET OF ROWS FOR BOTH ENGINES (2026-09-12). The bracket engine
    covers a held-out row only when its athlete has other races within
    the window, 59% of the corpus's held-out rows; the joint model covers
    89%. The rows the bracket engine can score are the easy ones, so its
    headline sd is not comparable with the joint model's. The ladder's
    base rung writes its per-row predictions (run_joint.holdout,
    XCP_HOLDOUT_DUMP); here both engines are scored on the rows BOTH
    cover. Returns dict(n, sd_bracket, sd_joint, by_sport) or None."""
    path = dump_path or os.path.join(_ROOT, "engine", "data", "ladder_logs",
                                     "base_holdout.npz")
    if not os.path.exists(path):
        print(f"        (no per-row joint predictions at {path}; the next ladder "
              f"run writes them, then this prints both engines on the same rows)")
        return None
    d = np.load(path, allow_pickle=False)
    rows_j = np.asarray(d["row"], dtype=np.int64)
    pred_j = np.asarray(d["pred"], dtype=np.float64)
    cov_j = np.asarray(d["covered"], dtype=bool)
    y_j = np.asarray(d["y"], dtype=np.float64)
    n_pack = both.size
    if "row_space" not in d.files:
        # ! A FILE WRITTEN BEFORE 2026-09-13 INDEXES THE SOLVE'S SORTED ROWS,
        #   not the pack's: run_joint sorts by (athlete, year) first. The
        #   sort is recomputed here and undone. Run 21's first comparison
        #   read 35% overlap and a joint error of 0.0625 for this reason.
        order = np.lexsort((np.asarray(full_year), np.asarray(full_ath)))
        rows_j = order[rows_j]
    # the dump's own times must land on the rows they came from; if they do
    # not, the two engines are being compared on different rows and it stops
    y_full = np.log(np.asarray(full_norm, dtype=np.float64))
    inb = (rows_j >= 0) & (rows_j < n_pack)
    match = np.isclose(y_full[rows_j[inb]], y_j[inb], atol=1e-6)
    if match.mean() < 0.99:
        print(f"        the joint file's held-out rows do not land on this pack "
              f"({match.mean():.1%} of their times agree); no same-rows comparison")
        return None
    joint = np.full(n_pack, np.nan)
    okj = cov_j & (rows_j >= 0) & (rows_j < n_pack)
    joint[rows_j[okj]] = pred_j[okj]
    held_j = np.zeros(n_pack, dtype=bool)
    held_j[rows_j[(rows_j >= 0) & (rows_j < n_pack)]] = True
    pack_rows = np.flatnonzero(both)
    test_pack = np.zeros(n_pack, dtype=bool); test_pack[pack_rows[test_s]] = True
    overlap = float((held_j & test_pack).sum() / max(test_pack.sum(), 1))
    jp = joint[pack_rows]
    m = test_s & cov & np.isfinite(jp)
    if m.sum() < 100:
        print(f"        the joint file's held-out rows overlap {overlap:.1%} of these; "
              f"too few in common to compare")
        return None
    e_b = y[m] - pred[m]
    e_j = y[m] - jp[m]
    res = dict(n=int(m.sum()), sd_bracket=float(e_b.std()), sd_joint=float(e_j.std()),
               overlap=overlap, by_sport={})
    print(f"\n[bracket] SAME ROWS, BOTH ENGINES: {res['n']:,} held-out rows both cover "
          f"(the joint file's held-out rows match {overlap:.1%} of this split)")
    print(f"        bracket engine {res['sd_bracket']:.6f}   joint model {res['sd_joint']:.6f}")
    if "sport" in sub:
        sport = np.asarray(sub["sport"])
        for code, name in ((0, "XC"), (1, "TF")):
            mm = m & (sport == code)
            if mm.sum() > 1000:
                sb, sj = float((y[mm] - pred[mm]).std()), float((y[mm] - jp[mm]).std())
                res["by_sport"][name] = (sb, sj, int(mm.sum()))
                print(f"        {name}: bracket {sb:.6f}   joint {sj:.6f}  ({int(mm.sum()):,} rows)")
    # ★ WHERE SHRINKAGE SHOWS: the same rows, binned by how many TRAINING
    #   races stand behind the row's course. A course seen once in training
    #   is predicted from one day's opinion of it; if its held-out error is
    #   not worse than a well-known course's by more than the day noise, the
    #   prior is about right; if it is far worse, the prior is too loose.
    if races_at_cell is not None:
        r = np.asarray(races_at_cell, dtype=np.int64)
        res["by_races"] = {}
        print(f"        by training races at the course:  {'races':>7} {'rows':>9} "
              f"{'bracket':>9} {'joint':>9}")
        for lo, hi, lab in ((1, 1, "1"), (2, 3, "2-3"), (4, 9, "4-9"), (10, 29, "10-29"),
                            (30, 10**9, "30+")):
            mm = m & (r >= lo) & (r <= hi)
            if mm.sum() >= 200:
                sb, sj = float((y[mm] - pred[mm]).std()), float((y[mm] - jp[mm]).std())
                res["by_races"][lab] = (sb, sj, int(mm.sum()))
                print(f"        {'':34}{lab:>7} {int(mm.sum()):>9,} {sb:>9.5f} {sj:>9.5f}")
    return res


def fitAll(cols, npz, out_path, codes=None, era_years=0, window=21.0, top=0.5,
           prior_races=be.PRIOR_RACES, prior_group=be.PRIOR_FIT, iters=30, tilt=True,
           use_curve=True):
    """Fit every row and write the difficulty file."""
    t0 = time.time()
    if codes is None or "_cell" not in cols:
        cols, codes = bk.packCodes(cols, npz, era_years)
    f = be.fit(cols, npz, train=None, window=window, top=top, era_years=era_years,
               n_iter=iters, prior_races=prior_races, prior_group=prior_group, tilt=tilt,
               use_curve=use_curve, verbose=True, codes=codes)
    np.savez(out_path, D=f["D"], votes=f["votes"], course_keys=np.array(f["cell_keys"]),
             D_race=f["D_race"], votes_race=f["votes_race"],
             races_per_cell=f["races_per_cell"], races_per_base=f["races_per_base"],
             base_of_cell=f["base_of_cell"],
             window=np.array([window]), top=np.array([top]),
             era_years=np.array([era_years]), prior_races=np.array([prior_races]),
             prior_group=np.asarray(f["prior_group"], dtype=np.float64),
             prior_group_names=np.array(list(f["prior_group_names"])),
             race_sat=np.array([f["race_sat"]]))
    print(f"[bracket] wrote {out_path}: {int((f['votes'] > 0).sum()):,} cells with votes "
          f"in {time.time() - t0:.0f}s")
    return f


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    d = rj.buildParser()
    ap.add_argument("--pack", default=d.get_default("pack"))
    ap.add_argument("--npz", default=d.get_default("out"),
                    help="the joint solve's file, for the form curve and the ratings")
    ap.add_argument("--out", default=os.path.join(_ROOT, "engine", "data",
                                                  "bracket_difficulty.npz"))
    ap.add_argument("--pct", type=float, default=15.0)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--era-years", type=int, default=0)
    ap.add_argument("--window", type=float, default=21.0)
    ap.add_argument("--top", type=float, default=0.5)
    ap.add_argument("--prior-races", type=float, default=be.PRIOR_RACES,
                    help="races' worth of pull of an era toward its course's history")
    ap.add_argument("--prior-group", type=be.parsePrior, default=be.PRIOR_FIT,
                    help="races' worth of pull of a course toward its sport's average")
    ap.add_argument("--iters", type=int, default=30)
    ap.add_argument("--no-tilt", action="store_true")
    ap.add_argument("--no-curve", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="fit on every row and write --out instead of scoring")
    args = ap.parse_args()
    cols, npz = bk.loadInputs(args.pack, args.npz)
    if npz is None:
        print(f"(no joint file at {args.npz}: no curve, no ratings, no tilt)")
    t0 = time.time()
    cols, codes = bk.packCodes(cols, npz, args.era_years)
    print(f"[bracket] {np.asarray(cols['norm']).size:,} rows coded in {time.time() - t0:.0f}s",
          flush=True)
    if args.full:
        fitAll(cols, npz, args.out, codes=codes, era_years=args.era_years,
               window=args.window, top=args.top, prior_races=args.prior_races,
               prior_group=args.prior_group, iters=args.iters, tilt=not args.no_tilt,
               use_curve=not args.no_curve)
        return
    score(cols, npz, codes=codes, pct=args.pct, seed=args.seed, era_years=args.era_years,
          window=args.window, top=args.top, prior_races=args.prior_races,
          prior_group=args.prior_group, iters=args.iters, tilt=not args.no_tilt,
          use_curve=not args.no_curve)


if __name__ == "__main__":
    main()
