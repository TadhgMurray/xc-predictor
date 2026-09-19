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
          tilt=True, use_curve=True, verbose=True, joint_dump=None, gauge=be.GAUGE_DEFAULT,
          prior_athlete=be.PRIOR_ATHLETE, dump=None, compare=None):
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
               use_curve=use_curve, verbose=verbose, codes=codes, gauge=gauge,
               prior_athlete=prior_athlete)
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
    # ★ THE SAME-ROWS COMPARISON GOES BEFORE THE JOINT-MODEL BLOCK, so it sits
    #   next to the headline it is correcting.
    if compare:
        out["window_compare"] = _compareRuns(compare, cov, pred, y, test_s,
                                             pct, seed, era_years, window)
    if dump:
        _dumpRun(dump, cov, pred, y, test_s, pct, seed, era_years, window)
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
                                    np.maximum(f["cell"], 0)]],
                                rows_at_athlete=_rowsPerAthleteSeason(
                                    sub, train_s))
    return out


# _rowsPerAthleteSeason
# Purpose:   For every row, how many TRAINING rows stand behind that row's
#            athlete-season -- the athlete-side counterpart of
#            races_per_base, and the axis a wrong athlete prior shows on.
# Arguments: cols -- the pack; train_s -- the training-row mask.
# Output:    an int array, one count per row of the pack.
# ! TRAINING ROWS ONLY, AND EXCLUDING THE ROW ITSELF IS NOT NEEDED HERE: a
#   held-out row is by construction not in train_s, so its own athlete count
#   is already the evidence the engine actually had.
# _dumpRun / _compareRuns
# Purpose:   Score two runs on the ROWS BOTH COVER, which is the only fair way
#            to compare settings that change COVERAGE.
#
# ⚠ WHY THIS EXISTS. The first window experiment (2026-09-19) read:
#       window 21   error sd 0.041938   covered 63.1%   XC 0.046661
#       window 45   error sd 0.042507   covered 69.3%   XC 0.047595
#   and 45 looks worse. But it covers 6.2 points MORE of the corpus, and the
#   rows it adds are exactly the ones that had no sibling race within 21 days
#   -- the hardest rows there are. So the two numbers are computed on
#   different populations and the comparison is confounded: a setting that
#   reaches further into the thin tail is penalised for reaching.
#
# ★ THE SAMPLE IS IDENTICAL ACROSS RUNS WITH THE SAME --pct AND --seed, and
#   in the same order, so the arrays align elementwise and no row ids are
#   needed. The signature is stored and checked anyway, because a silently
#   misaligned comparison would be worse than no comparison.
def _dumpRun(path, cov, pred, y, test_s, pct, seed, era_years, window):
    np.savez_compressed(
        path, cov=np.asarray(cov), pred=np.asarray(pred, dtype=np.float32),
        y=np.asarray(y, dtype=np.float32), test=np.asarray(test_s),
        sig=np.asarray([float(pct), float(seed), float(era_years),
                        float(len(cov))]), window=np.asarray([float(window)]))
    print(f"        dumped this run to {path} "
          f"(compare another with --compare {path})")


def _compareRuns(path, cov, pred, y, test_s, pct, seed, era_years, window):
    if not os.path.exists(path):
        print(f"        --compare: no dump at {path}; run the other setting "
              f"first with --dump {path}")
        return None
    d = np.load(path)
    sig = d["sig"]
    mine = [float(pct), float(seed), float(era_years), float(len(cov))]
    if list(sig) != mine:
        print(f"        --compare REFUSED: the dump was made with "
              f"pct/seed/era/rows {list(sig)} and this run is {mine}. The "
              f"samples differ, so the rows do not align.")
        return None
    other_w = float(d["window"][0])
    both = np.asarray(test_s) & np.asarray(cov) & d["cov"]
    if not both.any():
        print("        --compare: no row is covered by both runs")
        return None
    e_mine = float((np.asarray(y)[both] - np.asarray(pred)[both]).std())
    e_other = float((d["y"][both] - d["pred"][both]).std())
    print(f"\n[bracket] SAME ROWS, BOTH WINDOWS: {int(both.sum()):,} held-out "
          f"rows covered by both")
    print(f"        window {window:g}: {e_mine:.6f}     "
          f"window {other_w:g}: {e_other:.6f}")
    better = f"{window:g}" if e_mine < e_other else f"{other_w:g}"
    print(f"        -> window {better} wins on the rows both can score. This "
          f"is the comparison;\n           the headline sds are not, because "
          f"they are computed on different rows.")
    return dict(n=int(both.sum()), sd_this=e_mine, sd_other=e_other,
                window_this=float(window), window_other=other_w)


def _priorAthlete(text):
    """'fit' stays a string; anything else becomes a float.

    ! parsePrior's shape, kept separate because the athlete prior is ONE
      number or 'fit' -- it has no per-group spelling, and accepting one
      would imply a per-group meaning that levels() does not have.
    """
    t = (text or "").strip().lower()
    return be.PRIOR_FIT if t == be.PRIOR_FIT else float(t or 0.0)


def _rowsPerAthleteSeason(sub, train_s):
    # ⚠ sub, NOT cols. train_s, test_s, pred, y and cov are all over the
    #   SAMPLE (bk.subsetCols(cols, both)), not the whole pack -- 8,986,323
    #   rows against 62,805,298. Indexing the full pack with the sample's mask
    #   raised "boolean index did not match indexed array along axis 0" AFTER
    #   the headline numbers had printed, so the run was not wasted, but the
    #   by-athlete table never appeared. The other two arrays at this call site
    #   (races_at_cell, from f["cell"]) are sample-shaped for the same reason;
    #   full_ath/full_year/full_norm are passed separately and ARE the pack,
    #   which is what made the mistake easy to make.
    ath = np.asarray(sub["athlete"]).astype(np.int64)
    year = np.asarray(sub["year"]).astype(np.int64)
    n_mask = int(np.asarray(train_s).size)
    if ath.size != n_mask:
        raise AssertionError(
            f"_rowsPerAthleteSeason: {ath.size:,} rows but a {n_mask:,}-row "
            f"mask. Pass the SAMPLE (sub), not the pack (cols).")
    key = ath * 10_000 + year
    uniq, inv = np.unique(key, return_inverse=True)
    counts = np.bincount(inv[np.asarray(train_s, dtype=bool)],
                         minlength=uniq.size)
    return counts[inv]


def sameRows(sub, both, test_s, cov, pred, y, dump_path=None, full_ath=None,
             full_year=None, full_norm=None, races_at_cell=None,
             rows_at_athlete=None):
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
        # ⚠ CLAMP BEFORE INDEXING, NOT AFTER (2026-09-19). The `inb` guard
        #   below exists for exactly this case and sat one line too late, so a
        #   dump written against a DIFFERENT pack raised
        #   "index 62805404 is out of bounds for axis 0 with size 62805298"
        #   instead of reporting the mismatch it was written to report. 107
        #   rows is a pack rebuilt between the two runs; the ids are then
        #   meaningless, not merely shifted.
        bad = (rows_j < 0) | (rows_j >= order.size)
        if bad.any():
            share = float(bad.mean())
            print(f"        the joint file holds {int(bad.sum()):,} row ids "
                  f"({share:.1%}) outside this pack's {order.size:,} rows — it "
                  f"was written against a DIFFERENT pack")
            if share > 0.01:
                print(f"        no same-rows comparison: rebuild the dump with "
                      f"`scripts/ablation_ladder.py --only base`, or point "
                      f"--dump-path at one built on this pack")
                return None
            print(f"        dropping them and continuing on the rest")
        rows_j = np.where(bad, -1, order[np.clip(rows_j, 0, order.size - 1)])
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

    # ★★ AND THE SAME QUESTION ON THE ATHLETE AXIS, which had no table at all
    #    (2026-09-19). The course side has been scored by its thinness since
    #    the prior existed; the athlete side has no prior and so had nothing
    #    to score -- which is exactly how an asymmetry survives. bracket's
    #    levels() takes the PLAIN MEAN of an athlete-season's other rows, so
    #    an athlete with one other row is a single noisy reading used with
    #    the authority of an athlete with thirty.
    #
    #    THIS IS THE TABLE THAT DECIDES prior_athlete, and how to read it:
    #      thin buckets improve, fat buckets unchanged -> the prior is right
    #      fat buckets get WORSE                       -> over-shrinking
    #      nothing moves at all                        -> tau >> sigma; the
    #                                                     athletes really are
    #                                                     that different and
    #                                                     no prior is wanted
    #    Run once at prior_athlete=0 and once at "fit" and compare columns.
    if rows_at_athlete is not None:
        ra = np.asarray(rows_at_athlete, dtype=np.int64)
        res["by_athlete_rows"] = {}
        print(f"        by training rows behind the ATHLETE:  {'rows/ath':>8} "
              f"{'rows':>9} {'bracket':>9} {'joint':>9}")
        for lo, hi, lab in ((1, 1, "1"), (2, 3, "2-3"), (4, 9, "4-9"),
                            (10, 29, "10-29"), (30, 10 ** 9, "30+")):
            mm = m & (ra >= lo) & (ra <= hi)
            if mm.sum() >= 200:
                sb = float((y[mm] - pred[mm]).std())
                sj = float((y[mm] - jp[mm]).std())
                res["by_athlete_rows"][lab] = (sb, sj, int(mm.sum()))
                print(f"        {'':37}{lab:>8} {int(mm.sum()):>9,} "
                      f"{sb:>9.5f} {sj:>9.5f}")
    return res


def fitAll(cols, npz, out_path, codes=None, era_years=0, window=21.0, top=0.5,
           prior_races=be.PRIOR_RACES, prior_group=be.PRIOR_FIT, iters=30, tilt=True,
           use_curve=True, gauge=be.GAUGE_DEFAULT,
           prior_athlete=be.PRIOR_ATHLETE):
    """Fit every row and write the difficulty file."""
    t0 = time.time()
    if codes is None or "_cell" not in cols:
        cols, codes = bk.packCodes(cols, npz, era_years)
    f = be.fit(cols, npz, train=None, window=window, top=top, era_years=era_years,
               n_iter=iters, prior_races=prior_races, prior_group=prior_group, tilt=tilt,
               use_curve=use_curve, verbose=True, codes=codes, gauge=gauge,
               prior_athlete=prior_athlete)
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
    # ★ THE GAUGE, SO THE CHANGE IS SCORED RATHER THAN ARGUED ABOUT.
    #   "outdoor" makes an ordinary outdoor track the zero and lets indoor's
    #   level sit where the data puts it; "all" is the pre-2026-09-18
    #   behaviour, which pinned indoor and outdoor together and left the split
    #   between them free (indoor came out 1.5% EASIER, sign wrong).
    ap.add_argument("--gauge", choices=be.GAUGE_CHOICES,
                    default=be.GAUGE_DEFAULT,
                    help="which cells are held at zero. Run both and compare "
                         "the held-out error before trusting either.")
    ap.add_argument("--prior-athlete", default=str(be.PRIOR_ATHLETE),
                    help="shrink each athlete-season's level toward its "
                         "POOL's mean by this many rows' worth. A number, or "
                         "'fit' to estimate sigma_row^2/tau^2 per pool. "
                         "Default %(default)s = OFF, which is the plain mean "
                         "of the athlete's other rows in the window -- one "
                         "row counting as much as thirty. Run at 0 and at "
                         "'fit' and read the 'by training rows behind the "
                         "ATHLETE' table: thin buckets should improve and fat "
                         "ones should not get worse.")
    ap.add_argument("--dump", default=None, metavar="PATH",
                    help="write this run's per-row held-out predictions, so "
                         "another run can be scored on the SAME rows")
    ap.add_argument("--compare", default=None, metavar="PATH",
                    help="a --dump from another run: print both on the rows "
                         "BOTH cover. Use this whenever the setting changes "
                         "coverage (--window does), because the headline sds "
                         "are computed on different populations otherwise.")
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
               use_curve=not args.no_curve, gauge=args.gauge,
               prior_athlete=_priorAthlete(args.prior_athlete))
        return
    score(cols, npz, codes=codes, pct=args.pct, seed=args.seed, era_years=args.era_years,
          window=args.window, top=args.top, prior_races=args.prior_races,
          prior_group=args.prior_group, iters=args.iters, tilt=not args.no_tilt,
          use_curve=not args.no_curve, gauge=args.gauge,
          prior_athlete=_priorAthlete(args.prior_athlete),
          dump=args.dump, compare=args.compare)


if __name__ == "__main__":
    main()
